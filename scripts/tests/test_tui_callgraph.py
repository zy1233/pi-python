"""Unit tests for scripts/tui_callgraph.py (call graphs of the TUI workspace, from source text)."""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tui_callgraph.py"


def _load():
    spec = importlib.util.spec_from_file_location("tui_callgraph", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


tg = _load()


# ---- the Rust text helpers -----------------------------------------------------------------


def test_mask_non_code_blanks_comments_and_strings_and_keeps_the_layout():
    src = (
        'let a = "use x::y;"; // use p::q;\n'
        '/* use r::s; /* nested */ use t::u; */ let b = r#"use "v"::w;"#;\n'
        "let c = 'x'; let d = '\\''; fn f<'a>(x: &'a str) {}\n"
    )
    out = tg.mask_non_code(src)
    assert len(out) == len(src)
    assert out.count("\n") == src.count("\n")
    assert "use" not in out  # every `use` sat in a comment or a string
    assert "let a" in out and "let b" in out and "let c" in out and "let d" in out
    assert "fn f<'a>(x: &'a str)" in out  # a lifetime is code, not a char literal


def test_mask_non_code_handles_escaped_quotes_and_byte_and_raw_strings():
    src = (
        'let a = "say \\"use a::b;\\" now"; let b = br#"use c::d;"#; '
        'let e = r"use f::g;"; use h::i;'
    )
    out = tg.mask_non_code(src)
    assert "use a" not in out and "use c" not in out and "use f" not in out
    assert "use h::i;" in out


def test_strip_cfg_test_removes_test_items_and_reports_test_modules():
    src = textwrap.dedent(
        """\
        use a::keep;
        #[cfg(test)]
        use b::drop_a;
        #[cfg(test)]
        mod tests {
            use c::drop_b;
            fn t() { let _ = "}"; }
        }
        fn live() { d::keep(); }
        #[cfg(test)]
        #[path = "x_tests.rs"]
        mod extra;
        #[cfg(test)]
        pub(crate) mod more;
        #[cfg(not(test))]
        use e::keep;
        #[cfg(test)]
        impl X { fn g() { f::drop_c(); } }
        """
    )
    code, only = tg.strip_cfg_test(tg.mask_non_code(src), src)
    assert "drop" not in code
    assert "a::keep" in code and "d::keep" in code and "e::keep" in code
    assert only.modules == [("extra", "x_tests.rs"), ("more", None)]
    assert not only.whole_file
    assert len(code) == len(src)


def test_strip_cfg_test_sees_a_file_level_test_attribute():
    src = "#![cfg(test)]\nuse a::b;\n"
    assert tg.strip_cfg_test(tg.mask_non_code(src), src)[1].whole_file


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("a::b", [(("a", "b"), None)]),
        (
            "a::{b, c::d as e, f::*}",
            [(("a", "b"), None), (("a", "c", "d"), "e"), (("a", "f", "*"), None)],
        ),
        ("a::b::{self, c}", [(("a", "b"), None), (("a", "b", "c"), None)]),
        (
            "crate::x::{y::{z, w}, v}",
            [
                (("crate", "x", "y", "z"), None),
                (("crate", "x", "y", "w"), None),
                (("crate", "x", "v"), None),
            ],
        ),
        ("::std::fmt", [(("std", "fmt"), None)]),
        ("pi_http as http", [(("pi_http",), "http")]),
        ("a::{b, }", [(("a", "b"), None)]),
        ("super::*", [(("super", "*"), None)]),
        ("{a, b::c}", [(("a",), None), (("b", "c"), None)]),
        ("a::b as _", [(("a", "b"), "_")]),
    ],
)
def test_parse_use_tree(body, expected):
    assert tg.parse_use_tree(body) == expected


def test_parse_use_tree_survives_junk():
    assert tg.parse_use_tree("") == []
    assert tg.parse_use_tree("}{,,") is not None  # no exception, no endless loop


