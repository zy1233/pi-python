"""A workflow script may point a sub-agent at a directory, but only inside the project.

``agent(prompt, cwd=...)`` used to take any path. The permission prompt for a sub-agent's
tool call names the directory, yet nothing kept a script from choosing ``/`` or the home
directory, so one yes to a ``write`` there was a yes for any file the model could name.
Now the directory is resolved against the project root (symlinks followed) and refused
when it is not inside it. Both the runtime (before a slot is spent) and the executor (which
is what actually uses the path) check.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pi_dynamic_workflows.journal import Journal, hash_request
from pi_dynamic_workflows.paths import resolve_subagent_cwd
from pi_dynamic_workflows.runtime import AgentResult, WorkflowRuntime

WINDOWS = sys.platform == "win32"


def _link_dir(link: Path, target: Path) -> None:
    """A symlink to *target*; on Windows without the privilege a junction (same effect)."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if WINDOWS:
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
        )
        if done.returncode == 0:
            return
    pytest.skip("this environment cannot create a directory link")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "sub" / "nested").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    return root


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


class TestResolveSubagentCwd:
    def test_a_directory_inside_the_project_is_allowed_and_comes_back_absolute(
        self, project: Path
    ) -> None:
        got = resolve_subagent_cwd(str(project / "sub"), str(project))
        assert Path(got) == (project / "sub").resolve()
        assert os.path.isabs(got)

    def test_the_project_root_itself_is_allowed(self, project: Path) -> None:
        assert Path(resolve_subagent_cwd(str(project), str(project))) == project.resolve()

    def test_a_relative_path_is_read_against_the_project_not_the_process_cwd(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path / "elsewhere")
        got = resolve_subagent_cwd(os.path.join("sub", "nested"), str(project))
        assert Path(got) == (project / "sub" / "nested").resolve()

    def test_a_relative_root_means_the_process_cwd_it_always_did(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(project)
        assert Path(resolve_subagent_cwd("sub", ".")) == (project / "sub").resolve()

    def test_dot_dot_that_stays_inside_is_fine(self, project: Path) -> None:
        got = resolve_subagent_cwd(os.path.join("sub", "nested", "..", "."), str(project))
        assert Path(got) == (project / "sub").resolve()

    def test_dot_dot_that_leaves_is_refused(self, project: Path) -> None:
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(os.path.join("..", "elsewhere"), str(project))

    def test_an_absolute_path_elsewhere_is_refused(self, project: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(str(tmp_path / "elsewhere"), str(project))

    def test_the_filesystem_root_is_refused(self, project: Path) -> None:
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(os.path.abspath(os.sep), str(project))

    def test_a_sibling_whose_name_starts_with_the_projects_is_refused(
        self, project: Path, tmp_path: Path
    ) -> None:
        """``project-evil`` begins with ``project``: a prefix test on strings lets it in."""
        sibling = tmp_path / "project-evil"
        sibling.mkdir()
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(str(sibling), str(project))

    def test_a_link_inside_the_project_that_points_out_is_refused(
        self, project: Path, tmp_path: Path
    ) -> None:
        _link_dir(project / "door", tmp_path / "elsewhere")
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(str(project / "door"), str(project))

    def test_a_path_beyond_a_link_that_points_out_is_refused(
        self, project: Path, tmp_path: Path
    ) -> None:
        (tmp_path / "elsewhere" / "deeper").mkdir()
        _link_dir(project / "door", tmp_path / "elsewhere")
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(os.path.join("door", "deeper"), str(project))

    def test_a_link_that_stays_inside_is_fine(self, project: Path) -> None:
        _link_dir(project / "shortcut", project / "sub" / "nested")
        got = resolve_subagent_cwd(str(project / "shortcut"), str(project))
        assert Path(got) == (project / "sub" / "nested").resolve()

    def test_the_project_may_itself_be_reached_through_a_link(
        self, project: Path, tmp_path: Path
    ) -> None:
        _link_dir(tmp_path / "alias", project)
        got = resolve_subagent_cwd(str(tmp_path / "alias" / "sub"), str(tmp_path / "alias"))
        assert Path(got) == (project / "sub").resolve()

    def test_a_directory_that_does_not_exist_yet_is_judged_by_where_it_would_be(
        self, project: Path, tmp_path: Path
    ) -> None:
        assert resolve_subagent_cwd(str(project / "later"), str(project))
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(str(tmp_path / "later"), str(project))

    def test_the_message_names_the_path_and_the_project(self, project: Path) -> None:
        with pytest.raises(ValueError) as err:
            resolve_subagent_cwd(os.path.join("..", "elsewhere"), str(project))
        assert str(project) in str(err.value)
        assert "elsewhere" in str(err.value)

    def test_something_that_is_not_a_path_is_a_type_error(self, project: Path) -> None:
        with pytest.raises(TypeError, match="cwd"):
            resolve_subagent_cwd(42, str(project))  # type: ignore[arg-type]

    def test_a_nul_byte_is_refused_not_passed_on(self, project: Path) -> None:
        with pytest.raises(ValueError):
            resolve_subagent_cwd("sub\x00/..", str(project))

    @pytest.mark.skipif(not WINDOWS, reason="drive letters")
    def test_another_drive_is_refused(self, project: Path) -> None:
        other = "D:\\" if project.drive.upper() != "D:" else "C:\\"
        with pytest.raises(ValueError, match="outside the project"):
            resolve_subagent_cwd(other + "anything", str(project))

    @pytest.mark.skipif(not WINDOWS, reason="Windows paths ignore case and take both slashes")
    def test_case_and_slash_style_do_not_make_a_path_look_foreign(self, tmp_path: Path) -> None:
        """A project that is not on disk (yet) is compared as spelled, so only the platform's
        own rules for case and separators can say that two spellings are one place."""
        root = tmp_path / "Ghost-Project"
        spelled = str(root / "Sub").swapcase().replace("\\", "/")
        got = resolve_subagent_cwd(spelled, str(root))
        assert os.path.normcase(got) == os.path.normcase(str(root / "Sub"))


# ---------------------------------------------------------------------------
# The runtime applies it to what a script asks for
# ---------------------------------------------------------------------------


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        self.calls.append({"prompt": prompt, **kwargs})
        return AgentResult(text="ok", tokens_used=10)


def _script(call: str) -> str:
    return f'meta = {{"name": "cwd"}}\nasync def main():\n    result(await {call})\n'


# What escapes ``execute()`` when the script's own ``agent()`` call raises: the error itself
# while scripts run in-process, a RuntimeError carrying its text once they run in a sandbox.
SCRIPT_FAILED = (RuntimeError, ValueError, TypeError)


class TestRuntimeCwd:
    async def test_a_cwd_outside_the_project_never_reaches_the_executor(
        self, project: Path, tmp_path: Path
    ) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project))
        script = _script(f"agent('x', cwd={str(tmp_path / 'elsewhere')!r})")

        with pytest.raises(SCRIPT_FAILED, match="outside the project"):
            await runtime.execute(script)

        assert executor.calls == []

    async def test_the_refusal_spends_neither_an_agent_slot_nor_budget(
        self, project: Path, tmp_path: Path
    ) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project), max_agents=1, token_budget=100)
        script = (
            'meta = {"name": "cwd"}\n'
            "async def main():\n"
            "    try:\n"
            f"        await agent('x', cwd={str(tmp_path / 'elsewhere')!r})\n"
            "    except ValueError:\n"
            "        log('refused')\n"
            "    result(await agent('fine'))\n"
        )

        run = await runtime.execute(script)

        assert run.logs == ["refused"]  # the script saw an ordinary ValueError it can catch
        assert run.agent_count == 1  # only the call that ran took the single slot
        assert run.token_usage["spent"] == 10
        assert [c["prompt"] for c in executor.calls] == ["fine"]

    async def test_a_cwd_inside_the_project_reaches_the_executor_resolved(
        self, project: Path
    ) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project))

        await runtime.execute(_script(f"agent('x', cwd={str(project / 'sub')!r})"))

        assert Path(executor.calls[0]["cwd"]) == (project / "sub").resolve()

    async def test_a_relative_cwd_is_read_against_the_project(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path / "elsewhere")  # a relative path must not mean *here*
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project))

        await runtime.execute(_script("agent('x', cwd='sub')"))

        assert Path(executor.calls[0]["cwd"]) == (project / "sub").resolve()

    async def test_without_a_cwd_the_runtimes_own_is_passed_on_untouched(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd="/test")  # need not exist

        await runtime.execute(_script("agent('x')"))

        assert executor.calls[0]["cwd"] == "/test"

    async def test_an_empty_cwd_means_the_default_as_it_always_did(self, project: Path) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project))

        await runtime.execute(_script("agent('x', cwd='')"))

        assert executor.calls[0]["cwd"] == str(project)

    async def test_a_cwd_that_is_not_a_string_is_a_type_error_in_the_script(
        self, project: Path
    ) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project))

        with pytest.raises(SCRIPT_FAILED, match="cwd"):
            await runtime.execute(_script("agent('x', cwd=5)"))

        assert executor.calls == []

    async def test_a_call_answered_from_the_journal_is_checked_like_any_other(
        self, project: Path, tmp_path: Path
    ) -> None:
        """A policy tightened since the first run must hold for the resume as well."""
        elsewhere = str(tmp_path / "elsewhere")
        script = _script(f"agent('x', cwd={elsewhere!r})")
        first = Journal()
        # First run: the project is wide enough to contain that directory.
        await WorkflowRuntime(RecordingExecutor(), cwd=str(tmp_path), journal=first).execute(script)
        assert first.entry_count == 1

        def resume(root: Path) -> tuple[WorkflowRuntime, RecordingExecutor]:
            journal = Journal()
            journal._entries = list(first._entries)  # what a resume loads from disk
            executor = RecordingExecutor()
            return WorkflowRuntime(executor, cwd=str(root), journal=journal), executor

        runtime, executor = resume(tmp_path)  # control: same project, answered from the journal
        await runtime.execute(script)
        assert executor.calls == []

        runtime, executor = resume(project)  # a narrower project: no longer allowed
        with pytest.raises(SCRIPT_FAILED, match="outside the project"):
            await runtime.execute(script)
        assert executor.calls == []

    async def test_the_journal_key_still_uses_the_cwd_as_the_script_wrote_it(
        self, project: Path
    ) -> None:
        """Resolving the path for the executor must not change what old journals hold."""
        written = hash_request(
            "agent",
            {
                "prompt": "x",
                "tier": None,
                "model": None,
                "label": None,
                "phase": None,
                "schema": None,
                "cwd": "sub",  # as the script wrote it, as journals have always keyed it
            },
        )
        recorded = Journal()
        recorded.append("agent", written, "from an earlier run")
        journal = Journal()
        journal._entries = list(recorded._entries)  # what a resume loads from disk
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=str(project), journal=journal)

        run = await runtime.execute(_script("agent('x', cwd='sub')"))

        assert run.result == "from an earlier run"
        assert executor.calls == []


