"""The context use that the agent tells a client: the ACP ``usage_update``.

Record: ``docs/specs/2026-11-13-agent-context-usage.md``. The rules under test:

* A model with no ``context_window`` in ``agent.toml`` gets no update (the 128,000 of the core is
  a placeholder, and it is never sent).
* ``used`` is the number that the provider gave, plus an estimate for the tool results of the turn.
  A provider that gives none, and a failed turn, give no update.
* After a compaction the count is an estimate of what remains. After ``session/load`` and
  ``session/resume`` the count is read from the saved history, but only a number that is still
  true: a number from before a compaction is not.
* A window that is declared reaches compaction, and the limits of compaction fit small windows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from acp import text_block
from acp.schema import UsageUpdate
from pydantic import BaseModel

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import MAX_CONTEXT_WINDOW, CliConfig, ModelChoice, load_config
from pi_agent_cli.context_usage import (
    estimated_context_tokens,
    stored_context_tokens,
    turn_context_tokens,
    usage_update,
)
from pi_agent_cli.extension_trust import TRUST_ENV
from pi_agent_cli.factory import auto_compact_enabled, compaction_settings, model_for_choice
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ExtensionLoader
from pi_agent_core.messages import AssistantMessage, ToolResultMessage, Usage, UserMessage
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, ErrorEvent, Model, StartEvent, TextDeltaEvent
from pi_agent_harness.compaction import estimate_message_tokens
from pi_agent_harness.compaction.compaction import COMPACTION_SYSTEM_PROMPT
from pi_agent_harness.types import CompactionEntry, MessageEntry


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    # What is installed or set on this machine must not add a tool or a notice of its own.
    monkeypatch.delenv(TRUST_ENV, raising=False)
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])


# --- The pure part -----------------------------------------------------------------------


def _assistant(total: int, *, stop: str = "stop", text: str = "answer") -> AssistantMessage:
    return AssistantMessage(
        content=[{"type": "text", "text": text}],
        usage=Usage(input=total, totalTokens=total),
        stopReason=stop,  # type: ignore[arg-type]
        timestamp=1,
    )


def _tool_result(text: str) -> ToolResultMessage:
    return ToolResultMessage(
        toolCallId="call_1",
        toolName="read",
        content=[{"type": "text", "text": text}],
        timestamp=1,
    )


def test_the_update_has_the_two_numbers_and_no_cost():
    update = usage_update(1_200, 200_000)

    assert isinstance(update, UsageUpdate)
    assert update.model_dump(by_alias=True, exclude_none=True) == {
        "sessionUpdate": "usage_update",
        "used": 1_200,
        "size": 200_000,
    }


def test_the_count_of_a_turn_is_the_number_of_the_provider():
    assert turn_context_tokens(_assistant(12_000)) == 12_000


def test_the_tool_results_of_a_turn_are_added_as_an_estimate():
    result = _tool_result("x" * 400)  # 4 characters for 1 token: 100 tokens

    assert estimate_message_tokens(result) == 100
    assert turn_context_tokens(_assistant(1_000, stop="toolUse"), [result]) == 1_100


@pytest.mark.parametrize(
    "message",
    [
        _assistant(0),  # a gateway that sends no usage
        _assistant(5_000, stop="error"),  # a failed turn
        _assistant(5_000, stop="aborted"),  # a cancelled turn
        UserMessage(content="hello", timestamp=1),
        None,
    ],
    ids=["no-usage", "error", "aborted", "not-an-assistant-message", "no-message"],
)
def test_a_turn_without_a_trusted_number_has_no_count(message):
    assert turn_context_tokens(message, [_tool_result("x" * 400)]) is None


def test_the_estimate_after_a_compaction_ignores_the_usage_of_the_messages():
    messages = [
        _assistant(90_000, text="x" * 40),  # carries the usage of a context that is gone
        _tool_result("y" * 80),
    ]

    assert estimated_context_tokens(messages) == 10 + 20
    assert estimated_context_tokens([]) == 0


_STAMP = "2026-11-16T00:00:00Z"


def _entry(entry_id: str, parent: str | None, message: BaseModel) -> MessageEntry:
    return MessageEntry(
        id=entry_id, parentId=parent, timestamp=_STAMP, message=message.model_dump(mode="json")
    )


def _user(text: str = "hello") -> UserMessage:
    return UserMessage(content=text, timestamp=1)


def test_the_count_of_a_saved_session_is_the_last_number_plus_what_follows():
    entries = [
        _entry("e1", None, _user()),
        _entry("e2", "e1", _assistant(10_000)),
        _entry("e3", "e2", _user("y" * 40)),  # 10 tokens, and the provider has not seen them
    ]

    assert stored_context_tokens(entries) == 10_010


@pytest.mark.parametrize(
    "assistant",
    [_assistant(0), _assistant(5_000, stop="error")],
    ids=["no-usage", "error"],
)
def test_a_saved_session_with_no_trusted_number_has_no_count(assistant):
    entries = [_entry("e1", None, _user()), _entry("e2", "e1", assistant)]

    assert stored_context_tokens(entries) is None
    assert stored_context_tokens([]) is None


def _compaction(entry_id: str, parent: str, first_kept: str) -> CompactionEntry:
    return CompactionEntry(
        id=entry_id,
        parentId=parent,
        timestamp=_STAMP,
        summary="SUMMARY",
        firstKeptEntryId=first_kept,
        tokensBefore=50_000,
    )


def test_a_number_from_before_a_compaction_is_not_true_any_more():
    """The kept assistant message still says 50,000. The context that it counted is gone."""
    entries = [
        _entry("e1", None, _user()),
        _entry("e2", "e1", _assistant(50_000)),
        _entry("e3", "e2", _user()),
        _entry("e4", "e3", _assistant(50_000)),
        _compaction("c1", "e4", first_kept="e3"),
        _entry("e5", "c1", _user()),
    ]

    assert stored_context_tokens(entries) is None


def test_a_number_from_after_a_compaction_is_true():
    entries = [
        _entry("e1", None, _user()),
        _entry("e2", "e1", _assistant(50_000)),
        _entry("e3", "e2", _user()),
        _entry("e4", "e3", _assistant(50_000)),
        _compaction("c1", "e4", first_kept="e3"),
        _entry("e5", "c1", _user()),
        _entry("e6", "e5", _assistant(7_000)),
    ]

    assert stored_context_tokens(entries) == 7_000


# --- Configuration -----------------------------------------------------------------------


def _load(tmp_path: Path, text: str) -> CliConfig:
    (tmp_path / "agent.toml").write_text(text, encoding="utf-8")
    return load_config(tmp_path)


def test_the_window_of_a_model_is_read_from_the_model_table(tmp_path):
    config = _load(tmp_path, '[model]\nprovider = "openai"\nid = "m1"\ncontext_window = 200_000\n')

    assert config.context_window == 200_000
    assert config.default_choice().context_window == 200_000
    assert config.auto.notes == ()


def test_a_model_with_no_window_has_none():
    assert CliConfig().context_window is None
    assert CliConfig().default_choice().context_window is None


def test_a_listed_model_never_takes_the_window_of_the_default(tmp_path):
    """The window belongs to one model. Another model of the same provider has its own."""
    config = _load(
        tmp_path,
        """
