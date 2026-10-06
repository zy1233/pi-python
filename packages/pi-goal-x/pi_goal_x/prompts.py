"""Prompt snippets and guidelines for goal-driven mode."""

# The goal tools are registered in every session, so these guidelines are rendered into
# every session's system prompt. Each line must therefore hold when no goal is active:
# describe when the tools apply, never claim that goal mode is already on.
GOAL_SYSTEM_GUIDELINES = [
    "Goal mode starts only when the user runs /goal <description>. "
    "Call goal_update and goal_complete only while a goal is active.",
    "While a goal is active, break it into discrete, actionable steps and call goal_update "
    "after each step (status='blocked' with details when you hit a blocker).",
    "When every step of an active goal is done, call goal_complete with a summary of what "
    "was accomplished.",
]

GOAL_UPDATE_SNIPPET = "Report progress on a goal step (mark as pending/in_progress/done/blocked)"
GOAL_COMPLETE_SNIPPET = "Mark the current goal as completed and provide a summary"
