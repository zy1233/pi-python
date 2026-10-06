"""pi-goal-x — Goal-driven planning extension for pi-python.

Provides:
  - ``/goal <description>`` command to start goal-driven mode
  - ``goal_update`` tool for the agent to report step progress
  - ``goal_complete`` tool for the agent to mark goal completion
  - ``turn_end`` hook that reminds the agent to finish a goal whose steps are all done
  - ``session_start`` hook that restores goal state from session

Every change to the goal is saved in the session when it happens (``/goal``, ``goal_update``,
``goal_complete``), so the last saved state is the current one.

Install: ``pip install pi-goal-x-py``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pi_goal_x.goal_state import GoalState
from pi_goal_x.goal_tool import create_goal_complete_tool, create_goal_update_tool

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

logger = logging.getLogger(__name__)

# A reminder is a message, and a message starts another turn. An agent that ignores it would
# be reminded on every turn until the harness's turn limit ended the run in an error, so the
# reminders are limited. Changing a step so that the goal is no longer all done starts the
# count again.
MAX_COMPLETION_REMINDERS = 2


def _save(pi: ExtensionAPI, state: GoalState) -> None:
    """Put the current goal state in the session (it is written when the turn ends)."""
    pi.append_entry("goal_state", state.to_dict())


def _start_goal(pi: ExtensionAPI, state: GoalState, args: str) -> None:
    """Handler for ``/goal <description>``."""
    description = args.strip()
    if not description:
        if state.active:
            pi.send_message(f"Current goal status:\n\n{state.format_status()}")
        else:
            pi.send_message("Usage: /goal <description> — Start goal-driven planning mode")
        return

    state.start(description)
    _save(pi, state)
    pi.send_message(
        f"🎯 Goal set: {description}\n\n"
        "I'll break this down into steps and track progress. "
        "Use the goal_update and goal_complete tools to manage the plan."
    )


def _check_goal_progress(pi: ExtensionAPI, state: GoalState, event: Any) -> None:
    """``turn_end`` hook — remind the agent to call goal_complete once every step is done."""
    if not state.all_done:
        state.reminders_sent = 0
        return
    if not state.active:
        return  # already completed
    if state.reminders_sent >= MAX_COMPLETION_REMINDERS:
        logger.debug(
            "Goal %r: not reminding again; the agent has not completed it", state.description
        )
        return
    state.reminders_sent += 1
    pi.send_message(
        f"All steps are done for goal: {state.description}\n"
        "Call goal_complete with a summary to finish."
    )


def _restore_goal_state(pi: ExtensionAPI, state: GoalState, event: Any) -> None:
    """``session_start`` hook — restore goal from session entries."""
    try:
        entries = pi.get_custom_entries("goal_state")
    except Exception:
        logger.debug("Could not read goal_state entries for restore", exc_info=True)
        return
    if not entries:
        return
    last = entries[-1]
    data = getattr(last, "data", None)
    if not isinstance(data, dict):
        return
    try:
        restored = GoalState.from_dict(data)
        if restored.active:
            state.description = restored.description
            state.steps = restored.steps
            state.completed = restored.completed
            state.summary = restored.summary
            logger.debug("Restored goal state: %s", state.description)
    except Exception:
        logger.debug("Failed to restore goal state from entry", exc_info=True)


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point — called by the ExtensionLoader."""
    state = GoalState()

    def save() -> None:
        _save(pi, state)

    pi.register_tool(create_goal_update_tool(state, on_change=save))
    pi.register_tool(create_goal_complete_tool(state, on_change=save))

    pi.register_command(
        "goal",
        description="Start goal-driven planning mode: /goal <description>",
        handler=lambda args: _start_goal(pi, state, args if isinstance(args, str) else ""),
    )

    pi.on("turn_end", lambda event: _check_goal_progress(pi, state, event))
    pi.on("session_start", lambda event: _restore_goal_state(pi, state, event))
