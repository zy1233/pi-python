"""ACP Agent: standard methods only; AgentHarness is the engine."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, RequestError
from acp.helpers import update_agent_message_text
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
from pydantic import ValidationError

from pi_agent_cli.config import (
    CliConfig,
    ModelChoice,
    PermissionMode,
    load_config,
    pi_home,
)
from pi_agent_cli.events import project_event, project_message_replay
from pi_agent_cli.extension_notices import failed_extensions_notice
from pi_agent_cli.extension_trust import (
    NoticeReason,
    ProjectTrust,
    ProjectTrustDecision,
    decide_project_trust,
    notice_reason,
    skipped_project_resources,
    unsaved_trust_notice,
    untrusted_project_notice,
)
from pi_agent_cli.factory import (
    api_key_for_choice,
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
from pi_agent_cli.session_list import (
    SESSION_LIST_PAGE_SIZE,
    decode_cursor,
    encode_cursor,
    read_session_previews,
)
from pi_agent_cli.trust_prompt import (
    trust_explanation,
    trust_options,
    trust_outcome_grants,
    trust_tool_call,
)
from pi_agent_cli.trust_store import TrustStore
from pi_agent_core.coding_tools.path_utils import normalize_host_path
from pi_agent_core.messages import ImageContent
from pi_agent_core.types import StreamFn
from pi_agent_harness import AgentHarness, AgentHarnessError, JsonlSessionRepo, Session

logger = logging.getLogger(__name__)

_AGENT_INFO = Implementation(name="pi-agent-cli", title="pi-python ACP agent", version="0.1.0")

# ACP Session Config Option id for the model selector (``session/set_config_option``).
MODEL_CONFIG_ID = "model"


@dataclass
class _SessionTrust:
    """One session's standing on its project's own extensions, saved workflows, prompt files
    and skills.

    ``trust`` is what the session's resource loaders consult. The project's files are hashed
    when the session opens; when the answer is the user's to give, the question is put after
    ``session/new`` (or load/resume) has been answered, and ``settled`` is set once it is
    closed: answered, or never to be asked.
    """

    trust: ProjectTrust
    # Why things were left out, for the notice (``None``: nothing was, or nothing to explain).
    why: NoticeReason | None = None
    # Extensions load, and the first prompt proceeds, only after this is set.
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    # Set by ``session/cancel`` to end the prompts held back by the question. Each cancel uses
    # one up and puts a fresh one here, so it never outlives the prompts it was meant for
    # (and a cancel that finds none waiting leaves nothing behind).
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    # Why the answer could not be saved (it holds for this session regardless).
    save_error: str | None = None
    # The deferred setup that puts the question; cancelled when the session goes away.
    task: asyncio.Task[Any] | None = None


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
        self._permission_locks: dict[str, asyncio.Lock] = {}
        self._prompts_in_flight: dict[str, int] = {}
        self._trust: dict[str, _SessionTrust] = {}
        self._project_locks: dict[str, asyncio.Lock] = {}

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
        # the response is flushed first.  (See zed#60199, zed#53161.)  The same goes
        # for the question about trusting the project, a request to the client.
        self._schedule_deferred_setup(session_id)
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
        self._schedule_deferred_setup(session_id)
        return LoadSessionResponse(
            config_options=self._config_options(session_id),
            field_meta=self._session_response_meta(session_id),
        )

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        after = None
        if cursor is not None:
            try:
                after = decode_cursor(cursor)
            except ValueError:
                raise RequestError.invalid_params(
                    {"cursor": cursor, "reason": "not a cursor this agent issued"}
                ) from None
        page = await self._repo.list_page(
            {"cwd": cwd, "limit": SESSION_LIST_PAGE_SIZE, "after": after}
        )
        listed = page.sessions
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
        next_cursor = encode_cursor(page.next_after) if page.next_after is not None else None
        return ListSessionsResponse(sessions=sessions, next_cursor=next_cursor)

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
        self._schedule_deferred_setup(session_id)
        return ResumeSessionResponse(
            config_options=self._config_options(session_id),
            field_meta=self._session_response_meta(session_id),
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        self._session_models.pop(session_id, None)
        harness = self._harnesses.pop(session_id, None)
        self._permission_locks.pop(session_id, None)
        self._drop_trust(session_id)
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
        # Until the user has answered the question about the project, nothing of it is
        # loaded, and a prompt would run without it (and then again with it, differently).
        if not await self._wait_for_trust(session_id, harness):
            return PromptResponse(stop_reason="cancelled")
        # Fallback: re-advertise commands if the deferred task was missed.
        if session_id not in self._commands_advertised:
            await self._advertise_commands(session_id)
        text, images = _prompt_to_text_images(prompt)
        message = await self._run_prompt(session_id, harness, text, images)
        if message is None:
            return PromptResponse(stop_reason="cancelled")
        return PromptResponse(stop_reason=_stop_reason(message))

    async def _run_prompt(
        self, session_id: str, harness: AgentHarness, text: str, images: list[ImageContent]
    ) -> Any | None:
        """Run a prompt; ``None`` when it was cancelled (or the session closed) while it waited.

        A turn the client did not start, an extension delivering a background result, holds
        the harness, and ACP v1 has no way to tell the client so: ``state_update`` is a v2
        draft notification, which neither the Python SDK nor the crate behind the TUI can
        parse, and this agent never claims protocol 2. A prompt that finds the harness busy
        with such a turn therefore waits for it instead of failing. A prompt that finds
        another one of the client's own in flight is a client error and is refused as busy.
        """
        in_flight = self._prompts_in_flight
        in_flight[session_id] = in_flight.get(session_id, 0) + 1
        try:
            while True:
                try:
                    return await harness.prompt(text, images or None)
                except Exception as exc:
                    if not _is_busy(exc):
                        raise
                    if in_flight[session_id] > 1:
                        raise RequestError.invalid_params({"reason": "busy"}) from exc
                if not await self._wait_until_idle(session_id, harness):
                    return None
        finally:
            in_flight[session_id] -= 1
            if not in_flight[session_id]:
                del in_flight[session_id]

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        state = self._trust.get(session_id)
        if state is not None:
            # Prompts may be held back by the question about the project, with no turn in the
            # harness to abort yet: this is what ends them (all of them: they hold this very
            # event). A prompt put afterwards waits on the fresh one.
            state.cancelled.set()
            state.cancelled = asyncio.Event()
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
        await harness.set_model(model_for_choice(choice, reasoning=self._config.model_reasoning))
        harness.get_api_key = api_key_for_choice(choice)
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
            self._permission_locks.pop(session_id, None)
            self._drop_trust(session_id)
            if harness is not None:
                # Deleting ends the session, like ``session/close``: stop the turn, then run
                # the extensions' cleanup (background workflows, worktrees).
                await harness.abort()
                await harness.close()
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
        return await self._repo.find(session_id)

    async def _bind_session(self, session_id: str, session: Session, cwd: str) -> None:
        cwd = normalize_host_path(cwd)

        async def on_tool_call(event: Any) -> dict[str, Any] | None:
            return await self._handle_tool_call(session_id, event)

        # Decide about the project's own extensions, saved workflows, prompt files and skills
        # before any of them is read.
        decision = await self._decide_trust(cwd)
        state = _SessionTrust(trust=ProjectTrust(decision), why=notice_reason(decision))
        resources = await load_session_resources(
            cwd=cwd, config=self._config, trusted=state.trust.trusted
        )
        choice = await self._restored_choice(session) or self._config.default_choice()
        harness = await create_session_harness(
            session=session,
            cwd=cwd,
            config=self._config,
            stream_fn=self._stream_fn,
            resources=resources,
            on_tool_call=on_tool_call,
            extensions=self._extensions,
            home=self._home,
            trust=state.trust,
            model_choice=choice,
        )
        self._session_models[session_id] = choice

        async def on_event(event: Any, signal: Any | None = None) -> None:
            await self._emit_updates(session_id, event)

        harness.subscribe(on_event)
        self._drop_trust(session_id)  # binding again: a question still open is for the old one
        self._harnesses[session_id] = harness
        self._session_cwds[session_id] = cwd
        self._trust[session_id] = state

        if decision.can_ask:
            # The user decides, and can only be asked once the client knows the session
            # (see ``_deferred_session_setup``). Extensions are code: they load with the answer.
            # (With no client to ask, that setup settles it as untrusted.)
            return
        state.settled.set()
        # Eagerly load extensions so slash commands are available.
        await harness.load_extensions()

    def _schedule_deferred_setup(self, session_id: str) -> None:
        """Fire-and-forget: finish setting the session up after the current response flushes."""
        task = asyncio.create_task(self._deferred_session_setup(session_id))
        state = self._trust.get(session_id)
        if state is not None:
            state.task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _deferred_session_setup(self, session_id: str) -> None:
        """Ask about the project if need be, advertise commands, report what did not load.

        Zed registers ACP sessions only after processing the response to
        ``session/new``.  Notifications sent *before* that response are
        silently dropped ("unknown session").  Yielding with ``sleep(0)``
        lets the response flush first.  (See zed-industries/zed#60199.)  A request
        to the client is no different.
        """
        await asyncio.sleep(0)
        await self._settle_project_trust(session_id)
        await self._advertise_commands(session_id)
        await self._notify_untrusted_project(session_id)
        await self._notify_failed_extensions(session_id)

    async def _settle_project_trust(self, session_id: str) -> None:
        """Put the question about the project to the user, act on the answer, load extensions.

        Whatever happens, the session ends up settled, so a prompt held back by the question
        is released: to run with the project's resources if the user said yes, without them
        if not (or if asking failed; the session is still useful).
        """
        state = self._trust.get(session_id)
        harness = self._harnesses.get(session_id)
        cwd = self._session_cwds.get(session_id)
        if state is None or harness is None or cwd is None or state.settled.is_set():
            return
        try:
            try:
                await self._ask_about_project(session_id, state, harness, cwd)
            except Exception:
                logger.warning("Could not ask about trusting %s", cwd, exc_info=True)
                state.why = "unasked"
            try:
                await harness.load_extensions()
            except Exception:
                logger.exception("Loading extensions failed for session %s", session_id)
        finally:
            state.settled.set()

    async def _ask_about_project(
        self, session_id: str, state: _SessionTrust, harness: AgentHarness, cwd: str
    ) -> None:
        """Decide afresh and, when it is the user's call, ask; trust the session on a yes.

        Nothing here is left half done: the session becomes trusted only at the end, after
        the answer was checked against what is on disk now.
        """
        if self._conn is None:
            return
        project = str(Path(cwd).resolve())
        # One question per project at a time. Sessions opened together in one directory would
        # otherwise stack identical dialogs, and the later ones can then rely on the answer.
        async with self._project_locks.setdefault(project, asyncio.Lock()):
            # Afresh: another session may have answered while this one waited, and the files
            # may have changed since this one opened.
            decision = await self._decide_trust(cwd)
            state.why = notice_reason(decision)
            if not decision.trusted:
                if not decision.can_ask or decision.fingerprint is None:
                    return  # nothing to put to the user (``never``, unpinnable, nothing gated)
                # What the user decides about goes first, as a message: the TUI shows a
                # request's title and options and nothing else of it. If it cannot be
                # delivered, nothing is asked (a yes or no about what one was not told).
                await self._conn.session_update(
                    session_id=session_id,
                    update=update_agent_message_text(trust_explanation(project, decision)),
                )
                answer = await self._conn.request_permission(
                    session_id=session_id,
                    tool_call=trust_tool_call(project, decision),
                    options=trust_options(),
                )
                if not trust_outcome_grants(answer.outcome):
                    state.why = "declined"
                    return
                # The dialog can stay open for a long time, and the answer is about what it
                # described. Trust nothing that is not that.
                current = await self._decide_trust(cwd)
                if current.fingerprint != decision.fingerprint:
                    state.why = "modified"
                    return
                state.save_error = await self._remember_trust(
                    project, decision.fingerprint, [item.describe() for item in decision.resources]
                )
        state.why = None
        state.trust.grant()
        harness.set_trust_project_extensions(True)
        await harness.set_resources(
            await load_session_resources(cwd=cwd, config=self._config, trusted=True)
        )

    async def _decide_trust(self, cwd: str) -> ProjectTrustDecision:
        """Decide about a project, in a worker thread: that reads and hashes files, which on
        the event loop would stall every other session and the client's traffic."""
        return await asyncio.to_thread(decide_project_trust, self._config, cwd, home=self._home)

    async def _remember_trust(
        self, project: str, fingerprint: str, resources: list[str]
    ) -> str | None:
        """Save a "yes" against the files it was about. The error text, if that failed: the
        session is trusted regardless, only the next one will have to ask again."""
        try:
            await asyncio.to_thread(
                TrustStore(self._home).remember, project, fingerprint, resources=resources
            )
        except OSError as exc:
            logger.warning("Could not save the trust decision for %s: %s", project, exc)
            return str(exc)
        return None

    async def _wait_for_trust(self, session_id: str, harness: AgentHarness) -> bool:
        """Hold a prompt back while the question about the project is open.

        ``False`` when the prompt must end as cancelled instead: ``session/cancel`` came in,
        or the session was closed, while it waited.
        """
        state = self._trust.get(session_id)
        if state is None or state.settled.is_set():
            return True
        cancel = state.cancelled  # this prompt's own: ``cancel`` swaps in a fresh one after use
        settled = asyncio.ensure_future(state.settled.wait())
        cancelled = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({settled, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            settled.cancel()
            cancelled.cancel()
        if cancel.is_set():
            return False
        return self._harnesses.get(session_id) is harness

    async def _wait_until_idle(self, session_id: str, harness: AgentHarness) -> bool:
        """Hold a prompt back while the harness runs a turn the client did not start.

        ``False`` when the prompt must end as cancelled instead: ``session/cancel`` came in,
        or the session was closed, while it waited.
        """
        state = self._trust.get(session_id)
        cancel = state.cancelled if state is not None else asyncio.Event()
        idle = asyncio.ensure_future(harness.wait_for_idle())
        cancelled = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({idle, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            idle.cancel()
            cancelled.cancel()
        if cancel.is_set():
            return False
        return self._harnesses.get(session_id) is harness

    def _drop_trust(self, session_id: str) -> None:
        """Forget a session's standing on its project. A question still open is withdrawn,
        and a prompt held back by it is let go."""
        state = self._trust.pop(session_id, None)
        if state is None:
            return
        if state.task is not None:
            state.task.cancel()
        state.settled.set()

    async def _notify_untrusted_project(self, session_id: str) -> None:
        """Tell the user which project extensions, saved workflows, prompt files and skills were
        left out because the project is untrusted (extensions were never imported), and how to
        enable them; and if a yes could not be saved, that it will have to be given again."""
        harness = self._harnesses.get(session_id)
        cwd = self._session_cwds.get(session_id)
        state = self._trust.get(session_id)
        if self._conn is None or harness is None or cwd is None or state is None:
            return
        notice = untrusted_project_notice(
            extensions=harness.skipped_extensions,
            resources=skipped_project_resources(
                self._config, cwd, trusted=state.trust.trusted, home=self._home
            ),
            cwd=cwd,
            home=self._home,
            why=state.why,
        )
        if notice is not None:
            await self._conn.session_update(
                session_id=session_id, update=update_agent_message_text(notice)
            )
        if state.save_error is not None:
            await self._conn.session_update(
                session_id=session_id,
                update=update_agent_message_text(
                    unsaved_trust_notice(error=state.save_error, cwd=cwd, home=self._home)
                ),
            )

    async def _notify_failed_extensions(self, session_id: str) -> None:
        """Tell the user which extensions failed to load: the session carries on without
        them, and stderr (where the traceback goes) is not something an ACP client shows."""
        harness = self._harnesses.get(session_id)
        if self._conn is None or harness is None:
            return
        notice = failed_extensions_notice(harness.failed_extensions)
        if notice is None:
            return
        await self._conn.session_update(
            session_id=session_id, update=update_agent_message_text(notice)
        )

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
            try:
                ac = AvailableCommand(
                    name=name,
                    description=cmd.description,
                    input=AvailableCommandInput(
                        root=UnstructuredCommandInput(hint="<args>"),
                    ),
                )
            except Exception as exc:
                # A command the schema rejects (an extension that put a non-text description
                # into the registry) must not take the other commands down with it. This is
                # sent again with every prompt until it succeeds, so one bad entry used to make
                # every ``session/prompt`` fail (audit P7-17).
                logger.warning("Command /%s is not advertised: %s", name, _why_invalid(exc))
                continue
            available.append(ac)
        if not available:
            return
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
        if not needs_permission(self._config.permission, getattr(event, "annotations", None)):
            return None
        if self._conn is None:
            return {"block": True, "reason": "No ACP client connected"}
        raw_input = dict(event.input or {})
        tool_call = permission_tool_call(
            event.toolCallId, name, raw_input, origin=getattr(event, "origin", None)
        )
        # One prompt at a time per session. Parallel workflow sub-agents would otherwise
        # open several at once, and a client that shows a single dialog would leave the
        # rest unanswered, hanging the workflow.
        async with self._permission_locks.setdefault(session_id, asyncio.Lock()):
            response = await self._conn.request_permission(
                session_id=session_id,
                tool_call=tool_call,
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


def _is_busy(exc: Exception) -> bool:
    """Whether *exc* is the harness saying it is running something else."""
    return isinstance(exc, AgentHarnessError) and exc.code == "busy"


def _why_invalid(exc: Exception) -> str:
    """A short reason a command was rejected, e.g. ``description: Input should be a valid
    string`` (pydantic's own text also repeats the value, which may be anything)."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
    return f"{type(exc).__name__}: {exc}"


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
