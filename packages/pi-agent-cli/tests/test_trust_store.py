"""The saved project-trust decisions (audit P7-02 follow-up: trust by content, not by path).

``<home>/agent/trust.json`` maps a project directory to the fingerprint of the files the user
approved. The file lives in the user's home, where a repository cannot write, and every way it
can be wrong (missing, corrupt, hand-edited into the wrong shape, unwritable) has to end in
"no trust", never in an exception that stops a session or in trust nobody gave.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from pi_agent_cli.trust_store import STORE_VERSION, TrustRecord, TrustStore

FINGERPRINT = "sha256:" + "ab" * 32


def _project(tmp_path: Path, name: str = "project") -> Path:
    project = tmp_path / name
    project.mkdir(exist_ok=True)
    return project


def _write(store: TrustStore, data: object) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Where it lives, and the round trip
# ---------------------------------------------------------------------------


def test_the_store_lives_in_the_agent_directory_of_the_home(tmp_path: Path):
    assert TrustStore(tmp_path).path == tmp_path / "agent" / "trust.json"


def test_the_home_defaults_to_the_pi_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PI_HOME", str(tmp_path / "elsewhere"))

    assert TrustStore().path == tmp_path / "elsewhere" / "agent" / "trust.json"


def test_a_missing_store_trusts_nothing(tmp_path: Path):
    store = TrustStore(tmp_path)

    assert store.get(_project(tmp_path)) is None
    assert store.records() == []
    assert not store.path.exists()  # reading never creates the file


def test_a_remembered_project_is_found_again(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path)

    store.remember(project, FINGERPRINT, resources=("extensions: a.py", "prompt files: SYSTEM.md"))

    record = store.get(project)
    assert record is not None
    assert record.fingerprint == FINGERPRINT
    assert record.resources == ("extensions: a.py", "prompt files: SYSTEM.md")
    assert Path(record.project) == project.resolve()


def test_a_second_store_object_reads_what_the_first_wrote(tmp_path: Path):
    project = _project(tmp_path)
    TrustStore(tmp_path).remember(project, FINGERPRINT)

    record = TrustStore(tmp_path).get(project)

    assert record is not None and record.fingerprint == FINGERPRINT


def test_the_time_of_the_decision_is_recorded_in_utc(tmp_path: Path):
    store = TrustStore(tmp_path)

    record = store.remember(_project(tmp_path), FINGERPRINT)

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record.trusted_at)


def test_remembering_again_replaces_the_earlier_fingerprint(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    store.remember(project, FINGERPRINT)

    store.remember(project, "sha256:" + "cd" * 32)

    record = store.get(project)
    assert record is not None and record.fingerprint == "sha256:" + "cd" * 32
    assert len(store.records()) == 1


def test_records_are_kept_per_project(tmp_path: Path):
    store = TrustStore(tmp_path)
    first, second = _project(tmp_path, "one"), _project(tmp_path, "two")

    store.remember(first, FINGERPRINT)

    assert store.get(first) is not None
    assert store.get(second) is None  # trusting one project trusts no other


def test_a_project_below_a_remembered_one_is_not_covered(tmp_path: Path):
    """A record vouches for the files fingerprinted in that directory. A subdirectory has
    files of its own that nobody looked at."""
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    child = project / "sub"
    child.mkdir()
    store.remember(project, FINGERPRINT)

    assert store.get(child) is None


def test_a_sibling_sharing_a_name_prefix_is_not_the_same_project(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.remember(_project(tmp_path, "proj"), FINGERPRINT)

    assert store.get(_project(tmp_path, "proj-evil")) is None


def test_forgetting_removes_the_record(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    store.remember(project, FINGERPRINT)

    assert store.forget(project) is True

    assert store.get(project) is None
    assert store.forget(project) is False  # nothing left to forget


def test_the_records_can_be_listed(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.remember(_project(tmp_path, "one"), FINGERPRINT)
    store.remember(_project(tmp_path, "two"), FINGERPRINT)

    assert sorted(Path(r.project).name for r in store.records()) == ["one", "two"]


def test_the_file_is_plain_json_a_person_can_read_and_edit(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    store.remember(project, FINGERPRINT, resources=("skills: .pi/skills",))

    data = json.loads(store.path.read_text(encoding="utf-8"))

    assert data["version"] == STORE_VERSION
    entry = data["projects"][str(project.resolve())]
    assert entry["fingerprint"] == FINGERPRINT
    assert entry["resources"] == ["skills: .pi/skills"]
    assert "trusted_at" in entry


def test_non_ascii_paths_survive_the_round_trip(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path, "项目-é")
    store.remember(project, FINGERPRINT)

    assert store.get(project) is not None
    assert "项目-é" in store.path.read_text(encoding="utf-8")  # readable, not \u-escaped


@pytest.mark.skipif(os.name == "nt", reason="Windows paths are always valid text")
def test_a_path_that_is_not_valid_text_can_still_be_saved(tmp_path: Path):
    """POSIX file names are bytes; one that is not UTF-8 shows up as a lone surrogate, which
    a plain UTF-8 write refuses."""
    store = TrustStore(tmp_path)
    project = tmp_path / os.fsdecode(b"caf\xe9")
    project.mkdir()

    store.remember(project, FINGERPRINT)

    assert store.get(project) is not None
    assert TrustStore(tmp_path).get(project) is not None


# ---------------------------------------------------------------------------
# What counts as "the same directory"
# ---------------------------------------------------------------------------


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this environment cannot create symlinks")


def test_a_symlink_to_a_remembered_project_finds_its_record(tmp_path: Path):
    """The files that run are where the link points; that is the path a record belongs to."""
    store = TrustStore(tmp_path)
    real = _project(tmp_path, "real")
    alias = tmp_path / "alias"
    _symlink(alias, real)
    store.remember(real, FINGERPRINT)

    assert store.get(alias) is not None


def test_remembering_through_a_symlink_records_the_real_directory(tmp_path: Path):
    store = TrustStore(tmp_path)
    real = _project(tmp_path, "real")
    alias = tmp_path / "alias"
    _symlink(alias, real)

    store.remember(alias, FINGERPRINT)

    assert store.get(real) is not None
    assert [Path(r.project) for r in store.records()] == [real.resolve()]


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_paths_differing_only_in_case_are_one_project_on_windows(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path, "Proj")
    store.remember(project, FINGERPRINT)

    assert store.get(Path(str(project).upper())) is not None
    store.remember(Path(str(project).lower()), "sha256:" + "cd" * 32)
    assert len(store.records()) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_a_hand_typed_key_in_another_case_is_replaced_not_duplicated(tmp_path: Path):
    """The file is meant to be edited by people, who do not all spell a path the same way."""
    store = TrustStore(tmp_path)
    project = _project(tmp_path, "Proj")
    typed = str(project.resolve()).upper()
    _write(
        store,
        {"version": STORE_VERSION, "projects": {typed: {"fingerprint": FINGERPRINT}}},
    )
    assert store.get(project) is not None

    store.remember(project, "sha256:" + "cd" * 32)
    [only] = store.records()
    assert only.fingerprint == "sha256:" + "cd" * 32

    _write(
        store,
        {"version": STORE_VERSION, "projects": {typed: {"fingerprint": FINGERPRINT}}},
    )
    assert store.forget(project) is True
    assert store.records() == []


# ---------------------------------------------------------------------------
# Every way the file can be wrong grants nothing
# ---------------------------------------------------------------------------


def test_a_file_that_is_not_json_grants_nothing(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{ this is not json", encoding="utf-8")

    assert store.get(project) is None
    assert store.records() == []


def test_a_binary_file_grants_nothing(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"\xff\xfe\x00\x01")

    assert store.get(_project(tmp_path)) is None


def test_an_empty_file_grants_nothing(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("", encoding="utf-8")

    assert store.get(_project(tmp_path)) is None


def test_a_directory_where_the_file_should_be_grants_nothing(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.path.mkdir(parents=True)

    assert store.get(_project(tmp_path)) is None


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda entries: [], id="a list"),
        pytest.param(lambda entries: "trusted", id="a string"),
        pytest.param(lambda entries: {"projects": entries}, id="no version"),
        pytest.param(
            lambda entries: {"version": STORE_VERSION + 1, "projects": entries},
            id="a newer version, which cannot be understood",
        ),
        pytest.param(lambda entries: {"version": 0, "projects": entries}, id="an older version"),
        pytest.param(
            lambda entries: {"version": True, "projects": entries}, id="true, which is not 1"
        ),
        pytest.param(
            lambda entries: {"version": str(STORE_VERSION), "projects": entries},
            id="the version as text",
        ),
        pytest.param(
            lambda entries: {"version": STORE_VERSION, "projects": [entries]},
            id="projects as a list",
        ),
        pytest.param(
            lambda entries: {"version": STORE_VERSION, "projects": "all"}, id="projects as text"
        ),
    ],
)
def test_a_file_of_the_wrong_shape_grants_nothing(tmp_path: Path, build: Any):
    """Each shape holds a perfectly good record for the project; none may be honoured."""
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    good = {str(project.resolve()): {"fingerprint": FINGERPRINT, "trusted_at": "x"}}
    _write(store, build(good))

    assert store.get(project) is None
    assert store.records() == []


def test_a_record_of_the_wrong_shape_is_ignored_and_the_others_still_count(tmp_path: Path):
    store = TrustStore(tmp_path)
    good = _project(tmp_path, "good")
    bad_string = _project(tmp_path, "bad-string")
    bad_number = _project(tmp_path, "bad-number")
    bad_empty = _project(tmp_path, "bad-empty")
    _write(
        store,
        {
            "version": STORE_VERSION,
            "projects": {
                str(good.resolve()): {"fingerprint": FINGERPRINT, "trusted_at": "x"},
                str(bad_string.resolve()): "trusted",
                str(bad_number.resolve()): {"fingerprint": 7},
                str(bad_empty.resolve()): {"fingerprint": ""},
            },
        },
    )

    assert store.get(good) is not None
    assert store.get(bad_string) is None
    assert store.get(bad_number) is None
    assert store.get(bad_empty) is None


def test_a_record_with_odd_optional_fields_still_reads(tmp_path: Path):
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    _write(
        store,
        {
            "version": STORE_VERSION,
            "projects": {
                str(project.resolve()): {"fingerprint": FINGERPRINT, "resources": "not a list"}
            },
        },
    )

    record = store.get(project)

    assert record is not None
    assert record.resources == ()
    assert record.trusted_at == ""


def test_a_corrupt_file_is_kept_aside_when_a_new_decision_is_saved(tmp_path: Path):
    """The user may have hand-edited it into a syntax error; do not destroy their other
    entries just because saving one more decision needs a valid file."""
    store = TrustStore(tmp_path)
    project = _project(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text('{"version": 1, "projects": {oops', encoding="utf-8")

    store.remember(project, FINGERPRINT)

    assert store.get(project) is not None
    kept = store.path.with_name("trust.json.corrupt")
    assert kept.read_text(encoding="utf-8") == '{"version": 1, "projects": {oops'


def test_a_file_that_cannot_be_read_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Saving over something we could not look at could destroy the user's decisions."""
    store = TrustStore(tmp_path)
    first, second = _project(tmp_path, "first"), _project(tmp_path, "second")
    store.remember(first, FINGERPRINT)
    before = store.path.read_bytes()
    real_read_text = Path.read_text

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == store.path:
            raise PermissionError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    try:
        assert store.get(first) is None  # unreadable grants nothing...
        with pytest.raises(OSError):  # ...and is not something to save over
            store.remember(second, FINGERPRINT)
        with pytest.raises(OSError):
            store.forget(first)
    finally:
        monkeypatch.undo()

    assert store.path.read_bytes() == before
    assert store.get(first) is not None


