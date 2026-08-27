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
from pi_agent_cli.headless import resolve_print_prompt, run_print


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
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    headless = any(
        value is not None for value in (args.print_prompt, args.prompt_json, args.prompt_file)
    )
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
        raise SystemExit(asyncio.run(run_print(prompt, cwd=args.cwd)))
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
