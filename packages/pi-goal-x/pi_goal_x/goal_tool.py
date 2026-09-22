"""goal_update and goal_complete tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pi_agent_core.types import AgentToolResult
from pi_goal_x.goal_state import GoalState
from pi_goal_x.prompts import (
    GOAL_COMPLETE_SNIPPET,
    GOAL_SYSTEM_GUIDELINES,
    GOAL_UPDATE_SNIPPET,
)

# ---------------------------------------------------------------------------
# goal_update
# ---------------------------------------------------------------------------


class GoalUpdateParams(BaseModel):
    step_index: int | None = Field(
        default=None,
        description="Index of the step to update (0-based). If omitted, a new step is appended.",
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


def _make_goal_update_execute(state: GoalState) -> Any:
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

        step = state.update_step(
            params.step_index,
            description=params.description,
            status=params.status,
            details=params.details,
        )

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


def _make_goal_complete_execute(state: GoalState) -> Any:
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


def create_goal_update_tool(state: GoalState) -> Any:
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="goal_update",
        description="Report progress on a goal step — create, update status, or add details",
        parameters=GoalUpdateParams,
        execute=_make_goal_update_execute(state),
        label="Goal Update",
        prompt_snippet=GOAL_UPDATE_SNIPPET,
        prompt_guidelines=GOAL_SYSTEM_GUIDELINES,
    )


def create_goal_complete_tool(state: GoalState) -> Any:
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="goal_complete",
        description="Mark the current goal as completed and provide a summary",
        parameters=GoalCompleteParams,
        execute=_make_goal_complete_execute(state),
        label="Goal Complete",
        prompt_snippet=GOAL_COMPLETE_SNIPPET,
    )
