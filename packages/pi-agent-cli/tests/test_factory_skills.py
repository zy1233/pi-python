"""Skills loading for session harness construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent_cli.config import load_config
from pi_agent_cli.create_harness import build_coding_agent_harness_system_prompt
from pi_agent_cli.factory import create_session_harness, default_stream_fn, load_session_resources
from pi_agent_cli.system_prompt import BuildSystemPromptOptions
from pi_agent_core.coding_tools import create_all_tools
from pi_agent_harness import JsonlSessionRepo


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
    harness = await create_session_harness(
        session=session,
        cwd=tmp_path,
        config=config,
        stream_fn=default_stream_fn(),
        resources=resources,
        home=tmp_path,
    )
    loaded = harness.get_resources().skills or []
    assert [skill.name for skill in loaded] == ["writer"]
    tools = list(create_all_tools(str(tmp_path)).values())
    prompt = build_coding_agent_harness_system_prompt(
        cwd=str(tmp_path),
        tools=tools,
        active_tool_names=[tool.name for tool in tools],
        system_prompt_options=BuildSystemPromptOptions(cwd=str(tmp_path), skills=loaded),
    )
    assert "<available_skills>" in prompt
    assert "<name>writer</name>" in prompt
