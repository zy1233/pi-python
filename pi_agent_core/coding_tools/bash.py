"""bash tool (port of pi ``bash.ts`` + ``utils/shell.ts``, minus TUI rendering).

Runs a command through a real shell, merging stdout+stderr in arrival order
into an :class:`OutputAccumulator` (tail-biased truncation, temp-file spill).
Terminal states mirror pi's wording exactly: ``Command exited with code N`` /
``Command timed out after N seconds`` / ``Command aborted`` — always prefixed
with whatever output was captured.

Like pi's ``waitForChildProcess``, completion is keyed on process exit, not
pipe EOF, so a detached descendant holding the pipe open cannot hang the tool.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool
from pi_agent_core.coding_tools.output_accumulator import OutputAccumulator, OutputSnapshot
from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
)
from pi_agent_core.types import AgentTool, AgentToolResult, AgentToolUpdateCallback

MAX_TIMEOUT_MS = 2_147_483_647
MAX_TIMEOUT_SECONDS = MAX_TIMEOUT_MS / 1000
UPDATE_THROTTLE_SECONDS = 0.1
# After process exit, how long to keep draining buffered pipe data before
# dropping the pipe (a detached descendant may hold the write end open).
DRAIN_GRACE_SECONDS = 0.25

_DESCRIPTION = (
    "Execute a bash command in the current working directory. Returns stdout and stderr. "
    f"Output is truncated to last {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB "
    "(whichever is hit first). If truncated, full output is saved to a temp file. "
    "Optionally provide a timeout in seconds."
)


class BashParams(BaseModel):
    command: str = Field(description="Bash command to execute")
    timeout: float | None = Field(
        default=None, description="Timeout in seconds (optional, no default timeout)"
    )


# --- shell resolution (port of pi utils/shell.ts getShellConfig) ---


@dataclass(frozen=True)
class ShellConfig:
    shell: str
    args: tuple[str, ...]
    command_transport: Literal["argv", "stdin"] = "argv"


def _is_legacy_wsl_bash_path(path: str) -> bool:
    normalized = path.replace("/", "\\").lower()
    return (
        re.fullmatch(r"[a-z]:\\windows\\(?:system32|sysnative)\\bash\.exe", normalized) is not None
    )


def _bash_shell_config(shell: str) -> ShellConfig:
    # Legacy WSL bash mangles argv; feed the command through stdin instead.
    if _is_legacy_wsl_bash_path(shell):
        return ShellConfig(shell=shell, args=("-s",), command_transport="stdin")
    return ShellConfig(shell=shell, args=("-c",))


def get_shell_config(shell_path: str | None = None) -> ShellConfig:
    """Resolve the shell: explicit path > bash > platform fallback.

    Windows resolution order is Git Bash in known locations, then ``bash`` on
    PATH, then — a declared deviation from pi, which errors out — ``cmd /c``
    so the tool stays usable on bash-less boxes.
    """
    if shell_path:
        if os.path.exists(shell_path):
            return _bash_shell_config(shell_path)
        raise ValueError(f"Custom shell path not found: {shell_path}")

    if sys.platform == "win32":
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_key)
            if base:
                candidate = os.path.join(base, "Git", "bin", "bash.exe")
                if os.path.exists(candidate):
                    return _bash_shell_config(candidate)
        bash = shutil.which("bash")
        if bash and os.path.exists(bash):
            return _bash_shell_config(bash)
        return ShellConfig(shell="cmd", args=("/c",))

    if os.path.exists("/bin/bash"):
        return _bash_shell_config("/bin/bash")
    bash = shutil.which("bash")
    if bash:
        return _bash_shell_config(bash)
    return ShellConfig(shell="sh", args=("-c",))


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its children (port of pi ``killProcessTree``)."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            subprocess.Popen(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        return
    import signal as signal_module

    # start_new_session=True made the child a group leader: pgid == pid.
    try:
        os.killpg(pid, signal_module.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal_module.SIGKILL)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def _try_assign_job(pid: int) -> int | None:
        """Put *pid* in a fresh job object so descendants are killable as one.

        Necessary on Windows because MSYS shells (Git Bash) emulate exec()
        by spawning a replacement process and exiting — walking the tree
        from the original pid (``taskkill /T``) then finds nothing, while
        job membership is inherited by every descendant regardless.
        """
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        handle = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            _kernel32.CloseHandle(job)
            return None
        assigned = _kernel32.AssignProcessToJobObject(job, handle)
        _kernel32.CloseHandle(handle)
        if not assigned:
            _kernel32.CloseHandle(job)
            return None
        return job


class _ProcessTreeKiller:
    """Terminates the whole subprocess tree; kill-switch only, never on success.

    Background children deliberately survive normal completion (pi allows
    ``server &`` to outlive the command): no kill-on-close limit is set, so
    ``close()`` merely releases the job handle.
    """

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._job = _try_assign_job(pid) if sys.platform == "win32" else None

    def kill(self) -> None:
        if self._job is not None:
            _kernel32.TerminateJobObject(self._job, 1)
        else:
            kill_process_tree(self._pid)

    def close(self) -> None:
        if self._job is not None:
            _kernel32.CloseHandle(self._job)
            self._job = None


# --- helpers ---


def _resolve_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Invalid timeout: must be a finite number of seconds")
    if timeout * 1000 > MAX_TIMEOUT_MS:
        raise ValueError(f"Invalid timeout: maximum is {MAX_TIMEOUT_SECONDS} seconds")
    return timeout


def _format_output(
    snapshot: OutputSnapshot, last_line_bytes: int, empty_text: str = "(no output)"
) -> tuple[str, dict[str, Any] | None]:
    """Append pi's truncation footer and build the ``details`` payload."""
    truncation = snapshot.truncation
    text = snapshot.content or empty_text
    details: dict[str, Any] | None = None
    if truncation.truncated:
        details = {
            "truncation": truncation.to_dict(),
            "fullOutputPath": snapshot.full_output_path,
        }
        start_line = truncation.total_lines - truncation.output_lines + 1
        end_line = truncation.total_lines
        path = snapshot.full_output_path
        if truncation.last_line_partial:
            text += (
                f"\n\n[Showing last {format_size(truncation.output_bytes)} of line {end_line} "
                f"(line is {format_size(last_line_bytes)}). Full output: {path}]"
            )
        elif truncation.truncated_by == "lines":
            text += (
                f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines}. "
                f"Full output: {path}]"
            )
        else:
            # pi hardcodes the default byte limit in this notice.
            text += (
                f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines} "
                f"({format_size(DEFAULT_MAX_BYTES)} limit). Full output: {path}]"
            )
    return text, details


