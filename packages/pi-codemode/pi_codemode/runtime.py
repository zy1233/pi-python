"""Codemode script runtime: RW/sequential gate lock, inner tool invocation, and formatting."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pi_agent_core.sandbox import (
    CodemodeScriptError,
    SandboxError,
    SandboxLimits,
    ScriptSandbox,
)
from pi_agent_core.types import AgentToolResult
from pi_codemode.settings import CodemodeSettings
from pi_codemode.signatures import (
    FORBIDDEN_INNER_TOOLS,
    build_tools_catalog,
)

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

logger = logging.getLogger(__name__)

_PROGRESS_INTERVAL_S = 0.15
_MAX_PROGRESS_LINES = 20
_MAX_IMAGES_PER_RESULT = 8


class ToolError(RuntimeError):
    """Raised in host so the sandbox wire carries ``etype='ToolError'`` and ``tool_name``."""

    def __init__(self, message: str, *, tool_name: str = "") -> None:
        super().__init__(message)
        self.tool_name = tool_name


class ToolPermissionError(ToolError):
    """Raised in host when permission or policy blocks an inner tool call."""


class _GateLock:
    """Writer-preferring RW lock for inner tool gating under parallel vs sequential policies."""

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextlib.asynccontextmanager
    async def reader(self) -> AsyncIterator[None]:
        async with self._cond:
            while self._writer or self._waiting_writers > 0:
                await self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            async with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def writer(self) -> AsyncIterator[None]:
        async with self._cond:
            self._waiting_writers += 1
            acquired = False
            try:
                while self._writer or self._readers > 0:
                    await self._cond.wait()
                self._writer = True
                acquired = True
            finally:
                self._waiting_writers -= 1
                if not acquired:
                    self._cond.notify_all()
        try:
            yield
        finally:
            async with self._cond:
                self._writer = False
                self._cond.notify_all()


def _extract_result_parts(
    result: AgentToolResult,
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
    texts: list[str] = []
    images: list[dict[str, str]] = []
    for block in getattr(result, "content", None) or []:
        if isinstance(block, dict):
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text") or ""))
            elif btype == "image" and len(images) < _MAX_IMAGES_PER_RESULT:
                data = block.get("data")
                mime = block.get("mimeType") or "image/png"
                if isinstance(data, str) and data:
                    images.append({"data": data, "mimeType": str(mime)})
    raw_details = getattr(result, "details", None)
    details: dict[str, Any] = {}
    if raw_details is not None:
        try:
            encoded = json.loads(json.dumps(raw_details, default=str))
            if isinstance(encoded, dict):
                details = encoded
        except Exception:
            details = {}
    return "\n".join(texts), details, images


def _summarize_args(args: dict[str, Any]) -> str:
    if "path" in args:
        return str(args["path"])
    if "command" in args:
        cmd = " ".join(str(args["command"]).split())
        return cmd[:60] + ("..." if len(cmd) > 60 else "")
    if "pattern" in args:
        return str(args["pattern"])
    if "query" in args:
        return str(args["query"])
    return ""


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


class _CodemodeHost:
    def __init__(
        self,
        *,
        pi: ExtensionAPI,
        catalog: dict[str, dict[str, Any]],
        outer_call_id: str,
        run_id: str,
        cwd: str,
        signal: Any | None,
        on_update: Callable[[AgentToolResult], Any] | None,
    ) -> None:
        self._pi = pi
        self._catalog = catalog
        self._outer_call_id = outer_call_id
        self._run_id = run_id
        self._cwd = cwd
        self._signal = signal
        self._on_update = on_update
        self._gate_lock = _GateLock()
        self._seq = 0
        self._progress_lines: list[str] = []
        self._last_emit = 0.0
        self.tool_call_count = 0

    def _emit_progress(self, line: str, *, force: bool = False) -> None:
        self._progress_lines.append(line)
        if len(self._progress_lines) > _MAX_PROGRESS_LINES:
            self._progress_lines = self._progress_lines[-_MAX_PROGRESS_LINES:]
        if self._on_update is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < _PROGRESS_INTERVAL_S:
            return
        self._last_emit = now
        with contextlib.suppress(Exception):
            self._on_update(
                AgentToolResult(
                    content=[{"type": "text", "text": "\n".join(self._progress_lines)}],
                    details={"codemode_progress": True},
                )
            )

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name in FORBIDDEN_INNER_TOOLS:
            raise ToolPermissionError(
                f"Tool {tool_name!r} cannot be called from inside codemode",
                tool_name=tool_name,
            )
        entry = self._catalog.get(tool_name)
        if entry is None:
            raise ToolError(f"Unknown tool {tool_name!r}", tool_name=tool_name)
        if not isinstance(args, dict):
            raise ToolError(
                f"{tool_name}: arguments must be a dict\nExpected signature: {entry['signature']}",
                tool_name=tool_name,
            )

        self._seq += 1
        self.tool_call_count += 1
        call_id = f"codemode-{self._run_id}:{self._seq}"
        origin = {
            "kind": "codemode",
            "cwd": self._cwd,
            "parent_call_id": self._outer_call_id,
        }
        harmless = bool(entry.get("read_only"))
        need_exclusive = (not harmless) or self._pi.is_sequential_tool_policy

        arg_preview = _summarize_args(args)
        label = f"{tool_name} {arg_preview}".strip()
        self._emit_progress(f"▸ {label}")
        started = time.monotonic()

        ctx_mgr = self._gate_lock.writer() if need_exclusive else self._gate_lock.reader()
        try:
            async with ctx_mgr:
                result = await self._pi.invoke_tool(
                    call_id,
                    tool_name,
                    args,
                    origin=origin,
                    signal=self._signal,
                )
        except PermissionError as exc:
            elapsed = time.monotonic() - started
            self._emit_progress(f"✗ {label} ({elapsed:.2f}s)", force=True)
            raise ToolPermissionError(f"{tool_name}: {exc}", tool_name=tool_name) from exc
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            elapsed = time.monotonic() - started
            self._emit_progress(f"✗ {label} ({elapsed:.2f}s)", force=True)
            raise ToolError(
                f"{tool_name}: {exc}\nExpected signature: {entry['signature']}",
                tool_name=tool_name,
            ) from exc
        except Exception as exc:
            elapsed = time.monotonic() - started
            self._emit_progress(f"✗ {label} ({elapsed:.2f}s)", force=True)
            raise ToolError(f"{tool_name}: {exc}", tool_name=tool_name) from exc

        elapsed = time.monotonic() - started
        text, details, images = _extract_result_parts(result)
        if details.get("isError") is True or details.get("is_error") is True:
            self._emit_progress(f"✗ {label} ({elapsed:.2f}s)", force=True)
            raise ToolError(text or f"{tool_name} returned an error", tool_name=tool_name)

        self._emit_progress(f"✓ {label} ({elapsed:.2f}s)")
        return {"text": text, "details": details, "images": images}

    def log(self, message: str) -> None:
        return None

    def output(self, kind: str, data: Any) -> None:
        return None


def _format_outcome(
    *,
    outputs: Sequence[tuple[str, Any]],
    result: Any,
    logs: Sequence[str],
    tool_call_count: int,
    max_output_kb: int,
    layers: tuple[str, ...],
) -> AgentToolResult:
    text_parts: list[str] = [str(data) for kind, data in outputs if kind == "text" and str(data)]
    images: list[dict[str, str]] = [
        {"type": "image", "data": data["data"], "mimeType": data["mimeType"]}
        for kind, data in outputs
        if kind == "image" and isinstance(data, dict)
    ]

    if result is not None:
        if isinstance(result, str):
            text_parts.append(result)
        else:
            text_parts.append(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if logs:
        console = "\n".join(logs)
        text_parts.append(f"<console_output>\n{console}\n</console_output>")

    combined = "\n\n".join(text_parts)
    if not combined and not images:
        combined = "(codemode finished with no output)"

    max_bytes = max(1, max_output_kb) * 1024
    combined, truncated = _truncate_utf8(combined, max_bytes)
    if truncated:
        combined += (
            f"\n\n[Output truncated to {max_output_kb} KiB; filter or summarize inside the "
            "codemode script before returning.]"
        )

    content: list[Any] = [{"type": "text", "text": combined}]
    content.extend(images)
    return AgentToolResult(
        content=content,
        details={
            "tool_calls": tool_call_count,
            "truncated": truncated,
            "layers": list(layers),
        },
    )


class CodemodeRuntime:
    """Executes codemode scripts inside the shared ``ScriptSandbox``."""

    def __init__(
        self,
        pi: ExtensionAPI,
        get_settings: Callable[[], CodemodeSettings],
        *,
        audit_hook: bool = True,
    ) -> None:
        self._pi = pi
        self._get_settings = get_settings
        self._audit_hook = audit_hook

    async def execute(
        self,
        tool_call_id: str,
        script: str,
        *,
        timeout_ms: int | None = None,
        signal: Any | None = None,
        on_update: Callable[[AgentToolResult], Any] | None = None,
    ) -> AgentToolResult:
        if signal and getattr(signal, "aborted", False):
            raise RuntimeError("Codemode execution aborted")

        settings = self._get_settings()
        wall_seconds = settings.timeout_seconds
        if timeout_ms is not None:
            wall_seconds = min(settings.timeout_seconds, max(1.0, float(timeout_ms) / 1000.0))

        try:
            cwd = self._pi.cwd
        except RuntimeError:
            cwd = "."

        tools_info = self._pi.get_all_tools()
        catalog = build_tools_catalog(tools_info)
        run_id = uuid.uuid4().hex[:10]
        host = _CodemodeHost(
            pi=self._pi,
            catalog=catalog,
            outer_call_id=tool_call_id,
            run_id=run_id,
            cwd=cwd,
            signal=signal,
            on_update=on_update,
        )
        limits = SandboxLimits(
            memory_bytes=256 * 1024 * 1024,
            cpu_seconds=30,
            wall_seconds=wall_seconds,
            open_files=64,
            max_message_bytes=4 * 1024 * 1024,
            max_inflight_calls=16,
            max_log_lines=2000,
            max_log_chars=256 * 1024,
        )
        sandbox = ScriptSandbox(limits, audit_hook=self._audit_hook)

        run_task = asyncio.ensure_future(
            sandbox.run_codemode(
                script,
                tools_meta=catalog,
                host=host,
                max_tool_calls=settings.max_tool_calls,
            )
        )
        abort_task: asyncio.Task[None] | None = None
        if signal is not None and hasattr(signal, "wait_aborted"):

            async def _watch_abort() -> None:
                await signal.wait_aborted()
                if not run_task.done():
                    run_task.cancel()

            abort_task = asyncio.ensure_future(_watch_abort())

        try:
            outcome = await run_task
        except asyncio.CancelledError as exc:
            raise RuntimeError("Codemode execution aborted") from exc
        except CodemodeScriptError as exc:
            loc = f" (line {exc.line})" if exc.line is not None else ""
            raise RuntimeError(f"Codemode failed{loc}: {exc.error_type}: {exc}") from exc
        except SandboxError as exc:
            raise RuntimeError(f"Codemode failed: {exc}") from exc
        finally:
            if abort_task is not None:
                abort_task.cancel()
                with contextlib.suppress(BaseException):
                    await abort_task

        return _format_outcome(
            outputs=outcome.outputs,
            result=outcome.result,
            logs=outcome.logs,
            tool_call_count=host.tool_call_count,
            max_output_kb=settings.max_output_kb,
            layers=outcome.layers,
        )
