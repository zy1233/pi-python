"""Helpers that the tests of pi-mcp share."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pi_mcp.manager import Manager
from pi_mcp.settings import Settings

from pi_agent_core.extensions import ClientMcpServer

MOCK_SERVER = Path(__file__).with_name("mock_server.py")
WAIT = 20  # seconds: a broken test fails instead of hanging
SETTINGS = Settings(startup_timeout=30.0, call_timeout=30.0)
MOCK_TOOLS = {
    "echo",
    "add",
    "slow",
    "fail",
    "big",
    "readonly",
    "env_names",
    "env_value",
    "header",
    "pid",
    "cwd",
    "crash",
    "structured",
    "image",
    "noisy",
    "bash",
}


def stdio_server(
    name: str = "mock",
    *,
    command: str | None = None,
    args: tuple[str, ...] | None = None,
    env: tuple[tuple[str, str], ...] = (),
) -> ClientMcpServer:
    """A stdio server. By default it is the mock server, started with this Python."""
    return ClientMcpServer(
        name=name,
        transport="stdio",
        command=command or sys.executable,
        args=args if args is not None else (str(MOCK_SERVER),),
        env=env,
    )


def python_server(
    name: str, code: str, *extra: str, env: tuple[tuple[str, str], ...] = ()
) -> ClientMcpServer:
    """A stdio server that runs *code* with ``python -c``; *extra* become ``sys.argv[1:]``."""
    return stdio_server(name, args=("-c", code, *extra), env=env)


class FakePi:
    """What ``Manager`` needs of an ``ExtensionAPI``: it records what the manager does."""

    def __init__(self, home: Path, cwd: str | None = None) -> None:
        self.home = home
        self.cwd = cwd if cwd is not None else str(home)
        self.tools: dict[str, Any] = {}
        self.register_calls: list[str] = []
        self.notices: list[str] = []
        self.messages: list[str] = []

    def register_tool(self, definition: Any) -> None:
        self.register_calls.append(definition.name)
        self.tools[definition.name] = definition

    def notify(self, text: str) -> None:
        self.notices.append(text)

    def send_message(self, text: str) -> None:
        self.messages.append(text)


class Signal:
    """An abort signal like the one the agent loop gives a tool."""

    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()

    async def wait_aborted(self) -> None:
        await self._event.wait()


def text_of(result: Any) -> str:
    """The text blocks of a tool result, joined."""
    return "".join(b["text"] for b in result.content if b.get("type") == "text")


class ToolScript:
    """A ``stream_fn`` that asks for the given tool calls in the first turn, then answers in text.

    ``offered`` keeps the names of the tools each request to the model carried.
    """

    def __init__(self, *calls: tuple[str, dict[str, Any]]) -> None:
        self.calls = list(calls)
        self.offered: list[list[str]] = []

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        from pi_agent_core.event_stream import AssistantMessageEventStream
        from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
        from pi_agent_core.types import DoneEvent, StartEvent

        self.offered.append([getattr(tool, "name", "?") for tool in context.tools or []])
        answered = any(getattr(m, "role", None) == "toolResult" for m in context.messages)
        if not self.calls or answered:
            return await mock_text_stream(model, context, options)
        blocks: list[Any] = [
            {"type": "toolCall", "id": f"call_{i}", "name": name, "arguments": arguments}
            for i, (name, arguments) in enumerate(self.calls)
        ]
        partial = _base_partial(model, blocks)
        partial.stopReason = "toolUse"
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream


def block_text(block: Any) -> str:
    """The text of a content block (a dict or a model), or ``""``."""
    if isinstance(block, dict):
        return str(block.get("text", "")) if block.get("type") == "text" else ""
    return str(getattr(block, "text", "") or "")


class Lab:
    """A fake session (``FakePi``) and the managers started in it. All are closed at the end."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.pi = FakePi(tmp_path / "home", cwd=str(tmp_path))
        self.managers: list[Manager] = []

    async def start(
        self, *servers: ClientMcpServer, settings: Settings = SETTINGS, wait: bool = True
    ) -> Manager:
        manager = Manager(self.pi, servers, settings=settings)
        self.managers.append(manager)
        manager.start()
        if wait:
            await self.settled(manager)
        return manager

    @staticmethod
    async def settled(manager: Manager) -> None:
        """Wait until every server of *manager* is ready or has failed."""
        await asyncio.wait_for(
            asyncio.gather(*(c.wait_started() for c in manager.connections.values())), 60
        )

    async def call(
        self, tool: str, params: dict[str, Any] | None = None, *, signal: Signal | None = None
    ) -> Any:
        """Run a registered tool the way the harness does."""
        definition = self.pi.tools[tool]
        return await definition.execute("call-1", params or {}, signal, None)

    async def text(
        self, tool: str, params: dict[str, Any] | None = None, *, signal: Signal | None = None
    ) -> str:
        return text_of(await self.call(tool, params, signal=signal))

    def log(self, name: str = "mock") -> str:
        return (self.pi.home / "logs" / "mcp" / f"{name}.log").read_text(encoding="utf-8")

    async def server_pid(self, tool: str = "mcp__mock__pid") -> int:
        return int(await self.text(tool))

    async def aclose(self) -> None:
        for manager in self.managers:
            await manager.close()


def kill_pid(pid: int) -> None:
    """End a process at once (on Windows ``SIGTERM`` is ``TerminateProcess``)."""
    os.kill(pid, signal.SIGTERM)


def kill_process(process: subprocess.Popen[bytes]) -> None:
    """End a child process of a test, and wait for it."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)


async def until(condition: Callable[[], Any], *, timeout: float = WAIT) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


def process_exists(pid: int) -> bool:
    """Whether a process with this id is running (and not a finished one that is not reaped)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def wait_gone(pid: int, *, timeout: float = WAIT) -> None:
    await until(lambda: not process_exists(pid), timeout=timeout)


async def read_pid(path: Path, *, timeout: float = WAIT) -> int:
    """The process id that a test server wrote to *path* when it started."""
    await until(lambda: path.exists() and path.read_text().strip().isdigit(), timeout=timeout)
    return int(path.read_text().strip())
