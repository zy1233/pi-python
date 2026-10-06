"""Unit tests for scripts/tui_baseline.py (stage-0 baseline tooling for the TUI workspace)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tui_baseline.py"
MANIFEST = REPO_ROOT / "scripts" / "tui_baseline.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tui-ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location("tui_baseline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


tb = _load()


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


# ---- parsing --------------------------------------------------------------------------------

LIBTEST = """\
running 3 tests
test a::ok ... ok
test a::known_flaky ... FAILED
test a::boom ... FAILED

failures:
    a::known_flaky
    a::boom

test result: FAILED. 1 passed; 2 failed; 4 ignored; 0 measured; 7 filtered out; finished in 0.01s

running 2 tests
test b::ok ... ok
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""


def test_parse_test_output_aggregates_targets_and_collects_failures():
    totals = tb.parse_test_output(LIBTEST)
    assert (totals.passed, totals.failed, totals.ignored, totals.filtered) == (3, 2, 4, 7)
    assert totals.result_lines == 2
    assert totals.failed_tests == ["a::known_flaky", "a::boom"]


def test_parse_test_output_ignores_ansi_colour_codes():
    coloured = "\x1b[0mtest result: ok. 5 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out\n"
    totals = tb.parse_test_output(coloured)
    assert (totals.passed, totals.ignored, totals.result_lines) == (5, 1, 1)


def test_count_tree_nodes_dedupes_repeat_markers_and_headers():
    tree = "\n".join(
        [
            "app v1.0.0 (/w/app)",
            "serde v1.0.0",
            "serde v1.0.0 (*)",
            "serde_derive v1.0.0 (proc-macro)",
            "serde_derive v1.0.0",
            "[dev-dependencies]",
            "",
        ]
    )
    assert tb.count_tree_nodes(tree) == 3


def _message(level, text, *, package_id, crate="pi-shell", code="dead_code"):
    return json.dumps(
        {
            "reason": "compiler-message",
            "package_id": package_id,
            "target": {"name": crate},
            "message": {
                "level": level,
                "message": text,
                "code": {"code": code} if code else None,
                "rendered": f"{level}: {text}\n",
            },
        }
    )


WORKSPACE = "path+file:///w/crates/pi-shell#pi-shell@1.0.8"
THIRD_PARTY = "registry+https://github.com/rust-lang/crates.io-index#serde@1.0.0"


def test_count_warnings_counts_workspace_crates_only_and_skips_rustc_summary():
    lines = [
        _message("warning", "function `x` is never used", package_id=WORKSPACE),
        _message("warning", "unused import", package_id=WORKSPACE, code="unused_imports"),
        _message("warning", "something in a dependency", package_id=THIRD_PARTY, crate="serde"),
        _message("warning", "2 warnings emitted", package_id=WORKSPACE, code=None),
        _message("error", "boom", package_id=WORKSPACE),
        '{"reason": "compiler-artifact"}',
        "not json at all",
    ]
    stats = tb.count_warnings(lines)
    assert stats.total == 2
    assert stats.by_lint == {"dead_code": 1, "unused_imports": 1}
    assert stats.by_crate == {"pi-shell": 2}


def test_collect_errors_skips_the_abort_summary():
    lines = [
        _message("error", "cannot find value `x` in this scope\nmore", package_id=WORKSPACE),
        _message("error", "aborting due to 1 previous error", package_id=WORKSPACE, code=None),
    ]
    assert tb.collect_errors(lines) == ["pi-shell: cannot find value `x` in this scope"]


def test_scan_words_matches_whole_words_and_skips_target_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "src" / "a.rs").write_text(
        "struct MvpAgent;\nlet MvpAgentFactory = 1;\n", encoding="utf-8"
    )
    (tmp_path / "src" / "b.bin").write_text("MvpAgent", encoding="utf-8")  # not a text suffix
    (tmp_path / "target" / "c.rs").write_text("MvpAgent", encoding="utf-8")
    hits = tb.scan_words(tmp_path, ["MvpAgent"])
    assert len(hits) == 1
    assert hits[0].endswith("a.rs:1: struct MvpAgent;")
    assert tb.scan_words(tmp_path, []) == []


# ---- suite evaluation -------------------------------------------------------------------------


def _suite(**extra):
    return {"package": "pi-x", "targets": ["--lib"], **extra}


def _totals(passed=10, failed=0, failed_tests=(), result_lines=1):
    return tb.SuiteTotals(
        passed=passed, failed=failed, result_lines=result_lines, failed_tests=list(failed_tests)
    )


def _evaluate(suite, totals, *, exit_code=0, timed_out=False):
    return tb.evaluate_suite(suite, totals, exit_code=exit_code, timed_out=timed_out, seconds=1.0)


def test_evaluate_suite_passes_when_all_tests_pass():
    assert _evaluate(_suite(), _totals()).status == "pass"


def test_evaluate_suite_tolerates_known_failures_by_suffix():
    suite = _suite(known_failures=["render_footer"])
    result = _evaluate(
        suite, _totals(failed=1, failed_tests=["views::render_footer"]), exit_code=101
    )
    assert result.status == "pass"
    assert result.known_failures_seen == ["views::render_footer"]
    assert "1 known failure(s)" in result.notes


def test_evaluate_suite_fails_on_unexpected_failure():
    suite = _suite(known_failures=["render_footer"])
    totals = _totals(failed=2, failed_tests=["views::render_footer", "other::boom"])
    result = _evaluate(suite, totals, exit_code=101)
    assert result.status == "fail"
    assert result.unexpected_failures == ["other::boom"]


