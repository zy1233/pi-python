"""Unit tests for scripts/tui_envspy/analyze.py (the reader of the env spy logs).

The spy itself (envspy.c) is Linux only and is checked by `envspy.sh selftest`. These tests feed
the analyzer a small log, so they run everywhere and need neither `nm` nor a test binary.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tui_envspy" / "analyze.py"


def _load():
    spec = importlib.util.spec_from_file_location("tui_envspy_analyze", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


ea = _load()

# A log of two tests that change FOO at the same time (test_b overlaps test_a), a reader that sees
# the change, and a variable that is still changed at exit. The frames are offsets into the binary
# "bin"; `_symbols` below names them. 0x310 is a frame of the standard library.
LOG = textwrap.dedent(
    """\
    100 START pid=1 exe=/x/bin
    200 SET key=FOO val=a tid=11 tname=t::a dirty_before=0 dirty_after=1 overlap=0 leak=0
      BT: bin+0x110 bin+0x310
    300 SET key=FOO val=b tid=12 tname=t::b dirty_before=1 dirty_after=1 overlap=1 leak=0
      BT: bin+0x210 bin+0x310
      PREV-OWNER tid=11 name=t::a
      PREV-BT: bin+0x110 bin+0x310
    400 GET-FOREIGN key=FOO seen=b owner=12 owner_name=t::b tid=11 tname=t::a site=s1 n=1
      BT: bin+0x120 bin+0x110
    500 SITE kind=GET-FOREIGN key=FOO site=s1 count=7
    600 SET key=BAR val=x tid=13 tname=t::c dirty_before=0 dirty_after=1 overlap=0 leak=0
      BT: bin+0x230
    700 FINAL key=BAR val=x owner=13 owner_name=t::c alive=0 op=SET
      BT: bin+0x230
    800 CHDIR path=/tmp/w tid=11 tname=t::a
      BT: bin+0x110
    """
)


def _symbols():
    """Symbols of a binary "bin" without `nm`: 0x100.. is test_a, 0x200.. is test_b and so on."""
    sym = ea.Symbols.__new__(ea.Symbols)
    sym.base = "bin"
    sym.addrs = [0x100, 0x200, 0x220, 0x300]
    sym.names = [
        "crate::tests::test_a",
        "crate::tests::test_b",
        "crate::tests::test_c",
        "std::sys::thing",
    ]
    return sym


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "bin.123.log"
    path.write_text(LOG, encoding="utf-8")
    return path


def test_parse_reads_events_fields_and_backtraces(log_file):
    events = ea.parse(str(log_file))
    kinds = [e.kind for e in events]
    assert kinds == ["START", "SET", "SET", "GET-FOREIGN", "SITE", "SET", "FINAL", "CHDIR"]
    second = events[2]
    assert second.fields["key"] == "FOO"
    assert second.fields["overlap"] == "1"
    assert second.fields["value"] == "b"
    assert second.bt.startswith("BT:")
    assert second.prev_bt.startswith("BT:")  # the "PREV-" is removed
    assert events[3].fields["owner"] == "12"


def test_symbols_turn_offsets_into_names_and_keep_unknown_frames():
    sym = _symbols()
    frames = sym.frames("BT: bin+0x110 bin+0x2ff other.so+0x10 bin+0x50")
    assert frames[0] == "crate::tests::test_a"
    assert frames[1] == "crate::tests::test_c"  # the last symbol that starts before 0x2ff
    assert frames[2] == "other.so+0x10"  # another object: kept as it is
    assert frames[3] == "bin+0x50"  # before the first symbol: kept as it is


def test_root_and_site_skip_the_standard_library():
    sym = _symbols()
    root, site = ea.root_and_site(sym.frames("BT: bin+0x120 bin+0x310 bin+0x110"))
    assert root == "crate::tests::test_a"
    assert site == "crate::tests::test_a"
    assert ea.root_and_site(sym.frames("BT: bin+0x310")) == (ea.NO_FRAME, ea.NO_FRAME)


def test_report_counts_overlaps_names_the_tests_and_lists_finals(log_file, capsys):
    ea.report_log(_symbols(), str(log_file), top=10, depth=3, only_key=None, detail=False)
    out = capsys.readouterr().out
    assert re.search(r"FOO\s+events=\s*2\s+overlaps=\s*1\s+leaks=\s*0\s+threads=\s*2", out)
    assert re.search(r"BAR\s+events=\s*1\s+overlaps=\s*0", out)
    overlaps = out.split("[overlaps]")[1].split("[leaks")[0]
    assert "SET of FOO" in overlaps
    assert "now : crate::tests::test_b" in overlaps
    assert "prev: crate::tests::test_a" in overlaps
    finals = out.split("[final]")[1].split("[reads]")[0]
    assert "BAR = x" in finals
    assert "owner ended" in finals
    assert "last change by: crate::tests::test_c" in finals


def test_report_weights_reads_with_the_site_count_not_the_samples(log_file, capsys):
    ea.report_log(_symbols(), str(log_file), top=10, depth=3, only_key=None, detail=False)
    reads = capsys.readouterr().out.split("[reads]")[1].split("[cached]")[0]
    # one sample in the log, but the SITE line says that the call site ran 7 times
    assert re.search(r"GET-FOREIGN\s+FOO\s+~7 reads, 1 reader tests", reads)
    assert "owner test : crate::tests::test_b" in reads
    assert "reader test: crate::tests::test_a" in reads


def test_report_lists_the_places_that_change_the_working_directory(log_file, capsys):
    ea.report_log(_symbols(), str(log_file), top=10, depth=3, only_key=None, detail=False)
    chdir = capsys.readouterr().out.split("[chdir]")[1]
    assert "1 calls, 1 places" in chdir
    assert "crate::tests::test_a" in chdir


def test_key_option_limits_the_report_to_one_variable(log_file, capsys):
    ea.report_log(_symbols(), str(log_file), top=10, depth=3, only_key="BAR", detail=False)
    out = capsys.readouterr().out
    assert "BAR" in out
    assert "FOO" not in out


def test_writers_out_has_one_line_per_variable_operation_and_test(log_file, tmp_path, capsys):
    writers = tmp_path / "writers.tsv"
    ea.report_log(
        _symbols(),
        str(log_file),
        top=10,
        depth=3,
        only_key=None,
        detail=False,
        writers_out=str(writers),
    )
    capsys.readouterr()
    rows = [line.split("\t") for line in writers.read_text(encoding="utf-8").splitlines()]
    assert sorted(rows) == [
        ["bin.123.log", "BAR", "SET", "crate::tests::test_c", "1"],
        ["bin.123.log", "FOO", "SET", "crate::tests::test_a", "1"],
        ["bin.123.log", "FOO", "SET", "crate::tests::test_b", "1"],
    ]


def test_readers_out_names_reader_and_owner(log_file, tmp_path, capsys):
    readers = tmp_path / "readers.tsv"
    ea.report_log(
        _symbols(),
        str(log_file),
        top=10,
        depth=3,
        only_key=None,
        detail=False,
        readers_out=str(readers),
    )
    capsys.readouterr()
    rows = [line.split("\t") for line in readers.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        [
            "bin.123.log",
            "FOO",
            "GET-FOREIGN",
            "crate::tests::test_a",
            "crate::tests::test_b",
            "1",
        ]
    ]


def test_a_log_without_changes_says_so(tmp_path, capsys):
    path = tmp_path / "bin.1.log"
    path.write_text("100 START pid=1 exe=/x/bin\n", encoding="utf-8")
    ea.report_log(_symbols(), str(path), top=10, depth=3, only_key=None, detail=False)
    assert "no variable was changed in this process" in capsys.readouterr().out
