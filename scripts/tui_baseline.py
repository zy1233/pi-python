#!/usr/bin/env python3
"""Stage-0 baseline and gates for the TUI (Rust) workspace.

Context: docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md (stage 0) and docs/baselines/tui.md.
Config:  scripts/tui_baseline.toml. Standard library only (Python >= 3.11).

    python scripts/tui_baseline.py gates     # dependency-graph and source gates (no compilation)
    python scripts/tui_baseline.py check     # gates + cargo check (consumer build, workspace tests)
    python scripts/tui_baseline.py test      # cargo test per suite
    python scripts/tui_baseline.py release   # release-dist build: binary size, build time, startup

Every command streams the underlying cargo output, writes JSON and Markdown under --out and, when
$GITHUB_STEP_SUMMARY is set, appends the Markdown there. The exit status is non-zero when an
*enforced* check fails (see the `enforce*` keys in the manifest).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("tui_baseline.toml")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TEST_RESULT_RE = re.compile(
    r"^test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
    r"\d+ measured; (\d+) filtered out"
)
TEST_FAILED_RE = re.compile(r"^test (\S+) \.\.\. FAILED$")
RUSTC_SUMMARY_RE = re.compile(r"^(?:\d+ warnings? emitted|aborting due to .*)$")
TEXT_SUFFIXES = frozenset(
    {".rs", ".toml", ".md", ".txt", ".json", ".yaml", ".yml", ".sh", ".py", ".proto", ".html"}
)
SKIP_DIRS = frozenset({"target", ".git", "node_modules"})
REFERENCE_TOLERANCE = 0.05


# --------------------------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    status: str  # "pass" | "fail" | "info"
    detail: str = ""


@dataclass
class SuiteTotals:
    passed: int = 0
    failed: int = 0
    ignored: int = 0
    filtered: int = 0
    result_lines: int = 0
    failed_tests: list[str] = field(default_factory=list)


@dataclass
class WarningStats:
    total: int = 0
    by_lint: dict[str, int] = field(default_factory=dict)
    by_crate: dict[str, int] = field(default_factory=dict)


@dataclass
class SuiteResult:
    package: str
    targets: list[str]
    status: str  # "pass" | "fail"
    passed: int
    failed: int
    ignored: int
    exit_code: int
    timed_out: bool
    seconds: float
    reference_passed: int | None = None
    unexpected_failures: list[str] = field(default_factory=list)
    known_failures_seen: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------------------------


def parse_test_output(text: str) -> SuiteTotals:
    """Aggregate libtest `test result:` lines and collect the names of failed tests."""
    totals = SuiteTotals()
    for raw in text.splitlines():
        line = ANSI_RE.sub("", raw).rstrip()
        if m := TEST_RESULT_RE.match(line):
            passed, failed, ignored, filtered = (int(g) for g in m.groups())
            totals.passed += passed
            totals.failed += failed
            totals.ignored += ignored
            totals.filtered += filtered
            totals.result_lines += 1
        elif m := TEST_FAILED_RE.match(line):
            totals.failed_tests.append(m.group(1))
    return totals


def count_tree_nodes(text: str) -> int:
    """Unique packages in `cargo tree --prefix none` output (repeat markers removed)."""
    nodes = set()
    for raw in text.splitlines():
        line = ANSI_RE.sub("", raw).strip()
        if not line or line.startswith("["):
            continue
        line = re.sub(r" \((?:\*|proc-macro)\)", "", line)
        nodes.add(line)
    return len(nodes)


def cargo_messages(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """`compiler-message` records of `cargo --message-format=json` output."""
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("reason") == "compiler-message":
            yield record


def is_workspace_record(record: dict[str, Any]) -> bool:
    return "path+file://" in str(record.get("package_id", ""))


def count_warnings(lines: Iterable[str]) -> WarningStats:
    """Warnings emitted by workspace crates (third-party warnings are capped by cargo)."""
    stats = WarningStats()
    for record in cargo_messages(lines):
        message = record["message"]
        if message.get("level") != "warning" or RUSTC_SUMMARY_RE.match(message.get("message", "")):
            continue
        if not is_workspace_record(record):
            continue
        lint = (message.get("code") or {}).get("code") or "other"
        crate = record.get("target", {}).get("name", "?")
        stats.total += 1
        stats.by_lint[lint] = stats.by_lint.get(lint, 0) + 1
        stats.by_crate[crate] = stats.by_crate.get(crate, 0) + 1
    return stats


def collect_errors(lines: Iterable[str]) -> list[str]:
    """Compile errors as `crate: message`, without rustc's trailing summary."""
    errors = []
    for record in cargo_messages(lines):
        message = record["message"]
        if message.get("level") != "error" or RUSTC_SUMMARY_RE.match(message.get("message", "")):
            continue
        crate = record.get("target", {}).get("name", "?")
        errors.append(f"{crate}: {message.get('message', '').splitlines()[0]}")
    return errors