def test_evaluate_suite_fails_on_build_error_crash_and_timeout():
    no_results = _totals(passed=0, result_lines=0)
    assert _evaluate(_suite(), no_results, exit_code=101).status == "fail"
    assert _evaluate(_suite(), _totals(), exit_code=134).status == "fail"  # abort, no failed test
    timed_out = _evaluate(_suite(), _totals(), exit_code=-9, timed_out=True)
    assert timed_out.status == "fail"
    assert timed_out.timed_out


def test_evaluate_suite_enforces_floor_and_notes_reference_drift():
    assert _evaluate(_suite(min_passed=11), _totals(passed=10)).status == "fail"
    drift = _evaluate(_suite(reference_passed=100), _totals(passed=80))
    assert drift.status == "pass"
    assert any("deviates" in note for note in drift.notes)
    assert not _evaluate(_suite(reference_passed=100), _totals(passed=97)).notes


# ---- manifest and workflow consistency -----------------------------------------------------------


def test_manifest_toolchain_matches_the_pin_in_tui():
    pinned = tomllib.loads((REPO_ROOT / "tui" / "rust-toolchain.toml").read_text(encoding="utf-8"))
    assert _manifest()["toolchain"]["channel"] == pinned["toolchain"]["channel"]


def _workspace_package_names() -> set[str]:
    names = set()
    for cargo_toml in (REPO_ROOT / "tui" / "crates").rglob("Cargo.toml"):
        text = cargo_toml.read_text(encoding="utf-8")
        match = re.search(r'^name = "([^"]+)"', text, re.MULTILINE)
        if match:
            names.add(match.group(1))
    return names


def test_manifest_packages_exist_in_the_workspace():
    manifest = _manifest()
    known = _workspace_package_names()
    wanted = {s["package"] for s in manifest["test"]["suite"]}
    wanted |= set(manifest["check"]["packages"]) | set(manifest["check"]["workspace_tests_exclude"])
    wanted |= {manifest["graph"]["package"], manifest["release"]["package"]}
    assert wanted <= known, sorted(wanted - known)


def test_manifest_suites_are_well_formed():
    suites = _manifest()["test"]["suite"]
    packages = [s["package"] for s in suites]
    assert len(packages) == len(set(packages)), "one cargo invocation per package"
    for suite in suites:
        assert suite["targets"], suite
        assert all(isinstance(s, str) for s in suite.get("skip", []))
        assert all(isinstance(s, str) for s in suite.get("known_failures", []))


def test_select_suites_narrows_by_package_and_rejects_unknown_names():
    suites = [{"package": "pi-a"}, {"package": "pi-b"}, {"package": "pi-c"}]
    assert tb.select_suites(suites, []) == suites
    assert tb.select_suites(suites, ["pi-c", "pi-a"]) == [{"package": "pi-a"}, {"package": "pi-c"}]
    with pytest.raises(SystemExit, match="pi-zzz"):
        tb.select_suites(suites, ["pi-zzz"])


def test_tests_run_without_fail_fast_so_one_failing_target_cannot_hide_the_rest():
    assert '"--no-fail-fast"' in SCRIPT.read_text(encoding="utf-8")


def test_workflow_runs_the_documented_commands():
    text = WORKFLOW.read_text(encoding="utf-8")
    for command in ("check", "test", "release"):  # `gates` runs inside `check`
        assert f"scripts/tui_baseline.py {command}" in text, command
    assert "scripts/tui_baseline.toml" in text  # path filter, so manifest edits trigger the job


def _sample_report(detail: str = "a | b") -> dict:
    return {
        "tools": {"cargo": "cargo 1.94.0"},
        "checks": [{"name": "x", "status": "fail", "detail": detail}],
        "warnings": {"total": 3, "by_lint": {"dead_code": 3}, "by_crate": {}},
        "graph": {"x86_64-unknown-linux-gnu": 995},
        "tracked": {"pi-tools": ["pi-agent"], "gone": None},
        "enforced": False,
        "suites": [
            {
                "package": "pi-x",
                "status": "pass",
                "passed": 1,
                "failed": 0,
                "ignored": 0,
                "notes": ["n"],
                "unexpected_failures": [],
            }
        ],
        "totals": {"passed": 1, "failed": 0, "ignored": 0},
    }


def test_render_markdown_smoke():
    markdown = tb.render_markdown("test", _sample_report())
    assert "a \\| b" in markdown
    assert "report only" in markdown
    assert "(not in graph)" in markdown


def test_publish_survives_a_legacy_default_encoding(tmp_path):
    """Windows (and `LC_ALL=C` on POSIX) default to a legacy codec; the reports are not ASCII."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_sample_report("a \u2014 b \u2705")), encoding="utf-8")
    program = (
        "import importlib.util, json, sys\n"
        "from pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('tui_baseline', sys.argv[1])\n"
        "tb = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = tb\n"
        "spec.loader.exec_module(tb)\n"
        "tb.use_utf8_streams()\n"
        "report = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "tb.publish('test', report, Path(sys.argv[3]))\n"
    )
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"}
    for name in ("PYTHONIOENCODING", "GITHUB_STEP_SUMMARY"):
        env.pop(name, None)
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-c", program, str(SCRIPT), str(report_path), str(out_dir)],
        env=env,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "\u2014" in (out_dir / "test.md").read_text(encoding="utf-8")
    assert "\u2705" in proc.stdout.decode("utf-8")
