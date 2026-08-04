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
    prepare_branch_entries,
    prepare_compaction,
)
from pi_agent_harness.compaction.compaction import (
    build_compaction_prompt,
    extract_file_details,
    format_file_details,
)
from pi_agent_harness.types import AgentHarnessError, BranchSummaryEntry, CompactionEntry


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


@pytest.mark.asyncio
async def test_split_turn_summary_uses_structured_serialization():
    """Split-turn prefix should use serialize_conversation, not raw repr (H3-1 fix)."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="start turn", timestamp=1))
    await session.append_message(
        AssistantMessage(
            content=[
                {"type": "text", "text": "partial answer"},
                {"type": "toolCall", "id": "c1", "name": "read", "arguments": {"path": "f.py"}},
            ],
            stopReason="toolUse",
            timestamp=2,
        )
    )
    await session.append_message(
        ToolResultMessage(
            toolCallId="c1",
            toolName="read",
            content=[{"type": "text", "text": "file content"}],
            timestamp=3,
        )
    )
    await session.append_message(
        AssistantMessage(
            content=[{"type": "text", "text": "continued " * 30}],
            timestamp=4,
        )
    )
    await session.append_message(UserMessage(content="next turn", timestamp=5))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "final"}], timestamp=6)
    )

    preparation = prepare_compaction(
        await session.get_branch(),
        CompactionSettings(keep_recent_tokens=5),
    )
    assert preparation is not None
    if preparation.splitTurnSummary:
        assert "Turn Context:" in preparation.splitTurnSummary
        assert "[User]:" in preparation.splitTurnSummary
        assert "AssistantMessage" not in preparation.splitTurnSummary


@pytest.mark.asyncio
async def test_build_compaction_prompt_update_mode_includes_existing_summary():
    """Iterative compaction uses UPDATE mode with existing summary."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="first request", timestamp=1))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "first answer"}], timestamp=2)
    )
    await session.append_compaction("Old summary text", "dummy", 100)
    await session.append_message(UserMessage(content="second request", timestamp=3))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "second answer"}], timestamp=4)
    )
    await session.append_message(UserMessage(content="third request", timestamp=5))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "third answer"}], timestamp=6)
    )

    preparation = prepare_compaction(
        await session.get_branch(),
        CompactionSettings(keep_recent_tokens=5),
    )
    assert preparation is not None
    assert preparation.previousSummary == "Old summary text"
    prompt = build_compaction_prompt(preparation)
    assert "Update the existing summary" in prompt
    assert "Old summary text" in prompt


def test_extract_file_details_accumulates_and_inherits():
    """extract_file_details collects from toolCalls and inherits previous details."""
    messages = [
        AssistantMessage(
            content=[
                {"type": "toolCall", "id": "c1", "name": "read", "arguments": {"path": "/a.py"}},
                {"type": "toolCall", "id": "c2", "name": "write", "arguments": {"path": "/b.py"}},
            ],
            timestamp=1,
        ),
        UserMessage(content="ignored", timestamp=2),
        AssistantMessage(
            content=[
                {"type": "toolCall", "id": "c3", "name": "edit", "arguments": {"path": "/c.py"}},
            ],
            timestamp=3,
        ),
    ]
    previous = {"readFiles": ["/old.py"], "modifiedFiles": []}
    details = extract_file_details(messages, previous)
    assert "/a.py" in details["readFiles"]
    assert "/old.py" in details["readFiles"]
    assert "/b.py" in details["modifiedFiles"]
    assert "/c.py" in details["modifiedFiles"]


