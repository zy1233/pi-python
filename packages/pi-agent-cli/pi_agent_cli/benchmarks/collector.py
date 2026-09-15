"""Report collector and summarizer for benchmark evaluations."""

from __future__ import annotations

from pathlib import Path

from pi_agent_cli.benchmarks.models import BenchmarkSummary


def format_markdown_report(summary: BenchmarkSummary) -> str:
    """Generate a clean Markdown evaluation report."""
    pass_pct = f"{summary.pass_rate * 100:.1f}%"
    cache_pct = f"{summary.typical_cache_hit_rate * 100:.1f}%"
    cost_per_pass = (
        f"${summary.effective_cost_per_pass:.4f}"
        if summary.effective_cost_per_pass is not None
        else "N/A"
    )
    tokens_per_pass = (
        f"{summary.tokens_per_solved:,.0f}" if summary.tokens_per_solved is not None else "N/A"
    )

    md = [
        f"# Benchmark Evaluation Report: {summary.run_id}",
        "",
        f"- **Harness**: `{summary.harness}`",
        f"- **Model**: `{summary.model}` ({summary.provider})",
        f"- **Time**: `{summary.started_at}` to `{summary.finished_at}`",
        f"- **Tasks**: {summary.successful_tasks} / {summary.completed_tasks} passed ({pass_pct})",
        "",
        "## Key Metrics",
        "",
        "| Metric | Value | Meaning |",
        "| :--- | :--- | :--- |",
        f"| **Pass Rate** | **{pass_pct}** | Solved and verified tasks |",
        f"| **Effective Cost / Pass** | **{cost_per_pass}** | Amortized cost per passed task |",
    ]
    if summary.effective_cost_per_pass_kimi_k3 is not None:
        md.append(
            f"| **Normalized Cost / Pass (Kimi K3)** | "
            f"**${summary.effective_cost_per_pass_kimi_k3:.4f}** | "
            "Amortized cost normalized to official benchmark pricing |"
        )
    md.extend(
        [
            f"| **Tokens / Solved** | **{tokens_per_pass}** | Amortized tokens per passed task |",
            f"| **Total Cost** | ${summary.total_cost_usd:.4f} | Total API spend for the run |",
            f"| **Typical Cache Hit Rate** | {cache_pct} | Prompt caching efficiency |",
            f"| **Mean Turns** | {summary.mean_turns:.1f} | Average interaction turns |",
            (
                f"| **Mean No-Action Turns** | {summary.mean_no_action_turns:.1f} | "
                "Turns without file/shell operations (overhead) |"
            ),
            (
                f"| **Median Duration** | {summary.median_duration_seconds:.1f}s | "
                "Median elapsed time |"
            ),
            "",
            "## Task Results",
            "",
            (
                "| Status | Task ID | Duration | Turns (No-Act) | "
                "Tokens | Cache | Cost | Norm Cost (K3) |"
            ),
            "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
    )

    for t in summary.trials:
        icon = "✅" if t.success else "❌"
        cache_str = f"{t.cache_hit_rate_normalized * 100:.0f}%"
        k3_cost = (
            f"${t.cost_kimi_k3_normalized_usd:.4f}"
            if t.cost_kimi_k3_normalized_usd is not None
            else "N/A"
        )
        md.append(
            f"| {icon} | `{t.id}` | {t.duration_seconds:.1f}s | "
            f"{t.turns} ({t.no_action_turns}) | {t.total_tokens:,} | "
            f"{cache_str} | ${t.cost_first_cold_usd:.4f} | {k3_cost} |"
        )

    md.append("")
    return "\n".join(md)


def print_summary_table(summary: BenchmarkSummary) -> None:
    """Print an ASCII table of results to standard output."""
    pass_pct = f"{summary.pass_rate * 100:.1f}%"
    cost_per_pass = (
        f"${summary.effective_cost_per_pass:.4f}"
        if summary.effective_cost_per_pass is not None
        else "N/A"
    )

    print("\n" + "=" * 70)
    print(f"  BENCHMARK RUN SUMMARY: {summary.run_id}")
    print(f"  Model: {summary.provider}:{summary.model}")
    print("=" * 70)
    print(f"  Passed:         {summary.successful_tasks}/{summary.completed_tasks} ({pass_pct})")
    print(f"  Effective Cost: {cost_per_pass}")
    if summary.effective_cost_per_pass_kimi_k3 is not None:
        print(f"  Norm Cost (K3): ${summary.effective_cost_per_pass_kimi_k3:.4f}")
    print(f"  Total Cost:     ${summary.total_cost_usd:.4f}")
    print(f"  Typical Cache:  {summary.typical_cache_hit_rate * 100:.1f}%")
    print(f"  Mean Turns (No-Act):   {summary.mean_turns:.1f} ({summary.mean_no_action_turns:.1f})")
    print("-" * 70)
    print(f"  {'Status':<8} {'Task ID':<30} {'Turns':<8} {'Tokens':<10} {'Cost':<10}")
    print("-" * 70)
    for t in summary.trials:
        stat = "PASS" if t.success else "FAIL"
        cost_str = f"${t.cost_first_cold_usd:<9.4f}"
        print(f"  {stat:<8} {t.title:<30} {t.turns:<8} {t.total_tokens:<10} {cost_str}")
    print("=" * 70 + "\n")


def save_summary_artifacts(
    summary: BenchmarkSummary,
    output_dir: Path,
) -> Path:
    """Save summary JSON and Markdown report into output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_json_path = output_dir / "eval-summary.json"
    summary_json_path.write_text(summary.to_json(indent=2), encoding="utf-8")

    report_md_path = output_dir / "REPORT.md"
    report_md_path.write_text(format_markdown_report(summary), encoding="utf-8")

    return report_md_path
