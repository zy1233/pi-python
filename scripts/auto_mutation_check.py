#!/usr/bin/env python3
# The mutation texts are exact source lines, so they can be long.
# ruff: noqa: E501
"""Mutation check for the Auto rules: break one rule at a time; a test must notice each break.

    python scripts/auto_mutation_check.py              # run every mutation (about eight minutes on four cores)
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
        "parts = [name for name in map(_fold_name, names) if name]",
        "parts = [name for name in names if name]",
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
        'if _is_inside(real, os.path.join(self.home, ".config"), fold=True):',
        "if False:",
    ),
    m(
        "protected: the agent's own home",
        "if _is_inside(real, self.pi_home, fold=True):",
        "if False:",
    ),
    m(
        "protected: listed gated resources",
        "if any(_is_inside(real, item, fold=True) for item in self.protected):",
        "if False:",
    ),
    m(
        "protected: extra globs",
        "        if self._glob_hit(real, parts):",
        "        if False and self._glob_hit(real, parts):",
    ),
    m("path: ~ not expanded in tool paths", 'if text.startswith(("~/", "~\\\\")):', "if False:"),
    m("path: ~ not expanded for a bare tilde", 'if text == "~":', "if False:"),
    m("path: glob followed by dotdot", 'if ".." in re.split(r"[\\\\/]", text):', "if False:"),
    m("path: plain name that is a link", "if env.fs.lexists(candidate):", "if False:"),
    m(
        "path: value of --option=path",
        '_check_path(text.split("=", 1)[1], word.glob, st, force=force)',
        "pass",
    ),
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
        "        real = env.fs.realpath(joined)\n        env.path_floor(real)",
        "        real = env.fs.realpath(joined)",
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
        "            if close < 0:\n                close = n\n            buf.append",
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
        '                return known(env.cwd, close + 1, in_dq)\n            refuse(Reason.UNCLEAR, "bash.unclear.expansion")',
        '                return known(env.cwd, close + 1, in_dq)\n            return "", index + 2',
    ),
    m("lex: special parameters", "if nxt and nxt in _SPECIAL_PARAMS:", "if False:"),
    m(
        "lex: $HOME not expanded",
        '            if name == "HOME":\n                return known(env.home, match.end(), in_dq)',
        "            if False:\n                return known(env.home, match.end(), in_dq)",
    ),
    m("lex: ~user", '        elif ch == "~" and not in_word:', "        elif False:"),
    m("lex: brace expansion", "if braces and braces.pop():", "if False:"),
    m(
        "lex: brace group marked by a comma or ..",
        'elif braces and (ch == "," or command.startswith("..", i)):',
        "elif False:",
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
        "    if not proven or dynamic or project_path_program:\n        st.proven = False",
        "    if not proven or project_path_program:\n        st.proven = False",
    ),
    # ---- programs
    m(
        "prog: hidden programs",
        "if program in _HIDDEN_PROGRAMS or program in _LAUNCHERS:",
        "if program in _LAUNCHERS:",
    ),
    m(
        "prog: launchers",
        "if program in _HIDDEN_PROGRAMS or program in _LAUNCHERS:",
        "if program in _HIDDEN_PROGRAMS:",
    ),
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
    m("prog: python -m", 'if family == "python" and char == "m":', "if False:"),
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
    m("prog: mkfs", 'program.startswith("mkfs")', "False", 1),
    m("prog: mkfs with a version flag", 'program.startswith("mkfs")', "False", 0),
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
        '    version_flag = len(args_words) == 1 and (\n        args_words[0].text in _VERSION_FLAGS\n        or args_words[0].text == _SHORT_VERSION.get(family or "")\n    )',
        "    version_flag = len(args_words) >= 1 and args_words[-1].text in _VERSION_FLAGS",
    ),
    m(
        "prog: no version shortcut",
        '        and not program.startswith("mkfs")\n    ):\n        if family is None',
        '        and not program.startswith("mkfs")\n        and False\n    ):\n        if family is None',
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
    m(
        "prog: read-only banned long flags",
        "if any(_long_hit(option, spec.banned_longs) for option in args.longs):",
        "if False:",
    ),
    m("prog: read-only banned raw flags", "if set(args.raw) & spec.banned_raw:", "if False:"),
    m(
        "prog: read-only positional limit",
        "    if spec.max_positional is not None and (len(args.pos) > spec.max_positional or args.glob_pos):\n        return False",
        "    if False:\n        return False",
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
        "_check_path(target, any(word.glob for word in chdirs), st, force=True)",
        "pass",
    ),
    m("git: -C word ignored", "            chdirs.append(args[index + 1])", "            pass"),
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
        'return not (has(*_GIT_BANNED_READ_OPTIONS, "-O") or opts.lead_glob)',
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
    # ---- red team: the lexer and the parser
    m("redteam: $[ is not refused", '        if nxt == "[":', "        if False:"),
    m(
        "redteam: a known variable is not checked for splitting",
        'if not in_dq and re.search(r"[\\s*?\\[]", value):',
        "if False:",
    ),
    m(
        "redteam: a known variable is checked inside quotes",
        'if not in_dq and re.search(r"[\\s*?\\[]", value):',
        'if re.search(r"[\\s*?\\[]", value):',
    ),
    m(
        "redteam: splitting ignores glob characters",
        'if not in_dq and re.search(r"[\\s*?\\[]", value):',
        'if not in_dq and re.search(r"[\\s]", value):',
    ),
    m(
        "redteam: ${HOME} skips the split check",
        "return known(env.home, close + 1, in_dq)",
        "return env.home, close + 1",
    ),
    m(
        "redteam: ${PWD} skips the split check",
        "return known(env.cwd, close + 1, in_dq)",
        "return env.cwd, close + 1",
    ),
    m(
        "redteam: $HOME skips the split check",
        "return known(env.home, match.end(), in_dq)",
        "return env.home, match.end()",
    ),
    m(
        "redteam: $PWD skips the split check",
        "return known(env.cwd, match.end(), in_dq)",
        "return env.cwd, match.end()",
    ),
    m(
        "redteam: ${PWD} is not marked",
        "                pwd = True\n                return known(env.cwd, close + 1, in_dq)",
        "                return known(env.cwd, close + 1, in_dq)",
    ),
    m(
        "redteam: $PWD is not marked",
        "                pwd = True\n                return known(env.cwd, match.end(), in_dq)",
        "                return known(env.cwd, match.end(), in_dq)",
    ),
    m(
        "redteam: a brace group is never a brace expansion",
        "            if braces and braces.pop():",
        "            if braces and braces.pop() and False:",
    ),
    m("redteam: a , does not mark a brace group", "braces[-1] = True", "braces[-1] = False"),
    m(
        "redteam: a .. does not mark a brace group",
        'elif braces and (ch == "," or command.startswith("..", i)):',
        'elif braces and ch == ",":',
    ),
    m("redteam: every brace group is an expansion", "braces.append(False)", "braces.append(True)"),
    m("redteam: braces outlive the word", "        braces.clear()", "        pass"),
    m(
        "redteam: a &> target is not remembered",
        'cur.both_target = cur.both_target or pending in ("&>", "&>>")',
        "cur.both_target = False",
    ),
    m(
        "redteam: a later redirect forgets the &> target",
        'cur.both_target = cur.both_target or pending in ("&>", "&>>")',
        'cur.both_target = pending in ("&>", "&>>")',
    ),
    m(
        "redteam: &>> is not a both-target",
        'cur.both_target = cur.both_target or pending in ("&>", "&>>")',
        'cur.both_target = cur.both_target or pending == "&>"',
    ),
    m(
        "redteam: &> is not a both-target",
        'cur.both_target = cur.both_target or pending in ("&>", "&>>")',
        'cur.both_target = cur.both_target or pending == "&>>"',
    ),
    m(
        "redteam: words after a &> target are read",
        "            elif cur.both_target:",
        "            elif False:",
    ),
    # ---- red team: assignments that change what runs
    m(
        "redteam: env NAME=value is not checked",
        "                    _env_assign_floor(text)\n                    index += 1",
        "                    index += 1",
    ),
    m(
        "redteam: the exec names are not read",
        "if _ENV_EXEC.match(name) or (not strict_only and _ENV_LOAD.match(name)):",
        "if not strict_only and _ENV_LOAD.match(name):",
    ),
    m(
        "redteam: the load names are not read",
        "if _ENV_EXEC.match(name) or (not strict_only and _ENV_LOAD.match(name)):",
        "if _ENV_EXEC.match(name):",
    ),
    m(
        "redteam: the load names are read for a plain assignment",
        "if _ENV_EXEC.match(name) or (not strict_only and _ENV_LOAD.match(name)):",
        "if _ENV_EXEC.match(name) or _ENV_LOAD.match(name):",
    ),
    m("redteam: += keeps its plus", 'name = name.rstrip("+")', "name = name"),
    m(
        "redteam: a path in LC_ is not read",
        'if (name.startswith("LC_") or name in ("LANG", "LANGUAGE")) and "/" in value:',
        "if False:",
    ),
    m(
        "redteam: LC_ is not read",
        'if (name.startswith("LC_") or name in ("LANG", "LANGUAGE")) and "/" in value:',
        'if (name in ("LANG", "LANGUAGE")) and "/" in value:',
    ),
    m(
        "redteam: LANG is not read",
        'if (name.startswith("LC_") or name in ("LANG", "LANGUAGE")) and "/" in value:',
        'if (name.startswith("LC_") or name in ("LANGUAGE",)) and "/" in value:',
    ),
    m(
        "redteam: LANGUAGE is not read",
        'if (name.startswith("LC_") or name in ("LANG", "LANGUAGE")) and "/" in value:',
        'if (name.startswith("LC_") or name in ("LANG",)) and "/" in value:',
    ),
    m(
        "redteam: LC_ is read without a path",
        'if (name.startswith("LC_") or name in ("LANG", "LANGUAGE")) and "/" in value:',
        'if (name.startswith("LC_") or name in ("LANG", "LANGUAGE")):',
    ),
    m(
        "redteam: a plain assignment is not checked",
        "    for assign in simple.assigns:\n        _env_assign_floor(assign, strict_only=True)",
        "    for assign in simple.assigns:\n        pass",
    ),
    m(
        "redteam: export, declare and local are not read",
        "    if program in _ASSIGNING_BUILTINS:",
        "    if False:",
    ),
    m(
        "redteam: export reads the wide list",
        "                _env_assign_floor(word.text, strict_only=True)",
        "                _env_assign_floor(word.text)",
    ),
    # ---- red team: -- after a wrapper, uv run, bundle exec
    m(
        "redteam: command skips no --",
        '            if rest and rest[0].text == "--":\n                rest = rest[1:]',
        "            if False:\n                rest = rest[1:]",
        0,
    ),
    m(
        "redteam: nohup, exec and time skip no --",
        '            if rest and rest[0].text == "--":\n                rest = rest[1:]',
        "            if False:\n                rest = rest[1:]",
        1,
    ),
    m(
        "redteam: timeout skips no --",
        '            if rest and rest[0].text == "--":\n                rest = rest[1:]',
        "            if False:\n                rest = rest[1:]",
        2,
    ),
    m(
        "redteam: stdbuf skips no --",
        '            if rest and rest[0].text == "--":\n                rest = rest[1:]',
        "            if False:\n                rest = rest[1:]",
        3,
    ),
    m(
        "redteam: uv run is not unwrapped",
        'elif (program == "uv" and rest[:1] and rest[0].text == "run") or (',
        "elif (False) or (",
    ),
    m(
        "redteam: bundle exec is not unwrapped",
        'program == "bundle" and rest[:1] and rest[0].text == "exec"',
        "False",
    ),
    m(
        "redteam: any option is a uv run flag",
        'if program == "uv" and text in _UV_RUN_FLAGS:',
        "if True:",
    ),
    m(
        "redteam: bundle exec takes the uv flags",
        'if program == "uv" and text in _UV_RUN_FLAGS:',
        "if text in _UV_RUN_FLAGS:",
    ),
    m(
        "redteam: uv run alone is unwrapped",
        "            if index >= len(inner):\n                return words, dynamic",
        "            if False:\n                return words, dynamic",
    ),
    m(
        "redteam: uv run is proven read-only",
        "            st.proven = False\n            st.project_code = True",
        "            st.project_code = True",
    ),
    m(
        "redteam: uv run is not project code",
        "            st.proven = False\n            st.project_code = True",
        "            st.proven = False",
    ),
    # ---- red team: interpreters
    m(
        "redteam: printf -v is not read",
        'if program == "printf" and "v" in args.shorts:',
        "if False:",
    ),
    m(
        "redteam: other interpreters are not read",
        "family = _OTHER_INTERPRETERS.get(program)",
        "family = None",
    ),
    m("redteam: pypy is not python", "(python|pypy)(", "(python)("),
    m("redteam: a python build suffix d", "(\\.\\d+)*[dt]?)?$|^py$", "(\\.\\d+)*[t]?)?$|^py$"),
    m("redteam: a python build suffix t", "(\\.\\d+)*[dt]?)?$|^py$", "(\\.\\d+)*[d]?)?$|^py$"),
    m(
        "redteam: a value option in a cluster takes no value",
        "                    step = 1 if tail else 2",
        "                    step = 1",
    ),
    m(
        "redteam: python -m in a cluster",
        'if family == "python" and char == "m":',
        "if False:",
    ),
    m(
        "redteam: python -m reads no next word",
        'module = tail or (args[index + 1].text if index + 1 < len(args) else "")',
        "module = tail",
    ),
    m(
        "redteam: python -m reads no glued module",
        'module = tail or (args[index + 1].text if index + 1 < len(args) else "")',
        'module = args[index + 1].text if index + 1 < len(args) else ""',
    ),
    m("redteam: a value option in a cluster", 'if f"-{char}" in value_options:', "if False:"),
    m("redteam: php -R is not inline", '"php": "rRBE",', '"php": "rBE",'),
    m("redteam: php -B is not inline", '"php": "rRBE",', '"php": "rRE",'),
    m("redteam: php -E is not inline", '"php": "rRBE",', '"php": "rRB",'),
    m("redteam: php -r is not inline", '"php": "rRBE",', '"php": "RBE",'),
    m("redteam: julia -E is not inline", '"julia": "eE",', '"julia": "e",'),
    m("redteam: julia -e is not inline", '"julia": "eE",', '"julia": "E",'),
    m(
        "redteam: yarn --version is pure",
        '    "git svn docker docker-compose podman gh make',
        '    "git yarn svn docker docker-compose podman gh make',
    ),
    m(
        "redteam: pytest --help is pure",
        '    "git svn docker docker-compose podman gh make',
        '    "git pytest svn docker docker-compose podman gh make',
    ),
    m(
        "redteam: a file of the project that is called git is proven",
        "    if not proven or dynamic or project_path_program:\n        st.proven = False",
        "    if not proven or dynamic:\n        st.proven = False",
    ),
    m(
        "redteam: the version of a file of the project that is called python is plain",
        '        if kind == "version" and not project_path_program:',
        '        if kind == "version":',
    ),
    m(
        "redteam: --version is safe for a program that deletes",
        "        and program not in _DESTRUCTIVE\n",
        "",
    ),
    m(
        "redteam: --version is safe for mkfs",
        '        and not program.startswith("mkfs")\n',
        "",
    ),
    m(
        "redteam: --version is safe for a program in the project",
        "        and not project_path_program\n",
        "",
    ),
    m(
        "redteam: a launcher is not hidden",
        "if program in _HIDDEN_PROGRAMS or program in _LAUNCHERS:",
        "if program in _HIDDEN_PROGRAMS:",
    ),
    m(
        "redteam: tar -I is not an exec option",
        'or args.shorts & set("IF")',
        'or args.shorts & set("F")',
    ),
    m(
        "redteam: tar -F is not an exec option",
        'or args.shorts & set("IF")',
        'or args.shorts & set("I")',
    ),
    m(
        "redteam: tar xIf is not an exec option",
        '        or (args.pos and not args_words[0].text.startswith("-") and set(args.pos[0]) & set("IF"))\n',
        "",
    ),
    m(
        "redteam: tar options are exact",
        "any(_long_hit(option, _TAR_EXEC_LONGS) for option in args.longs)",
        "any(option in _TAR_EXEC_LONGS for option in args.longs)",
    ),
    m("redteam: tar --rsh-command", '"--rsh-command"', '"--rsh-x"'),
    m("redteam: tar --rmt-command", '"--rmt-command"', '"--rmt-x"'),
    m("redteam: tar --info-script", '"--info-script"', '"--info-x"'),
    m("redteam: tar --new-volume-script", '"--new-volume-script"', '"--new-volume-x"'),
    m("redteam: tar --to-command", '"--to-command"', '"--to-x"'),
    m("redteam: tar --checkpoint-action", '"--checkpoint-action"', '"--checkpoint-x"'),
    m("redteam: tar --use-compress-program", '"--use-compress-program"', '"--use-compress-x"'),
    # ---- red team: names that a file system treats as the same
    m(
        "redteam: names keep their compatibility form",
        '    part = unicodedata.normalize("NFKC", part)\n',
        "",
    ),
    m(
        "redteam: names keep invisible characters",
        '    part = "".join(char for char in part if unicodedata.category(char) != "Cf")\n',
        "",
    ),
    m(
        "redteam: names keep a stream",
        '        part = part.split(":", 1)[0]',
        "        pass",
    ),
    m(
        "redteam: names keep trailing dots",
        'return part.rstrip(" .").lower()',
        'return part.rstrip(" ").lower()',
    ),
    m(
        "redteam: names keep trailing spaces",
        'return part.rstrip(" .").lower()',
        'return part.rstrip(".").lower()',
    ),
    m(
        "redteam: names keep their case",
        'return part.rstrip(" .").lower()',
        'return part.rstrip(" .")',
    ),
    m(
        "redteam: the protected set compares with case",
        "        p, r = p.casefold(), r.casefold()",
        "        pass",
    ),
    m(
        "redteam: the parts are not folded",
        "parts = [name for name in map(_fold_name, names) if name]",
        "parts = [name.lower() for name in names]",
    ),
    m(
        "redteam: 8.3 names are not read",
        "        if any(_is_short_name(name) for name in names):",
        "        if False:",
    ),
    m(
        "redteam: an 8.3 name has no extension",
        '_SHORT_NAME = re.compile(r"^[^~.]{1,6}~\\d{1,6}(\\.[^.~]{0,3})?$")',
        '_SHORT_NAME = re.compile(r"^[^~.]{1,6}~\\d{1,6}$")',
    ),
    m(
        "redteam: a stream mark is not looked for",
        '    has_stream_mark = ":$" in part',
        "    has_stream_mark = False",
    ),
    m(
        "redteam: any : cuts an 8.3 name",
        "    return has_stream_mark and bool(_SHORT_NAME.match(_fold_name(part)))",
        "    return bool(_SHORT_NAME.match(_fold_name(part)))",
    ),
    m(
        "redteam: home secret folders are not read",
        "        if any(_is_inside(real, os.path.join(self.home, d), fold=True) for d in _HOME_SECRET_DIRS):",
        "        if False:",
    ),
    m(
        "redteam: .docker is not secret",
        '_HOME_SECRET_DIRS = (".docker", ',
        "_HOME_SECRET_DIRS = (",
    ),
    m("redteam: .azure is not secret", '".azure", ', ""),
    m("redteam: .oci is not secret", '".oci", ', ""),
    m("redteam: .terraform.d is not secret", '".terraform.d", ', ""),
    m("redteam: .password-store is not secret", ', ".password-store")', ")"),
    m(
        "redteam: the agent home compares with case",
        "if _is_inside(real, self.pi_home, fold=True):",
        "if _is_inside(real, self.pi_home):",
    ),
    m(
        "redteam: a gated path compares with case",
        "if any(_is_inside(real, item, fold=True) for item in self.protected):",
        "if any(_is_inside(real, item) for item in self.protected):",
    ),
    m(
        "redteam: the home config compares with case",
        'if _is_inside(real, os.path.join(self.home, ".config"), fold=True):',
        'if _is_inside(real, os.path.join(self.home, ".config")):',
    ),
    m(
        "redteam: a protected glob has case",
        'pattern = glob.replace("\\\\", "/").lower()',
        'pattern = glob.replace("\\\\", "/")',
    ),
    m(
        "redteam: a protected glob sees the folded name",
        'forms = {real.replace("\\\\", "/").lower(), "/" + "/".join(parts)}',
        'forms = {real.replace("\\\\", "/").lower()}',
    ),
    m(
        "redteam: a protected glob sees the last name",
        "        if parts:\n            forms.add(parts[-1])",
        "        if False:\n            forms.add(parts[-1])",
    ),
    m(
        "redteam: a protected glob is read below the project root",
        "if parts[: len(root_parts)] == root_parts:",
        "if False:",
    ),
    m(
        "redteam: a protected glob may start with ./",
        'if pattern.startswith("./"):',
        "if False:",
    ),
    m(
        "redteam: a protected glob may end with /",
        'if pattern.endswith("/"):',
        "if False:",
    ),
    m(
        "redteam: a protected glob may use backslashes",
        'pattern = glob.replace("\\\\", "/").lower()',
        "pattern = glob.lower()",
    ),
    m("redteam: a path has no length cap", "    if len(path) > MAX_PATH_CHARS:", "    if False:"),
    m("redteam: .htpasswd is not secret", "|\\.htpasswd", "|\\.htpasswdx"),
    m("redteam: .vault-token is not secret", "|\\.vault-token", "|\\.vault-tokenx"),
    m("redteam: .token is not secret", "|\\.token|", "|\\.tokenx|"),
    m("redteam: .yarnrc.yml is not secret", "\\.yarnrc(\\.yml)?", "\\.yarnrc"),
    m("redteam: .tfvars.json is not secret", "|.*\\.tfvars(\\.json)?", "|.*\\.tfvars"),
    m(
        "redteam: .tfstate.backup is not secret",
        "terraform\\.tfstate(\\.backup)?",
        "terraform\\.tfstate",
    ),
    m(
        "redteam: secrets files are not secret",
        "|secrets?\\.(json|toml|ya?ml|txt|ini|env)",
        "|secrets?\\.xxx",
    ),
    m("redteam: token files are not secret", "|tokens?\\.(json|toml|ya?ml|txt)", "|tokens?\\.xxx"),
    m(
        "redteam: credentials files are not secret",
        "credentials(\\.(json|toml|ya?ml|xml|txt|ini))?",
        "credentials(\\.json)?",
    ),
    m("redteam: history files are not secret", "|.*_history", "|.*_historyx"),
    # ---- red team: globs
    m(
        "redteam: a glob with .. in it",
        '    if ".." in re.split(r"[\\\\/]", text):',
        "    if False:",
    ),
    m(
        "redteam: a glob that matches .. is allowed",
        '        if _has_glob(comp) and comp[0] in ".[" and re.match(fnmatch.translate(comp), ".."):',
        "        if False:",
    ),
    m(
        "redteam: a bracket glob that matches .. is allowed",
        'comp[0] in ".[" and re.match(fnmatch.translate(comp), ".."):',
        'comp[0] in "." and re.match(fnmatch.translate(comp), ".."):',
    ),
    m("redteam: one literal is enough", "    if literals < 2:", "    if literals < 1:"),
    m("redteam: three literals are needed", "    if literals < 2:", "    if literals < 3:"),
    m(
        "redteam: * matches a leading dot",
        '    if shell and name.startswith(".") and pattern[:1] in ("*", "?"):',
        "    if False:",
    ),
    m(
        "redteam: ? matches a leading dot",
        '    if shell and name.startswith(".") and pattern[:1] in ("*", "?"):',
        '    if shell and name.startswith(".") and pattern[:1] in ("*",):',
    ),
    m(
        "redteam: * does not match a name without a dot",
        '    if shell and name.startswith(".") and pattern[:1] in ("*", "?"):',
        '    if shell and pattern[:1] in ("*", "?"):',
    ),
    m(
        "redteam: a short run points at a key suffix",
        "if len(run) >= 3]",
        "if len(run) >= 1]",
    ),
    m(
        "redteam: a glob stem is not tried",
        'stems = ["x", first + "x", *(group[0] for group in re.findall(r"\\[([^\\]!^][^\\]]*)\\]", pattern))]',
        'stems = ["x", *(group[0] for group in re.findall(r"\\[([^\\]!^][^\\]]*)\\]", pattern))]',
    ),
    m(
        "redteam: a bracket stem is not tried",
        'stems = ["x", first + "x", *(group[0] for group in re.findall(r"\\[([^\\]!^][^\\]]*)\\]", pattern))]',
        'stems = ["x", first + "x"]',
    ),
    m(
        "redteam: a glob is not tried against a key suffix",
        "            if hit is None and _glob_reaches_key(comp):",
        "            if False:",
    ),
    m(
        "redteam: .htpasswd is not a probe",
        ".git-credentials .htpasswd .docker ",
        ".git-credentials .docker ",
    ),
    m(
        "redteam: .bash_history is not a probe",
        ".bash_history terraform.tfstate",
        "terraform.tfstate",
    ),
    m(
        "redteam: terraform.tfstate is not a probe",
        ".bash_history terraform.tfstate",
        ".bash_history",
    ),
    m(
        "redteam: a short option carries no value",
        "    values = [text[2:]]",
        "    values = []",
    ),
    m(
        "redteam: a cluster carries no path",
        '        if text[k] in "/\\\\":',
        "        if False:",
    ),
    m(
        "redteam: an option with a value is not checked",
        '        elif not text.startswith("--"):\n            for value in _attached_values(text):',
        "        elif False:\n            for value in _attached_values(text):",
    ),
    # ---- red team: git
    m(
        "redteam: a quoted pathspec glob is not read",
        "    if _has_glob(text) and not word.glob:",
        "    if False:",
    ),
    m(
        "redteam: an unquoted glob is read as a pathspec",
        "    if _has_glob(text) and not word.glob:",
        "    if _has_glob(text):",
    ),
    m(
        "redteam: git words with no : are read",
        '    if ":" not in text:\n        return',
        "    if False:\n        return",
    ),
    m(
        "redteam: a git word with many parts",
        "    if len(parts) > MAX_GIT_SPEC_PARTS:",
        "    if False:",
    ),
    m(
        "redteam: a git option is read as a path",
        '    if text.startswith(("-", ":/")) or "://" in text:',
        '    if text.startswith((":/",)) or "://" in text:',
    ),
    m(
        "redteam: a git :/ search is read as a path",
        '    if text.startswith(("-", ":/")) or "://" in text:',
        '    if text.startswith(("-",)) or "://" in text:',
    ),
    m(
        "redteam: a git URL is read as a path",
        '    if text.startswith(("-", ":/")) or "://" in text:',
        '    if text.startswith(("-", ":/")):',
    ),
    m(
        "redteam: git words are not read",
        "    for word in args[index + 1 :]:\n        _git_spec_floor(word, st)",
        "    for word in args[index + 1 :]:\n        pass",
    ),
    m(
        "redteam: a glob after : is not read",
        "        if _has_glob(part):\n            _glob_floor(part, st, shell=False)",
        "        if False:\n            _glob_floor(part, st, shell=False)",
    ),
    m(
        "redteam: git -C is not chained",
        "            target = os.path.join(target, word.text)",
        "            target = word.text",
    ),
    m(
        "redteam: git -C is always chained",
        "        if target is None or os.path.isabs(word.text):",
        "        if os.path.isabs(word.text):",
    ),
    m(
        "redteam: git -C is never chained",
        "        if target is None or os.path.isabs(word.text):",
        "        if True:",
    ),
    m(
        "redteam: the -C folder is not checked",
        "    if target is not None:\n        _check_path(target,",
        "    if False:\n        _check_path(target,",
    ),
    m(
        "redteam: .git ends no search",
        '            if env.fs.lexists(os.path.join(folder, ".git")):\n                break',
        "            if False:\n                break",
    ),
    m(
        "redteam: one name makes a bare repository",
        'if all(env.fs.lexists(os.path.join(folder, n)) for n in ("HEAD", "objects", "refs")):',
        'if any(env.fs.lexists(os.path.join(folder, n)) for n in ("HEAD", "objects", "refs")):',
    ),
    m(
        "redteam: HEAD is not needed for a bare repository",
        '("HEAD", "objects", "refs")',
        '("objects", "refs")',
    ),
    m(
        "redteam: objects is not needed for a bare repository",
        '("HEAD", "objects", "refs")',
        '("HEAD", "refs")',
    ),
    m(
        "redteam: refs is not needed for a bare repository",
        '("HEAD", "objects", "refs")',
        '("HEAD", "objects")',
    ),
    m(
        "redteam: the search stops at the folder",
        "            parent = os.path.dirname(folder)\n",
        "            parent = folder\n",
    ),
    m("redteam: the guard reads git version", '    if sub != "version":', "    if True:"),
    m(
        "redteam: the guard skips git help",
        '    if sub != "version":',
        '    if sub not in ("version", "help"):',
    ),
    m("redteam: the guard is off", '    if sub != "version":', "    if False:"),
    m(
        "redteam: a git option is read only when exact",
        'flag == name or (flag.startswith("--") and _long_hit(flag, frozenset({name})))',
        "flag == name",
    ),
    m(
        "redteam: git global options are exact",
        '        elif text.split("=", 1)[0] in _GIT_REPO_GLOBALS:',
        "        elif text in _GIT_REPO_GLOBALS:",
    ),
    m(
        "redteam: git repository options are not read",
        '        elif text.split("=", 1)[0] in _GIT_REPO_GLOBALS:',
        "        elif False:",
    ),
    m(
        "redteam: git -O is not banned",
        'return not (has(*_GIT_BANNED_READ_OPTIONS, "-O") or opts.lead_glob)',
        "return not (has(*_GIT_BANNED_READ_OPTIONS) or opts.lead_glob)",
    ),
    m(
        "redteam: git cat-file is split in two words",
        '"status diff log show rev-parse ls-files blame annotate describe shortlog ls-tree cat-file "',
        '"status diff log show rev-parse ls-files blame annotate describe shortlog ls-tree cat- file "',
    ),
    m(
        "redteam: git --glob-pathspecs is split in two words",
        '"--no-pager -p -P --paginate --no-replace-objects --literal-pathspecs --glob-pathspecs "',
        '"--no-pager -p -P --paginate --no-replace-objects --literal-pathspecs --glob- pathspecs "',
    ),
    m(
        "redteam: a git glob is not banned",
        'return not (has(*_GIT_BANNED_READ_OPTIONS, "-O") or opts.lead_glob)',
        'return not (has(*_GIT_BANNED_READ_OPTIONS, "-O"))',
    ),
    m("redteam: git branch --set-upstream-to", '            "--set-upstream-to",\n', ""),
    # ---- red team: the read-only table
    m(
        "redteam: a leading glob is not read",
        "    if spec.glob_inject and args.lead_glob:",
        "    if False:",
    ),
    m(
        "redteam: a glob is dangerous for every program",
        "    if spec.glob_inject and args.lead_glob:",
        "    if args.lead_glob:",
    ),
    m(
        "redteam: a glob is dangerous only with a ban",
        "        guarded = spec.banned_shorts or spec.banned_longs or spec.banned_raw",
        "        guarded = True",
    ),
    m(
        "redteam: a glob may be many words",
        "(len(args.pos) > spec.max_positional or args.glob_pos)",
        "(len(args.pos) > spec.max_positional)",
    ),
    m(
        "redteam: date may set the clock",
        '    return not (program == "date" and any(_DATE_SET.match(item) for item in args.pos))',
        "    return True",
    ),
    m(
        "redteam: an option of two dashes",
        "len(option) > 2 and name.startswith(option)",
        "name.startswith(option)",
    ),
    m(
        "redteam: a long option is read only when exact",
        "len(option) > 2 and name.startswith(option)",
        "option == name",
    ),
    m(
        "redteam: wc may be given a glob",
        '"wc": _ReadOnly(banned_longs=frozenset({"--files0-from"}), glob_inject=False)',
        '"wc": _ReadOnly(banned_longs=frozenset({"--files0-from"}))',
    ),
    m(
        "redteam: du may be given a glob",
        '"du": _ReadOnly(banned_longs=frozenset({"--files0-from"}), glob_inject=False)',
        '"du": _ReadOnly(banned_longs=frozenset({"--files0-from"}))',
    ),
    m(
        "redteam: tail may be given a glob",
        ', banned_longs=frozenset({"--follow"}), glob_inject=False)',
        ', banned_longs=frozenset({"--follow"}))',
    ),
    m(
        "redteam: wc --files0-from",
        '"wc": _ReadOnly(banned_longs=frozenset({"--files0-from"})',
        '"wc": _ReadOnly(banned_longs=frozenset({"--files0-x"})',
    ),
    m(
        "redteam: du --files0-from",
        '"du": _ReadOnly(banned_longs=frozenset({"--files0-from"})',
        '"du": _ReadOnly(banned_longs=frozenset({"--files0-x"})',
    ),
    m(
        "redteam: sort --files0-from",
        '"--output", "--compress-program", "--files0-from"',
        '"--output", "--compress-program"',
    ),
    m(
        "redteam: sort --compress-program",
        '"--output", "--compress-program", "--files0-from"',
        '"--output", "--files0-from"',
    ),
    m(
        "redteam: sort --output",
        '"--output", "--compress-program", "--files0-from"',
        '"--compress-program", "--files0-from"',
    ),
    m(
        "redteam: find -files0-from",
        '| {"-fprintf", "-fls", "-files0-from"}',
        '| {"-fprintf", "-fls"}',
    ),
    m(
        "redteam: hostname -b",
        'banned_shorts="bF", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
        'banned_shorts="F", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
    ),
    m(
        "redteam: hostname -F",
        'banned_shorts="bF", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
        'banned_shorts="b", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
    ),
    m(
        "redteam: hostname --boot",
        'banned_shorts="bF", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
        'banned_shorts="bF", banned_longs=frozenset({"--file"}), max_positional=0',
    ),
    m(
        "redteam: hostname --file",
        'banned_shorts="bF", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
        'banned_shorts="bF", banned_longs=frozenset({"--boot"}), max_positional=0',
    ),
    m(
        "redteam: hostname NAME",
        'banned_shorts="bF", banned_longs=frozenset({"--boot", "--file"}), max_positional=0',
        'banned_shorts="bF", banned_longs=frozenset({"--boot", "--file"})',
    ),
    # ---- red team: cd, pushd and $PWD
    m(
        "redteam: a folder stack option is followed",
        '    if any(re.match(r"^[+-]\\d+$", w.text) for w in args_words) or (',
        "    if False or (",
    ),
    m(
        "redteam: pushd -n is followed",
        '        program == "pushd" and any(w.text == "-n" for w in args_words)\n',
        "        False\n",
    ),
    m(
        "redteam: a cd through a link is followed",
        "        if _key(real) != _key(os.path.normpath(joined)):",
        "        if False:",
    ),
    m(
        "redteam: a cd does not move $PWD",
        "    st.add_cwd(new)\n    st.moved = True",
        "    st.add_cwd(new)",
    ),
    m(
        "redteam: $PWD is the start folder after a cd",
        "    if st.moved:\n        targets",
        "    if False:\n        targets",
    ),
    m(
        "redteam: $PWD in a redirect target after a cd",
        "if any(word.pwd for word in [*simple.words, *targets]):",
        "if any(word.pwd for word in [*simple.words]):",
    ),
    m(
        "redteam: $PWD in a word after a cd",
        "if any(word.pwd for word in [*simple.words, *targets]):",
        "if any(word.pwd for word in [*targets]):",
    ),
    m(
        "redteam: a read of an endless device is not proven",
        "    if text in _DEV_ENDLESS:\n        st.proven = False\n",
        "    if text in _DEV_ENDLESS:\n        pass\n",
    ),
    m(
        "redteam: an endless device is a plain path",
        "    if text in _DEV_ENDLESS:\n        st.proven = False\n        return\n",
        "",
    ),
    m(
        "redteam: /dev/zero is harmless",
        '_DEV_ENDLESS = _words("/dev/zero /dev/random /dev/urandom")',
        '_DEV_ENDLESS = _words("/dev/random /dev/urandom")',
    ),
    m(
        "redteam: /dev/random is harmless",
        '_DEV_ENDLESS = _words("/dev/zero /dev/random /dev/urandom")',
        '_DEV_ENDLESS = _words("/dev/zero /dev/urandom")',
    ),
    m(
        "redteam: /dev/urandom is harmless",
        '_DEV_ENDLESS = _words("/dev/zero /dev/random /dev/urandom")',
        '_DEV_ENDLESS = _words("/dev/zero /dev/random")',
    ),
    m(
        "redteam: /dev/null is an outside path",
        '_DEV_OK = _words("/dev/null /dev/stdin /dev/stdout /dev/stderr")',
        '_DEV_OK = _words("/dev/stdin /dev/stdout /dev/stderr")',
    ),
    m(
        "redteam: /dev/stdin is an outside path",
        '_DEV_OK = _words("/dev/null /dev/stdin /dev/stdout /dev/stderr")',
        '_DEV_OK = _words("/dev/null /dev/stdout /dev/stderr")',
    ),
    m(
        "redteam: /dev/stdout is an outside path",
        '_DEV_OK = _words("/dev/null /dev/stdin /dev/stdout /dev/stderr")',
        '_DEV_OK = _words("/dev/null /dev/stdin /dev/stderr")',
    ),
    m(
        "redteam: /dev/stderr is an outside path",
        '_DEV_OK = _words("/dev/null /dev/stdin /dev/stdout /dev/stderr")',
        '_DEV_OK = _words("/dev/null /dev/stdin /dev/stdout")',
    ),
    m(
        "redteam: the version flag of an unknown program is plain",
        "        if family is None and program not in _READONLY and program not in _VERSION_PURE:",
        "        if False:",
    ),
    m(
        "redteam: the version flag of a known program is project code",
        "        if family is None and program not in _READONLY and program not in _VERSION_PURE:",
        "        if family is None and program not in _READONLY:",
    ),
    m(
        "redteam: the version flag of a read-only program is project code",
        "        if family is None and program not in _READONLY and program not in _VERSION_PURE:",
        "        if family is None and program not in _VERSION_PURE:",
    ),
    m(
        "redteam: the version flag of an interpreter is project code",
        "        if family is None and program not in _READONLY and program not in _VERSION_PURE:",
        "        if program not in _READONLY and program not in _VERSION_PURE:",
    ),
    m(
        "redteam: the version flag of an unknown program sets nothing",
        "            st.project_code = True\n            st.proven = False\n        return\n\n"
        "    if family is not None:",
        "            pass\n        return\n\n    if family is not None:",
    ),
    m(
        "redteam: the version flag of an unknown program stays proven in a trusted project",
        "            st.project_code = True\n            st.proven = False\n        return\n\n"
        "    if family is not None:",
        "            st.project_code = True\n        return\n\n    if family is not None:",
    ),
    m(
        "redteam: the version flag of an unknown program is not project code",
        "            st.project_code = True\n            st.proven = False\n        return\n\n"
        "    if family is not None:",
        "            st.proven = False\n        return\n\n    if family is not None:",
    ),
    m(
        "redteam: hg --version is pure",
        '    "git svn docker docker-compose podman gh make',
        '    "git hg svn docker docker-compose podman gh make',
    ),
    m(
        "redteam: -v is the version flag of every program",
        'args_words[0].text == _SHORT_VERSION.get(family or "")',
        'args_words[0].text == "-v"',
    ),
    m(
        "redteam: no short version flag",
        'args_words[0].text == _SHORT_VERSION.get(family or "")',
        "False",
    ),
    m("redteam: node -v is not a version flag", '    "node": "-v",', '    "node": "-V",'),
    m("redteam: ruby -v is not a version flag", '    "ruby": "-v",', '    "ruby": "-V",'),
    m("redteam: perl -v is not a version flag", '    "perl": "-v",', '    "perl": "-V",'),
    m("redteam: php -v is not a version flag", '    "php": "-v",', '    "php": "-V",'),
    m("redteam: lua -v is not a version flag", '    "lua": "-v",', '    "lua": "-V",'),
    m("redteam: python -VV is not a version flag", '    "python": "-VV",', '    "python": "-vv",'),
    m(
        "redteam: a relative folder in the context is trusted",
        "    if not _context_is_absolute(ctx):",
        "    if False and not _context_is_absolute(ctx):",
    ),
    m(
        "redteam: the sub-agent folder may be relative",
        "    if ctx.origin_cwd:\n        folders.append",
        "    if False:\n        folders.append",
    ),
    m(
        "redteam: the session folder may be relative",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
        "folders = [ctx.home_dir, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
    ),
    m(
        "redteam: the home folder may be relative",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
        "folders = [ctx.cwd, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
    ),
    m(
        "redteam: the agent home may be relative",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
        "folders = [ctx.cwd, ctx.home_dir, *ctx.extra_roots, *ctx.protected_paths]",
    ),
    m(
        "redteam: an extra root may be relative",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.protected_paths]",
    ),
    m(
        "redteam: a protected path may be relative",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.extra_roots, *ctx.protected_paths]",
        "folders = [ctx.cwd, ctx.home_dir, ctx.pi_home, *ctx.extra_roots]",
    ),
    m(
        "redteam: a folder that is not text is absolute",
        "    except TypeError:\n        return False\n    if not isinstance(text, str):",
        "    except TypeError:\n        return True\n    if not isinstance(text, str):",
    ),
    m(
        "redteam: a folder as bytes is absolute",
        "    if not isinstance(text, str):\n        return False\n    return os.path.isabs(text)",
        "    if False:\n        return False\n    return os.path.isabs(text)",
    ),
    m(
        "redteam: every folder is absolute",
        "    return os.path.isabs(text) or (os.name",
        "    return True or (os.name",
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
