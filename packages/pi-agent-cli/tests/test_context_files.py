"""Tests for AGENTS.md / SYSTEM.md context file discovery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
    (tmp_path / "repo" / ".git").mkdir()
    (project / "AGENTS.md").write_text("Project rules", encoding="utf-8")

    files = discover_context_files(cwd=project, home=tmp_path)
    contents = [item.content for item in files]
    assert "Global rules" in contents
    assert "Project rules" in contents


def test_agents_override_preferred_in_same_directory(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("Base", encoding="utf-8")
    (root / "AGENTS.override.md").write_text("Override", encoding="utf-8")

    files = discover_context_files(cwd=root, home=tmp_path / "empty")
    paths = [Path(item.path).name for item in files]
    assert "AGENTS.override.md" in paths
    assert "AGENTS.md" in paths


def test_one_file_under_two_names_is_loaded_once(tmp_path):
    """Both ``AGENTS.md`` and ``AGENTS.MD`` are looked for; as one file they make one entry.

    A case-insensitive file system (macOS) makes them one file by itself; elsewhere a hard link
    does.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("Rules", encoding="utf-8")
    try:
        os.link(repo / "AGENTS.md", repo / "AGENTS.MD")
    except FileExistsError:
        pass  # a case-insensitive file system: the two names already are one file
    except OSError:
        pytest.skip("this file system has no hard links")

    files = discover_context_files(cwd=repo, home=tmp_path / "empty")

    assert [item.content for item in files] == ["Rules"]


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


def test_discover_context_files_stops_at_repo_root(tmp_path):
    outside = tmp_path / "AGENTS.md"
    outside.write_text("Outside repo", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("Repo root rules", encoding="utf-8")

    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)
    files = discover_context_files(cwd=nested, home=tmp_path / "empty")
    contents = [item.content for item in files]

    assert "Repo root rules" in contents
    assert "Outside repo" not in contents
