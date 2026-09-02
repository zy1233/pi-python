"""stdio entry: python -m pi_agent_cli

Default: ACP agent on stdio.
`python -m pi_agent_cli -p "..."`: one-shot headless turn (no TUI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from acp import run_agent

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import load_local_env
from pi_agent_cli.headless import HeadlessPromptOverrides, resolve_print_prompt, run_print

_PROMPT_CLI_FLAG_NAMES = (
    "system_prompt",
    "system_prompt_file",
    "append_system_prompt",
    "append_system_prompt_file",
    "no_context_files",
)


async def _amain() -> None:
    await run_agent(PiAcpAgent())


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
    return parser


def _prompt_overrides_from_args(args: argparse.Namespace) -> HeadlessPromptOverrides:
    return HeadlessPromptOverrides(
        system_prompt=args.system_prompt,
        system_prompt_file=args.system_prompt_file,
        append_system_prompt=args.append_system_prompt,
        append_system_prompt_file=args.append_system_prompt_file,
        no_context_files=True if args.no_context_files else None,
    )


def _has_prompt_cli_flags(args: argparse.Namespace) -> bool:
    return any(getattr(args, name) for name in _PROMPT_CLI_FLAG_NAMES)


def main() -> None:
    load_local_env()
    parser = _build_parser()
    args = parser.parse_args()
    headless = any(
        value is not None for value in (args.print_prompt, args.prompt_json, args.prompt_file)
    )
    if not headless and _has_prompt_cli_flags(args):
        print(
            "error: --system-prompt, --append-system-prompt, and --no-context-files "
            "require headless mode (-p, --prompt-json, or --prompt-file)",
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
                run_print(
                    prompt,
                    cwd=args.cwd,
                    prompt_overrides=_prompt_overrides_from_args(args),
                )
            )
        )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
