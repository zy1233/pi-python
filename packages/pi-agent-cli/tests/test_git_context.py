"""Git workspace snapshot injected into the system prompt."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pi_agent_cli.config import CliConfig
from pi_agent_cli.factory import create_session_harness
from pi_agent_cli.git_context import GitSnapshot, format_git_status, snapshot_git_context
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_cli.system_prompt import BuildSystemPromptOptions, ContextFile, build_system_prompt
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness import JsonlSessionRepo
from pi_agent_harness.types import Skill


def _git(cwd: Path, *args: str) -> None:
    # Bytes, not text: git echoes non-ASCII branch names, and decoding those with the
    # locale codec (cp936 on Chinese Windows) would crash the test helper itself.
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _init_repo(path: Path, branch: str = "main") -> None:
    _git(path, "init", "-b", branch)
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


# Branch names chosen so that, decoded with a GBK locale, some raise (`功`, `功能分`)
# and the rest silently turn into mojibake (`功能`, `feature-功能分支`).
@pytest.mark.parametrize("branch", ["功", "功能", "功能分", "feature-功能分支"])
def test_snapshot_keeps_non_ascii_branch_and_paths_intact(tmp_path: Path, branch: str):
    _init_repo(tmp_path, branch=branch)
    (tmp_path / "中文文件.txt").write_text("x\n", encoding="utf-8")

    snapshot = snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=40)

    assert snapshot is not None
    assert snapshot.branch == branch
    assert f"## {branch}" in snapshot.status_porcelain
    # core.quotePath=false: the model sees the real name, not "\344\270\255..." escapes.
    assert "中文文件.txt" in snapshot.status_porcelain
    assert "\\344" not in snapshot.status_porcelain


def test_git_invocation_pins_utf8_and_verbatim_paths(monkeypatch: pytest.MonkeyPatch):
    """Locale-independent guard: never fall back to the locale codec or quoted paths."""
    calls: list[tuple[list[str], dict]] = []
    outputs = {"--is-inside-work-tree": "true\n", "--abbrev-ref": "main\n", "status": "## main\n"}

    def _fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        stdout = next(text for key, text in outputs.items() if key in cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    snapshot = snapshot_git_context("/repo", timeout_seconds=5, max_lines=40)

    assert snapshot is not None
    assert len(calls) == 3
    for cmd, kwargs in calls:
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        subcommand = "status" if "status" in cmd else "rev-parse"
        # `-c key=value` is a global option, so it must precede the subcommand.
        assert cmd.index("core.quotePath=false") < cmd.index(subcommand)


@pytest.mark.parametrize(
    "failure",
    [
        UnicodeDecodeError("gbk", b"\xe5\x8a\x9f\n", 2, 3, "illegal multibyte sequence"),
        subprocess.SubprocessError("boom"),
        ValueError("boom"),
    ],
    ids=["undecodable-output", "subprocess-error", "value-error"],
)
def test_snapshot_omits_section_on_any_git_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception
):
    """The git block is optional prompt context: no failure may escape and break the turn."""

    def _boom(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", _boom)
    assert snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=40) is None


def test_snapshot_omits_section_when_stdout_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A reader-thread decode failure leaves ``stdout=None`` behind; that must not crash."""

    def _no_stdout(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=None, stderr=None)

    monkeypatch.setattr(subprocess, "run", _no_stdout)
    assert snapshot_git_context(str(tmp_path), timeout_seconds=5, max_lines=40) is None


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


def test_format_git_status_declares_itself_a_session_snapshot():
    block = format_git_status(
        GitSnapshot(branch="main", status_porcelain="## main\n M a.py", truncated=False)
    )

    lines = block.splitlines()
    assert lines[0] == "<git_status>"
    assert "not refreshed" in lines[1] and "`git status`" in lines[1]
    assert lines[2:] == ["Branch: main", "## main", " M a.py", "</git_status>"]


@pytest.mark.asyncio
async def test_git_block_is_a_stable_session_snapshot_and_says_so(tmp_path: Path):
    """The harness caches the system prompt for the whole session (prefix-cache stability,
    commit bee053c), so the block cannot track the working tree. It must tell the model
    so, instead of presenting stale state as current."""
    _init_repo(tmp_path)
    prompts: list[str] = []

    async def capture(model, context, options=None):
        prompts.append(context.system_prompt)
        return await mock_text_stream(model, context, options)

    session = await JsonlSessionRepo(tmp_path / "sessions").create({"cwd": str(tmp_path)})
    harness = await create_session_harness(
        session=session, cwd=tmp_path, config=CliConfig(), stream_fn=capture, home=tmp_path
    )

    await harness.prompt("first")
    (tmp_path / "later.txt").write_text("created mid-session\n", encoding="utf-8")
    await harness.prompt("second")

    assert prompts[0] == prompts[1]  # one system prompt per session: the cache prefix holds
    assert "<git_status>" in prompts[1]
    assert "not refreshed" in prompts[1]
    assert "later.txt" not in prompts[1]


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
