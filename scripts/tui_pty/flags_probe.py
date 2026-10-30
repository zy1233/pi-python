"""Which command-line flags of zypi change anything for the Python agent? (plan 1.P6, decision 8)

    flags_probe.py [--zypi PATH] [--python PATH] [--only TEXT] [--list] [--json FILE] [--raw DIR]

zypi starts the Python agent with no argument and with no variable but ``PI_HOME``; the only input
the agent gets from a flag is what zypi puts on the ACP wire (``initialize``, ``session/new``,
``session/prompt``, ``session/set_config_option``, ``pi/...`` notifications). So for each flag
this script runs zypi twice over: once as it is, once with the flag, always in a throw-away home,
with ``wire_log_agent.py`` between zypi and ``context_spy_agent.py`` (the Python agent with its
LLM call recorded), and lists what is different:

- the ACP lines zypi sent (``c2a ...``) and the agent's errors (``a2c error``);
- the agent's arguments and variables (``agent.argv``, ``agent.env...``);
- what the LLM call would be sent (``spy.model``, ``spy.reasoning``, ``spy.tools``,
  ``spy.system_prompt``);
- the label on the prompt box, whether the permission question came, the files that appeared;
- or that zypi refused to start (exit status and first line).

``NO CHANGE`` means nothing of this moved: the flag is accepted and has no effect on the agent.
That is not yet "dead": a flag can work inside zypi (``--no-alt-screen``) or write a file
(``--debug-file``); those show in ``files`` and ``label``, and the table in the plan says which.
Flags of ``-p`` have their own contract (``print_mode.rs``) and are not run here. ``--raw DIR``
keeps the wire, the spy log and the two screens of every run in DIR. Linux only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import wire_probe as wp
import zypi_env as env

WRITE_CALL = {"name": "write", "arguments": {"path": "probe.txt", "content": "probe"}}
SESSION_ID = "11111111-2222-4333-8444-555555555555"
TS = re.compile(r"\d{8}T\d{9}Z")


def make_other_dir(root: Path) -> None:
    (root / "other").mkdir()


def make_git_repo(root: Path) -> None:
    """``work`` becomes a git repository with one commit (``--worktree`` needs one)."""
    work = root / "work"
    steps = (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "probe@example.invalid"],
        ["git", "config", "user.name", "probe"],
        ["git", "commit", "-q", "--allow-empty", "-m", "start"],
    )
    for step in steps:
        subprocess.run(step, cwd=work, check=True, capture_output=True)


def files_found(root: Path) -> dict[str, str]:
    """What the run left under its scratch folder (names only; ``(empty)`` for a size of 0)."""
    skip = {"wire.jsonl", "spy.jsonl"}
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in skip or ".git" in path.parts:
            continue
        rel = TS.sub("<ts>", path.relative_to(root).as_posix())
        found.append(rel + ("" if path.stat().st_size else " (empty)"))
    return {"files": "\n".join(found)}


@dataclass(frozen=True)
class Case:
    name: str
    argv: tuple[str, ...] = ()
    base: str = "plain"  # which run it is compared with: "plain" or "tool"
    options: dict = field(default_factory=dict)  # keyword arguments of wire_probe.execute


TOOL = {"tool_call": WRITE_CALL}
GIT = {"setup": make_git_repo}
# The runs the others are compared with. "tool": the first turn calls ``write``, in ask mode.
BASES = {
    "plain": Case("(no flag)"),
    "tool": Case("(no flag, tool call)", base="tool", options=TOOL),
}


def cases() -> list[Case]:
    return [
        Case("(no flag, second run)"),  # the base must repeat itself, or nothing below means much
        # --- what zypi does with the working directory, logging, output
        Case("--cwd ../other", ("--cwd", "../other"), options={"setup": make_other_dir}),
        Case("--debug", ("--debug",)),
        Case("--debug-file ../dbg.log", ("--debug-file", "../dbg.log")),
        Case("--no-alt-screen", ("--no-alt-screen",)),
        Case("--minimal", ("--minimal",)),
        Case("--fullscreen", ("--fullscreen",)),
        Case("PROMPT (positional)", ("go",), options={"prompts": ("",)}),
        # --- permissions
        Case("--always-approve", ("--always-approve",), "tool", TOOL),
        Case("--yolo", ("--yolo",), "tool", TOOL),
        Case("--dangerously-skip-permissions", ("--dangerously-skip-permissions",), "tool", TOOL),
        Case("--permission-mode default", ("--permission-mode", "default"), "tool", TOOL),
        Case(
            "--permission-mode bypassPermissions",
            ("--permission-mode", "bypassPermissions"),
            "tool",
            TOOL,
        ),
        Case("--allow Edit", ("--allow", "Edit"), "tool", TOOL),
        Case("--deny Edit", ("--deny", "Edit"), "tool", TOOL),
        Case("--allowedTools Edit", ("--allowedTools", "Edit"), "tool", TOOL),
        Case("--trust", ("--trust",)),
        # --- the model
        Case("-m mock-b", ("-m", "mock-b")),
        Case("-m no-such-model", ("-m", "no-such-model")),
        Case("--reasoning-effort high", ("--reasoning-effort", "high")),
        Case("--effort high -m mock-b", ("--effort", "high", "-m", "mock-b")),
        # --- the system prompt
        Case("--rules TEXT", ("--rules", "ALWAYS-ANSWER-IN-FRENCH")),
        Case("--append-system-prompt TEXT", ("--append-system-prompt", "ALWAYS-ANSWER-IN-FRENCH")),
        Case("--system-prompt-override TEXT", ("--system-prompt-override", "YOU-ARE-A-PIRATE")),
        Case("--system-prompt TEXT", ("--system-prompt", "YOU-ARE-A-PIRATE")),
        # --- tools and agents
        Case("--tools Read", ("--tools", "Read")),
        Case("--disallowed-tools bash", ("--disallowed-tools", "bash")),
        Case("--disable-web-search", ("--disable-web-search",)),
        Case("--no-plan", ("--no-plan",)),
        Case("--no-subagents", ("--no-subagents",)),
        Case("--no-ask-user", ("--no-ask-user",)),
        Case("--agent reviewer", ("--agent", "reviewer")),
        Case("--agents JSON", ("--agents", '{"a":{"description":"d","prompt":"p"}}')),
        Case("--max-turns 3", ("--max-turns", "3")),
        # --- sessions and worktrees
        Case("--session-id UUID", ("--session-id", SESSION_ID)),
        Case("--worktree feat", ("--worktree", "feat"), options=GIT),
        Case(
            "--worktree feat --worktree-ref HEAD",
            ("--worktree", "feat", "--worktree-ref", "HEAD"),
            options=GIT,
        ),
        Case("--restore-code", ("--restore-code",), options=GIT),
        # --- flags of headless mode, given to an interactive start
        Case("--verbatim", ("--verbatim",)),
        Case("--output-format json", ("--output-format", "json")),
        Case("--include-partial-messages", ("--include-partial-messages",)),
        Case("--json-schema {}", ("--json-schema", "{}")),
        Case("--no-context-files", ("--no-context-files",)),
        Case("--system-prompt-file FILE", ("--system-prompt-file", "../nofile")),
        Case("--append-system-prompt-file FILE", ("--append-system-prompt-file", "../nofile")),
        # --- accounts
        Case("--oauth", ("--oauth",)),
        Case("--force-login", ("--force-login",)),
        # --- hidden flags that put something on the wire or in a file
        Case("--client-identifier X", ("--client-identifier", "probe-client")),
        Case("--hunk-tracker-mode off", ("--hunk-tracker-mode", "off")),
        Case("--terminal", ("--terminal",)),
        Case("--fs-read", ("--fs-read",)),
        Case("--fs-write", ("--fs-write",)),
        Case("--installer X", ("--installer", "probe")),
        Case("--storage-mode writeback", ("--storage-mode", "writeback")),
        Case("--todo-gate", ("--todo-gate",)),
        Case("--no-memory", ("--no-memory",)),
        Case("--experimental-memory", ("--experimental-memory",)),
        Case("--compaction-mode transcript", ("--compaction-mode", "transcript")),
        Case("--compaction-detail minimal", ("--compaction-detail", "minimal")),
        Case("--no-wait-for-background", ("--no-wait-for-background",)),
        Case("--background-wait-timeout 5", ("--background-wait-timeout", "5")),
        Case("--no-auto-update", ("--no-auto-update",)),
        Case("--log-sampling", ("--log-sampling",)),
    ]


def run_case(case: Case, zypi: str, python: str, raw: Path | None) -> dict[str, object]:
    options = dict(case.options)
    options.setdefault("collect", files_found)
    run = wp.execute(case.name, case.argv, zypi=zypi, python=python, **options)
    if raw is not None:
        wp.keep(run, raw)
    return wp.summarize(run)


def verdict(diff: list[str]) -> str:
    if diff and diff[0].startswith("refused"):
        return "REFUSED"
    return "CHANGED" if diff else "NO CHANGE"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument("--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON)")
    ap.add_argument("--only", help="run only the cases whose name contains this text")
    ap.add_argument("--list", action="store_true", help="list the cases and stop")
    ap.add_argument("--json", help="write every summary to this file")
    ap.add_argument("--raw", help="keep wire, spy log and screens of every run in this folder")
    args = ap.parse_args()
    table = cases()
    if args.list:
        for case in table:
            print(f"{case.name:44} [{case.base}] {' '.join(case.argv)}")
        return 0
    zypi, python = env.find_zypi(args.zypi), env.agent_python(args.python)
    raw = Path(args.raw) if args.raw else None
    chosen = [c for c in table if not args.only or args.only in c.name]
    bases: dict[str, dict[str, object]] = {}
    for key in sorted({c.base for c in chosen}):
        bases[key] = run_case(BASES[key], zypi, python, raw)
        base = bases[key]
        print(
            f"== base {BASES[key].name}: label={base.get('label')!r} "
            f"answered={base.get('answered')} asked={base.get('asked')}",
            flush=True,
        )
    dump: dict[str, object] = {"bases": bases}
    results: list[tuple[str, str, int]] = []
    for case in chosen:
        summary = run_case(case, zypi, python, raw)
        dump[case.name] = summary
        diff = wp.changes(bases[case.base], summary)
        results.append((case.name, verdict(diff), len(diff)))
        print(f"[{case.base}] {case.name:44} {verdict(diff)}", flush=True)
        for line in diff:
            print(f"      {line}", flush=True)
    print("\n== table")
    for name, kind, count in results:
        print(f"{name:44} {kind:10} {count if count else ''}")
    if args.json:
        Path(args.json).write_text(json.dumps(dump, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
