"""goal_update and goal_complete tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from pi_agent_core.types import AgentToolResult
from pi_goal_x.goal_state import GoalState
from pi_goal_x.prompts import (
    GOAL_COMPLETE_SNIPPET,
    GOAL_SYSTEM_GUIDELINES,
    GOAL_UPDATE_SNIPPET,
)

# Both tools write, but only the agent's own notes (the goal, kept as session entries): they
# touch nothing of the user's and reach nowhere, so the CLI's ``ask`` mode has no reason to
# stop for them. (Hints in the MCP vocabulary; see ``pi_agent_core.types.ToolAnnotations``.)
_KEEPS_NOTES = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}

# ---------------------------------------------------------------------------
# goal_update
# ---------------------------------------------------------------------------


class GoalUpdateParams(BaseModel):
    step_index: int | None = Field(
        default=None,
        description=(
            "Index of the step to update, as shown in brackets in the goal status (0-based). "
            "If omitted, a new step is appended."
        ),
    )
    description: str | None = Field(
        default=None,
        description="Description of the step (required when creating a new step)",
    )
    status: Literal["pending", "in_progress", "done", "blocked"] | None = Field(
        default=None,
        description="New status of the step",
    )
    details: str | None = Field(
        default=None,
        description="Additional details or notes about this step",
    )


def _make_goal_update_execute(state: GoalState, on_change: Callable[[], None] | None) -> Any:
    async def goal_update_execute(
        tool_call_id: str,
        params: Any,
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        if not state.active:
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "No active goal. Use /goal <description> to start one.",
                    }
                ]
            )

        # A step that does not exist, or a new one without a description, raises ValueError:
        # the agent loop reports it to the model as a failed tool call, and nothing changed.
        step = state.update_step(
            params.step_index,
            description=params.description,
            status=params.status,
            details=params.details,
        )
        if on_change is not None:
            on_change()

        return AgentToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"Step updated: {step.description} [{step.status}]\n\n"
                        f"{state.format_status()}"
                    ),
                }
            ],
        )

    return goal_update_execute


# ---------------------------------------------------------------------------
# goal_complete
# ---------------------------------------------------------------------------


class GoalCompleteParams(BaseModel):
    summary: str = Field(
        description="Summary of what was accomplished to achieve the goal",
    )


def _make_goal_complete_execute(state: GoalState, on_change: Callable[[], None] | None) -> Any:
    async def goal_complete_execute(
        tool_call_id: str,
        params: Any,
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        if not state.active:
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "No active goal to complete.",
                    }
                ]
            )

        state.complete(params.summary)
        if on_change is not None:
            on_change()

        return AgentToolResult(
            content=[
                {
                    "type": "text",
                    "text": f"✅ Goal completed: {state.description}\n\nSummary: {params.summary}",
                }
            ],
            terminate=True,
        )

    return goal_complete_execute


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_goal_update_tool(
    state: GoalState, *, on_change: Callable[[], None] | None = None
) -> Any:
    """The ``goal_update`` tool. *on_change* is called after every change to *state*."""
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="goal_update",
        description="Report progress on a goal step — create, update status, or add details",
        parameters=GoalUpdateParams,
        execute=_make_goal_update_execute(state, on_change),
        label="Goal Update",
        prompt_snippet=GOAL_UPDATE_SNIPPET,
        prompt_guidelines=GOAL_SYSTEM_GUIDELINES,
        annotations=dict(_KEEPS_NOTES),
    )


def create_goal_complete_tool(
    state: GoalState, *, on_change: Callable[[], None] | None = None
) -> Any:
    """The ``goal_complete`` tool. *on_change* is called after *state* is completed."""
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="goal_complete",
        description="Mark the current goal as completed and provide a summary",
        parameters=GoalCompleteParams,
        execute=_make_goal_complete_execute(state, on_change),
        label="Goal Complete",
        prompt_snippet=GOAL_COMPLETE_SNIPPET,
        annotations=dict(_KEEPS_NOTES),
    )
