"""HarnessBridge — protocol that ExtensionAPI delegates to.

This is a narrow interface that ``AgentHarness`` implements so that
``ExtensionAPI`` can call back into the harness without a circular import.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pi_agent_core.extensions.types import ToolDefinition, ToolInfo


@runtime_checkable
class HarnessBridge(Protocol):
    """Back-channel from ExtensionAPI into the harness runtime."""

    def inject_tool(self, definition: ToolDefinition) -> None:
        """Inject a dynamically registered tool into the harness."""
        ...

    def remove_tool(self, name: str) -> None:
        """Remove a tool from the harness's live tool set."""
        ...

    def get_active_tool_names(self) -> list[str]: ...

    def set_active_tool_names(self, names: list[str]) -> None: ...

    def get_all_tool_info(self) -> list[ToolInfo]: ...

    def send_message(self, text: str) -> None: ...

    def trigger_prompt(self, text: str) -> None:
        """Schedule a prompt on the harness if idle, otherwise steer.

        Implementations should use ``asyncio.create_task`` internally so this
        can be called from sync code (like done-callbacks).
        """
        ...

    def append_entry(self, custom_type: str, data: Any) -> None: ...

    async def exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Any: ...

    @property
    def cwd(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def stream_fn(self) -> Any:
        """The parent harness's StreamFn."""
        ...

    @property
    def model(self) -> Any:
        """The parent harness's current Model."""
        ...

    @property
    def get_api_key_fn(self) -> Any:
        """The parent harness's get_api_key callback (or None)."""
        ...

    def add_hook(self, event: str, handler: Any) -> None:
        """Add *handler* to the harness's live hook list for *event*.

        Called by ``ExtensionAPI.on()`` when registering after initial
        loading so that dynamic subscriptions take effect at runtime.
        """
        ...

    def remove_hook(self, event: str, handler: Any) -> None:
        """Remove *handler* from the harness's live hook list for *event*.

        Called by ``ExtensionAPI.on()``'s unsubscribe function so that
        handlers copied into the harness at activation time are also
        removed from the runtime dispatch path.
        """
        ...

    def get_custom_entries(self, custom_type: str) -> list[Any]:
        """Return all ``CustomEntry`` objects with matching *custom_type*.

        Entries are returned in session order (oldest first).  Each entry
        has at least ``customType`` and ``data`` attributes/keys.
        """
        ...

    def register_cleanup(self, callback: Any) -> None:
        """Register an async cleanup callback for session-close.

        *callback* must be an async callable (no arguments) invoked when
        the harness owner (e.g. ACP agent) calls ``harness.close()``.
        """
        ...
