"""stdio entry: python -m pi_agent_cli

Default: ACP agent on stdio.
`python -m pi_agent_cli -p "..."`: one-shot headless turn (no TUI).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from acp import run_agent

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import load_local_env
from pi_agent_cli.extension_trust import TRUST_ENV
from pi_agent_cli.headless import HeadlessPromptOverrides, resolve_print_prompt, run_print

_PROMPT_CLI_FLAG_NAMES = (
    "system_prompt",
    "system_prompt_file",
    "append_system_prompt",
    "append_system_prompt_file",
    "no_context_files",
    "no_git_context",
)


# SIGHUP is what a closing terminal sends to its foreground process group, which includes the
# agent when the TUI is started from a shell. Windows has neither signal handlers nor SIGHUP.
_STOP_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)

# `zypi -p` gives this process the user's own terminal as stdin, so the way the TUI's agent learns
# that its client is gone (EOF on stdin) does not exist there. zypi names itself in this variable
# instead, and the run stops once that process is no longer the parent: the kernel re-parents a
# child whatever killed its parent, SIGKILL included.
PARENT_PID_ENV = "PI_AGENT_PARENT_PID"
PARENT_POLL_SECONDS = 0.5


class _StopRequest:
    """Ends the main task once, and remembers what asked for it."""

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task
        self.signum: int | None = None

    def install_signal_handlers(self) -> None:
        """Stop on SIGTERM / SIGHUP the way EOF on stdin stops the stdio agent.

        The main task is cancelled, so `asyncio.run` cancels the in-flight turns and each running
        tool's process group is reaped. Without a handler the signal ends the process at once and
        its tools outlive it. (SIGKILL cannot be handled; there the tools are orphaned.)
        """
        loop = asyncio.get_running_loop()
        for stop_signal in _STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(stop_signal, self.request, int(stop_signal))

    def request(self, signum: int) -> None:
        if self.signum is not None:
            return
        self.signum = signum
        # A repeated signal gets the default action, so a process that hangs while it shuts down
        # (a thread that cannot be cancelled) can still be stopped.
        loop = asyncio.get_running_loop()
        for stop_signal in _STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(stop_signal)
        self._task.cancel()


async def serve(agent: PiAcpAgent) -> None:
    """Serve ``agent`` over stdio until stdin closes or the process is told to stop."""
    main_task = asyncio.current_task()
    if main_task is not None:
        _StopRequest(main_task).install_signal_handlers()
    # `session/close` and `session/resume` are unstable in the SDK and answered with
    # "Method not found" unless this flag is set, although `initialize` advertises both.
    with contextlib.suppress(asyncio.CancelledError):
        await run_agent(agent, use_unstable_protocol=True)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it is just not ours
    return True


def _take_parent_pid() -> int | None:
    """The pid in ``PI_AGENT_PARENT_PID`` if this run should stop when that process goes away.

    The variable is removed either way, so nothing this process starts (a tool, a nested
    ``pi -p``) inherits a parent that is not its own. It counts when it names this process's
    parent, or a process that no longer exists (the parent died while this one was starting; the
    first check then stops the run). A live process that is not the parent is a wrapper in
    between, and what this process should wait on is not its pid.
    """
    raw = os.environ.pop(PARENT_PID_ENV, None)
    if os.name != "posix" or raw is None:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    if not 0 < pid < 2**31:
        return None
    if pid == os.getppid() or not _process_exists(pid):
        return pid
    return None


async def _watch_parent(parent_pid: int, stop: _StopRequest) -> None:
    """Ask for a stop once ``parent_pid`` is no longer this process's parent."""
    while os.getppid() == parent_pid:
        await asyncio.sleep(PARENT_POLL_SECONDS)
    stop.request(signal.SIGTERM)


async def _print_main(
    prompt: str,
    *,
    cwd: Path | None,
    prompt_overrides: HeadlessPromptOverrides,
    parent_pid: int | None,
) -> int:
    """One ``-p`` turn, ended early by a stop signal or by the death of the process that started it.

    A stop cancels the turn the way it does for the stdio agent, so the process group of every
    running tool is reaped; the exit status is 128 plus the signal number, as a shell reports a
    killed process.
    """
    task = asyncio.current_task()
    if task is None:
        return await run_print(prompt, cwd=cwd, prompt_overrides=prompt_overrides)
    stop = _StopRequest(task)
    stop.install_signal_handlers()
    watcher = (
        asyncio.create_task(_watch_parent(parent_pid, stop)) if parent_pid is not None else None
    )
    try:
        return await run_print(prompt, cwd=cwd, prompt_overrides=prompt_overrides)
    except asyncio.CancelledError:
        if stop.signum is None:
            raise
        return 128 + stop.signum
    finally:
        if watcher is not None:
            watcher.cancel()


