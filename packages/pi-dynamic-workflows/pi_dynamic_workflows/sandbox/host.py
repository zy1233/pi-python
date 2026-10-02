"""Run a workflow script in a process of its own (the host side of ``sandbox/child.py``).

The script runs in a child process that has nothing but the standard library: no inherited
environment, no inherited file descriptors, an empty working directory, and kernel limits on
memory, CPU time and (where the platform allows it) on starting more processes. It cannot do
anything by itself that matters; to run a sub-agent, log a line or learn what the budget
stands at it sends a request over a pipe, and the host (a ``ScriptHost``, in practice the
``WorkflowRuntime``) does it. Everything the child sends is checked here: a message that is
malformed, oversized or out of line ends the run and the child.

See ``docs/specs/2026-10-01-workflow-sandbox-design.md`` for the threat model and what each
platform enforces.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

CHILD_SCRIPT = Path(__file__).with_name("child.py")
PROTOCOL = 1
_STDERR_TAIL = 2048
# The same cut as ``child.py``'s: the script process cuts phase titles before it sends them.
_MAX_PHASE_TITLE = 500


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SandboxError(RuntimeError):
    """The script could not be run, or was stopped, by the sandbox (not by its own code)."""


class SandboxUnavailable(SandboxError):
    """The script's process could not be started or confined. Nothing was run."""


class SandboxLimitExceeded(SandboxError):
    """The script used more of something than it was given and was stopped."""


class SandboxCrashed(SandboxError):
    """The script's process ended without saying why (out of memory, killed, crashed)."""


class SandboxProtocolError(SandboxError):
    """The script's process sent something the protocol does not allow; it was stopped."""