# ---------------------------------------------------------------------------
# The executor checks too: it is the one that really uses the path
# ---------------------------------------------------------------------------


def _executor(project: Path, **kwargs: Any) -> Any:
    from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

    from pi_agent_core.types import Model

    return HarnessSubagentExecutor(
        stream_fn=None,
        parent_model=Model(provider="mock", model_id="m1"),
        cwd=str(project),
        **kwargs,
    )


class TestExecutorCwd:
    async def test_run_agent_refuses_a_cwd_outside_its_project(
        self, project: Path, tmp_path: Path
    ) -> None:
        executor = _executor(project)
        ran: list[str] = []

        async def fake_run_in(prompt: str, **kwargs: Any) -> AgentResult:
            ran.append(kwargs["effective_cwd"])
            return AgentResult(text="ok")

        executor._run_in = fake_run_in
        with pytest.raises(ValueError, match="outside the project"):
            await executor.run_agent("x", cwd=str(tmp_path / "elsewhere"))
        assert ran == []

    async def test_run_agent_runs_in_the_resolved_directory(self, project: Path) -> None:
        executor = _executor(project)
        ran: list[str] = []

        async def fake_run_in(prompt: str, **kwargs: Any) -> AgentResult:
            ran.append(kwargs["effective_cwd"])
            return AgentResult(text="ok")

        executor._run_in = fake_run_in
        await executor.run_agent("x", cwd="sub")

        assert Path(ran[0]) == (project / "sub").resolve()

    async def test_run_agent_without_a_cwd_uses_the_executors_own(self, project: Path) -> None:
        executor = _executor(project)
        ran: list[str] = []

        async def fake_run_in(prompt: str, **kwargs: Any) -> AgentResult:
            ran.append(kwargs["effective_cwd"])
            return AgentResult(text="ok")

        executor._run_in = fake_run_in
        await executor.run_agent("x")

        assert ran == [str(project)]

    async def test_a_refused_cwd_does_not_create_a_worktree(
        self, project: Path, tmp_path: Path
    ) -> None:
        created: list[int] = []

        class Manager:
            async def create(self) -> str:
                created.append(1)
                return str(tmp_path / "wt")

        executor = _executor(project, worktree_manager=Manager())
        with pytest.raises(ValueError, match="outside the project"):
            await executor.run_agent("x", cwd=str(tmp_path / "elsewhere"))
        assert created == []