def test_module_path_and_test_path_follow_the_file_layout():
    assert tg.module_path("lib.rs") == ()
    assert tg.module_path("main.rs") == ()
    assert tg.module_path("a/mod.rs") == ("a",)
    assert tg.module_path("a/b.rs") == ("a", "b")
    assert tg.module_path("a/b/mod.rs") == ("a", "b")
    for rel in (
        "foo_tests.rs",
        "a/tests/x.rs",
        "a/tests.rs",
        "test_support/mod.rs",
        "a_tests/x.rs",
    ):
        assert tg.is_test_path(rel), rel
    for rel in ("attests.rs", "a/contest.rs", "testing.rs", "a/b.rs"):
        assert not tg.is_test_path(rel), rel


# ---- a small workspace ---------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8", newline="\n")


def _crate(tui: Path, name: str, manifest_tail: str, files: dict[str, str], dirname=None) -> None:
    root = tui / "crates" / (dirname or name)
    _write(root / "Cargo.toml", f'[package]\nname = "{name}"\nversion = "0.1.0"\n{manifest_tail}')
    for rel, text in files.items():
        _write(root / "src" / rel, text)


@pytest.fixture
def tui(tmp_path: Path) -> Path:
    """app (product) -> mid -> leaf -> aliased-name; devonly and orphan are not in the closure."""
    root = tmp_path / "tui"
    _write(
        root / "Cargo.toml",
        """
        [workspace]
        members = ["crates/*"]

        [workspace.dependencies]
        leaf = { path = "crates/leaf" }
        renamed = { path = "crates/aliased", package = "aliased-name" }
        """,
    )
    _crate(
        root,
        "app",
        """
        [dependencies]
        mid = { path = "../mid" }
        [target.'cfg(unix)'.dependencies]
        leaf = { workspace = true }
        [dev-dependencies]
        devonly = { path = "../devonly" }
        """,
        {
            "main.rs": """
                use mid::deep::inner;
                use mid::{alpha::run, beta, ef};
                fn main() {
                    run();
                    beta::go();
                    inner::x();
                    leaf::util::helper();
                    leaf::provider::p();
                    mid::lf::util::other();
                    ef();
                }
            """,
            "bin/playground.rs": "fn main() { mid::unused::thing(); }\n",
        },
    )
    _crate(
        root,
        "mid",
        """
        [dependencies]
        leaf = { workspace = true }
        [build-dependencies]
        buildhelper = { path = "../buildhelper" }
        """,
        {
            "lib.rs": """
                pub mod alpha;
                pub mod beta;
                pub mod deep;
                pub mod instr;
                pub mod style18;
                pub mod tested;
                pub mod unused;
                #[path = "renamed_file.rs"]
                pub mod pathmod;
                pub use alpha::exported as ef;
                pub use leaf as lf;
                #[macro_export]
                macro_rules! shout { () => {}; }
            """,
            "alpha.rs": """
                use crate::beta::helper;
                pub fn run() { helper(); }
                pub fn exported() {}
                #[cfg(test)]
                mod tests {
                    use crate::tested::t;
                }
            """,
            "beta.rs": """
                pub fn helper() {}
                pub fn go() {
                    crate::shout!();
                    crate::timer!();
                    crate::pathmod::f();
                    crate::beta::helper();
                    // crate::unused::thing();
                    let _ = "crate::unused::thing";
                }
            """,
            "instr.rs": """
                #[macro_export]
                #[doc(hidden)]
                macro_rules! timer { () => {}; }
            """,
            "renamed_file.rs": "pub fn f() {}\n",
            "deep/mod.rs": "pub mod inner;\npub mod dead_inner;\n",
            "deep/inner.rs": "use crate::style18::entry;\npub fn x() { entry(); }\n",
            "deep/dead_inner.rs": "pub fn y() {}\n",
            "style18.rs": "pub mod child;\nuse child::f;\npub fn entry() { f(); }\n",
            "style18/child.rs": "pub fn f() {}\n",
            "tested.rs": "pub fn t() {}\n",
            "beta_tests.rs": "use crate::unused::thing;\n",
            "unused.rs": "pub fn thing() { leaf::exclusive::x(); }\n",
        },
    )
    _crate(
        root,
        "leaf",
        """
        [dependencies]
        renamed = { workspace = true }
        """,
        {
            "lib.rs": "pub mod exclusive;\npub mod provider;\npub mod util;\n",
            "util.rs": "pub fn helper() { renamed::stuff::go(); }\npub fn other() {}\n",
            "exclusive.rs": "pub fn x() {}\n",
            "provider.rs": "use async_openai::Client;\npub fn p() { let _ = pi_sampler::X; }\n",
        },
    )
    _crate(
        root,
        "aliased-name",
        "",
        {
            "lib.rs": "pub mod stuff;\npub mod spare;\n",
            "stuff.rs": "pub fn go() {}\n",
            "spare.rs": "pub fn s() {}\n",
        },
        dirname="aliased",
    )
    _crate(root, "buildhelper", "", {"lib.rs": "pub fn h() {}\n"})
    _crate(root, "devonly", "", {"lib.rs": "pub fn d() {}\n"})
    _crate(
        root,
        "orphan",
        "[dependencies]\nleaf = { workspace = true }\n",
        {"lib.rs": "pub fn o() {}\n"},
    )
    return root


