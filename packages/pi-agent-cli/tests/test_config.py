"""Tests for ~/.pi-python/agent.toml loading."""

from __future__ import annotations

import os
from pathlib import Path

from pi_agent_cli.config import (
    CliConfig,
    expand_config_path,
    load_config,
    load_local_env,
    make_get_api_key,
)


def test_load_config_defaults_when_missing(tmp_path: Path):
    assert load_config(tmp_path) == CliConfig()


def test_load_config_prefers_agent_toml(tmp_path: Path):
    (tmp_path / "config.toml").write_text('permission = "auto"\n', encoding="utf-8")
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "deepseek"\nid = "from-agent"\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.model_id == "from-agent"


def test_load_config_parses_model_skills_and_agent(tmp_path: Path):
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
permission = "auto"
thinking_level = "low"
max_turns = 12

[model]
provider = "deepseek"
id = "deepseek-chat"
base_url = "https://api.example/v1"
api_key_env = "MY_KEY"

[skills]
paths = ["~/.pi-python/skills", ".pi/skills"]

[agent]
command = "python -m pi_agent_cli"
""".strip(),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config == CliConfig(
        permission="auto",
        provider="deepseek",
        model_id="deepseek-chat",
        base_url="https://api.example/v1",
        thinking_level="low",
        max_turns=12,
        api_key_env="MY_KEY",
        skills_dirs=("~/.pi-python/skills", ".pi/skills"),
        agent_command="python -m pi_agent_cli",
    )


def test_make_get_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    getter = make_get_api_key(CliConfig(api_key_env="MY_KEY"))  # type: ignore[arg-type]
    assert getter is not None
    assert getter("deepseek") == "secret"


def test_load_local_env_does_not_override_existing(tmp_path: Path, monkeypatch):
    env_path = tmp_path / "local.env"
    env_path.write_text("REAL_LLM_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("REAL_LLM_API_KEY", "from-env")
    load_local_env(tmp_path)
    assert os.environ["REAL_LLM_API_KEY"] == "from-env"


def test_load_local_env_sets_missing_keys(tmp_path: Path, monkeypatch):
    env_path = tmp_path / "local.env"
    env_path.write_text('export REAL_LLM_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.delenv("REAL_LLM_API_KEY", raising=False)
    load_local_env(tmp_path)
    assert os.environ.get("REAL_LLM_API_KEY") == "from-file"
    rel = tmp_path / "skills"
    rel.mkdir()
    got = expand_config_path(".pi/skills", cwd=tmp_path / "proj")
    assert got == str((tmp_path / "proj" / ".pi" / "skills").resolve())
