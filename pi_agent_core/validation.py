"""Tool argument validation (Pydantic replacement for pi-ai TypeBox)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from pi_agent_core.messages import AgentToolCall
from pi_agent_core.types import AgentTool


def validate_tool_arguments(tool: AgentTool, tool_call: AgentToolCall) -> Any:
    params = tool.parameters
    args = tool_call["arguments"]
    if isinstance(params, type) and issubclass(params, BaseModel):
        return params.model_validate(args)
    return args


def validate_tool_call(tool: AgentTool, tool_call: AgentToolCall) -> AgentToolCall:
    try:
        validated = validate_tool_arguments(tool, tool_call)
        if validated is tool_call["arguments"]:
            return tool_call
        return {**tool_call, "arguments": validated if isinstance(validated, dict) else validated.model_dump()}
    except ValidationError as e:
        raise ValueError(str(e)) from e
