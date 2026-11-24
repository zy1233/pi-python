"""Tests for ``pi-mcp`` deferred exposure when ``pi-codemode`` is present vs absent."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pi_codemode
import pi_mcp
import pytest

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ClientMcpServer
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, LlmContext, Model, StartEvent, StreamOptions
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

MOCK_SERVER = Path(__file__).with_name("mock_server.py")


def _server(name: str = "mock") -> ClientMcpServer:
    return ClientMcpServer(
        name=name, transport="stdio", command=sys.executable, args=(str(MOCK_SERVER),)
    )


async def _make_harness(
    *,
    extensions: list[Any],
    stream_fn: Any = None,
    codemode_settings: dict[str, Any] | None = None,
    mcp_settings: dict[str, Any] | None = None,
) -> AgentHarness:
    harness = AgentHarness(
        session=Session(await MemorySessionStorage.create(session_id="mcp-deferred")),
        model=Model(provider="mock", model_id="m1"),
        stream_fn=stream_fn or mock_text_stream,
        extensions=extensions,
    )
    if codemode_settings is not None:
        harness.set_extension_settings("pi-codemode", codemode_settings)
    if mcp_settings is not None:
        harness.set_extension_settings("pi-mcp", mcp_settings)
    harness.set_client_mcp_servers([_server()])
    return harness


class TestMcpDeferredWithCodemode:
    async def test_mcp_tools_defer_when_codemode_active_and_are_callable_inside_codemode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PI_MCP_EXPOSURE", raising=False)
        offered: list[list[str]] = []
        step = 0

        async def llm(model: Model, context: LlmContext, options: StreamOptions | None = None):
            nonlocal step
            step += 1
            offered.append([t.name for t in context.tools])
            if step == 1:
                stream = AssistantMessageEventStream()
                partial = _base_partial(
                    model,
                    [
                        {
                            "type": "toolCall",
                            "id": "c1",
                            "name": "codemode",
                            "arguments": {
                                "code": (
                                    'hits = search_tools("echo")\n'
                                    'name = hits[0]["name"]\n'
                                    'res = await call_tool(name, {"text": "deferred-hi"})\n'
                                    "return str(res)"
                                )
                            },
                        }
                    ],
                )
                partial.stopReason = "toolUse"
                stream.push(StartEvent(partial=partial.model_copy(deep=True)))
                stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
                stream.set_final_message(partial)
                stream.end()
                return stream
            return await mock_text_stream(model, context, options)

        harness = await _make_harness(
            extensions=[pi_codemode.activate, pi_mcp.activate],
            stream_fn=llm,
        )
        try:
            await harness.prompt("use mcp via codemode")
            # MCP tools are deferred — not offered directly on turn 1
            assert "codemode" in offered[0]
            assert "mcp__mock__echo" not in offered[0]
            assert "mcp__mock__echo" in harness.deferred_tool_names

            ctx = await harness.session.build_context()
            tool_results = [m for m in ctx.messages if getattr(m, "role", None) == "toolResult"]
            assert tool_results[0].content[0]["text"] == "echo: deferred-hi"
        finally:
            await harness.close()

    async def test_mcp_tools_stay_direct_when_exposure_direct_or_codemode_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PI_MCP_EXPOSURE", "direct")
        offered: list[list[str]] = []

        async def llm(model: Model, context: LlmContext, options: StreamOptions | None = None):
            offered.append([t.name for t in context.tools])
            return await mock_text_stream(model, context, options)

        harness = await _make_harness(
            extensions=[pi_codemode.activate, pi_mcp.activate],
            stream_fn=llm,
        )
        try:
            await harness.prompt("go")
            assert "mcp__mock__echo" in offered[0]
        finally:
            await harness.close()

        monkeypatch.delenv("PI_MCP_EXPOSURE", raising=False)
        offered.clear()
        harness2 = await _make_harness(extensions=[pi_mcp.activate], stream_fn=llm)
        try:
            await harness2.prompt("go")
            assert "mcp__mock__echo" in offered[0]
        finally:
            await harness2.close()

    async def test_deferred_exposure_requires_active_discovery_tool(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("PI_MCP_EXPOSURE", "deferred")
        offered: list[list[str]] = []

        async def llm(model: Model, context: LlmContext, options: StreamOptions | None = None):
            offered.append([t.name for t in context.tools])
            return await mock_text_stream(model, context, options)

        # Even when PI_MCP_EXPOSURE=deferred, without codemode/tool_search MCP tools stay direct
        with caplog.at_level("WARNING"):
            harness_no_disc = await _make_harness(extensions=[pi_mcp.activate], stream_fn=llm)
            try:
                await harness_no_disc.prompt("go")
                assert "mcp__mock__echo" in offered[0]
                assert "mcp__mock__echo" not in harness_no_disc.deferred_tool_names
                assert "neither codemode nor tool_search is active" in caplog.text
            finally:
                await harness_no_disc.close()

        # When codemode is disabled via settings, PI_MCP_EXPOSURE=deferred also keeps MCP direct
        offered.clear()
        harness_disabled = await _make_harness(
            extensions=[pi_codemode.activate, pi_mcp.activate],
            stream_fn=llm,
            codemode_settings={"enabled": False},
        )
        try:
            await harness_disabled.prompt("go")
            assert "mcp__mock__echo" in offered[0]
            assert "mcp__mock__echo" not in harness_disabled.deferred_tool_names
        finally:
            await harness_disabled.close()

        # When codemode IS active, PI_MCP_EXPOSURE=deferred defers even with defer_mcp=False
        offered.clear()
        harness_deferred = await _make_harness(
            extensions=[pi_codemode.activate, pi_mcp.activate],
            stream_fn=llm,
            codemode_settings={"defer_mcp": False},
        )
        try:
            await harness_deferred.prompt("go")
            assert "mcp__mock__echo" not in offered[0]
            assert "mcp__mock__echo" in harness_deferred.deferred_tool_names
        finally:
            await harness_deferred.close()
