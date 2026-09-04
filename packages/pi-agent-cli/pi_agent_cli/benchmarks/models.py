"""Data models for benchmark tasks and evaluation results.

Aligned with FrontierHarness Eval and Harbor evaluation record contracts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


@dataclass
class BenchmarkTask:
    """Specification of an evaluation benchmark task."""

    id: str
    name: str
    suite: str
    instruction: str
    task_dir: Path
    agent_timeout_sec: float = 300.0
    verifier_timeout_sec: float = 180.0
    max_turns: int = 30
    difficulty: str = "medium"
    tags: list[str] = field(default_factory=list)
    workdir_relative: str = "."
    verifier_script: Path | None = None
    verifier_test: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrialResult:
    """Outcome and execution metrics for a single task trial.

    Conforms to the FrontierHarness trial.json record contract.
    """

    id: str
    title: str
    suite: str
    status: Literal["success", "failure", "error", "infra_invalid"]
    success: bool
    duration_seconds: float
    cost_first_cold_usd: float = 0.0
    turns: int = 0
    no_action_turns: int = 0
    cache_hit_rate_normalized: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None
    verifier_output: str = ""
    trajectory_file: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class BenchmarkSummary:
    """Aggregated metrics across a benchmark evaluation run."""

    run_id: str
    harness: str
    model: str
    provider: str
    started_at: str
    finished_at: str
    total_tasks: int
    completed_tasks: int
    successful_tasks: int
    infra_invalid: int
    pass_rate: float
    total_cost_usd: float
    effective_cost_per_pass: float | None
    median_cost_per_task: float
    median_duration_seconds: float
    mean_turns: float
    mean_no_action_turns: float
    mean_tokens_per_task: float
    tokens_per_solved: float | None
    typical_cache_hit_rate: float
    trials: list[TrialResult] = field(default_factory=list)

    @classmethod
    def from_trials(
        cls,
        *,
        run_id: str,
        harness: str,
        model: str,
        provider: str,
        started_at: str,
        trials: list[TrialResult],
    ) -> BenchmarkSummary:
        finished_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        scored = [t for t in trials if t.status != "infra_invalid"]
        invalid = [t for t in trials if t.status == "infra_invalid"]
        passes = [t for t in scored if t.success]

        total_tasks = len(trials)
        completed = len(scored)
        successful = len(passes)
        pass_rate = (successful / completed) if completed > 0 else 0.0

        total_cost = sum(t.cost_first_cold_usd for t in scored)
        effective_cost_per_pass = (total_cost / successful) if successful > 0 else None

        costs = sorted(t.cost_first_cold_usd for t in scored)
        median_cost = costs[len(costs) // 2] if costs else 0.0

        durations = sorted(t.duration_seconds for t in passes) if passes else [0.0]
        median_duration = durations[len(durations) // 2]

        mean_turns = (sum(t.turns for t in scored) / completed) if completed > 0 else 0.0
        mean_no_action = (
            (sum(t.no_action_turns for t in scored) / completed) if completed > 0 else 0.0
        )
        total_tokens = sum(t.total_tokens for t in scored)
        mean_tokens = (total_tokens / completed) if completed > 0 else 0.0
        tokens_per_solved = (total_tokens / successful) if successful > 0 else None

        cache_rates = sorted(t.cache_hit_rate_normalized for t in scored)
        typical_cache = cache_rates[len(cache_rates) // 2] if cache_rates else 0.0

        return cls(
            run_id=run_id,
            harness=harness,
            model=model,
            provider=provider,
            started_at=started_at,
            finished_at=finished_at,
            total_tasks=total_tasks,
            completed_tasks=completed,
            successful_tasks=successful,
            infra_invalid=len(invalid),
            pass_rate=pass_rate,
            total_cost_usd=round(total_cost, 4),
            effective_cost_per_pass=(
                round(effective_cost_per_pass, 4) if effective_cost_per_pass is not None else None
            ),
            median_cost_per_task=round(median_cost, 4),
            median_duration_seconds=round(median_duration, 1),
            mean_turns=round(mean_turns, 1),
            mean_no_action_turns=round(mean_no_action, 1),
            mean_tokens_per_task=round(mean_tokens, 0),
            tokens_per_solved=(
                round(tokens_per_solved, 0) if tokens_per_solved is not None else None
            ),
            typical_cache_hit_rate=round(typical_cache, 4),
            trials=trials,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trials"] = [t.to_dict() for t in self.trials]
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
