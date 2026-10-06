"""Workflow extension: one home, and no silent mock executor (audit P7-13, P7-14)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pi_dynamic_workflows import _try_build_real_executor, activate
from pi_dynamic_workflows.runtime import AgentResult, UnavailableSubagentExecutor
from pi_dynamic_workflows.store import WorkflowStore
from pi_dynamic_workflows.workflow_tool import WorkflowParams, create_workflow_tool

from pi_agent_core.extensions import ExtensionLoader, ExtensionRegistry
from pi_agent_core.home import HOME_ENV

SCRIPT = """
meta = {"name": "probe"}
async def main():
    r = await agent("hello")
    result(r)
"""


@pytest.fixture()
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fake user home (which must stay untouched) and a separate pi home."""
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.delenv(HOME_ENV, raising=False)
    monkeypatch.setenv("HOME", str(user))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return SimpleNamespace(user=user, pi=tmp_path / "pi-home", env_pi=tmp_path / "env-pi-home")


def _journals(root: Path) -> list[Path]:
    return sorted((root / "workflow-journals").glob("*.jsonl"))


# ---------------------------------------------------------------------------
# P7-13: journals and saved workflows live under the session's home
# ---------------------------------------------------------------------------


class TestJournalHome:
    async def test_the_journal_goes_under_the_home_the_tool_was_given(self, homes: Any):
        tool = create_workflow_tool(home=homes.pi)

        result = await tool.execute("tc-1", WorkflowParams(script=SCRIPT))

        assert "completed" in result.content[0]["text"]
        assert len(_journals(homes.pi)) == 1
        assert not (homes.user / ".pi-python").exists()  # the real home was not touched

    async def test_without_a_home_the_journal_follows_pi_home(
        self, homes: Any, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(HOME_ENV, str(homes.env_pi))
        tool = create_workflow_tool()

        await tool.execute("tc-1", WorkflowParams(script=SCRIPT))

        assert len(_journals(homes.env_pi)) == 1
        assert not (homes.user / ".pi-python").exists()

    async def test_a_resume_reads_the_journal_from_the_same_home(self, homes: Any):
        tool = create_workflow_tool(home=homes.pi)
        first = await tool.execute("tc-1", WorkflowParams(script=SCRIPT))
        run_id = first.details["runId"]

        resumed = await tool.execute(
            "tc-2", WorkflowParams(script=SCRIPT, resume_from_run_id=run_id)
        )

        assert "completed" in resumed.content[0]["text"]  # found it, did not fail to load

    async def test_a_run_id_with_a_trailing_newline_is_not_a_valid_run_id(self, homes: Any):
        """``$`` also matches before a final newline; the id becomes part of a file name."""
        tool = create_workflow_tool(home=homes.pi)

        result = await tool.execute(
            "tc-1", WorkflowParams(script=SCRIPT, resume_from_run_id="abc123\n")
        )

        assert "Invalid resume_from_run_id" in result.content[0]["text"]


class TestStoreHome:
    def _save(self, directory: Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.py").write_text(f'meta = {{"name": "{name}"}}\n', encoding="utf-8")

    def test_the_user_directory_is_below_the_given_pi_home(self, homes: Any):
        self._save(homes.pi / "workflows", "from-pi-home")
        self._save(homes.user / ".pi-python" / "workflows", "from-real-home")

        names = {wf.name for wf in WorkflowStore(cwd=str(homes.pi.parent), pi_home=homes.pi).scan()}

        assert names == {"from-pi-home"}

    def test_the_default_follows_pi_home(self, homes: Any, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(HOME_ENV, str(homes.env_pi))
        self._save(homes.env_pi / "workflows", "from-env-home")
        self._save(homes.user / ".pi-python" / "workflows", "from-real-home")

        names = {wf.name for wf in WorkflowStore(cwd=str(homes.pi.parent)).scan()}

        assert names == {"from-env-home"}

    def test_the_older_home_argument_still_means_the_user_home(self, homes: Any):
        """``WorkflowStore(home=...)`` predates ``pi_home``: it names the *user* home, with
        ``.pi-python`` below it."""
        self._save(homes.pi / ".pi-python" / "workflows", "legacy")

        names = {wf.name for wf in WorkflowStore(cwd=str(homes.pi), home=homes.pi).scan()}

        assert names == {"legacy"}

    def test_pi_home_wins_when_both_arguments_are_given(self, homes: Any):
        self._save(homes.pi / "workflows", "from-pi-home")
        self._save(homes.user / ".pi-python" / "workflows", "from-user-home")

        store = WorkflowStore(cwd=str(homes.pi.parent), home=homes.user, pi_home=homes.pi)

        assert {wf.name for wf in store.scan()} == {"from-pi-home"}

    def test_saving_lands_in_the_pi_home(self, homes: Any):
        store = WorkflowStore(cwd=str(homes.pi.parent), pi_home=homes.pi)

        dest = store.save_user("mine", 'meta = {"name": "mine"}\n')

        assert dest == homes.pi / "workflows" / "mine.py"

    def test_a_name_with_a_trailing_newline_is_refused(self, homes: Any):
        store = WorkflowStore(cwd=str(homes.pi.parent), pi_home=homes.pi)

        with pytest.raises(ValueError, match="Invalid workflow name"):
            store.save_user("mine\n", "x = 1\n")

    @pytest.mark.parametrize("odd_meta", ['{"name": 5}', "5", "[1, 2]", '"just text"'])
    def test_one_script_with_an_odd_meta_does_not_hide_the_others(self, homes: Any, odd_meta: str):
        """A script's ``meta`` is untrusted input; a non-text name or a non-dict value used
        to raise out of the scan, and every saved workflow vanished from the commands."""
        self._save(homes.pi / "workflows", "good")
        (homes.pi / "workflows" / "odd.py").write_text(f"meta = {odd_meta}\n", encoding="utf-8")

        names = {wf.name for wf in WorkflowStore(cwd=str(homes.pi.parent), pi_home=homes.pi).scan()}

        assert "good" in names

    def test_saved_workflows_become_commands_from_the_extensions_home(self, homes: Any):
        self._save(homes.pi / "workflows", "saved-in-pi-home")
        self._save(homes.user / ".pi-python" / "workflows", "saved-in-real-home")
        registry = ExtensionRegistry()
        loader = ExtensionLoader(registry, home=homes.pi)

        loader.load_callable(activate, name="wf", bridge=_Bridge())

        commands = registry.get_commands()
        assert "saved-in-pi-home" in commands
        assert "saved-in-real-home" not in commands


# ---------------------------------------------------------------------------
# P7-14: no silent fallback to the mock executor
# ---------------------------------------------------------------------------


class _Bridge:
    """Just enough bridge for ``activate``; ``stream_fn`` / ``model`` are settable."""

    stream_fn: Any = None
    model: Any = None
    get_api_key_fn: Any = None
    tool_call_gate: Any = None
    cwd = "."
    session_id = ""

    def register_cleanup(self, callback: Any) -> None:
        pass

    def inject_tool(self, definition: Any) -> None:
        pass


def _load_workflow_extension(bridge: Any, home: Path) -> Any:
    registry = ExtensionRegistry()
    ExtensionLoader(registry, home=home).load_callable(activate, name="wf", bridge=bridge)
    return registry.get_tools()["workflow"]


class TestExecutorAvailability:
    async def test_a_session_without_a_model_refuses_instead_of_running_the_mock(
        self, homes: Any, caplog: pytest.LogCaptureFixture
    ):
        """Any failure to build the real executor used to fall back to the mock, so a
        workflow "completed" with canned ``[mock agent response to: ...]`` text."""
        with caplog.at_level(logging.WARNING):
            tool = _load_workflow_extension(_Bridge(), homes.pi)

        result = await tool.execute("tc-1", WorkflowParams(script=SCRIPT))

        text = result.content[0]["text"]
        assert "unavailable" in text.lower()
        assert "mock agent response" not in text
        assert "completed" not in text
        assert _journals(homes.pi) == []  # nothing ran, so nothing was journaled
        assert any(
            r.levelno >= logging.WARNING and "unavailable" in r.getMessage().lower()
            for r in caplog.records
        )

    async def test_the_reason_is_part_of_the_answer(self, homes: Any):
        tool = _load_workflow_extension(_Bridge(), homes.pi)

        result = await tool.execute("tc-1", WorkflowParams(script=SCRIPT))

        assert "stream_fn" in result.content[0]["text"]

    async def test_a_background_request_gets_the_same_answer(self, homes: Any):
        tool = _load_workflow_extension(_Bridge(), homes.pi)

        result = await tool.execute("tc-1", WorkflowParams(script=SCRIPT, background=True))

        assert "unavailable" in result.content[0]["text"].lower()

    async def test_a_built_in_workflow_gets_the_same_answer(self, homes: Any):
        tool = _load_workflow_extension(_Bridge(), homes.pi)

        result = await tool.execute(
            "tc-1", WorkflowParams(name="deep-research", args={"question": "What is Python?"})
        )

        assert "unavailable" in result.content[0]["text"].lower()

    async def test_the_unavailable_executor_reports_its_reason_as_an_error(self):
        result = await UnavailableSubagentExecutor("no model").run_agent("hi")

        assert isinstance(result, AgentResult)
        assert result.text is None
        assert result.error is not None and "no model" in result.error

    async def test_a_tool_built_without_an_executor_still_uses_the_mock(self, homes: Any):
        """The mock is the documented test double of ``create_workflow_tool()``; only the
        extension's own wiring must never fall back to it."""
        tool = create_workflow_tool(home=homes.pi)

        result = await tool.execute("tc-1", WorkflowParams(script=SCRIPT))

        assert "completed" in result.content[0]["text"]

    def test_a_bridge_that_is_not_connected_is_reported(self, caplog: pytest.LogCaptureFixture):
        class _Pi:
            cwd = "."

            def _require_bridge(self):
                raise RuntimeError("not connected to a harness")

        with caplog.at_level(logging.WARNING, logger="pi_dynamic_workflows"):
            executor, manager = _try_build_real_executor(_Pi())

        assert (executor, manager) == (None, None)
        assert any(
            "not connected" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_an_unexpected_failure_is_reported_with_its_traceback(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model

        class _Pi:
            cwd = "."

            def _require_bridge(self):
                bridge = _Bridge()
                bridge.stream_fn = mock_text_stream
                bridge.model = Model(provider="mock", model_id="m1")
                bridge.tool_call_gate = object()  # present: no separate "no gate" warning
                return bridge

        def boom(*args: Any, **kwargs: Any) -> None:
            raise ImportError("pi_agent_harness is not installed")

        monkeypatch.setattr("pi_dynamic_workflows.subagent.HarnessSubagentExecutor", boom)

        with caplog.at_level(logging.WARNING, logger="pi_dynamic_workflows"):
            executor, manager = _try_build_real_executor(_Pi())

        assert (executor, manager) == (None, None)
        (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "pi_agent_harness is not installed" in record.getMessage()
        assert record.exc_info is not None
