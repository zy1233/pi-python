"""Rules of the Auto permission mode: the part that needs no model.

Not wired in yet: nothing in the agent calls this module. ``decide`` takes one tool call and
the session facts (``AutoContext``) and gives back one ``RuleDecision``. Design and reasons:
``docs/specs/2026-11-02-auto-mode-design.md`` (sections 2, 3.2, 3.3).

The steps, first answer wins:

1. The tool declares itself harmless (same test as ``ask`` mode): ALLOW.
2. Floor rules: ASK. A later model step can never turn a floor ASK into ALLOW. The floor is
   tool kind, paths (outside the project folder, protected files, secrets), and the text of a
   ``bash`` command (delete, network, secrets, hidden commands, code of an untrusted project).
3. Allow rules: ``write`` / ``edit`` inside the project folder, and a ``bash`` command that the
   parser proves read-only: ALLOW.
4. A ``bash`` command that is not on the floor and not proven read-only: JUDGE. Later, the model
   step answers. Until then ``decide_rules_only`` turns JUDGE into ASK ``unclear``.
5. Everything else: ASK ``unclear``.

Fail closed: any doubt, any error and any input that the parser cannot read is an ASK. The
``bash`` parser is small and conservative on purpose. It is not a shell: where the shell and
this parser could read a command in two ways, the parser refuses.

Known gaps (the same as for the ``read`` and ``grep`` tools, which ``ask`` mode allows): a glob
such as ``cat *`` or a recursive ``grep -r`` can read a protected file that is not named. What
the kernel sandbox stops is the sandbox's job.
"""

from __future__ import annotations

import fnmatch
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pi_agent_cli.auto_reasons import Reason
from pi_agent_cli.permissions import needs_permission

MAX_COMMAND_BYTES = 4096
MAX_WRAPPER_DEPTH = 5
MAX_CWD_CANDIDATES = 8


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


