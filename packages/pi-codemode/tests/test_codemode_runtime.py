"""Runtime tests for ``pi-codemode`` inside ``AgentHarness``."""

from __future__ import annotations

import asyncio
from typing import Any

import pi_codemode
import pytest
from pi_codemode.codemode_tool import CodemodeParams
from pi_codemode.runtime import _GateLock
from pydantic import BaseModel, ValidationError

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ExtensionAPI, ToolDefinition
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import (
    AgentToolResult,
    DoneEvent,
    LlmContext,
    Model,
    StartEvent,
    StreamOptions,
)
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session


class _ReadParams(BaseModel):
    path: str


class _WriteParams(BaseModel):
    path: str
    content: str = ""


class _McpHyphenParams(BaseModel):
    id: str


def _model() -> Model:
    return Model(provider="mock", model_id="m1")


async def _session(sid: str = "cm-runtime") -> Session:
    return Session(await MemorySessionStorage.create(session_id=sid))


def _single_codemode_llm(code: str, *, timeout_ms: int | None = None):
    calls = 0

    async def stream_fn(model: Model, context: LlmContext, options: StreamOptions | None = None):
        nonlocal calls
        calls += 1
        if calls > 1:
            return await mock_text_stream(model, context, options)
        args: dict[str, Any] = {"code": code}
        if timeout_ms is not None:
            args["timeout_ms"] = timeout_ms
        stream = AssistantMessageEventStream()
        partial = _base_partial(
            model,
            [{"type": "toolCall", "id": "call_cm_1", "name": "codemode", "arguments": args}],
        )
        partial.stopReason = "toolUse"
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream

    return stream_fn


async def _tool_results(harness: AgentHarness) -> list[Any]:
    ctx = await harness.session.build_context()
    return [m for m in ctx.messages if getattr(m, "role", None) == "toolResult"]


