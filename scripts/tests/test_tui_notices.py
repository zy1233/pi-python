"""Unit tests for scripts/tui_notices.py (tui/THIRD-PARTY-NOTICES kept in step with Cargo.lock)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tui_notices.py"
REAL_NOTICES = REPO_ROOT / "tui" / "THIRD-PARTY-NOTICES"


def _load():
    spec = importlib.util.spec_from_file_location("tui_notices", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


tn = _load()

SEP = "-" * 80
BAR = "=" * 80
TAIL = "PART I (continued) \u2014 BUNDLED UI / SYNTAX THEMES"
MIT_TEXT = "License text: see Part II \u2014 MIT"
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


# ---- builders: a notices file and a `cargo metadata` dump, written out by hand --------------


def entry_lines(
    name: str,
    version: str = "1.0.0",
    *,
    license_lines: tuple[str, ...] = ("License: MIT  (upstream declares: MIT OR Apache-2.0)",),
    copyright_lines: tuple[str, ...] = ("  Copyright (c) 2020 A. Author",),
    text_line: str = MIT_TEXT,
    notes: tuple[str, ...] = (),
) -> list[str]:
    lines = [SEP, f"{name} {version}", SEP, f"Source:  https://example.com/{name}"]
    lines += list(license_lines)
    lines += ["", "Copyright notice:", *copyright_lines, "", text_line]
    if notes:
        lines += ["", "Additional requirements / notices:", *notes]
    lines.append("")
    return lines


def notices_file(entries: list[list[str]], hand_written: tuple[str, ...] = ("by hand",)) -> str:
    lines = ["THIRD-PARTY NOTICES", BAR, "PART I \u2014 PER-PACKAGE ENTRIES", BAR, ""]
    for entry in entries:
        lines += entry
    lines += ["", BAR, TAIL, BAR, "", *hand_written, "", "PART II \u2014 LICENSE TEXTS"]
    return "\n".join(lines)


def package(
    name: str,
    version: str = "1.0.0",
    *,
    source: str | None = REGISTRY,
    manifest: str | None = None,
    license: str = "MIT",
    repository: str = "",
    authors: tuple[str, ...] = (),
) -> dict:
    return {
        "id": f"{name} {version}",
        "name": name,
        "version": version,
        "source": source,
        "manifest_path": manifest or f"/registry/{name}-{version}/Cargo.toml",
        "license": license,
        "repository": repository,
        "authors": list(authors),
    }


def metadata(packages: list[dict], deps: dict[str, list[tuple[str, tuple[str | None, ...]]]]):
    """``deps`` maps a package id to ``[(dependency id, kinds)]``. A kind: None, "dev", "build"."""
    nodes = []
    for pkg in packages:
        pid = pkg["id"]
        listed = [
            {"pkg": dep, "dep_kinds": [{"kind": kind, "target": None} for kind in kinds]}
            for dep, kinds in deps.get(pid, [])
        ]
        nodes.append({"id": pid, "deps": listed})
    return {"packages": packages, "resolve": {"nodes": nodes}}


def sample_metadata():
    """app needs a (normal), b (build), c (normal and dev), vendored, first; d only in dev."""
    packages = [
        package("app", source=None, manifest="/repo/tui/crates/app/Cargo.toml"),
        package("a", "1.2.0", license="MIT  OR\n Apache-2.0"),
        package("b", "0.3.1", license="Zlib"),
        package("c", "2.0.0"),
        package("d", "9.9.9"),
        package("below-d", "1.0.0"),
        package("vendored", source=None, manifest="/repo/tui/third_party/vendored/Cargo.toml"),
        package("first", source=None, manifest="/repo/tui/crates/first/Cargo.toml"),
        package("below-a", "0.1.0"),
    ]
    deps = {
        "app 1.0.0": [
            ("a 1.2.0", (None,)),
            ("b 0.3.1", ("build",)),
            ("c 2.0.0", (None, "dev")),
            ("d 9.9.9", ("dev",)),
            ("vendored 1.0.0", (None,)),
            ("first 1.0.0", (None,)),
        ],
        "a 1.2.0": [("below-a 0.1.0", (None,))],
        "d 9.9.9": [("below-d 1.0.0", (None,))],
    }
    return metadata(packages, deps)


# ---- which packages ship -------------------------------------------------------------------


def test_shipped_packages_follows_normal_and_build_edges_only():
    shipped = tn.shipped_packages(sample_metadata(), root="app")
    assert set(shipped) == {
        ("a", "1.2.0"),
        ("b", "0.3.1"),
        ("c", "2.0.0"),
        ("below-a", "0.1.0"),
        ("vendored", "1.0.0"),
    }


def test_shipped_packages_skips_a_dev_dependency_and_what_only_it_needs():
    shipped = tn.shipped_packages(sample_metadata(), root="app")
    assert ("d", "9.9.9") not in shipped
    assert ("below-d", "1.0.0") not in shipped


def test_shipped_packages_counts_vendored_crates_but_not_first_party_ones():
    shipped = tn.shipped_packages(sample_metadata(), root="app")
    assert ("vendored", "1.0.0") in shipped
    assert ("first", "1.0.0") not in shipped
    assert ("app", "1.0.0") not in shipped


def test_shipped_packages_collapses_white_space_in_the_license_expression():
    shipped = tn.shipped_packages(sample_metadata(), root="app")
    assert shipped[("a", "1.2.0")].license == "MIT OR Apache-2.0"


def test_shipped_packages_reads_repository_authors_and_directory():
    packages = [
        package("app", source=None),
        package(
            "x",
            repository=" https://github.com/o/x ",
            authors=("A <a@example.com>",),
            manifest="/registry/x-1.0.0/Cargo.toml",
        ),
    ]
    shipped = tn.shipped_packages(metadata(packages, {"app 1.0.0": [("x 1.0.0", (None,))]}), "app")
    pkg = shipped[("x", "1.0.0")]
    assert pkg.repository == "https://github.com/o/x"
    assert pkg.authors == ("A <a@example.com>",)
    assert pkg.directory == Path("/registry/x-1.0.0")


def test_shipped_packages_with_an_unknown_root_stops():
    with pytest.raises(SystemExit, match="nope"):
        tn.shipped_packages(sample_metadata(), root="nope")


def test_collect_takes_the_union_of_the_dumps(tmp_path):
    first = sample_metadata()
    second = sample_metadata()
    second["packages"].append(package("only-here", "3.0.0"))
    second["resolve"]["nodes"][0]["deps"].append(
        {"pkg": "only-here 3.0.0", "dep_kinds": [{"kind": None, "target": None}]}
    )
    second["resolve"]["nodes"].append({"id": "only-here 3.0.0", "deps": []})
    files = []
    for i, dump in enumerate((first, second)):
        path = tmp_path / f"meta-{i}.json"
        path.write_text(json.dumps(dump), encoding="utf-8")
        files.append(path)
    union = tn.collect(tmp_path, "app", ["unused"], files)
    assert ("only-here", "3.0.0") in union
    assert ("a", "1.2.0") in union
    assert len(union) == 6


def test_collect_keeps_the_package_of_the_first_dump_when_two_dumps_have_the_same_key(tmp_path):
    dumps = []
    for repository in ("https://example.com/first", "https://example.com/second"):
        packages = [package("app", source=None), package("a", repository=repository)]
        dumps.append(metadata(packages, {"app 1.0.0": [("a 1.0.0", (None,))]}))
    files = []
    for i, dump in enumerate(dumps):
        path = tmp_path / f"meta-{i}.json"
        path.write_text(json.dumps(dump), encoding="utf-8")
        files.append(path)
    union = tn.collect(tmp_path, "app", [], files)
    assert union[("a", "1.0.0")].repository == "https://example.com/first"


def test_run_metadata_asks_cargo_for_one_platform_and_the_locked_graph(monkeypatch, tmp_path):
    seen = {}

    class Done:
        returncode = 0
        stdout = '{"packages": [], "resolve": {"nodes": []}}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["cwd"] = cmd, kwargs["cwd"]
        return Done()

    monkeypatch.setattr(tn.subprocess, "run", fake_run)
    assert tn.run_metadata(tmp_path, "aarch64-apple-darwin")["packages"] == []
    assert seen["cwd"] == tmp_path
    assert seen["cmd"][:2] == ["cargo", "metadata"]
    assert "--locked" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--filter-platform") + 1] == "aarch64-apple-darwin"


def test_run_metadata_reports_a_cargo_failure(monkeypatch, tmp_path):
    class Done:
        returncode = 101
        stdout = ""
        stderr = "error: the lock file needs to be updated"

    monkeypatch.setattr(tn.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(SystemExit, match="lock file needs to be updated"):
        tn.run_metadata(tmp_path, "x86_64-unknown-linux-gnu")


# ---- the notices file ----------------------------------------------------------------------


def test_parse_notices_and_render_give_back_the_same_text():
    text = notices_file([entry_lines("a"), entry_lines("b", notes=("  A note.",))])
    notices = tn.parse_notices(text)
    assert [e.key for e in notices.entries] == [("a", "1.0.0"), ("b", "1.0.0")]
    assert notices.render() == text


def test_parse_notices_keeps_the_hand_written_part_apart_from_the_entries():
    notices = tn.parse_notices(notices_file([entry_lines("a")], hand_written=("keep me",)))
    assert notices.tail[0] == BAR
    assert "keep me" in notices.tail
    assert notices.head[0] == "THIRD-PARTY NOTICES"
    assert all("keep me" not in line for e in notices.entries for line in e.lines)


def test_parse_notices_without_the_marker_stops():
    with pytest.raises(SystemExit, match="is this the notices file"):
        tn.parse_notices("something else\n" + "\n".join(entry_lines("a")))


def test_parse_notices_without_an_entry_stops():
    with pytest.raises(SystemExit, match="no per-package entry"):
        tn.parse_notices(notices_file([]))


def test_a_line_of_dashes_inside_an_entry_does_not_start_a_new_entry():
    entry = entry_lines("a", copyright_lines=("  Copyright (c) 2020 A", SEP, "  text"))
    notices = tn.parse_notices(notices_file([entry, entry_lines("b")]))
    assert [e.key for e in notices.entries] == [("a", "1.0.0"), ("b", "1.0.0")]


def test_insert_entry_sorts_names_bytewise_and_versions_by_number():
    notices = tn.parse_notices(
        notices_file(
            [entry_lines("Zeta"), entry_lines("serde", "1.0.9"), entry_lines("serde", "1.0.100")]
        )
    )
    for name, version in (("serde", "1.0.10"), ("alpha", "1.0.0"), ("zzz", "0.1.0")):
        tn.insert_entry(notices, tn.Entry((name, version), entry_lines(name, version)))
    assert [e.key for e in notices.entries] == [
        ("Zeta", "1.0.0"),
        ("alpha", "1.0.0"),
        ("serde", "1.0.9"),
        ("serde", "1.0.10"),
        ("serde", "1.0.100"),
        ("zzz", "0.1.0"),
    ]


@pytest.mark.skipif(not REAL_NOTICES.exists(), reason="tui/ is not in this checkout")
def test_the_real_file_parses_and_renders_back_unchanged():
    text = REAL_NOTICES.read_text(encoding="utf-8")
    notices = tn.parse_notices(text)
    assert notices.render() == text
    keys = [e.key for e in notices.entries]
    assert len(keys) > 100
    assert len(set(keys)) == len(keys)


# ---- how an entry reads: learned from the entries that exist --------------------------------


def test_split_fields_reads_every_part_of_an_entry():
    entry = tn.Entry(
        ("a", "1.0.0"),
        entry_lines(
            "a",
            license_lines=("License: MIT  (upstream declares: MIT OR Apache-2.0)", "  second line"),
            copyright_lines=("  Copyright (c) 2020 A", "  Copyright (c) 2021 B"),
            notes=(
                "  Upstream license expression: MIT OR Apache-2.0. Satisfied under: MIT.",
                "  MPL-2.0 notice: source is at the URL above.",
                "  A sentence a person wrote.",
            ),
        ),
    )
    f = tn.split_fields(entry)
    assert f.source == "Source:  https://example.com/a"
    assert f.license_lines == [
        "License: MIT  (upstream declares: MIT OR Apache-2.0)",
        "  second line",
    ]
    assert f.copyright == ["  Copyright (c) 2020 A", "  Copyright (c) 2021 B"]
    assert f.text_line == MIT_TEXT
    assert f.notes == [
        "  Upstream license expression: MIT OR Apache-2.0. Satisfied under: MIT.",
        "  MPL-2.0 notice: source is at the URL above.",
    ]
    assert f.extra is True


def test_split_fields_of_a_plain_entry_has_no_notes_and_no_extra():
    f = tn.split_fields(tn.Entry(("a", "1.0.0"), entry_lines("a")))
    assert f.notes == []
    assert f.extra is False


def test_declared_expression_prefers_what_upstream_declared():
    with_note = tn.split_fields(tn.Entry(("a", "1"), entry_lines("a")))
    assert tn.declared_expression(with_note) == "MIT OR Apache-2.0"
    plain = tn.split_fields(
        tn.Entry(("b", "1"), entry_lines("b", license_lines=("License: Unlicense",)))
    )
    assert tn.declared_expression(plain) == "Unlicense"


def test_learn_policies_takes_the_wording_most_entries_use():
    common = ("License: MIT  (upstream declares: MIT OR Apache-2.0)",)
    rare = ("License: Apache-2.0  (upstream declares: MIT OR Apache-2.0)",)
    entries = [
        tn.Entry(("a", "1"), entry_lines("a", license_lines=common)),
        tn.Entry(("b", "1"), entry_lines("b", license_lines=common)),
        tn.Entry(("c", "1"), entry_lines("c", license_lines=rare)),
    ]
    policy = tn.learn_policies(entries)["MIT OR Apache-2.0"]
    assert policy.license_lines == common


def test_learn_policies_skips_an_entry_without_license_text_line():
    broken = entry_lines("a")
    broken.remove(MIT_TEXT)
    assert tn.learn_policies([tn.Entry(("a", "1"), broken)]) == {}


def test_learn_policies_turns_an_upstream_pointer_into_a_slot_for_the_source():
    entry = tn.Entry(
        ("a", "1"),
        entry_lines(
            "a",
            license_lines=("License: CC0-1.0",),
            text_line="License text: see upstream https://example.com/a (license: CC0-1.0)",
        ),
    )
    policy = tn.learn_policies([entry])["CC0-1.0"]
    assert policy.text_line == f"License text: see upstream {tn.SOURCE_SLOT} (license: CC0-1.0)"


def test_policy_for_an_alias_reads_like_the_base_license_and_says_what_upstream_declared():
    base = tn.Entry(
        ("a", "1"),
        entry_lines(
            "a",
            license_lines=("License: MPL-2.0",),
            text_line="License text: see Part II \u2014 MPL-2.0",
            notes=("  MPL-2.0 notice: the source is at the URL above.",),
        ),
    )
    policies = tn.learn_policies([base])
    policy = tn.policy_for("MPL-2.0+", policies)
    assert policy is not None
    assert policy.license_lines[0] == "License: MPL-2.0  (upstream declares: MPL-2.0+)"
    assert policy.text_line == "License text: see Part II \u2014 MPL-2.0"
    assert policy.notes == ("  MPL-2.0 notice: the source is at the URL above.",)


def test_policy_for_an_alias_gives_up_when_the_base_license_is_not_stated_plainly():
    odd = tn.Entry(
        ("a", "1"),
        entry_lines("a", license_lines=("License: MIT  (upstream declares: MPL-2.0)",)),
    )
    assert tn.policy_for("MPL-2.0+", tn.learn_policies([odd])) is None
    assert tn.policy_for("Nothing-1.0", tn.learn_policies([odd])) is None


# ---- copyright lines -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Copyright (c) 2019 Jane Doe",
        "Copyright 2019 Jane Doe",
        "COPYRIGHT 2019 JANE DOE",
        "Copyright (C) Jonas Schievink <jonas@example.com>",
        "Portions copyright 2019 The Foo Authors",
        "(c) 2019 Jane Doe",
        "\u00a9 2019 Jane Doe",
    ],
)
def test_is_notice_accepts_a_line_that_names_a_holder(line):
    assert tn.is_notice(line)


@pytest.mark.parametrize(
    "line",
    [
        "Copyright [yyyy] [name of copyright owner]",
        "Copyright (c) <year> <copyright holders>",
        "Copyright {yyyy} {name of copyright owner}",
        "THE COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM",
        "copyright law applies to this work",
        "Copyright notice, this list of conditions and the following disclaimer.",
        "copyrighted material",
        "Permission is hereby granted",
        "",
    ],
)
def test_is_notice_rejects_license_text_and_forms_to_fill_in(line):
    assert not tn.is_notice(line)


def test_copyright_lines_reads_the_license_files_in_order_without_repeats(tmp_path):
    (tmp_path / "LICENSE-MIT").write_text(
        "MIT License\n\nCopyright (c) 2019 Jane Doe\nCopyright (c) 2019 Jane Doe\n\n"
        "Permission is hereby granted, free of charge,\n"
        "THE COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM.\n",
        encoding="utf-8",
    )
    (tmp_path / "LICENSE-APACHE").write_text(
        "APPENDIX: How to apply\n   Copyright [yyyy] [name of copyright owner]\n", encoding="utf-8"
    )
    (tmp_path / "COPYING").write_text(" * Copyright 2001 Joe\n # (c) 2002 Ann\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Copyright (c) 1999 Not A License\n", encoding="utf-8")
    assert tn.copyright_lines(tmp_path) == [
        "Copyright 2001 Joe",
        "(c) 2002 Ann",
        "Copyright (c) 2019 Jane Doe",
    ]


def test_copyright_lines_without_a_directory_or_files_is_empty(tmp_path):
    assert tn.copyright_lines(None) == []
    assert tn.copyright_lines(tmp_path / "missing") == []
    assert tn.copyright_lines(tmp_path) == []


def test_copyright_lines_survives_a_license_file_that_is_not_utf8(tmp_path):
    (tmp_path / "LICENSE").write_bytes(b"Copyright (c) 2019 J\xe9r\xf4me\n")
    lines = tn.copyright_lines(tmp_path)
    assert len(lines) == 1 and lines[0].startswith("Copyright (c) 2019 J")


# ---- one entry -----------------------------------------------------------------------------


def make_pkg(**kwargs) -> object:
    defaults = {
        "name": "x",
        "version": "1.0.0",
        "license": "MIT",
        "repository": "",
        "authors": (),
        "directory": None,
    }
    return tn.Pkg(**{**defaults, **kwargs})


def test_source_line_prefers_the_repository_and_drops_a_trailing_git_or_slash():
    pkg = make_pkg(repository="https://github.com/o/x.git/")
    assert tn.source_line(pkg) == "Source:  https://github.com/o/x"


def test_source_line_falls_back_to_the_registry_page():
    assert tn.source_line(make_pkg(name="serde", version="1.0.2")) == (
        "Source:  https://crates.io/crates/serde/1.0.2"
    )


def test_copyright_block_uses_license_lines_then_authors_then_a_plain_sentence():
    pkg = make_pkg(authors=("A <a@example.com>", "B"))
    assert tn.copyright_block(pkg, ["Copyright (c) 2020 Z"]) == ["  Copyright (c) 2020 Z"]
    assert tn.copyright_block(pkg, []) == [
        "  Copyright holders / authors (from package metadata):",
        "    - A <a@example.com>",
        "    - B",
    ]
    assert tn.copyright_block(make_pkg(), []) == [
        "  Copyright (c) the package authors. See the package repository for details."
    ]


def _policies_from_one_entry():
    existing = tn.Entry(
        ("a", "1"),
        entry_lines(
            "a",
            notes=("  Upstream license expression: MIT OR Apache-2.0. Satisfied under: MIT.",),
        ),
    )
    return tn.learn_policies([existing])


def test_render_entry_copies_the_wording_of_entries_with_the_same_license():
    pkg = make_pkg(name="new", version="2.1.0", license="MIT OR Apache-2.0")
    entry, review = tn.render_entry(pkg, _policies_from_one_entry(), ["Copyright (c) 2020 N"])
    assert review is False
    assert entry.key == ("new", "2.1.0")
    assert entry.lines == [
        SEP,
        "new 2.1.0",
        SEP,
        "Source:  https://crates.io/crates/new/2.1.0",
        "License: MIT  (upstream declares: MIT OR Apache-2.0)",
        "",
        "Copyright notice:",
        "  Copyright (c) 2020 N",
        "",
        MIT_TEXT,
        "",
        "Additional requirements / notices:",
        "  Upstream license expression: MIT OR Apache-2.0. Satisfied under: MIT.",
        "",
    ]


def test_render_entry_puts_its_own_source_in_a_pointer_to_the_upstream_text():
    teacher = tn.Entry(
        ("a", "1"),
        entry_lines(
            "a",
            license_lines=("License: CC0-1.0",),
            text_line="License text: see upstream https://example.com/a (license: CC0-1.0)",
        ),
    )
    pkg = make_pkg(name="b", license="CC0-1.0", repository="https://example.com/b")
    entry, review = tn.render_entry(pkg, tn.learn_policies([teacher]), [])
    assert review is False
    assert "License text: see upstream https://example.com/b (license: CC0-1.0)" in entry.lines
    assert not any(tn.SOURCE_SLOT in line for line in entry.lines)


def test_render_entry_for_a_license_nobody_wrote_yet_is_plain_and_flagged():
    pkg = make_pkg(name="odd", license="Zlib", repository="https://example.com/odd")
    entry, review = tn.render_entry(pkg, _policies_from_one_entry(), [])
    assert review is True
    assert "License: Zlib" in entry.lines
    assert "License text: see upstream https://example.com/odd (license: Zlib)" in entry.lines
    assert "Additional requirements / notices:" not in entry.lines


def test_render_entry_with_no_license_in_the_metadata_says_unknown():
    entry, review = tn.render_entry(make_pkg(license=""), {}, [])
    assert review is True
    assert "License: unknown" in entry.lines


def test_a_rendered_entry_parses_back_as_the_same_entry():
    pkg = make_pkg(name="new", license="MIT OR Apache-2.0")
    entry, _ = tn.render_entry(pkg, _policies_from_one_entry(), ["Copyright (c) 2020 N"])
    notices = tn.parse_notices(notices_file([entry.lines, entry_lines("zzz")]))
    assert notices.entries[0].key == ("new", "1.0.0")
    assert notices.entries[0].lines == entry.lines
    assert tn.split_fields(notices.entries[0]).copyright == ["  Copyright (c) 2020 N"]


# ---- the plan and the commands -------------------------------------------------------------


def shipped_of(*names: str) -> dict:
    return {(n, "1.0.0"): make_pkg(name=n, repository=f"https://example.com/{n}") for n in names}


def test_make_plan_lists_what_is_missing_and_what_is_stale_in_order():
    notices = tn.parse_notices(notices_file([entry_lines("a"), entry_lines("b"), entry_lines("z")]))
    plan = tn.make_plan(notices, shipped_of("a", "m", "c"))
    assert plan.missing == [("c", "1.0.0"), ("m", "1.0.0")]
    assert plan.stale == [("b", "1.0.0"), ("z", "1.0.0")]


def test_a_new_version_of_a_package_is_one_missing_and_one_stale_entry():
    notices = tn.parse_notices(notices_file([entry_lines("a", "1.0.0")]))
    shipped = {("a", "2.0.0"): make_pkg(name="a", version="2.0.0")}
    plan = tn.make_plan(notices, shipped)
    assert plan.missing == [("a", "2.0.0")]
    assert plan.stale == [("a", "1.0.0")]


def test_apply_plan_removes_stale_entries_adds_missing_ones_and_keeps_the_rest_byte_for_byte():
    original = notices_file(
        [entry_lines("a", copyright_lines=("  Copyright (c) 1999 Keep Me",)), entry_lines("b")],
        hand_written=("hand written, keep",),
    )
    notices = tn.parse_notices(original)
    shipped = shipped_of("a", "c")
    shipped[("c", "1.0.0")] = make_pkg(name="c", license="MIT OR Apache-2.0")
    added, review = tn.apply_plan(notices, tn.make_plan(notices, shipped), shipped)
    assert added == [("c", "1.0.0")]
    assert review == []
    result = tn.parse_notices(notices.render())
    assert [e.key for e in result.entries] == [("a", "1.0.0"), ("c", "1.0.0")]
    assert "  Copyright (c) 1999 Keep Me" in result.entries[0].lines
    assert result.tail == tn.parse_notices(original).tail
    assert result.head == tn.parse_notices(original).head


def test_apply_plan_learns_from_a_stale_entry_before_it_removes_it():
    only_teacher = entry_lines(
        "old",
        license_lines=("License: BSD-3-Clause",),
        text_line="License text: see Part II \u2014 BSD-3-Clause",
        notes=("  Upstream license expression: BSD-3-Clause. Satisfied under: BSD-3-Clause.",),
    )
    notices = tn.parse_notices(notices_file([only_teacher]))
    shipped = {("new", "1.0.0"): make_pkg(name="new", license="BSD-3-Clause")}
    added, review = tn.apply_plan(notices, tn.make_plan(notices, shipped), shipped)
    assert added == [("new", "1.0.0")]
    assert review == []  # the wording came from the entry that was removed
    assert "License text: see Part II \u2014 BSD-3-Clause" in notices.entries[0].lines


def test_apply_plan_reports_an_entry_that_a_person_has_to_read():
    notices = tn.parse_notices(notices_file([entry_lines("a")]))
    shipped = {
        ("a", "1.0.0"): make_pkg(name="a", license="MIT OR Apache-2.0"),
        ("odd", "1.0.0"): make_pkg(name="odd", license="Zlib"),
    }
    _, review = tn.apply_plan(notices, tn.make_plan(notices, shipped), shipped)
    assert review == [("odd", "1.0.0")]


# ---- the command line ----------------------------------------------------------------------


def make_tree(tmp_path: Path, entries: list[list[str]], shipped_names: list[str]):
    """A `tui/` directory with a notices file, and a metadata dump saying what ships."""
    tui = tmp_path / "tui"
    tui.mkdir()
    notices = tui / "THIRD-PARTY-NOTICES"
    notices.write_bytes(notices_file(entries).encode("utf-8"))
    packages = [package("app", source=None)]
    packages += [package(n, license="MIT OR Apache-2.0") for n in shipped_names]
    dump = metadata(packages, {"app 1.0.0": [(f"{n} 1.0.0", (None,)) for n in shipped_names]})
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(dump), encoding="utf-8")
    return tui, notices, meta


def run_main(tui: Path, meta: Path, *command: str) -> int:
    return tn.main(["--tui-dir", str(tui), "--root", "app", "--metadata", str(meta), *command])


def test_a_repeated_metadata_option_does_not_take_the_sub_command_as_a_file(tmp_path):
    tui, _, meta = make_tree(tmp_path, [entry_lines("a")], ["a"])
    other = tmp_path / "other.json"
    other.write_bytes(meta.read_bytes())
    argv = ["--tui-dir", str(tui), "--root", "app"]
    argv += ["--metadata", str(meta), "--metadata", str(other), "check"]
    assert tn.main(argv) == 0


def test_check_passes_when_every_shipped_package_has_one_entry_and_nothing_else(tmp_path, capsys):
    tui, _, meta = make_tree(tmp_path, [entry_lines("a"), entry_lines("b")], ["a", "b"])
    assert run_main(tui, meta, "check") == 0
    assert "2 packages ship; 2 entries" in capsys.readouterr().out


def test_check_fails_for_a_missing_entry_and_for_a_stale_one(tmp_path, capsys):
    tui, _, meta = make_tree(tmp_path, [entry_lines("a"), entry_lines("gone")], ["a", "b"])
    assert run_main(tui, meta, "check") == 1
    out = capsys.readouterr().out
    assert "missing (1): b 1.0.0" in out
    assert "stale (1): gone 1.0.0" in out


def test_update_without_write_changes_nothing(tmp_path, capsys):
    tui, notices, meta = make_tree(tmp_path, [entry_lines("a"), entry_lines("gone")], ["a", "b"])
    before = notices.read_bytes()
    assert run_main(tui, meta, "update") == 0
    assert notices.read_bytes() == before
    assert "would write: 1 removed, 1 added" in capsys.readouterr().out


def test_update_with_write_makes_check_pass_and_a_second_update_changes_nothing(tmp_path):
    tui, notices, meta = make_tree(tmp_path, [entry_lines("a"), entry_lines("gone")], ["a", "b"])
    assert run_main(tui, meta, "update", "--write") == 0
    written = notices.read_bytes()
    assert b"\r" not in written
    assert b"\ngone 1.0.0\n" not in written
    assert b"\nb 1.0.0\n" in written
    assert run_main(tui, meta, "check") == 0
    assert run_main(tui, meta, "update", "--write") == 0
    assert notices.read_bytes() == written