def scan_words(root: Path, words: Sequence[str]) -> list[str]:
    """`path:line: text` for every whole-word occurrence of any word under `root`."""
    if not words:
        return []
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b")
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath, name)
            if path.suffix not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path}:{number}: {line.strip()[:120]}")
    return hits


def evaluate_suite(
    suite: dict[str, Any],
    totals: SuiteTotals,
    *,
    exit_code: int,
    timed_out: bool,
    seconds: float,
) -> SuiteResult:
    """Classify one `cargo test` invocation against the manifest entry."""
    known = suite.get("known_failures", [])
    unexpected = [t for t in totals.failed_tests if not any(t.endswith(k) for k in known)]
    known_seen = [t for t in totals.failed_tests if t not in unexpected]
    notes: list[str] = []
    failed = False

    if timed_out:
        failed = True
        notes.append("timed out (a test hangs, or the build is stuck)")
    elif exit_code != 0 and totals.result_lines == 0:
        failed = True
        notes.append("build or harness error: no test results were produced")
    elif exit_code != 0 and not totals.failed_tests:
        failed = True
        notes.append("non-zero exit without a failed test (crash or abort?)")
    if unexpected:
        failed = True
        notes.append(f"{len(unexpected)} unexpected failure(s)")
    if known_seen:
        notes.append(f"{len(known_seen)} known failure(s)")

    minimum = suite.get("min_passed")
    if minimum is not None and totals.passed < minimum:
        failed = True
        notes.append(f"passed {totals.passed} < floor {minimum}")
    reference = suite.get("reference_passed")
    if reference and abs(totals.passed - reference) > reference * REFERENCE_TOLERANCE:
        notes.append(f"passed {totals.passed} deviates >5% from the macOS reference {reference}")

    return SuiteResult(
        package=suite["package"],
        targets=list(suite.get("targets", [])),
        status="fail" if failed else "pass",
        passed=totals.passed,
        failed=totals.failed,
        ignored=totals.ignored,
        exit_code=exit_code,
        timed_out=timed_out,
        seconds=round(seconds, 1),
        reference_passed=reference,
        unexpected_failures=unexpected,
        known_failures_seen=known_seen,
        notes=notes,
    )


# --------------------------------------------------------------------------------------------
# Process helpers
# --------------------------------------------------------------------------------------------


def log(message: str) -> None:
    print(message, flush=True)


@contextlib.contextmanager
def log_group(title: str):
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    log(f"::group::{title}" if in_actions else f"=== {title}")
    try:
        yield
    finally:
        if in_actions:
            log("::endgroup::")


