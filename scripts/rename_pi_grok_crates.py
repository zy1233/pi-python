#!/usr/bin/env python3
"""One-shot rename pi-grok-* crates to pi-* under tui/. Run from repo root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TUI = ROOT / "tui"

# Text replacements (order matters for overlapping patterns).
REPLACEMENTS = [
    ("pi-grok-", "pi-"),
    ("pi_grok_", "pi_"),
]


def rename_dirs() -> None:
    dirs: list[Path] = []
    for base in (TUI / "crates" / "codegen", TUI / "crates" / "common"):
        if base.is_dir():
            dirs.extend(sorted(base.glob("pi-grok-*"), key=lambda p: len(p.name), reverse=True))
    for old in dirs:
        new_name = old.name.replace("pi-grok-", "pi-", 1)
        new = old.parent / new_name
        if new.exists():
            print(f"skip exists: {new}")
            continue
        print(f"git mv {old.relative_to(ROOT)} -> {new.relative_to(ROOT)}")
        subprocess.run(["git", "mv", str(old), str(new)], cwd=ROOT, check=True)


def patch_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    orig = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    if text != orig:
        path.write_text(text, encoding="utf-8", newline="\n")
        return True
    return False


def patch_tree() -> int:
    count = 0
    for path in TUI.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {
            ".rs",
            ".toml",
            ".md",
            ".bzl",
            ".json",
            ".yaml",
            ".yml",
            ".sh",
            ".mdc",
        } and path.name not in {"Cargo.lock", "clippy.toml", "rust-toolchain.toml"}:
            continue
        if "target" in path.parts:
            continue
        if patch_file(path):
            count += 1
            print(f"patched {path.relative_to(ROOT)}")
    # docs outside tui that reference crate names
    for rel in (
        "docs/WINDOWS.md",
        "docs/DESIGN.md",
        "docs/AUDIT/SPIKE-P0-GROK-TUI.md",
        "docs/specs/2026-08-25-phase4-coding-agent-cli-design.md",
        "AGENTS.md",
        "README.md",
        "CHANGELOG.md",
    ):
        p = ROOT / rel
        if p.is_file() and patch_file(p):
            count += 1
            print(f"patched {rel}")
    return count


def main() -> int:
    if "--dirs-only" in sys.argv:
        rename_dirs()
        return 0
    if "--text-only" in sys.argv:
        patch_tree()
        return 0
    rename_dirs()
    n = patch_tree()
    print(f"patched {n} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