class WorkflowScriptError(RuntimeError):
    """The script itself raised. ``str()`` is the script's own message."""

    def __init__(self, message: str, *, error_type: str = "Exception", line: int | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.line = line


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxLimits:
    """What a script's process is given. The defaults suit an orchestration script."""

    memory_bytes: int = 512 * 1024 * 1024  # address space (POSIX) / committed memory (Windows)
    cpu_seconds: int = 60  # CPU time the process itself may use, not time spent waiting
    wall_seconds: float | None = None  # overall; None: until the run is aborted
    open_files: int = 64
    max_message_bytes: int = 4 * 1024 * 1024
    max_inflight_calls: int = 64  # requests read ahead of the host answering them
    max_log_lines: int = 5000
    max_log_chars: int = 1024 * 1024
    max_phases: int = 200
    startup_seconds: float = 30.0


@dataclass
class ScriptOutcome:
    result: Any
    meta: dict[str, Any] | None
    layers: tuple[str, ...]


class ScriptHost(Protocol):
    """What the host does on the script's behalf."""

    async def agent(self, prompt: str, opts: dict[str, Any]) -> Any: ...

    def phase(self, title: str) -> None: ...

    def log(self, message: str) -> None: ...

    def spent(self) -> int: ...


# ---------------------------------------------------------------------------
# The child process
# ---------------------------------------------------------------------------


def _child_environment() -> dict[str, str]:
    """Nothing of the host's environment (API keys, tokens, proxies...) reaches the script.

    Windows cannot start sockets, which the event loop needs, without these.
    """
    if os.name != "nt":
        return {}
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR")
    return {name: os.environ[name] for name in keep if os.environ.get(name)}


def _interpreter() -> str:
    # On Windows a venv's python.exe starts the real interpreter as a second process, which
    # a job that allows one process would refuse; the base interpreter is one process.
    return getattr(sys, "_base_executable", None) or sys.executable


class _Child:
    def __init__(self, limits: SandboxLimits, *, audit_hook: bool) -> None:
        self.limits = limits
        self._audit_hook = audit_hook
        self.proc: asyncio.subprocess.Process | None = None
        self.timed_out = False
        self._tmpdir: str | None = None
        self._job: Any = None
        self._stderr = bytearray()
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._wall_clock: asyncio.TimerHandle | None = None

    async def start(self) -> None:
        limits = self.limits
        self._tmpdir = tempfile.mkdtemp(prefix="pi-workflow-")
        argv = [
            _interpreter(),
            "-I",  # isolated: no PYTHON* variables, no user site, no cwd on sys.path
            "-S",  # no site: nothing installed is importable
            "-B",  # write no bytecode
            "-X",
            "utf8",
            str(CHILD_SCRIPT),
            "--memory",
            str(limits.memory_bytes),
            "--cpu",
            str(limits.cpu_seconds),
            "--nofile",
            str(limits.open_files),
            "--max-message",
            str(limits.max_message_bytes),
        ]
        if not self._audit_hook:
            argv.append("--no-audit")
        spawn: dict[str, Any] = {}
        if os.name == "nt":
            import subprocess

            spawn["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            spawn["start_new_session"] = True
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=limits.max_message_bytes + 1024,
                env=_child_environment(),
                cwd=self._tmpdir,
                **spawn,
            )
        except NotImplementedError as exc:  # e.g. Windows' selector event loop
            await self.close()
            raise SandboxUnavailable(
                "this event loop cannot start subprocesses, so a workflow script cannot be run"
                " (on Windows that takes the default, proactor, loop)"
            ) from exc
        except OSError as exc:
            await self.close()
            raise SandboxUnavailable(f"could not start the workflow script process: {exc}") from exc
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())
        if os.name == "nt":
            try:
                from pi_dynamic_workflows.sandbox.winjob import JobObject

                self._job = JobObject(
                    memory_bytes=limits.memory_bytes, cpu_seconds=limits.cpu_seconds
                )
                self._job.assign(self.proc.pid)
            except OSError as exc:
                await self.close()
                raise SandboxUnavailable(
                    f"could not confine the workflow script process: {exc}"
                ) from exc
        if limits.wall_seconds is not None:
            self._wall_clock = asyncio.get_running_loop().call_later(
                limits.wall_seconds, self._on_wall_clock
            )

    def _on_wall_clock(self) -> None:
        self.timed_out = True
        self.kill()

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        with contextlib.suppress(Exception):
            while chunk := await self.proc.stderr.read(4096):
                self._stderr += chunk
                del self._stderr[:-_STDERR_TAIL]

    def stderr_text(self) -> str:
        return bytes(self._stderr).decode("utf-8", "replace").strip()

    async def send_line(self, data: bytes) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        async with self._write_lock:
            with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()

    async def read_message(self) -> dict[str, Any] | None:
        """The next message, or ``None`` when the process has closed its output."""
        assert self.proc is not None and self.proc.stdout is not None
        try:
            line = await self.proc.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise SandboxProtocolError(
                "the workflow script process sent a message larger than "
                f"{self.limits.max_message_bytes} bytes"
            ) from exc
        if not line.endswith(b"\n"):
            return None
        try:
            message = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise SandboxProtocolError("the workflow script process sent invalid JSON") from exc
        if not isinstance(message, dict) or not isinstance(message.get("t"), str):
            raise SandboxProtocolError("the workflow script process sent a malformed message")
        return message

    def kill(self) -> None:
        """Stop the process and anything it started. Safe to call at any time, repeatedly."""
        if self._job is not None:
            with contextlib.suppress(Exception):
                self._job.terminate()
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)  # the child leads its own session
        with contextlib.suppress(ProcessLookupError):
            proc.kill()

    async def exit_error(self) -> SandboxError:
        """Why the process went away without finishing the script."""
        assert self.proc is not None
        try:
            code = await asyncio.wait_for(self.proc.wait(), 5)
        except TimeoutError:
            self.kill()
            code = await self.proc.wait()
        if self._stderr_task is not None:
            await asyncio.wait([self._stderr_task], timeout=1)
        limits = self.limits
        if self.timed_out:
            return SandboxLimitExceeded(
                f"The workflow script ran past its time limit of {limits.wall_seconds:g} s"
            )
        detail = f"exit code {code}"
        if os.name != "nt" and code < 0:
            with contextlib.suppress(ValueError):
                detail = f"killed by {signal.Signals(-code).name}"
        text = (
            f"The workflow script's process ended unexpectedly ({detail}). It may have used "
            f"more than its {limits.memory_bytes // (1024 * 1024)} MiB of memory or "
            f"{limits.cpu_seconds} s of CPU time."
        )
        if tail := self.stderr_text():
            text += f" Its error output: {tail}"
        return SandboxCrashed(text)

    async def close(self) -> None:
        """Release everything: the process (killed if still running), pipes, job, directory."""
        if self._wall_clock is not None:
            self._wall_clock.cancel()
            self._wall_clock = None
        self.kill()
        proc = self.proc
        if proc is not None:
            if proc.stdin is not None:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 10)
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
            self._stderr_task = None
        if self._job is not None:
            self._job.close()
            self._job = None
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


def _encode(message: dict[str, Any]) -> bytes:
    return (
        json.dumps(message, ensure_ascii=True, separators=(",", ":"), default=str).encode() + b"\n"
    )


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------


def _field(message: dict[str, Any], name: str, kind: type | tuple[type, ...]) -> Any:
    value = message.get(name)
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise SandboxProtocolError(
            f"the workflow script process sent a {message['t']!r} message with a bad {name!r}"
        )
    return value


