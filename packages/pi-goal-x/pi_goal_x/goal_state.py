"""GoalState — state machine for goal-driven planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StepStatus = Literal["pending", "in_progress", "done", "blocked"]


@dataclass
class GoalStep:
    """One step within a goal plan."""

    description: str
    status: StepStatus = "pending"
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"description": self.description, "status": self.status}
        if self.details:
            d["details"] = self.details
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoalStep:
        return cls(
            description=d["description"],
            status=d.get("status", "pending"),
            details=d.get("details"),
        )


@dataclass
class GoalState:
    """Goal planning state — tracks description, steps, and completion."""

    description: str | None = None
    steps: list[GoalStep] = field(default_factory=list)
    completed: bool = False
    summary: str | None = None
    # How many times the agent was reminded, since the steps last stopped being all done, to
    # call goal_complete. Bookkeeping of this process only: never saved with the goal.
    reminders_sent: int = field(default=0, repr=False, compare=False)

    @property
    def active(self) -> bool:
        return self.description is not None and not self.completed

    @property
    def progress(self) -> str:
        """Human-readable progress string."""
        if not self.steps:
            return "No steps defined"
        done = sum(1 for s in self.steps if s.status == "done")
        total = len(self.steps)
        return f"{done}/{total} steps completed"

    @property
    def all_done(self) -> bool:
        return bool(self.steps) and all(s.status == "done" for s in self.steps)

    def start(self, description: str) -> None:
        """Start a new goal (resets previous state)."""
        self.description = description
        self.steps = []
        self.completed = False
        self.summary = None
        self.reminders_sent = 0

    def update_step(
        self,
        step_index: int | None = None,
        *,
        description: str | None = None,
        status: StepStatus | None = None,
        details: str | None = None,
    ) -> GoalStep:
        """Update the step at *step_index* (0-based), or append a new one if it is None.

        Raises ``ValueError`` when *step_index* names no step, and when a new step has no
        description. Making up a step ("Unnamed step") would hide the mistake: the caller
        believes it changed a step that is still as it was.
        """
        if step_index is None:
            if not description:
                raise ValueError("A new step needs a description.")
            new_step = GoalStep(
                description=description, status=status or "pending", details=details
            )
            self.steps.append(new_step)
            return new_step
        if not 0 <= step_index < len(self.steps):
            raise ValueError(
                f"There is no step {step_index}: this goal {self._step_indexes()}. "
                "Leave step_index out to add a new step."
            )
        step = self.steps[step_index]
        if description is not None:
            step.description = description
        if status is not None:
            step.status = status
        if details is not None:
            step.details = details
        return step

    def _step_indexes(self) -> str:
        """The steps that exist, as a caller needs to name them ("has 2 steps, 0 and 1")."""
        count = len(self.steps)
        if count == 0:
            return "has no steps yet"
        if count == 1:
            return "has 1 step, index 0"
        return f"has {count} steps, indexes 0 to {count - 1}"

    def complete(self, summary: str | None = None) -> None:
        """Mark the goal as completed."""
        self.completed = True
        self.summary = summary
        for step in self.steps:
            if step.status != "done":
                step.status = "done"

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "completed": self.completed,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoalState:
        return cls(
            description=d.get("description"),
            steps=[GoalStep.from_dict(s) for s in d.get("steps", [])],
            completed=d.get("completed", False),
            summary=d.get("summary"),
        )

    def format_status(self) -> str:
        """Format current goal status for prompt injection."""
        if not self.active:
            return ""
        lines = [f"## Current Goal: {self.description}", ""]
        for i, step in enumerate(self.steps):
            icon = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "blocked": "🚫"}.get(
                step.status, "⬜"
            )
            # The number goal_update's step_index takes (0-based), not a count from 1: a model
            # that reads "Step 2" and passes 2 would change the wrong step.
            lines.append(f"  {icon} [{i}] {step.description}")
            if step.details:
                lines.append(f"       {step.details}")
        lines.append(f"\nProgress: {self.progress}")
        return "\n".join(lines)
