"""Prompt snippets and guidelines for goal-driven mode."""

GOAL_SYSTEM_GUIDELINES = [
    "You are in goal-driven mode. Track your progress using "
    "the goal_update and goal_complete tools.",
    "After completing each step, call goal_update to mark it as done before moving to the next.",
    "When all steps are finished, call goal_complete with a summary of what was accomplished.",
    "If you encounter a blocker on a step, call goal_update "
    "with status='blocked' and explain in details.",
    "Break complex tasks into discrete, actionable steps that can be individually tracked.",
]

GOAL_UPDATE_SNIPPET = "Report progress on a goal step (mark as pending/in_progress/done/blocked)"
GOAL_COMPLETE_SNIPPET = "Mark the current goal as completed and provide a summary"
