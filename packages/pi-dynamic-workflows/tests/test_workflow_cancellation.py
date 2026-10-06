"""A foreground ``workflow`` run stops when the turn that started it is aborted (audit P7-06).

The tool was handed the turn's abort signal and never looked at it: pressing cancel left the
sub-agents running (and spending tokens) until the script finished on its own. Now the tool
races the run against the signal; on abort it cancels the run, waits for it to wind down
(worktrees are removed by the ``finally`` blocks on the way out) and answers "cancelled".
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pi_dynamic_workflows import workflow_tool
from pi_dynamic_workflows.manager import WorkflowManager
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor
from pi_dynamic_workflows.workflow_tool import WorkflowParams, create_workflow_tool

from pi_agent_core.agent import _AbortSignal

SERIAL = """
meta = {"name": "serial"}
async def main():
    await agent("first")
    await agent("second")
    result("done")
"""

FAN_OUT = """
meta = {"name": "fan"}
async def main():
    await parallel([lambda: agent("a"), lambda: agent("b"), lambda: agent("c")])
    result("done")
"""

PIPELINE = """
meta = {"name": "pipe"}
async def one(value, item, index):
    return await agent("one:" + item)
async def two(value, item, index):
    return await agent("two:" + item)
async def main():
    await pipeline(["x", "y", "z"], one, two)
    result("done")
"""


class _Agents(SubagentExecutor):
    """Sub-agents that run until they are cancelled; what started and stopped is recorded.

    A prompt in *quick* is answered at once. Winding down after a cancel takes *unwind_s*,
    so a caller that does not wait for it sees an incomplete ``cancelled`` list.
    """

    def __init__(self, *, quick: set[str] | None = None, unwind_s: float = 0.05) -> None:
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self._quick = quick or set()
        self._unwind_s = unwind_s

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        self.started.append(prompt)
        if prompt in self._quick:
            return AgentResult(text=f"answer to {prompt}")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(self._unwind_s)
            self.cancelled.append(prompt)
            raise
        return AgentResult(text="never")


class _FlagOnly:
    """An abort signal that can only be looked at: no ``wait_aborted()``."""

    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _Bridge:
    """What ``WorkflowManager`` needs of a bridge; the delivered messages are recorded."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    def trigger_prompt(self, text: str) -> None:
        self.delivered.append(text)

    def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
        self.delivered.append(text)


