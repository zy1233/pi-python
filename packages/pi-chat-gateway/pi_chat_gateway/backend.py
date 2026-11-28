"""The agents behind the gateway: one ``zycode-agent`` process per project.

A project is a trust domain (Invariant 9): the extensions of a trusted project run inside its
agent's Python process, so sessions of different projects never share one. Sessions of the same
project share a process; the gateway never sends ``pi/yolo_mode_changed``, the one item that
moves every session of a process at once (audit 2026-11-28, RM-10).

An agent with no session that runs a turn is stopped after ``idle_agent_minutes`` (its stdin is
closed, the agent's own exit path). The next message starts it again and resumes the session.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import ClientCapabilities, Implementation

from . import __version__
from .config import GatewayConfig, ProjectConfig

logger = logging.getLogger(__name__)

INITIALIZE_TIMEOUT = 120.0
REQUEST_TIMEOUT = 120.0
CLIENT_CAPABILITIES = {"pi/capabilities": {"steer": True, "btw": True}}


class AgentError(RuntimeError):
    """The agent could not be started, or it refused a request the gateway needs."""


def resolve_agent_command(config: GatewayConfig) -> tuple[str, ...]:
    """``agent_command``; else ``zycode-agent`` next to this program, then on ``PATH``; else
    ``python -m pi_agent_cli`` of this environment (the gateway depends on ``pi-agent-cli-lc``)."""
    if config.agent_command:
        return config.agent_command
    names = ("zycode-agent.exe", "zycode-agent") if sys.platform == "win32" else ("zycode-agent",)
    for folder in (Path(sys.argv[0]).resolve().parent, Path(sys.executable).parent):
        for name in names:
            candidate = folder / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return (str(candidate),)
    found = shutil.which("zycode-agent")
    if found:
        return (found,)
    if importlib.util.find_spec("pi_agent_cli") is not None:
        return (sys.executable, "-m", "pi_agent_cli")
    raise AgentError("no agent found: install zycode-agent, or set agent_command in chat.toml")


@dataclass(frozen=True)
class AgentInfo:
    name: str
    version: str
    steer: bool
    btw: bool


@dataclass
class SessionStart:
    session_id: str
    mode: str | None
    config_options: list[Any]


def _mode_of(response: Any) -> str | None:
    modes = getattr(response, "modes", None)
    return getattr(modes, "current_mode_id", None) if modes is not None else None


class AgentProcess:
    """One running agent, as an ACP connection."""

    def __init__(
        self,
        project: ProjectConfig,
        command: Sequence[str],
        env: Mapping[str, str],
        client: Any,
        *,
        log_dir: Path,
        on_exit: Callable[[AgentProcess], None],
    ) -> None:
        self.project = project
        self._command = tuple(command)
        self._env = dict(env)
        self._client = client
        self._log_dir = log_dir
        self._on_exit = on_exit
        self._stack: contextlib.AsyncExitStack | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._closing = False
        self.conn: Any = None
        self.process: Any = None
        self.info = AgentInfo("", "", False, False)
        self.sessions: set[str] = set()
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None and not self._closing

    async def start(self) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / f"agent-{_safe(self.project.name)}.log"
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        stack = contextlib.AsyncExitStack()
        try:
            program, *args = self._command
            self.conn, self.process = await stack.enter_async_context(
                spawn_agent_process(
                    self._client,
                    program,
                    *args,
                    env=self._env,
                    cwd=str(self.project.path),
                    transport_kwargs={"stderr": log_fd, "shutdown_timeout": 5.0},
                )
            )
        except BaseException:
            await stack.aclose()
            raise
        finally:
            os.close(log_fd)
        self._stack = stack
        try:
            response = await asyncio.wait_for(
                self.conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(field_meta=CLIENT_CAPABILITIES),
                    client_info=Implementation(name="zycode-chat", version=__version__),
                ),
                INITIALIZE_TIMEOUT,
            )
        except BaseException:
            await self.close()
            raise
        meta = getattr(response, "field_meta", None) or {}
        caps = meta.get("pi/capabilities") if isinstance(meta, dict) else None
        caps = caps if isinstance(caps, dict) else {}
        agent = getattr(response, "agent_info", None)
        self.info = AgentInfo(
            name=getattr(agent, "name", "") or "",
            version=getattr(agent, "version", "") or "",
            steer=caps.get("steer") is True,
            btw=caps.get("btw") is True,
        )
        self._watcher = asyncio.get_running_loop().create_task(self._watch())
        logger.info(
            "agent for %s started: %s %s (steer=%s, btw=%s)",
            self.project.name,
            self.info.name,
            self.info.version,
            self.info.steer,
            self.info.btw,
        )

    async def _watch(self) -> None:
        await self.process.wait()
        if not self._closing:
            logger.warning(
                "the agent for %s exited (code %s)", self.project.name, self.process.returncode
            )
            self._on_exit(self)

    async def close(self) -> None:
        self._closing = True
        stack, self._stack = self._stack, None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()
        if self._watcher is not None and not self._watcher.done():
            self._watcher.cancel()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    # -- requests ------------------------------------------------------------------------------

    async def _call(self, coroutine: Any, timeout: float = REQUEST_TIMEOUT) -> Any:
        self.touch()
        return await asyncio.wait_for(coroutine, timeout)

    async def new_session(self) -> SessionStart:
        response = await self._call(
            self.conn.new_session(cwd=str(self.project.path), mcp_servers=[])
        )
        self.sessions.add(response.session_id)
        return SessionStart(
            response.session_id, _mode_of(response), list(response.config_options or [])
        )

    async def resume_session(self, session_id: str) -> SessionStart:
        response = await self._call(
            self.conn.resume_session(
                session_id=session_id, cwd=str(self.project.path), mcp_servers=[]
            )
        )
        self.sessions.add(session_id)
        return SessionStart(session_id, _mode_of(response), list(response.config_options or []))

    async def list_sessions(self) -> list[Any]:
        response = await self._call(self.conn.list_sessions(cwd=str(self.project.path)))
        return list(response.sessions or [])

    async def set_mode(self, session_id: str, mode: str) -> None:
        await self._call(self.conn.set_session_mode(session_id=session_id, mode_id=mode))

    async def set_config_option(self, session_id: str, config_id: str, value: str) -> list[Any]:
        response = await self._call(
            self.conn.set_config_option(config_id=config_id, session_id=session_id, value=value)
        )
        return list(getattr(response, "config_options", None) or [])

    async def prompt(self, session_id: str, blocks: list[Any]) -> Any:
        """No timeout: a turn runs as long as the agent works (and waits for approvals)."""
        self.touch()
        try:
            return await self.conn.prompt(session_id=session_id, prompt=blocks)
        finally:
            self.touch()

    async def cancel(self, session_id: str) -> None:
        self.touch()
        await self.conn.cancel(session_id=session_id)

    async def steer(self, session_id: str, steer_id: str, blocks: list[Any]) -> dict[str, Any]:
        params = {"sessionId": session_id, "steerId": steer_id, "prompt": _dump(blocks)}
        return await self._call(self.conn.ext_method("pi/session/steer", params))

    async def btw(
        self, session_id: str, btw_id: str, blocks: list[Any], history: list[dict[str, str]]
    ) -> dict[str, Any]:
        params = {
            "sessionId": session_id,
            "btwId": btw_id,
            "prompt": _dump(blocks),
            "history": history,
        }
        # The agent has its own timeout ([btw] timeout_seconds, at most 600 s).
        return await self._call(self.conn.ext_method("pi/session/btw", params), timeout=660.0)

    async def btw_cancel(self, session_id: str, btw_id: str) -> None:
        params = {"sessionId": session_id, "btwId": btw_id}
        await self._call(self.conn.ext_method("pi/session/btw_cancel", params))


def _dump(blocks: list[Any]) -> list[dict[str, Any]]:
    return [
        block.model_dump(by_alias=True, exclude_none=True)
        if hasattr(block, "model_dump")
        else block
        for block in blocks
    ]


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "project"


class AgentPool:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        client: Any,
        command: Sequence[str],
        env: Mapping[str, str],
        on_exit: Callable[[AgentProcess], None],
    ) -> None:
        self._config = config
        self._client = client
        self._command = tuple(command)
        self._env = dict(env)
        self._on_exit = on_exit
        self._agents: dict[str, AgentProcess] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def running(self, project: str) -> AgentProcess | None:
        agent = self._agents.get(project)
        return agent if agent is not None and agent.alive else None

    async def get(self, project: ProjectConfig) -> AgentProcess:
        async with self._locks.setdefault(project.name, asyncio.Lock()):
            agent = self.running(project.name)
            if agent is not None:
                return agent
            agent = AgentProcess(
                project,
                self._command,
                self._env,
                self._client,
                log_dir=self._config.home / "chat" / "logs",
                on_exit=self._exited,
            )
            try:
                await agent.start()
            except Exception as exc:
                raise AgentError(f"{type(exc).__name__}: {exc}") from exc
            self._agents[project.name] = agent
            return agent

    def _exited(self, agent: AgentProcess) -> None:
        if self._agents.get(agent.project.name) is agent:
            del self._agents[agent.project.name]
        self._on_exit(agent)
        asyncio.get_running_loop().create_task(agent.close())

    async def reap_idle(self, busy: Callable[[str], bool]) -> None:
        now = time.monotonic()
        for name, agent in list(self._agents.items()):
            if busy(name) or now - agent.last_used < self._config.idle_agent_seconds:
                continue
            logger.info("stopping the idle agent for %s", name)
            del self._agents[name]
            await agent.close()

    async def close_all(self) -> None:
        agents, self._agents = list(self._agents.values()), {}
        await asyncio.gather(*(agent.close() for agent in agents), return_exceptions=True)