def _append_status(text: str, status: str) -> str:
    return f"{text}\n\n{status}" if text else status


def _format_timeout_seconds(timeout: float) -> str:
    # Render like a JS number: 5.0 -> "5", 0.5 -> "0.5".
    return f"{timeout:g}"


class _UpdateEmitter:
    """100ms-throttled ``on_update`` snapshots (pi's scheduleOutputUpdate)."""

    def __init__(self, on_update: AgentToolUpdateCallback | None, output: OutputAccumulator):
        self._on_update = on_update
        self._output = output
        self._dirty = False
        self._last_emit = 0.0

    @property
    def enabled(self) -> bool:
        return self._on_update is not None

    def emit_initial(self) -> None:
        if self._on_update is not None:
            self._on_update(AgentToolResult(content=[], details=None))

    def mark_dirty(self) -> None:
        if self._on_update is None:
            return
        self._dirty = True
        if asyncio.get_running_loop().time() - self._last_emit >= UPDATE_THROTTLE_SECONDS:
            self.flush()

    def flush(self) -> None:
        if self._on_update is None or not self._dirty:
            return
        self._dirty = False
        self._last_emit = asyncio.get_running_loop().time()
        snapshot = self._output.snapshot(persist_if_truncated=True)
        details: dict[str, Any] | None = None
        if snapshot.truncation.truncated:
            details = {
                "truncation": snapshot.truncation.to_dict(),
                "fullOutputPath": snapshot.full_output_path,
            }
        self._on_update(
            AgentToolResult(
                content=[{"type": "text", "text": snapshot.content or ""}], details=details
            )
        )

    async def run_trailing_flusher(self) -> None:
        # Emits data that arrived inside a throttle window once it elapses.
        while True:
            await asyncio.sleep(UPDATE_THROTTLE_SECONDS)
            self.flush()


async def _wait_abort(signal: Any) -> None:
    wait_aborted = getattr(signal, "wait_aborted", None)
    if callable(wait_aborted):
        await wait_aborted()
        return
    # Plain `.aborted` flag without an event: degrade to polling (same
    # compatibility strategy as langchain_stream).
    while not getattr(signal, "aborted", False):
        await asyncio.sleep(0.05)


def _bash_subprocess_env(
    expose_session_environment: bool,
    prepare_env: Callable[[], dict[str, str]] | None,
) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "PI_SESSION_ID",
        "PI_SESSION_FILE",
        "PI_PROVIDER",
        "PI_MODEL",
        "PI_REASONING_LEVEL",
    ):
        env.pop(key, None)
    if expose_session_environment and prepare_env is not None:
        env.update(prepare_env())
    return env