class ScriptSandbox:
    """Runs workflow scripts, one child process per run.

    *audit_hook* is on in every real use; the tests turn it off to look at the layers
    beneath it one at a time.
    """

    def __init__(self, limits: SandboxLimits | None = None, *, audit_hook: bool = True) -> None:
        self.limits = limits or SandboxLimits()
        self._audit_hook = audit_hook
        self.layers: tuple[str, ...] = ()

    async def run(
        self,
        script: str,
        *,
        args: Any,
        cwd: str,
        budget_total: int | None,
        host: ScriptHost,
    ) -> ScriptOutcome:
        request = _encode(
            {
                "t": "run",
                "script": script,
                "args": args,
                "cwd": cwd,
                "budget": {"total": budget_total, "spent": host.spent()},
            }
        )
        if len(request) > self.limits.max_message_bytes:
            raise SandboxLimitExceeded(
                f"The workflow script and its arguments are {len(request)} bytes; "
                f"at most {self.limits.max_message_bytes} can be sent to a script process"
            )
        child = _Child(self.limits, audit_hook=self._audit_hook)
        calls: dict[int, asyncio.Task[None]] = {}
        try:
            await child.start()
            await self._handshake(child)
            await child.send_line(request)
            return await self._serve(child, host, calls)
        finally:
            await self._finish(child, calls)

    async def _handshake(self, child: _Child) -> None:
        try:
            message = await asyncio.wait_for(child.read_message(), self.limits.startup_seconds)
        except TimeoutError:
            raise SandboxUnavailable(
                "the workflow script process did not start within "
                f"{self.limits.startup_seconds:g} s"
            ) from None
        if message is None:
            error = await child.exit_error()
            raise SandboxUnavailable(f"the workflow script process failed to start: {error}")
        if message["t"] != "ready" or message.get("protocol") != PROTOCOL:
            raise SandboxUnavailable("the workflow script process speaks another protocol")

    async def _serve(
        self, child: _Child, host: ScriptHost, calls: dict[int, asyncio.Task[None]]
    ) -> ScriptOutcome:
        limits = self.limits
        result: Any = None
        log_lines = log_chars = 0
        log_dropped = False
        phases: set[str] = set()
        while True:
            while len(calls) >= limits.max_inflight_calls:  # let the host catch up first
                await asyncio.wait(list(calls.values()), return_when=asyncio.FIRST_COMPLETED)
            message = await child.read_message()
            if message is None:
                raise await child.exit_error()
            kind = message["t"]
            if kind == "call":
                call_id = _field(message, "id", int)
                if _field(message, "fn", str) != "agent" or call_id in calls:
                    raise SandboxProtocolError("the workflow script process sent a bad call")
                prompt = _field(message, "prompt", str)
                opts = _field(message, "opts", dict)
                calls[call_id] = asyncio.ensure_future(
                    self._answer(child, host, calls, call_id, prompt, opts)
                )
            elif kind == "cancel":
                call = calls.get(_field(message, "id", int))
                if call is not None:
                    call.cancel()
            elif kind == "log":
                text = _field(message, "msg", str)
                log_lines += 1
                if log_lines <= limits.max_log_lines and log_chars < limits.max_log_chars:
                    text = text[: limits.max_log_chars - log_chars]
                    log_chars += len(text)
                    host.log(text)
                elif not log_dropped:
                    log_dropped = True
                    host.log("[further log output of this workflow was dropped: too much]")
            elif kind == "phase":
                title = _field(message, "title", str)[:_MAX_PHASE_TITLE]
                if title in phases or len(phases) < limits.max_phases:
                    phases.add(title)
                    host.phase(title)
            elif kind == "result":
                if "value" not in message:
                    raise SandboxProtocolError("the workflow script process sent a bad result")
                result = message["value"]
            elif kind == "started":
                layers = _field(message, "layers", list)
                self.layers = tuple(str(layer) for layer in layers)
                if child._job is not None:
                    self.layers += ("job-object",)
            elif kind == "done":
                meta = message.get("meta")
                if meta is not None and not isinstance(meta, dict):
                    raise SandboxProtocolError("the workflow script process sent a bad meta")
                return ScriptOutcome(result=result, meta=meta, layers=self.layers)
            elif kind == "error":
                line = message.get("line")
                raise WorkflowScriptError(
                    _field(message, "message", str),
                    error_type=_field(message, "etype", str),
                    line=line if isinstance(line, int) else None,
                )
            else:
                raise SandboxProtocolError(
                    f"the workflow script process sent an unknown message {kind!r}"
                )

    async def _answer(
        self,
        child: _Child,
        host: ScriptHost,
        calls: dict[int, asyncio.Task[None]],
        call_id: int,
        prompt: str,
        opts: dict[str, Any],
    ) -> None:
        try:
            try:
                value = await host.agent(prompt, opts)
                data = _encode(
                    {"t": "reply", "id": call_id, "ok": True, "value": value, "spent": host.spent()}
                )
                if len(data) > self.limits.max_message_bytes:
                    raise ValueError(
                        f"the answer is {len(data)} bytes; a script is sent at most "
                        f"{self.limits.max_message_bytes}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                data = _encode(
                    {
                        "t": "reply",
                        "id": call_id,
                        "ok": False,
                        "etype": type(exc).__name__,
                        "message": str(exc),
                        "spent": host.spent(),
                    }
                )
            await child.send_line(data)
        finally:
            calls.pop(call_id, None)

    async def _finish(self, child: _Child, calls: dict[int, asyncio.Task[None]]) -> None:
        child.kill()  # first: nothing more is read, and nothing keeps running or spending
        pending = list(calls.values())
        for call in pending:
            call.cancel()
        try:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await child.close()
