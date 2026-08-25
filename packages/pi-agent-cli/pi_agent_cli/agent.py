"""ACP Agent: standard methods only; AgentHarness is the engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, RequestError
from acp.interfaces import Agent, Client
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    ClientCapabilities,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    ResourceContentBlock,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SseMcpServer,
    TextContentBlock,
)

from pi_agent_cli.config import CliConfig, load_config, pi_home
from pi_agent_cli.events import project_event
from pi_agent_cli.factory import create_session_harness, default_stream_fn
from pi_agent_cli.permissions import (
    PERMISSION_OPTIONS,
    needs_permission,
    outcome_allows,
    permission_tool_call,
)
from pi_agent_core.messages import ImageContent
from pi_agent_core.types import StreamFn
from pi_agent_harness import AgentHarness, JsonlSessionRepo, Session

_AGENT_INFO = Implementation(name="pi-agent-cli", title="pi-python ACP agent", version="0.1.0")


class PiAcpAgent(Agent):
    """Standard-ACP-only agent. Does not register any vendor extension methods."""

    _conn: Client | None

    def __init__(
        self,
        *,
        stream_fn: StreamFn | None = None,
        home: Path | str | None = None,
        config: CliConfig | None = None,
        repo: JsonlSessionRepo | None = None,
    ) -> None:
        self._conn = None
        self._home = pi_home(home)
        self._config = config if config is not None else load_config(self._home)
        self._stream_fn = stream_fn if stream_fn is not None else default_stream_fn()
        sessions_dir = self._home / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._repo = repo if repo is not None else JsonlSessionRepo(sessions_dir)
        self._harnesses: dict[str, AgentHarness] = {}
        self._abort_tasks: set[asyncio.Task[Any]] = set()

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=min(protocol_version, PROTOCOL_VERSION),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    image=True, audio=False, embedded_context=False
                ),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                    close=SessionCloseCapabilities(),
                ),
            ),
            auth_methods=[],
            agent_info=_AGENT_INFO,
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session = await self._repo.create({"cwd": cwd})
        session_id = (await session.get_metadata()).id
        await self._bind_session(session_id, session, cwd)
        return NewSessionResponse(session_id=session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        metadata = await self._find_metadata(session_id)
        if metadata is None:
            raise RequestError.resource_not_found(session_id)
        session = await self._repo.open(metadata)
        await self._bind_session(session_id, session, metadata.cwd or cwd)
        return LoadSessionResponse()

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        listed = await self._repo.list({"cwd": cwd} if cwd is not None else None)
        sessions = [
            SessionInfo(
                session_id=item.id,
                cwd=item.cwd,
                title=None,
                updated_at=item.createdAt,
            )
            for item in listed
        ]
        return ListSessionsResponse(sessions=sessions)

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        self._harnesses.pop(session_id, None)
        return CloseSessionResponse()

    async def prompt(
        self,
        session_id: str,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        **kwargs: Any,
    ) -> PromptResponse:
        harness = self._require_harness(session_id)
        text, images = _prompt_to_text_images(prompt)
        try:
            message = await harness.prompt(text, images or None)
        except Exception as exc:
            if type(exc).__name__ == "AgentHarnessError" and getattr(exc, "code", None) == "busy":
                raise RequestError.invalid_params({"reason": "busy"}) from exc
            raise
        return PromptResponse(stop_reason=_stop_reason(message))

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        harness = self._harnesses.get(session_id)
        if harness is None:
            return
        task = asyncio.create_task(harness.abort())
        self._abort_tasks.add(task)
        task.add_done_callback(self._abort_tasks.discard)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    def _require_harness(self, session_id: str) -> AgentHarness:
        harness = self._harnesses.get(session_id)
        if harness is None:
            raise RequestError.invalid_params(
                {"sessionId": session_id, "reason": "unknown session"}
            )
        return harness

    async def _find_metadata(self, session_id: str) -> Any:
        for item in await self._repo.list():
            if item.id == session_id:
                return item
        return None

    async def _bind_session(self, session_id: str, session: Session, cwd: str) -> None:
        async def on_tool_call(event: Any) -> dict[str, Any] | None:
            return await self._handle_tool_call(session_id, event)

        harness = create_session_harness(
            session=session,
            cwd=cwd,
            config=self._config,
            stream_fn=self._stream_fn,
            on_tool_call=on_tool_call,
        )

        async def on_event(event: Any, signal: Any | None = None) -> None:
            await self._emit_updates(session_id, event)

        harness.subscribe(on_event)
        self._harnesses[session_id] = harness

    async def _emit_updates(self, session_id: str, event: Any) -> None:
        if self._conn is None:
            return
        for update in project_event(event):
            await self._conn.session_update(session_id=session_id, update=update)

    async def _handle_tool_call(self, session_id: str, event: Any) -> dict[str, Any] | None:
        name = event.toolName
        if not needs_permission(name, self._config.permission):
            return None
        if self._conn is None:
            return {"block": True, "reason": "No ACP client connected"}
        raw_input = dict(event.input or {})
        response = await self._conn.request_permission(
            session_id=session_id,
            tool_call=permission_tool_call(event.toolCallId, name, raw_input),
            options=list(PERMISSION_OPTIONS),
        )
        if outcome_allows(response.outcome):
            return None
        return {"block": True, "reason": "User denied permission"}


def _prompt_to_text_images(
    prompt: list[Any],
) -> tuple[str, list[ImageContent]]:
    texts: list[str] = []
    images: list[ImageContent] = []
    for block in prompt:
        if isinstance(block, dict):
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text") or ""))
            elif btype == "image":
                images.append(
                    {
                        "type": "image",
                        "data": str(block.get("data") or ""),
                        "mimeType": str(
                            block.get("mimeType") or block.get("mime_type") or "image/png"
                        ),
                    }
                )
            continue
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(str(getattr(block, "text", "") or ""))
        elif btype == "image":
            images.append(
                {
                    "type": "image",
                    "data": str(getattr(block, "data", "") or ""),
                    "mimeType": str(
                        getattr(block, "mime_type", None)
                        or getattr(block, "mimeType", None)
                        or "image/png"
                    ),
                }
            )
    return "".join(texts), images


def _stop_reason(message: Any) -> str:
    reason = getattr(message, "stopReason", None) or "stop"
    if reason == "aborted":
        return "cancelled"
    if reason == "length":
        return "max_tokens"
    if reason == "error":
        return "refusal"
    return "end_turn"