[model]
provider = "openai"
id = "big"
context_window = 1000000

[[models]]
id = "same-provider-no-window"

[[models]]
id = "same-provider-own-window"
context_window = 64000

[[models]]
provider = "anthropic"
id = "other-provider"
context_window = 200000
""",
    )

    windows = {choice.id: choice.context_window for choice in config.model_choices()}
    assert windows == {
        "big": 1_000_000,
        "same-provider-no-window": None,
        "same-provider-own-window": 64_000,
        "other-provider": 200_000,
    }


@pytest.mark.parametrize("raw", ["128_000", "1.28e5"], ids=["integer", "float-without-fraction"])
def test_a_whole_number_is_accepted(tmp_path, raw):
    config = _load(tmp_path, f'[model]\nprovider = "openai"\nid = "m1"\ncontext_window = {raw}\n')

    assert config.context_window == 128_000
    assert config.auto.notes == ()


def test_a_key_at_the_top_of_the_file_is_read_like_the_other_model_keys(tmp_path):
    """The flat form is an old way to write ``[model]`` keys. It must stay in the same place."""
    config = _load(tmp_path, 'context_window = 64000\n[model]\nprovider = "openai"\nid = "m1"\n')

    assert config.context_window == 64_000
    assert config.default_choice().context_window == 64_000
    assert config.auto.notes == ()


def test_the_model_table_wins_over_a_key_at_the_top_of_the_file(tmp_path):
    config = _load(
        tmp_path,
        """
context_window = 64000