def cargo(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["cargo", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def stream(
    cmd: Sequence[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    timeout_s: float | None = None,
    merge_stderr: bool,
    on_line: Callable[[str], None] | None = None,
) -> tuple[int, str, bool]:
    """Run `cmd`, echo and collect stdout (stderr too if `merge_stderr`), kill on timeout."""
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover - Windows
                proc.kill()

    timer = threading.Timer(timeout_s, kill) if timeout_s else None
    if timer:
        timer.start()
    collected: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            collected.append(line)
            if on_line:
                on_line(line)
        proc.wait()
    finally:
        if timer:
            timer.cancel()
    return proc.returncode, "".join(collected), timed_out.is_set()


def echo_diagnostic(line: str) -> None:
    """Show the rendered text of cargo JSON diagnostics so CI logs stay readable."""
    if not line.startswith("{"):
        return
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return
    if record.get("reason") == "compiler-message":
        rendered = record["message"].get("rendered")
        if rendered:
            print(rendered, end="", flush=True)


def tool_versions(cwd: Path) -> dict[str, str]:
    versions = {}
    for name, cmd in (("rustc", ["rustc", "-vV"]), ("cargo", ["cargo", "-V"])):
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        versions[name] = proc.stdout.strip().replace("\n", "; ")
    return versions


# --------------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------------


def run_gates(manifest: dict[str, Any], tui_dir: Path) -> tuple[list[Check], dict[str, Any]]:
    """Static gates. Returns the checks and extra data (graph sizes, tracked-crate dependents)."""
    checks: list[Check] = []
    extra: dict[str, Any] = {"graph": {}, "tracked": {}}
    package = manifest["graph"]["package"]
    gates = manifest["gates"]

    pinned = tomllib.loads((tui_dir / "rust-toolchain.toml").read_text(encoding="utf-8"))[
        "toolchain"
    ]["channel"]
    expected = manifest["toolchain"]["channel"]
    checks.append(
        Check(
            "toolchain pin",
            "pass" if pinned == expected else "fail",
            f"tui/rust-toolchain.toml={pinned}, manifest={expected}",
        )
    )

    lock = (tui_dir / "Cargo.lock").read_text(encoding="utf-8")
    for crate in gates["deny_crates"]:
        in_lock = re.search(rf'^name = "{re.escape(crate)}"$', lock, re.MULTILINE) is not None
        proc = cargo(["tree", "--locked", "-p", package, "-i", crate, "--prefix", "none"], tui_dir)
        absent = proc.returncode != 0 and "did not match any packages" in proc.stderr
        problem = "still in Cargo.lock" if in_lock else "still in the dependency graph"
        checks.append(
            Check(
                f"deny crate {crate}",
                "pass" if absent and not in_lock else "fail",
                "absent from Cargo.lock and the graph" if absent and not in_lock else problem,
            )
        )

    hits = scan_words(tui_dir / "crates", gates["deny_words"])
    words = ", ".join(gates["deny_words"])
    checks.append(
        Check(
            f"deny words ({words})",
            "pass" if not hits else "fail",
            "no occurrences under tui/crates" if not hits else "; ".join(hits[:5]),
        )
    )

    for triple, ceiling in manifest["graph"].get("max_nodes", {}).items():
        proc = cargo(
            ["tree", "--locked", "-p", package, "--target", triple, "--prefix", "none"], tui_dir
        )
        if proc.returncode != 0:
            checks.append(Check(f"graph nodes {triple}", "fail", proc.stderr.strip()[-300:]))
            continue
        nodes = count_tree_nodes(proc.stdout)
        extra["graph"][triple] = nodes
        checks.append(
            Check(
                f"graph nodes {triple}",
                "pass" if nodes <= ceiling else "fail",
                f"{nodes} packages (ceiling {ceiling})",
            )
        )

    for crate in gates.get("tracked_crates", []):
        proc = cargo(
            ["tree", "--locked", "-p", package, "-i", crate, "--depth", "1", "--prefix", "none"],
            tui_dir,
        )
        dependents = [line.split(" ")[0] for line in proc.stdout.splitlines()[1:] if line.strip()]
        extra["tracked"][crate] = dependents if proc.returncode == 0 else None
    return checks, extra


def run_check(manifest: dict[str, Any], tui_dir: Path) -> tuple[dict[str, Any], bool]:
    cfg = manifest["check"]
    checks, extra = run_gates(manifest, tui_dir)
    report: dict[str, Any] = {"tools": tool_versions(tui_dir), **extra}
    ok = all(c.status != "fail" for c in checks)

    cmd = ["cargo", "check", "--locked", "--message-format=json"]
    for package in cfg["packages"]:
        cmd += ["-p", package]
    with log_group("cargo check (consumer build): " + " ".join(cfg["packages"])):
        started = time.monotonic()
        code, out, _ = stream(
            cmd, tui_dir, timeout_s=3600, merge_stderr=False, on_line=echo_diagnostic
        )
        seconds = time.monotonic() - started
    lines = out.splitlines()
    stats = count_warnings(lines)
    report["consumer_build"] = {"exit_code": code, "seconds": round(seconds, 1)}
    report["warnings"] = asdict(stats)
    checks.append(
        Check("cargo check (consumer build)", "pass" if code == 0 else "fail", f"exit {code}")
    )
    ok = ok and code == 0
    ceiling = cfg.get("max_warnings")
    if ceiling is None:
        checks.append(Check("warnings", "info", f"{stats.total} (report only)"))
    else:
        within = stats.total <= ceiling
        checks.append(
            Check("warnings", "pass" if within else "fail", f"{stats.total} (ceiling {ceiling})")
        )
        ok = ok and within

    if cfg.get("workspace_tests"):
        cmd = ["cargo", "check", "--locked", "--workspace", "--tests", "--keep-going"]
        cmd.append("--message-format=json")
        for excluded in cfg.get("workspace_tests_exclude", []):
            cmd += ["--exclude", excluded]
        with log_group("cargo check --workspace --tests"):
            started = time.monotonic()
            code, out, _ = stream(
                cmd, tui_dir, timeout_s=3600, merge_stderr=False, on_line=echo_diagnostic
            )
            seconds = time.monotonic() - started
        errors = collect_errors(out.splitlines())
        enforced = bool(cfg.get("enforce_workspace_tests"))
        report["workspace_tests"] = {
            "exit_code": code,
            "seconds": round(seconds, 1),
            "errors": errors,
            "enforced": enforced,
        }
        status = "pass" if code == 0 else ("fail" if enforced else "info")
        detail = f"exit {code}, {len(errors)} error(s)" + ("" if enforced else " (report only)")
        checks.append(Check("cargo check --workspace --tests", status, detail))
        ok = ok and (code == 0 or not enforced)

    report["checks"] = [asdict(c) for c in checks]
    report["ok"] = ok
    return report, ok


def select_suites(suites: Sequence[dict[str, Any]], only: Sequence[str]) -> list[dict[str, Any]]:
    """The manifest suites to run; ``only`` (package names) narrows them, unknown names fail."""
    if not only:
        return list(suites)
    known = {suite["package"] for suite in suites}
    if unknown := sorted(set(only) - known):
        raise SystemExit(f"unknown suite(s): {', '.join(unknown)} (manifest has: {sorted(known)})")
    return [suite for suite in suites if suite["package"] in only]


def run_tests(
    manifest: dict[str, Any], tui_dir: Path, only: Sequence[str] = ()
) -> tuple[dict[str, Any], bool]:
    cfg = manifest["test"]
    env = os.environ.copy()
    env.update(cfg.get("env", {}))
    for key in cfg.get("unset_env", []):
        env.pop(key, None)
    timeout_s = float(cfg.get("timeout_minutes", 60)) * 60

    results: list[SuiteResult] = []
    for suite in select_suites(cfg["suite"], only):
        # --no-fail-fast: a failing lib test must not hide the integration targets behind it.
        cmd = [
            "cargo",
            "test",
            "--locked",
            "--no-fail-fast",
            "-p",
            suite["package"],
            *suite.get("targets", []),
        ]
        skips = suite.get("skip", [])
        if skips:
            cmd += ["--", *(arg for s in skips for arg in ("--skip", s))]
        with log_group(" ".join(cmd)):
            started = time.monotonic()
            code, out, timed_out = stream(
                cmd, tui_dir, env=env, timeout_s=timeout_s, merge_stderr=True, on_line=print
            )
            seconds = time.monotonic() - started
        results.append(
            evaluate_suite(
                suite,
                parse_test_output(out),
                exit_code=code,
                timed_out=timed_out,
                seconds=seconds,
            )
        )
    ok = all(r.status == "pass" for r in results)
    report = {
        "tools": tool_versions(tui_dir),
        "enforced": bool(cfg.get("enforce")),
        "suites": [asdict(r) for r in results],
        "totals": {
            "passed": sum(r.passed for r in results),
            "failed": sum(r.failed for r in results),
            "ignored": sum(r.ignored for r in results),
        },
        "ok": ok,
    }
    return report, ok or not cfg.get("enforce")


def run_release(manifest: dict[str, Any], tui_dir: Path) -> tuple[dict[str, Any], bool]:
    cfg = manifest["release"]
    reference = cfg["reference"]
    cmd = ["cargo", "build", "--locked", "--profile", cfg["profile"], "-p", cfg["package"]]
    with log_group(" ".join(cmd)):
        started = time.monotonic()
        code, _, timed_out = stream(
            cmd, tui_dir, timeout_s=3 * 3600, merge_stderr=True, on_line=print
        )
        seconds = time.monotonic() - started
    binary = tui_dir / "target" / cfg["profile"] / cfg["binary"]
    checks = [
        Check("release build", "pass" if code == 0 and not timed_out else "fail", f"exit {code}")
    ]
    report: dict[str, Any] = {
        "tools": tool_versions(tui_dir),
        "build_seconds": round(seconds, 1),
        "reference": reference,
    }
    if code == 0 and binary.exists():
        size = binary.stat().st_size
        latencies = []
        for _ in range(5):
            began = time.monotonic()
            subprocess.run([str(binary), "--version"], capture_output=True, check=False, timeout=60)
            latencies.append((time.monotonic() - began) * 1000)
        report["binary_bytes"] = size
        report["version_latency_ms"] = round(statistics.median(latencies), 1)
        delta = (size / reference["size_bytes"] - 1) * 100
        checks.append(
            Check(
                "binary size not above the pre-removal baseline",
                "pass" if size <= reference["size_bytes"] else "fail",
                f"{size:,} B vs {reference['size_bytes']:,} B ({delta:+.1f} %)",
            )
        )
    report["checks"] = [asdict(c) for c in checks]
    report["ok"] = all(c.status != "fail" for c in checks)
    return report, report["ok"]


# --------------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------------

ICON = {"pass": "✅", "fail": "❌", "info": "🔹"}


def render_checks(checks: Sequence[dict[str, Any]]) -> list[str]:
    rows = ["| Check | Result | Detail |", "|---|---|---|"]
    for c in checks:
        detail = str(c["detail"]).replace("|", "\\|")
        rows.append(f"| {c['name']} | {ICON.get(c['status'], c['status'])} | {detail} |")
    return rows


def render_markdown(command: str, report: dict[str, Any]) -> str:
    lines = [f"## TUI baseline — `{command}`", ""]
    tools = report.get("tools", {})
    if tools:
        lines += [f"`{tools.get('cargo', '')}`", ""]
    if "checks" in report:
        lines += [*render_checks(report["checks"]), ""]
    if "warnings" in report:
        w = report["warnings"]
        by_lint = ", ".join(f"{k} {v}" for k, v in sorted(w["by_lint"].items())) or "none"
        lines += [f"Warnings: **{w['total']}** ({by_lint})", ""]
    if report.get("graph"):
        sizes = ", ".join(f"{t}: {n}" for t, n in report["graph"].items())
        lines += [f"Dependency graph (unique packages): {sizes}", ""]
    if report.get("tracked"):
        lines += ["| Tracked crate | Direct dependents in `pi-pager-bin` |", "|---|---|"]
        for crate, dependents in report["tracked"].items():
            shown = ", ".join(dependents) if dependents else "(not in graph)"
            lines.append(f"| `{crate}` | {shown} |")
        lines.append("")
    if "suites" in report:
        mode = "enforced" if report["enforced"] else "report only"
        lines += [f"Tests ({mode})", "", "| Suite | Result | Passed | Failed | Ignored | Notes |"]
        lines.append("|---|---|---|---|---|---|")
        for s in report["suites"]:
            notes = "; ".join(s["notes"] + s["unexpected_failures"][:3])
            lines.append(
                f"| `{s['package']}` | {ICON[s['status']]} | {s['passed']} | {s['failed']} "
                f"| {s['ignored']} | {notes} |"
            )
        t = report["totals"]
        lines += [
            "",
            f"Total: **{t['passed']}** passed, {t['failed']} failed, {t['ignored']} ignored",
            "",
        ]
    if "binary_bytes" in report:
        ref = report["reference"]
        lines += [
            f"Binary: **{report['binary_bytes']:,} B** (pre-removal {ref['size_bytes']:,} B); "
            f"build **{report['build_seconds']:.0f} s** (pre-removal {ref['build_seconds']} s); "
            f"`--version` median {report['version_latency_ms']} ms",
            "",
        ]
    ws = report.get("workspace_tests")
    if ws and ws["errors"]:
        lines += ["Workspace test compile errors:", ""]
        lines += [f"- {e}" for e in ws["errors"][:20]] + [""]
    return "\n".join(lines)


def publish(command: str, report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{command}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(command, report)
    (out_dir / f"{command}.md").write_text(markdown + "\n", encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    log("\n" + markdown)


def use_utf8_streams() -> None:
    """The reports contain non-ASCII (— ✅ ❌); a legacy console encoding must not crash the run."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["gates", "check", "test", "release"])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--tui-dir", type=Path, default=REPO_ROOT / "tui")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tui-baseline")
    parser.add_argument(
        "--suite",
        action="append",
        default=[],
        metavar="PACKAGE",
        help="`test` only: run just this manifest suite (repeatable); default is all of them",
    )
    args = parser.parse_args(argv)

    manifest = tomllib.loads(args.manifest.read_text(encoding="utf-8"))
    tui_dir = args.tui_dir.resolve()

    if args.command == "gates":
        checks, extra = run_gates(manifest, tui_dir)
        report: dict[str, Any] = {"checks": [asdict(c) for c in checks], **extra}
        report["ok"] = ok = all(c.status != "fail" for c in checks)
    elif args.command == "check":
        report, ok = run_check(manifest, tui_dir)
    elif args.command == "test":
        report, ok = run_tests(manifest, tui_dir, args.suite)
    else:
        report, ok = run_release(manifest, tui_dir)

    publish(args.command, report, args.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
