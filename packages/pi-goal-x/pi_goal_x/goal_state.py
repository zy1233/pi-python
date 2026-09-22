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

    def update_step(
        self,
        step_index: int | None = None,
        *,
        description: str | None = None,
        status: StepStatus | None = None,
        details: str | None = None,
    ) -> GoalStep:
        """Update an existing step or append a new one."""
        if step_index is not None and 0 <= step_index < len(self.steps):
            step = self.steps[step_index]
            if description is not None:
                step.description = description
            if status is not None:
                step.status = status
            if details is not None:
                step.details = details
            return step
        new_step = GoalStep(
            description=description or "Unnamed step",
            status=status or "pending",
            details=details,
        )
        self.steps.append(new_step)
        return new_step

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
            lines.append(f"  {icon} Step {i + 1}: {step.description}")
            if step.details:
                lines.append(f"       {step.details}")
        lines.append(f"\nProgress: {self.progress}")
        return "\n".join(lines)
