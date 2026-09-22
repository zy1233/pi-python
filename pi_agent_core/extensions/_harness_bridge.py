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

    def get_active_tool_names(self) -> list[str]: ...

    def set_active_tool_names(self, names: list[str]) -> None: ...

    def get_all_tool_info(self) -> list[ToolInfo]: ...

    def send_message(self, text: str) -> None: ...

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
