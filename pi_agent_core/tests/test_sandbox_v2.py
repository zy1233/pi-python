"""Tests for ``pi_agent_core.sandbox`` protocol v2 and codemode runtime."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from pi_agent_core.sandbox import (
    PROTOCOL,
    CodemodeScriptError,
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxProtocolError,
    ScriptSandbox,
)


class _FakeCodemodeHost:
    def __init__(self, handler: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.logs: list[str] = []
        self.outputs: list[tuple[str, Any]] = []
        self._handler = handler

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, args))
        if self._handler is not None:
            return await self._handler(tool_name, args)
        return {"text": f"{tool_name}:{args}", "details": {"ok": True}, "images": []}

    def log(self, message: str) -> None:
        self.logs.append(message)

    def output(self, kind: str, data: Any) -> None:
        self.outputs.append((kind, data))


def _meta() -> dict[str, Any]:
    return {
        "read": {
            "call_name": "read",
            "description": "Read a file from disk",
            "signature": "async def read(*, path: str) -> ToolText",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            "deferred": False,
            "read_only": True,
        },
        "mcp__dev-srv__get-doc": {
            "call_name": "mcp__dev_srv__get_doc",
            "description": "Fetch library documentation",
            "signature": "async def mcp__dev_srv__get_doc(*, id: str) -> ToolText",
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
            "deferred": True,
            "read_only": True,
        },
    }


async def _run_cm(
    script: str,
    *,
    host: _FakeCodemodeHost | None = None,
    limits: SandboxLimits | None = None,
    max_tool_calls: int = 100,
    tools_meta: dict[str, Any] | None = None,
):
    sb = ScriptSandbox(limits or SandboxLimits(wall_seconds=30.0))
    return await sb.run_codemode(
        script,
        tools_meta=tools_meta if tools_meta is not None else _meta(),
        host=host or _FakeCodemodeHost(),
        max_tool_calls=max_tool_calls,
    )


class TestCodemodeExecutionAndReturn:
    async def test_protocol_version_is_2(self) -> None:
        assert PROTOCOL == 2

    async def test_trailing_expression_is_captured_without_explicit_return(self) -> None:
        out = await _run_cm("x = 20\ny = 22\nx + y")
        assert out.result == 42

    async def test_explicit_return_overrides_trailing_code(self) -> None:
        out = await _run_cm("x = 1\nreturn {'value': x}\n999")
        assert out.result == {"value": 1}

    async def test_trailing_print_text_image_do_not_return_none_as_result(self) -> None:
        host = _FakeCodemodeHost()
        out = await _run_cm(
            'print("log-1")\ntext("hello", "world")\nimage("QUJD", mime_type="image/jpeg")',
            host=host,
        )
        assert out.result is None
        assert out.logs == ["log-1"]
        assert out.outputs == [
            ("text", "hello world"),
            ("image", {"data": "QUJD", "mimeType": "image/jpeg"}),
        ]

    async def test_markdown_fence_is_stripped_preserving_error_line(self) -> None:
        script = "```python\nx = 1\n1 / 0\n```"
        with pytest.raises(CodemodeScriptError) as exc_info:
            await _run_cm(script)
        assert exc_info.value.error_type == "ZeroDivisionError"
        assert exc_info.value.line == 3

    async def test_syntax_error_reports_exact_line(self) -> None:
        with pytest.raises(CodemodeScriptError) as exc_info:
            await _run_cm("a = 1\nb = (\n")
        assert exc_info.value.error_type == "SyntaxError"
        assert exc_info.value.line == 2


class TestToolsAndDiscovery:
    async def test_dual_calling_convention_and_tool_text_properties(self) -> None:
        async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {
                "text": f'{{"name": "{name}", "path": "{args.get("path")}"}}',
                "details": {"bytes": 42},
                "images": [{"data": "IMGDATA", "mimeType": "image/png"}],
            }

        host = _FakeCodemodeHost(handler)
        out = await _run_cm(
            """
a = await tools.read(path="one.py")
b = await tools.read({"path": "two.py"})
parsed = json.loads(a)
if b.images:
    image(b.images[0])
return {
    "is_str": isinstance(a, str),
    "text_eq": a.text == str(a),
    "details": a.details,
    "parsed": parsed,
}
""",
            host=host,
        )
        assert host.calls == [("read", {"path": "one.py"}), ("read", {"path": "two.py"})]
        assert out.result == {
            "is_str": True,
            "text_eq": True,
            "details": {"bytes": 42},
            "parsed": {"name": "read", "path": "one.py"},
        }
        assert out.outputs == [("image", {"data": "IMGDATA", "mimeType": "image/png"})]

    async def test_call_tool_and_normalized_identifier_reach_raw_name(self) -> None:
        host = _FakeCodemodeHost()
        await _run_cm(
            """
