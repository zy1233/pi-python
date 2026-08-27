"""Real-LLM pelican-on-a-bicycle benchmark over pi-agent-cli / AgentHarness.

Same stack the pi TUI spawns via ACP. Skipped without ``REAL_LLM_API_KEY``.

Run::

    $env:REAL_LLM_API_KEY = 'sk-...'
    .venv\\Scripts\\python.exe -m pytest \\
        packages/pi-agent-cli/tests/test_pelican_real_llm.py -m real_llm -v
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from pi_agent_cli.benchmarks.pelican import (
    PELICAN_PROMPT,
    extract_svg,
    save_pelican_artifact,
    validate_pelican_svg,
)
from pi_agent_cli.config import load_config
from pi_agent_cli.factory import create_session_harness, default_stream_fn, load_session_resources
from pi_agent_cli.headless import assistant_text
from pi_agent_harness import JsonlSessionRepo

pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(not os.environ.get("REAL_LLM_API_KEY"), reason="REAL_LLM_API_KEY not set"),
]


@pytest.mark.asyncio
async def test_pelican_on_bicycle_agent_path(tmp_path: Path, monkeypatch):
    """Foundation case: one harness turn returns structurally valid SVG."""
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    monkeypatch.delenv("PI_USE_MOCK", raising=False)

    config = replace(load_config(tmp_path), permission="auto")
    repo = JsonlSessionRepo(tmp_path / "sessions")
    session = await repo.create({"cwd": str(tmp_path)})
    resources = await load_session_resources(cwd=tmp_path, config=config)
    harness = create_session_harness(
        session=session,
        cwd=tmp_path,
        config=config,
        stream_fn=default_stream_fn(),
        resources=resources,
    )
    message = await harness.prompt(PELICAN_PROMPT)
    text = assistant_text(message)

    report = validate_pelican_svg(extract_svg(text))
    if report.svg:
        save_pelican_artifact(report.svg, home=tmp_path)
    assert report.ok, f"pelican checks failed: {report.checks} notes={report.notes}\n{text[:800]}"
