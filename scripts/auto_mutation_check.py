#!/usr/bin/env python3
# The mutation texts are exact source lines, so they can be long.
# ruff: noqa: E501
"""Mutation check for the Auto rules: break one rule at a time; a test must notice each break.

    python scripts/auto_mutation_check.py              # run every mutation (about two minutes)
    python scripts/auto_mutation_check.py --list       # print the mutations
    python scripts/auto_mutation_check.py --check      # only check that each mutation still applies
    python scripts/auto_mutation_check.py --only floor # run the mutations whose name contains "floor"

Each mutation is an exact text replacement in ``auto_rules.py`` (or ``auto_reasons.py``). The
changed copy of the package goes to a temporary directory and the Auto tests run against it
(``PYTHONPATH``), so the working tree is never changed. A mutation that no test notices
"survives" and the script exits with 1: either a test is missing, or the code is dead.

The mutations break: each floor rule, the order of the steps, each refusal of the parser (hidden
commands, unreadable commands, unknown variables), the path logic, the wrappers, the fail-closed
paths. Run it again after any change to ``auto_rules.py``; a text that no longer matches is an
error, not a pass.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "packages" / "pi-agent-cli"
TESTS = [
    "packages/pi-agent-cli/tests/test_auto_rules.py",
    "packages/pi-agent-cli/tests/test_auto_reasons.py",
    "packages/pi-agent-cli/tests/test_auto_corpus.py",
]


@dataclass(frozen=True)
class Mutation:
    name: str
    old: str
    new: str
    nth: int | None = None  # which match to change when the text appears more than once
    file: str = "auto_rules.py"


def m(name: str, old: str, new: str, nth: int | None = None, file: str = "auto_rules.py"):
    return Mutation(name, old, new, nth, file)


MUTATIONS: list[Mutation] = [
    # ---- the pipeline: steps and their order
    m("step1: no harmless step", 'if not needs_permission("ask", call.annotations):', "if False:"),
    m(
        "step1: harmless means anything",
        'if not needs_permission("ask", call.annotations):',
        "if True:",
    ),
    m("floor: workflow not asked", 'if call.name == "workflow":', "if False:"),
    m(
        "floor: builtin flag ignored",
        "if not call.builtin or call.name not in _OWN_TOOLS:",
        "if call.name not in _OWN_TOOLS:",
    ),
    m(
        "floor: any name is ours",
        "if not call.builtin or call.name not in _OWN_TOOLS:",
        "if not call.builtin:",
    ),
    m("floor: sub-agent folder not checked", "if not env.inside(env.cwd):", "if False:"),
    m("floor: input type not checked", "if not isinstance(call.input, Mapping):", "if False:"),
    m(
        "order: a floor hit becomes a judge",
        "    except _Floor as floor:\n        return _ask(floor.reason, floor.rule_id)\n    if not st.seen_command:",
        "    except _Floor:\n        return RuleDecision(Outcome.JUDGE, By.RULE, Reason.UNCLEAR, 'x')\n    if not st.seen_command:",
    ),
    m(
        "order: outside check before protected check",
        '        hit = self.protected_hit(real)\n        if hit is not None:\n            raise _Floor(*hit)\n        if not self.inside(real):\n            raise _Floor(Reason.OUTSIDE_PROJECT, "path.outside_project")',
        '        if not self.inside(real):\n            raise _Floor(Reason.OUTSIDE_PROJECT, "path.outside_project")\n        hit = self.protected_hit(real)\n        if hit is not None:\n            raise _Floor(*hit)',
    ),
    m(
        "order: untrusted project check removed",
        "if st.project_code and not env.ctx.project_trusted:",
        "if False:",
    ),
    m(
        "order: untrusted check ignores the flag",
        "if st.project_code and not env.ctx.project_trusted:",
        "if st.project_code and False:",
    ),
    m(
        "fail-closed: exceptions escape decide",
        "    except Exception:  # fail closed",
        "    except KeyError:  # fail closed",
    ),
    m(
        "fail-closed: internal error is an allow",
        'return _ask(Reason.INTERNAL_ERROR, "pipeline.internal_error")',
        'return _allow(Reason.HARMLESS, "x")',
    ),
    m(
        "fail-closed: judge stays judge in rules-only",
        "if decision.outcome is Outcome.JUDGE:",
        "if False:",
    ),
    m(
        "fail-closed: an ask is not by the floor",
        "return RuleDecision(Outcome.ASK, By.FLOOR, reason, rule_id)",
        "return RuleDecision(Outcome.ASK, By.RULE, reason, rule_id)",
    ),
    m("fail-closed: bash command type", "if not isinstance(command, str):", "if False:"),
    m("fail-closed: empty command allowed", "if not st.seen_command:", "if False:"),
    m(
        "fail-closed: size limit",
        'if len(command.encode("utf-8", "replace")) > MAX_COMMAND_BYTES:',
        "if False:",
    ),
    m(
        "fail-closed: control characters",
        'if category == "Cf" or (category == "Cc" and char not in "\\t\\n"):',
        "if False:",
    ),
    m(
        "fail-closed: unproven becomes allowed",
        "    if st.proven:\n        return _allow(Reason.READ_ONLY_COMMAND",
        "    if True:\n        return _allow(Reason.READ_ONLY_COMMAND",
    ),
    # ---- paths
    m(
        "path: sibling folder with the same prefix",
        "return p.startswith(r.rstrip(os.sep) + os.sep)",
        "return p.startswith(r)",
    ),
    m(
        "path: the folder itself is not inside",
        "    if p == r:\n        return True",
        "    if False:\n        return True",
    ),
    m(
        "path: filesystem root accepted as a root",
        "self.roots = [r for r in roots if not _is_fs_root(r)]",
        "self.roots = roots",
    ),
    m("path: extra roots ignored", "(ctx.cwd, *ctx.extra_roots)", "(ctx.cwd,)"),
    m(
        "path: outside check removed",
        '        if not self.inside(real):\n            raise _Floor(Reason.OUTSIDE_PROJECT, "path.outside_project")',
        '        if False:\n            raise _Floor(Reason.OUTSIDE_PROJECT, "path.outside_project")',
    ),
    m(
        "path: protected check removed",
        "        hit = self.protected_hit(real)\n        if hit is not None:\n            raise _Floor(*hit)",
        "        hit = None\n        if hit is not None:\n            raise _Floor(*hit)",
    ),
    m(
        "path: case not folded in component names",
        "parts = [p.lower() for p in re.split",
        "parts = [p for p in re.split",
    ),
    m("protected: secret folders", "if _SECRET_DIRS.intersection(parts):", "if False:"),
    m("protected: .env files", 'if base.startswith(".env"):', "if False:"),
    m("protected: secret file names", "if _SECRET_NAME.match(base):", "if False:"),
    m("protected: .git folder", 'if ".git" in parts:', "if False:"),
    m("protected: .pi folders", 'if ".pi" in parts or ".pi-python" in parts:', "if False:"),
    m("protected: .gitmodules", 'if base == ".gitmodules":', "if False:"),
    m("protected: shell start files", "if base in _SHELL_START:", "if False:"),
    m(
        "protected: home config folder",
        'if _is_inside(real, os.path.join(self.home, ".config")):',
        "if False:",
    ),
    m("protected: the agent's own home", "if _is_inside(real, self.pi_home):", "if False:"),
    m(
        "protected: listed gated resources",
        "if any(_is_inside(real, item) for item in self.protected):",
        "if False:",
    ),
    m(
        "protected: extra globs",
        '        slashed = real.replace("\\\\", "/")\n        if any(',
        '        slashed = real.replace("\\\\", "/")\n        if False and any(',
    ),
    m("path: ~ not expanded in tool paths", 'if text.startswith(("~/", "~\\\\")):', "if False:"),
    m("path: ~ not expanded for a bare tilde", 'if text == "~":', "if False:"),
    m("path: glob followed by dotdot", 'if ".." in re.split(r"[\\\\/]", text):', "if False:"),
    m("path: plain name that is a link", "if env.fs.lexists(candidate):", "if False:"),
    m("path: value of --option=path", 'text = text.split("=", 1)[1]', "return"),
    m("path: /dev/tcp", 'if text.startswith(("/dev/tcp/", "/dev/udp/")):', "if False:"),
    m(
        "path: dotfiles are not paths",
        '        or text.startswith(("~", "."))',
        '        or text.startswith("~")',
    ),
    m(
        "path: program path outside the project",
        "if directory not in _SYSTEM_BIN_DIRS:",
        "if False:",
    ),
    m(
        "path: write path not resolved with the home",
        "resolve_to_cwd(env.expand_home(path), env.cwd)",
        "resolve_to_cwd(path, env.cwd)",
    ),
    m(
        "write: floor not applied",
        "    try:\n        env.path_floor(real)\n    except _Floor as floor:\n        return _ask(floor.reason, floor.rule_id)\n    if env.fs.lexists(real)",
        "    if env.fs.lexists(real)",
    ),
    m(
        "write: overwrite outside git",
        "if env.fs.lexists(real) and not env.fs.in_git_worktree(real):",
        "if False:",
    ),
    m(
        "write: path type",
        'if not isinstance(path, str) or not path.strip() or "\\x00" in path:',
        "if False:",
    ),
    # ---- cd tracking
    m(
        "cd: failed cd forgotten",
        "        else:\n            self._merge(new)",
        "        else:\n            self.cwds = list(new)",
    ),
    m(
        "cd: && chain never settles",
        'if self.op_after != "&&" and self.fallback is not None:',
        "if False:",
    ),
    m("cd: too many candidates", "if len(merged) > MAX_CWD_CANDIDATES:", "if False:"),
    m(
        "cd: target not checked",
        "        real = _resolve(target.text, cwd, env)\n        env.path_floor(real)",
        "        real = _resolve(target.text, cwd, env)",
    ),
    m("cd: several targets", "if len(positional) > 1:", "if False:"),
    m("cd: cd -", 'elif positional[0].text == "-":', "elif False:"),
    # ---- the lexer: refusals
    m(
        "lex: command substitution",
        '        if nxt == "(":\n            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.subst")',
        '        if False:\n            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.subst")',
    ),
    m(
        "lex: backtick",
        '        elif ch == "`":\n            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.backtick")',
        '        elif False:\n            refuse(Reason.HIDDEN_COMMAND, "bash.hidden.backtick")',
    ),
    m(
        "lex: backtick in double quotes",
        '                elif c == "`":',
        "                elif False:",
    ),
    m("lex: $'...'", 'if nxt == "\'" and not in_dq:', "if False:"),
    m('lex: $"..."', "if nxt == '\"' and not in_dq:", "if False:"),
    m(
        "lex: here-document and process substitution",
        'if rest.startswith(("<<", "<(", ">(")):',
        "if False:",
    ),
    m("lex: line continuation", 'if i + 1 >= n or command[i + 1] == "\\n":', "if False:"),
    m(
        "lex: unterminated single quote",
        '            if close < 0:\n                refuse(Reason.UNCLEAR, "bash.unclear.quote")\n            buf.append',
        '            if False:\n                refuse(Reason.UNCLEAR, "bash.unclear.quote")\n            buf.append',
    ),
    m(
        "lex: unterminated double quote",
        "                if i >= n:\n                    refuse",
        "                if False:\n                    refuse",
    ),
    m("lex: continuation in double quotes", 'if nxt == "\\n" or not nxt:', "if False:"),
    m(
        "lex: unknown variable",
        '            refuse(Reason.UNCLEAR, "bash.unclear.variable")',
        '            return "", match.end()',
    ),
    m(
        "lex: unknown ${...}",
        '            refuse(Reason.UNCLEAR, "bash.unclear.expansion")',
        '            return "", index + 2',
    ),
    m("lex: special parameters", "if nxt and nxt in _SPECIAL_PARAMS:", "if False:"),
    m(
        "lex: $HOME not expanded",
        '            if name == "HOME":\n                return env.home, match.end()',
        "            if False:\n                return env.home, match.end()",
    ),
    m("lex: ~user", '        elif ch == "~" and not in_word:', "        elif False:"),
    m(
        "lex: brace expansion",
        'if close > 0 and not re.search(r"\\s", span) and ("," in span or ".." in span):',
        "if False:",
    ),
    m("lex: parentheses", '        elif ch in "()":', "        elif False:"),
    m("lex: ;;", 'if ch == ";" and command.startswith(";;", i):', "if False:"),
    m(
        "lex: a comment is not a comment",
        '        elif ch == "#" and not in_word:',
        "        elif False:",
    ),
    m("lex: fd duplication", '    if op in (">&", "<&"):', "    if False:"),
    # ---- the parser
    m("parse: reserved words", "if program in _RESERVED:", "if False:"),
    m("parse: empty command between operators", 'if op != ";" or pipe_next:', "if False:"),
    m(
        "parse: redirect without a target",
        '    if pending is not None:\n        raise _Floor(Reason.UNCLEAR, "bash.unclear.redirect")\n    if cur.words',
        "    if cur.words",
    ),
    m(
        "parse: assignment prefix",
        "elif not cur.words and not tok.word.quoted and _ASSIGN.match(tok.word.text):",
        "elif False:",
    ),
    m(
        "parse: a trailing pipe",
        "    elif pipe_next:\n        raise",
        "    elif False:\n        raise",
    ),
    m("parse: non-ASCII program name", "    if not text.isascii():", "    if False:"),
    m(
        "parse: program name with a space",
        'if not text or re.search(r"\\s", text) or any(ord(c) < 32 for c in text):',
        "if False:",
    ),
    m(
        "parse: redirect writes are not writes",
        'if redirect.op != "<" and redirect.target.text not in _DEV_OK:',
        "if False:",
    ),
    m(
        "parse: redirect target not checked",
        "    _check_word(redirect.target, st, force=True)\n    if redirect.op",
        "    if redirect.op",
    ),
    m(
        "parse: assignments keep the command proven",
        "    if simple.assigns:\n        st.proven = False",
        "    if False:\n        st.proven = False",
    ),
    # ---- wrappers
    m("wrap: privilege (program pass)", "\n    if program in _PRIVILEGE:", "\n    if False:"),
    m("wrap: env", '        if program == "env":', "        if False:"),
    m(
        "wrap: env dump",
        "            if not rest:\n                raise _Floor(Reason.SECRETS",
        "            if False:\n                raise _Floor(Reason.SECRETS",
    ),
    m(
        "wrap: env unknown option",
        '                elif text.startswith("-"):\n                    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")\n                elif _ASSIGN',
        '                elif False:\n                    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_option")\n                elif _ASSIGN',
    ),
    m("wrap: command", '        elif program == "command":', "        elif False:"),
    m("wrap: command -v", 'if rest and rest[0].text in ("-v", "-V"):', "if False:"),
    m(
        "wrap: exec nohup time",
        '        elif program in ("builtin", "exec", "nohup", "setsid", "time"):',
        "        elif False:",
    ),
    m("wrap: nice", '        elif program == "nice":', "        elif False:"),
    m("wrap: timeout", '        elif program == "timeout":', "        elif False:"),
    m("wrap: stdbuf", '        elif program == "stdbuf":', "        elif False:"),
    m("wrap: xargs", '        elif program == "xargs":', "        elif False:"),
    m(
        "wrap: xargs arguments are not unknown",
        "            dynamic = True",
        "            dynamic = False",
    ),
    m("wrap: depth limit", "for _ in range(MAX_WRAPPER_DEPTH):", "for _ in range(100):"),
    m(
        "wrap: depth limit raises nothing",
        '    raise _Floor(Reason.UNCLEAR, "bash.unclear.wrapper_depth")\n\n\ndef _git',
        "    return words, dynamic\n\n\ndef _git",
    ),
    m(
        "wrap: a dynamic call can be proven read-only",
        "    if not proven or dynamic:\n        st.proven = False",
        "    if not proven:\n        st.proven = False",
    ),
    # ---- programs
    m("prog: hidden programs", "if program in _HIDDEN_PROGRAMS:", "if False:"),
    m("prog: inline code and stdin", 'if kind in ("inline", "stdin"):', "if False:"),
    m(
        "prog: python network modules",
        'if kind == "module" and detail in _PYTHON_NETWORK_MODULES:',
        "if False:",
    ),
    m(
        "prog: interpreter runs project code",
        "        st.project_code = True\n        st.proven = False\n        for word in args_words:",
        "        st.proven = False\n        for word in args_words:",
    ),
    m("prog: python -m", 'if text == "-m" and family == "python":', "if False:"),
    m(
        "prog: python -W value skipped",
        "        if text in value_options:\n            index += 2\n            continue",
        "        if False:\n            index += 2\n            continue",
    ),
    m(
        "prog: destructive programs",
        'if program in _DESTRUCTIVE or program.startswith("mkfs"):',
        "if False:",
    ),
    m("prog: mkfs", 'program.startswith("mkfs")', "False"),
    m("prog: chmod -R", 'if program in ("chmod",) and', "if False and"),
    m("prog: network programs", "if program in _NETWORK:", "if False:"),
    m("prog: package managers", "if program in _PACKAGE_MANAGERS and not", "if False and not"),
    m("prog: container tools", 'if program in ("docker", "podman") and', "if False and"),
    m("prog: secret programs", "if program in _SECRET_PROGRAMS:", "if False:"),
    m("prog: history", 'if program == "history":', "if False:"),
    m(
        "prog: environment dumps",
        "        if dumping:\n            raise",
        "        if False:\n            raise",
    ),
    m("prog: npm network", "        if sub in _NPM_NETWORK:", "        if False:"),
    m("prog: cargo network", "        if sub in _CARGO_NETWORK:", "        if False:"),
    m("prog: go network", "        if sub in _GO_NETWORK:", "        if False:"),
    m("prog: dotnet network", "        if sub in _DOTNET_NETWORK:", "        if False:"),
    m("prog: uv network", "        if sub in _UV_NETWORK:", "        if False:"),
    m(
        "prog: bundle network",
        '        if sub in ("install", "update", "add", "remove", "lock"):',
        "        if False:",
    ),
    m(
        "prog: script runners are project code",
        '    if program in _SCRIPT_RUNNERS:\n        if sub in _NPM_NETWORK:\n            raise _Floor(Reason.NETWORK, "bash.network.package_manager")\n        st.project_code = True',
        '    if program in _SCRIPT_RUNNERS:\n        if sub in _NPM_NETWORK:\n            raise _Floor(Reason.NETWORK, "bash.network.package_manager")\n        pass',
    ),
    m(
        "prog: known project-code programs",
        "    elif program in _PROJECT_CODE:\n        st.project_code = True",
        "    elif program in _PROJECT_CODE:\n        pass",
    ),
    m(
        "prog: a program given by path is project code",
        "    if project_path_program:\n        st.project_code = True",
        "    if project_path_program:\n        pass",
    ),
    m(
        "prog: cargo test is project code",
        "st.project_code = sub in _CARGO_CODE or st.project_code",
        "pass",
    ),
    m(
        "prog: go test is project code",
        "st.project_code = sub in _GO_CODE or st.project_code",
        "pass",
    ),
    m(
        "prog: a version flag unlocks any program",
        "    if len(args_words) == 1 and args_words[0].text in _VERSION_FLAGS and not project_path_program:",
        "    if len(args_words) >= 1 and args_words[-1].text in _VERSION_FLAGS:",
    ),
    m(
        "prog: no version shortcut",
        "    if len(args_words) == 1 and args_words[0].text in _VERSION_FLAGS and not project_path_program:\n        return",
        "    if False:\n        return",
    ),
    m(
        "prog: read-only table ignored",
        "        proven = _readonly_ok(program, args)",
        "        proven = True",
    ),
    m(
        "prog: read-only banned short flags",
        "if any(c in spec.banned_shorts for c in args.shorts):",
        "if False:",
    ),
    m("prog: read-only banned long flags", "if args.longs & spec.banned_longs:", "if False:"),
    m("prog: read-only banned raw flags", "if set(args.raw) & spec.banned_raw:", "if False:"),
    m(
        "prog: read-only positional limit",
        "    return not (spec.max_positional is not None and len(args.pos) > spec.max_positional)",
        "    return True",
    ),
    m(
        "prog: path arguments not checked",
        '    if program not in ("cd", "pushd"):\n        for word in args_words:\n            _check_word(word, st)',
        "    if False:\n        for word in args_words:\n            _check_word(word, st)",
    ),
    m(
        "prog: python arguments not checked",
        "        st.project_code = True\n        st.proven = False\n        for word in args_words:\n            _check_word(word, st)",
        "        st.project_code = True\n        st.proven = False",
    ),
    # ---- find
    m("find: -delete", 'if "-delete" in raw:', "if False:"),
    m("find: -exec", 'if word.text in ("-exec", "-execdir", "-ok", "-okdir"):', "if False:"),
    m("find: -exec without a command", "            if not inner:", "            if False:"),
    # ---- git
    m("git: -c", 'elif text == "-c" or text.startswith(("-c", "--config-env")):', "elif False:"),
    m(
        "git: -C path",
        "            _check_word(args[index + 1], st, force=True)\n            index += 2",
        "            index += 2",
    ),
    m(
        "git: unknown global option",
        '        elif text.startswith("-"):\n            raise _Floor(Reason.UNCLEAR, "bash.unclear.git_option")\n        else:\n            break',
        '        elif False:\n            raise _Floor(Reason.UNCLEAR, "bash.unclear.git_option")\n        else:\n            break',
    ),
    m("git: launchers", "if sub in _GIT_LAUNCHERS:", "if False:"),
    m("git: network subcommands", "if sub in _GIT_NETWORK:", "if False:"),
    m("git: remote", 'if sub == "remote" and', "if False and"),
    m("git: credential", 'if sub.startswith("credential"):', "if False:"),
    m("git: history rewriting", "if sub in _GIT_DESTRUCTIVE:", "if False:"),
    m("git: reset --hard", 'if sub == "reset" and', "if False and"),
    m("git: clean", 'if sub == "clean" and', "if False and"),
    m("git: checkout", 'if sub == "checkout" and (', "if False and ("),
    m("git: switch", 'if sub == "switch" and', "if False and"),
    m("git: restore", 'if sub == "restore" and', "if False and"),
    m("git: stash drop", 'if sub == "stash" and', "if False and"),
    m("git: branch -D", 'if sub == "branch" and ("D"', 'if False and ("D"'),
    m("git: tag -d", 'if sub == "tag" and', "if False and"),
    m("git: reflog", 'if sub == "reflog" and', "if False and"),
    m("git: gc --prune", 'if sub == "gc" and', "if False and"),
    m("git: rm -f", 'if sub == "rm" and', "if False and"),
    m("git: worktree", 'if sub == "worktree" and', "if False and"),
    m(
        "git: config writes",
        "if scope or writes or (not reading and len(opts.pos) >= 2):",
        "if False:",
    ),
    m(
        "git: banned read options",
        'return not (flags & _GIT_BANNED_READ_OPTIONS or "-O" in flags)',
        "return True",
    ),
    m("git: branch creation", "return not mutating and (listing or not opts.pos)", "return True"),
    # ---- redirects and reasons
    m(
        "reasons: a sentence is empty",
        'Reason.OUTSIDE_PROJECT: "leaves the project folder",',
        'Reason.OUTSIDE_PROJECT: "",',
        None,
        "auto_reasons.py",
    ),
    m(
        "reasons: model code ALLOW",
        '    "DESTRUCTIVE": Reason.DESTRUCTIVE,',
        '    "ALLOW": Reason.HARMLESS,\n    "DESTRUCTIVE": Reason.DESTRUCTIVE,',
        None,
        "auto_reasons.py",
    ),
    m(
        "reasons: an allow reason has a title",
        "    if reason in ALLOW_REASONS:\n        raise",
        "    if False:\n        raise",
        None,
        "auto_reasons.py",
    ),
]


def apply(source: str, mutation: Mutation) -> str:
    count = source.count(mutation.old)
    if count == 0:
        raise ValueError(f"{mutation.name}: the text to change is not in {mutation.file}")
    if mutation.nth is None and count != 1:
        raise ValueError(f"{mutation.name}: the text appears {count} times; set nth")
    if mutation.nth is None:
        return source.replace(mutation.old, mutation.new)
    position = -1
    for _ in range(mutation.nth + 1):
        position = source.find(mutation.old, position + 1)
    return source[:position] + mutation.new + source[position + len(mutation.old) :]


def run_one(mutation: Mutation, base: Path, work: Path) -> tuple[str, bool, str]:
    """Returns ``(name, killed, detail)``."""
    directory = Path(tempfile.mkdtemp(prefix="auto-mut-", dir=work))
    package = directory / "pi_agent_cli"
    shutil.copytree(base, package, ignore=shutil.ignore_patterns("__pycache__"))
    target = package / mutation.file
    original = target.read_text(encoding="utf-8")
    mutated = apply(original, mutation)
    compile(mutated, str(target), "exec")  # a mutation must stay valid Python
    target.write_text(mutated, encoding="utf-8")
    env = {"PYTHONPATH": str(directory), "PATH": __import__("os").environ.get("PATH", "")}
    env["HOME"] = str(directory)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *TESTS],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    shutil.rmtree(directory, ignore_errors=True)
    detail = ""
    if result.returncode != 0:
        failed = [line for line in result.stdout.splitlines() if line.startswith("FAILED")]
        detail = failed[0][:150] if failed else result.stdout.strip().splitlines()[-1][:150]
    return mutation.name, result.returncode != 0, detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="store_true", help="only check that every text applies")
    parser.add_argument("--only", default="", help="run mutations whose name contains this text")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    chosen = [mut for mut in MUTATIONS if args.only in mut.name]
    base = PACKAGE_DIR / "pi_agent_cli"
    if args.list:
        for mut in chosen:
            print(mut.name)
        return 0
    problems = []
    for mut in chosen:
        try:
            apply((base / mut.file).read_text(encoding="utf-8"), mut)
        except ValueError as error:
            problems.append(str(error))
    names = [mut.name for mut in MUTATIONS]
    problems += [f"{name}: name used twice" for name in set(names) if names.count(name) > 1]
    if problems:
        print("\n".join(problems))
        return 2
    if args.check:
        print(f"{len(chosen)} mutations apply")
        return 0
    baseline = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if baseline.returncode != 0:
        print("The tests fail before any mutation:\n" + baseline.stdout[-1500:])
        return 2
    survived = []
    with (
        tempfile.TemporaryDirectory(prefix="auto-mut-work-") as tmp,
        ThreadPoolExecutor(max_workers=args.jobs) as pool,
    ):
        futures = [pool.submit(run_one, mut, base, Path(tmp)) for mut in chosen]
        for future in futures:
            name, killed, detail = future.result()
            print(
                f"{'killed  ' if killed else 'SURVIVED'}  {name}"
                + (f"  <- {detail}" if killed else "")
            )
            if not killed:
                survived.append(name)
    print(f"\n{len(chosen) - len(survived)} of {len(chosen)} mutations were caught by a test.")
    if survived:
        print("Survivors:\n  " + "\n  ".join(survived))
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
