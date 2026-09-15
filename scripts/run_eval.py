#!/usr/bin/env python3
"""CLI runner for pi-python code-agent harness evaluations.

Runs benchmark tasks (FrontierHarness / Terminal-Bench style), evaluates agent execution,
records token & cost statistics, and outputs summary reports.

Usage:
    # Run a single task:
    .venv\\Scripts\\python.exe scripts/run_eval.py --task regex-log

    # Run all curated tasks:
    .venv\\Scripts\\python.exe scripts/run_eval.py --all

    # Run from external tasks directory:
    # .venv\\Scripts\\python.exe scripts/run_eval.py --tasks-dir <path> --task regex-log
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure project root is in sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "pi-agent-harness"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "pi-agent-cli"))

from pi_agent_cli.benchmarks import (  # noqa: E402
    OFFICIAL_ALLOWED_HOSTS,
    BenchmarkSummary,
    discover_tasks,
    print_summary_table,
    provision_golden_seeds,
    run_trial,
    save_summary_artifacts,
)
from pi_agent_cli.benchmarks.docker_runner import get_docker_cmd, run_docker_sync  # noqa: E402
from pi_agent_cli.benchmarks.golden_seed import get_git_commit, load_golden_manifest  # noqa: E402
from pi_agent_cli.config import load_config, pi_home  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pi-python Code Agent Benchmark Runner")
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="Task name(s) or ID(s) to evaluate. Can be specified multiple times.",
    )
    parser.add_argument(
        "--frontier-30",
        action="store_true",
        help="Run the complete FrontierHarness 30-task benchmark suite (benchmarks/frontier_30).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all tasks found in the tasks directory.",
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=_REPO_ROOT / "benchmarks" / "tasks",
        help="Path to tasks directory (default: benchmarks/tasks).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / ".pi-eval" / "runs",
        help="Root directory for storing run artifacts.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Custom run ID (default: timestamp-based).",
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Disable Docker sandbox execution even if Docker is available.",
    )
    parser.add_argument(
        "--provision",
        action="store_true",
        help=(
            "Provision golden seeds for tasks without running evaluation "
            "(extract initial /app workspaces)."
        ),
    )
    parser.add_argument(
        "--seeds-dir",
        type=Path,
        default=None,
        help=(
            "Directory for golden seeds (default: /tmp/pi-golden-seeds on Unix, or .pi-eval/seeds)."
        ),
    )
    parser.add_argument(
        "--egress-mode",
        choices=["auto", "no-network", "allowlist", "open"],
        default="auto",
        help="Container egress isolation mode: auto (default), no-network, allowlist, open.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Override max turns per task.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override model ID (defaults to agent.toml).",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Override provider (defaults to agent.toml).",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()

    # Check API key in environment
    home = pi_home()
    config = load_config(home)

    if args.model or args.provider:
        from dataclasses import replace

        overrides: dict[str, str] = {}
        if args.model:
            if ":" in args.model:
                prov, mid = args.model.split(":", 1)
                overrides["provider"] = prov
                overrides["model_id"] = mid
            else:
                overrides["model_id"] = args.model
        if args.provider:
            overrides["provider"] = args.provider
        config = replace(config, **overrides)

    if args.frontier_30:
        tasks_dir = (_REPO_ROOT / "benchmarks" / "frontier_30").resolve()
        filter_names = args.tasks if args.tasks else None
    else:
        tasks_dir = args.tasks_dir.resolve()
        filter_names = args.tasks if not args.all else None

    if not tasks_dir.is_dir():
        print(f"Error: Tasks directory does not exist: {tasks_dir}", file=sys.stderr)
        return 1

    tasks = discover_tasks(tasks_dir, filter_names=filter_names)
    if not tasks:
        print(f"No tasks found in {tasks_dir} matching {args.tasks}", file=sys.stderr)
        return 1

    default_seeds_dir = (
        Path("/tmp/pi-golden-seeds")
        if sys.platform != "win32"
        else (_REPO_ROOT / ".pi-eval" / "seeds")
    )
    seeds_dir = args.seeds_dir or default_seeds_dir

    if args.provision:
        print(f"\n[PROVISION] Target seeds directory: {seeds_dir}")
        manifest_file = provision_golden_seeds(tasks, seeds_dir=seeds_dir, verbose=True)
        print(f"[PROVISION] Golden manifest written to: {manifest_file}")
        return 0

    key_env = config.api_key_env or "REAL_LLM_API_KEY"
    api_key = os.environ.get(key_env)
    if not api_key:
        print(f"Error: {key_env} environment variable is not set.", file=sys.stderr)
        print(
            "Please export your API key (e.g. . $env:USERPROFILE\\.pi-python\\local.env.ps1).",
            file=sys.stderr,
        )
        return 1

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = args.out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 70}")
    print(f" Starting pi-python Benchmark Run: {run_id}")
    print(f" Tasks: {len(tasks)} to evaluate")
    print(f" Model: {config.provider}:{config.model_id}")
    print(f" Output: {run_dir}")
    print(f"{'=' * 70}\n")

    trials = []
    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for idx, task in enumerate(tasks, 1):
        print(f"[{idx}/{len(tasks)}] Evaluating {task.name} ({task.id})...")
        safe_name = task.name.replace("/", "-")
        trial_dir = run_dir / "trials" / safe_name

        trial = await run_trial(
            task,
            trial_dir=trial_dir,
            config=config,
            home=home,
            max_turns=args.max_turns,
            verbose=True,
            use_docker=not args.no_docker,
            seeds_dir=seeds_dir,
            egress_mode=args.egress_mode,
        )
        trials.append(trial)

    summary = BenchmarkSummary.from_trials(
        run_id=run_id,
        harness="pi-python",
        model=config.model_id,
        provider=config.provider,
        started_at=started_at,
        trials=trials,
    )

    print_summary_table(summary)
    report_file = save_summary_artifacts(summary, run_dir)
    print(f"Report saved to: {report_file}")
    print(f"Summary JSON:   {run_dir / 'eval-summary.json'}")

    # Record environment fingerprint in run.json
    golden_manifest_data = load_golden_manifest(seeds_dir)
    golden_manifest_path = seeds_dir / "golden-manifest.json" if seeds_dir.is_dir() else None

    # Detect Docker version for reproducibility metadata
    docker_version: str | None = None
    if not args.no_docker and get_docker_cmd():
        try:
            ver_res = run_docker_sync(["version", "--format", "{{.Server.Version}}"], timeout=5.0)
            if ver_res.returncode == 0 and ver_res.stdout.strip():
                docker_version = ver_res.stdout.strip()
        except Exception:
            pass

    run_manifest = {
        "run_id": run_id,
        "pi_python_commit": get_git_commit(_REPO_ROOT),
        "started_at": started_at,
        "finished_at": summary.finished_at,
        "harness": "pi-python",
        "model": f"{config.provider}:{config.model_id}",
        "tasks_dir": str(tasks_dir),
        "total_tasks": len(tasks),
        "seeds_dir": str(seeds_dir) if seeds_dir.is_dir() else None,
        "golden_manifest": (
            str(golden_manifest_path)
            if golden_manifest_path and golden_manifest_path.is_file()
            else None
        ),
        "has_golden_seeds": golden_manifest_data is not None,
        "egress_mode": args.egress_mode,
        "egress_policy": {
            "mode": args.egress_mode,
            "allowed_hosts": (
                OFFICIAL_ALLOWED_HOSTS if args.egress_mode in ("auto", "allowlist") else []
            ),
        },
        "runtime": {
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "docker_available": not args.no_docker,
            "docker_version": docker_version,
            "cpus_per_task": 2,
            "memory_mb_per_task": 8192,
        },
        "pricing_table": "kimi_k3_2026_08_20",
        "methodology_comparable": False,
        "summary": {
            "pass_rate": summary.pass_rate,
            "successful_tasks": summary.successful_tasks,
            "completed_tasks": summary.completed_tasks,
            "total_cost_usd": summary.total_cost_usd,
            "effective_cost_per_pass": summary.effective_cost_per_pass,
            "effective_cost_per_pass_kimi_k3": summary.effective_cost_per_pass_kimi_k3,
            "typical_cache_hit_rate": summary.typical_cache_hit_rate,
        },
    }
    run_json_path = run_dir / "run.json"
    run_json_path.write_text(
        json.dumps(run_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Run Manifest:   {run_json_path}\n")

    return 0 if summary.successful_tasks == summary.completed_tasks else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
