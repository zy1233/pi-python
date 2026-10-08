"""ExtensionAPI — the public facade extensions interact with.

Mirrors upstream pi's TypeScript ``ExtensionAPI`` (minimal Python subset).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_agent_core.extensions.registry import ExtensionRegistry
from pi_agent_core.extensions.types import (
    CommandDef,
    CommandHandler,
    EventHandler,
    EventRegistration,
    ExtensionMeta,
    ToolDefinition,
    ToolInfo,
    validate_tool_name,
)
from pi_agent_core.home import pi_home

if TYPE_CHECKING:
    from pi_agent_core.extensions._harness_bridge import HarnessBridge

logger = logging.getLogger(__name__)


class ExtensionAPI:
    """Python equivalent of pi's TypeScript ``ExtensionAPI``.

    Each loaded extension receives its own ``ExtensionAPI`` instance so the
    registry can track which extension registered what.  The ``HarnessBridge``
    back-reference is injected by the harness after construction.
    """

    def __init__(
        self,
        registry: ExtensionRegistry,
        meta: ExtensionMeta,
        *,
        home: Path | str | None = None,
    ) -> None:
        self._registry = registry
        self._meta = meta
        self._home = home
        self._bridge: HarnessBridge | None = None
        self._loading = True  # suppresses bridge side-effects during activate

    # -- internal: set by harness after activation --------------------------

    def _set_bridge(self, bridge: HarnessBridge) -> None:
        self._bridge = bridge

    def _require_bridge(self) -> HarnessBridge:
        if self._bridge is None:
            raise RuntimeError(
                "ExtensionAPI is not connected to a harness yet. "
                "This method can only be called after the extension has been activated."
            )
        return self._bridge

    # -- register_tool -------------------------------------------------------

    def register_tool(self, definition: ToolDefinition) -> None:
        """Register a custom tool callable by the LLM.

        During ``activate()`` only writes to registry; the harness injects
        all registered tools later via ``_apply_extension_registrations()``.
        After loading, dynamically registered tools are injected immediately.

        Raises ``ValueError`` for a name model providers would reject (anything outside
        ``[a-zA-Z0-9_-]{1,128}``). During ``activate()`` that fails the extension's load.
        """
        validate_tool_name(definition.name, extension=self._meta.name)
        self._registry.add_tool(definition, extension_name=self._meta.name)
        if not self._loading and self._bridge is not None:
            self._bridge.inject_tool(definition)

    # -- register_command ----------------------------------------------------

    def register_command(
        self,
        name: str,
        *,
        description: str = "",
        handler: CommandHandler,
        passthrough: bool = False,
    ) -> None:
        """Register a slash-command (e.g. ``/search``).

        When *passthrough* is True the command appears in client autocomplete
        but the text is forwarded to the LLM as a regular prompt instead of
        being intercepted.  Use this for commands backed by LLM tools.

        Raises ``TypeError`` when *description* is not text (audit P7-17): clients are sent it
        as ``AvailableCommand.description``, and one that is not a string made the whole
        update invalid. During ``activate()`` that fails the extension's load.
        """
        if not isinstance(description, str):
            raise TypeError(
                f"Extension {self._meta.name!r}: the description of command /{name} must be a "
                f"string, not {type(description).__name__}"
            )
        self._registry.add_command(
            CommandDef(
                name=name,
                description=description,
                handler=handler,
                extension_name=self._meta.name,
                passthrough=passthrough,
            )
        )

    # -- on (event subscription) ---------------------------------------------

    def on(self, event: str, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to a lifecycle event.  Returns an unsubscribe function."""
        registration = EventRegistration(
            event=event,
            handler=handler,
            extension_name=self._meta.name,
        )
        self._registry.add_event_handler(registration)

        # After initial loading, also inject into live harness hooks
        if not self._loading and self._bridge is not None:
            self._bridge.add_hook(event, handler)

        def unsubscribe() -> None:
            self._registry.remove_event_handler(registration)
            if self._bridge is not None:
                self._bridge.remove_hook(event, handler)

        return unsubscribe

    # -- tool management (delegates to harness) ------------------------------

    def get_active_tools(self) -> list[str]:
        """Names of tools currently enabled for the LLM."""
        return list(self._require_bridge().get_active_tool_names())

    def set_active_tools(self, names: list[str]) -> None:
        """Change the active tool set."""
        self._require_bridge().set_active_tool_names(names)

    def get_all_tools(self) -> list[ToolInfo]:
        """All registered tools (built-in + extension) with metadata."""
        return self._require_bridge().get_all_tool_info()

    # -- message injection ---------------------------------------------------

    def send_message(self, text: str) -> None:
        """Inject a user-role message into the conversation (steer queue)."""
        self._require_bridge().send_message(text)

    # -- session persistence -------------------------------------------------

    def append_entry(self, custom_type: str, data: Any = None) -> None:
        """Persist a custom entry in the session tree (survives restart)."""
        self._require_bridge().append_entry(custom_type, data)

    def get_custom_entries(self, custom_type: str) -> list[Any]:
        """Read all persisted custom entries of *custom_type* from the session."""
        return self._require_bridge().get_custom_entries(custom_type)

    # -- shell execution -----------------------------------------------------

    async def exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Run a shell command.  Returns ``ExecResult``."""
        return await self._require_bridge().exec(command, cwd=cwd, timeout=timeout)

    # -- read-only accessors -------------------------------------------------

    @property
    def cwd(self) -> str:
        """Working directory of the current session."""
        return self._require_bridge().cwd

    @property
    def session_id(self) -> str:
        """Unique identifier of the current session."""
        return self._require_bridge().session_id

    @property
    def project_trusted(self) -> bool:
        """Whether the user vouched for the project this session runs in (audit F7-01).

        The answer that lets the project's own extensions load, final by the time an
        extension activates. Read what a repository ships (``<cwd>/.pi-python/...``: scripts
        you turn into commands, text you hand to the model) only when this is ``True``; the
        user's own files, under ``pi.home``, need no such answer. Only an actual ``True`` is a
        yes: a bridge that hands back anything else is read as "not trusted".
        """
        return self._require_bridge().project_trusted is True

    @property
    def home(self) -> Path:
        """The pi-python home directory of this session (``$PI_HOME`` or ``~/.pi-python``).

        Keep an extension's own state below it rather than under ``Path.home()``, so a
        session started with ``PI_HOME`` set does not scatter files into the real home.
        Available during ``activate()``; needs no harness.
        """
        return pi_home(self._home)

    @property
    def extension_name(self) -> str:
        return self._meta.name

    # -- convenience: schedule an async callback on the running loop ---------

    def run_async(self, coro: Any) -> asyncio.Task[Any]:
        """Fire-and-forget an async task on the running event loop."""
        loop = asyncio.get_event_loop()
        return loop.create_task(coro)
