"""Unit tests for scripts/tui_baseline.py (stage-0 baseline tooling for the TUI workspace)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tui_baseline.py"
MANIFEST = REPO_ROOT / "scripts" / "tui_baseline.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tui-ci.yml"
E2E_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tui-e2e.yml"


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


# Real output of cargo-nextest 0.9.148 (no terminal, stderr merged) for
# `cargo nextest run --no-fail-fast -p demo --lib --test it --bin demo -- --skip skipme`
# on a small crate of 14 tests: 4 are `#[ignore]`, 2 are removed by the filter, 8 run. In
# NEXTEST_FAIL three of the 8 fail.
NEXTEST_FAIL = """\
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.00s
────────────
 Nextest run ID ea55175a-70df-438a-a72d-777a4068ebed with nextest profile: default
    Starting 8 tests across 3 binaries (6 tests skipped)
        PASS [   0.003s] (1/8) demo::bin/demo tests::bin_ok
        PASS [   0.003s] (2/8) demo tests::nested::nested_ok
        PASS [   0.003s] (3/8) demo tests::ok_two
        FAIL [   0.003s] (4/8) demo tests::fails
  stdout ───

    running 1 test
    test tests::fails ... FAILED

    failures:

    failures:
        tests::fails

    test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 8 filtered out; finished in 0.00s

  stderr ───

    thread 'tests::fails' (7139) panicked at src/lib.rs:12:66:
    boom
    note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

        PASS [   0.004s] (5/8) demo::it it_ok
        PASS [   0.004s] (6/8) demo tests::ok_one
        FAIL [   0.004s] (7/8) demo tests::nested::nested_fails
  stdout ───

    running 1 test
    test tests::nested::nested_fails ... FAILED

    failures:

    failures:
        tests::nested::nested_fails

    test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 8 filtered out; finished in 0.00s

  stderr ───

    thread 'tests::nested::nested_fails' (7142) panicked at src/lib.rs:15:77:
    assertion `left == right` failed: nested boom
      left: 1
     right: 2
    note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

        FAIL [   0.004s] (8/8) demo::it it_fails
  stdout ───

    running 1 test
    test it_fails ... FAILED

    failures:

    failures:
        it_fails

    test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 2 filtered out; finished in 0.00s

  stderr ───

    thread 'it_fails' (7143) panicked at tests/it.rs:2:65:
    it boom
    note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

────────────
     Summary [   0.004s] 8 tests run: 5 passed, 3 failed, 6 skipped
        FAIL [   0.003s] (4/8) demo tests::fails
        FAIL [   0.004s] (7/8) demo tests::nested::nested_fails
        FAIL [   0.004s] (8/8) demo::it it_fails
error: test run failed
"""  # noqa: E501 (the lines are the real output)

# The same crate, all 8 tests pass.
NEXTEST_PASS = """\
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.00s
────────────
 Nextest run ID b94e9677-fe9c-4ac6-ac36-b61cd8b71784 with nextest profile: default
    Starting 8 tests across 3 binaries (6 tests skipped)
        PASS [   0.016s] (1/8) demo::bin/demo tests::bin_ok
        PASS [   0.016s] (2/8) demo tests::nested::nested_fails
        PASS [   0.016s] (3/8) demo tests::ok_two
        PASS [   0.016s] (4/8) demo tests::fails
        PASS [   0.016s] (5/8) demo tests::ok_one
        PASS [   0.016s] (6/8) demo::it it_ok
        PASS [   0.016s] (7/8) demo::it it_fails
        PASS [   0.016s] (8/8) demo tests::nested::nested_ok
────────────
     Summary [   0.016s] 8 tests run: 8 passed, 6 skipped
"""

# The same crate, a name filter that no test has (exit code 4).
NEXTEST_NONE = """\
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.00s
────────────
 Nextest run ID da5643a6-cff3-4f7f-8c75-4c2d3717e2a6 with nextest profile: default
    Starting 0 tests across 3 binaries (14 tests skipped)
────────────
     Summary [   0.000s] 0 tests run: 0 passed, 14 skipped
