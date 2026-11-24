"""Tests for deferred tool loading, extension settings, and gated ``invoke_tool``."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from pi_agent_core.extensions import ExtensionAPI, ExtensionMeta, ExtensionRegistry, ToolDefinition
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import AgentToolResult, LlmContext, Model, StreamOptions
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session
from pi_agent_harness.types import AgentHarnessError


class _EchoParams(BaseModel):
    msg: str
    count: int = 1


async def _session(sid: str = "deferred-test") -> Session:
    return Session(await MemorySessionStorage.create(session_id=sid))


class TestDeferredToolsAndSettings:
    async def test_deferred_tool_omitted_from_llm_tools_until_activated(self) -> None:
        offered_tool_names: list[list[str]] = []
        seen_deferred_in_prompt: list[list[str]] = []
        captured: dict[str, Any] = {}

        async def recording_stream(model: Model, context: LlmContext, options: StreamOptions):
            offered_tool_names.append([t.name for t in context.tools])
            return await mock_text_stream(model, context, options)

        def system_prompt(ctx: dict[str, Any]) -> str:
            seen_deferred_in_prompt.append(list(ctx.get("deferred_tool_names") or []))
            return "sys"

        def extension(pi: ExtensionAPI) -> None:
            captured["pi"] = pi
            captured["settings"] = pi.get_settings()

            async def run_eager(_id, params: _EchoParams, _sig, _upd):
                return AgentToolResult(content=[{"type": "text", "text": params.msg}], details={})

            async def run_deferred(_id, params: _EchoParams, _sig, _upd):
                return AgentToolResult(
                    content=[{"type": "text", "text": f"def:{params.msg}x{params.count}"}],
                    details={"ok": True},
                )

            pi.register_tool(
                ToolDefinition(
                    name="eager_tool",
                    description="Always active",
                    parameters=_EchoParams,
                    execute=run_eager,
                    annotations={"readOnlyHint": True},
                )
            )
            pi.register_tool(
                ToolDefinition(
                    name="lazy_tool",
                    description="Loaded on demand",
                    parameters=_EchoParams,
                    execute=run_deferred,
                    annotations={"readOnlyHint": False},
                    deferred=True,
                )
            )

        harness = AgentHarness(
            session=await _session(),
            model=Model(provider="mock", model_id="m1"),
            stream_fn=recording_stream,
            system_prompt=system_prompt,
        )
        harness.set_extension_settings("my-ext", {"enabled": True, "limit": 42})
        harness.load_extension(extension, name="my-ext")

        events: list[Any] = []
        harness.subscribe(lambda ev, _sig=None: events.append(ev))

        await harness.prompt("turn 1")

        assert captured["settings"] == {"enabled": True, "limit": 42}
        assert offered_tool_names[0] == ["eager_tool"]
        assert seen_deferred_in_prompt[0] == ["lazy_tool"]

        pi: ExtensionAPI = captured["pi"]
        infos = {info.name: info for info in pi.get_all_tools()}
        assert infos["eager_tool"].deferred is False
        assert infos["eager_tool"].active is True
        assert infos["eager_tool"].annotations == {"readOnlyHint": True}
        assert "msg" in infos["lazy_tool"].parameters["properties"]
        assert infos["lazy_tool"].deferred is True
        assert infos["lazy_tool"].active is False

        added = pi.activate_tools(["lazy_tool", "lazy_tool", "eager_tool"])
        assert added == ["lazy_tool"]
        assert "lazy_tool" in harness.active_tool_names
        assert harness.deferred_tool_names == []

        await harness.prompt("turn 2")
        assert offered_tool_names[1] == ["eager_tool", "lazy_tool"]
        assert seen_deferred_in_prompt[1] == []

        removed = pi.deactivate_tools(["lazy_tool"])
        assert removed == ["lazy_tool"]
        assert "lazy_tool" not in harness.active_tool_names
        assert harness.deferred_tool_names == ["lazy_tool"]
        await asyncio.sleep(0)
        assert any(getattr(ev, "type", None) == "tools_update" for ev in events)

    def test_unconnected_api_settings_and_policy_fall_back_safely(self) -> None:
        api = ExtensionAPI(registry=ExtensionRegistry(), meta=ExtensionMeta(name="bare"))
        assert api.get_settings() == {}
        assert api.is_sequential_tool_policy is False


class TestInvokeTool:
    async def test_invoke_tool_validates_prepares_and_gates_with_origin(self) -> None:
        captured: dict[str, Any] = {}
        seen_events: list[Any] = []

        def extension(pi: ExtensionAPI) -> None:
            captured["pi"] = pi

            async def exec_fn(call_id: str, params: _EchoParams, _sig, _upd):
                return AgentToolResult(
                    content=[{"type": "text", "text": f"{call_id}:{params.msg}:{params.count}"}],
                    details={"count": params.count},
                )

            pi.register_tool(
                ToolDefinition(
                    name="prepared_tool",
                    description="Tool with prepare_arguments",
                    parameters=_EchoParams,
                    execute=exec_fn,
                    prepare_arguments=lambda args: {
                        **args,
                        "msg": str(args.get("msg", "")).strip(),
                    },
                    annotations={"destructiveHint": False, "openWorldHint": False},
                    deferred=True,
                )
            )

        harness = AgentHarness(
            session=await _session("invoke-1"),
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
            extensions=[extension],
        )

        def on_tool_call(event: Any) -> dict[str, Any] | None:
            seen_events.append(event)
            if event.input.get("msg") == "forbidden":
                return {"block": True, "reason": "Denied by policy"}
            return None

        harness.on("tool_call", on_tool_call)
        await harness.load_extensions()

        pi: ExtensionAPI = captured["pi"]
        assert pi.is_sequential_tool_policy is False
        harness.tool_execution = "sequential"
        assert pi.is_sequential_tool_policy is True

        # Valid call to a deferred tool
        res = await pi.invoke_tool(
            "cm-1:1",
            "prepared_tool",
            {"msg": "  hello  ", "count": 3},
            origin={"kind": "codemode", "cwd": "."},
        )
        assert res.content[0]["text"] == "cm-1:1:hello:3"
        assert seen_events[0].origin == {"kind": "codemode", "cwd": "."}
        assert seen_events[0].annotations == {"destructiveHint": False, "openWorldHint": False}

        # Invalid arguments fail validation before reaching tool_call hooks
        count_before = len(seen_events)
        with pytest.raises(ValidationError):
            await pi.invoke_tool("cm-1:2", "prepared_tool", {"count": "not-an-int"})
        assert len(seen_events) == count_before

        # Blocked call raises PermissionError
        with pytest.raises(PermissionError, match="Denied by policy"):
            await pi.invoke_tool("cm-1:3", "prepared_tool", {"msg": "forbidden"})

        # Missing tool raises KeyError
        with pytest.raises(KeyError):
            await pi.invoke_tool("cm-1:4", "no_such_tool", {})

    async def test_invoke_tool_fails_closed_when_hook_raises_or_aborted(self) -> None:
        executed = False
        captured: dict[str, Any] = {}

        def extension(pi: ExtensionAPI) -> None:
            captured["pi"] = pi

            async def exec_fn(_id, _params: _EchoParams, _sig, _upd):
                nonlocal executed
                executed = True
                return AgentToolResult(content=[{"type": "text", "text": "ran"}], details={})

            pi.register_tool(
                ToolDefinition(
                    name="target",
                    description="Target",
                    parameters=_EchoParams,
                    execute=exec_fn,
                )
            )

        harness = AgentHarness(
            session=await _session("invoke-2"),
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
            extensions=[extension],
        )

        def boom(_event: Any) -> None:
            raise RuntimeError("hook crashed")

        harness.on("tool_call", boom)
        await harness.load_extensions()
        pi: ExtensionAPI = captured["pi"]

        with pytest.raises(AgentHarnessError, match="hook crashed"):
            await pi.invoke_tool("cm-2:1", "target", {"msg": "x"})
        assert executed is False

        class _Sig:
            aborted = True

        with pytest.raises(RuntimeError, match="aborted"):
            await pi.invoke_tool("cm-2:2", "target", {"msg": "x"}, signal=_Sig())
        assert executed is False