def test_load_workspace_reads_every_kind_of_dependency(tui):
    ws = tg.load_workspace(tui)
    assert sorted(ws.crates) == [
        "aliased-name",
        "app",
        "buildhelper",
        "devonly",
        "leaf",
        "mid",
        "orphan",
    ]
    assert ws.deps("app") == {"mid", "leaf"}  # normal + target-specific; the dev-dependency is out
    assert ws.deps("mid") == {"leaf", "buildhelper"}  # build-dependencies are edges too
    assert ws.deps("leaf") == {"aliased-name"}  # the workspace entry renames the package
    assert ws.crates["leaf"].idents == {"aliased-name": "renamed"}


def test_reach_and_dependents(tui):
    ws = tg.load_workspace(tui)
    assert ws.reach("app") == {"mid", "leaf", "buildhelper", "aliased-name"}
    assert ws.reach("app", skip={"leaf"}) == {"mid", "buildhelper"}
    assert ws.dependents("leaf") == ["app", "mid", "orphan"]
    assert ws.dependents("leaf", within={"app", "mid", "leaf"}) == ["app", "mid"]


def test_crates_report_says_what_is_in_the_closure_and_what_hangs_on_a_crate(tui):
    ws = tg.load_workspace(tui)
    data = tg.crates_report(ws, tops=["app"], without=["leaf"], who_uses="leaf")
    assert data["workspace_crates"] == 7
    assert data["reachable"] == 5  # app itself and four more
    assert data["not_reachable"] == ["devonly", "orphan"]
    assert data["without"]["only_through_skipped"] == ["aliased-name"]
    assert data["who_uses"]["dependents"] == ["app", "mid"]  # orphan is not part of the product
    text = tg.render_crates_text(data)
    assert "not reachable (2): devonly, orphan" in text
    assert "only through it: aliased-name" in text


def test_crate_graph_collects_edges_exports_aliases_and_references(tui):
    ws = tg.load_workspace(tui)
    g = tg.build_crate_graph(ws, "mid")
    assert set(g.loc) == {
        "<root>",
        "alpha",
        "beta",
        "deep",
        "deep::inner",
        "deep::dead_inner",
        "instr",
        "renamed_file",
        "style18",
        "style18::child",
        "tested",
        "unused",
    }
    assert g.loc["style18"] == 3
    assert g.test_files == 1  # beta_tests.rs
    assert g.edges["alpha"] >= {"beta"}
    assert "beta" not in g.edges["beta"]  # a unit that names itself has no edge to itself
    # the macro, the #[path] module and the code that calls them
    assert g.edges["beta"] >= {"<root>", "instr", "renamed_file"}
    assert "unused" not in g.edges["beta"]  # a comment and a string are not references
    assert "tested" not in g.edges["alpha"]  # only a #[cfg(test)] block names it
    assert "unused" not in g.edges.get("beta_tests", set())
    assert g.edges["style18"] >= {"style18::child"}  # `use child::f;` in a 2018-style layout
    assert g.edges["deep::inner"] >= {"style18", "deep"}  # a path, and the declaring module
    assert g.reexports == {"ef": ("alpha", "exported")}
    assert g.aliases == {"lf": ("leaf", ())}
    assert g.macros == {"shout": "<root>", "timer": "instr"}
    assert g.module_aliases == {("pathmod",): ("renamed_file",)}
    assert ("leaf", ("exclusive", "x")) in g.xrefs["unused"]


