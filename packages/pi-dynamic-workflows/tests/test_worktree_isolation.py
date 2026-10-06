"""Worktree isolation (audit P7-10).

A sub-agent run with ``isolation=True`` works in a linked git worktree and its changes are
written back to the project when it finishes. Three things were wrong:

* the write-back was ``git diff HEAD`` — no *new* files, so what the agent created was lost;
* the diff went through ``decode(errors="replace")`` / ``encode``, so a file that is not
  UTF-8 (GBK source, binary) came back damaged and ``git apply`` refused it;
* a worktree was only removed on the success path: a timeout, an error, or a cancel left it
  (and its directory) behind, and a background run never cleaned up at all.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pi_dynamic_workflows.manager import WorkflowManager
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor, WorkflowRuntime
from pi_dynamic_workflows.subagent import HarnessSubagentExecutor
from pi_dynamic_workflows.workflow_tool import WorkflowParams, create_workflow_tool
from pi_dynamic_workflows.worktree import WorktreeManager

from pi_agent_core.agent import _AbortSignal
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, Model, StartEvent

GBK_TEXT = "第一行\n你好世界\n第三行\n".encode("gbk")
BLOB = bytes(range(256)) * 4  # has NUL bytes: git treats it as binary


def _git(repo: Path, *args: str) -> bytes:
    done = subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)
    return done.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    for key, value in (
        ("user.email", "test@test"),
        ("user.name", "test"),
        ("core.autocrlf", "false"),  # the bytes written are the bytes compared
        ("core.safecrlf", "false"),
    ):
        _git(root, "config", key, value)
    (root / "a.txt").write_text("original\n", encoding="utf-8")
    (root / "gbk.txt").write_bytes(GBK_TEXT)
    (root / "blob.bin").write_bytes(BLOB)
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    return root


def _leftover_worktrees(repo: Path) -> list[str]:
    """Linked worktrees still registered with *repo* (the main one is not counted)."""
    listing = _git(repo, "worktree", "list", "--porcelain").decode("utf-8", errors="replace")
    paths = [
        line[len("worktree ") :] for line in listing.splitlines() if line.startswith("worktree ")
    ]
    return paths[1:]


async def _isolated(repo: Path) -> tuple[WorktreeManager, str]:
    manager = WorktreeManager(str(repo))
    return manager, await manager.create()


class TestWriteBack:
    @pytest.mark.parametrize("name", ["new.txt", "sub/dir/new.txt", "中文.txt"])
    async def test_a_file_the_subagent_created_is_written_back(self, repo: Path, name: str):
        manager, wt = await _isolated(repo)
        target = Path(wt) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("created by the agent\n", encoding="utf-8")

        await manager.apply_changes(wt)

        assert (repo / name).read_text(encoding="utf-8") == "created by the agent\n"
        await manager.cleanup(wt)

    async def test_files_git_ignores_are_not_written_back(self, repo: Path):
        manager, wt = await _isolated(repo)
        (Path(wt) / "build").mkdir()
        (Path(wt) / "build" / "out.o").write_bytes(b"object")
        (Path(wt) / "keep.txt").write_text("keep\n", encoding="utf-8")

        await manager.apply_changes(wt)

        assert (repo / "keep.txt").exists()
        assert not (repo / "build").exists()
        await manager.cleanup(wt)

    async def test_the_diff_is_bytes(self, repo: Path):
        manager, wt = await _isolated(repo)
        (Path(wt) / "a.txt").write_text("changed\n", encoding="utf-8")

        diff = await manager.collect_diff(wt)

        assert isinstance(diff, bytes)
        assert b"changed" in diff
        await manager.cleanup(wt)

    async def test_a_file_that_is_not_utf8_comes_back_intact(self, repo: Path):
        manager, wt = await _isolated(repo)
        edited = GBK_TEXT.replace("你好".encode("gbk"), "再见".encode("gbk"))
        assert edited != GBK_TEXT
        (Path(wt) / "gbk.txt").write_bytes(edited)

        await manager.apply_changes(wt)

        assert (repo / "gbk.txt").read_bytes() == edited
        await manager.cleanup(wt)

    async def test_binary_files_come_back_intact_whether_changed_or_new(self, repo: Path):
        manager, wt = await _isolated(repo)
        changed = bytes(reversed(BLOB))
        (Path(wt) / "blob.bin").write_bytes(changed)
        (Path(wt) / "fresh.bin").write_bytes(b"\x00\x01\xff" * 100)

        await manager.apply_changes(wt)

        assert (repo / "blob.bin").read_bytes() == changed
        assert (repo / "fresh.bin").read_bytes() == b"\x00\x01\xff" * 100
        await manager.cleanup(wt)

    def test_what_git_says_about_a_failure_is_readable_whatever_its_bytes(self):
        from pi_dynamic_workflows.worktree import _text

        assert _text(b"error: bad \xff\xfe name\n") == "error: bad \ufffd\ufffd name"

    async def test_a_directory_that_is_not_a_worktree_is_an_error_not_an_empty_patch(
        self, repo: Path, tmp_path: Path
    ):
        plain = tmp_path / "plain"
        plain.mkdir()

        with pytest.raises(RuntimeError, match="git add failed"):
            await WorktreeManager(str(repo)).collect_diff(str(plain))

    async def test_a_diff_git_cannot_produce_is_an_error_not_an_empty_patch(
        self, repo: Path, tmp_path: Path
    ):
        unborn = tmp_path / "unborn"  # a repository whose HEAD is not a commit yet
        unborn.mkdir()
        _git(unborn, "init")
        (unborn / "f.txt").write_text("x\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="git diff failed"):
            await WorktreeManager(str(repo)).collect_diff(str(unborn))

    async def test_nothing_is_written_when_the_subagent_changed_nothing(self, repo: Path):
        manager, wt = await _isolated(repo)

        await manager.apply_changes(wt)

        assert _git(repo, "status", "--porcelain") == b""
        await manager.cleanup(wt)

    async def test_only_the_subagents_own_changes_are_written_back_over_a_dirty_source(
        self, repo: Path
    ):
        (repo / "a.txt").write_text("dirty\n", encoding="utf-8")
        manager, wt = await _isolated(repo)
        assert (Path(wt) / "a.txt").read_text(encoding="utf-8") == "dirty\n"
        (Path(wt) / "made.txt").write_text("made\n", encoding="utf-8")

        await manager.apply_changes(wt)

        assert (repo / "a.txt").read_text(encoding="utf-8") == "dirty\n"
        assert (repo / "made.txt").read_text(encoding="utf-8") == "made\n"
        await manager.cleanup(wt)


def _snapshot_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Snapshot" in r.getMessage()]


class TestSnapshot:
    async def test_a_binary_change_in_the_source_reaches_the_worktree(
        self, repo: Path, caplog: pytest.LogCaptureFixture
    ):
        changed = bytes(reversed(BLOB))
        (repo / "blob.bin").write_bytes(changed)

        manager, wt = await _isolated(repo)

        assert (Path(wt) / "blob.bin").read_bytes() == changed
        assert _snapshot_warnings(caplog) == []
        await manager.cleanup(wt)

    async def test_a_staged_binary_change_reaches_the_worktree(
        self, repo: Path, caplog: pytest.LogCaptureFixture
    ):
        changed = bytes(reversed(BLOB))
        (repo / "blob.bin").write_bytes(changed)
        _git(repo, "add", "blob.bin")

        manager, wt = await _isolated(repo)

        assert (Path(wt) / "blob.bin").read_bytes() == changed
        assert _snapshot_warnings(caplog) == []
        await manager.cleanup(wt)


def _executor(stream_fn: Any, repo: Path, manager: WorktreeManager) -> HarnessSubagentExecutor:
    return HarnessSubagentExecutor(
        stream_fn=stream_fn,
        parent_model=Model(provider="mock", model_id="m1"),
        cwd=str(repo),
    ).with_worktree_manager(manager)


def _writes(path: str, wrote: asyncio.Event | None = None):
    """A sub-agent LLM that writes ``path`` and then answers; with *wrote*, it sets the
    event once the file is written and then never answers."""

    async def stream(model, context, options=None):
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            if wrote is None:
                return await mock_text_stream(model, context, options)
            wrote.set()
            await asyncio.sleep(3600)
        call = {
            "type": "toolCall",
            "id": "call_1",
            "name": "write",
            "arguments": {"path": path, "content": "written\n"},
        }
        partial = _base_partial(model, [call])
        partial.stopReason = "toolUse"
        events = AssistantMessageEventStream()
        events.push(StartEvent(partial=partial.model_copy(deep=True)))
        events.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        events.set_final_message(partial)
        events.end()
        return events

    return stream


class TestCleanup:
    async def test_a_finished_subagent_leaves_no_worktree(self, repo: Path):
        manager = WorktreeManager(str(repo))

        result = await _executor(mock_text_stream, repo, manager).run_agent("go")

        assert result.error is None
        assert _leftover_worktrees(repo) == []
        assert manager._active == []

    async def test_a_finished_subagents_changes_are_written_back(self, repo: Path):
        manager = WorktreeManager(str(repo))

        result = await _executor(_writes("note.txt"), repo, manager).run_agent("go")

        assert result.error is None
        assert (repo / "note.txt").read_text(encoding="utf-8") == "written\n"
        assert _leftover_worktrees(repo) == []

    async def test_a_timed_out_subagent_leaves_no_worktree_and_writes_nothing_back(
        self, repo: Path
    ):
        manager = WorktreeManager(str(repo))
        wrote = asyncio.Event()
        executor = _executor(_writes("note.txt", wrote), repo, manager)

        result = await executor.run_agent("go", timeout_ms=3000)

        assert result.error == "timeout"
        assert wrote.is_set()  # the file was written in the worktree before the time ran out
        assert _leftover_worktrees(repo) == []
        assert manager._active == []
        assert not (repo / "note.txt").exists()  # a failed run's changes are discarded

    async def test_a_failing_subagent_leaves_no_worktree(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from pi_agent_harness.agent_harness import AgentHarness

        async def boom(self: Any, text: str, images: Any = None) -> Any:
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(AgentHarness, "prompt", boom)
        manager = WorktreeManager(str(repo))

        result = await _executor(mock_text_stream, repo, manager).run_agent("go")

        assert result.error == "provider exploded"
        assert _leftover_worktrees(repo) == []
        assert manager._active == []

    async def test_a_cancelled_subagent_leaves_no_worktree(self, repo: Path):
        started = asyncio.Event()

        async def hang(model, context, options=None):
            started.set()
            await asyncio.sleep(3600)

        manager = WorktreeManager(str(repo))
        task = asyncio.create_task(_executor(hang, repo, manager).run_agent("go"))
        await asyncio.wait_for(started.wait(), timeout=30)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert _leftover_worktrees(repo) == []
        assert manager._active == []

    async def test_a_cancel_that_arrives_while_a_timed_out_agent_unwinds_gets_through(
        self, repo: Path
    ):
        started = asyncio.Event()

        async def slow_to_cancel(model, context, options=None):
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(0.5)  # the agent takes its time to wind down
                raise

        manager = WorktreeManager(str(repo))
        executor = _executor(slow_to_cancel, repo, manager)
        task = asyncio.create_task(executor.run_agent("go", timeout_ms=100))
        await asyncio.wait_for(started.wait(), timeout=30)
        await asyncio.sleep(0.3)  # the 100 ms are spent: the call is waiting for the unwind

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert _leftover_worktrees(repo) == []

    async def test_a_worktree_that_cannot_be_removed_does_not_change_the_answer(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ):
        async def stuck(self: Any, worktree_path: str) -> None:
            raise RuntimeError("worktree is locked")

        monkeypatch.setattr(WorktreeManager, "cleanup", stuck)
        manager = WorktreeManager(str(repo))

        result = await _executor(mock_text_stream, repo, manager).run_agent("go")

        assert result.error is None
        assert result.text
        monkeypatch.undo()
        await manager.cleanup_all()  # the stuck one is really removed, by the run's owner

    async def test_a_worktree_that_cannot_be_applied_is_still_removed(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ):
        async def refuse(self: Any, worktree_path: str, *, target: str | None = None) -> None:
            raise RuntimeError("apply_changes failed: patch does not apply")

        monkeypatch.setattr(WorktreeManager, "apply_changes", refuse)
        manager = WorktreeManager(str(repo))

        result = await _executor(mock_text_stream, repo, manager).run_agent("go")

        assert result.error is None  # the agent's answer is still the answer
        assert _leftover_worktrees(repo) == []


class _Bridge:
    """What ``WorkflowManager`` needs of a bridge; the delivered messages are recorded."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    def trigger_prompt(self, text: str) -> None:
        self.delivered.append(text)

    def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
        self.delivered.append(text)


