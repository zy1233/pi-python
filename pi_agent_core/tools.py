"""Helper to define AgentTool implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from pi_agent_core.types import AgentToolResult, AgentToolUpdateCallback, ToolExecutionMode


@dataclass
class SimpleTool:
    name: str
    description: str
    label: str
    parameters: type[BaseModel] | dict[str, Any]
    execute_fn: Callable[..., Any]
    execution_mode: ToolExecutionMode | None = None
    prepare_arguments: Callable[[Any], Any] | None = None

    async def execute(
        self,
        tool_call_id: str,
        params: Any,
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        result = self.execute_fn(tool_call_id, params, signal, on_update)
        if hasattr(result, "__await__"):
            return await result
        return result