def create_bash_tool(
    cwd: str,
    *,
    shell_path: str | None = None,
    expose_session_environment: bool = True,
    prepare_env: Callable[[], dict[str, str]] | None = None,
) -> AgentTool:
    """Build a bash tool bound to *cwd* (optionally pinning the shell binary)."""

    prompt_guidelines = (
        ["You can inspect PI_* environment variables for current model and session details."]
        if expose_session_environment
        else None
    )

    async def execute(
        _tool_call_id: str,
        params: BashParams,
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        output = OutputAccumulator(temp_file_prefix="pi-bash")
        emitter = _UpdateEmitter(on_update, output)
        emitter.emit_initial()

        timeout = _resolve_timeout(params.timeout)
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Command aborted")
        shell_config = get_shell_config(shell_path)
        if not os.path.exists(cwd):
            raise ValueError(
                f"Working directory does not exist: {cwd}\nCannot execute bash commands."
            )

        transport_stdin = shell_config.command_transport == "stdin"
        argv = [shell_config.shell, *shell_config.args]
        if not transport_stdin:
            argv.append(params.command)

        spawn_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdin": asyncio.subprocess.PIPE if transport_stdin else asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "env": _bash_subprocess_env(expose_session_environment, prepare_env),
        }
        if sys.platform == "win32":
            spawn_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            # New session -> new process group, so the whole tree is killable.
            spawn_kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(*argv, **spawn_kwargs)
        killer = _ProcessTreeKiller(proc.pid)

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    return
                output.append(chunk)
                emitter.mark_dirty()

        async def wait_exit() -> int:
            # asyncio's Process.wait() resolves only once every pipe has
            # disconnected, so a surviving descendant holding the write end
            # would stall it past the kill. Poll the returncode instead
            # (pi's waitForChildProcess: keyed on exit, not stream close).
            while proc.returncode is None:
                await asyncio.sleep(0.02)
            return proc.returncode

        exit_task = asyncio.create_task(wait_exit())
        pump_task = asyncio.create_task(pump())
        if transport_stdin:
            # Pump is already draining stdout, so a large command cannot
            # deadlock against a full output pipe while we feed stdin.
            assert proc.stdin is not None
            with contextlib.suppress(OSError, BrokenPipeError, ConnectionResetError):
                proc.stdin.write(params.command.encode("utf-8"))
                await proc.stdin.drain()
            with contextlib.suppress(OSError):
                proc.stdin.close()
        watchers: dict[asyncio.Task, str] = {}
        if signal is not None:
            watchers[asyncio.create_task(_wait_abort(signal))] = "abort"
        if timeout is not None:
            watchers[asyncio.create_task(asyncio.sleep(timeout))] = "timeout"
        flusher_task = (
            asyncio.create_task(emitter.run_trailing_flusher()) if emitter.enabled else None
        )

        kill_reason: str | None = None
        try:
            done, _pending = await asyncio.wait(
                {exit_task, *watchers}, return_when=asyncio.FIRST_COMPLETED
            )
            if exit_task not in done:
                fired = next(task for task in done if task in watchers)
                kill_reason = watchers[fired]
                killer.kill()
                await exit_task
            # Drain data already buffered in the pipe, then stop accepting
            # output: a detached descendant may never close the write end.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(pump_task, timeout=DRAIN_GRACE_SECONDS)
        finally:
            for task in watchers:
                task.cancel()
            if flusher_task is not None:
                flusher_task.cancel()
            if not pump_task.done():
                pump_task.cancel()
            if proc.returncode is None:
                # Reached on cancellation (e.g. loop-level tool_timeout):
                # never leave the subprocess tree orphaned.
                kill_reason = kill_reason or "cancelled"
                killer.kill()
            killer.close()
            if kill_reason is not None:
                # A straggler may hold the pipe open indefinitely; tear the
                # transport down now so no overlapped reads outlive us.
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    with contextlib.suppress(Exception):
                        transport.close()

        # pi checks abort before timeout, so abort wins when both fired.
        if signal is not None and getattr(signal, "aborted", False):
            kill_reason = "abort"

        output.finish()
        emitter.flush()
        snapshot = output.snapshot(persist_if_truncated=True)
        output.close_temp_file()

        if kill_reason == "abort":
            text, _ = _format_output(snapshot, output.get_last_line_bytes(), empty_text="")
            raise RuntimeError(_append_status(text, "Command aborted"))
        if kill_reason == "timeout":
            text, _ = _format_output(snapshot, output.get_last_line_bytes(), empty_text="")
            assert params.timeout is not None
            raise RuntimeError(
                _append_status(
                    text,
                    f"Command timed out after {_format_timeout_seconds(params.timeout)} seconds",
                )
            )

        text, details = _format_output(snapshot, output.get_last_line_bytes())
        exit_code = exit_task.result()
        # Negative returncode = killed by a signal (POSIX): pi reports null
        # and treats it as success, returning whatever output was captured.
        if exit_code != 0 and exit_code >= 0:
            raise RuntimeError(_append_status(text, f"Command exited with code {exit_code}"))
        return AgentToolResult(content=[{"type": "text", "text": text}], details=details)

    return CodingTool(
        name="bash",
        description=_DESCRIPTION,
        label="bash",
        parameters=BashParams,
        execute_fn=execute,
        prompt_snippet="Execute bash commands (ls, grep, find, etc.)",
        prompt_guidelines=prompt_guidelines or [],
    )
