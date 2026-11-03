"""ACP Agent: standard methods only; AgentHarness is the engine."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, RequestError
from acp.helpers import update_agent_message_text
from acp.interfaces import Agent, Client
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    AuthenticateResponse,
    AvailableCommand,
    AvailableCommandInput,
    AvailableCommandsUpdate,
    ClientCapabilities,
    CloseSessionResponse,
    ConfigOptionUpdate,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
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
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
    UnstructuredCommandInput,
)
from pydantic import ValidationError

from pi_agent_cli.auth_methods import (
    SETUP_METHOD_ID,
    auth_required_error,
    setup_auth_method,
    terminal_auth_form,
)
from pi_agent_cli.auto_mode import (
    AUTO_EXTENSION_NAME,
    AutoSession,
    BashLimitRegistry,
    config_notes_message,
    config_problems_message,
    entry_message,
    problems_message,
)
from pi_agent_cli.auto_reasons import Reason
from pi_agent_cli.client_mcp import client_mcp_servers, mcp_extension_installed
from pi_agent_cli.config import (
    CliConfig,
    ModelChoice,
    PermissionMode,
    config_problem,
    load_config,
    load_local_env,
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
from pi_agent_cli.mcp_notice import ignored_mcp_servers_notice
from pi_agent_cli.permission_modes import (
    MODE_CONFIG_ID,
    mode_config_option,
    mode_from_notification,
    mode_from_session_meta,
    selectable_mode,
    session_mode_state,
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
from pi_agent_harness import (
    AgentHarness,
    AgentHarnessError,
    DeferredJsonlSessionStorage,
    JsonlSessionRepo,
    Session,
)

logger = logging.getLogger(__name__)

# The package this agent ships in. Its version is the version of the release.
_DISTRIBUTION = "pi-agent-cli-lc"


def _installed_version() -> str:
    """The version of the installed package, for ACP ``agentInfo.version``.

    Without package metadata (a checkout that was never installed) the answer is
    ``0.0.0+unknown``, which is still a valid version.
    """
    try:
        return importlib.metadata.version(_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


_AGENT_INFO = Implementation(
    name="pi-agent-cli", title="pi-python ACP agent", version=_installed_version()
)

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
        # The terminal login (``--setup``) is for an agent that reads its model and key from the
        # files of the home folder. Code that gives its own configuration or stream function
        # (tests, an embedding program) has other ways to set them.
        self._offers_setup = stream_fn is None and config is None
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
        # Held while a session is created under an id the client chose, so two requests for the
        # same id cannot both find it free.
        self._chosen_id_lock = asyncio.Lock()
        # The ids that the client chose, or asked for again (``_reopen_blank_session``), for
        # sessions that are open here and may have no file yet (a new session is written with
        # its first message). The files cannot say that such an id is taken, so this does. An id
        # leaves when its session is closed or deleted. (The id that the agent itself gives to a
        # new session is not here: the check at the first message costs a directory listing, and
        # it would be paid by every session.)
        self._reserved_ids: set[str] = set()
        # Lines for the user that an extension sent (``ExtensionAPI.notify``). The client drops
        # what it gets before it knows the session (zed#60199), so they are held until the
        # deferred setup of the session has run, then sent in order.
        self._notices_open: set[str] = set()
        self._held_notices: dict[str, list[str]] = {}
        # The permission mode of each open session, and the Auto state that goes with it. The
        # mode is per session; the legacy notification ``pi/yolo_mode_changed`` moves them all.
        self._session_modes: dict[str, PermissionMode] = {}
        self._autos: dict[str, AutoSession] = {}
        # Sessions whose Auto mode came from ``permission = "auto"`` in ``agent.toml`` (which
        # meant "no question" before): their entry message says what the setting means now.
        self._auto_from_config: set[str] = set()
        self._mode_lock = asyncio.Lock()

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
        remote_mcp = mcp_extension_installed()
        setup_method = (
            setup_auth_method(client_capabilities, self._home) if self._offers_setup else None
        )
        return InitializeResponse(
            protocol_version=min(protocol_version, PROTOCOL_VERSION),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    image=True, audio=False, embedded_context=False
                ),
                # stdio servers are always allowed in ACP. A client passes remote ones only when
                # the agent says it takes them, and only the ``pi-mcp`` extension does.
                mcp_capabilities=McpCapabilities(http=remote_mcp, sse=remote_mcp),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                    close=SessionCloseCapabilities(),
                ),
            ),
            auth_methods=[setup_method] if setup_method is not None else [],
            agent_info=_AGENT_INFO,
        )

    def _refresh_config(self) -> None:
        """Read ``agent.toml`` and ``local.env`` again when what the agent has cannot run a turn.

        ``--setup`` may have run in a terminal of the client while this process was up. Only a
        configuration that was read from the files is read again, and only while it is unusable,
        so a working one never changes under a running agent.
        """
        if not self._offers_setup or config_problem(self._config) is None:
            return
        load_local_env(self._home)
        try:
            self._config = load_config(self._home)
        except (OSError, ValueError):
            logger.warning("could not read the configuration again", exc_info=True)

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        """The client ran the terminal login and asks whether it worked."""
        if not self._offers_setup or method_id != SETUP_METHOD_ID:
            raise RequestError.invalid_params(
                {"methodId": method_id, "reason": "not an authentication method of this agent"}
            )
        self._refresh_config()
        problem = config_problem(self._config)
        if problem is not None:
            raise auth_required_error(problem)
        return AuthenticateResponse()

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        # A client that can run the terminal login is sent to it when a session could not work.
        # A client that cannot gets the session, and the first turn says what is missing.
        self._refresh_config()
        problem = config_problem(self._config) if self._offers_setup else None
        if problem is not None and terminal_auth_form(self._client_capabilities) is not None:
            raise auth_required_error(problem)
        chosen_id = _chosen_session_id(kwargs)
        # The session has no file yet: it is written with its first message. A session in which
        # nobody typed anything leaves nothing to list, and nothing for ``--continue`` to take.
        if chosen_id is None:
            session = await self._repo.create({"cwd": cwd, "deferred": True})
        else:
            session = await self._create_session_with_id(chosen_id, cwd)
        session_id = (await session.get_metadata()).id
        try:
            await self._bind_session(session_id, session, cwd, mcp_servers, meta=kwargs)
        except BaseException:
            self._reserved_ids.discard(session_id)
            raise
        # Defer available_commands_update: Zed only registers the session
        # AFTER it receives NewSessionResponse, so notifications sent before
        # that are silently dropped.  Schedule via create_task + sleep(0) so
        # the response is flushed first.  (See zed#60199, zed#53161.)  The same goes
        # for the question about trusting the project, a request to the client.
        self._schedule_deferred_setup(session_id)
        return NewSessionResponse(
            session_id=session_id,
            modes=session_mode_state(self._session_mode(session_id)),
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
        self._refresh_config()  # old history can be read without a model, so this never refuses
        metadata = await self._find_metadata(session_id)
        if metadata is None:
            # No file: nobody typed in it (there is nothing to replay), or the id is unknown.
            await self._reopen_blank_session(session_id, cwd, mcp_servers, meta=kwargs)
        else:
            session = await self._repo.open(metadata)
            await self._bind_session(
                session_id, session, metadata.cwd or cwd, mcp_servers, meta=kwargs
            )
            if self._conn is not None:
                context = await session.build_context()
                for msg in context.messages:
                    for update in project_message_replay(msg):
                        await self._conn.session_update(session_id=session_id, update=update)
            self._schedule_deferred_setup(session_id)
        return LoadSessionResponse(
            modes=session_mode_state(self._session_mode(session_id)),
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
        self._refresh_config()
        metadata = await self._find_metadata(session_id)
        if metadata is None:
            await self._reopen_blank_session(session_id, cwd, mcp_servers, meta=kwargs)
        else:
            session = await self._repo.open(metadata)
            await self._bind_session(
                session_id, session, metadata.cwd or cwd, mcp_servers, meta=kwargs
            )
            # ACP session/resume intentionally does not replay history.
            self._schedule_deferred_setup(session_id)
        return ResumeSessionResponse(
            modes=session_mode_state(self._session_mode(session_id)),
            config_options=self._config_options(session_id),
            field_meta=self._session_response_meta(session_id),
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        self._session_models.pop(session_id, None)
        self._reserved_ids.discard(session_id)
        harness = self._harnesses.pop(session_id, None)
        self._permission_locks.pop(session_id, None)
        self._drop_trust(session_id)
        self._forget_notices(session_id)
        auto = self._autos.get(session_id)
        if auto is not None:
            auto.cancel()
        self._forget_mode(session_id)
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
        # An extension may still be setting up what the turn needs (the tools of an MCP server
        # that is starting). The wait ends when it is done, or given up, or on ``session/cancel``.
        if not await self._wait_for_turn_barriers(session_id, harness):
            return PromptResponse(stop_reason="cancelled")
        # Fallback: re-advertise commands if the deferred task was missed.
        if session_id not in self._commands_advertised:
            await self._advertise_commands(session_id)
        await self._refuse_id_taken_meanwhile(session_id, harness)
        text, images = _prompt_to_text_images(prompt)
        auto = self._autos.get(session_id)
        if auto is not None:
            auto.note_user_message(text)
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
        auto = self._autos.get(session_id)
        if auto is not None:
            # A decision that waits for the model ends, and its call does not run.
            auto.cancel()
        harness = self._harnesses.get(session_id)
        if harness is None:
            return
        task = asyncio.create_task(harness.abort())
        self._abort_tasks.add(task)
        task.add_done_callback(self._abort_tasks.discard)

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        """``session/set_config_option``: the ``model`` selector and the ``mode`` selector."""
        harness = self._require_harness(session_id)
        if config_id == MODE_CONFIG_ID:
            await self._select_mode(session_id, value, {"configId": config_id, "value": value})
            return SetSessionConfigOptionResponse(config_options=self._config_options(session_id))
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

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        """``session/set_mode``: the same state as the ``mode`` config option, for the ACP v1
        clients that read ``modes``. Any id but ``ask`` and ``auto`` is ``invalid_params``."""
        self._require_harness(session_id)
        await self._select_mode(session_id, mode_id, {"modeId": mode_id})
        return SetSessionModeResponse()

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
            self._reserved_ids.discard(session_id)
            self._permission_locks.pop(session_id, None)
            self._drop_trust(session_id)
            self._forget_notices(session_id)
            self._forget_mode(session_id)
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
        """``pi/yolo_mode_changed`` (the TUI's mode setting): the mode of every open session, and
        the start mode of the sessions that open later. The notification has no session id."""
        mode = mode_from_notification(method, params)
        if mode is None:
            return None
        self._config = replace(self._config, permission=mode)
        for session_id in list(self._session_modes):
            if session_id in self._harnesses and self._session_mode(session_id) != mode:
                await self._switch_mode(session_id, mode)
                await self._push_mode_option(session_id)
        return None

    def _session_mode(self, session_id: str) -> PermissionMode:
        return self._session_modes.get(session_id, self._config.permission)

    def _forget_mode(self, session_id: str) -> None:
        self._session_modes.pop(session_id, None)
        self._autos.pop(session_id, None)
        self._auto_from_config.discard(session_id)

    async def _select_mode(self, session_id: str, value: object, detail: dict[str, Any]) -> None:
        """A client chose a mode with a standard item. Only ``ask`` and ``auto`` can be chosen;
        the mode that the session is in already is accepted too, so that a client that sends
        the current value back is not refused."""
        mode = selectable_mode(value)
        if mode is None:
            if value == self._session_mode(session_id):
                return
            raise RequestError.invalid_params({**detail, "reason": "unknown mode"})
        await self._switch_mode(session_id, mode)

    async def _switch_mode(self, session_id: str, mode: PermissionMode) -> bool:
        """Put the session in *mode*. ``True`` when that changed it.

        Turning Auto on comes first and the mode after it: until Auto is ready, a call is asked
        about as in ``ask`` mode. Turning it off sets the mode first, so no call is decided by
        Auto after the user left it. A session that enters Auto gets the entry message.
        """
        async with self._mode_lock:
            previous = self._session_mode(session_id)
            registered = session_id in self._session_modes
            if registered and mode == previous:
                return False
            auto = self._autos.get(session_id)
            if mode == "auto" and auto is not None:
                await auto.enter()
            self._session_modes[session_id] = mode
            if mode != "auto" and auto is not None:
                auto.leave()
        if mode == "auto":
            await self._send_auto_notice(session_id)
        return True

    async def _send_text(self, session_id: str, text: str) -> None:
        """One line of the agent to the user. A client that is gone is not an error here."""
        if self._conn is None:
            return
        try:
            await self._conn.session_update(
                session_id=session_id, update=update_agent_message_text(text)
            )
        except Exception:
            logger.warning("Could not send a notice for session %s", session_id, exc_info=True)

    async def _send_auto_notice(self, session_id: str) -> None:
        """Tell the user that the session is in Auto: what it means, and what is wrong with the
        session if anything is (then Auto asks about every call)."""
        auto = self._autos.get(session_id)
        if auto is not None:
            parts = [
                entry_message(
                    from_config=session_id in self._auto_from_config,
                    classifier=auto.classifier_name,
                    model=auto.model_description(),
                )
            ]
        else:
            parts = [
                entry_message(
                    from_config=session_id in self._auto_from_config,
                    classifier=self._config.auto.classifier,
                )
            ]
        self._auto_from_config.discard(session_id)
        problems = auto.problems if auto is not None else ()
        if problems:
            parts.append(problems_message(problems))
        await self._send_text(session_id, "\n\n".join(parts))

    async def _push_mode_option(self, session_id: str) -> None:
        """Tell the client that the mode of a session changed without its asking."""
        if self._conn is None:
            return
        try:
            await self._conn.session_update(
                session_id=session_id,
                update=ConfigOptionUpdate(
                    session_update="config_option_update",
                    config_options=self._config_options(session_id),
                ),
            )
        except Exception:
            logger.warning("Could not send the mode of session %s", session_id, exc_info=True)

    async def _notify_permission_mode(self, session_id: str) -> None:
        """At the start of a session: the entry message when it is in Auto (it lists what is
        wrong with the settings), else the values of ``[auto]`` that Auto cannot work with; and
        the values of ``[auto]`` that were read and not used."""
        if self._conn is None or session_id not in self._harnesses:
            return
        if self._session_mode(session_id) == "auto":
            await self._send_auto_notice(session_id)
        elif self._config.auto.problems:
            await self._send_text(session_id, config_problems_message(self._config.auto.problems))
        notes = self._config.auto.notes
        if notes:
            await self._send_text(session_id, config_notes_message(notes))

    def _choice_by_id(self, model_id: str) -> ModelChoice | None:
        for choice in self._config.model_choices():
            if choice.id == model_id:
                return choice
        return None

    def _current_choice(self, session_id: str) -> ModelChoice:
        return self._session_models.get(session_id) or self._config.default_choice()

    def _config_options(self, session_id: str) -> list[SessionConfigOptionSelect]:
        """ACP Session Config Options: a ``model`` select (category ``model``) for ``/model``,
        and a ``mode`` select (category ``mode``) for the permission mode."""
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
            ),
            mode_config_option(self._session_mode(session_id)),
        ]

    def _session_response_meta(self, session_id: str) -> dict[str, Any] | None:
        """What the response that opens a session says beyond the standard fields.

        ``pi/cwd`` is the directory the session works in. It is the session's own when it is
        loaded or resumed (``session/load`` and ``session/resume`` take a ``cwd`` too, and the
        saved one wins), so it can differ from the one the client sent; a client that does
        not know the directory a session was made in learns it here. The ``pi/*`` model hints
        are kept for clients that predate config options.
        """
        meta: dict[str, Any] = {}
        cwd = self._session_cwds.get(session_id)
        if cwd:
            meta["pi/cwd"] = cwd
        current = self._current_choice(session_id)
        model_id = current.id.strip()
        if model_id:
            meta["pi/currentModelId"] = model_id
            meta["pi/currentModelDisplayName"] = current.name or model_id
            provider = (current.provider or "").strip()
            if provider:
                meta["pi/provider"] = provider
        return meta or None

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

    async def _create_session_with_id(self, session_id: str, cwd: str) -> Session:
        """Create the session under the id the client chose; an id already in use is refused.

        In use means that a session file has the id, or that a session open here was made under
        it and has no file yet (it is written with its first message). The repository does not
        refuse it: a session's file name starts with its creation time, so a second session with
        the same id would be written beside the first and either would answer a later
        ``session/load``.
        """
        async with self._chosen_id_lock:
            if session_id in self._reserved_ids or await self._repo.has_session(session_id):
                raise _id_in_use(session_id)
            session = await self._repo.create({"cwd": cwd, "id": session_id, "deferred": True})
            self._reserved_ids.add(session_id)
            return session

    async def _reopen_blank_session(
        self,
        session_id: str,
        cwd: str,
        mcp_servers: Sequence[Any] | None,
        *,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """``session/load`` or ``session/resume`` of an id that has no session file.

        A session in which nobody typed has no file (plan decision 17), but a client can still
        hold its id. Zed saves the id of every thread that shows anything, and the notices of
        this agent are something (the ignored MCP servers, the question about the project). It
        asks for the id again when it starts, and when the thread is opened from its history.
        ``resource_not_found`` left such a thread unusable ("Failed to Launch" and a Retry that
        fails the same way). The session is as empty as it was, so it is opened empty. It still
        has no file until its first message.

        Only an id of the kind this agent makes is taken for such a session: a UUID in its
        canonical form (the form the agent gives out, and the form a file's header holds). Any
        other id is unknown, and ``resource_not_found`` is still the answer.
        """
        try:
            ours = str(uuid.UUID(session_id)) == session_id
        except (ValueError, AttributeError, TypeError):
            ours = False
        if not ours:
            raise RequestError.resource_not_found(session_id)
        if session_id in self._harnesses:
            return  # Open here already, and with no file, so empty: nothing to bind or replay.
        session = await self._create_session_with_id(session_id, cwd)
        try:
            await self._bind_session(session_id, session, cwd, mcp_servers, meta=meta)
        except BaseException:
            self._reserved_ids.discard(session_id)
            raise
        logger.info("Session %s has no file (nobody typed in it): opened empty", session_id)
        self._schedule_deferred_setup(session_id)

    async def _refuse_id_taken_meanwhile(self, session_id: str, harness: AgentHarness) -> None:
        """Before the first message of a session made under a chosen id: is the id still free?

        The same holds for a blank session that the client asked for again with ``session/load``
        (``_reopen_blank_session``). Another process may have saved a session under that id
        since this one was made, and the check at that time could not see it. Saving this one
        too would put a second session with the id beside the first. The check is made once: a
        session that has its file is no longer in question.
        """
        storage = harness.session.get_storage()
        if (
            session_id in self._reserved_ids
            and isinstance(storage, DeferredJsonlSessionStorage)
            and not storage.written
            and await self._repo.has_session(session_id)
        ):
            raise _id_in_use(session_id)

    async def _end_replaced_harness(self, session_id: str, old: AgentHarness) -> None:
        """End a harness that a new one has replaced: ``close()`` stops its turn and runs the
        cleanup of its extensions (an MCP server is a child process).

        A client can ask for a session that is open already (a second ``session/load``, a retry
        after "Failed to Launch"), and the new harness takes the place of the old one. Nothing
        else would ever call the ``close()`` of the old one, and what its extensions started
        would run on.
        """
        try:
            await old.close()
        except Exception:
            logger.warning(
                "Could not end the earlier harness of session %s", session_id, exc_info=True
            )

    async def _bind_session(
        self,
        session_id: str,
        session: Session,
        cwd: str,
        mcp_servers: Sequence[Any] | None = None,
        *,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Build the harness of a session and make it the one that answers for the id.

        *mcp_servers* is what the client passed with the request. They go to the harness as data
        (``client_mcp``), before the extensions load; an extension may connect them. This agent
        connects none itself. *meta* is the ``_meta`` of the request: the TUI pager sets the
        permission mode of the session there (``autoMode``, ``yoloMode``).
        """
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
        limits = BashLimitRegistry()
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
            bash_limits=limits.take,
        )
        harness.set_client_mcp_servers(client_mcp_servers(mcp_servers))
        self._session_models[session_id] = choice
        # Built before any extension loads: the tool objects that are ours are taken by identity.
        auto = AutoSession(
            harness=harness,
            get_config=lambda: self._config,
            home=self._home,
            trusted=lambda: state.trust.trusted,
            limits=limits,
        )
        await auto.load_history(session)
        harness.load_extension(auto.activate, name=AUTO_EXTENSION_NAME)
        mode = await self._start_mode(session_id, auto, meta)

        async def on_event(event: Any, signal: Any | None = None) -> None:
            await self._emit_updates(session_id, event)

        harness.subscribe(on_event)
        self._drop_trust(session_id)  # binding again: a question still open is for the old one
        # The response to this request is not sent yet, so the client may not know the session
        # (a retry after a failed load): lines for the user wait for ``_open_notices`` again.
        self._notices_open.discard(session_id)
        replaced = self._harnesses.get(session_id)
        replaced_auto = self._autos.get(session_id)
        if replaced_auto is not None and replaced_auto is not auto:
            replaced_auto.cancel()
        self._autos[session_id] = auto
        self._session_modes[session_id] = mode
        self._harnesses[session_id] = harness
        self._session_cwds[session_id] = cwd
        self._trust[session_id] = state
        if replaced is not None and replaced is not harness:
            await self._end_replaced_harness(session_id, replaced)

        if decision.can_ask:
            # The user decides, and can only be asked once the client knows the session
            # (see ``_deferred_session_setup``). Extensions are code: they load with the answer.
            # (With no client to ask, that setup settles it as untrusted.)
            return
        state.settled.set()
        # Eagerly load extensions so slash commands are available.
        await harness.load_extensions()

    async def _start_mode(
        self, session_id: str, auto: AutoSession, meta: Mapping[str, Any] | None
    ) -> PermissionMode:
        """The mode a session starts in, and Auto made ready when that is ``auto``.

        The order: what the client asked for in ``_meta`` (the pager's two flags), else the
        mode the session had when it was bound before, else ``permission`` of ``agent.toml``
        (which the legacy notification may have changed since).
        """
        mode = mode_from_session_meta(meta or {})
        if mode is None:
            mode = self._session_modes.get(session_id)
        if mode is None:
            mode = self._config.permission
            if mode == "auto":
                self._auto_from_config.add(session_id)
        if mode == "auto":
            await auto.enter()
        return mode

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
        # The client has taken an update for the session, so it knows the session: the lines
        # that extensions sent before can go out, and the ones that follow need not wait.
        await self._open_notices(session_id)
        await self._notify_permission_mode(session_id)
        await self._notify_untrusted_project(session_id)
        await self._notify_failed_extensions(session_id)
        await self._notify_ignored_mcp_servers(session_id)

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
        and a prompt held back (by it, or by an extension's turn barrier) is let go: it ends
        as cancelled, for the session it waited on is gone."""
        state = self._trust.pop(session_id, None)
        if state is None:
            return
        if state.task is not None:
            state.task.cancel()
        state.settled.set()
        state.cancelled.set()

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

    async def _notify_ignored_mcp_servers(self, session_id: str) -> None:
        """Tell the user which MCP servers their client passed are not used (names only).

        A server that an extension took over (``claim_client_mcp_servers``) is not one of them:
        that extension reports on it itself. Called when the extensions are loaded.
        """
        harness = self._harnesses.get(session_id)
        if self._conn is None or harness is None:
            return
        names = [server.name for server in harness.unclaimed_client_mcp_servers]
        if not names:
            return
        logger.warning(
            "Ignoring %d MCP server(s) the client passed for session %s: %s",
            len(names),
            session_id,
            ", ".join(names),
        )
        notice = ignored_mcp_servers_notice(names, extension_installed=mcp_extension_installed())
        if notice is None:
            return
        await self._conn.session_update(
            session_id=session_id, update=update_agent_message_text(notice)
        )

    async def _open_notices(self, session_id: str) -> None:
        """From here on the client knows the session. Send the lines that extensions sent
        before (``ExtensionAPI.notify``), in order; the lines that follow go out at once."""
        if session_id not in self._harnesses:
            return  # closed in the meantime
        while True:
            held = self._held_notices.pop(session_id, [])
            if not held:
                break
            if self._conn is None:
                continue
            for text in held:
                await self._conn.session_update(
                    session_id=session_id, update=update_agent_message_text(text)
                )
        # No ``await`` between the last check of the held lines and this: a line that comes in
        # while the lines above are sent is held too, and the loop sends it.
        self._notices_open.add(session_id)

    def _forget_notices(self, session_id: str) -> None:
        self._notices_open.discard(session_id)
        self._held_notices.pop(session_id, None)

    async def _wait_for_turn_barriers(self, session_id: str, harness: AgentHarness) -> bool:
        """Hold a prompt back while an extension still sets up what the turn needs.

        The harness waits for the same barriers before it builds a turn. Here the wait can end
        on ``session/cancel`` too: ``False`` means the prompt must end as cancelled instead
        (the session may also have been closed while it waited).
        """
        state = self._trust.get(session_id)
        cancel = state.cancelled if state is not None else asyncio.Event()
        barriers = asyncio.ensure_future(harness.wait_turn_barriers())
        cancelled = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({barriers, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            barriers.cancel()
            cancelled.cancel()
        if cancel.is_set():
            return False
        return self._harnesses.get(session_id) is harness

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
        if getattr(event, "type", None) == "notice" and session_id not in self._notices_open:
            # Too early for the client (``_open_notices`` sends it). A session that is closed
            # has nothing to hold it for.
            text = getattr(event, "text", None)
            if isinstance(text, str) and text.strip() and session_id in self._harnesses:
                self._held_notices.setdefault(session_id, []).append(text)
            return
        cwd = self._session_cwds.get(session_id)
        for update in project_event(event, cwd=cwd):
            await self._conn.session_update(session_id=session_id, update=update)

    async def _handle_tool_call(self, session_id: str, event: Any) -> dict[str, Any] | None:
        name = event.toolName
        mode = self._session_mode(session_id)
        if not needs_permission(mode, getattr(event, "annotations", None)):
            return None
        auto_reason: Reason | None = None
        if mode == "auto":
            auto = self._autos.get(session_id)
            if auto is None:
                auto_reason = Reason.INTERNAL_ERROR  # no Auto state for the session: ask
            else:
                verdict = await auto.decide(event)
                if verdict.allows:
                    return None
                if verdict.cancelled:
                    # ``session/cancel`` came while the model step was asked: the user has
                    # stopped the turn, so the call is not put to them as a question.
                    return {"block": True, "reason": "Cancelled"}
                auto_reason = verdict.reason
        if self._conn is None:
            return {"block": True, "reason": "No ACP client connected"}
        raw_input = dict(event.input or {})
        tool_call = permission_tool_call(
            event.toolCallId,
            name,
            raw_input,
            origin=getattr(event, "origin", None),
            auto_reason=auto_reason,
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


def _chosen_session_id(meta: dict[str, Any]) -> str | None:
    """The id the client chose for a new session, from the ``_meta`` of ``session/new``.

    ACP v1 has no field for it; the TUI's ``--session-id`` is sent as ``_meta.sessionId``.
    ``None`` when the client chose none. The id becomes part of a file name, so it must be a
    UUID, and it comes back in the canonical form (lower case, hyphens) whatever form it came in.
    """
    raw = meta.get("sessionId")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            pass
    raise RequestError.invalid_params({"sessionId": raw, "reason": "must be a UUID"})


def _id_in_use(session_id: str) -> RequestError:
    return RequestError.invalid_params(
        {"sessionId": session_id, "reason": "a session with this id already exists"}
    )


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