async def _until(condition: Callable[[], Any], *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for a condition"
        await asyncio.sleep(0.01)


def _tool(executor: SubagentExecutor, tmp_path: Path, manager: Any = None) -> Any:
    return create_workflow_tool(
        executor=executor, cwd=str(tmp_path), manager=manager, home=tmp_path / "pi"
    )


async def _aborted_while_running(
    tool: Any,
    script: str,
    agents: _Agents,
    signal: Any,
    *,
    running: int = 1,
    **params: Any,
) -> Any:
    """Start *script*, abort once *running* agents are in flight, return the tool's answer."""
    call = asyncio.create_task(tool.execute("tc", WorkflowParams(script=script, **params), signal))
    await _until(lambda: len(agents.started) >= running)
    signal.abort()
    return await asyncio.wait_for(call, timeout=5)


def _text(result: Any) -> str:
    return result.content[0]["text"]


class TestAnAbortStopsTheRun:
    async def test_the_tool_answers_cancelled_and_no_later_agent_starts(self, tmp_path: Path):
        agents = _Agents()

        result = await _aborted_while_running(
            _tool(agents, tmp_path), SERIAL, agents, _AbortSignal()
        )

        assert "cancelled" in _text(result).lower()
        assert re.search(r"run_id: [0-9a-f]{12}", _text(result))
        await asyncio.sleep(0.2)
        assert agents.started == ["first"]

    async def test_the_agent_that_was_running_is_cancelled_before_the_tool_answers(
        self, tmp_path: Path
    ):
        agents = _Agents(unwind_s=0.2)

        await _aborted_while_running(_tool(agents, tmp_path), SERIAL, agents, _AbortSignal())

        assert agents.cancelled == ["first"]

    @pytest.mark.parametrize("script", [FAN_OUT, PIPELINE], ids=["parallel", "pipeline"])
    async def test_every_agent_in_flight_is_cancelled_before_the_tool_answers(
        self, tmp_path: Path, script: str
    ):
        agents = _Agents(unwind_s=0.2)

        await _aborted_while_running(
            _tool(agents, tmp_path), script, agents, _AbortSignal(), running=3
        )

        assert len(agents.started) == 3
        assert sorted(agents.cancelled) == sorted(agents.started)
        await asyncio.sleep(0.2)
        assert len(agents.started) == 3  # nothing was started by the unwinding

    async def test_a_signal_that_is_already_aborted_starts_nothing(self, tmp_path: Path):
        agents = _Agents()
        signal = _AbortSignal()
        signal.abort()

        result = await asyncio.wait_for(
            _tool(agents, tmp_path).execute("tc", WorkflowParams(script=SERIAL), signal),
            timeout=5,
        )

        assert "cancelled" in _text(result).lower()
        assert agents.started == []

    async def test_a_signal_that_is_only_a_flag_stops_the_run_too(self, tmp_path: Path):
        agents = _Agents()

        result = await _aborted_while_running(_tool(agents, tmp_path), SERIAL, agents, _FlagOnly())

        assert "cancelled" in _text(result).lower()
        assert agents.cancelled == ["first"]

    async def test_a_signal_that_is_never_aborted_does_not_disturb_the_run(self, tmp_path: Path):
        agents = _Agents(quick={"first", "second"})

        result = await _tool(agents, tmp_path).execute(
            "tc", WorkflowParams(script=SERIAL), _AbortSignal()
        )

        assert "completed" in _text(result)
        assert agents.started == ["first", "second"]

    async def test_a_run_that_finishes_leaves_no_task_waiting_on_the_signal(self, tmp_path: Path):
        agents = _Agents(quick={"first", "second"})
        before = asyncio.all_tasks()

        await _tool(agents, tmp_path).execute("tc", WorkflowParams(script=SERIAL), _AbortSignal())
        await asyncio.sleep(0)

        assert asyncio.all_tasks() <= before

    async def test_a_failing_run_is_reported_as_failed_not_cancelled(self, tmp_path: Path):
        script = 'meta = {"name": "bad"}\nasync def main():\n    raise RuntimeError("bad script")\n'

        result = await _tool(_Agents(), tmp_path).execute(
            "tc", WorkflowParams(script=script), _AbortSignal()
        )

        assert "Workflow failed: bad script" in _text(result)

    async def test_the_cancellation_of_the_tool_call_itself_stops_the_run_too(self, tmp_path: Path):
        agents = _Agents(unwind_s=0.2)
        call = asyncio.create_task(
            _tool(agents, tmp_path).execute("tc", WorkflowParams(script=SERIAL), _AbortSignal())
        )
        await _until(lambda: agents.started)

        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        assert agents.cancelled == ["first"]


class TestWhatIsKept:
    async def test_agents_that_finished_are_journaled_and_replayed_by_a_resume(
        self, tmp_path: Path
    ):
        first = _Agents(quick={"first"})
        tool = _tool(first, tmp_path)

        result = await _aborted_while_running(tool, SERIAL, first, _AbortSignal(), running=2)

        run_id = re.search(r"run_id: ([0-9a-f]{12})", _text(result)).group(1)  # type: ignore[union-attr]
        assert "resume_from_run_id" in _text(result)
        second = _Agents(quick={"first", "second"})
        resumed = await _tool(second, tmp_path).execute(
            "tc", WorkflowParams(script=SERIAL, resume_from_run_id=run_id)
        )
        assert "completed" in _text(resumed)
        assert second.started == ["second"]  # "first" was answered by the journal

    async def test_a_signal_that_is_already_aborted_does_not_start_a_background_run(
        self, tmp_path: Path
    ):
        agents = _Agents(quick={"first", "second"})
        bridge = _Bridge()
        manager = WorkflowManager(bridge)  # type: ignore[arg-type]
        signal = _AbortSignal()
        signal.abort()

        result = await _tool(agents, tmp_path, manager).execute(
            "tc", WorkflowParams(script=SERIAL, background=True), signal
        )
        await asyncio.sleep(0.1)

        assert "cancelled" in _text(result).lower()
        assert manager.pending_count == 0
        assert agents.started == []
        assert bridge.delivered == []

    async def test_a_background_run_is_not_stopped_by_the_abort_of_the_turn_that_started_it(
        self, tmp_path: Path
    ):
        release = asyncio.Event()

        class Gated(SubagentExecutor):
            async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
                await release.wait()
                return AgentResult(text="finished")

        bridge = _Bridge()
        manager = WorkflowManager(bridge)  # type: ignore[arg-type]
        signal = _AbortSignal()
        script = (
            'meta = {"name": "bg"}\nasync def main():\n    await agent("go")\n    result("ok")\n'
        )

        started = await _tool(Gated(), tmp_path, manager).execute(
            "tc", WorkflowParams(script=script, background=True), signal
        )
        signal.abort()
        release.set()
        await _until(lambda: bridge.delivered)

        assert "running in the background" in _text(started)
        assert "completed" in bridge.delivered[0]


class TestARunThatWillNotStop:
    async def test_it_is_abandoned_after_a_grace_period_instead_of_hanging_the_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(workflow_tool, "_UNWIND_GRACE_S", 0.2)
        finished = asyncio.Event()

        class Stubborn(SubagentExecutor):
            def __init__(self) -> None:
                self.started: list[str] = []

            async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
                self.started.append(prompt)
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    await asyncio.sleep(1.5)  # carries on instead of stopping
                    finished.set()
                return AgentResult(text="finished anyway")

        agents = Stubborn()
        signal = _AbortSignal()
        one_agent = 'meta = {"name": "one"}\nasync def main():\n    await agent("only")\n'
        call = asyncio.create_task(
            _tool(agents, tmp_path).execute("tc", WorkflowParams(script=one_agent), signal)
        )
        await _until(lambda: agents.started)
        signal.abort()

        # ``wait``, not ``wait_for``: on a timeout that would wait for the stubborn task too.
        done, _ = await asyncio.wait({call}, timeout=1.0)

        assert done, "the abort hung on a run that would not stop"
        result = call.result()
        assert "cancelled" in _text(result).lower()
        assert not finished.is_set()  # the tool did not wait the agent out
        await asyncio.wait_for(finished.wait(), timeout=10)  # let the abandoned run end
        await asyncio.sleep(0.1)


class TestParallelDoesNotOrphanAgents:
    async def test_a_thunk_that_cannot_start_leaves_nothing_running(self, tmp_path: Path):
        agents = _Agents()
        script = (
            'meta = {"name": "broken"}\n'
            "async def main():\n"
            '    await parallel([lambda: agent("a"), lambda: 5])\n'
        )

        result = await _tool(agents, tmp_path).execute("tc", WorkflowParams(script=script))

        assert "Workflow failed" in _text(result)
        await asyncio.sleep(0.1)
        # "a" was scheduled before the second thunk turned out not to be startable; with
        # nobody left to wait for its answer it must not run (and spend tokens) anyway.
        assert agents.started == []