async def _amain() -> None:
    await serve(PiAcpAgent())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-agent-cli",
        description="Standard ACP agent over AgentHarness (stdio), or one-shot -p print.",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        metavar="PROMPT",
        help="Run one prompt, print the assistant text, and exit (no TUI, no ACP stdio).",
    )
    src.add_argument(
        "--prompt-json",
        metavar="JSON",
        help="Single-turn prompt as a JSON string or list of content blocks.",
    )
    src.add_argument(
        "--prompt-file",
        metavar="PATH",
        type=Path,
        help="Read the single-turn prompt from a file.",
    )
    parser.add_argument(
        "--cwd",
        metavar="PATH",
        type=Path,
        help="Working directory for the headless session (default: process cwd).",
    )
    system = parser.add_mutually_exclusive_group()
    system.add_argument(
        "--system-prompt",
        "--system-prompt-override",
        dest="system_prompt",
        metavar="TEXT",
        help="Replace the default system prompt (headless only).",
    )
    system.add_argument(
        "--system-prompt-file",
        metavar="PATH",
        type=Path,
        help="Read the system prompt override from a file (headless only).",
    )
    append = parser.add_mutually_exclusive_group()
    append.add_argument(
        "--append-system-prompt",
        "--rules",
        dest="append_system_prompt",
        metavar="TEXT",
        help="Append text to the system prompt (headless only).",
    )
    append.add_argument(
        "--append-system-prompt-file",
        metavar="PATH",
        type=Path,
        help="Read append text from a file (headless only).",
    )
    parser.add_argument(
        "--no-context-files",
        action="store_true",
        help="Skip AGENTS.md / CLAUDE.md discovery (headless only).",
    )
    parser.add_argument(
        "--no-git-context",
        action="store_true",
        help="Omit the <git_status> section from the system prompt (headless only).",
    )
    parser.add_argument(
        "--trust-project-extensions",
        action="store_true",
        help=(
            "Load extensions from <project>/.pi-python/extensions, which run arbitrary "
            f"Python (same as {TRUST_ENV}=1). Without it they are skipped unless the "
            "project is listed under [extensions] trusted_projects in agent.toml."
        ),
    )
    return parser


def _apply_trust_flag(args: argparse.Namespace) -> None:
    """``--trust-project-extensions`` is ``PI_TRUST_PROJECT_EXTENSIONS=1`` for this process."""
    if args.trust_project_extensions:
        os.environ[TRUST_ENV] = "1"


def _prompt_overrides_from_args(args: argparse.Namespace) -> HeadlessPromptOverrides:
    return HeadlessPromptOverrides(
        system_prompt=args.system_prompt,
        system_prompt_file=args.system_prompt_file,
        append_system_prompt=args.append_system_prompt,
        append_system_prompt_file=args.append_system_prompt_file,
        no_context_files=True if args.no_context_files else None,
        no_git_context=True if args.no_git_context else None,
    )


def _has_prompt_cli_flags(args: argparse.Namespace) -> bool:
    return any(getattr(args, name) for name in _PROMPT_CLI_FLAG_NAMES)


def main() -> None:
    parent_pid = _take_parent_pid()  # first: nothing started from here may inherit the variable
    load_local_env()
    parser = _build_parser()
    args = parser.parse_args()
    _apply_trust_flag(args)
    headless = any(
        value is not None for value in (args.print_prompt, args.prompt_json, args.prompt_file)
    )
    if not headless and _has_prompt_cli_flags(args):
        print(
            "error: --system-prompt, --append-system-prompt, --no-context-files, "
            "and --no-git-context require headless mode "
            "(-p, --prompt-json, or --prompt-file)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if headless:
        try:
            prompt = resolve_print_prompt(
                print_prompt=args.print_prompt,
                prompt_json=args.prompt_json,
                prompt_file=args.prompt_file,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        raise SystemExit(
            asyncio.run(
                _print_main(
                    prompt,
                    cwd=args.cwd,
                    prompt_overrides=_prompt_overrides_from_args(args),
                    parent_pid=parent_pid,
                )
            )
        )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