def test_crate_graph_counts_the_provider_stack_per_unit(tui):
    ws = tg.load_workspace(tui)
    g = tg.build_crate_graph(ws, "leaf")
    assert g.leak == {"provider": [1, 2]}
    off = tg.build_crate_graph(ws, "leaf", leak_pattern=None)
    assert off.leak == {}


def test_analyse_follows_references_from_live_code_only(tui):
    ws = tg.load_workspace(tui)
    reports = tg.analyse(ws, ["mid", "leaf", "aliased-name"], live_crates=["app"])

    mid = reports["mid"]
    assert not mid.live_crate
    assert mid.live == [
        "<root>",
        "alpha",
        "beta",
        "deep",
        "deep::inner",
        "instr",
        "renamed_file",
        "style18",
        "style18::child",
    ]
    # alive on paper only: nothing live names them, or only a test, a comment, a bin target does
    assert mid.dead == ["deep::dead_inner", "tested", "unused"]
    assert mid.roots["alpha"] == 2  # `mid::alpha::run` and the re-export `mid::ef`
    assert mid.dead_lines == sum(mid.loc[u] for u in mid.dead)

    leaf = reports["leaf"]
    assert leaf.live == ["<root>", "provider", "util"]
    assert leaf.dead == ["exclusive"]  # its only caller, mid::unused, is dead
    assert leaf.roots["util"] == 2  # app's `leaf::util::helper` and `mid::lf::util::other`
    assert leaf.leak_units == {"provider": [1, 2]}

    # util is live, and it is what refers to the renamed crate
    aliased = reports["aliased-name"]
    assert aliased.live == ["<root>", "stuff"]
    assert aliased.dead == ["spare"]


def test_a_product_crate_is_live_in_full(tui):
    reports = tg.analyse(tg.load_workspace(tui), ["app"], live_crates=["app"])
    assert reports["app"].live_crate
    assert reports["app"].dead == []


def test_ban_reports_cut_points_and_what_dies_with_the_banned_unit(tui):
    ws = tg.load_workspace(tui)
    reports = tg.analyse(ws, ["mid"], live_crates=["app"], banned={"mid": ["beta"]})
    mid = reports["mid"]
    assert mid.banned == ["beta"]
    assert mid.cut_points == ["alpha"]  # still refers to the unit that went away
    # beta took the macros and the #[path] module with it
    assert {"instr", "renamed_file"} <= set(mid.dead)
    # a banned prefix covers the units under it
    deep = tg.analyse(ws, ["mid"], live_crates=["app"], banned={"mid": ["deep"]})["mid"]
    assert deep.banned == ["deep", "deep::dead_inner", "deep::inner"]


def test_ban_on_a_product_crate_takes_the_unit_out_of_its_live_set(tui):
    ws = tg.load_workspace(tui)
    app = tg.analyse(ws, ["app"], live_crates=["app"], banned={"app": ["<root>"]})["app"]
    assert app.banned == ["<root>"]
    assert app.live == [] and app.dead == []  # banned units are neither live nor dead


def test_analyse_rejects_a_crate_that_is_not_shipped(tui):
    ws = tg.load_workspace(tui)
    with pytest.raises(SystemExit, match="orphan is not in the dependency closure"):
        tg.analyse(ws, ["orphan"], live_crates=["app"])
    with pytest.raises(SystemExit, match="unknown crate: nope"):
        tg.analyse(ws, ["nope"], live_crates=["app"])


def test_unresolved_references_are_reported_not_dropped(tui):
    _write(
        tui / "crates" / "app" / "src" / "main.rs",
        "fn main() { mid::Mystery::go(); leaf::util::helper(); }\n",
    )
    mid = tg.analyse(tg.load_workspace(tui), ["mid"], live_crates=["app"])["mid"]
    assert mid.unresolved == {"Mystery::go": 1}