class TestCodemodeInHarness:
    async def test_executes_tools_filters_output_and_forwards_images(self) -> None:
        def ext(pi: ExtensionAPI) -> None:
            async def run_read(_id, params: _ReadParams, _sig, _upd):
                return AgentToolResult(
                    content=[
                        {"type": "text", "text": f'{{"file": "{params.path}", "n": 7}}'},
                        {"type": "image", "data": "QUJDRA==", "mimeType": "image/png"},
                    ],
                    details={"bytes": 12},
                )

            async def run_hyphen(_id, params: _McpHyphenParams, _sig, _upd):
                return AgentToolResult(content=[{"type": "text", "text": f"doc:{params.id}"}])

            pi.register_tool(
                ToolDefinition(
                    name="read",
                    description="Read file",
                    parameters=_ReadParams,
                    execute=run_read,
                    annotations={"readOnlyHint": True},
                )
            )
            pi.register_tool(
                ToolDefinition(
                    name="mcp__my-srv__get-doc",
                    description="Get docs",
                    parameters=_McpHyphenParams,
                    execute=run_hyphen,
                    annotations={"readOnlyHint": True},
                    deferred=True,
                )
            )

        updates: list[str] = []
        harness = AgentHarness(
            session=await _session("t1"),
            model=_model(),
            stream_fn=_single_codemode_llm(
                """
r1 = await tools.read(path="a.json")
r2 = await tools.read({"path": "b.json"})
d1 = await tools.mcp__my_srv__get_doc(id="x1")
d2 = await call_tool("mcp__my-srv__get-doc", {"id": "x2"})
if r1.images:
    image(r1.images[0])
print("debug-line")
text("summary:")
return {
    "n": json.loads(r1)["n"],
    "r2_details": r2.details,
    "docs": [str(d1), str(d2)],
}
"""
            ),
            extensions=[ext, pi_codemode.activate],
        )

        def on_ev(ev: Any, _sig: Any = None) -> None:
            if getattr(ev, "type", None) == "tool_execution_update":
                for b in ev.partial_result.content:
                    if b.get("type") == "text":
                        updates.append(b["text"])

        harness.subscribe(on_ev)
        await harness.prompt("go")

        results = await _tool_results(harness)
        assert len(results) == 1
        blocks = results[0].content
        text_out = blocks[0]["text"]
        assert "summary:" in text_out
        assert '"n": 7' in text_out
        assert '"docs": [\n    "doc:x1",\n    "doc:x2"\n  ]' in text_out
        assert "<console_output>\ndebug-line\n</console_output>" in text_out
        assert any(b.get("type") == "image" and b.get("data") == "QUJDRA==" for b in blocks)
        assert results[0].details["tool_calls"] == 4
        assert updates  # progress streaming fired

    async def test_validation_and_permission_errors_are_catchable_with_hints(self) -> None:
        def ext(pi: ExtensionAPI) -> None:
            async def run_write(_id, params: _WriteParams, _sig, _upd):
                return AgentToolResult(content=[{"type": "text", "text": f"wrote:{params.path}"}])

            pi.register_tool(
                ToolDefinition(
                    name="write",
                    description="Write file",
                    parameters=_WriteParams,
                    execute=run_write,
                )
            )

        harness = AgentHarness(
            session=await _session("t2"),
            model=_model(),
            stream_fn=_single_codemode_llm(
                """
errs = []
try:
    await tools.write(wrong_arg=1)
except ToolError as e:
    errs.append(("val", e.tool_name, "Expected signature" in str(e)))
try:
    await tools.write(path="secret.txt", content="x")
except ToolPermissionError as e:
    errs.append(("perm", e.tool_name, "blocked-by-policy" in str(e)))
return errs
"""
            ),
            extensions=[ext, pi_codemode.activate],
        )
        harness.on(
            "tool_call",
            lambda ev: (
                {"block": True, "reason": "blocked-by-policy"} if ev.toolName == "write" else None
            ),
        )
        await harness.prompt("go")

        import json as _json

        results = await _tool_results(harness)
        out = results[0].content[0]["text"]
        assert _json.loads(out) == [["val", "write", True], ["perm", "write", True]]

    async def test_non_harmless_inner_calls_serialize_decide_then_execute(self) -> None:
        log: list[str] = []

        def ext(pi: ExtensionAPI) -> None:
            async def run_write(_id, params: _WriteParams, _sig, _upd):
                log.append(f"start:{params.path}")
                await asyncio.sleep(0.05)
                log.append(f"end:{params.path}")
                return AgentToolResult(content=[{"type": "text", "text": "ok"}])

            pi.register_tool(
                ToolDefinition(
                    name="write",
                    description="Write",
                    parameters=_WriteParams,
                    execute=run_write,
                )
            )

        harness = AgentHarness(
            session=await _session("t3"),
            model=_model(),
            stream_fn=_single_codemode_llm(
                'await parallel(tools.write(path="1"), tools.write(path="2"))'
            ),
            extensions=[ext, pi_codemode.activate],
        )
        harness.on(
            "tool_call",
            lambda ev: log.append(f"decide:{ev.input['path']}") if ev.toolName == "write" else None,
        )
        await harness.prompt("go")

        assert log == [
            "decide:1",
            "start:1",
            "end:1",
            "decide:2",
            "start:2",
            "end:2",
        ]

    async def test_harmless_inner_calls_run_in_parallel_when_policy_is_parallel(self) -> None:
        active = 0
        peak = 0

        def ext(pi: ExtensionAPI) -> None:
            async def run_read(_id, params: _ReadParams, _sig, _upd):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1
                return AgentToolResult(content=[{"type": "text", "text": params.path}])

            pi.register_tool(
                ToolDefinition(
                    name="read",
                    description="Read",
                    parameters=_ReadParams,
                    execute=run_read,
                    annotations={"readOnlyHint": True},
                )
            )

        harness = AgentHarness(
            session=await _session("t4"),
            model=_model(),
            stream_fn=_single_codemode_llm(
                'await parallel(tools.read(path="a"), tools.read(path="b"), tools.read(path="c"))'
            ),
            extensions=[ext, pi_codemode.activate],
        )
        await harness.prompt("go")

        assert peak >= 2

    async def test_output_truncation_and_require_approval_setting(self) -> None:
        # Default require_approval=True leaves outer codemode annotations as None
        default_annotations: list[Any] = []
        harness_default = AgentHarness(
            session=await _session("t5a"),
            model=_model(),
            stream_fn=_single_codemode_llm('return "ok"'),
            extensions=[pi_codemode.activate],
        )
        harness_default.on("tool_call", lambda ev: default_annotations.append(ev.annotations))
        await harness_default.prompt("go")
        assert default_annotations == [None]

        # Explicit require_approval=False declares outer codemode harmless + truncates output
        opt_out_annotations: list[Any] = []
        harness2 = AgentHarness(
            session=await _session("t5b"),
            model=_model(),
            stream_fn=_single_codemode_llm('return "A" * 10000'),
        )
        harness2.set_extension_settings(
            "pi-codemode", {"max_output_kb": 4, "require_approval": False}
        )
        harness2.load_extension(pi_codemode.activate, name="pi-codemode")
        harness2.on("tool_call", lambda ev: opt_out_annotations.append(ev.annotations))
        await harness2.prompt("go")

        assert opt_out_annotations == [{"destructiveHint": False, "openWorldHint": False}]
        results = await _tool_results(harness2)
        text_out = results[0].content[0]["text"]
        assert "Output truncated to 4 KiB" in text_out
        assert results[0].details["truncated"] is True

    async def test_disabled_tool_omitted_from_catalog_and_blocked_in_codemode(self) -> None:
        def ext(pi: ExtensionAPI) -> None:
            async def run_read(_id, params: _ReadParams, _sig, _upd):
                return AgentToolResult(content=[{"type": "text", "text": f"r:{params.path}"}])

            async def run_secret(_id, params: _ReadParams, _sig, _upd):
                return AgentToolResult(content=[{"type": "text", "text": f"s:{params.path}"}])

            pi.register_tool(
                ToolDefinition(
                    name="read",
                    description="Read file",
                    parameters=_ReadParams,
                    execute=run_read,
                    annotations={"readOnlyHint": True},
                )
            )
            pi.register_tool(
                ToolDefinition(
                    name="secret_tool",
                    description="Disabled tool",
                    parameters=_ReadParams,
                    execute=run_secret,
                    annotations={"readOnlyHint": True},
                )
            )

        harness = AgentHarness(
            session=await _session("t-disabled"),
            model=_model(),
            stream_fn=_single_codemode_llm(
                "names = sorted(ALL_TOOLS)\n"
                "try:\n"
                '    await call_tool("secret_tool", {"path": "x"})\n'
                "    err = None\n"
                "except Exception as exc:\n"
                "    err = str(exc)\n"
                'return {"names": names, "err": err}'
            ),
            extensions=[ext, pi_codemode.activate],
        )
        await harness.load_extensions()
        await harness.set_active_tools(["read", "codemode", "tool_search"])
        await harness.prompt("go")

        results = await _tool_results(harness)
        import json

        payload = json.loads(results[0].content[0]["text"])
        assert payload["names"] == ["read"]
        assert "Unknown tool 'secret_tool'" in (payload["err"] or "")

    async def test_gate_lock_prefers_waiting_writers_and_wakes_on_cancel(self) -> None:
        lock = _GateLock()
        events: list[str] = []

        async with lock.reader():
            writer_started = asyncio.Event()
            writer_acquired = asyncio.Event()
            reader2_acquired = asyncio.Event()

            async def writer() -> None:
                writer_started.set()
                async with lock.writer():
                    writer_acquired.set()
                    events.append("writer")

            async def reader2() -> None:
                async with lock.reader():
                    reader2_acquired.set()
                    events.append("reader2")

            w_task = asyncio.create_task(writer())
            await writer_started.wait()
            await asyncio.sleep(0)  # writer is queued in _waiting_writers

            r2_task = asyncio.create_task(reader2())
            await asyncio.sleep(0)
            # Reader 2 must yield to the queued writer instead of starving it
            assert not reader2_acquired.is_set()
            assert not writer_acquired.is_set()

        await asyncio.wait_for(asyncio.gather(w_task, r2_task), timeout=5)
        assert events == ["writer", "reader2"]

        # Cancelling a queued writer notifies blocked readers cleanly
        async with lock.reader():
            w_queued = asyncio.Event()

            async def cancelled_writer() -> None:
                w_queued.set()
                async with lock.writer():
                    pass

            cw_task = asyncio.create_task(cancelled_writer())
            await w_queued.wait()
            await asyncio.sleep(0)

            r3_acquired = asyncio.Event()

            async def reader3() -> None:
                async with lock.reader():
                    r3_acquired.set()

            r3_task = asyncio.create_task(reader3())
            await asyncio.sleep(0)
            assert not r3_acquired.is_set()

            cw_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cw_task
            await asyncio.wait_for(r3_task, timeout=5)
            assert r3_acquired.is_set()

    def test_codemode_params_rejects_timeout_below_minimum(self) -> None:
        with pytest.raises(ValidationError):
            CodemodeParams(code="return 1", timeout_ms=500)
        valid = CodemodeParams(code="return 1", timeout_ms=1000)
        assert valid.timeout_ms == 1000

    async def test_abort_cancels_running_codemode_script(self) -> None:
        started = asyncio.Event()

        def ext(pi: ExtensionAPI) -> None:
            async def run_slow(_id, params: _ReadParams, _sig, _upd):
                started.set()
                await asyncio.sleep(30)
                return AgentToolResult(content=[{"type": "text", "text": "done"}])

            pi.register_tool(
                ToolDefinition(
                    name="read",
                    description="Slow read",
                    parameters=_ReadParams,
                    execute=run_slow,
                    annotations={"readOnlyHint": True},
                )
            )

        harness = AgentHarness(
            session=await _session("t6"),
            model=_model(),
            stream_fn=_single_codemode_llm('await tools.read(path="slow")'),
            extensions=[ext, pi_codemode.activate],
        )
        task = asyncio.create_task(harness.prompt("go"))
        await asyncio.wait_for(started.wait(), timeout=10)
        aborted = await harness.abort()
        await asyncio.wait_for(task, timeout=10)

        assert aborted is not None
        results = await _tool_results(harness)
        assert any("aborted" in r.content[0]["text"].lower() for r in results)
