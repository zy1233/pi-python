"""Tests for ``tool_search``, ``/codemode`` commands, and mode persistence."""

from __future__ import annotations

from typing import Any

import pi_codemode
from pydantic import BaseModel

from pi_agent_core.extensions import ExtensionAPI, ToolDefinition
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import AgentToolResult, LlmContext, Model, StreamOptions
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session


class _DummyParams(BaseModel):
    q: str = ""


def _model() -> Model:
    return Model(provider="mock", model_id="m1")


async def _session(sid: str = "cm-modes") -> Session:
    return Session(await MemorySessionStorage.create(session_id=sid))


def _sample_ext(pi: ExtensionAPI) -> None:
    async def run(_id, _params: _DummyParams, _sig, _upd):
        return AgentToolResult(content=[{"type": "text", "text": "ok"}])

    pi.register_tool(
        ToolDefinition(
            name="read",
            description="Read files",
            parameters=_DummyParams,
            execute=run,
            annotations={"readOnlyHint": True},
        )
    )
    pi.register_tool(
        ToolDefinition(
            name="mcp__github__list_issues",
            description="List issues in a GitHub repository",
            parameters=_DummyParams,
            execute=run,
            deferred=True,
        )
    )
    pi.register_tool(
        ToolDefinition(
            name="mcp__slack__send_msg",
            description="Send a message to Slack",
            parameters=_DummyParams,
            execute=run,
            deferred=True,
        )
    )


class TestToolSearchAndSlashCommands:
    async def test_tool_search_discovers_and_activates_deferred_tools(self) -> None:
        offered: list[list[str]] = []

        async def rec_stream(model: Model, context: LlmContext, options: StreamOptions):
            offered.append([t.name for t in context.tools])
            return await mock_text_stream(model, context, options)

        captured: dict[str, Any] = {}

        def capture_ext(pi: ExtensionAPI) -> None:
            captured["pi"] = pi

        harness = AgentHarness(
            session=await _session("search-1"),
            model=_model(),
            stream_fn=rec_stream,
            extensions=[_sample_ext, pi_codemode.activate, capture_ext],
        )
        await harness.prompt("turn 1")

        assert set(offered[0]) == {"read", "codemode", "tool_search"}

        pi: ExtensionAPI = captured["pi"]
        res = await pi.invoke_tool(
            "ts-1",
            "tool_search",
            {"query": "github issues", "activate": True},
        )
        text = res.content[0]["text"]
        assert "mcp__github__list_issues" in text
        assert "mcp__slack__send_msg" not in text
        assert "mcp__github__list_issues" in harness.active_tool_names

        await harness.prompt("turn 2")
        assert "mcp__github__list_issues" in offered[1]
        assert "mcp__slack__send_msg" not in offered[1]

    async def test_codemode_slash_command_switches_only_hybrid_and_off(self) -> None:
        harness = AgentHarness(
            session=await _session("cmd-1"),
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[_sample_ext, pi_codemode.activate],
        )
        await harness.load_extensions()

        status_msg = await harness.dispatch_command("codemode", "")
        assert status_msg is not None and "hybrid" in status_msg

        only_msg = await harness.dispatch_command("codemode", "only")
        assert only_msg is not None and "only" in only_msg
        assert set(harness.active_tool_names) == {"codemode", "tool_search"}

        on_msg = await harness.dispatch_command("codemode", "on")
        assert on_msg is not None and "hybrid" in on_msg
        assert "read" in harness.active_tool_names
        assert "codemode" in harness.active_tool_names

        off_msg = await harness.dispatch_command("codemode", "off")
        assert off_msg is not None and "disabled" in off_msg
        assert "codemode" not in harness.active_tool_names
        assert "tool_search" not in harness.active_tool_names
        # Turning codemode off promotes deferred tools so the model can still reach them
        assert "mcp__github__list_issues" in harness.active_tool_names
        assert "mcp__slack__send_msg" in harness.active_tool_names

    async def test_mode_persists_across_session_start_replay(self) -> None:
        storage = await MemorySessionStorage.create(session_id="persist-1")
        s1 = Session(storage)
        h1 = AgentHarness(
            session=s1,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[_sample_ext, pi_codemode.activate],
        )
        await h1.load_extensions()
        await h1.dispatch_command("codemode", "only")
        await h1._flush_pending_session_writes()
        await h1.close()

        s2 = Session(storage)
        h2 = AgentHarness(
            session=s2,
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[_sample_ext, pi_codemode.activate],
        )
        await h2.load_extensions()
        assert set(h2.active_tool_names) == {"codemode", "tool_search"}
        await h2.close()

    async def test_disabled_by_setting_registers_nothing_until_turned_on(self) -> None:
        harness = AgentHarness(
            session=await _session("disabled-1"),
            model=_model(),
            stream_fn=mock_text_stream,
            extensions=[_sample_ext],
        )
        harness.set_extension_settings("pi-codemode", {"enabled": False})
        harness.load_extension(pi_codemode.activate, name="pi-codemode")
        await harness.load_extensions()

        assert "codemode" not in harness._tools
        assert "tool_search" not in harness._tools
        assert harness.deferred_tool_names == [
            "mcp__github__list_issues",
            "mcp__slack__send_msg",
        ]

        await harness.dispatch_command("codemode", "on")
        assert "codemode" in harness.active_tool_names
        assert "tool_search" in harness.active_tool_names