@pytest.mark.asyncio
async def test_navigate_tree_hook_can_cancel():
    """session_before_tree hook can cancel navigation."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="root", timestamp=1))
    target_id = await session.append_message(UserMessage(content="target", timestamp=2))

    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)
    harness.on("session_before_tree", lambda event: {"cancel": True})

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.navigate_tree(target_id)
    assert exc_info.value.code == "branch_summary"
    assert await session.get_leaf_id() == target_id


@pytest.mark.asyncio
async def test_navigate_tree_hook_supplies_summary_and_label():
    """session_before_tree hook can supply summary and label, skipping LLM."""
    session = await _memory_session()
    root_id = await session.append_message(UserMessage(content="root", timestamp=1))
    branch_a = await session.append_message(UserMessage(content="branch a", timestamp=2))
    await session.move_to(root_id)
    await session.append_message(UserMessage(content="branch b", timestamp=3))

    async def fail_stream(model, context, options=None):
        raise AssertionError("stream should not be called")

    harness = AgentHarness(session=session, model=_model(), stream_fn=fail_stream)

    def hook(event):
        return {"summary": "HOOK BRANCH SUMMARY", "label": "my-label"}

    harness.on("session_before_tree", hook)

    result = await harness.navigate_tree(branch_a, {"summarize": True})
    assert result.summary == "HOOK BRANCH SUMMARY"
    assert await session.get_label(branch_a) == "my-label"


@pytest.mark.asyncio
async def test_navigate_tree_custom_message_target_returns_editor_text():
    """navigate_tree on custom_message target returns editorText and moves to parent (H3-2)."""
    session = await _memory_session()
    root_id = await session.append_message(UserMessage(content="root", timestamp=1))
    cm_id = await session.append_custom_message_entry(
        custom_type="prompt", content="custom prompt text", display=True
    )

    harness = AgentHarness(session=session, model=_model(), stream_fn=mock_text_stream)
    result = await harness.navigate_tree(cm_id)

    assert result.editorText == "custom prompt text"
    assert result.leafId == root_id


@pytest.mark.asyncio
async def test_compact_raises_when_nothing_to_compact():
    """compact() raises when there are fewer than 2 message entries."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="only one", timestamp=1))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=mock_text_stream,
        compaction=CompactionSettings(keep_recent_tokens=1),
    )

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.compact()
    assert exc_info.value.code == "compaction"
    assert "Nothing to compact" in str(exc_info.value)


@pytest.mark.asyncio
async def test_auto_compact_failure_does_not_propagate_to_prompt():
    """auto_compact failure is silently swallowed; prompt() still returns."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="old " * 200, timestamp=1))

    call_count = 0

    async def failing_compact_stream(model, context, options=None):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("compaction LLM failed")
        return await mock_text_stream(model, context, options)

    harness = AgentHarness(
        session=session,
        model=_model(context_window=50),
        stream_fn=failing_compact_stream,
        compaction=CompactionSettings(
            reserve_tokens=10,
            keep_recent_tokens=1,
            auto_compact=True,
        ),
    )

    result = await harness.prompt("new request")
    assert result.role == "assistant"
    assert call_count >= 2


@pytest.mark.asyncio
async def test_prepare_branch_entries_respects_token_budget():
    """prepare_branch_entries trims from head when budget is exceeded."""
    session = await _memory_session()
    ids = []
    for i in range(10):
        ids.append(
            await session.append_message(UserMessage(content=f"message {i} " * 50, timestamp=i))
        )

    all_entries = await session.get_branch()
    small_budget = prepare_branch_entries(all_entries, token_budget=50)
    full_budget = prepare_branch_entries(all_entries, token_budget=999_999)

    assert len(small_budget) < len(full_budget)
    assert len(full_budget) == len(all_entries)
    assert small_budget[-1].id == all_entries[-1].id


@pytest.mark.asyncio
async def test_navigate_tree_from_hook_false_when_summary_from_llm():
    """fromHook is False when hook only provides a label but summary comes from LLM (H3-3)."""
    session = await _memory_session()
    root_id = await session.append_message(UserMessage(content="root", timestamp=1))
    branch_a = await session.append_message(UserMessage(content="branch a", timestamp=2))
    await session.move_to(root_id)
    await session.append_message(UserMessage(content="branch b", timestamp=3))

    harness = AgentHarness(
        session=session, model=_model(), stream_fn=_summary_stream("LLM SUMMARY")
    )
    harness.on("session_before_tree", lambda event: {"label": "tag-only"})

    result = await harness.navigate_tree(branch_a, {"summarize": True})
    assert result.summary == "Summary of abandoned branch:\n\nLLM SUMMARY"
    entries = await session.get_entries()
    bs_entry = next(e for e in entries if isinstance(e, BranchSummaryEntry))
    assert bs_entry.fromHook is False


@pytest.mark.asyncio
async def test_navigate_tree_from_hook_true_when_hook_supplies_summary():
    """fromHook is True only when the hook actually supplies a summary (H3-3)."""
    session = await _memory_session()
    root_id = await session.append_message(UserMessage(content="root", timestamp=1))
    branch_a = await session.append_message(UserMessage(content="branch a", timestamp=2))
    await session.move_to(root_id)
    await session.append_message(UserMessage(content="branch b", timestamp=3))

    async def fail_stream(model, context, options=None):
        raise AssertionError("stream should not be called")

    harness = AgentHarness(session=session, model=_model(), stream_fn=fail_stream)
    harness.on("session_before_tree", lambda event: {"summary": "HOOK ONLY"})

    await harness.navigate_tree(branch_a, {"summarize": True})
    entries = await session.get_entries()
    bs_entry = next(e for e in entries if isinstance(e, BranchSummaryEntry))
    assert bs_entry.fromHook is True


@pytest.mark.asyncio
async def test_cut_point_skips_backward_over_non_message_entries():
    """Cut point step 3: non-message entries before the cut are absorbed (H3-4)."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="old message " * 80, timestamp=1))
    mc_id = await session.append_model_change("new-provider", "new-model")
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "old answer " * 80}], timestamp=3)
    )
    await session.append_message(UserMessage(content="recent", timestamp=4))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "answer"}], timestamp=5)
    )

    preparation = prepare_compaction(
        await session.get_branch(),
        CompactionSettings(keep_recent_tokens=5),
    )
    assert preparation is not None
    assert preparation.firstKeptEntryId == mc_id