error: no tests to run
(hint: use `--no-tests` to customize)
"""

# The status labels that the recap of nextest can show (from the snapshot tests of nextest-runner,
# `displayer/imp.rs`), with whether the test failed.
NEXTEST_LABELS = {
    "PASS": False,
    "LEAK": False,
    "TIMEOUT-PASS": False,
    "SLOW": False,
    "SLOW + LEAK": False,
    "SLOW+TMPASS": False,
    "FLAKY 2/2": False,
    "FLKY-FL 2/2": True,
    "FAIL": True,
    "FAIL + LEAK": True,
    "XFAIL": True,
    "LEAK-FAIL": True,
    "TIMEOUT": True,
    "SIGSEGV": True,
    "ABORT": True,
    "TRY 2 FAIL": True,
    "TRY 2 FL+LK": True,
    "TRY 2 LKFAIL": True,
    "TRY 2 TMT": True,
}


def test_parse_nextest_output_reads_the_totals_and_the_recap_of_failures():
    totals = tb.parse_nextest_output(NEXTEST_FAIL)
    assert (totals.passed, totals.failed, totals.ignored, totals.result_lines) == (5, 3, 6, 1)
    # Once each, in the order of the recap, without the binary id.
    assert totals.failed_tests == ["tests::fails", "tests::nested::nested_fails", "it_fails"]


def test_parse_nextest_output_of_a_clean_run():
    totals = tb.parse_nextest_output(NEXTEST_PASS)
    assert (totals.passed, totals.failed, totals.ignored, totals.result_lines) == (8, 0, 6, 1)
    assert totals.failed_tests == []


def test_parse_nextest_output_of_a_run_with_no_test_still_has_a_summary():
    totals = tb.parse_nextest_output(NEXTEST_NONE)
    assert (totals.passed, totals.failed, totals.ignored, totals.result_lines) == (0, 0, 14, 1)


def test_parse_nextest_output_has_no_result_without_a_summary():
    """A build error stops nextest before it prints a summary."""
    totals = tb.parse_nextest_output(
        "error[E0432]: unresolved import `x`\nerror: could not compile"
    )
    assert (totals.passed, totals.failed, totals.result_lines) == (0, 0, 0)


def test_parse_nextest_output_ignores_ansi_colour_codes():
    coloured = (
        "\x1b[32;1m     Summary\x1b[0m [   0.004s] \x1b[1m8\x1b[0m tests run: "
        "\x1b[1m7\x1b[0m \x1b[32;1mpassed\x1b[0m, \x1b[1m1\x1b[0m \x1b[31;1mfailed\x1b[0m, "
        "\x1b[1m6\x1b[0m \x1b[33;1mskipped\x1b[0m\n"
        "\x1b[31;1m        FAIL\x1b[0m [   0.003s] (4/8) \x1b[35;1mdemo\x1b[0m "
        "\x1b[36mtests\x1b[0m\x1b[36m::\x1b[0m\x1b[34;1mfails\x1b[0m\n"
    )
    totals = tb.parse_nextest_output(coloured)
    assert (totals.passed, totals.failed, totals.ignored) == (7, 1, 6)
    assert totals.failed_tests == ["tests::fails"]


def test_parse_nextest_output_counts_exec_failed_and_timed_out_as_failed():
    summary = (
        "     Summary [  15.750s] 8/10 tests run: 5 passed (1 slow, 1 flaky, 1 leaky), "
        "2 failed, 1 exec failed, 1 timed out, 2 skipped\n"
        "warning: 2/10 tests were not run due to signal\n"
    )
    totals = tb.parse_nextest_output(summary)
    assert (totals.passed, totals.failed, totals.ignored) == (5, 4, 2)


def test_parse_nextest_output_tells_failed_from_passed_labels_in_the_recap():
    lines = ["     Summary [   1.000s] 19 tests run: 7 passed, 12 failed, 0 skipped"]
    for number, label in enumerate(NEXTEST_LABELS, start=1):
        lines.append(f"{label:>12} [   1.000s] ({number:>3}/100) my-binary-id test_{number}")
    totals = tb.parse_nextest_output("\n".join(lines))
    failing = [f"test_{n}" for n, failed in enumerate(NEXTEST_LABELS.values(), start=1) if failed]
    assert totals.failed_tests == failing


def test_parse_nextest_output_reads_only_the_recap_for_names():
    """The captured output of a failed test is before the summary; a line of it can look like a
    status line, and must not count as a failed test."""
    text = "\n".join(
        [
            "        FAIL [   0.010s] (1/2) pkg tests::boom",
            "  stdout ───",
            "",
            "    FAIL [   0.001s] (9/9) fake::bin some::other_name",
            "────────────",
            "     Summary [   0.011s] 2 tests run: 1 passed, 1 failed, 0 skipped",
            "        FAIL [   0.010s] (1/2) pkg tests::boom",
        ]
    )
    assert tb.parse_nextest_output(text).failed_tests == ["tests::boom"]


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


def test_count_residue_ignores_case_and_wants_no_letter_or_digit_before_the_phrase(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "src" / "a.rs").write_text(
        "// x.ai/session is a vendor method\n"  # counts for x.ai
        'let url = "https://api.x.ai/v1";\n'  # counts for x.ai (a dot before is fine)
        'const M: &str = "_x.ai/notify";\n'  # counts for x.ai (an underscore before is fine)
        "// the domain max.ai is not ours\n"  # no match: a letter right before
        "// a2x.ai neither\n"  # no match: a digit and a letter before
        "Leader mode, LEADER, leader\n"  # one line, counts once for leader
        "let is_leader_mode = false;\n"  # counts for leader
        "// misleader\n"  # no match: a letter right before
        "Run `grok agent stdio` and GROK AGENT\n"  # one line, counts once for grok agent
        "grok  agent\n",  # no match: two spaces
        encoding="utf-8",
    )
    (tmp_path / "src" / "b.md").write_text("leader\n", encoding="utf-8")
    (tmp_path / "src" / "c.bin").write_text("leader x.ai", encoding="utf-8")  # not a text suffix
    (tmp_path / "target" / "d.rs").write_text("leader x.ai", encoding="utf-8")  # skipped dir
    counts = tb.count_residue(tmp_path, ["x.ai", "leader", "grok agent", "mvp"])
    assert counts == {"x.ai": 3, "leader": 3, "grok agent": 1, "mvp": 0}
    assert tb.count_residue(tmp_path, []) == {}


def test_manifest_residue_ceilings_are_whole_numbers():
    residue = _manifest()["gates"]["residue"]
    assert residue["grok agent"] == 0, "this one reached zero; it must stay there"
    for phrase, ceiling in residue.items():
        assert isinstance(ceiling, int) and not isinstance(ceiling, bool), phrase
        assert ceiling >= 0, phrase


def test_the_residue_ceilings_hold_in_this_tree():
    """The same ratchet as `tui_baseline.py gates`, so the Python CI job also sees a regression."""
    residue = _manifest()["gates"]["residue"]
    counts = tb.count_residue(REPO_ROOT / "tui" / "crates", list(residue))
    over = {p: (counts[p], limit) for p, limit in residue.items() if counts[p] > limit}
    assert not over, f"lines now vs ceiling in scripts/tui_baseline.toml: {over}"


# ---- session disk-read gate (ADR1) ------------------------------------------------------------


def _crate(tmp_path: Path, files: dict[str, str]) -> Path:
    """A fake `tui/` with one crate `codegen/pi-pager`; returns the `tui` directory."""
    src = tmp_path / "tui" / "crates" / "codegen" / "pi-pager" / "src"
    for name, text in files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path / "tui"


def _scan(tui: Path, patterns=("list_summaries", 'join("sessions")')) -> list[str]:
    return tb.scan_session_disk_reads(tui, ["codegen/pi-pager"], list(patterns))


def test_session_disk_read_scan_reports_production_code_with_path_and_line(tmp_path):
    reads = (
        'fn a() {\n    let rows = list_summaries(cwd).await;\n    let d = h.join("sessions");\n}\n'
    )
    tui = _crate(tmp_path, {"app/a.rs": reads, "app/clean.rs": "fn b() {}\n"})
    hits = _scan(tui)
    assert hits == [
        "crates/codegen/pi-pager/src/app/a.rs:2: let rows = list_summaries(cwd).await;",
        'crates/codegen/pi-pager/src/app/a.rs:3: let d = h.join("sessions");',
    ]
    assert tb.scan_session_disk_reads(tui, ["codegen/pi-pager"], []) == []


@pytest.mark.parametrize(
    "name",
    [
        "app/dispatch/tests/mod.rs",
        "app/effects/tests.rs",
        "app/session_resolve_tests.rs",
        "test_util.rs",
        "app/agent_view/test_fixtures.rs",
    ],
)
def test_session_disk_read_scan_skips_test_files(tmp_path, name):
    tui = _crate(tmp_path, {name: "fn t() { list_summaries(); }\n"})
    assert _scan(tui) == []


def test_session_disk_read_scan_skips_comments_and_inline_test_modules(tmp_path):
    text = (
        "// list_summaries used to be called here\n"
        '/// Reads `join("sessions")`, formerly.\n'
        "fn live() {}\n"
        "#[cfg(test)]\n"
        "#[allow(dead_code)]\n"
        "pub(crate) mod tests {\n"
        "    fn t() {\n"
        "        list_summaries();\n"
        "    }\n"
        "}\n"
        "fn after_the_tests() {\n"
        "    list_summaries();\n"
        "}\n"
    )
    tui = _crate(tmp_path, {"a.rs": text})
    assert _scan(tui) == ["crates/codegen/pi-pager/src/a.rs:12: list_summaries();"]


def test_a_cfg_test_on_a_single_item_does_not_hide_the_code_after_it(tmp_path):
    """`plan.rs` has `#[cfg(test)] use ...;` at the top; the read at line 35 must still show."""
    text = (
        "#[cfg(test)]\n"
        "use super::test_fixtures;\n"
        '#[cfg(test)]\n#[path = "a_tests.rs"]\nmod tests;\n'
        "fn read() {\n"
        "    list_summaries();\n"
        "}\n"
    )
    tui = _crate(tmp_path, {"a.rs": text})
    assert _scan(tui) == ["crates/codegen/pi-pager/src/a.rs:7: list_summaries();"]


def test_manifest_session_disk_read_gate_is_well_formed():
    gate = _manifest()["gates"]["session_disk_reads"]
    assert gate["crates"] and gate["patterns"]
    for crate in gate["crates"]:
        assert (REPO_ROOT / "tui" / "crates" / crate / "src").is_dir(), crate
    assert all(isinstance(p, str) and p for p in gate["patterns"])
    # The pure title helpers (`session::title`) stay allowed: the pager uses them on purpose.
    assert "session::title" not in gate["patterns"]


def test_no_pager_code_reads_session_files_in_this_tree():
    """The same gate as `tui_baseline.py gates`, so the Python CI job also sees a regression."""
    gate = _manifest()["gates"]["session_disk_reads"]
    hits = tb.scan_session_disk_reads(REPO_ROOT / "tui", gate["crates"], gate["patterns"])
    assert not hits, "the pager reads session files again (ADR1): " + "; ".join(hits[:5])


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


def test_a_nextest_run_is_judged_like_a_libtest_run():
    suite = _suite(known_failures=["tests::fails"], min_passed=5)
    result = _evaluate(suite, tb.parse_nextest_output(NEXTEST_FAIL), exit_code=100)
    assert result.status == "fail"
    assert result.known_failures_seen == ["tests::fails"]
    assert result.unexpected_failures == ["tests::nested::nested_fails", "it_fails"]
    assert (result.passed, result.failed, result.ignored) == (5, 3, 6)
    clean = _evaluate(_suite(min_passed=8), tb.parse_nextest_output(NEXTEST_PASS))
    assert (clean.status, clean.passed, clean.ignored) == ("pass", 8, 6)
    assert _evaluate(_suite(min_passed=9), tb.parse_nextest_output(NEXTEST_PASS)).status == "fail"


def test_a_nextest_run_that_ran_no_test_fails():
    """Exit code 4 and a floor: a filter that no test matches must not look like a pass."""
    totals = tb.parse_nextest_output(NEXTEST_NONE)
    assert _evaluate(_suite(), totals, exit_code=4).status == "fail"
    assert _evaluate(_suite(min_passed=1), totals, exit_code=0).status == "fail"


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


def test_suite_command_for_each_runner():
    suite = {"package": "pi-acp-lib", "targets": ["--lib"], "skip": ["a_test", "b_test"]}
    assert tb.suite_command("nextest", suite) == [
        *("cargo", "nextest", "run", "--locked", "--no-fail-fast", "-p", "pi-acp-lib", "--lib"),
        *("--", "--skip", "a_test", "--skip", "b_test"),
    ]
    assert tb.suite_command(
        "cargo", {"package": "pi-http", "targets": ["--lib", "--test", "t"]}
    ) == [
        *("cargo", "test", "--locked", "--no-fail-fast", "-p", "pi-http", "--lib", "--test", "t"),
    ]


def test_the_cargo_flags_come_before_the_arguments_of_the_test_binary_with_each_runner():
    for runner in tb.TEST_RUNNERS:
        for suite in _manifest()["test"]["suite"]:
            command = tb.suite_command(runner, suite)
            cargo_part = command[: command.index("--")] if "--" in command else command
            assert {"--locked", "--no-fail-fast"} <= set(cargo_part), (runner, suite["package"])


def test_pick_runner_takes_the_flag_before_the_manifest_and_rejects_unknown_names():
    assert tb.pick_runner({"runner": "nextest"}) == "nextest"
    assert tb.pick_runner({"runner": "nextest"}, "cargo") == "cargo"
    assert tb.pick_runner({}) == "cargo"
    with pytest.raises(SystemExit, match="unknown test runner 'libtest'"):
        tb.pick_runner({"runner": "libtest"})


def test_manifest_names_a_known_test_runner():
    assert _manifest()["test"]["runner"] in tb.TEST_RUNNERS


def test_require_nextest_stops_with_a_hint_when_it_is_not_installed(monkeypatch, tmp_path):
    def fake_cargo(args, cwd):
        assert list(args) == ["nextest", "--version"]
        return subprocess.CompletedProcess(["cargo", *args], returncode)

    monkeypatch.setattr(tb, "cargo", fake_cargo)
    returncode = 0
    tb.require_nextest(tmp_path)  # installed: no error
    returncode = 101
    with pytest.raises(SystemExit, match=r"cargo-nextest is not installed.*--runner cargo"):
        tb.require_nextest(tmp_path)


def test_tool_versions_keeps_only_the_first_line_of_the_nextest_version(monkeypatch, tmp_path):
    outputs = {
        "rustc": "rustc 1.94.0\nhost: x86_64",
        "cargo": "cargo 1.94.0",
        "nextest": "cargo-nextest 0.9.148 (abc 2026-10-07)\nrelease: 0.9.148\nhost: x86_64",
    }

    def fake_run(cmd, **_kwargs):
        name = "nextest" if "nextest" in cmd else cmd[0]
        return subprocess.CompletedProcess(cmd, 0, stdout=outputs[name], stderr="")

    monkeypatch.setattr(tb.subprocess, "run", fake_run)
    assert tb.tool_versions(tmp_path, "nextest") == {
        "rustc": "rustc 1.94.0; host: x86_64",
        "cargo": "cargo 1.94.0",
        "nextest": "cargo-nextest 0.9.148 (abc 2026-10-07)",
    }
    assert set(tb.tool_versions(tmp_path)) == {"rustc", "cargo"}


def test_the_test_job_installs_the_pinned_nextest_when_the_manifest_asks_for_it():
    if _manifest()["test"]["runner"] != "nextest":
        pytest.skip("the manifest does not use nextest")
    text = WORKFLOW.read_text(encoding="utf-8")
    job = text.split("\n  test:\n", 1)[1].split("\n  release-baseline:\n", 1)[0]
    assert re.search(
        r"uses: taiki-e/install-action@v2\n\s+with:\n\s+tool: nextest@\d+\.\d+\.\d+", job
    )
    assert job.index("taiki-e/install-action") < job.index("scripts/tui_baseline.py test")


def test_the_test_job_makes_the_sandbox_tests_fail_when_they_cannot_enforce():
    # Without SANDBOX_E2E_REQUIRE_ENFORCEMENT the `pi-sandbox` end-to-end tests return early on a
    # runner with no Landlock or no working `bwrap`, and they count as passed.
    text = WORKFLOW.read_text(encoding="utf-8")
    job = text.split("\n  test:\n", 1)[1].split("\n  release-baseline:\n", 1)[0]
    assert "pi-sandbox" in {s["package"] for s in _manifest()["test"]["suite"]}
    run = job.index("scripts/tui_baseline.py test")
    step = job.rindex("      - name:", 0, run)
    assert re.search(r'env:\n\s+SANDBOX_E2E_REQUIRE_ENFORCEMENT: "1"\n', job[step:run])
    install = job.index("apt-get install -y -qq bubblewrap")
    assert install < run
    assert "kernel.apparmor_restrict_unprivileged_userns=0" in job[install:run]


def test_workflow_runs_the_documented_commands():
    text = WORKFLOW.read_text(encoding="utf-8")
    for command in ("check", "test", "release"):  # `gates` runs inside `check`
        assert f"scripts/tui_baseline.py {command}" in text, command
    assert "scripts/tui_baseline.toml" in text  # path filter, so manifest edits trigger the job


# ---- the end-to-end workflow (tui-e2e.yml) -------------------------------------------------------
#
# A change that touches only the Python agent starts tui-e2e.yml (one test target) and not
# tui-ci.yml (eleven suites, 18 to 35 minutes). These tests keep the two files from drifting apart.

# What the end-to-end tests read on the Python side.
AGENT_PATHS = (
    "packages/pi-agent-cli/pi_agent_cli/**",
    "packages/pi-agent-cli/tests/_tool_agent.py",
)


def _trigger_paths(text: str, event: str) -> list[str]:
    """The entries of `on.<event>.paths` of a workflow file, as written (comments left out)."""
    lines = text.splitlines()
    start = lines.index(f"  {event}:")
    paths: list[str] = []
    in_paths = False
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("   "):  # the next event, or the next block
            break
        item = line.strip()
        if item == "paths:":
            in_paths = True
        elif in_paths and item.startswith("- "):
            paths.append(item[2:].strip().strip('"'))
        elif in_paths and item and not item.startswith("#"):
            break  # another key of the event: the list is over
    return paths


def test_the_trigger_paths_of_the_full_workflow_are_read():
    # Without this, the next test could pass because the helper found nothing.
    text = WORKFLOW.read_text(encoding="utf-8")
    for event in ("push", "pull_request"):
        paths = _trigger_paths(text, event)
        assert "tui/**" in paths, event
        assert ".github/workflows/tui-ci.yml" in paths, event


def test_a_change_of_the_python_agent_alone_starts_the_end_to_end_workflow_and_not_the_full_one():
    full = WORKFLOW.read_text(encoding="utf-8")
    end_to_end = E2E_WORKFLOW.read_text(encoding="utf-8")
    for event in ("push", "pull_request"):
        of_full = set(_trigger_paths(full, event))
        of_end_to_end = set(_trigger_paths(end_to_end, event))
        assert set(AGENT_PATHS) <= of_end_to_end, event
        assert not set(AGENT_PATHS) & of_full, (event, "tui-ci.yml would run for the agent alone")
        # A change of the workflow file is its own test.
        assert ".github/workflows/tui-e2e.yml" in of_end_to_end, event


def test_the_end_to_end_workflow_runs_a_target_of_the_manifest_with_the_flags_of_the_script():
    pager = next(s for s in _manifest()["test"]["suite"] if s["package"] == "pi-pager")
    targets = pager["targets"]
    assert targets[targets.index("python_agent_e2e") - 1] == "--test"
    command = tb.suite_command(
        "nextest", {"package": "pi-pager", "targets": ["--test", "python_agent_e2e"]}
    )
    assert " ".join(command) in E2E_WORKFLOW.read_text(encoding="utf-8")


def test_the_end_to_end_workflow_has_the_environment_of_the_manifest():
    text = E2E_WORKFLOW.read_text(encoding="utf-8")
    test_config = _manifest()["test"]
    for key, value in test_config["env"].items():
        assert re.search(rf"^\s+{re.escape(key)}: {re.escape(value)}$", text, re.MULTILINE), key
    for key in test_config["unset_env"]:  # the manifest unsets them; the job must not set them
        assert key not in text, key


def test_the_end_to_end_workflow_installs_the_agent_and_nextest_as_the_test_job_does():
    full = WORKFLOW.read_text(encoding="utf-8")
    end_to_end = E2E_WORKFLOW.read_text(encoding="utf-8")
    for pattern in (r"^\s*pip install .+$", r"tool: (nextest@\S+)"):
        found = re.findall(pattern, end_to_end, re.MULTILINE)
        assert found, pattern
        assert found == re.findall(pattern, full, re.MULTILINE), pattern
    # A Python without the agent is a failure of the job, not a skipped test.
    assert 'echo "PI_E2E_PYTHON=$(command -v python)" >> "$GITHUB_ENV"' in end_to_end
    assert 'echo "PI_E2E_REQUIRED=1" >> "$GITHUB_ENV"' in end_to_end


def test_the_end_to_end_workflow_restores_the_cache_of_the_test_job_and_never_saves_it():
    full = WORKFLOW.read_text(encoding="utf-8")
    test_job = full.split("\n  test:\n", 1)[1].split("\n  release-baseline:\n", 1)[0]
    key = re.search(r"shared-key: (\S+)", test_job).group(1)
    end_to_end = E2E_WORKFLOW.read_text(encoding="utf-8")
    # Same key, so the job finds the cache of the full job. It saves nothing: a cache with the
    # same key but fewer artifacts would stop the full job from saving the whole one.
    assert f"shared-key: {key}\n" in end_to_end
    assert 'save-if: "false"' in end_to_end
    assert "save-if: ${{" not in end_to_end


# ---- one toolchain on the runner (the key of rust-cache) -----------------------------------------
#
# rust-cache puts the version of every Rust toolchain on the runner into its cache key. The image
# has its own `stable`, and a new image has a new version of it: the key changed, no cache matched,
# and the next run built everything again (about 30 minutes). The jobs keep only the pinned
# toolchain. These tests keep that step the same in every job and before the cache step, and keep
# the variables that the key hashes the same in both workflows (the end-to-end job restores the key
# of the `test` job, so the two hashes must be equal).

INSTALL_STEP = "Install Rust toolchain (pinned by tui/rust-toolchain.toml)"
PIN_STEP = "Keep only the pinned Rust toolchain"
CACHE_ENV_PREFIXES = ("CARGO", "CC", "CFLAGS", "CXX", "CMAKE", "RUST")  # what rust-cache hashes


def _jobs(text: str) -> dict[str, str]:
    """Every job of a workflow file by name: the text under its `  <name>:` line."""
    body = text.split("\njobs:\n", 1)[1]
    jobs: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in body.splitlines():
        header = re.fullmatch(r"  ([\w-]+):", line)
        if header:
            current = jobs.setdefault(header.group(1), [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


def _all_jobs() -> dict[str, str]:
    """The jobs of both workflow files, named `<file>:<job>`."""
    found: dict[str, str] = {}
    for path in (WORKFLOW, E2E_WORKFLOW):
        for name, text in _jobs(path.read_text(encoding="utf-8")).items():
            found[f"{path.name}:{name}"] = text
    return found


def _step_text(job: str, name: str) -> str:
    """One step of a job, from its `- name:` line to the next step (or its comment)."""
    lines = job.splitlines()
    start = lines.index(f"      - name: {name}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^      (- |#)", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end]).rstrip()


def _workflow_env(text: str) -> dict[str, str]:
    """The variables of the top-level `env:` block of a workflow file."""
    lines = text.split("\njobs:\n", 1)[0].splitlines()
    env: dict[str, str] = {}
    for line in lines[lines.index("env:") + 1 :]:
        if line.strip() and not line.startswith("  "):
            break
        item = re.fullmatch(r"  ([A-Z_]+): (.+)", line)
        if item:
            env[item.group(1)] = item.group(2).strip('"')
    return env


def test_the_jobs_of_the_two_workflows_are_read():
    # Without this, the next tests could pass because a helper found nothing.
    jobs = _all_jobs()
    names = {
        "tui-ci.yml:check",
        "tui-ci.yml:test",
        "tui-ci.yml:release-baseline",
        "tui-e2e.yml:e2e",
    }
    assert names <= set(jobs)
    assert "Swatinem/rust-cache@v2" in jobs["tui-ci.yml:test"]
    assert "Swatinem/rust-cache@v2" in jobs["tui-e2e.yml:e2e"]


def test_every_job_that_installs_the_pinned_toolchain_then_keeps_only_that_one():
    jobs = {key: job for key, job in _all_jobs().items() if f"- name: {INSTALL_STEP}" in job}
    assert {"tui-ci.yml:check", "tui-ci.yml:test", "tui-e2e.yml:e2e"} <= set(jobs)
    for key, job in jobs.items():
        assert f"- name: {PIN_STEP}" in job, key
        assert job.index(f"- name: {INSTALL_STEP}") < job.index(f"- name: {PIN_STEP}"), key
        if "Swatinem/rust-cache@v2" in job:
            # rust-cache makes its key when its step starts, so the toolchains must be gone by then.
            assert job.index(f"- name: {PIN_STEP}") < job.index("Swatinem/rust-cache@v2"), key


def test_the_step_that_keeps_only_the_pinned_toolchain_is_the_same_in_every_job():
    steps = {
        key: _step_text(job, PIN_STEP)
        for key, job in _all_jobs().items()
        if f"- name: {PIN_STEP}" in job
    }
    assert len(steps) >= 4
    assert len(set(steps.values())) == 1, steps
    step = next(iter(steps.values()))
    assert "working-directory: tui" in step  # rust-toolchain.toml is read there
    assert "rustc --print sysroot" in step  # the toolchain that the file selects
    assert 'rustup default "$pinned"' in step  # so a command outside tui/ uses it too
    assert "rustup toolchain list --quiet" in step
    assert 'rustup toolchain uninstall "$t"' in step


def test_the_two_workflows_set_the_same_variables_that_the_cache_key_hashes():
    def hashed(path: Path) -> dict[str, str]:
        env = _workflow_env(path.read_text(encoding="utf-8"))
        return {key: value for key, value in env.items() if key.startswith(CACHE_ENV_PREFIXES)}

    assert hashed(WORKFLOW)  # the helper found the block
    assert hashed(WORKFLOW) == hashed(E2E_WORKFLOW)


def _sample_report(detail: str = "a | b") -> dict:
    return {
        "tools": {"cargo": "cargo 1.94.0"},
        "checks": [{"name": "x", "status": "fail", "detail": detail}],
        "warnings": {"total": 3, "by_lint": {"dead_code": 3}, "by_crate": {}},
        "graph": {"x86_64-unknown-linux-gnu": 995},
        "residue": {"leader": 7, "x.ai": 0},
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
    assert "Leftover words under `tui/crates` (lines): `leader` 7, `x.ai` 0" in markdown


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


def test_render_markdown_says_skipped_for_nextest_and_ignored_for_libtest():
    libtest = tb.render_markdown("test", _sample_report())
    assert "runner: cargo" in libtest
    assert "| Suite | Result | Passed | Failed | Ignored | Notes |" in libtest
    assert "Total: **1** passed, 0 failed, 0 ignored" in libtest

    report = _sample_report()
    report["runner"] = "nextest"
    report["tools"]["nextest"] = "cargo-nextest 0.9.148"
    nextest = tb.render_markdown("test", report)
    assert "runner: nextest" in nextest
    assert "`cargo 1.94.0`, `cargo-nextest 0.9.148`" in nextest
    assert "| Suite | Result | Passed | Failed | Skipped | Notes |" in nextest
    assert "Total: **1** passed, 0 failed, 0 skipped" in nextest
    assert "together as skipped" in nextest


# ---- the timeout of a command ----------------------------------------------------------------

posix_only = pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups need POSIX")


@posix_only
def test_stream_asks_the_group_to_stop_before_it_kills(tmp_path):
    """nextest puts each test in a process group of its own, so only a nextest that gets a SIGTERM
    stops its tests (measured: after a SIGKILL to the group of nextest the test lives on)."""
    cmd = ["sh", "-c", "trap 'echo got-sigterm; exit 0' TERM; while true; do sleep 0.05; done"]
    code, out, timed_out = tb.stream(cmd, tmp_path, timeout_s=0.5, merge_stderr=True)
    assert timed_out
    assert "got-sigterm" in out
    assert code == 0


@posix_only
def test_stream_kills_a_group_that_ignores_sigterm_after_the_grace_time(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "STOP_GRACE_SECONDS", 0.5)
    cmd = ["sh", "-c", "trap '' TERM; while true; do sleep 0.05; done"]
    started = time.monotonic()
    code, _, timed_out = tb.stream(cmd, tmp_path, timeout_s=0.5, merge_stderr=True)
    assert timed_out
    assert code == -signal.SIGKILL
    assert time.monotonic() - started < 15


@posix_only
def test_stream_returns_the_output_of_a_command_that_ends_before_the_timeout(tmp_path):
    cmd = ["sh", "-c", "echo done; exit 3"]
    code, out, timed_out = tb.stream(cmd, tmp_path, timeout_s=30, merge_stderr=True)
    assert (code, out.strip(), timed_out) == (3, "done", False)
