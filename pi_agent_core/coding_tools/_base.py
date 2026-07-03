"""Shared scaffolding for built-in coding tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from pi_agent_core.types import (
    AgentToolResult,
    AgentToolUpdateCallback,
    ToolExecutionMode,
)


@dataclass
class CodingTool:
    """A built-in tool instance (implements the ``AgentTool`` protocol).

    ``prompt_snippet``/``prompt_guidelines`` carry pi's prompt-assembly
    metadata for harness consumers; the core loop ignores them.
    """

    name: str
    description: str
    label: str
    parameters: type[BaseModel] | dict[str, Any]
    execute_fn: Callable[..., Any]
    execution_mode: ToolExecutionMode | None = None
    prepare_arguments: Callable[[Any], Any] | None = None
    prompt_snippet: str | None = None
    prompt_guidelines: list[str] = field(default_factory=list)

    async def execute(
        self,
        tool_call_id: str,
        params: Any,
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        return await self.execute_fn(tool_call_id, params, signal, on_update)


def raise_if_aborted(signal: Any | None) -> None:
    """Raise pi's ``Operation aborted`` if the cooperative signal fired."""
    if signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")
