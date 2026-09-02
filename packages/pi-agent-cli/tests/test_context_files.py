"""Tests for AGENTS.md / SYSTEM.md context file discovery."""

from __future__ import annotations

from pathlib import Path

from pi_agent_cli.context_files import (
    discover_context_files,
    load_append_system_prompt_file,
    load_system_prompt_file,
)


def test_discover_context_files_walks_up_and_includes_global(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "AGENTS.md").write_text("Global rules", encoding="utf-8")

    project = tmp_path / "repo" / "src"
    project.mkdir(parents=True)
    (project / "AGENTS.md").write_text("Project rules", encoding="utf-8")

    files = discover_context_files(cwd=project, home=tmp_path)
    contents = [item.content for item in files]
    assert "Global rules" in contents
    assert "Project rules" in contents


def test_agents_override_preferred_in_same_directory(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("Base", encoding="utf-8")
    (root / "AGENTS.override.md").write_text("Override", encoding="utf-8")

    files = discover_context_files(cwd=root, home=tmp_path / "empty")
    paths = [Path(item.path).name for item in files]
    assert "AGENTS.override.md" in paths
    assert "AGENTS.md" in paths


def test_system_and_append_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "SYSTEM.md").write_text("Custom system", encoding="utf-8")
    (tmp_path / "agent" / "APPEND_SYSTEM.md").write_text("Extra rules", encoding="utf-8")

    project = tmp_path / "repo"
    project.mkdir()

    assert load_system_prompt_file(cwd=project, home=tmp_path) == "Custom system"
    assert load_append_system_prompt_file(cwd=project, home=tmp_path) == "Extra rules"

    (project / ".pi").mkdir()
    (project / ".pi" / "SYSTEM.md").write_text("Project system", encoding="utf-8")
    assert load_system_prompt_file(cwd=project, home=tmp_path) == "Project system"
