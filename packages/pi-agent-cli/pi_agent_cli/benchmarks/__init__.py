"""Benchmark evaluation subsystem for pi-agent-cli and pi-python harness."""

from pi_agent_cli.benchmarks.collector import (
    format_markdown_report,
    print_summary_table,
    save_summary_artifacts,
)
from pi_agent_cli.benchmarks.evaluator import run_trial
from pi_agent_cli.benchmarks.models import (
    BenchmarkSummary,
    BenchmarkTask,
    TrialResult,
)
from pi_agent_cli.benchmarks.pelican import (
    PELICAN_PROMPT,
    PelicanSvgReport,
    extract_svg,
    save_pelican_artifact,
    validate_pelican_svg,
)
from pi_agent_cli.benchmarks.task_loader import discover_tasks, load_task_from_dir

__all__ = [
    "PELICAN_PROMPT",
    "BenchmarkSummary",
    "BenchmarkTask",
    "PelicanSvgReport",
    "TrialResult",
    "discover_tasks",
    "extract_svg",
    "format_markdown_report",
    "load_task_from_dir",
    "print_summary_table",
    "run_trial",
    "save_pelican_artifact",
    "save_summary_artifacts",
    "validate_pelican_svg",
]