class _LeakyExecutor(SubagentExecutor):
    """Creates a worktree per agent and never removes it: only the run's owner can."""

    def __init__(self, manager: WorktreeManager | None = None) -> None:
        self._manager = manager

    def with_worktree_manager(self, manager: WorktreeManager) -> _LeakyExecutor:
        return _LeakyExecutor(manager)

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        assert self._manager is not None
        await self._manager.create()
        return AgentResult(text="done")


ONE_AGENT = 'meta = {"name": "one"}\nasync def main():\n    await agent("go")\n    result("done")\n'


class TestTheRunCleansUpAfterItself:
    async def test_a_foreground_run_removes_worktrees_its_agents_left(
        self, repo: Path, tmp_path: Path
    ):
        tool = create_workflow_tool(executor=_LeakyExecutor(), cwd=str(repo), home=tmp_path / "pi")

        result = await tool.execute("tc", WorkflowParams(script=ONE_AGENT, isolation=True))

        assert "completed" in result.content[0]["text"]
        assert _leftover_worktrees(repo) == []

    async def test_an_aborted_foreground_run_removes_the_worktrees_of_its_running_agents(
        self, repo: Path, tmp_path: Path
    ):
        started = asyncio.Event()

        async def hang(model, context, options=None):
            started.set()
            await asyncio.sleep(3600)

        executor = HarnessSubagentExecutor(
            stream_fn=hang, parent_model=Model(provider="mock", model_id="m1"), cwd=str(repo)
        )
        tool = create_workflow_tool(executor=executor, cwd=str(repo), home=tmp_path / "pi")
        signal = _AbortSignal()
        call = asyncio.create_task(
            tool.execute("tc", WorkflowParams(script=ONE_AGENT, isolation=True), signal)
        )
        await asyncio.wait_for(started.wait(), timeout=30)
        assert len(_leftover_worktrees(repo)) == 1  # the agent is working in its worktree

        signal.abort()
        result = await asyncio.wait_for(call, timeout=30)

        assert "cancelled" in result.content[0]["text"].lower()
        assert _leftover_worktrees(repo) == []

    async def test_a_background_run_removes_worktrees_its_agents_left(
        self, repo: Path, tmp_path: Path
    ):
        bridge = _Bridge()
        manager = WorkflowManager(bridge)  # type: ignore[arg-type]
        tool = create_workflow_tool(
            executor=_LeakyExecutor(), cwd=str(repo), manager=manager, home=tmp_path / "pi"
        )

        await tool.execute("tc", WorkflowParams(script=ONE_AGENT, isolation=True, background=True))
        for _ in range(200):
            if bridge.delivered:
                break
            await asyncio.sleep(0.05)

        assert bridge.delivered, "the background run never finished"
        assert _leftover_worktrees(repo) == []

    async def test_a_cleanup_runs_when_the_background_script_fails(self):
        cleaned: list[str] = []

        async def cleanup() -> None:
            cleaned.append("cleaned")

        manager = WorkflowManager(_Bridge())  # type: ignore[arg-type]
        runtime = WorkflowRuntime(_LeakyExecutor())
        script = 'meta = {"name": "bad"}\nasync def main():\n    raise RuntimeError("bad script")\n'

        await manager.start_background(runtime, script, None, run_id="r1", cleanup=cleanup)
        for _ in range(100):
            if manager.pending_count == 0:
                break
            await asyncio.sleep(0.02)

        assert cleaned == ["cleaned"]

    async def test_a_cleanup_runs_when_the_background_run_is_cancelled(self):
        cleaned: list[str] = []
        entered = asyncio.Event()

        async def cleanup() -> None:
            cleaned.append("cleaned")

        class Hanging(SubagentExecutor):
            async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
                entered.set()
                await asyncio.sleep(3600)
                return AgentResult(text="never")

        manager = WorkflowManager(_Bridge())  # type: ignore[arg-type]
        await manager.start_background(
            WorkflowRuntime(Hanging()), ONE_AGENT, None, run_id="r2", cleanup=cleanup
        )
        await asyncio.wait_for(entered.wait(), timeout=30)

        await manager.cancel_all()
        for _ in range(100):
            if cleaned:
                break
            await asyncio.sleep(0.02)

        assert cleaned == ["cleaned"]

    async def test_a_cleanup_that_raises_does_not_lose_the_result(self):
        bridge = _Bridge()

        async def cleanup() -> None:
            raise RuntimeError("worktree remove failed")

        manager = WorkflowManager(bridge)  # type: ignore[arg-type]
        await manager.start_background(
            WorkflowRuntime(_LeakyExecutor()),
            'meta = {"name": "ok"}\nasync def main():\n    result("fine")\n',
            None,
            run_id="r3",
            cleanup=cleanup,
        )
        for _ in range(100):
            if bridge.delivered:
                break
            await asyncio.sleep(0.02)

        assert len(bridge.delivered) == 1
        assert "fine" in bridge.delivered[0]
