"""ExtensionAPI — the public facade extensions interact with.

Mirrors upstream pi's TypeScript ``ExtensionAPI`` (minimal Python subset).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
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
)

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
    ) -> None:
        self._registry = registry
        self._meta = meta
        self._bridge: HarnessBridge | None = None

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

        Can be called during ``activate()`` or dynamically later.
        """
        self._registry.add_tool(definition, extension_name=self._meta.name)
        bridge = self._bridge
        if bridge is not None:
            bridge.inject_tool(definition)

    # -- register_command ----------------------------------------------------

    def register_command(
        self,
        name: str,
        *,
        description: str = "",
        handler: CommandHandler,
    ) -> None:
        """Register a slash-command (e.g. ``/search``)."""
        self._registry.add_command(
            CommandDef(
                name=name,
                description=description,
                handler=handler,
                extension_name=self._meta.name,
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

        def unsubscribe() -> None:
            self._registry.remove_event_handler(registration)

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
    def extension_name(self) -> str:
        return self._meta.name

    # -- convenience: schedule an async callback on the running loop ---------

    def run_async(self, coro: Any) -> asyncio.Task[Any]:
        """Fire-and-forget an async task on the running event loop."""
        loop = asyncio.get_event_loop()
        return loop.create_task(coro)
