"""Tests for Golden Seed checkpoint, egress policy, and pricing normalization."""

from __future__ import annotations

import json
from pathlib import Path

from pi_agent_cli.benchmarks.egress_policy import (
    OFFICIAL_ALLOWED_HOSTS,
    resolve_task_egress_policy,
)
from pi_agent_cli.benchmarks.evaluator import (
    DEFAULT_PRICING,
    KIMI_K3_PRICING,
    calculate_first_cold_cost,
)
from pi_agent_cli.benchmarks.golden_seed import (
    get_seed_dir,
    get_seed_dir_name,
    is_seed_ready,
    load_golden_manifest,
)
from pi_agent_cli.benchmarks.models import BenchmarkSummary, BenchmarkTask, TrialResult


def test_seed_dir_helpers(tmp_path: Path):
    assert get_seed_dir_name("terminal-bench/regex-log") == "terminal-bench_regex-log"
    assert get_seed_dir_name("calc-eval") == "calc-eval"

    task = BenchmarkTask(
        id="terminal-bench/regex-log",
        name="regex-log",
        suite="terminal-bench",
        instruction="Do something",
        task_dir=tmp_path / "dummy",
    )

    # Not ready initially
    assert not is_seed_ready(tmp_path, task)
    assert get_seed_dir(tmp_path, task) is None

    # Create seed directory with a dummy file
    seed_dir = tmp_path / "terminal-bench_regex-log"
    seed_dir.mkdir(parents=True)
    (seed_dir / "app.py").write_text("print('hello')", encoding="utf-8")

    assert is_seed_ready(tmp_path, task)
    assert get_seed_dir(tmp_path, task) == seed_dir

    # Manifest loading
    assert load_golden_manifest(tmp_path) is None
    manifest_data = {"version": "1.0", "pi_python_commit": "abc"}
    (tmp_path / "golden-manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    assert load_golden_manifest(tmp_path) == manifest_data


def test_egress_policy_no_network(tmp_path: Path):
    task = BenchmarkTask(
        id="datacurve/httpx-patch",
        name="httpx-patch",
        suite="datacurve",
        instruction="Fix bug",
        task_dir=tmp_path,
        metadata={"agent": {"network_mode": "no-network"}},
    )

    policy = resolve_task_egress_policy(task, egress_mode="auto")
    assert policy.mode == "no-network"
    assert policy.docker_run_args() == ["--network", "none"]
    assert policy.allowed_hosts == []


def test_egress_policy_allowlist(tmp_path: Path):
    task = BenchmarkTask(
        id="terminal-bench/regex-log",
        name="regex-log",
        suite="terminal-bench",
        instruction="Fix regex",
        task_dir=tmp_path,
        metadata={"environment": {"allow_internet": True}},
    )

    policy = resolve_task_egress_policy(
        task, egress_mode="auto", custom_proxy="http://proxy.local:8080"
    )
    assert policy.mode == "allowlist"
    assert "astral.sh" in policy.allowed_hosts
    assert len(policy.allowed_hosts) == len(OFFICIAL_ALLOWED_HOSTS)
    args = policy.docker_run_args()
    assert "-e" in args
    assert "http_proxy=http://proxy.local:8080" in args


def test_egress_policy_override(tmp_path: Path):
    task = BenchmarkTask(
        id="terminal-bench/regex-log",
        name="regex-log",
        suite="terminal-bench",
        instruction="Fix regex",
        task_dir=tmp_path,
        metadata={"environment": {"allow_internet": True}},
    )

    # Force no-network
    forced_no_net = resolve_task_egress_policy(task, egress_mode="no-network")
    assert forced_no_net.mode == "no-network"
    assert forced_no_net.docker_run_args() == ["--network", "none"]

    # Open mode
    open_pol = resolve_task_egress_policy(task, egress_mode="open")
    assert open_pol.mode == "open"
    assert open_pol.allowed_hosts == ["*"]


def test_first_cold_cost_calculation():
    # 1,000,000 uncached input, 0 cached, 100,000 output
    # DEFAULT_PRICING: input 1.0, output 2.0 -> 1.0 + 0.2 = 1.2
    cost = calculate_first_cold_cost(
        input_tokens=1_000_000,
        cached_tokens=0,
        output_tokens=100_000,
        first_turn_cached=0,
        pricing=DEFAULT_PRICING,
    )
    assert round(cost, 4) == 1.2

    # With cached tokens: 500k cached (rate 0.1), 500k uncached (rate 1.0), 0 out -> 0.55
    # If first_turn_cached is 500,000, it is repriced at cold rate:
    # cold_adjustment = 500,000 * (1.0 - 0.1) / 1M = 0.45
    # Total = 0.55 + 0.45 = 1.0
    cost_cold = calculate_first_cold_cost(
        input_tokens=1_000_000,
        cached_tokens=500_000,
        output_tokens=0,
        first_turn_cached=500_000,
        pricing=DEFAULT_PRICING,
    )
    assert round(cost_cold, 4) == 1.0

    # Test KIMI_K3_PRICING:
    # input 3.0, cached 0.30, output 15.0
    k3_cost = calculate_first_cold_cost(
        input_tokens=1_000_000,
        cached_tokens=0,
        output_tokens=100_000,
        first_turn_cached=0,
        pricing=KIMI_K3_PRICING,
    )
    # 3.0 + 1.5 = 4.5
    assert round(k3_cost, 4) == 4.5


def test_benchmark_summary_with_normalized_cost():
    t1 = TrialResult(
        id="test/task-1",
        title="task-1",
        suite="test",
        status="success",
        success=True,
        duration_seconds=10.0,
        cost_first_cold_usd=0.10,
        cost_kimi_k3_normalized_usd=0.30,
        turns=5,
    )
    t2 = TrialResult(
        id="test/task-2",
        title="task-2",
        suite="test",
        status="failure",
        success=False,
        duration_seconds=20.0,
        cost_first_cold_usd=0.20,
        cost_kimi_k3_normalized_usd=0.60,
        turns=10,
    )
    summary = BenchmarkSummary.from_trials(
        run_id="test-run",
        harness="pi-python",
        model="deepseek",
        provider="deepseek",
        started_at="2026-09-15T00:00:00Z",
        trials=[t1, t2],
    )

    assert summary.total_tasks == 2
    assert summary.successful_tasks == 1
    assert summary.pass_rate == 0.5
    # total cost = 0.10 + 0.20 = 0.30, 1 pass -> 0.30 / 1 = 0.30
    assert summary.effective_cost_per_pass == 0.30
    # normalized kimi k3 total = 0.30 + 0.60 = 0.90, 1 pass -> 0.90 / 1 = 0.90
    assert summary.effective_cost_per_pass_kimi_k3 == 0.90