class Outcome(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    JUDGE = "judge"


class By(StrEnum):
    RULE = "rule"
    FLOOR = "floor"


@dataclass(frozen=True)
class RuleDecision:
    outcome: Outcome
    by: By
    reason: Reason
    rule_id: str


@dataclass(frozen=True)
class ToolCall:
    """One tool call as the rules see it.

    ``builtin`` says that the tool is one of ours (``bash``, ``write``, ``edit``). A tool of an
    extension or an MCP server may use the same name; it is not ours, so name rules do not
    apply to it and it is asked about unless it declares itself harmless.
    """

    name: str
    input: Mapping[str, Any]
    annotations: Mapping[str, Any] | None = None
    builtin: bool = False


class FsProbe(Protocol):
    def realpath(self, path: str) -> str: ...

    def lexists(self, path: str) -> bool: ...

    def in_git_worktree(self, path: str) -> bool: ...


class RealFs:
    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def lexists(self, path: str) -> bool:
        return os.path.lexists(path)

    def in_git_worktree(self, path: str) -> bool:
        current = path if os.path.isdir(path) else os.path.dirname(path)
        while True:
            if os.path.lexists(os.path.join(current, ".git")):
                return True
            parent = os.path.dirname(current)
            if parent == current:
                return False
            current = parent


@dataclass(frozen=True)
class AutoContext:
    """What the rules need to know about the session.

    ``cwd`` is the session's project folder; with ``extra_roots`` (from the home config) it makes
    the project roots. ``origin_cwd`` is the working folder of a workflow sub-agent: it is
    where relative paths go, and it has to be inside the roots. ``protected_paths`` are files
    or folders (absolute) that always ask; ``protected_globs`` likewise, matched on the whole
    path with ``/`` as separator. ``pi_home`` is the agent's own home (config, trust store,
    sessions): all of it is protected.
    """

    cwd: str
    home_dir: str
    pi_home: str
    project_trusted: bool
    extra_roots: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    protected_globs: tuple[str, ...] = ()
    origin_cwd: str | None = None
    fs: FsProbe = field(default_factory=RealFs)


def build_context(
    *,
    cwd: str,
    project_trusted: bool,
    home_dir: str | None = None,
    pi_home_dir: str | None = None,
    extra_roots: tuple[str, ...] = (),
    protected_globs: tuple[str, ...] = (),
    config: Any = None,
    origin_cwd: str | None = None,
    fs: FsProbe | None = None,
) -> AutoContext:
    """An ``AutoContext`` with the protected set filled from what the project asks to be
    trusted for (``gated_project_resources``), so the list does not drift from the trust code.
    """
    from pi_agent_core.home import pi_home

    home = home_dir if home_dir is not None else os.path.expanduser("~")
    agent_home = str(pi_home(pi_home_dir))
    gated: list[str] = []
    if config is not None:
        from pi_agent_cli.trust_fingerprint import gated_project_resources

        gated = [str(item.path) for item in gated_project_resources(config, cwd, home=agent_home)]
    return AutoContext(
        cwd=cwd,
        home_dir=home,
        pi_home=agent_home,
        project_trusted=project_trusted,
        extra_roots=tuple(extra_roots),
        protected_paths=tuple(gated),
        protected_globs=tuple(protected_globs),
        origin_cwd=origin_cwd,
        fs=fs if fs is not None else RealFs(),
    )


class _Floor(Exception):
    """A floor hit, or a refusal to read: the call is an ASK."""

    def __init__(self, reason: Reason, rule_id: str) -> None:
        super().__init__(rule_id)
        self.reason = reason
        self.rule_id = rule_id


def _ask(reason: Reason, rule_id: str) -> RuleDecision:
    return RuleDecision(Outcome.ASK, By.FLOOR, reason, rule_id)


def _allow(reason: Reason, rule_id: str) -> RuleDecision:
    return RuleDecision(Outcome.ALLOW, By.RULE, reason, rule_id)


# --------------------------------------------------------------------------- paths


def _key(path: str) -> str:
    return os.path.normcase(path)


def _is_inside(path: str, root: str) -> bool:
    p, r = _key(path), _key(root)
    if p == r:
        return True
    return p.startswith(r.rstrip(os.sep) + os.sep)


def _is_fs_root(path: str) -> bool:
    return os.path.dirname(path) == path


_DEV_OK = _words("/dev/null /dev/stdin /dev/stdout /dev/stderr /dev/zero /dev/random /dev/urandom")

_SECRET_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube"})
_SECRET_NAME = re.compile(
    r"^(id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|.*\.(pem|key|p12|pfx|ppk|kdbx|keystore|jks)"
    r"|\.netrc|_netrc|\.pgpass|\.git-credentials|\.npmrc|\.pypirc|credentials(\.json)?"
    r"|kubeconfig|authorized_keys|known_hosts)$",
    re.IGNORECASE,
)
_SHELL_START = _words(
    ".bashrc .bash_profile .bash_login .bash_logout .profile .zshrc .zshenv .zprofile .zlogin "
    ".zlogout .cshrc .tcshrc .kshrc .inputrc .gitconfig config.fish"
)


class _Env:
    """The context with every path resolved once."""

    def __init__(self, ctx: AutoContext) -> None:
        self.ctx = ctx
        self.fs = ctx.fs
        self.cwd = ctx.fs.realpath(ctx.origin_cwd or ctx.cwd)
        roots = [ctx.fs.realpath(r) for r in (ctx.cwd, *ctx.extra_roots)]
        self.roots = [r for r in roots if not _is_fs_root(r)]
        self.home = ctx.fs.realpath(ctx.home_dir)
        self.pi_home = ctx.fs.realpath(ctx.pi_home)
        self.protected = [ctx.fs.realpath(p) for p in ctx.protected_paths]
        self.globs = ctx.protected_globs

    def inside(self, real: str) -> bool:
        return any(_is_inside(real, root) for root in self.roots)

    def protected_hit(self, real: str) -> tuple[Reason, str] | None:
        """Why *real* (a resolved path) is protected, or ``None``."""
        parts = [p.lower() for p in re.split(r"[\\/]+", real) if p]
        base = parts[-1] if parts else ""
        if _SECRET_DIRS.intersection(parts):
            return Reason.SECRETS, "path.secret_dir"
        if base.startswith(".env"):
            return Reason.SECRETS, "path.env_file"
        if _SECRET_NAME.match(base):
            return Reason.SECRETS, "path.secret_name"
        if ".git" in parts:
            return Reason.PROTECTED_PATH, "path.git_dir"
        if ".pi" in parts or ".pi-python" in parts:
            return Reason.PROTECTED_PATH, "path.pi_dir"
        if base == ".gitmodules":
            return Reason.PROTECTED_PATH, "path.gitmodules"
        if base in _SHELL_START:
            return Reason.PROTECTED_PATH, "path.shell_start"
        if _is_inside(real, os.path.join(self.home, ".config")):
            return Reason.PROTECTED_PATH, "path.home_config"
        if _is_inside(real, self.pi_home):
            return Reason.PROTECTED_PATH, "path.pi_home"
        if any(_is_inside(real, item) for item in self.protected):
            return Reason.PROTECTED_PATH, "path.gated_resource"
        slashed = real.replace("\\", "/")
        if any(
            fnmatch.fnmatch(slashed, glob) or fnmatch.fnmatch(base, glob) for glob in self.globs
        ):
            return Reason.PROTECTED_PATH, "path.protected_glob"
        return None

    def path_floor(self, real: str) -> None:
        """Raise ``_Floor`` when *real* is protected or outside the project roots."""
        hit = self.protected_hit(real)
        if hit is not None:
            raise _Floor(*hit)
        if not self.inside(real):
            raise _Floor(Reason.OUTSIDE_PROJECT, "path.outside_project")

    def expand_home(self, text: str) -> str:
        if text == "~":
            return self.home
        if text.startswith(("~/", "~\\")):
            return os.path.join(self.home, text[2:])
        return text


# ------------------------------------------------------------------------- the lexer


@dataclass
class _Word:
    text: str
    quoted: bool = False
    glob: bool = False


@dataclass
class _Tok:
    kind: str  # "word", "op", "redir"
    text: str = ""
    word: _Word | None = None


_NAME_START = re.compile(r"[A-Za-z_]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FD_DUP = re.compile(r"(\d+|-)(?=[\s;&|)<>]|$)")
_SPECIAL_PARAMS = "?$!@*#-0123456789"


def _lex(command: str, env: _Env) -> list[_Tok]:
    n = len(command)
    toks: list[_Tok] = []
    buf: list[str] = []
    in_word = False
    quoted = False
    glob = False
    i = 0

    def flush() -> None:
        nonlocal buf, in_word, quoted, glob
        if in_word:
            toks.append(_Tok("word", word=_Word("".join(buf), quoted, glob)))
        buf, in_word, quoted, glob = [], False, False, False

    def refuse(reason: Reason, rule_id: str) -> None:
        raise _Floor(reason, rule_id)

    def dollar(index: int, in_dq: bool) -> tuple[str, int]:
        """Expansion at ``command[index] == '$'``. Only ``$HOME`` and ``$PWD`` are known."""
        nxt = command[index + 1] if index + 1 < n else ""
        if nxt == "(":
            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.subst")
        if nxt == "'" and not in_dq:
            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.ansi_c")
        if nxt == '"' and not in_dq:
            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.locale_quote")
        if nxt == "{":
            close = command.find("}", index + 2)
            name = command[index + 2 : close] if close > 0 else ""
            if name == "HOME":
                return env.home, close + 1
            if name == "PWD":
                return env.cwd, close + 1
            refuse(Reason.UNCLEAR, "bash.unclear.expansion")
        if nxt and _NAME_START.match(nxt):
            match = _NAME.match(command, index + 1)
            assert match is not None
            name = match.group(0)
            if name == "HOME":
                return env.home, match.end()
            if name == "PWD":
                return env.cwd, match.end()
            refuse(Reason.UNCLEAR, "bash.unclear.variable")
        if nxt and nxt in _SPECIAL_PARAMS:
            refuse(Reason.UNCLEAR, "bash.unclear.special_param")
        return "$", index + 1

    while i < n:
        ch = command[i]
        if ch in " \t":
            flush()
            i += 1
        elif ch == "\n":
            flush()
            toks.append(_Tok("op", ";"))
            i += 1
        elif ch == "#" and not in_word:
            while i < n and command[i] != "\n":
                i += 1
        elif ch == "\\":
            if i + 1 >= n or command[i + 1] == "\n":
                refuse(Reason.UNCLEAR, "bash.unclear.continuation")
            buf.append(command[i + 1])
            in_word = quoted = True
            i += 2
        elif ch == "'":
            close = command.find("'", i + 1)
            if close < 0:
                refuse(Reason.UNCLEAR, "bash.unclear.quote")
            buf.append(command[i + 1 : close])
            in_word = quoted = True
            i = close + 1
        elif ch == '"':
            i += 1
            in_word = quoted = True
            while True:
                if i >= n:
                    refuse(Reason.UNCLEAR, "bash.unclear.quote")
                c = command[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\":
                    nxt = command[i + 1] if i + 1 < n else ""
                    if nxt == "\n" or not nxt:
                        refuse(Reason.UNCLEAR, "bash.unclear.continuation")
                    if nxt in '$`"\\':
                        buf.append(nxt)
                    else:
                        buf.append(c + nxt)
                    i += 2
                elif c == "`":
                    refuse(Reason.HIDDEN_COMMAND, "bash.hidden.backtick")
                elif c == "$":
                    text, i = dollar(i, True)
                    buf.append(text)
                else:
                    buf.append(c)
                    i += 1
        elif ch == "`":
            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.backtick")
        elif ch == "$":
            text, i = dollar(i, False)
            buf.append(text)
            in_word = True
        elif ch == "~" and not in_word:
            nxt = command[i + 1] if i + 1 < n else ""
            if nxt == "" or nxt in " \t\n/\\;&|<>":
                buf.append(env.home)
                in_word = quoted = True  # a path now, never an option or a glob
                i += 1
            else:
                refuse(Reason.UNCLEAR, "bash.unclear.tilde")
        elif ch in "<>":
            fd_prefix = in_word and not quoted and "".join(buf).isdigit()
            if fd_prefix:
                buf, in_word = [], False
            else:
                flush()
            i = _lex_redirect(command, i, toks)
        elif ch == "&" and command.startswith("&>", i):
            flush()
            op = "&>>" if command.startswith("&>>", i) else "&>"
            toks.append(_Tok("redir", op))
            i += len(op)
        elif ch in "|&;":
            flush()
            if ch == ";" and command.startswith(";;", i):
                refuse(Reason.UNCLEAR, "bash.unclear.case")
            two = command[i : i + 2]
            op = two if two in ("&&", "||", "|&") else ch
            toks.append(_Tok("op", op))
            i += len(op)
        elif ch in "()":
            refuse(Reason.UNCLEAR, "bash.unclear.group")
        elif ch == "{":
            close = command.find("}", i)
            span = command[i : close if close > 0 else i]
            if close > 0 and not re.search(r"\s", span) and ("," in span or ".." in span):
                refuse(Reason.UNCLEAR, "bash.unclear.brace")
            buf.append(ch)
            in_word = True
            i += 1
        else:
            if ch in "*?[":
                glob = True
            buf.append(ch)
            in_word = True
            i += 1
    flush()
    return toks


def _lex_redirect(command: str, i: int, toks: list[_Tok]) -> int:
    rest = command[i:]
    if rest.startswith(("<<", "<(", ">(")):
        raise _Floor(Reason.HIDDEN_COMMAND, "bash.hidden.heredoc_or_procsub")
    for op in (">>", ">|", ">&", "<&", "<>", ">", "<"):
        if rest.startswith(op):
            break
    else:  # pragma: no cover - the caller only calls on < or >
        raise _Floor(Reason.UNCLEAR, "bash.unclear.redirect")
    after = i + len(op)
    if op in (">&", "<&"):
        match = _FD_DUP.match(command, after)
        if match:
            toks.append(_Tok("redir", "dup"))
            return match.end()
        op = ">" if op == ">&" else "<"
    toks.append(_Tok("redir", ">" if op in (">|", "<>") else op))
    return after


@dataclass
class _Redirect:
    op: str
    target: _Word | None


@dataclass
class _Simple:
    assigns: list[str] = field(default_factory=list)
    words: list[_Word] = field(default_factory=list)
    redirects: list[_Redirect] = field(default_factory=list)
    pipe_prev: bool = False
    op_after: str = ""


_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_RESERVED = _words("if then else elif fi for while until do done case esac function select")
_RESERVED |= _words("{ } ! [[ ]] coproc in")


def _parse(toks: list[_Tok]) -> list[_Simple]:
    simples: list[_Simple] = []
    cur = _Simple()
    pending: str | None = None
    pipe_next = False

    def close(op: str) -> None:
        nonlocal cur, pipe_next
        empty = not (cur.words or cur.assigns or cur.redirects)
        if empty:
            if op != ";" or pipe_next:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.empty_command")
        else:
            cur.op_after = op
            simples.append(cur)
        pipe_next = op in ("|", "|&")
        cur = _Simple(pipe_prev=pipe_next)

    for tok in toks:
        if tok.kind == "redir":
            if pending is not None:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.redirect")
            if tok.text == "dup":
                cur.redirects.append(_Redirect("dup", None))
            else:
                pending = tok.text
        elif tok.kind == "word":
            assert tok.word is not None
            if pending is not None:
                cur.redirects.append(_Redirect(pending, tok.word))
                pending = None
            elif not cur.words and not tok.word.quoted and _ASSIGN.match(tok.word.text):
                cur.assigns.append(tok.word.text)
            else:
                cur.words.append(tok.word)
        else:
            if pending is not None:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.redirect")
            close(tok.text)
    if pending is not None:
        raise _Floor(Reason.UNCLEAR, "bash.unclear.redirect")
    if cur.words or cur.assigns or cur.redirects:
        simples.append(cur)
    elif pipe_next:
        raise _Floor(Reason.UNCLEAR, "bash.unclear.empty_command")
    return simples


# ------------------------------------------------------------------- program tables


def _program(text: str) -> str:
    name = re.split(r"[\\/]", text)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


_SYSTEM_BIN_DIRS = _words(
    "/bin /sbin /usr/bin /usr/sbin /usr/local/bin /usr/local/sbin /opt/homebrew/bin"
)

_PRIVILEGE = _words(
    "sudo su doas pkexec runas sudoedit runuser chroot nsenter unshare chown chgrp crontab at "
    "batch systemctl service launchctl chsh passwd useradd usermod userdel groupadd visudo "
    "setcap sysctl modprobe insmod rmmod iptables ufw shutdown reboot halt poweroff mount "
    "umount swapon swapoff losetup schtasks netsh diskpart"
)
_DESTRUCTIVE = _words(
    "rm rmdir unlink shred srm dd mke2fs mkswap fdisk sfdisk parted wipefs blkdiscard "
    "cryptsetup fsck truncate kill killall pkill skill del erase rd remove-item format trash "
    "trash-put"
)
_NETWORK = _words(
    "curl wget nc ncat netcat telnet ssh scp sftp rsync ftp tftp socat nmap ping ping6 "
    "traceroute tracepath mtr dig nslookup host whois aria2c lynx w3m links http https xh "
    "httpie gh hub glab aws gcloud az kubectl helm terraform tofu ansible ansible-playbook "
    "pulumi vercel netlify wrangler flyctl fly heroku firebase supabase doctl sam serverless "
    "sls railway deno npx bunx pnpx mosh"
)
_PACKAGE_MANAGERS = _words(
    "apt apt-get aptitude yum dnf zypper apk pacman snap flatpak choco winget scoop brew "
    "conda mamba micromamba gem pip pip3 pipx poetry pdm cpan cpanm luarocks composer nuget"
)
_PACKAGE_QUERY_SUBS = frozenset(
    {"list", "ls", "show", "freeze", "check", "inspect", "help", "debug", "hash", "info"}
)
_SECRET_PROGRAMS = _words(
    "printenv ssh-keygen ssh-add ssh-agent gpg gpg2 pass op vault security secret-tool cmdkey "
    "keyctl"
)
_HIDDEN_PROGRAMS = _words(
    "eval source . trap watch tmux screen parallel busybox expect osascript powershell pwsh cmd wsl"
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash"})
_NODE = re.compile(r"^(node|nodejs)$")
_PYTHON = re.compile(r"^(python|pypy)(\d+(\.\d+)*)?$|^py$")
_VERSION_FLAGS = frozenset({"--version", "-V", "-version", "--help", "-h"})

# Programs that run code the repository ships. Not floor in a trusted project.
_PROJECT_CODE = _words(
    "pytest py.test tox nox make gmake ninja just task rake rspec gradle gradlew mvn mvnw ant "
    "bazel jest vitest mocha mypy pylint eslint webpack vite rustc javac gcc g++ cc clang "
    "clang++"
)
_SCRIPT_RUNNERS = frozenset({"npm", "yarn", "pnpm", "bun"})
_NPM_NETWORK = _words(
    "install i add ci update up upgrade remove rm uninstall un publish login adduser link "
    "unlink exec x dlx create init audit outdated info view search dedupe deprecate unpublish "
    "token whoami"
)
_CARGO_NETWORK = _words(
    "install add publish update fetch login yank owner search rm remove vendor uninstall"
)
_CARGO_CODE = frozenset(
    {"test", "build", "run", "check", "bench", "clippy", "doc", "b", "t", "r", "c", "nextest"}
)
_GO_NETWORK = frozenset({"get", "install", "mod", "work", "clean"})
_GO_CODE = frozenset({"test", "build", "run", "vet", "generate"})
_DOTNET_NETWORK = frozenset({"restore", "add", "publish", "tool", "nuget", "workload", "new"})
_DOTNET_CODE = frozenset({"build", "test", "run", "watch", "msbuild"})
_UV_NETWORK = _words(
    "add remove sync lock pip tool python self publish init export venv tree cache"
)
_DOCKER_FLOOR = _words(
    "run exec pull push build buildx compose login logout cp rm rmi kill stop create start "
    "restart system volume network commit load save import export prune container image swarm "
    "service stack"
)
_PYTHON_NETWORK_MODULES = _words(
    "pip ensurepip pipx http.server ftplib smtpd xmlrpc.server webbrowser uv"
)
_INLINE_LETTERS = {
    "python": "c",
    "perl": "eE",
    "ruby": "e",
    "php": "r",
    "lua": "e",
    "node": "ep",
    "shell": "c",
    "rscript": "e",
}
_VALUE_OPTIONS = {
    "python": frozenset({"-W", "-X"}),
    "node": frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"}),
    "perl": frozenset({"-I"}),
    "ruby": frozenset({"-I", "-r"}),
}
_INLINE_LONG = frozenset({"--eval", "--print", "--command", "--execute", "--exec"})

_GIT_NO_ARG_GLOBALS = _words(
    "--no-pager -p -P --paginate --bare --no-replace-objects --literal-pathspecs --glob- "
    "pathspecs --noglob-pathspecs --icase-pathspecs --no-optional-locks --version --help -h"
)
_GIT_VALUE_GLOBALS = ("--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix")
_GIT_READONLY = _words(
    "status diff log show rev-parse ls-files blame annotate describe shortlog ls-tree cat- "
    "file rev-list name-rev merge-base show-ref count-objects check-ignore grep for-each-ref "
    "diff-tree diff-files diff-index whatchanged cherry version"
)
_GIT_NETWORK = _words(
    "push fetch pull clone ls-remote submodule send-email svn lfs daemon http-fetch http-push "
    "fetch-pack send-pack upload-pack receive-pack imap-send request-pull"
)
_GIT_REMOTE_NETWORK = frozenset(
    {"add", "set-url", "update", "remove", "rm", "rename", "prune", "set-head", "set-branches"}
)
_GIT_DESTRUCTIVE = frozenset(
    {"prune", "filter-branch", "filter-repo", "update-ref", "replace", "fast-import"}
)
_GIT_LAUNCHERS = frozenset({"difftool", "mergetool", "gui", "instaweb", "web--browse", "citool"})
_GIT_BANNED_READ_OPTIONS = frozenset({"--output", "--ext-diff", "--open-files-in-pager", "--pager"})


@dataclass(frozen=True)
class _ReadOnly:
    banned_shorts: str = ""
    banned_longs: frozenset[str] = frozenset()
    banned_raw: frozenset[str] = frozenset()
    max_positional: int | None = None


_READONLY: dict[str, _ReadOnly] = {
    "ls": _ReadOnly(),
    "cat": _ReadOnly(),
    "head": _ReadOnly(),
    "tail": _ReadOnly(banned_shorts="fF", banned_longs=frozenset({"--follow"})),
    "wc": _ReadOnly(),
    "rg": _ReadOnly(banned_longs=frozenset({"--pre", "--hostname-bin"})),
    "grep": _ReadOnly(),
    "find": _ReadOnly(
        banned_raw=frozenset(
            {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0"}
            | {"-fprintf", "-fls"}
        )
    ),
    "pwd": _ReadOnly(),
    "echo": _ReadOnly(),
    "printf": _ReadOnly(),
    "which": _ReadOnly(),
    "type": _ReadOnly(),
    "file": _ReadOnly(banned_shorts="Cm", banned_longs=frozenset({"--compile", "--magic-file"})),
    "stat": _ReadOnly(),
    "diff": _ReadOnly(),
    "sort": _ReadOnly(
        banned_shorts="o", banned_longs=frozenset({"--output", "--compress-program"})
    ),
    "uniq": _ReadOnly(max_positional=1),
    "cut": _ReadOnly(),
    "tr": _ReadOnly(),
    "date": _ReadOnly(banned_shorts="s", banned_longs=frozenset({"--set"})),
    "tree": _ReadOnly(banned_shorts="o"),
    "du": _ReadOnly(),
    "df": _ReadOnly(),
    "realpath": _ReadOnly(),
    "readlink": _ReadOnly(),
    "dirname": _ReadOnly(),
    "basename": _ReadOnly(),
    "whoami": _ReadOnly(),
    "uname": _ReadOnly(),
    "hostname": _ReadOnly(max_positional=0),
    "id": _ReadOnly(),
    "sleep": _ReadOnly(),
    "seq": _ReadOnly(),
    "true": _ReadOnly(),
    "false": _ReadOnly(),
    "nproc": _ReadOnly(),
    "sha256sum": _ReadOnly(),
    "md5sum": _ReadOnly(),
    "cd": _ReadOnly(),
    "pushd": _ReadOnly(),
}


@dataclass
class _Args:
    raw: list[str]
    longs: set[str]
    shorts: set[str]
    pos: list[str]

    @classmethod
    def of(cls, words: list[_Word]) -> _Args:
        raw: list[str] = []
        longs: set[str] = set()
        shorts: set[str] = set()
        pos: list[str] = []
        after_dashes = False
        for word in words:
            text = word.text
            if after_dashes or not text.startswith("-") or text == "-":
                pos.append(text)
            elif text == "--":
                after_dashes = True
            else:
                raw.append(text)
                if text.startswith("--"):
                    longs.add(text.split("=", 1)[0])
                else:
                    shorts.update(text[1:])
        return cls(raw, longs, shorts, pos)


# ----------------------------------------------------------------- the bash analysis


class _State:
    def __init__(self, env: _Env) -> None:
        self.env = env
        self.cwds: list[str] = [env.cwd]
        self.proven = True
        self.project_code = False
        self.seen_command = False
        self.op_after = ""
        self.fallback: list[str] | None = None

    def add_cwd(self, new: list[str]) -> None:
        """After a ``cd``: the shell is in one of the new folders, or (if ``cd`` failed) still
        where it was. Only ``cd X && ...`` runs the next command in the new folder alone."""
        if self.op_after == "&&":
            if self.fallback is None:
                self.fallback = list(self.cwds)
            self.cwds = list(dict.fromkeys(new))
        else:
            self._merge(new)

    def _merge(self, new: list[str]) -> None:
        merged = list(dict.fromkeys([*self.cwds, *new]))
        if len(merged) > MAX_CWD_CANDIDATES:
            raise _Floor(Reason.UNCLEAR, "bash.unclear.cwd_tracking")
        self.cwds = merged

    def settle(self) -> None:
        """The end of a ``&&`` chain: a failed ``cd`` in it leaves the old folder possible."""
        if self.op_after != "&&" and self.fallback is not None:
            fallback, self.fallback = self.fallback, None
            self._merge(fallback)


def _looks_like_path(text: str) -> bool:
    return (
        "/" in text
        or "\\" in text
        or text in (".", "..")
        or text.startswith(("~", "."))
        or bool(re.match(r"^[A-Za-z]:", text))
    )


def _glob_prefix(text: str) -> str:
    cut = min((text.find(c) for c in "*?[" if c in text), default=len(text))
    prefix = text[:cut]
    sep = max(prefix.rfind("/"), prefix.rfind("\\"))
    return prefix[: sep + 1] if sep >= 0 else ""


def _resolve(text: str, cwd: str, env: _Env) -> str:
    joined = text if os.path.isabs(text) else os.path.join(cwd, text)
    return env.fs.realpath(joined)


def _check_word(word: _Word, st: _State, *, force: bool = False) -> None:
    """Floor when a word that names a path leaves the project or is protected."""
    env = st.env
    text = word.text
    if not force and text.startswith("-"):
        if "=" not in text:
            return
        text = text.split("=", 1)[1]
    if not text:
        return
    if text in _DEV_OK:
        return
    if text.startswith(("/dev/tcp/", "/dev/udp/")):
        raise _Floor(Reason.NETWORK, "bash.network.dev_tcp")
    if word.glob:
        if ".." in re.split(r"[\\/]", text):
            raise _Floor(Reason.OUTSIDE_PROJECT, "path.glob_dotdot")
        text = _glob_prefix(text) or "."
    if force or _looks_like_path(text):
        for cwd in st.cwds:
            env.path_floor(_resolve(text, cwd, env))
        return
    if len(text) > 255 or re.search(r"\s", text):
        return
    for cwd in st.cwds:  # a plain name can still be a symbolic link that leaves the project
        candidate = os.path.join(cwd, text)
        if env.fs.lexists(candidate):
            env.path_floor(env.fs.realpath(candidate))


def _redirect(redirect: _Redirect, st: _State) -> None:
    if redirect.target is None:
        return
    _check_word(redirect.target, st, force=True)
    if redirect.op != "<" and redirect.target.text not in _DEV_OK:
        st.proven = False


def _interp_kind(family: str, args: list[_Word]) -> tuple[str, str]:
    """What an interpreter is asked to run: ``(kind, detail)``.

    ``kind`` is ``inline`` (code in the command line), ``stdin`` (code from standard input),
    ``module``, ``file`` or ``version``. Options that come after the script or module belong to
    that script and are not read here.
    """
    letters = _INLINE_LETTERS[family]
    value_options = _VALUE_OPTIONS.get(family, frozenset())
    index = 0
    while index < len(args):
        text = args[index].text
        if text in value_options:
            index += 2
            continue
        if text == "--":
            index += 1
            break
        if text == "-":
            return "stdin", ""
        if text.startswith("--"):
            if text.split("=", 1)[0] in _INLINE_LONG:
                return "inline", text
            if text in _VERSION_FLAGS:
                return "version", text
            index += 1
            continue
        if text.startswith("-"):
            if text == "-m" and family == "python":
                module = args[index + 1].text if index + 1 < len(args) else ""
                return "module", module
            if text.startswith("-m") and family == "python":
                return "module", text[2:]
            if text in _VERSION_FLAGS:
                return "version", text
            if any(letter in text[1:] for letter in letters):
                return "inline", text
            index += 1
            continue
        return "file", text
    if index < len(args):
        return "file", args[index].text
    return "stdin", ""


def _unwrap(words: list[_Word], st: _State) -> tuple[list[_Word], bool]:
    """Strip wrappers (``env``, ``timeout``, ``xargs`` ...) down to the program that really runs.

    Returns the words from that program on, and whether its arguments are partly unknown
    (``xargs``).
    """
    dynamic = False
    for _ in range(MAX_WRAPPER_DEPTH):
        program = _program(words[0].text)
        rest = words[1:]
        if program == "env":
            index = 0
            while index < len(rest):
                text = rest[index].text
                if text in ("-u", "--unset"):
                    index += 2
                elif text in ("-i", "-0", "-v", "--ignore-environment", "--null", "--debug", "--"):
                    index += 1
                elif text.startswith("-"):
                    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
                elif _ASSIGN.match(text):
                    index += 1
                else:
                    break
            rest = rest[index:]
            if not rest:
                raise _Floor(Reason.SECRETS, "bash.secrets.env_dump")
        elif program == "command":
            if rest and rest[0].text in ("-v", "-V"):
                return [_Word("type"), *rest[1:]], dynamic
            if rest and rest[0].text == "-p":
                rest = rest[1:]
            if not rest:
                return words, dynamic
        elif program in ("builtin", "exec", "nohup", "setsid", "time"):
            if rest and rest[0].text == "--":
                rest = rest[1:]
            if program == "time" and rest and rest[0].text == "-p":
                rest = rest[1:]
            if rest and rest[0].text.startswith("-"):
                raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
            if not rest:
                return words, dynamic
        elif program == "nice":
            if len(rest) >= 2 and rest[0].text == "-n":
                rest = rest[2:]
            elif rest and re.match(r"^-\d+$|^--adjustment=", rest[0].text):
                rest = rest[1:]
            if not rest or rest[0].text.startswith("-"):
                raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
        elif program == "timeout":
            index = 0
            while index < len(rest) and rest[index].text.startswith("-"):
                text = rest[index].text
                if text in ("-s", "-k", "--signal", "--kill-after"):
                    index += 2
                elif text in ("--preserve-status", "--foreground", "-v", "--verbose") or (
                    text.startswith(("--signal=", "--kill-after="))
                ):
                    index += 1
                else:
                    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
            rest = rest[index + 1 :]
            if not rest:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
        elif program == "stdbuf":
            index = 0
            while index < len(rest) and re.match(r"^-[ioe]", rest[index].text):
                index += 2 if rest[index].text in ("-i", "-o", "-e") else 1
            rest = rest[index:]
            if not rest:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
        elif program == "xargs":
            dynamic = True
            index = 0
            while index < len(rest) and rest[index].text.startswith("-"):
                text = rest[index].text
                if text == "--":
                    index += 1
                    break
                if text in ("-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"):
                    if text == "-a" and index + 1 < len(rest):
                        _check_word(rest[index + 1], st, force=True)
                    index += 2
                elif re.match(r"^-[InPLdEs]\S", text) or text in (
                    "-0",
                    "-t",
                    "-r",
                    "-p",
                    "-x",
                    "--null",
                    "--verbose",
                    "--no-run-if-empty",
                ):
                    index += 1
                else:
                    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")
            rest = rest[index:] or [_Word("echo")]
        else:
            return words, dynamic
        words = rest
    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_depth")


def _git(args: list[_Word], st: _State) -> bool:
    """Rules for ``git``. Returns whether the call is proven read-only; raises on the floor."""
    index = 0
    while index < len(args):
        text = args[index].text
        if text in _GIT_NO_ARG_GLOBALS:
            index += 1
        elif text == "-C":
            if index + 1 >= len(args):
                raise _Floor(Reason.UNCLEAR, "bash.unclear.git_option")
            _check_word(args[index + 1], st, force=True)
            index += 2
        elif text == "-c" or text.startswith(("-c", "--config-env")):
            raise _Floor(Reason.HIDDEN_COMMAND, "bash.hidden.git_config_option")
        elif text.split("=", 1)[0] in _GIT_VALUE_GLOBALS:
            if "=" not in text:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.git_option")
            index += 1
        elif text.startswith("-"):
            raise _Floor(Reason.UNCLEAR, "bash.unclear.git_option")
        else:
            break
    if index >= len(args):
        return all(a.text in ("--version", "--help", "-h") for a in args) and bool(args)
    sub = args[index].text
    opts = _Args.of(args[index + 1 :])
    flags = opts.longs | {f"-{c}" for c in opts.shorts}

    if sub in _GIT_LAUNCHERS:
        raise _Floor(Reason.HIDDEN_COMMAND, "bash.hidden.git_launcher")
    if sub in _GIT_NETWORK:
        raise _Floor(Reason.NETWORK, "bash.network.git")
    if sub == "remote" and opts.pos and opts.pos[0] in _GIT_REMOTE_NETWORK:
        raise _Floor(Reason.NETWORK, "bash.network.git_remote")
    if sub.startswith("credential"):
        raise _Floor(Reason.SECRETS, "bash.secrets.git_credential")
    if sub in _GIT_DESTRUCTIVE:
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_history")
    if sub == "reset" and flags & {"--hard", "--merge", "--keep"}:
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_reset_hard")
    if sub == "clean" and not (flags & {"--dry-run", "-n"}):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_clean")
    if sub == "checkout" and (
        "--" in [w.text for w in args[index + 1 :]]
        or "." in opts.pos
        or flags & {"--force", "-f", "-B", "--ours", "--theirs"}
    ):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_checkout")
    if sub == "switch" and flags & {"--force", "-f", "--discard-changes", "-C", "--force-create"}:
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_switch")
    if sub == "restore" and (not (flags & {"--staged", "-S"}) or flags & {"--worktree", "-W"}):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_restore")
    if sub == "stash" and opts.pos and opts.pos[0] in ("drop", "clear"):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_stash")
    if sub == "branch" and ("D" in opts.shorts or flags & {"--force", "-f", "-M"}):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_branch")
    if sub == "tag" and flags & {"--delete", "-d", "--force", "-f"}:
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_tag")
    if sub == "reflog" and opts.pos and opts.pos[0] in ("expire", "delete"):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_reflog")
    if sub == "gc" and any(f.startswith("--prune") for f in opts.longs):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_gc")
    if sub == "rm" and flags & {"--force", "-f"}:
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_rm")
    if sub == "worktree" and opts.pos and opts.pos[0] in ("remove", "prune"):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.git_worktree")
    if sub == "config":
        reading = flags & {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin"}
        scope = flags & {"--global", "--system", "--file", "-f", "--worktree"}
        writes = flags & {"--add", "--unset", "--unset-all", "--edit", "-e", "--replace-all"} | (
            flags & {"--rename-section", "--remove-section"}
        )
        if scope or writes or (not reading and len(opts.pos) >= 2):
            raise _Floor(Reason.PROTECTED_PATH, "bash.protected.git_config")
        return False
    if sub in _GIT_READONLY:
        return not (flags & _GIT_BANNED_READ_OPTIONS or "-O" in flags)
    if sub == "branch":
        listing = bool(flags & {"--list", "-l"})
        mutating = flags & {
            "-d",
            "-m",
            "-c",
            "-C",
            "-u",
            "--set-upstream-to",
            "--unset-upstream",
            "--edit-description",
            "--move",
            "--copy",
            "--delete",
        }
        return not mutating and (listing or not opts.pos)
    return sub in ("--version", "version") or (sub in ("--help", "-h") and len(args) == index + 1)


def _readonly_ok(program: str, args: _Args) -> bool:
    spec = _READONLY.get(program)
    if spec is None:
        return False
    if any(c in spec.banned_shorts for c in args.shorts):
        return False
    if args.longs & spec.banned_longs:
        return False
    if set(args.raw) & spec.banned_raw:
        return False
    return not (spec.max_positional is not None and len(args.pos) > spec.max_positional)


def _program_rules(words: list[_Word], dynamic: bool, st: _State, depth: int = 0) -> None:
    """Apply the floor and the allow rules to one program with its arguments."""
    env = st.env
    text = words[0].text
    program = _program(text)
    args_words = words[1:]
    args = _Args.of(args_words)
    if not text or re.search(r"\s", text) or any(ord(c) < 32 for c in text):
        raise _Floor(Reason.UNCLEAR, "bash.unclear.program_name")
    if not text.isascii():
        raise _Floor(Reason.HIDDEN_COMMAND, "bash.hidden.non_ascii_program")
    if program in _RESERVED:
        raise _Floor(Reason.UNCLEAR, "bash.unclear.compound")

    project_path_program = False
    if "/" in text or "\\" in text:
        directory = text.replace("\\", "/").rsplit("/", 1)[0] or "/"
        if directory not in _SYSTEM_BIN_DIRS:
            for cwd in st.cwds:
                env.path_floor(_resolve(text, cwd, env))
            project_path_program = True

    if program in _PRIVILEGE:
        raise _Floor(Reason.PRIVILEGE, "bash.privilege")
    if program in _HIDDEN_PROGRAMS:
        raise _Floor(Reason.HIDDEN_COMMAND, "bash.hidden.program")

    family = None
    if _PYTHON.match(program):
        family = "python"
    elif program == "perl":
        family = "perl"
    elif program == "ruby":
        family = "ruby"
    elif program == "php":
        family = "php"
    elif program == "lua":
        family = "lua"
    elif _NODE.match(program):
        family = "node"
    elif program in _SHELLS:
        family = "shell"
    elif program == "rscript":
        family = "rscript"

    # A version flag alone asks the program for nothing but its version.
    if len(args_words) == 1 and args_words[0].text in _VERSION_FLAGS and not project_path_program:
        return

    if family is not None:
        kind, detail = _interp_kind(family, args_words)
        if kind in ("inline", "stdin"):
            raise _Floor(Reason.HIDDEN_COMMAND, f"bash.hidden.{family}_{kind}")
        if kind == "module" and detail in _PYTHON_NETWORK_MODULES:
            raise _Floor(Reason.NETWORK, "bash.network.python_module")
        if kind == "version":
            return
        st.project_code = True
        st.proven = False
        for word in args_words:
            _check_word(word, st)
        return

    if program in _DESTRUCTIVE or program.startswith("mkfs"):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.program")
    if program in ("chmod",) and (args.shorts & {"R"} or "--recursive" in args.longs):
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.chmod_recursive")
    if program in _NETWORK:
        raise _Floor(Reason.NETWORK, "bash.network.program")
    if program in _PACKAGE_MANAGERS and not (args.pos and args.pos[0] in _PACKAGE_QUERY_SUBS):
        raise _Floor(Reason.NETWORK, "bash.network.package_manager")
    if program in ("docker", "podman") and args.pos and args.pos[0] in _DOCKER_FLOOR:
        raise _Floor(Reason.NETWORK, "bash.network.container")
    if program in _SECRET_PROGRAMS:
        raise _Floor(Reason.SECRETS, "bash.secrets.program")
    if program == "history":
        raise _Floor(Reason.SECRETS, "bash.secrets.history")
    if program in ("set", "export", "declare", "typeset", "compgen"):
        if program == "set":
            dumping = not args.pos
        elif program == "compgen":
            dumping = "e" in args.shorts
        else:
            dumping = not args.pos or "p" in args.shorts
        if dumping:
            raise _Floor(Reason.SECRETS, "bash.secrets.env_dump")

    sub = args.pos[0] if args.pos else ""
    if program in _SCRIPT_RUNNERS:
        if sub in _NPM_NETWORK:
            raise _Floor(Reason.NETWORK, "bash.network.package_manager")
        st.project_code = True
        st.proven = False
    elif program == "cargo":
        if sub in _CARGO_NETWORK:
            raise _Floor(Reason.NETWORK, "bash.network.package_manager")
        st.project_code = sub in _CARGO_CODE or st.project_code
    elif program == "go":
        if sub in _GO_NETWORK:
            raise _Floor(Reason.NETWORK, "bash.network.package_manager")
        st.project_code = sub in _GO_CODE or st.project_code
    elif program == "dotnet":
        if sub in _DOTNET_NETWORK:
            raise _Floor(Reason.NETWORK, "bash.network.package_manager")
        st.project_code = sub in _DOTNET_CODE or st.project_code
    elif program == "uv":
        if sub in _UV_NETWORK:
            raise _Floor(Reason.NETWORK, "bash.network.package_manager")
        st.project_code = sub == "run" or st.project_code
    elif program == "bundle":
        if sub in ("install", "update", "add", "remove", "lock"):
            raise _Floor(Reason.NETWORK, "bash.network.package_manager")
        st.project_code = sub == "exec" or st.project_code
    elif program in _PROJECT_CODE:
        st.project_code = True
    if project_path_program:
        st.project_code = True

    proven = False
    if program == "git":
        proven = _git(args_words, st)
    elif program == "find":
        proven = _find(args_words, st, depth)
    elif program in ("cd", "pushd"):
        proven = _cd(args_words, st)
    elif not project_path_program and not dynamic:
        proven = _readonly_ok(program, args)

    if program not in ("cd", "pushd"):
        for word in args_words:
            _check_word(word, st)
    if not proven or dynamic:
        st.proven = False


def _find(args_words: list[_Word], st: _State, depth: int) -> bool:
    raw = {w.text for w in args_words if w.text.startswith("-")}
    if "-delete" in raw:
        raise _Floor(Reason.DESTRUCTIVE, "bash.destructive.find_delete")
    for index, word in enumerate(args_words):
        if word.text in ("-exec", "-execdir", "-ok", "-okdir"):
            inner: list[_Word] = []
            for follower in args_words[index + 1 :]:
                if follower.text in (";", "+"):
                    break
                inner.append(follower)
            if not inner:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.find_exec")
            if depth >= MAX_WRAPPER_DEPTH:
                raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_depth")
            unwrapped, _ = _unwrap(inner, st)
            _program_rules(unwrapped, True, st, depth + 1)
            return False
    return _readonly_ok("find", _Args.of(args_words))


def _cd(args_words: list[_Word], st: _State) -> bool:
    env = st.env
    positional = [w for w in args_words if not w.text.startswith("-") or w.text == "-"]
    if len(positional) > 1:
        raise _Floor(Reason.UNCLEAR, "bash.unclear.cd")
    if not positional:
        target = _Word(env.home)
    elif positional[0].text == "-":
        raise _Floor(Reason.UNCLEAR, "bash.unclear.cd")
    else:
        target = positional[0]
    if target.glob:
        raise _Floor(Reason.UNCLEAR, "bash.unclear.cd")
    new: list[str] = []
    for cwd in st.cwds:
        real = _resolve(target.text, cwd, env)
        env.path_floor(real)
        new.append(real)
    st.add_cwd(new)
    return True


def _analyze_simple(simple: _Simple, st: _State) -> None:
    st.op_after = simple.op_after
    _analyze_words(simple, st)
    st.settle()


def _analyze_words(simple: _Simple, st: _State) -> None:
    st.seen_command = True
    for redirect in simple.redirects:
        _redirect(redirect, st)
    if simple.assigns:
        st.proven = False
    if not simple.words:
        st.proven = False
        return
    words, dynamic = _unwrap(simple.words, st)
    _program_rules(words, dynamic, st)


def _decide_bash(call: ToolCall, env: _Env) -> RuleDecision:
    command = call.input.get("command")
    if not isinstance(command, str):
        return _ask(Reason.UNCLEAR, "bash.unclear.no_command")
    if len(command.encode("utf-8", "replace")) > MAX_COMMAND_BYTES:
        return _ask(Reason.TOO_LARGE, "bash.too_large")
    for char in command:
        category = unicodedata.category(char)
        if category == "Cf" or (category == "Cc" and char not in "\t\n"):
            return _ask(Reason.HIDDEN_COMMAND, "bash.hidden.control_char")
    st = _State(env)
    try:
        simples = _parse(_lex(command, env))
        for simple in simples:
            _analyze_simple(simple, st)
    except _Floor as floor:
        return _ask(floor.reason, floor.rule_id)
    if not st.seen_command:
        return _ask(Reason.UNCLEAR, "bash.unclear.empty")
    if st.project_code and not env.ctx.project_trusted:
        return _ask(Reason.UNTRUSTED_PROJECT_CODE, "bash.untrusted_project_code")
    if st.proven:
        return _allow(Reason.READ_ONLY_COMMAND, "allow.bash.read_only")
    return RuleDecision(Outcome.JUDGE, By.RULE, Reason.UNCLEAR, "bash.not_proven")


# ------------------------------------------------------------------ write and edit


def _decide_edit(call: ToolCall, env: _Env) -> RuleDecision:
    from pi_agent_core.coding_tools.path_utils import resolve_to_cwd

    path = call.input.get("path")
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return _ask(Reason.UNCLEAR, "edit.unclear.no_path")
    absolute = resolve_to_cwd(env.expand_home(path), env.cwd)
    real = env.fs.realpath(absolute)
    try:
        env.path_floor(real)
    except _Floor as floor:
        return _ask(floor.reason, floor.rule_id)
    if env.fs.lexists(real) and not env.fs.in_git_worktree(real):
        return _ask(Reason.OVERWRITE_NO_GIT, "edit.overwrite_no_git")
    return _allow(Reason.IN_PROJECT_EDIT, "allow.edit.in_project")


# --------------------------------------------------------------------- the pipeline


_OWN_TOOLS = frozenset({"bash", "write", "edit"})


def _decide(call: ToolCall, ctx: AutoContext) -> RuleDecision:
    if not needs_permission("ask", call.annotations):
        return _allow(Reason.HARMLESS, "allow.harmless")
    if not isinstance(call.input, Mapping):
        return _ask(Reason.UNCLEAR, "pipeline.bad_input")
    if call.name == "workflow":
        return _ask(Reason.WORKFLOW, "floor.workflow")
    if not call.builtin or call.name not in _OWN_TOOLS:
        return _ask(Reason.UNDECLARED_TOOL, "floor.undeclared_tool")
    env = _Env(ctx)
    if not env.inside(env.cwd):
        return _ask(Reason.OUTSIDE_PROJECT, "floor.cwd_outside_project")
    if call.name == "bash":
        return _decide_bash(call, env)
    return _decide_edit(call, env)


def decide(call: ToolCall, ctx: AutoContext) -> RuleDecision:
    """The rules' answer for one call: ALLOW, ASK, or JUDGE (a model may still decide)."""
    try:
        return _decide(call, ctx)
    except Exception:  # fail closed: a bug in the rules is an ASK, never an ALLOW
        return _ask(Reason.INTERNAL_ERROR, "pipeline.internal_error")


def decide_rules_only(call: ToolCall, ctx: AutoContext) -> RuleDecision:
    """``decide`` for ``classifier = "rules"``: a JUDGE is an ASK ``unclear``."""
    decision = decide(call, ctx)
    if decision.outcome is Outcome.JUDGE:
        return _ask(Reason.UNCLEAR, "pipeline.unclear")
    return decision