[model]
provider = "openai"
id = "m1"
context_window = 200000
""",
    )

    assert config.context_window == 200_000


@pytest.mark.parametrize(
    "raw",
    ['"big"', "0", "-5", "true", "1.5", str(MAX_CONTEXT_WINDOW + 1)],
    ids=["text", "zero", "negative", "boolean", "fraction", "too-large"],
)
def test_a_window_that_cannot_be_used_is_dropped_and_the_user_is_told(tmp_path, raw):
    config = _load(tmp_path, f'[model]\nprovider = "openai"\nid = "m1"\ncontext_window = {raw}\n')

    assert config.context_window is None
    (note,) = config.auto.notes
    assert "context_window of [model]" in note
    assert "sends no context size" in note


def test_the_note_for_a_listed_model_names_the_model(tmp_path):
    config = _load(tmp_path, '[[models]]\nid = "m2"\ncontext_window = "wide"\n')

    (choice,) = (c for c in config.model_choices() if c.id == "m2")
    assert choice.context_window is None
    (note,) = config.auto.notes
    assert '"m2"' in note


def test_the_window_reaches_the_model_only_when_it_is_declared():
    declared = model_for_choice(ModelChoice(id="m1", provider="mock", context_window=64_000))
    undeclared = model_for_choice(ModelChoice(id="m1", provider="mock"))

    assert declared.context_window == 64_000
    assert undeclared.context_window == Model(provider="mock", model_id="m1").context_window


def test_compaction_keeps_its_defaults_when_there_is_no_window_or_the_window_is_large():
    defaults = compaction_settings(ModelChoice(id="m"), auto_compact=False)

    assert (defaults.reserve_tokens, defaults.keep_recent_tokens) == (16_384, 20_000)
    assert defaults.auto_compact is False
    for window in (65_536, 200_000, 1_000_000):
        choice = ModelChoice(id="m", context_window=window)
        settings = compaction_settings(choice, auto_compact=True)
        assert (settings.reserve_tokens, settings.keep_recent_tokens) == (16_384, 20_000)
        assert settings.auto_compact is True


@pytest.mark.parametrize(
    ("window", "reserve", "keep"),
    [(32_000, 8_000, 10_666), (8_192, 2_048, 2_730), (1_000, 250, 333)],
)
def test_compaction_limits_fit_a_small_window(window, reserve, keep):
    settings = compaction_settings(ModelChoice(id="m", context_window=window), auto_compact=True)

    assert (settings.reserve_tokens, settings.keep_recent_tokens) == (reserve, keep)
    # Room is left between the point where compaction starts and the part that is kept.
    assert settings.keep_recent_tokens < window - settings.reserve_tokens


def test_compaction_limits_never_reach_zero():
    settings = compaction_settings(ModelChoice(id="m", context_window=1), auto_compact=True)

    assert (settings.reserve_tokens, settings.keep_recent_tokens) == (1, 1)


# --- The agent ---------------------------------------------------------------------------


@dataclass
class Step:
    """What the model answers to one request."""

    total: int = 0  # ``usage.totalTokens``; 0 is a provider that gives no usage
    text: str = "answer"
    tool: tuple[str, dict[str, Any]] | None = None  # a tool call, instead of text
    fail: bool = False


class Script:
    """A model that answers request N as ``steps[N]`` says.

    A request to summarize (compaction) is answered with a summary, and it takes no step.
    ``calls`` counts the requests that took a step.
    """

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.calls = 0

    async def __call__(
        self, model: Model, context: Any, options: Any = None
    ) -> AssistantMessageEventStream:
        if context.system_prompt == COMPACTION_SYSTEM_PROMPT:
            step = Step(text="SUMMARY")
        else:
            step = self._steps[self.calls]
            self.calls += 1
        if step.tool is not None:
            name, arguments = step.tool
            call = {"type": "toolCall", "id": f"call_{self.calls}", "name": name}
            content: list[Any] = [{**call, "arguments": arguments}]
        else:
            content = [{"type": "text", "text": step.text}]
        partial = _base_partial(model, content)
        partial.usage = Usage(input=step.total, totalTokens=step.total)
        partial.stopReason = "toolUse" if step.tool else "error" if step.fail else "stop"
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        if step.tool is None and not step.fail:
            stream.push(TextDeltaEvent(partial=partial.model_copy(deep=True), delta=step.text))
        if step.fail:
            partial.errorMessage = "scripted error"
            stream.push(
                ErrorEvent(
                    partial=partial.model_copy(deep=True),
                    reason="error",
                    error_message="scripted error",
                )
            )
        else:
            reason: Any = partial.stopReason
            stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason=reason))
        stream.set_final_message(partial)
        stream.end()
        return stream


class Recorder:
    """A client that records what the agent sends and is never asked anything."""

    def __init__(self, *, refuse_usage: bool = False) -> None:
        self.updates: list[tuple[str, Any]] = []
        self.refuse_usage = refuse_usage

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if self.refuse_usage and isinstance(update, UsageUpdate):
            raise ConnectionError("the client went away")
        self.updates.append((session_id, update))

    def usage(self) -> list[UsageUpdate]:
        return [update for _, update in self.updates if isinstance(update, UsageUpdate)]

    def kinds(self) -> list[str]:
        return [getattr(update, "session_update", "?") for _, update in self.updates]


def make_config(window: int | None = 200_000, **extra: Any) -> CliConfig:
    # The agent compacts by itself only when a test asks for it. A window of 40 tokens would
    # otherwise compact after every answer, and the tests that count after a compaction that
    # they start themselves would lose their steps. ``auto_compact=None`` is the rule of the file.
    extra.setdefault("auto_compact", False)
    return CliConfig(
        permission="ask", provider="mock", model_id="mock", context_window=window, **extra
    )


def make_agent(
    home: Path, stream_fn: Any, config: CliConfig | None = None, **client: Any
) -> tuple[PiAcpAgent, Recorder]:
    agent = PiAcpAgent(stream_fn=stream_fn, home=home, config=config or make_config())
    recorder = Recorder(**client)
    agent.on_connect(recorder)  # type: ignore[arg-type]
    return agent, recorder


async def settle(agent: PiAcpAgent) -> None:
    # The setup of a session runs after the response has flushed.
    await asyncio.gather(*agent._background_tasks)


async def say(agent: PiAcpAgent, session_id: str, text: str = "hello") -> None:
    await agent.prompt(session_id=session_id, prompt=[text_block(text)])


@pytest.mark.asyncio
async def test_one_turn_sends_one_update_with_the_count_and_the_window(tmp_path):
    agent, client = make_agent(tmp_path, Script(Step(total=12_000)))
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await settle(agent)
    assert client.usage() == []  # a new session has nothing to count

    await say(agent, session_id)

    (update,) = client.usage()
    assert (update.used, update.size) == (12_000, 200_000)
    assert update.cost is None
    assert client.updates[-1] == (session_id, update)  # it follows the answer
    assert "agent_message_chunk" in client.kinds()


@pytest.mark.asyncio
async def test_a_model_with_no_window_gets_no_update_even_when_the_provider_counts(tmp_path):
    script = Script(Step(total=12_000))
    agent, client = make_agent(tmp_path, script, make_config(window=None))
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await say(agent, session_id)

    assert script.calls == 1  # the turn ran
    assert "agent_message_chunk" in client.kinds()
    assert client.usage() == []


@pytest.mark.asyncio
async def test_a_provider_that_gives_no_usage_gets_no_update(tmp_path):
    agent, client = make_agent(tmp_path, mock_text_stream)  # usage is 0
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await say(agent, session_id)

    assert client.usage() == []


@pytest.mark.asyncio
async def test_a_failed_turn_gets_no_update(tmp_path):
    agent, client = make_agent(tmp_path, Script(Step(total=9_000, fail=True)))
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await say(agent, session_id)

    assert client.usage() == []


@pytest.mark.asyncio
async def test_each_turn_of_a_tool_loop_reports_the_count_of_its_end(tmp_path):
    (tmp_path / "notes.txt").write_text("x" * 400, encoding="utf-8")
    script = Script(
        Step(total=1_000, tool=("read", {"path": "notes.txt"})),
        Step(total=1_500, text="done"),
    )
    agent, client = make_agent(tmp_path, script)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await say(agent, session_id)

    first, second = client.usage()
    # The first count has the tool result: the model has not seen it yet, so it is an estimate.
    assert first.used > 1_000 + 100 - 1
    assert second.used == 1_500
    assert script.calls == 2


@pytest.mark.asyncio
async def test_a_client_that_refuses_the_update_does_not_break_the_turn(tmp_path, caplog):
    """The harness turns the error of a subscriber into an error of the turn."""
    agent, client = make_agent(tmp_path, Script(Step(total=12_000)), refuse_usage=True)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id

    response = await agent.prompt(session_id=session_id, prompt=[text_block("hello")])

    assert response.stop_reason == "end_turn"
    assert "Could not send the context use" in caplog.text
    assert client.usage() == []


@pytest.mark.asyncio
async def test_a_change_of_model_sends_the_count_again_with_the_new_window(tmp_path):
    config = make_config(
        window=200_000,
        models=(
            ModelChoice(id="small", provider="mock", context_window=32_000),
            ModelChoice(id="unknown", provider="mock"),
        ),
    )
    agent, client = make_agent(tmp_path, Script(Step(total=12_000)), config)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await say(agent, session_id)
    harness = agent._harnesses[session_id]
    assert [(u.used, u.size) for u in client.usage()] == [(12_000, 200_000)]
    assert harness.compaction.reserve_tokens == 16_384

    await agent.set_config_option(config_id="model", session_id=session_id, value="small")
    assert [(u.used, u.size) for u in client.usage()][-1] == (12_000, 32_000)
    assert (harness.compaction.reserve_tokens, harness.compaction.keep_recent_tokens) == (
        8_000,
        10_666,
    )

    sent = len(client.usage())
    await agent.set_config_option(config_id="model", session_id=session_id, value="unknown")
    assert len(client.usage()) == sent  # no window: nothing is sent, and nothing is guessed
    assert harness.compaction.reserve_tokens == 16_384

    await agent.set_config_option(config_id="model", session_id=session_id, value="mock")
    assert [(u.used, u.size) for u in client.usage()][-1] == (12_000, 200_000)


@pytest.mark.asyncio
async def test_closing_a_session_forgets_its_count(tmp_path):
    agent, _ = make_agent(tmp_path, Script(Step(total=12_000)))
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await say(agent, session_id)
    assert agent._context_used == {session_id: 12_000}

    await agent.close_session(session_id)

    assert agent._context_used == {}


@pytest.mark.asyncio
async def test_a_new_session_reads_no_history_and_logs_nothing(tmp_path, caplog):
    agent, client = make_agent(tmp_path, Script())
    await agent.new_session(cwd=str(tmp_path))
    await settle(agent)

    assert client.usage() == []
    assert "context use" not in caplog.text


async def _saved_session(home: Path, *steps: Step, config: CliConfig | None = None) -> str:
    """Run one prompt per step in a session of its own agent, so that the session has a file."""
    agent, _ = make_agent(home, Script(*steps), config)
    session_id = (await agent.new_session(cwd=str(home))).session_id
    for index, _ in enumerate(steps):
        await say(agent, session_id, f"request {index}")
    return session_id


@pytest.mark.asyncio
async def test_load_sends_the_count_of_the_saved_session_after_the_replay(tmp_path):
    session_id = await _saved_session(tmp_path, Step(total=12_000))

    agent, client = make_agent(tmp_path, Script())
    await agent.load_session(cwd=str(tmp_path), session_id=session_id)
    await settle(agent)

    (update,) = client.usage()
    assert (update.used, update.size) == (12_000, 200_000)
    assert "agent_message_chunk" in client.kinds()  # the replay
    assert client.kinds().index("usage_update") > client.kinds().index("agent_message_chunk")
    assert [sid for sid, u in client.updates if u is update] == [session_id]


@pytest.mark.asyncio
async def test_resume_sends_the_count_without_a_replay(tmp_path):
    session_id = await _saved_session(tmp_path, Step(total=12_000))

    agent, client = make_agent(tmp_path, Script())
    await agent.resume_session(cwd=str(tmp_path), session_id=session_id)
    await settle(agent)

    (update,) = client.usage()
    assert (update.used, update.size) == (12_000, 200_000)
    assert "agent_message_chunk" not in client.kinds()


@pytest.mark.asyncio
async def test_load_sends_nothing_for_a_model_with_no_window(tmp_path):
    session_id = await _saved_session(tmp_path, Step(total=12_000), config=make_config(None))

    agent, client = make_agent(tmp_path, Script(), make_config(None))
    await agent.load_session(cwd=str(tmp_path), session_id=session_id)
    await settle(agent)

    assert client.usage() == []


@pytest.mark.asyncio
async def test_load_sends_nothing_for_a_session_that_gave_no_number(tmp_path):
    session_id = await _saved_session(tmp_path, Step(total=0))

    agent, client = make_agent(tmp_path, Script())
    await agent.load_session(cwd=str(tmp_path), session_id=session_id)
    await settle(agent)

    assert client.usage() == []


async def _compacted_session(home: Path) -> tuple[str, Recorder]:
    """Three turns, then a compaction. Returns the id and the client that was told."""
    config = make_config(window=40)  # a window so small that three short turns need a summary
    agent, client = make_agent(
        home, Script(Step(total=1_000), Step(total=2_000), Step(total=3_000)), config
    )
    session_id = (await agent.new_session(cwd=str(home))).session_id
    for index in range(3):
        await say(agent, session_id, f"request number {index}")
    await agent._harnesses[session_id].compact()
    return session_id, client


@pytest.mark.asyncio
async def test_a_compaction_sends_an_estimate_of_what_remains(tmp_path):
    session_id, client = await _compacted_session(tmp_path)

    told = client.usage()
    assert [update.used for update in told[:3]] == [1_000, 2_000, 3_000]
    assert len(told) == 4
    after = told[3]
    assert after.size == 40
    assert 0 < after.used < 3_000  # the count fell, and it is an estimate: no usage of a provider
    assert {sid for sid, update in client.updates if isinstance(update, UsageUpdate)} == {
        session_id
    }


@pytest.mark.asyncio
async def test_load_after_a_compaction_does_not_send_the_number_from_before_it(tmp_path):
    session_id, _ = await _compacted_session(tmp_path)  # the session is saved: it has a file

    agent, client = make_agent(tmp_path, Script(), make_config(window=40))
    await agent.load_session(cwd=str(tmp_path), session_id=session_id)
    await settle(agent)

    assert client.usage() == []  # the kept messages still carry 3,000; the context does not


@pytest.mark.asyncio
async def test_a_compaction_with_no_count_on_the_client_sends_nothing(tmp_path):
    """No number was sent before, so there is no old number to correct."""
    config = make_config(window=40)
    agent, client = make_agent(
        tmp_path, Script(Step(total=0), Step(total=0), Step(total=0)), config
    )
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    for index in range(3):
        await say(agent, session_id, f"request number {index}")
    await agent._harnesses[session_id].compact()

    assert any(isinstance(entry, CompactionEntry) for entry in await _entries(agent, session_id))
    assert client.usage() == []


async def _entries(agent: PiAcpAgent, session_id: str) -> list[Any]:
    return await agent._harnesses[session_id].session.get_entries()


@pytest.mark.asyncio
async def test_compaction_uses_the_window_that_the_user_declared(tmp_path):
    """The same context compacts for a model with a small declared window, and not for one with
    no window (compaction then uses the placeholder of 128,000 tokens)."""

    async def run(window: int | None) -> bool:
        home = tmp_path / ("declared" if window else "undeclared")
        home.mkdir()
        # The rule of the file (``auto_compact=None``), and 30 turns or more: both say "on".
        config = make_config(window=window, max_turns=30, auto_compact=None)
        script = Script(*(Step(total=900) for _ in range(3)))
        agent, _ = make_agent(home, script, config)
        session_id = (await agent.new_session(cwd=str(home))).session_id
        for index in range(3):
            await say(agent, session_id, f"request {index} " + "a" * 4_000)
        return any(isinstance(e, CompactionEntry) for e in await _entries(agent, session_id))

    assert await run(window=1_000) is True  # 900 tokens is above 1,000 - 250
    assert await run(window=None) is False  # 900 tokens is far below 128,000 - 16,384


# --- Compaction: when the agent does it by itself -----------------------------------------


@pytest.mark.parametrize(
    ("auto_compact", "max_turns", "window", "expected"),
    [
        (None, None, None, False),  # nothing says so, and the window is a guess
        (None, 29, None, False),
        (None, 30, None, True),  # the rule from before the key existed
        (None, 200, None, True),
        (None, None, 1_000_000, True),  # a declared window
        (None, 5, 1_000_000, True),
        (False, 200, 1_000_000, False),  # the key wins over both rules
        (False, None, None, False),
        (True, None, None, True),
        (True, None, 1_000_000, True),
    ],
)
def test_the_rule_for_compaction_by_the_agent(auto_compact, max_turns, window, expected):
    config = CliConfig(provider="mock", auto_compact=auto_compact, max_turns=max_turns)
    choice = ModelChoice(id="m", context_window=window)

    assert auto_compact_enabled(config, choice) is expected


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("false", False)])
def test_auto_compact_is_read_from_the_file(tmp_path, raw, expected):
    config = _load(tmp_path, f"auto_compact = {raw}\n")

    assert config.auto_compact is expected
    assert config.auto.notes == ()


def test_a_file_with_no_auto_compact_leaves_it_not_set(tmp_path):
    config = _load(tmp_path, "max_turns = 200\n")

    assert config.auto_compact is None
    assert config.auto.notes == ()


@pytest.mark.parametrize(
    "raw",
    ['"yes"', '"true"', "1", "0", "[true]"],
    ids=["text", "text-true", "one", "zero", "list"],
)
def test_an_auto_compact_that_is_not_a_boolean_is_ignored_and_the_user_is_told(tmp_path, raw):
    config = _load(tmp_path, f"auto_compact = {raw}\n")

    assert config.auto_compact is None
    (note,) = config.auto.notes
    assert note == "auto_compact in agent.toml must be true or false, and it is ignored."


async def _compacts(home: Path, config: CliConfig, *, total: int, size: int = 4_000) -> bool:
    """Whether the agent compacted by itself, after three answers that each count ``total``.

    ``size`` is the length of each request. A window of the placeholder size keeps the last
    20,000 tokens, so there is nothing to compact until the requests are longer than that.
    """
    home.mkdir(parents=True, exist_ok=True)
    agent, _ = make_agent(home, Script(*(Step(total=total) for _ in range(3))), config)
    session_id = (await agent.new_session(cwd=str(home))).session_id
    for index in range(3):
        await say(agent, session_id, f"request {index} " + "a" * size)
    return any(isinstance(e, CompactionEntry) for e in await _entries(agent, session_id))


@pytest.mark.asyncio
async def test_a_declared_window_turns_compaction_on_with_no_turn_limit(tmp_path):
    declared = make_config(window=1_000, auto_compact=None)
    undeclared = make_config(window=None, auto_compact=None)

    assert await _compacts(tmp_path / "declared", declared, total=900) is True
    # No window and no turn limit: nothing is guessed, even above the placeholder of 128,000.
    # The requests are long enough that a compaction would find something to compact.
    guessed = await _compacts(tmp_path / "undeclared", undeclared, total=120_000, size=100_000)
    assert guessed is False


@pytest.mark.asyncio
async def test_auto_compact_false_turns_compaction_off_for_a_long_run(tmp_path):
    config = make_config(window=1_000, max_turns=200, auto_compact=False)

    assert await _compacts(tmp_path, config, total=900) is False


@pytest.mark.asyncio
async def test_auto_compact_true_turns_it_on_for_a_model_with_no_window(tmp_path):
    """The placeholder of 128,000 tokens applies then, which is why `agent.example.toml` says to
    set the real window."""
    config = make_config(window=None, auto_compact=True)

    over = await _compacts(tmp_path / "over", config, total=120_000, size=100_000)  # > 111,616
    under = await _compacts(tmp_path / "under", config, total=100_000, size=100_000)

    assert (over, under) == (True, False)


@pytest.mark.asyncio
async def test_a_change_of_model_decides_again_whether_the_agent_compacts(tmp_path):
    config = make_config(
        window=None,
        auto_compact=None,
        models=(
            ModelChoice(id="small", provider="mock", context_window=32_000),
            ModelChoice(id="unknown", provider="mock"),
        ),
    )
    agent, _ = make_agent(tmp_path, Script(), config)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await settle(agent)
    harness = agent._harnesses[session_id]
    assert harness.compaction.auto_compact is False  # the default model has no window

    await agent.set_config_option(config_id="model", session_id=session_id, value="small")
    assert harness.compaction.auto_compact is True

    await agent.set_config_option(config_id="model", session_id=session_id, value="unknown")
    assert harness.compaction.auto_compact is False


@pytest.mark.asyncio
async def test_a_change_of_model_keeps_what_the_key_says(tmp_path):
    config = make_config(
        window=None,
        auto_compact=False,
        models=(ModelChoice(id="small", provider="mock", context_window=32_000),),
    )
    agent, _ = make_agent(tmp_path, Script(), config)
    session_id = (await agent.new_session(cwd=str(tmp_path))).session_id
    await settle(agent)
    harness = agent._harnesses[session_id]

    await agent.set_config_option(config_id="model", session_id=session_id, value="small")

    assert harness.compaction.auto_compact is False
