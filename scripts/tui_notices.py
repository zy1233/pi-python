#!/usr/bin/env python3
"""Keep tui/THIRD-PARTY-NOTICES in step with the packages that ship in the `zypi` binary.

Context: docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md item C.3. The file was written by the
upstream project for its own dependency graph. It does not track `Cargo.lock`. This tool
finds the difference and fixes it. Standard library only (Python >= 3.11). It runs `cargo metadata`,
so run it where cargo is (WSL or CI), or pass `--metadata FILE` for a saved `cargo metadata` dump.

    python scripts/tui_notices.py check               # exit 1: an entry is missing or stale
    python scripts/tui_notices.py update              # show what would change
    python scripts/tui_notices.py update --write      # change tui/THIRD-PARTY-NOTICES

What ships: the packages in the normal and build dependency closure of the product binary
(`pi-pager-bin`), for the union of the targets in `DEFAULT_TARGETS`. Packages from crates.io or git
count, and so do the vendored crates under `tui/third_party/`. First-party crates do not.

What `update` does, and what it leaves alone:

- It removes the entries of packages that do not ship (Part I, one entry per package).
- It adds an entry for every package that ships and has none. The new entry follows the format of
  the existing entries. The license wording (which license applies, the notes) is copied from the
  existing entries that have the same SPDX expression, so the tool adds no legal policy of its own.
  A package with an expression that no entry has is written in a plain form and listed as
  "needs review"; a person has to read it.
- It does not touch an entry whose package still ships, the hand-written sections (bundled themes,
  in-tree source ports) or Part II (license texts).
- The copyright lines come from the license files in the package source (the cargo registry
  cache), or from the package authors when there is no such line.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TUI = REPO_ROOT / "tui"
DEFAULT_ROOT = "pi-pager-bin"
# The targets of docs/baselines/tui.md section 3.1. The release workflow builds the first and the
# third. The other three are kept so that a build for them has its notices too.
DEFAULT_TARGETS = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
)

SEP = "-" * 80
# The first line after the per-package part. The line above it is the "====" bar.
TAIL_MARKER = "PART I (continued) \u2014 BUNDLED UI / SYNTAX THEMES"

Key = tuple[str, str]  # (package name, version)


# --------------------------------------------------------------------------------------------
# The shipped packages (from `cargo metadata`)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pkg:
    name: str
    version: str
    license: str
    repository: str
    authors: tuple[str, ...]
    directory: Path | None

    @property
    def key(self) -> Key:
        return (self.name, self.version)


def _vendored(manifest: str) -> bool:
    """A path package under `third_party/` is third-party code kept in the tree."""
    return "third_party" in Path(manifest).parts


def shipped_packages(metadata: Mapping, root: str = DEFAULT_ROOT) -> dict[Key, Pkg]:
    """Packages in the normal and build dependency closure of ``root`` that are not first-party."""
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    start = [pid for pid, p in packages.items() if p["name"] == root]
    if not start:
        raise SystemExit(f"package {root!r} is not in the metadata")
    seen: set[str] = set()
    stack = list(start)
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for dep in nodes[pid]["deps"]:
            kinds = {kind["kind"] for kind in dep["dep_kinds"]}
            if kinds <= {"dev"}:  # a dev-dependency does not ship
                continue
            stack.append(dep["pkg"])
    result: dict[Key, Pkg] = {}
    for pid in seen:
        p = packages[pid]
        if p.get("source") is None and not _vendored(p["manifest_path"]):
            continue  # first-party
        pkg = Pkg(
            name=p["name"],
            version=p["version"],
            license=" ".join((p.get("license") or "").split()),
            repository=(p.get("repository") or "").strip(),
            authors=tuple(p.get("authors") or ()),
            directory=Path(p["manifest_path"]).parent,
        )
        result[pkg.key] = pkg
    return result


def run_metadata(tui: Path, target: str) -> dict:
    proc = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--filter-platform", target],
        cwd=tui,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"cargo metadata failed for {target}:\n{proc.stderr.strip()[-2000:]}")
    return json.loads(proc.stdout)


def collect(
    tui: Path, root: str, targets: Sequence[str], metadata_files: Sequence[Path]
) -> dict[Key, Pkg]:
    """The union of what ships for each target."""
    dumps: list[Mapping]
    if metadata_files:
        dumps = [json.loads(Path(f).read_text(encoding="utf-8")) for f in metadata_files]
    else:
        dumps = [run_metadata(tui, t) for t in targets]
    union: dict[Key, Pkg] = {}
    for dump in dumps:
        for key, pkg in shipped_packages(dump, root).items():
            union.setdefault(key, pkg)
    return union


# --------------------------------------------------------------------------------------------
# The notices file
# --------------------------------------------------------------------------------------------


@dataclass
class Entry:
    key: Key
    lines: list[str]  # the three header lines, the body, and the blank line that ends it


@dataclass
class Notices:
    head: list[str]
    entries: list[Entry]
    tail: list[str]  # from the "====" bar of the first hand-written section to the end

    def render(self) -> str:
        out = list(self.head)
        for e in self.entries:
            out += e.lines
        out += self.tail
        return "\n".join(out)


_HEADER = re.compile(r"^(\S+) (\S+)$")


def parse_notices(text: str) -> Notices:
    lines = text.split("\n")
    try:
        tail_at = next(i for i, line in enumerate(lines) if line.startswith(TAIL_MARKER)) - 1
    except StopIteration:
        raise SystemExit(
            f"no line starts with {TAIL_MARKER!r}; is this the notices file?"
        ) from None
    starts: list[int] = []
    for i in range(tail_at - 2):
        if lines[i] == SEP and lines[i + 2] == SEP and _HEADER.match(lines[i + 1]):
            starts.append(i)
    if not starts:
        raise SystemExit("no per-package entry found")
    entries = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else tail_at
        m = _HEADER.match(lines[start + 1])
        assert m is not None
        entries.append(Entry((m.group(1), m.group(2)), lines[start:end]))
    return Notices(lines[: starts[0]], entries, lines[tail_at:])


def _version_key(version: str) -> tuple[tuple[int, ...], str]:
    return (tuple(int(n) for n in re.findall(r"\d+", version)), version)


def sort_key(key: Key) -> tuple[str, tuple[tuple[int, ...], str]]:
    return (key[0], _version_key(key[1]))


def insert_entry(notices: Notices, entry: Entry) -> None:
    """Put ``entry`` before the first entry that sorts after it.

    Names sort bytewise. Versions sort by number.
    """
    mine = sort_key(entry.key)
    for i, other in enumerate(notices.entries):
        if sort_key(other.key) > mine:
            notices.entries.insert(i, entry)
            return
    notices.entries.append(entry)


# --------------------------------------------------------------------------------------------
# The wording of an entry: learned from the entries that exist
# --------------------------------------------------------------------------------------------

_NOTE_PREFIXES = (
    "  Upstream license expression:",
    "  Apache-2.0 additional terms:",
    "  MPL-2.0 notice:",
    "  Zlib additional terms:",
)


@dataclass(frozen=True)
class Policy:
    """How entries with one SPDX expression read: the License lines, the Part II pointer, notes."""

    license_lines: tuple[str, ...]
    text_line: str
    notes: tuple[str, ...]


@dataclass
class Fields:
    source: str = ""
    license_lines: list[str] = field(default_factory=list)
    copyright: list[str] = field(default_factory=list)
    text_line: str = ""
    notes: list[str] = field(default_factory=list)
    extra: bool = False  # the notes hold more than the standard sentences


def split_fields(entry: Entry) -> Fields:
    body = entry.lines[3:]
    f = Fields()
    section = ""
    for line in body:
        if line.startswith("Source:"):
            f.source, section = line, "source"
        elif line.startswith("License:"):
            f.license_lines, section = [line], "license"
        elif line.startswith("Copyright notice:"):
            section = "copyright"
        elif line.startswith("License text:"):
            f.text_line, section = line, "text"
        elif line.startswith("Additional requirements / notices:"):
            section = "notes"
        elif section == "license" and line.startswith("  "):
            f.license_lines.append(line)
        elif section == "copyright" and line.strip():
            f.copyright.append(line)
        elif section == "notes":
            f.notes.append(line)
        elif line.strip():
            section = ""
    while f.notes and not f.notes[-1].strip():
        f.notes.pop()
    standard: list[str] = []
    for line in f.notes:
        if line.startswith(_NOTE_PREFIXES):
            standard.append(line)
        else:
            break
    f.extra = len(standard) != len(f.notes)
    f.notes = standard
    return f


_DECLARED = re.compile(r"\(upstream declares: (.*)\)\s*$")


def declared_expression(f: Fields) -> str:
    """The SPDX expression the package declared, as far as the entry says."""
    first = f.license_lines[0] if f.license_lines else ""
    m = _DECLARED.search(first)
    if m:
        return m.group(1).strip()
    return first.removeprefix("License:").strip()


# A license with no text in Part II points at the upstream repository of the package.
_UPSTREAM_TEXT = re.compile(r"^License text: see upstream \S+ (\(license: .*\))$")
SOURCE_SLOT = "{source}"


def learn_policies(entries: Iterable[Entry]) -> dict[str, Policy]:
    """For each expression, the wording that most entries with it use."""
    seen: dict[str, collections.Counter[Policy]] = collections.defaultdict(collections.Counter)
    for entry in entries:
        f = split_fields(entry)
        if not f.license_lines or not f.text_line:
            continue
        text_line = f.text_line
        if m := _UPSTREAM_TEXT.match(text_line):
            text_line = f"License text: see upstream {SOURCE_SLOT} {m.group(1)}"
        policy = Policy(tuple(f.license_lines), text_line, tuple(f.notes))
        seen[declared_expression(f)][policy] += 1
    return {expr: counts.most_common(1)[0][0] for expr, counts in seen.items()}


# Expressions that are not valid SPDX but mean the same as another one ("or later" is met by the
# base license). The entry reads like the base license's entries and says what upstream declared.
SPDX_ALIASES = {"MPL-2.0+": "MPL-2.0"}


def policy_for(expression: str, policies: Mapping[str, Policy]) -> Policy | None:
    if policy := policies.get(expression):
        return policy
    base = SPDX_ALIASES.get(expression)
    base_policy = policies.get(base) if base else None
    if base_policy is None or base_policy.license_lines[0] != f"License: {base}":
        return None
    first = f"License: {base}  (upstream declares: {expression})"
    return Policy((first, *base_policy.license_lines[1:]), base_policy.text_line, base_policy.notes)


# --------------------------------------------------------------------------------------------
# Copyright lines
# --------------------------------------------------------------------------------------------

_LICENSE_FILE = re.compile(
    r"^(licen[cs]e|copying|unlicense|copyright|notice)([-_.].*)?$", re.IGNORECASE
)
_NOTICE_START = re.compile(r"^\s*(?:portions\s+)?(?:\(c\)\s*)?copyright\b(.*)$", re.IGNORECASE)
_SYMBOL_START = re.compile(r"^\s*(?:\(c\)\s*\d|\u00a9)", re.IGNORECASE)
# What follows "COPYRIGHT" in the text of a license:
# "COPYRIGHT HOLDERS BE LIABLE", "copyright law".
_LEGALESE = re.compile(
    r"^(?:holders?|owners?|notices?|and|in|law|laws|statement|license|is|or|to|ownership"
    r"|[-\u2013\u2014])\b",
    re.IGNORECASE,
)
# A field to fill in, in the boilerplate at the end of the Apache license:
# "Copyright [yyyy] [name]".
_PLACEHOLDER = re.compile(
    r"\[[^\]]*\]|<year>|<copyright holders?>|<owner>|\{[^}]*\}|\byyyy\b|<name of author>",
    re.IGNORECASE,
)


def is_notice(line: str) -> bool:
    """A line of a license file that states who holds the copyright."""
    if _PLACEHOLDER.search(line):
        return False
    m = _NOTICE_START.match(line)
    if m is None:
        return bool(_SYMBOL_START.match(line))
    rest = m.group(1).lstrip()
    return not _LEGALESE.match(rest) and not rest[:1].islower()


def license_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and _LICENSE_FILE.match(p.name))


def copyright_lines(directory: Path | None) -> list[str]:
    """The copyright notices in the license files of a package, in file order, without repeats."""
    if directory is None:
        return []
    found: list[str] = []
    for path in license_files(directory):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip().lstrip("*#/ ").strip()
            if line and is_notice(line) and line not in found:
                found.append(line)
    return found


# --------------------------------------------------------------------------------------------
# One entry
# --------------------------------------------------------------------------------------------


def source_line(pkg: Pkg) -> str:
    url = pkg.repository.rstrip("/")
    url = url.removesuffix(".git")
    return f"Source:  {url or f'https://crates.io/crates/{pkg.name}/{pkg.version}'}"


def copyright_block(pkg: Pkg, found: Sequence[str]) -> list[str]:
    if found:
        return [f"  {line}" for line in found]
    if pkg.authors:
        return ["  Copyright holders / authors (from package metadata):"] + [
            f"    - {author}" for author in pkg.authors
        ]
    return ["  Copyright (c) the package authors. See the package repository for details."]


def render_entry(
    pkg: Pkg, policies: Mapping[str, Policy], found: Sequence[str]
) -> tuple[Entry, bool]:
    """The entry of ``pkg`` and whether a person has to read it (no policy for its license)."""
    policy = policy_for(pkg.license, policies)
    source = source_line(pkg)
    where = source.removeprefix("Source:").strip()
    if policy is None:
        license_lines: Sequence[str] = [f"License: {pkg.license or 'unknown'}"]
        text_line = f"License text: see upstream {where} (license: {pkg.license or 'unknown'})"
        notes: Sequence[str] = ()
    else:
        license_lines, notes = policy.license_lines, policy.notes
        text_line = policy.text_line.replace(SOURCE_SLOT, where)
    lines = [
        SEP,
        f"{pkg.name} {pkg.version}",
        SEP,
        source,
        *license_lines,
        "",
        "Copyright notice:",
        *copyright_block(pkg, found),
        "",
        text_line,
    ]
    if notes:
        lines += ["", "Additional requirements / notices:", *notes]
    lines.append("")
    return Entry(pkg.key, lines), policy is None


# --------------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------------


@dataclass
class Plan:
    missing: list[Key]
    stale: list[Key]


def make_plan(notices: Notices, shipped: Mapping[Key, Pkg]) -> Plan:
    have = {e.key for e in notices.entries}
    return Plan(
        missing=sorted(set(shipped) - have, key=sort_key),
        stale=sorted(have - set(shipped), key=sort_key),
    )


def _fmt(keys: Iterable[Key]) -> str:
    return ", ".join(f"{n} {v}" for n, v in keys) or "none"


def apply_plan(
    notices: Notices, plan: Plan, shipped: Mapping[Key, Pkg]
) -> tuple[list[Key], list[Key]]:
    """Change ``notices`` in place. Returns (added, needs review)."""
    policies = learn_policies(notices.entries)  # before the stale entries go: they teach too
    stale = set(plan.stale)
    notices.entries = [e for e in notices.entries if e.key not in stale]
    added: list[Key] = []
    review: list[Key] = []
    for key in plan.missing:
        pkg = shipped[key]
        entry, needs_review = render_entry(pkg, policies, copyright_lines(pkg.directory))
        insert_entry(notices, entry)
        added.append(key)
        if needs_review:
            review.append(key)
    return added, review


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tui-dir", type=Path, default=DEFAULT_TUI)
    parser.add_argument("--root", default=DEFAULT_ROOT, help="the product package")
    # `action="append"`, not `nargs="+"`: a list option would take the sub-command as a value.
    parser.add_argument(
        "--target",
        action="append",
        metavar="TRIPLE",
        help="a target triple to take the closure for (repeat it). Default: DEFAULT_TARGETS",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        type=Path,
        metavar="FILE",
        help="a saved `cargo metadata` dump, one per target (repeat it)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="exit 1 when an entry is missing or stale")
    p_update = sub.add_parser("update", help="remove stale entries, add missing ones")
    p_update.add_argument("--write", action="store_true", help="write the file (default: dry run)")
    args = parser.parse_args(argv)

    path = args.tui_dir / "THIRD-PARTY-NOTICES"
    notices = parse_notices(path.read_text(encoding="utf-8"))
    shipped = collect(args.tui_dir, args.root, args.target or DEFAULT_TARGETS, args.metadata or [])
    plan = make_plan(notices, shipped)
    print(f"{len(shipped)} packages ship; {len(notices.entries)} entries in {path.name}")
    print(f"missing ({len(plan.missing)}): {_fmt(plan.missing)}")
    print(f"stale ({len(plan.stale)}): {_fmt(plan.stale)}")

    if args.command == "check":
        return 1 if plan.missing or plan.stale else 0

    added, review = apply_plan(notices, plan, shipped)
    print(
        f"{'wrote' if args.write else 'would write'}: {len(plan.stale)} removed, {len(added)} added"
    )
    if review:
        print(f"needs review ({len(review)}): {_fmt(review)}")
    if args.write:
        path.write_text(notices.render(), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
