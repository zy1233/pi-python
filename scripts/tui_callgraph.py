#!/usr/bin/env python3
"""Call graphs of the TUI (Rust) workspace, read from the source text alone.

Context: docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md item 0.5 (it replaces the hand-written
"examples" list of what the support layer keeps); its output for the current tree is
docs/baselines/tui-support.md. Standard library only (Python >= 3.11). It needs no cargo and no
network, and it reads every file as UTF-8, so it gives the same answer on Linux, macOS and Windows.

    python scripts/tui_callgraph.py crates                      # closure of the product crates
    python scripts/tui_callgraph.py crates --without pi-shell   # ... and what only pi-shell brings
    python scripts/tui_callgraph.py crates --who-uses pi-tools  # direct dependents of one crate
    python scripts/tui_callgraph.py units --crate pi-shell      # which modules of a crate are live
    python scripts/tui_callgraph.py units --crate pi-shell --ban auth   # ... if `auth` went away
    python scripts/tui_callgraph.py keep --out docs/baselines/tui-support.md

Two levels:

``crates``  Internal crates only. An edge is a normal, build or target-specific dependency in a
            Cargo.toml (dev-dependencies are not edges). The closure starts at the product crates.

``units``   Inside a crate. A *unit* is a module path cut to --depth segments (default 2:
            ``util::config``), taken from the file layout. An edge is a ``use`` statement or a
            ``crate::`` / ``super::`` / ``self::`` path in non-test code; a reference from another
            crate (``pi_shell::util::config::X``, or through a ``pub use other_crate as alias`` at
            the crate root) is an edge from the unit that makes it. A unit is *live* when
            non-test code of a live unit of a dependent crate refers to it, directly or through
            other live units. Product crates (--live) count as live in full.

It is an estimate, an upper bound on what is live. A path in a type position counts like a call,
a ``use`` counts even when the name is never used, and a unit that a macro or a trait object
reaches without naming it is missed. What it cannot resolve it reports (``unresolved``) rather
than drop silently. The compiler has the last word: check a deletion with ``cargo check``.

What counts as a reference, beyond a ``use`` statement: a path that starts with ``crate``,
``self`` or ``super``; a path that starts at a child module of the module that writes it
(``hub_auth::init_metrics()`` in ``lib.rs``); a path that starts at another crate; and a path
inside a string of an attribute (``#[serde(with = "crate::util::serde_base64")]``). A reference
in a comment, a string or a ``#[cfg(test)]`` item does not count.

One thing it cannot see is an ``impl`` that code uses without a name (``?`` calls
``impl From<A> for B``). A dead unit that has ``impl`` blocks for types it does not define is
reported apart ("check first"). Items inside a live unit are out of scope: it finds modules, not
functions.

Check on the tree of 2026-10 (docs/PLAN/archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md
section 10.14): the files of all 17 units reported dead were deleted from a copy; ``cargo check``
failed for exactly one of them, and that one was in the "check first" list.
"""

from __future__ import annotations

import argparse
import collections
import json
import posixpath
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TUI = REPO_ROOT / "tui"

# What ships: the product binary, its library, the minimal pager and the updater.
DEFAULT_LIVE = ("pi-pager-bin", "pi-pager", "pi-pager-minimal", "pi-update")
# `keep` reports the support layer: the biggest crate under the pager plus the crates that
# docs/baselines/tui.md section 4 gives a reason for.
SUPPORT_CRATES = (
    "pi-shell",
    "pi-sampling-types",
    "pi-tools",
    "pi-agent",
    "pi-workspace",
    "pi-hooks",
    "pi-mcp",
)
# References that keep the LLM provider stack (async-openai and friends) in the dependency graph.
PROVIDER_STACK = r"\b(?:pi_sampler|async_openai|pi_sampling_types)::|\bcrate::sampling\b"
ROOT_UNIT = "<root>"
DEFAULT_DEPTH = 2


# --------------------------------------------------------------------------------------------
# Rust source text (pure helpers, unit-tested)
# --------------------------------------------------------------------------------------------

_NON_NEWLINE = re.compile(r"[^\n]")
# The next thing that can hide code: a line comment, a block comment, a (raw) string, a quote.
_LEXEME = re.compile(r'//[^\n]*|/\*|(?<![A-Za-z0-9_])(?:br|cr|r)(#*)"|"|\'')
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)


def _blank(chunk: str) -> str:
    return _NON_NEWLINE.sub(" ", chunk)


def _block_comment_end(text: str, start: int) -> int:
    depth, pos = 1, start + 2
    while depth:
        opener, closer = text.find("/*", pos), text.find("*/", pos)
        if closer < 0:
            return len(text)
        if 0 <= opener < closer:
            depth, pos = depth + 1, opener + 2
        else:
            depth, pos = depth - 1, closer + 2
    return pos


def _char_literal_end(text: str, start: int) -> int | None:
    """End of the char literal at ``start``, or None when the quote opens a lifetime."""
    nxt = text[start + 1 : start + 2]
    if nxt == "\\":
        close = text.find("'", start + 3)
        return close + 1 if close >= 0 else None
    if nxt and text[start + 2 : start + 3] == "'":
        return start + 3
    return None


def mask_non_code(text: str) -> str:
    """Blank out comments, string literals and char literals; line breaks stay where they are.

    What is left is code, so a regex over it does not trip on ``// see crate::x`` or on a string
    that happens to contain ``use a::b;``. Offsets are the same as in ``text``.
    """
    out: list[str] = []
    flushed = pos = 0
    while m := _LEXEME.search(text, pos):
        start, lexeme = m.start(), m.group(0)
        if lexeme.startswith("//"):
            end = m.end()
        elif lexeme == "/*":
            end = _block_comment_end(text, start)
        elif lexeme == "'":
            end = _char_literal_end(text, start)
            if end is None:  # a lifetime or a label: it is code
                pos = start + 1
                continue
        elif lexeme == '"':
            s = _STRING.match(text, start)
            end = s.end() if s else len(text)
        else:  # raw string: runs to the quote followed by the same number of hashes
            terminator = '"' + m.group(1)
            close = text.find(terminator, m.end())
            end = len(text) if close < 0 else close + len(terminator)
        out.append(text[flushed:start])
        out.append(_blank(text[start:end]))
        flushed = pos = end
    out.append(text[flushed:])
    return "".join(out)