def test_a_valid_file_is_not_kept_aside(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.remember(_project(tmp_path, "one"), FINGERPRINT)
    store.remember(_project(tmp_path, "two"), FINGERPRINT)

    assert not store.path.with_name("trust.json.corrupt").exists()


# ---------------------------------------------------------------------------
# Writing is safe
# ---------------------------------------------------------------------------


def test_the_missing_directories_are_created(tmp_path: Path):
    store = TrustStore(tmp_path / "deep" / "home")

    store.remember(_project(tmp_path), FINGERPRINT)

    assert store.path.is_file()


def test_no_temporary_file_is_left_behind(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.remember(_project(tmp_path), FINGERPRINT)

    assert sorted(p.name for p in store.path.parent.iterdir()) == ["trust.json"]


def test_a_failed_save_leaves_the_earlier_file_intact_and_tells_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = TrustStore(tmp_path)
    first = _project(tmp_path, "first")
    store.remember(first, FINGERPRINT)
    before = store.path.read_bytes()

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        store.remember(_project(tmp_path, "second"), FINGERPRINT)
    monkeypatch.undo()

    assert store.path.read_bytes() == before
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["trust.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_file_is_private_to_the_user(tmp_path: Path):
    store = TrustStore(tmp_path)
    store.remember(_project(tmp_path), FINGERPRINT)

    assert store.path.stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------------------
# The record type
# ---------------------------------------------------------------------------


def test_a_record_is_immutable():
    record = TrustRecord(project="/p", fingerprint=FINGERPRINT, trusted_at="t", resources=())

    with pytest.raises(AttributeError):
        record.fingerprint = "other"  # type: ignore[misc]
