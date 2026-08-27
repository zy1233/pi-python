#!/usr/bin/env python3
"""Manual pelican-on-a-bicycle smoke test for pi TUI / pi-agent-cli.

Exercises the same Python agent path the Rust TUI spawns. Saves SVG under
``~/.pi-python/benchmarks/pelican/`` for visual inspection.

Usage (PowerShell):
    . $env:USERPROFILE\\.pi-python\\local.env.ps1
    .venv-tui\\Scripts\\python.exe scripts/smoke_pelican.py

Exit 0 = structural PASS (not artistic quality). See docs/benchmarks/PELCAN-BICYCLE.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

from pi_agent_cli.benchmarks.pelican import (
    PELICAN_PROMPT,
    extract_svg,
    save_pelican_artifact,
    validate_pelican_svg,
)
from pi_agent_cli.config import load_config, pi_home
from pi_agent_cli.factory import create_session_harness, default_stream_fn, load_session_resources
from pi_agent_cli.headless import assistant_text
from pi_agent_harness import JsonlSessionRepo


def _report(report, *, artifact: Path | None) -> bool:
    print(f"\n=== pelican-on-a-bicycle: {'PASS' if report.ok else 'FAIL'} ===")
    for label, passed in report.checks.items():
        print(f"  [{'x' if passed else ' '}] {label}")
    for note in report.notes:
        print(f"  note: {note}")
    if artifact:
        print(f"  artifact: {artifact}")
    return report.ok


async def _run(cwd: Path) -> bool:
    key = os.environ.get("REAL_LLM_API_KEY") or os.environ.get("SMOKE_API_KEY")
    if not key:
        print("REAL_LLM_API_KEY (or SMOKE_API_KEY) is not set; aborting.", file=sys.stderr)
        return False

    home = pi_home()
    config = replace(load_config(home), permission="auto")
    repo = JsonlSessionRepo(home / "sessions")
    session = await repo.create({"cwd": str(cwd.resolve())})
    resources = await load_session_resources(cwd=cwd, config=config)
    harness = create_session_harness(
        session=session,
        cwd=cwd,
        config=config,
        stream_fn=default_stream_fn(),
        resources=resources,
    )
    print(f"prompt: {PELICAN_PROMPT!r}")
    print(f"model: {config.provider}/{config.model_id} @ {config.base_url}")
    message = await harness.prompt(PELICAN_PROMPT)
    text = assistant_text(message)
    report = validate_pelican_svg(extract_svg(text))
    artifact = save_pelican_artifact(report.svg, home=home) if report.svg else None
    if not report.ok:
        print("\n--- model output (first 1200 chars) ---")
        print(text[:1200])
    return _report(report, artifact=artifact)


def main() -> None:
    cwd = Path.cwd()
    ok = asyncio.run(_run(cwd))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
