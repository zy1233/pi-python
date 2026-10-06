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

        The text is treated as if the user had typed it (slash commands included), so it
        is only for input the user themselves asked for. Output of tools or agents belongs
        in ``trigger_message``. Does nothing once the harness is closed.

        Implementations should use ``asyncio.create_task`` internally so this
        can be called from sync code (like done-callbacks).
        """
        ...

    def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
        """Deliver *text* as a typed (custom) message, starting a turn if the harness is idle.

        The message records where it came from (*custom_type*, *details*) and is never taken
        for a slash command. A turn in progress picks it up as steering; an idle harness
        starts a turn with it. Does nothing once the harness is closed. The model still
        reads it as context, so what comes from an untrusted source has to be marked as
        such in *text* itself.

        Like ``trigger_prompt`` this can be called from sync code.
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

    @property
    def tool_call_gate(self) -> Any:
        """Async ``(tool_call_id, tool_name, tool_input, *, origin=None)`` callable that runs
        the parent harness's ``tool_call`` policy chain (permission layer, extension hooks)
        for a tool call made *outside* the harness's own loop.

        Extensions that run agents of their own (dynamic workflows) put every tool call of
        those agents through it. It returns ``None`` to allow, or
        ``{"block": True, "reason": ...}`` to deny; it raises when the policy cannot decide,
        which callers must treat as a denial.
        """
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
