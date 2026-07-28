"""H3 tests for compaction and tree navigation."""

from __future__ import annotations

import pytest

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import AssistantMessage, ToolResultMessage, UserMessage
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, Model, StartEvent, StreamOptions
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session
from pi_agent_harness.compaction import (
    CompactionSettings,
    collect_entries_for_branch_summary,
    estimate_context_tokens,
    prepare_compaction,
)
from pi_agent_harness.types import BranchSummaryEntry, CompactionEntry


def _model(context_window: int = 1_000) -> Model:
    return Model(provider="mock", model_id="m1", context_window=context_window)


async def _memory_session(session_id: str = "h3") -> Session:
    return Session(await MemorySessionStorage.create(session_id=session_id))


@pytest.mark.asyncio
async def test_prepare_compaction_keeps_recent_context_and_skips_tool_result_cutpoint():
    session = await _memory_session()
    await session.append_message(UserMessage(content="old " * 80, timestamp=1))
    await session.append_message(
        AssistantMessage(
            content=[
                {
                    "type": "toolCall",
                    "id": "call_1",
                    "name": "read",
                    "arguments": {"path": "old.py"},
                }
            ],
            stopReason="toolUse",
            timestamp=2,
        )
    )
    await session.append_message(
        ToolResultMessage(
            toolCallId="call_1",
            toolName="read",
            content=[{"type": "text", "text": "tool output " * 80}],
            timestamp=3,
        )
    )
    kept_id = await session.append_message(UserMessage(content="recent request", timestamp=4))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "recent answer"}], timestamp=5)
    )

    preparation = prepare_compaction(
        await session.get_branch(),
        CompactionSettings(keep_recent_tokens=5),
    )

    assert preparation is not None
    assert preparation.firstKeptEntryId == kept_id
    assert preparation.tokensBefore >= estimate_context_tokens(
        (await session.build_context()).messages
    )
    assert all(
        getattr(message, "role", None) != "toolResult" for message in preparation.keptMessages[:1]
    )


@pytest.mark.asyncio
async def test_compact_uses_llm_summary_and_persists_compaction_entry():
    session = await _memory_session()
    await session.append_message(UserMessage(content="old request", timestamp=1))
    kept_id = await session.append_message(UserMessage(content="kept request", timestamp=2))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "kept answer"}], timestamp=3)
    )
    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=_summary_stream("SUMMARY FROM LLM"),
        compaction=CompactionSettings(keep_recent_tokens=5),
    )
    events: list[str] = []
    harness.subscribe(lambda event, signal=None: events.append(event.type))

    result = await harness.compact()

    assert result.summary == "SUMMARY FROM LLM"
    assert result.firstKeptEntryId == kept_id
    assert "session_compact" in events
    entries = await session.get_entries()
    assert isinstance(entries[-1], CompactionEntry)
    context = await session.build_context()
    assert [getattr(m, "role", None) for m in context.messages] == [
        "compactionSummary",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_compact_hook_can_supply_summary_without_llm_call():
    session = await _memory_session()
    await session.append_message(UserMessage(content="old", timestamp=1))
    kept_id = await session.append_message(UserMessage(content="kept", timestamp=2))

    async def fail_stream(model, context, options=None):
        raise AssertionError("stream should not be called")

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=fail_stream,
        compaction=CompactionSettings(keep_recent_tokens=1),
    )

    def before_compact(event):
        return {
            "compaction": {
                "summary": "HOOK SUMMARY",
                "firstKeptEntryId": event.preparation.firstKeptEntryId,
                "tokensBefore": event.preparation.tokensBefore,
                "details": {"source": "hook"},
            }
        }

    harness.on("session_before_compact", before_compact)
    result = await harness.compact()

    assert result.summary == "HOOK SUMMARY"
    assert result.firstKeptEntryId == kept_id
    assert result.details == {"source": "hook"}


@pytest.mark.asyncio
async def test_navigate_tree_can_summarize_abandoned_branch_and_move_leaf():
    session = await _memory_session()
    root_id = await session.append_message(UserMessage(content="root", timestamp=1))
    branch_a = await session.append_message(UserMessage(content="branch a", timestamp=2))
    await session.move_to(root_id)
    branch_b = await session.append_message(UserMessage(content="branch b", timestamp=3))

    abandoned = collect_entries_for_branch_summary(await session.get_entries(), branch_b, branch_a)
    assert [entry.id for entry in abandoned] == [branch_b]

    harness = AgentHarness(
        session=session, model=_model(), stream_fn=_summary_stream("BRANCH SUMMARY")
    )
    events: list[str] = []
    harness.subscribe(lambda event, signal=None: events.append(event.type))

    result = await harness.navigate_tree(branch_a, {"summarize": True})

    assert result.targetId == branch_a
    assert result.leafId == root_id
    assert result.editorText == "branch a"
    assert await session.get_leaf_id() == result.branchSummaryEntryId
    assert "session_tree" in events
    assert any(isinstance(entry, BranchSummaryEntry) for entry in await session.get_entries())


@pytest.mark.asyncio
async def test_auto_compact_runs_after_turn_when_enabled():
    session = await _memory_session()
    await session.append_message(UserMessage(content="old " * 200, timestamp=1))
    harness = AgentHarness(
        session=session,
        model=_model(context_window=50),
        stream_fn=mock_text_stream,
        compaction=CompactionSettings(
            reserve_tokens=10,
            keep_recent_tokens=1,
            auto_compact=True,
        ),
    )

    await harness.prompt("new request")

    assert any(isinstance(entry, CompactionEntry) for entry in await session.get_entries())


def _summary_stream(text: str):
    async def stream(model: Model, context, options: StreamOptions | None = None):
        stream = AssistantMessageEventStream()
        partial = _base_partial(model, [{"type": "text", "text": text}])
        partial.stopReason = "stop"
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="stop"))
        stream.set_final_message(partial)
        stream.end()
        return stream

    return stream