await tools.mcp__dev_srv__get_doc(id="react")
await call_tool("mcp__dev-srv__get-doc", {"id": "vue"})
""",
            host=host,
        )
        assert host.calls == [
            ("mcp__dev-srv__get-doc", {"id": "react"}),
            ("mcp__dev-srv__get-doc", {"id": "vue"}),
        ]

    async def test_all_tools_search_and_describe(self) -> None:
        out = await _run_cm(
            """
hits = search_tools("/get[-_]doc/")
desc = describe_tool("mcp__dev_srv__get_doc")
all_count = len(ALL_TOOLS)
immutable = False
try:
    ALL_TOOLS["x"] = {}
except TypeError:
    immutable = True
return {
    "hit_names": [h["name"] for h in hits],
    "desc_name": desc["name"],
    "all_count": all_count,
    "immutable": immutable,
}
"""
        )
        assert out.result == {
            "hit_names": ["mcp__dev-srv__get-doc"],
            "desc_name": "mcp__dev-srv__get-doc",
            "all_count": 2,
            "immutable": True,
        }

    async def test_forbidden_tools_cannot_be_invoked_from_codemode(self) -> None:
        out = await _run_cm(
            """
errs = []
for fn in (lambda: tools.codemode, lambda: tools.workflow, lambda: tools.tool_search):
    try:
        fn()
    except PermissionError as e:
        errs.append(str(e))
try:
    await call_tool("codemode", {"code": "1"})
except PermissionError as e:
    errs.append(str(e))
return errs
"""
        )
        assert len(out.result) == 4
        assert all("cannot be called" in msg for msg in out.result)

    async def test_parallel_executes_concurrently_and_cleans_up(self) -> None:
        active = 0
        peak = 0

        async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return {"text": args["path"], "details": {}, "images": []}

        host = _FakeCodemodeHost(handler)
        out = await _run_cm(
            """
res = await parallel(
    tools.read(path="1"),
    tools.read(path="2"),
    tools.read(path="3"),
    tools.read(path="4"),
    concurrency=2,
)
return [str(x) for x in res]
""",
            host=host,
        )
        assert out.result == ["1", "2", "3", "4"]
        assert peak <= 2


class TestSandboxSafetyAndLimits:
    async def test_safe_stdlib_imports_work_while_unsafe_imports_fail(self) -> None:
        out = await _run_cm(
            """
import difflib
from urllib import parse
from urllib.parse import quote
from io import StringIO
from pathlib import PurePosixPath

blocked = {}
for mod in ("os", "sys", "subprocess", "socket", "ctypes", "shutil"):
    try:
        __import__(mod)
        blocked[mod] = "ALLOWED"
    except ImportError as e:
        blocked[mod] = "blocked"

bad_attrs = {}
try:
    from pathlib import Path
    bad_attrs["Path"] = "ALLOWED"
except ImportError:
    bad_attrs["Path"] = "blocked"
try:
    from io import open as io_open
    bad_attrs["io_open"] = "ALLOWED"
except ImportError:
    bad_attrs["io_open"] = "blocked"
try:
    from asyncio import create_subprocess_exec
    bad_attrs["subproc"] = "ALLOWED"
except ImportError:
    bad_attrs["subproc"] = "blocked"

return {
    "quoted": quote("a b") + "|" + parse.quote("c d"),
    "ratio": round(difflib.SequenceMatcher(None, "abc", "abd").ratio(), 2),
    "sio": StringIO("hi").read(),
    "pure": str(PurePosixPath("src") / "a.py"),
    "blocked": blocked,
    "bad_attrs": bad_attrs,
}
"""
        )
        assert out.result["quoted"] == "a%20b|c%20d"
        assert out.result["sio"] == "hi"
        assert out.result["pure"] == "src/a.py"
        assert all(v == "blocked" for v in out.result["blocked"].values())
        assert all(v == "blocked" for v in out.result["bad_attrs"].values())

    async def test_max_tool_calls_raises_tool_limit_error(self) -> None:
        host = _FakeCodemodeHost()
        out = await _run_cm(
            """
await tools.read(path="1")
await tools.read(path="2")
try:
    await tools.read(path="3")
    return "missed"
except ToolLimitError as e:
    return f"caught:{e.tool_name}"
""",
            host=host,
            max_tool_calls=2,
        )
        assert out.result == "caught:read"
        assert len(host.calls) == 2

    async def test_wall_clock_stops_runaway_codemode_script(self) -> None:
        started = time.monotonic()
        with pytest.raises(SandboxLimitExceeded):
            await _run_cm(
                "while True:\n    pass",
                limits=SandboxLimits(wall_seconds=1.0),
            )
        assert time.monotonic() - started < 15.0

    async def test_bad_protocol_message_raises_protocol_error(self) -> None:
        with pytest.raises(SandboxProtocolError, match="bad image output"):
            await _run_cm(
                'image.__globals__["_send"]({"t": "output", "kind": "image", "data": {"x": 1}})'
            )
