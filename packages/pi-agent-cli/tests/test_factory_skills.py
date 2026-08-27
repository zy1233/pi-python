"""Skills loading for session harness construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent_cli.config import load_config
from pi_agent_cli.factory import create_session_harness, default_stream_fn, load_session_resources
from pi_agent_cli.prompt import CODING_SYSTEM_PROMPT
from pi_agent_harness import JsonlSessionRepo
from pi_agent_harness.system_prompt import build_harness_system_prompt


@pytest.mark.asyncio
async def test_create_session_harness_loads_skills(tmp_path: Path, monkeypatch):
    skill_dir = tmp_path / "skills" / "writer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: writer\ndescription: Write polished text\n---\nBody\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "agent.toml"
    config_path.write_text('[skills]\npaths = ["skills"]\n', encoding="utf-8")
    monkeypatch.setenv("PI_USE_MOCK", "1")
    monkeypatch.setenv("PI_HOME", str(tmp_path))

    config = load_config(tmp_path)
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
    loaded = harness.get_resources().skills or []
    assert [skill.name for skill in loaded] == ["writer"]
    prompt = build_harness_system_prompt(CODING_SYSTEM_PROMPT, resources)
    assert '<skill name="writer"' in prompt