def test_use_of_a_unit_only_inside_a_test_module_file_does_not_keep_it_alive(tui):
    # `#[cfg(test)] mod checks;` makes checks.rs test code whatever it is called
    _write(
        tui / "crates" / "mid" / "src" / "alpha.rs", "#[cfg(test)]\nmod checks;\npub fn run() {}\n"
    )
    # alpha.rs declares `checks`, so the file is src/alpha/checks.rs
    _write(tui / "crates" / "mid" / "src" / "alpha" / "checks.rs", "use crate::unused::thing;\n")
    g = tg.build_crate_graph(tg.load_workspace(tui), "mid")
    assert g.test_files == 2  # beta_tests.rs and alpha/checks.rs
    assert "unused" not in g.edges["alpha"]
    assert "alpha::checks" not in g.loc


# ---- output and command line ---------------------------------------------------------------


def test_markdown_report_has_the_tables_and_the_dead_list(tui):
    ws = tg.load_workspace(tui)
    report = tg.analyse(ws, ["mid"], live_crates=["app"])["mid"]
    md = tg.render_units_markdown(report)
    assert md.startswith("### `mid`")
    assert "| dead | 3 |" in md
    assert "`deep::dead_inner`" in md and "`unused`" in md
    assert "<details><summary>Live units</summary>" in md
    text = tg.render_units_text(report)
    assert "dead     3 units" in text
    assert "dead units: " in text


def test_keep_document_names_its_commit_and_covers_each_crate(tui):
    ws = tg.load_workspace(tui)
    doc = tg.build_keep_document(
        ws, live_crates=["app"], depth=2, commit="abc1234", crates=["mid", "leaf", "missing"]
    )
    assert "Commit: `abc1234`" in doc
    assert "| `mid` |" in doc and "| `leaf` |" in doc
    assert "missing" not in doc  # a crate the workspace does not have is skipped
    assert doc.endswith("\n") and not doc.endswith("\n\n")


def test_main_prints_json_for_units_and_crates(tui, capsys):
    assert (
        tg.main(
            ["--tui-dir", str(tui), "units", "--crate", "mid", "--live", "app", "--format", "json"]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data[0]["crate"] == "mid"
    assert data[0]["dead"] == {"deep::dead_inner": 1, "tested": 1, "unused": 1}
    assert tg.main(["--tui-dir", str(tui), "crates", "--live", "app", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["reachable"] == 5


def test_main_writes_the_keep_document_with_unix_line_endings(tui, tmp_path):
    out = tmp_path / "keep.md"
    argv = [
        "--tui-dir",
        str(tui),
        "keep",
        "--crate",
        "mid",
        "leaf",
        "--live",
        "app",
        "--out",
        str(out),
    ]
    assert tg.main(argv) == 0
    raw = out.read_bytes()
    assert b"\r" not in raw
    assert raw.decode("utf-8").startswith("# TUI: what the support layer keeps")


def test_main_ban_applies_to_the_first_crate(tui, capsys):
    argv = ["--tui-dir", str(tui), "units", "--crate", "mid", "--live", "app", "--ban", "beta"]
    assert tg.main(argv) == 0
    out = capsys.readouterr().out
    assert "banned 1 units" in out
    assert "cut points (1): alpha" in out


# ---- the real tree -------------------------------------------------------------------------

REAL_TUI = REPO_ROOT / "tui"


@pytest.mark.skipif(not (REAL_TUI / "crates").is_dir(), reason="tui/ is not checked out")
def test_the_real_workspace_has_the_product_in_its_closure():
    ws = tg.load_workspace(REAL_TUI)
    assert "pi-pager-bin" in ws.crates
    assert len(ws.reach("pi-pager-bin")) > 30
    assert set(tg.DEFAULT_LIVE) <= set(ws.crates)
    assert set(tg.SUPPORT_CRATES) <= set(ws.crates)
    assert all(c in ws.reach(list(tg.DEFAULT_LIVE)) for c in tg.SUPPORT_CRATES)


@pytest.mark.skipif(not (REAL_TUI / "crates").is_dir(), reason="tui/ is not checked out")
def test_the_real_tree_gives_a_graph_for_a_small_crate():
    ws = tg.load_workspace(REAL_TUI)
    report = tg.analyse(ws, ["pi-http"], live_crates=["pi-http"])["pi-http"]
    assert report.live_crate and report.total_lines > 0
    assert report.dead == []
    assert "<root>" in report.loc