@pytest.mark.asyncio
async def test_compact_summary_includes_file_details_block():
    """Compaction summary includes <read-files>/<modified-files> blocks (H3-5)."""
    session = await _memory_session()
    await session.append_message(UserMessage(content="old request", timestamp=1))
    await session.append_message(
        AssistantMessage(
            content=[
                {"type": "toolCall", "id": "c1", "name": "read", "arguments": {"path": "/f.py"}},
                {"type": "toolCall", "id": "c2", "name": "write", "arguments": {"path": "/g.py"}},
            ],
            stopReason="toolUse",
            timestamp=2,
        )
    )
    await session.append_message(
        ToolResultMessage(
            toolCallId="c1",
            toolName="read",
            content=[{"type": "text", "text": "ok"}],
            timestamp=3,
        )
    )
    await session.append_message(
        ToolResultMessage(
            toolCallId="c2",
            toolName="write",
            content=[{"type": "text", "text": "ok"}],
            timestamp=4,
        )
    )
    await session.append_message(UserMessage(content="recent", timestamp=5))
    await session.append_message(
        AssistantMessage(content=[{"type": "text", "text": "final"}], timestamp=6)
    )

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=_summary_stream("BASE SUMMARY"),
        compaction=CompactionSettings(keep_recent_tokens=5),
    )
    result = await harness.compact()

    assert "<read-files>" in result.summary
    assert "/f.py" in result.summary
    assert "<modified-files>" in result.summary
    assert "/g.py" in result.summary
    assert result.details["readFiles"] == ["/f.py"]
    assert result.details["modifiedFiles"] == ["/g.py"]


def test_format_file_details_produces_xml_blocks():
    """format_file_details produces <read-files>/<modified-files> XML blocks."""
    details = {"readFiles": ["/a.py", "/b.py"], "modifiedFiles": ["/c.py"]}
    text = format_file_details(details)
    assert "<read-files>" in text
    assert "/a.py" in text
    assert "<modified-files>" in text
    assert "/c.py" in text

    empty = format_file_details({"readFiles": [], "modifiedFiles": []})
    assert empty == ""


@pytest.mark.asyncio
async def test_auto_compact_logs_warning_on_failure(caplog):
    """auto_compact logs a warning when compaction fails (H3-9)."""
    import logging

    session = await _memory_session()
    await session.append_message(UserMessage(content="old " * 200, timestamp=1))

    call_count = 0

    async def failing_compact_stream(model, context, options=None):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("compaction LLM failed")
        return await mock_text_stream(model, context, options)

    harness = AgentHarness(
        session=session,
        model=_model(context_window=50),
        stream_fn=failing_compact_stream,
        compaction=CompactionSettings(
            reserve_tokens=10,
            keep_recent_tokens=1,
            auto_compact=True,
        ),
    )

    with caplog.at_level(logging.WARNING, logger="pi_agent_harness.agent_harness"):
        await harness.prompt("new request")

    assert any("Auto-compaction failed" in record.message for record in caplog.records)


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
