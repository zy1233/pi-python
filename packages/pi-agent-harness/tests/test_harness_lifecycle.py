"""Session lifecycle: ``close()``, and what extensions start by themselves (audit P7-07).

Background work (a workflow) delivers its result by starting a turn through the bridge.
Three things were wrong:

* ``close()`` left the turn in flight running, and only ran the cleanup callbacks;
* a result delivered after ``close()`` started a fresh LLM turn on the dead session;
* a result entered the conversation as if the user had typed it, and ran as a slash command
  when it began with ``/``.

``trigger_message`` injects it as a typed (custom) message instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import ErrorEvent, Model, StartEvent
from pi_agent_harness import AgentHarness, AgentHarnessError, MemorySessionStorage, Session


def _user_texts(messages: list[Any]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        content = message.content
        if isinstance(content, str):
            texts.append(content)
        else:
            texts.append("".join(block.get("text", "") for block in content))
    return texts


def _aborted(model: Model) -> AssistantMessageEventStream:
    """What a StreamFn reports for an abort: a result, never a raise."""
    partial = _base_partial(model)
    partial.stopReason = "aborted"
    stream = AssistantMessageEventStream()
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(ErrorEvent(partial=partial.model_copy(deep=True), reason="aborted"))
    stream.set_final_message(partial)
    stream.end()
    return stream


class Llm:
    """A ``stream_fn`` that records what each call saw.

    With *hold_first* the first call waits until ``release`` is set or the turn is aborted
    (and then reports "aborted").
    """

    def __init__(self, *, hold_first: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._hold_first = hold_first

    async def __call__(self, model: Model, context: Any, options: Any = None) -> Any:
        self.calls.append(_user_texts(context.messages))
        self.entered.set()
        if self._hold_first and len(self.calls) == 1:
            signal = getattr(options, "signal", None)
            waiting = [asyncio.ensure_future(self.release.wait())]
            if signal is not None:
                waiting.append(asyncio.ensure_future(signal.wait_aborted()))
            _, pending = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if signal is not None and signal.aborted:
                return _aborted(model)
        return await mock_text_stream(model, context, options)


async def _harness(llm: Llm | None = None, **kwargs: Any) -> tuple[AgentHarness, Llm, Any]:
    """A loaded harness, its LLM stub, and the bridge its extensions get."""
    llm = llm or Llm()
    harness = AgentHarness(
        session=Session(await MemorySessionStorage.create(session_id="lifecycle")),
        model=Model(provider="mock", model_id="m1"),
        stream_fn=llm,
        **kwargs,
    )
    await harness.load_extensions()
    return harness, llm, harness._extension_bridge


async def _until(condition: Callable[[], Any], *, timeout: float = 5.0) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


class TestClose:
    async def test_it_stops_a_turn_in_flight_instead_of_leaving_it_running(self):
        harness, llm, _ = await _harness(Llm(hold_first=True))
        turn = asyncio.create_task(harness.prompt("work"))
        await asyncio.wait_for(llm.entered.wait(), 5)

        await asyncio.wait_for(harness.close(), 5)

        result = await asyncio.wait_for(turn, 5)
        assert result.stopReason == "aborted"

    async def test_it_runs_every_cleanup_callback_in_order_though_one_fails(self):
        harness, _, bridge = await _harness()
        seen: list[str] = []

        async def first() -> None:
            seen.append("first")
            raise RuntimeError("boom")

        def second() -> None:
            seen.append("second")

        bridge.register_cleanup(first)
        bridge.register_cleanup(second)

        await harness.close()

        assert seen == ["first", "second"]

    async def test_it_marks_the_harness_closed_and_a_second_close_does_nothing(self):
        harness, _, bridge = await _harness()
        calls: list[str] = []
        bridge.register_cleanup(lambda: calls.append("cleaned"))
        assert harness.closed is False

        await harness.close()
        await harness.close()

        assert harness.closed is True
        assert calls == ["cleaned"]

    async def test_it_stops_the_turns_the_harness_started_itself(self):
        before = asyncio.all_tasks()
        harness, llm, bridge = await _harness(Llm(hold_first=True))
        bridge.trigger_prompt("go")
        await asyncio.wait_for(llm.entered.wait(), 5)

        await asyncio.wait_for(harness.close(), 5)

        assert asyncio.all_tasks() <= before  # nothing is left running when close() returns

    async def test_it_does_not_wait_out_a_turn_that_ignores_the_abort(self, monkeypatch):
        from pi_agent_harness import agent_harness

        monkeypatch.setattr(agent_harness, "CLOSE_GRACE_S", 0.2)
        stuck = asyncio.Event()

        async def deaf(model: Model, context: Any, options: Any = None) -> Any:
            stuck.set()
            await asyncio.sleep(3600)  # never looks at the abort signal

        before = asyncio.all_tasks()
        harness, _, bridge = await _harness(deaf)  # type: ignore[arg-type]
        bridge.trigger_prompt("go")
        await asyncio.wait_for(stuck.wait(), 5)

        await asyncio.wait_for(harness.close(), 2)  # returns after the grace period

        assert asyncio.all_tasks() <= before  # the turn that would not stop was cancelled

    async def test_it_can_be_called_from_a_turn_the_harness_started_itself(self):
        holder: list[AgentHarness] = []

        def activate(pi: Any) -> None:
            pi.register_command("quit", description="x", handler=lambda args: holder[0].close())

        harness, _, bridge = await _harness(extensions=[activate])
        holder.append(harness)

        bridge.trigger_prompt("/quit")  # the command closes the harness from within the turn

        await _until(lambda: harness.closed)
        # Waiting on its own task would take the whole grace period.
        await _until(lambda: not harness._bg_tasks, timeout=1.0)

    async def test_it_stops_a_turn_that_was_still_being_set_up(self, monkeypatch):
        harness, _llm, _ = await _harness(Llm(hold_first=True))
        create = AgentHarness._create_turn_state
        setting_up = asyncio.Event()
        proceed = asyncio.Event()

        async def slow(self: Any) -> Any:
            setting_up.set()
            await proceed.wait()
            return await create(self)

        monkeypatch.setattr(AgentHarness, "_create_turn_state", slow)
        turn = asyncio.create_task(harness.prompt("work"))
        await asyncio.wait_for(setting_up.wait(), 5)

        await harness.close()  # there is no run to abort yet
        proceed.set()

        result = await asyncio.wait_for(turn, 5)
        assert result.stopReason == "aborted"


class TestAfterClose:
    async def test_a_closed_harness_refuses_a_prompt(self):
        harness, llm, _ = await _harness()
        await harness.close()

        with pytest.raises(AgentHarnessError, match="closed"):
            await harness.prompt("anyone there?")

        assert llm.calls == []

    async def test_a_prompt_triggered_before_close_but_not_yet_started_never_starts(self):
        harness, llm, bridge = await _harness()

        bridge.trigger_prompt("a result")  # scheduled: it has not had a chance to run
        await harness.close()
        await asyncio.sleep(0.1)

        assert llm.calls == []

    async def test_a_message_triggered_before_close_but_not_yet_started_never_starts(self):
        harness, llm, bridge = await _harness()

        bridge.trigger_message("workflow-result", "a result")
        await harness.close()
        await asyncio.sleep(0.1)

        assert llm.calls == []

    async def test_a_prompt_triggered_after_close_starts_no_turn(self):
        harness, llm, bridge = await _harness()
        await harness.close()

        bridge.trigger_prompt("a late result")
        await asyncio.sleep(0.1)

        assert llm.calls == []

    async def test_a_message_triggered_after_close_starts_no_turn(self):
        harness, llm, bridge = await _harness()
        await harness.close()

        bridge.trigger_message("workflow-result", "a late result")
        await asyncio.sleep(0.1)

        assert llm.calls == []

    async def test_a_message_triggered_after_close_is_not_queued_either(self):
        harness, _, bridge = await _harness()
        await harness.close()

        bridge.trigger_message("workflow-result", "a late result")

        assert harness.steer_queue == []

    async def test_nothing_is_queued_while_the_stopped_turn_is_still_winding_down(self):
        harness, llm, bridge = await _harness(Llm(hold_first=True))
        turn = asyncio.create_task(harness.prompt("work"))
        await asyncio.wait_for(llm.entered.wait(), 5)

        await harness.close()
        assert harness.phase == "turn"  # the turn has not seen the abort yet: a queue would take it

        bridge.trigger_prompt("a late result")
        bridge.trigger_message("workflow-result", "a late result")

        assert harness.steer_queue == []
        await asyncio.wait_for(turn, 5)


class TestTriggeredMessage:
    async def test_when_idle_it_starts_a_turn_that_opens_with_the_custom_message(self):
        harness, llm, bridge = await _harness()

        bridge.trigger_message("workflow-result", "the framed text", details={"runId": "r1"})
        await asyncio.wait_for(llm.entered.wait(), 5)
        await harness.wait_for_idle()

        assert llm.calls == [["the framed text"]]  # the model sees it as context, once
        context = await harness.session.build_context()
        first = context.messages[0]
        assert first["role"] == "custom"
        assert first["customType"] == "workflow-result"
        assert first["content"] == "the framed text"
        assert first["display"] is True
        assert first["details"] == {"runId": "r1"}
        assert context.messages[-1].role == "assistant"

    async def test_it_is_not_taken_for_a_slash_command(self):
        ran: list[str] = []

        def activate(pi: Any) -> None:
            pi.register_command("boom", description="x", handler=lambda args: ran.append(args))

        harness, llm, bridge = await _harness(extensions=[activate])

        bridge.trigger_message("workflow-result", "/boom now")
        await asyncio.wait_for(llm.entered.wait(), 5)
        await harness.wait_for_idle()

        assert ran == []
        assert llm.calls == [["/boom now"]]
        await harness.prompt("/boom now")  # what the user types is a command
        assert ran == ["now"]

    async def test_while_a_turn_runs_it_joins_that_turn(self):
        harness, llm, bridge = await _harness(Llm(hold_first=True))
        turn = asyncio.create_task(harness.prompt("first"))
        await asyncio.wait_for(llm.entered.wait(), 5)

        bridge.trigger_message("workflow-result", "a result")
        assert [m.role for m in harness.steer_queue] == ["custom"]
        llm.release.set()
        await asyncio.wait_for(turn, 5)

        assert llm.calls == [["first"], ["first", "a result"]]

    @pytest.mark.parametrize("trigger", ["prompt", "message"])
    async def test_a_turn_that_began_after_it_was_scheduled_is_joined_not_dropped(
        self, trigger: str
    ):
        harness, llm, bridge = await _harness(Llm(hold_first=True))
        # The user's prompt is scheduled first, so its turn begins before the triggered one.
        turn = asyncio.create_task(harness.prompt("first"))
        if trigger == "prompt":
            bridge.trigger_prompt("a result")
        else:
            bridge.trigger_message("workflow-result", "a result")
        assert harness.phase == "idle"  # what the bridge saw

        await asyncio.wait_for(llm.entered.wait(), 5)
        llm.release.set()
        await asyncio.wait_for(turn, 5)

        assert llm.calls == [["first"], ["first", "a result"]]

    async def test_details_are_optional(self):
        harness, llm, bridge = await _harness()

        bridge.trigger_message("note", "plain")
        await asyncio.wait_for(llm.entered.wait(), 5)
        await harness.wait_for_idle()

        first = (await harness.session.build_context()).messages[0]
        assert first["customType"] == "note"
        assert first.get("details") is None


class TestATriggeredTurnThatFails:
    @pytest.mark.parametrize("trigger", ["prompt", "message"])
    async def test_is_logged_and_leaves_the_harness_usable(
        self, trigger: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        harness, llm, bridge = await _harness()

        async def cannot_start(self: Any) -> Any:
            raise RuntimeError("cannot start")

        monkeypatch.setattr(AgentHarness, "_create_turn_state", cannot_start)
        with caplog.at_level(logging.WARNING, logger="pi_agent_harness.agent_harness"):
            if trigger == "prompt":
                bridge.trigger_prompt("go")
            else:
                bridge.trigger_message("workflow-result", "go")
            await _until(lambda: any(_is_about_the_failure(r) for r in caplog.records))

        assert harness.phase == "idle"
        monkeypatch.undo()
        await harness.prompt("still works")
        assert llm.calls[-1][-1] == "still works"


def _is_about_the_failure(record: logging.LogRecord) -> bool:
    """A warning the harness itself logged, with the failure (and its cause) attached."""
    if record.name != "pi_agent_harness.agent_harness" or record.levelno < logging.WARNING:
        return False
    if not record.exc_info:
        return False
    return "cannot start" in logging.Formatter().formatException(record.exc_info)