def _match_brace(code: str, open_at: int) -> int:
    """Index just after the ``}`` that closes the ``{`` at ``open_at`` (``code`` is masked)."""
    depth = 0
    for i in range(open_at, len(code)):
        ch = code[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(code)


def _item_end(code: str, start: int) -> int:
    """End of the item that starts at ``start``: its first top-level ``;`` or ``{ ... }``."""
    depth = 0
    for i in range(start, len(code)):
        ch = code[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "{" and depth <= 0:
            return _match_brace(code, i)
        elif ch == ";" and depth <= 0:
            return i + 1
    return len(code)


_CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
_INNER_CFG_TEST = re.compile(r"#\s*!\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
_ATTRIBUTE_AT = re.compile(r"\s*#\s*\[")
_MOD_DECL = re.compile(r"\s*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;")
_PATH_ATTR = re.compile(r'path\s*=\s*"([^"]*)"')


@dataclass
class TestOnly:
    """What `strip_cfg_test` found: a file-level ``#![cfg(test)]`` and ``#[cfg(test)] mod x;``."""

    whole_file: bool = False
    # (module name, value of a `#[path = "..."]` on the declaration or None)
    modules: list[tuple[str, str | None]] = field(default_factory=list)


def strip_cfg_test(code: str, original: str) -> tuple[str, TestOnly]:
    """Blank out every item marked ``#[cfg(test)]``; say which file modules it declares.

    ``code`` is ``mask_non_code(original)``; the two have the same offsets (``original`` is read
    only for the string of a ``#[path = "..."]``).
    """
    found = TestOnly(whole_file=bool(_INNER_CFG_TEST.search(code)))
    pieces: list[str] = []
    flushed = 0
    for m in _CFG_TEST.finditer(code):
        if m.start() < flushed:  # inside an item that is already blanked
            continue
        pos, path_attr = m.end(), None
        while a := _ATTRIBUTE_AT.match(code, pos):  # attributes between the cfg and the item
            close = code.find("]", a.end())
            if close < 0:
                break
            if p := _PATH_ATTR.search(original, a.end(), close):
                path_attr = p.group(1)
            pos = close + 1
        if decl := _MOD_DECL.match(code, pos):
            found.modules.append((decl.group(1), path_attr))
        end = _item_end(code, pos)
        pieces.append(code[flushed : m.start()])
        pieces.append(_blank(code[m.start() : end]))
        flushed = end
    pieces.append(code[flushed:])
    return "".join(pieces), found


UseLeaf = tuple[tuple[str, ...], str | None]  # (path, `as` name)
_USE_TOKEN = re.compile(r"::|\{|\}|,|\*|[A-Za-z_]\w*")


def parse_use_tree(body: str) -> list[UseLeaf]:
    """``a::{b, c::d as e, f::*}`` -> ``[(a, b), (a, c, d) as e, (a, f, *)]``.

    A trailing ``self`` is dropped (``a::{self}`` names ``a``). Anything that is not part of a
    path is skipped, so ``pub`` and stray whitespace do no harm.
    """
    toks = _USE_TOKEN.findall(body)
    pos = 0

    def tree(prefix: tuple[str, ...]) -> list[UseLeaf]:
        nonlocal pos
        path = list(prefix)
        if pos < len(toks) and toks[pos] == "::":
            pos += 1
        while pos < len(toks):
            tok = toks[pos]
            if tok == "{":
                pos += 1
                leaves: list[UseLeaf] = []
                while pos < len(toks) and toks[pos] != "}":
                    before = pos
                    leaves += tree(tuple(path))
                    # a comma separates the items; no progress means a stray token: skip it
                    if (pos < len(toks) and toks[pos] == ",") or pos == before:
                        pos += 1
                pos += 1  # the closing brace
                return leaves
            if tok == "*":
                pos += 1
                return [((*path, "*"), None)]
            if tok in ("::", ",", "}") or tok == "as":
                break
            path.append(tok)
            pos += 1
            if pos < len(toks) and toks[pos] == "::":
                pos += 1
                continue
            break
        alias = None
        if pos + 1 < len(toks) and toks[pos] == "as":
            alias = toks[pos + 1]
            pos += 2
        if len(path) > 1 and path[-1] == "self":
            path.pop()
        return [(tuple(path), alias)] if path else []

    return tree(())


_USE_STATEMENT = re.compile(r"\buse\s+([^;]+);")
_SELF_PATH = re.compile(r"(?<![\w])(crate|super|self)((?:::[A-Za-z_]\w*)+)")
_PATH_ATTRIBUTE = re.compile(r"#\s*\[\s*path\s*=")
# `#[path = "x.rs"] mod name;` (other attributes may sit in between), read from the original text
_PATH_MODULE = re.compile(
    r'#\s*\[\s*path\s*=\s*"([^"]*)"\s*\]\s*(?:#\s*\[[^\]]*\]\s*)*'
    r"(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;"
)
_MACRO_EXPORT = re.compile(
    r"#\s*\[\s*macro_export\b[^\]]*\]\s*(?:#\s*\[[^\]]*\]\s*)*macro_rules!\s*([A-Za-z_]\w*)"
)
# A path written as a string in an attribute, which `mask_non_code` blanks:
# `#[serde(with = "crate::util::serde_base64")]`. Read from the original text; the caller checks
# that the attribute itself is code.
_ATTRIBUTE_PATH_STRING = re.compile(
    r"\b(?:with|serialize_with|deserialize_with|default|skip_serializing_if|from|into|try_from)"
    r'\s*=\s*"([A-Za-z_]\w*(?:::[A-Za-z_]\w*)+)"'
)
# An `impl` item (at the start of a line; `-> impl Trait` in a signature is not one) up to its `{`.
_IMPL_ITEM = re.compile(r"(?m)^[ \t]*(?:unsafe[ \t]+)?impl\b([^{};]*)\{")
_TYPE_ITEM = re.compile(r"\b(?:struct|enum|union|trait|type)\s+([A-Za-z_]\w*)")
_IMPL_TARGET = re.compile(
    r"[&\s]*(?:'\w+\s+)?(?:mut\s+)?(?:dyn\s+)?([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)"
)


def impl_types(code: str) -> set[str]:
    """The type each ``impl`` item in ``code`` is for (``impl Tr for Ty`` and ``impl Ty``).

    The last segment of the path is the name; ``?`` stands for a type it cannot read (a tuple, a
    slice, a macro variable).
    """
    names: set[str] = set()
    for m in _IMPL_ITEM.finditer(code):
        head = m.group(1).strip().replace("->", "--")  # the `>` of an arrow is not a bracket
        if head.startswith("<"):  # the generics of the impl
            depth = 0
            for i, ch in enumerate(head):
                depth += (ch == "<") - (ch == ">")
                if depth == 0:
                    head = head[i + 1 :].strip()
                    break
        after_for = re.search(r"\bfor\s+(?!<)(.*)$", head, re.S)  # not the `for<'a>` of a bound
        target = _IMPL_TARGET.match(after_for.group(1) if after_for else head)
        names.add(target.group(1).split("::")[-1] if target else "?")
    return names


def is_test_path(rel: str) -> bool:
    """Whether a file under a crate's ``src/`` (posix, relative) is test-only by its name."""
    rel = "/" + rel
    return (
        "/tests/" in rel
        or rel.endswith(("_tests.rs", "/tests.rs"))
        or "_tests/" in rel
        or rel.startswith("/test_support")
    )


def module_path(rel: str) -> tuple[str, ...]:
    """Module path a file gets from the layout: ``a/b.rs`` and ``a/b/mod.rs`` are ``(a, b)``."""
    parts = PurePosixPath(rel).parts
    last = parts[-1]
    if last in ("lib.rs", "main.rs") and len(parts) == 1:
        return ()
    if last == "mod.rs":
        return tuple(parts[:-1])
    return (*parts[:-1], last.removesuffix(".rs"))


# --------------------------------------------------------------------------------------------
# Workspace: crates and their dependencies
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Crate:
    name: str
    root: Path
    deps: frozenset[str]  # internal crates it depends on (normal, build, target-specific)
    idents: Mapping[str, str]  # internal dependency -> the identifier its code uses

    @property
    def src(self) -> Path:
        return self.root / "src"


class Workspace:
    def __init__(self, crates: Mapping[str, Crate]) -> None:
        self.crates = dict(crates)

    def deps(self, name: str) -> frozenset[str]:
        return self.crates[name].deps

    def reach(self, start: str | Iterable[str], skip: Iterable[str] = ()) -> set[str]:
        """Crates reachable from ``start`` (itself excluded unless a cycle leads back to it)."""
        skipped = set(skip)
        seen: set[str] = set()
        stack = [start] if isinstance(start, str) else list(start)
        while stack:
            for dep in self.deps(stack.pop()):
                if dep not in seen and dep not in skipped:
                    seen.add(dep)
                    stack.append(dep)
        return seen

    def dependents(self, name: str, within: Iterable[str] | None = None) -> list[str]:
        pool = self.crates if within is None else set(within) & self.crates.keys()
        return sorted(c for c in pool if name in self.crates[c].deps)


def _dependency_tables(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tables = [manifest.get("dependencies", {}), manifest.get("build-dependencies", {})]
    for target in manifest.get("target", {}).values():
        tables += [target.get("dependencies", {}), target.get("build-dependencies", {})]
    return tables


def load_workspace(tui_dir: Path) -> Workspace:
    """Read every ``Cargo.toml`` under ``tui_dir/crates``; keep the dependencies on those crates."""
    manifests: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted((tui_dir / "crates").rglob("Cargo.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if "package" in data:
            manifests[data["package"]["name"]] = (path.parent, data)
    root_manifest = tui_dir / "Cargo.toml"
    shared: Mapping[str, Any] = {}
    if root_manifest.is_file():
        shared = (
            tomllib.loads(root_manifest.read_text(encoding="utf-8"))
            .get("workspace", {})
            .get("dependencies", {})
        )
    crates: dict[str, Crate] = {}
    for name, (root, data) in manifests.items():
        idents: dict[str, str] = {}
        for table in _dependency_tables(data):
            for key, spec in table.items():
                spec = spec if isinstance(spec, dict) else {}
                inherited = shared.get(key, {}) if spec.get("workspace") else {}
                inherited = inherited if isinstance(inherited, dict) else {}
                real = spec.get("package") or inherited.get("package") or key
                if real in manifests and real != name:
                    idents[real] = key.replace("-", "_")
        crates[name] = Crate(name, root, frozenset(idents), idents)
    return Workspace(crates)


# --------------------------------------------------------------------------------------------
# One crate: units and the edges between them
# --------------------------------------------------------------------------------------------


@dataclass
class FileInfo:
    rel: str
    mod_path: tuple[str, ...]
    loc: int
    code: str
    test: bool
    test_modules: list[tuple[str, str | None]]
    path_attributes: int
    path_modules: list[tuple[str, str]]  # (module name, file) of `#[path = "file"] mod name;`
    attribute_paths: list[tuple[str, ...]] = field(default_factory=list)  # `with = "a::b"`


@dataclass
class CrateGraph:
    name: str
    depth: int
    loc: dict[str, int] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    # unit -> references to other crates: (crate, path inside it)
    xrefs: dict[str, list[tuple[str, tuple[str, ...]]]] = field(
        default_factory=lambda: collections.defaultdict(list)
    )
    # root-level names: `pub use a::b::Name` -> (a, b, Name); `pub use other as alias` -> crate
    reexports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    aliases: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    macros: dict[str, str] = field(default_factory=dict)  # `#[macro_export]` name -> unit
    # `#[path = "x.rs"] mod name;`: the module path code uses -> the one the file layout gives
    module_aliases: dict[tuple[str, ...], tuple[str, ...]] = field(default_factory=dict)
    leak: dict[str, list[int]] = field(default_factory=dict)  # unit -> [files, hits]
    path_attributes: dict[str, int] = field(default_factory=dict)  # unit -> `#[path]` count
    # unit -> types its `impl` blocks are for, when the unit does not define them (see `impl_types`)
    foreign_impls: dict[str, list[str]] = field(default_factory=dict)
    test_files: int = 0

    @property
    def units(self) -> set[str]:
        return set(self.loc)

    def unit_of(self, path: Sequence[str]) -> str | None:
        """The unit a module path falls in: the longest unit that is a prefix of it."""
        for k in range(min(self.depth, len(path)), 0, -1):
            candidate = "::".join(path[:k])
            if candidate in self.loc:
                return candidate
        return None


def _read_files(src: Path) -> list[FileInfo]:
    files: list[FileInfo] = []
    for path in sorted(src.rglob("*.rs")):
        rel = path.relative_to(src).as_posix()
        if rel.startswith("bin/"):  # extra binaries are other targets, not this crate's code
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        masked = mask_non_code(original)
        code, only = strip_cfg_test(masked, original)
        files.append(
            FileInfo(
                rel=rel,
                mod_path=module_path(rel),
                loc=original.count("\n") + (0 if original.endswith("\n") or not original else 1),
                code=code,
                test=is_test_path(rel) or only.whole_file,
                test_modules=only.modules,
                path_attributes=len(_PATH_ATTRIBUTE.findall(code)),
                path_modules=[
                    (m.group(2), m.group(1))
                    for m in _PATH_MODULE.finditer(original)
                    if code[m.start()] == "#"  # code, not a comment, a string or a test item
                ],
                attribute_paths=[
                    tuple(m.group(1).split("::"))
                    for m in _ATTRIBUTE_PATH_STRING.finditer(original)
                    if code[m.start()] != " "  # the attribute is code, not text or a test item
                ],
            )
        )
    # `#[cfg(test)] mod x;` makes the file(s) of module x test code, whatever they are called.
    by_rel = {f.rel: f for f in files}
    for f in list(files):
        for name, path_attr in f.test_modules:
            if path_attr is not None:  # relative to the directory of the declaring file
                target = posixpath.normpath(posixpath.join(posixpath.dirname(f.rel), path_attr))
                if target in by_rel:
                    by_rel[target].test = True
                continue
            child = (*f.mod_path, name)
            for other in files:
                if other.mod_path[: len(child)] == child:
                    other.test = True
    return files


def _resolve_relative(
    path: Sequence[str], here: tuple[str, ...], children: Mapping[tuple[str, ...], set[str]]
) -> tuple[str, ...] | None:
    """Absolute module path of a path as written in module ``here``; None when it is not local."""
    head = path[0]
    if head == "crate":
        return tuple(path[1:])
    if head == "self":
        return (*here, *path[1:])
    if head == "super":
        ups = next((i for i, seg in enumerate(path) if seg != "super"), len(path))
        return (*here[: max(len(here) - ups, 0)], *path[ups:])
    if head in children.get(here, ()):  # `use child::x;` in a module that declares `child`
        return (*here, *path)
    return None


def build_crate_graph(
    ws: Workspace,
    name: str,
    *,
    depth: int = DEFAULT_DEPTH,
    leak_pattern: str | None = PROVIDER_STACK,
) -> CrateGraph:
    crate = ws.crates[name]
    graph = CrateGraph(name, depth)
    files = _read_files(crate.src)
    graph.test_files = sum(f.test for f in files)
    prod = [f for f in files if not f.test]
    defined: dict[str, set[str]] = collections.defaultdict(set)
    implemented: dict[str, set[str]] = collections.defaultdict(set)
    for f in prod:
        unit = "::".join(f.mod_path[:depth]) or ROOT_UNIT
        graph.loc[unit] = graph.loc.get(unit, 0) + f.loc
        defined[unit].update(_TYPE_ITEM.findall(f.code))
        implemented[unit].update(impl_types(f.code))
    graph.foreign_impls = {
        unit: sorted(names - defined[unit])
        for unit, names in implemented.items()
        if names - defined[unit]
    }

    children: dict[tuple[str, ...], set[str]] = collections.defaultdict(set)
    for f in files:
        for i in range(len(f.mod_path)):
            children[f.mod_path[:i]].add(f.mod_path[i])
    by_rel = {f.rel: f for f in files}
    for f in prod:
        followed = 0
        for mod_name, file_name in f.path_modules:
            target = by_rel.get(
                posixpath.normpath(posixpath.join(posixpath.dirname(f.rel), file_name))
            )
            if target is not None:
                graph.module_aliases[(*f.mod_path, mod_name)] = target.mod_path
                children[f.mod_path].add(mod_name)
                followed += 1
        if rest := f.path_attributes - followed:
            unit = "::".join(f.mod_path[:depth]) or ROOT_UNIT
            graph.path_attributes[unit] = graph.path_attributes.get(unit, 0) + rest

    idents = {ident: dep for dep, ident in crate.idents.items()}
    foreign = (
        re.compile(
            r"(?<![\w])("
            + "|".join(re.escape(i) for i in sorted(idents))
            + r")((?:::[A-Za-z_]\w*)+)"
        )
        if idents
        else None
    )
    leak_re = re.compile(leak_pattern) if leak_pattern else None

    def leaves_and_paths(f: FileInfo) -> tuple[list[UseLeaf], list[tuple[str, ...]]]:
        leaves: list[UseLeaf] = []
        for m in _USE_STATEMENT.finditer(f.code):
            leaves += parse_use_tree(m.group(1))
        # Paths written out in the code; the `use` statements are blanked so nothing counts twice.
        body = _USE_STATEMENT.sub(lambda m: _blank(m.group(0)), f.code)
        inline = [
            (m.group(1), *[s for s in m.group(2).split("::") if s])
            for m in _SELF_PATH.finditer(body)
        ]
        if foreign:
            inline += [
                (m.group(1), *[s for s in m.group(2).split("::") if s])
                for m in foreign.finditer(body)
            ]
        # `child::init()` in a module that declares `child`: a path may start at a child module.
        if kids := children.get(f.mod_path):
            child_path = re.compile(
                r"(?<![\w:])("
                + "|".join(re.escape(k) for k in sorted(kids))
                + r")((?:::[A-Za-z_]\w*)+)"
            )
            inline += [
                (m.group(1), *[s for s in m.group(2).split("::") if s])
                for m in child_path.finditer(body)
            ]
        inline += f.attribute_paths
        return leaves, inline

    for f in prod:
        unit = "::".join(f.mod_path[:depth]) or ROOT_UNIT
        for m in _MACRO_EXPORT.finditer(f.code):
            graph.macros[m.group(1)] = unit

    # Pass 1: what the crate root re-exports (names other code reaches through the root).
    for f in prod:
        if f.mod_path:
            continue
        for m in _USE_STATEMENT.finditer(f.code):
            for path, alias in parse_use_tree(m.group(1)):
                if path[-1] == "*":
                    continue
                exported = alias or path[-1]
                if path[0] in idents:
                    graph.aliases[exported] = (idents[path[0]], path[1:])
                    continue
                local = _resolve_relative(path, f.mod_path, children)
                if local is not None and local:
                    graph.reexports[exported] = unalias(graph, local)

    # Pass 2: edges, references to other crates, provider-stack hits.
    for f in prod:
        unit = "::".join(f.mod_path[:depth]) or ROOT_UNIT
        leaves, inline = leaves_and_paths(f)
        for path in [leaf[0] for leaf in leaves] + inline:
            if not path:
                continue
            if path[0] in idents:
                graph.xrefs[unit].append((idents[path[0]], tuple(path[1:])))
                continue
            local = _resolve_relative(path, f.mod_path, children)
            if local is None:
                continue
            if local and local[0] in graph.aliases:
                target, prefix = graph.aliases[local[0]]
                graph.xrefs[unit].append((target, (*prefix, *local[1:])))
                continue
            target_unit = resolve_in_crate(graph, local)
            if target_unit is not None and target_unit != unit:
                graph.edges[unit].add(target_unit)
        if leak_re and (hits := len(leak_re.findall(f.code))):
            counts = graph.leak.setdefault(unit, [0, 0])
            counts[0] += 1
            counts[1] += hits
    # A unit cannot be there without the module that declares it.
    for unit in graph.loc:
        if unit != ROOT_UNIT:
            parent = unit.rpartition("::")[0] or ROOT_UNIT
            if parent in graph.loc:
                graph.edges[unit].add(parent)
    return graph


def unalias(graph: CrateGraph, path: Sequence[str]) -> tuple[str, ...]:
    """Rewrite a module path that goes through ``#[path = "x.rs"] mod name;`` to the layout's."""
    for k in range(len(path), 0, -1):
        target = graph.module_aliases.get(tuple(path[:k]))
        if target is not None:
            return (*target, *path[k:])
    return tuple(path)


def resolve_in_crate(graph: CrateGraph, path: Sequence[str]) -> str | None:
    """The unit a path inside ``graph``'s crate points at, or None when it is not one of them.

    A name at the crate root that is not a module is looked up in the root's re-exports, then
    taken to be an item of the root file itself.
    """
    if not path:
        return ROOT_UNIT if ROOT_UNIT in graph.loc else None
    path = unalias(graph, path)
    if unit := graph.unit_of(path):
        return unit
    if path[0] in graph.reexports:
        unit = graph.unit_of((*graph.reexports[path[0]], *path[1:]))
        if unit:
            return unit
    if len(path) == 1 and path[0] in graph.macros:
        return graph.macros[path[0]]
    return ROOT_UNIT if ROOT_UNIT in graph.loc and len(path) == 1 else None


# --------------------------------------------------------------------------------------------
# The whole program: which units are live
# --------------------------------------------------------------------------------------------


def closure(
    edges: Mapping[str, Iterable[str]], starts: Iterable[str], banned: frozenset[str] = frozenset()
) -> set[str]:
    seen: set[str] = set()
    stack = [s for s in starts if s not in banned]
    while stack:
        unit = stack.pop()
        if unit in seen:
            continue
        seen.add(unit)
        stack += [v for v in edges.get(unit, ()) if v not in banned]
    return seen


@dataclass
class CrateReport:
    crate: str
    live_crate: bool  # a product crate: every unit counts as live
    total_lines: int
    loc: dict[str, int]
    live: list[str]
    dead: list[str]
    roots: dict[str, int]  # unit -> references from live code outside the crate
    banned: list[str]
    cut_points: list[str]
    leak_units: dict[str, list[int]]  # live units that refer to the provider stack
    unresolved: dict[str, int]  # names reached from outside that match no unit
    path_attributes: int
    test_files: int
    # dead unit -> types its `impl` blocks are for that it does not define: "check first"
    dead_impls: dict[str, list[str]] = field(default_factory=dict)

    @property
    def live_lines(self) -> int:
        return sum(self.loc[u] for u in self.live)

    @property
    def dead_lines(self) -> int:
        return sum(self.loc[u] for u in self.dead)


def _topological(ws: Workspace, names: set[str]) -> list[str]:
    """Dependents before the crates they depend on."""
    pending = {n: {d for d in ws.deps(n) if d in names} for n in names}
    needs_me: dict[str, set[str]] = collections.defaultdict(set)
    for n, deps in pending.items():
        for d in deps:
            needs_me[d].add(n)
    order: list[str] = []
    ready = sorted(n for n in names if not needs_me[n])
    while ready:
        n = ready.pop(0)
        order.append(n)
        for d in sorted(pending[n]):
            needs_me[d].discard(n)
            if not needs_me[d]:
                ready.append(d)
    if len(order) != len(names):  # a cycle cannot happen in a Cargo workspace; do not loop on it
        order += sorted(names - set(order))
    return order


def analyse(
    ws: Workspace,
    targets: Sequence[str],
    *,
    live_crates: Sequence[str] = DEFAULT_LIVE,
    depth: int = DEFAULT_DEPTH,
    banned: Mapping[str, Iterable[str]] | None = None,
    leak_pattern: str | None = PROVIDER_STACK,
) -> dict[str, CrateReport]:
    """Reports for ``targets``. Product crates (``live_crates``) are live in full; everything
    else is live from the references that live units of its dependents make."""
    tops = [c for c in live_crates if c in ws.crates]
    shipped = set(tops) | ws.reach(tops)
    for target in targets:
        if target not in ws.crates:
            raise SystemExit(f"unknown crate: {target}")
        if target not in shipped:
            raise SystemExit(f"{target} is not in the dependency closure of {', '.join(tops)}")
    # Only the crates above a target matter: they are where its references come from.
    needed = set(targets)
    for n in shipped:
        if needed & ws.reach(n):
            needed.add(n)

    graphs = {n: build_crate_graph(ws, n, depth=depth, leak_pattern=leak_pattern) for n in needed}
    ban_sets = {n: frozenset(ban) for n, ban in (banned or {}).items()}
    banned_units = {
        n: frozenset(
            u for u in g.loc if any(u == b or u.startswith(b + "::") for b in ban_sets.get(n, ()))
        )
        for n, g in graphs.items()
    }

    roots: dict[str, collections.Counter[str]] = {n: collections.Counter() for n in needed}
    unresolved: dict[str, collections.Counter[str]] = {n: collections.Counter() for n in needed}
    live: dict[str, set[str]] = {}

    def follow(crate: str, path: Sequence[str], hops: int = 0) -> None:
        """Count a reference to ``path`` in ``crate``; follow an alias to the crate behind it."""
        if crate not in graphs or hops > 8:
            return
        graph = graphs[crate]
        if not path:
            return
        head = path[0]
        if head in graph.aliases and not graph.unit_of(path[:1]):
            target, prefix = graph.aliases[head]
            follow(target, (*prefix, *path[1:]), hops + 1)
            return
        unit = resolve_in_crate(graph, path)
        if unit is None:
            unresolved[crate]["::".join(path[:2])] += 1
        elif unit not in banned_units[crate]:
            roots[crate][unit] += 1

    for name in _topological(ws, needed):
        graph = graphs[name]
        if name in tops:
            live[name] = set(graph.loc)
        else:
            live[name] = closure(graph.edges, roots[name], banned_units[name])
        live[name] -= banned_units[name]
        for unit in live[name]:
            for target, path in graph.xrefs.get(unit, ()):
                follow(target, path)

    reports: dict[str, CrateReport] = {}
    for name in targets:
        graph = graphs[name]
        gone = banned_units[name]
        cut = sorted(u for u in live[name] if graph.edges.get(u, set()) & gone)
        dead = sorted(set(graph.loc) - live[name] - gone)
        reports[name] = CrateReport(
            crate=name,
            live_crate=name in tops,
            total_lines=sum(graph.loc.values()),
            loc=dict(graph.loc),
            live=sorted(live[name]),
            dead=dead,
            roots=dict(roots[name]),
            banned=sorted(gone),
            cut_points=cut,
            leak_units={u: graph.leak[u] for u in sorted(live[name]) if u in graph.leak},
            unresolved=dict(unresolved[name].most_common()),
            path_attributes=sum(graph.path_attributes.values()),
            test_files=graph.test_files,
            dead_impls={u: graph.foreign_impls[u] for u in dead if u in graph.foreign_impls},
        )
    return reports


# --------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def _names(items: Iterable[str], *, ticks: bool = False) -> str:
    return ", ".join(f"`{i}`" if ticks else i for i in items) or "none"


def _leak_totals(report: CrateReport) -> tuple[int, int, int]:
    """(units, files, references) that reach the provider stack."""
    values = report.leak_units.values()
    return len(report.leak_units), sum(v[0] for v in values), sum(v[1] for v in values)


def _top_unresolved(report: CrateReport, limit: int = 8) -> str:
    return ", ".join(f"{k} ({v})" for k, v in list(report.unresolved.items())[:limit])


def render_units_text(report: CrateReport) -> str:
    total = report.total_lines
    live, dead = len(report.live), len(report.dead)
    lines = [
        f"{report.crate}: {total:,} lines of non-test code in {len(report.loc)} units"
        + (" (product crate: all live)" if report.live_crate else ""),
        f"  live  {live:>4} units {report.live_lines:>9,} lines ({_pct(report.live_lines, total)})",
        f"  dead  {dead:>4} units {report.dead_lines:>9,} lines ({_pct(report.dead_lines, total)})",
    ]
    if report.banned:
        gone = sum(report.loc[u] for u in report.banned)
        lines.append(f"  banned {len(report.banned)} units {gone:,} lines: {_names(report.banned)}")
        lines.append(f"  cut points ({len(report.cut_points)}): {_names(report.cut_points)}")
    if report.leak_units:
        units, files, hits = _leak_totals(report)
        lines.append(f"  provider stack: {units} live units, {files} files, {hits} references")
    if report.unresolved:
        count = sum(report.unresolved.values())
        lines.append(f"  unresolved references from outside: {count}: {_top_unresolved(report)}")
    if report.path_attributes:
        lines.append(f"  #[path = ...] in non-test code: {report.path_attributes} (not followed)")
    if report.dead:
        lines.append("  dead units: " + ", ".join(f"{u} ({report.loc[u]:,})" for u in report.dead))
    if report.dead_impls:
        which = "; ".join(f"{u} ({', '.join(t)})" for u, t in report.dead_impls.items())
        lines.append(f"  dead, with impls for types defined elsewhere (check first): {which}")
    return "\n".join(lines)


def render_units_markdown(report: CrateReport, *, level: int = 3) -> str:
    total = report.total_lines
    live, dead = len(report.live), len(report.dead)
    out = [
        f"{'#' * level} `{report.crate}`",
        "",
        f"Non-test code: {total:,} lines in {len(report.loc)} units"
        + (". Product crate: all of it is live." if report.live_crate else "."),
        "",
        "| | units | lines | share |",
        "|---|---|---|---|",
        f"| live | {live} | {report.live_lines:,} | {_pct(report.live_lines, total)} |",
        f"| dead | {dead} | {report.dead_lines:,} | {_pct(report.dead_lines, total)} |",
        "",
    ]
    if report.banned:
        out += [
            f"Banned: {_names(report.banned, ticks=True)}. Cut points (live units that still "
            f"refer to them): {_names(report.cut_points, ticks=True)}.",
            "",
        ]
    if report.leak_units:
        units, files, hits = _leak_totals(report)
        which = ", ".join(f"`{u}` ({v[1]})" for u, v in report.leak_units.items())
        out += [
            "Provider stack (`pi_sampler`, `async_openai`, `pi_sampling_types`, "
            f"`crate::sampling`): {units} live units, {files} files, {hits} references: {which}.",
            "",
        ]
    if report.dead:
        which = ", ".join(f"`{u}` ({report.loc[u]:,})" for u in report.dead)
        out += [f"Dead units (nothing live refers to them): {which}.", ""]
    if report.dead_impls:
        which = "; ".join(
            f"`{u}` ({', '.join(f'`{t}`' for t in types)})"
            for u, types in report.dead_impls.items()
        )
        out += [
            "Check these dead units first. Each has `impl` blocks for types that other units "
            "define, and code uses an impl without naming it, so the compiler may need the unit: "
            f"{which}.",
            "",
        ]
    if report.unresolved:
        count = sum(report.unresolved.values())
        out += [
            f"Unresolved: {count} references from outside name nothing that is a unit here "
            f"(an item of the root file, a glob re-export, or a macro): {_top_unresolved(report)}.",
            "",
        ]
    if report.path_attributes:
        out += [
            f"`#[path = ...]` appears {report.path_attributes} times in its non-test code without "
            "a `mod name;` the tool can follow; a module that code reaches by another name "
            "looks dead.",
            "",
        ]
    if report.live and not report.live_crate:
        out += [
            "<details><summary>Live units</summary>",
            "",
            "| unit | lines | references from live code outside |",
            "|---|---|---|",
        ]
        ordered = sorted(report.live, key=lambda u: (-report.loc[u], u))
        out += [f"| `{u}` | {report.loc[u]:,} | {report.roots.get(u, 0)} |" for u in ordered]
        out += ["", "</details>", ""]
    return "\n".join(out)


def report_to_json(report: CrateReport) -> dict[str, Any]:
    return {
        "crate": report.crate,
        "live_crate": report.live_crate,
        "total_lines": report.total_lines,
        "units": len(report.loc),
        "live": {u: report.loc[u] for u in report.live},
        "dead": {u: report.loc[u] for u in report.dead},
        "dead_impls": report.dead_impls,
        "live_lines": report.live_lines,
        "dead_lines": report.dead_lines,
        "roots": report.roots,
        "banned": report.banned,
        "cut_points": report.cut_points,
        "provider_stack": report.leak_units,
        "unresolved": report.unresolved,
        "path_attributes": report.path_attributes,
        "test_files": report.test_files,
    }


def crates_report(
    ws: Workspace,
    *,
    tops: Sequence[str],
    without: Sequence[str] = (),
    who_uses: str | None = None,
) -> dict[str, Any]:
    tops = [t for t in tops if t in ws.crates]
    closure_all = ws.reach(tops) | set(tops)
    result: dict[str, Any] = {
        "workspace_crates": len(ws.crates),
        "product_crates": tops,
        "reachable": len(closure_all),
        "not_reachable": sorted(set(ws.crates) - closure_all),
    }
    if without:
        kept = ws.reach(tops, skip=without) | set(tops)
        gone = closure_all - kept - set(without)
        result["without"] = {
            "skipped": list(without),
            "reachable": len(kept),
            "only_through_skipped": sorted(gone),
        }
    if who_uses:
        if who_uses not in ws.crates:
            raise SystemExit(f"unknown crate: {who_uses}")
        result["who_uses"] = {"crate": who_uses, "dependents": ws.dependents(who_uses, closure_all)}
    return result


def render_crates_text(data: Mapping[str, Any]) -> str:
    lines = [
        f"{data['workspace_crates']} crates in the workspace; "
        f"{data['reachable']} in the closure of {', '.join(data['product_crates'])}",
    ]
    if data["not_reachable"]:
        lines.append(
            f"not reachable ({len(data['not_reachable'])}): {', '.join(data['not_reachable'])}"
        )
    if "without" in data:
        w = data["without"]
        only = w["only_through_skipped"]
        lines.append(
            f"without {', '.join(w['skipped'])}: {w['reachable']} reachable; "
            f"{len(only)} only through it: {', '.join(only) or '-'}"
        )
    if "who_uses" in data:
        w = data["who_uses"]
        deps = w["dependents"]
        lines.append(f"direct dependents of {w['crate']} ({len(deps)}): {', '.join(deps) or '-'}")
    return "\n".join(lines)


def commit_of(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


KEEP_HEADER = """\
# TUI: what the support layer keeps

This file is generated. Do not edit it by hand.

- Command: `python scripts/tui_callgraph.py keep`. Commit: `{commit}`.
- Run the command again after a change that moves code between modules or crates.
- It replaces the hand-written list of examples in item 0.5 of
  [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md).

## How to read it

- A *unit* is a module, cut to {depth} path segments.
- A unit is *live* when live non-test code refers to it.
  The product crates ({live}) are live in full.
- The numbers are an estimate. A `use` counts. A type in a signature counts.
  A macro that reaches code without a name does not count.
- A *dead* unit is a candidate for deletion, not proof. Delete it, then run `cargo check`.
- *Check first* counts the dead units that have `impl` blocks for types that other units define.
  Code uses such an impl without a name (`?` calls `impl From<A> for B`), so the compiler may
  need the unit.
- Method, limits and flags: the docstring of `scripts/tui_callgraph.py`.

"""


def build_keep_document(
    ws: Workspace, *, live_crates: Sequence[str], depth: int, commit: str, crates: Sequence[str]
) -> str:
    reports = analyse(
        ws, [c for c in crates if c in ws.crates], live_crates=live_crates, depth=depth
    )
    summary = [
        "| crate | non-test lines | live | dead | dead units | check first | provider-stack refs |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports.values():
        live = f"{r.live_lines:,} ({_pct(r.live_lines, r.total_lines)})"
        dead = f"{r.dead_lines:,} ({_pct(r.dead_lines, r.total_lines)})"
        hits = _leak_totals(r)[2]
        summary.append(
            f"| `{r.crate}` | {r.total_lines:,} | {live} | {dead} | {len(r.dead)} "
            f"| {len(r.dead_impls)} | {hits} |"
        )
    parts = [
        KEEP_HEADER.format(
            commit=commit, depth=depth, live=", ".join(f"`{c}`" for c in live_crates)
        ),
        "## Summary",
        "",
        *summary,
        "",
        "## Per crate",
        "",
    ]
    parts += [render_units_markdown(r) for r in reports.values()]
    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------------------------


def use_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tui-dir", type=Path, default=DEFAULT_TUI)
    sub = parser.add_subparsers(dest="command", required=True)

    p_crates = sub.add_parser("crates", help="crate-level dependency closure")
    p_crates.add_argument("--live", nargs="+", default=list(DEFAULT_LIVE), metavar="CRATE")
    p_crates.add_argument("--without", nargs="+", default=[], metavar="CRATE")
    p_crates.add_argument("--who-uses", metavar="CRATE")
    p_crates.add_argument("--format", choices=["text", "json"], default="text")

    p_units = sub.add_parser("units", help="module-level liveness inside one or more crates")
    p_units.add_argument("--crate", nargs="+", default=["pi-shell"], dest="crates", metavar="CRATE")
    p_units.add_argument("--live", nargs="+", default=list(DEFAULT_LIVE), metavar="CRATE")
    p_units.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    p_units.add_argument(
        "--ban",
        nargs="+",
        default=[],
        metavar="UNIT",
        help="what-if: units of the first --crate that go away",
    )
    p_units.add_argument(
        "--leak", default=PROVIDER_STACK, help="regex for the provider-stack count ('' = off)"
    )
    p_units.add_argument("--format", choices=["text", "md", "json"], default="text")

    p_keep = sub.add_parser("keep", help="markdown keep list of the support crates")
    p_keep.add_argument(
        "--crate", nargs="+", default=list(SUPPORT_CRATES), dest="crates", metavar="CRATE"
    )
    p_keep.add_argument("--live", nargs="+", default=list(DEFAULT_LIVE), metavar="CRATE")
    p_keep.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    p_keep.add_argument("--out", type=Path, help="write here instead of stdout")

    args = parser.parse_args(argv)
    ws = load_workspace(args.tui_dir)

    if args.command == "crates":
        data = crates_report(ws, tops=args.live, without=args.without, who_uses=args.who_uses)
        print(json.dumps(data, indent=2) if args.format == "json" else render_crates_text(data))
        return 0

    if args.command == "units":
        banned = {args.crates[0]: args.ban} if args.ban else None
        reports = analyse(
            ws,
            args.crates,
            live_crates=args.live,
            depth=args.depth,
            banned=banned,
            leak_pattern=args.leak or None,
        )
        if args.format == "json":
            print(json.dumps([report_to_json(r) for r in reports.values()], indent=2))
        elif args.format == "md":
            print("\n".join(render_units_markdown(r) for r in reports.values()))
        else:
            print("\n\n".join(render_units_text(r) for r in reports.values()))
        return 0

    text = build_keep_document(
        ws,
        live_crates=args.live,
        depth=args.depth,
        commit=commit_of(args.tui_dir.parent),
        crates=args.crates,
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
