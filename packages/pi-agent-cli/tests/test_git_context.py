"""Git workspace snapshot injected into the system prompt."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pi_agent_cli.config import CliConfig
from pi_agent_cli.git_context import snapshot_git_context
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_cli.system_prompt import BuildSystemPromptOptions, ContextFile, build_system_prompt
from pi_agent_harness.types import Skill


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")


def test_snapshot_reports_branch_and_modification(tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")

    snapshot = snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=40)

    assert snapshot is not None
    assert snapshot.branch == "main"
    assert snapshot.status_porcelain.startswith("## main")
    assert "README.md" in snapshot.status_porcelain
    assert snapshot.truncated is False


def test_snapshot_detached_head(tmp_path: Path):
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "--detach")

    snapshot = snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=40)

    assert snapshot is not None
    assert snapshot.branch.startswith("HEAD (detached at ")
    assert snapshot.branch.endswith(")")


def test_snapshot_none_outside_repo(tmp_path: Path):
    assert snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=40) is None


def test_snapshot_none_on_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert snapshot_git_context(str(tmp_path), timeout_seconds=0.01, max_lines=40) is None


def test_snapshot_truncates_status_lines(tmp_path: Path):
    _init_repo(tmp_path)
    for index in range(5):
        (tmp_path / f"f{index}.txt").write_text("x\n", encoding="utf-8")

    snapshot = snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=2)

    assert snapshot is not None
    assert snapshot.truncated is True
    assert snapshot.status_porcelain.endswith("# … truncated")
    assert len(snapshot.status_porcelain.splitlines()) == 3


def test_git_status_sits_between_context_and_skills():
    skills = [
        Skill(
            name="writer",
            description="Write",
            content="Body",
            filePath="/skills/writer/SKILL.md",
        )
    ]
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd="/workspace",
            selected_tools=["read"],
            context_files=[ContextFile(path="AGENTS.md", content="Rules")],
            skills=skills,
            git_status="<git_status>\nBranch: main\n## main\n</git_status>",
        )
    )
    context_at = prompt.index("<project_context>")
    git_at = prompt.index("<git_status>")
    skills_at = prompt.index("<available_skills>")
    assert context_at < git_at < skills_at
    assert "Branch: main" in prompt


def test_no_git_context_omits_section(tmp_path: Path):
    _init_repo(tmp_path)
    enabled = load_system_prompt_options(cwd=tmp_path, config=CliConfig(), home=tmp_path)
    disabled = load_system_prompt_options(
        cwd=tmp_path,
        config=CliConfig(git_enabled=False),
        home=tmp_path,
    )
    assert enabled.git_status is not None
    assert "<git_status>" in (enabled.git_status or "")
    assert disabled.git_status is None


def test_custom_prompt_still_includes_git_status():
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd="/workspace",
            custom_prompt="Custom body",
            git_status="<git_status>\nBranch: main\n</git_status>",
        )
    )
    assert prompt.startswith("Custom body")
    assert "<git_status>" in prompt
    assert "Branch: main" in prompt
