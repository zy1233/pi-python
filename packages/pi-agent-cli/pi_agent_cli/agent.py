"""ACP Agent: standard methods only; AgentHarness is the engine."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, RequestError
from acp.interfaces import Agent, Client
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    AvailableCommand,
    AvailableCommandInput,
    AvailableCommandsUpdate,
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
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfo,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SseMcpServer,
    TextContentBlock,
    UnstructuredCommandInput,
)

from pi_agent_cli.config import (
    CliConfig,
    ModelChoice,
    PermissionMode,
    api_key_getter,
    load_config,
    pi_home,
)
from pi_agent_cli.events import project_event, project_message_replay
from pi_agent_cli.factory import (
    create_session_harness,
    default_stream_fn,
    load_session_resources,
    model_for_choice,
)
from pi_agent_cli.permissions import (
    PERMISSION_OPTIONS,
    needs_permission,
    outcome_allows,
    permission_tool_call,
)
from pi_agent_cli.session_list import read_session_previews
from pi_agent_core.coding_tools.path_utils import normalize_host_path
from pi_agent_core.messages import ImageContent
from pi_agent_core.types import StreamFn
from pi_agent_harness import AgentHarness, JsonlSessionRepo, Session

logger = logging.getLogger(__name__)

_AGENT_INFO = Implementation(name="pi-agent-cli", title="pi-python ACP agent", version="0.1.0")

# ACP Session Config Option id for the model selector (``session/set_config_option``).
MODEL_CONFIG_ID = "model"


class PiAcpAgent(Agent):
    """Standard-ACP-only agent. Does not register any vendor extension methods."""

    _conn: Client | None
    _commands_advertised: set[str]

    def __init__(
        self,
        *,
        stream_fn: StreamFn | None = None,
        home: Path | str | None = None,
        config: CliConfig | None = None,
        repo: JsonlSessionRepo | None = None,
        extensions: list[Any] | None = None,
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
        self._session_cwds: dict[str, str] = {}
        self._session_models: dict[str, ModelChoice] = {}
        self._client_capabilities: ClientCapabilities | None = None
        self._client_info: Implementation | None = None
        self._extensions: list[Any] = extensions or []
        self._commands_advertised: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        self._client_capabilities = client_capabilities
        self._client_info = client_info
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
        # Defer available_commands_update: Zed only registers the session
        # AFTER it receives NewSessionResponse, so notifications sent before
        # that are silently dropped.  Schedule via create_task + sleep(0) so
        # the response is flushed first.  (See zed#60199, zed#53161.)
        self._schedule_deferred_advertise(session_id)
        return NewSessionResponse(
            session_id=session_id,
            config_options=self._config_options(session_id),
            field_meta=self._session_response_meta(session_id),
        )

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
        if self._conn is not None:
            context = await session.build_context()
            for msg in context.messages:
                for update in project_message_replay(msg):
                    await self._conn.session_update(session_id=session_id, update=update)
        self._schedule_deferred_advertise(session_id)
        return LoadSessionResponse(
            config_options=self._config_options(session_id),
            field_meta=self._session_response_meta(session_id),
        )

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        # One page: the pager does not follow ``nextCursor``, so ``cursor`` is not used.
        listed = await self._repo.list({"cwd": cwd} if cwd is not None else None)
        previews = await asyncio.to_thread(
            read_session_previews, [(item.path, item.createdAt) for item in listed]
        )
        sessions = [
            SessionInfo(
                session_id=item.id,
                cwd=item.cwd,
                title=preview.title,
                updated_at=preview.updated_at,
            )
            for item, preview in zip(listed, previews, strict=True)
        ]
        return ListSessionsResponse(sessions=sessions)

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        metadata = await self._find_metadata(session_id)
        if metadata is None:
            raise RequestError.resource_not_found(session_id)
        session = await self._repo.open(metadata)
        await self._bind_session(session_id, session, metadata.cwd or cwd)
        # ACP session/resume intentionally does not replay history.
        self._schedule_deferred_advertise(session_id)
        return ResumeSessionResponse(
            config_options=self._config_options(session_id),
            field_meta=self._session_response_meta(session_id),
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        self._session_models.pop(session_id, None)
        harness = self._harnesses.pop(session_id, None)
        if harness is not None:
            await harness.close()
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
        # Fallback: re-advertise commands if the deferred task was missed.
        if session_id not in self._commands_advertised:
            await self._advertise_commands(session_id)
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

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        """``session/set_config_option``: only the ``model`` selector is supported."""
        harness = self._require_harness(session_id)
        if config_id != MODEL_CONFIG_ID:
            raise RequestError.invalid_params(
                {"configId": config_id, "reason": "unknown config option"}
            )
        choice = self._choice_by_id(value) if isinstance(value, str) else None
        if choice is None:
            raise RequestError.invalid_params(
                {"configId": config_id, "value": value, "reason": "unknown model"}
            )
        await harness.set_model(model_for_choice(choice))
        harness.get_api_key = api_key_getter(choice.api_key_env)
        self._session_models[session_id] = choice
        return SetSessionConfigOptionResponse(config_options=self._config_options(session_id))

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "pi/session/delete":
            session_id_raw = params.get("sessionId", params.get("session_id"))
            if not isinstance(session_id_raw, str) or not session_id_raw.strip():
                raise RequestError.invalid_params(
                    {"reason": "sessionId is required", "method": method}
                )
            session_id = session_id_raw.strip()
            metadata = await self._find_metadata(session_id)
            harness = self._harnesses.pop(session_id, None)
            if harness is not None:
                await harness.abort()
            # Idempotent delete: missing session still returns success.
            if metadata is not None:
                await self._repo.delete(metadata)
            return {"sessionId": session_id, "deleted": True}
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        mode = _permission_mode_from_notification(method, params)
        if mode is not None:
            self._config = replace(self._config, permission=mode)
        return None

    def _choice_by_id(self, model_id: str) -> ModelChoice | None:
        for choice in self._config.model_choices():
            if choice.id == model_id:
                return choice
        return None

    def _current_choice(self, session_id: str) -> ModelChoice:
        return self._session_models.get(session_id) or self._config.default_choice()

    def _config_options(self, session_id: str) -> list[SessionConfigOptionSelect]:
        """ACP Session Config Options: a ``model`` select (category ``model``) for ``/model``."""
        choices = list(self._config.model_choices())
        current = self._current_choice(session_id)
        if all(choice.id != current.id for choice in choices):
            choices.insert(0, current)  # e.g. a persisted model no longer listed in agent.toml
        return [
            SessionConfigOptionSelect(
                id=MODEL_CONFIG_ID,
                name="Model",
                category="model",
                type="select",
                current_value=current.id,
                options=[
                    SessionConfigSelectOption(value=choice.id, name=choice.name or choice.id)
                    for choice in choices
                ],
            )
        ]

    def _session_response_meta(self, session_id: str) -> dict[str, Any] | None:
        """Legacy ``pi/*`` model hints (kept for clients that predate config options)."""
        current = self._current_choice(session_id)
        model_id = current.id.strip()
        if not model_id:
            return None
        meta: dict[str, Any] = {
            "pi/currentModelId": model_id,
            "pi/currentModelDisplayName": current.name or model_id,
        }
        provider = (current.provider or "").strip()
        if provider:
            meta["pi/provider"] = provider
        return meta

    async def _restored_choice(self, session: Session) -> ModelChoice | None:
        """Model persisted in the session, if it is still configured.

        Taken from the latest model change or assistant message.
        """
        context = await session.build_context()
        persisted = context.model or {}
        model_id = persisted.get("modelId")
        if not model_id:
            return None
        provider = persisted.get("provider")
        for choice in self._config.model_choices():
            if choice.id == model_id and (not provider or choice.provider == provider):
                return choice
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
        cwd = normalize_host_path(cwd)

        async def on_tool_call(event: Any) -> dict[str, Any] | None:
            return await self._handle_tool_call(session_id, event)

        resources = await load_session_resources(cwd=cwd, config=self._config)
        choice = await self._restored_choice(session) or self._config.default_choice()
        harness = await create_session_harness(
            session=session,
            cwd=cwd,
            config=self._config,
            stream_fn=self._stream_fn,
            resources=resources,
            on_tool_call=on_tool_call,
            extensions=self._extensions,
            model_choice=choice,
        )
        self._session_models[session_id] = choice

        async def on_event(event: Any, signal: Any | None = None) -> None:
            await self._emit_updates(session_id, event)

        harness.subscribe(on_event)
        self._harnesses[session_id] = harness
        self._session_cwds[session_id] = cwd

        # Eagerly load extensions so slash commands are available.
        await harness.load_extensions()

    def _schedule_deferred_advertise(self, session_id: str) -> None:
        """Fire-and-forget: advertise commands after the current response flushes."""
        task = asyncio.create_task(self._deferred_advertise_commands(session_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _deferred_advertise_commands(self, session_id: str) -> None:
        """Advertise commands after yielding the event loop.

        Zed registers ACP sessions only after processing the response to
        ``session/new``.  Notifications sent *before* that response are
        silently dropped ("unknown session").  Yielding with ``sleep(0)``
        lets the response flush first.  (See zed-industries/zed#60199.)
        """
        await asyncio.sleep(0)
        await self._advertise_commands(session_id)

    async def _advertise_commands(self, session_id: str) -> None:
        """Send ``AvailableCommandsUpdate`` so ACP clients show slash commands."""
        if self._conn is None:
            return
        harness = self._harnesses.get(session_id)
        if harness is None:
            return
        commands = harness.extension_registry.get_commands()
        if not commands:
            return
        available: list[AvailableCommand] = []
        for name, cmd in commands.items():
            ac = AvailableCommand(
                name=name,
                description=cmd.description,
                input=AvailableCommandInput(
                    root=UnstructuredCommandInput(hint="<args>"),
                ),
            )
            available.append(ac)
        logger.debug(
            "_advertise_commands: sending %d commands: %s",
            len(available),
            [c.name for c in available],
        )
        await self._conn.session_update(
            session_id=session_id,
            update=AvailableCommandsUpdate(
                session_update="available_commands_update",
                available_commands=available,
            ),
        )
        self._commands_advertised.add(session_id)

    async def _emit_updates(self, session_id: str, event: Any) -> None:
        if self._conn is None:
            return
        cwd = self._session_cwds.get(session_id)
        for update in project_event(event, cwd=cwd):
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
    """Map internal AssistantMessage stopReason to ACP PromptResponse stop_reason.

    ACP stopReason enum: 'end_turn' | 'max_tokens' | 'max_turn_requests' | 'refusal' | 'cancelled'.
    Internal stopReason values: 'stop' | 'length' | 'toolUse' | 'error' | 'aborted'.
    """
    reason = getattr(message, "stopReason", None) or "stop"
    if reason == "aborted":
        return "cancelled"
    if reason == "length":
        return "max_tokens"
    if reason == "error":
        err = str(getattr(message, "errorMessage", "") or "")
        if any(w in err.lower() for w in ("refus", "policy", "filter", "safety")):
            return "refusal"
        return "end_turn"
    return "end_turn"


def _permission_mode_from_notification(
    method: str, params: dict[str, Any] | None
) -> PermissionMode | None:
    """Best-effort permission mode sync for live sessions.

    TUI settings changes are sent as extension notifications; we accept known
    suffixes and update in-memory mode so the next tool call in this process
    uses the new policy immediately.
    """
    suffix = method.rsplit("/", 1)[-1].strip().lower()
    if suffix not in {"yolo_mode_changed", "permission_mode_changed"}:
        return None
    payload = params or {}
    raw = payload.get("permission_mode")
    if raw is None:
        raw = payload.get("permissionMode")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if normalized not in {"ask", "auto", "always-approve"}:
        return None
    return cast(PermissionMode, normalized)
