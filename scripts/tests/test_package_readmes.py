"""The README of a package is its page on PyPI, where a relative link does not work.

PyPI shows the file that ``readme`` names in ``pyproject.toml`` and nothing around it, so a link
such as ``../../docs/specs/x.md`` points nowhere there. 1.0.0 shipped three of them. Every link in
these files has to be absolute (or an anchor in the same page).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECTS = [
    REPO_ROOT / "pyproject.toml",
    *sorted((REPO_ROOT / "packages").glob("*/pyproject.toml")),
]

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "packages").is_dir(),
    reason="needs the monorepo checkout (packages/ is not part of an installed wheel)",
)

_INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)")
_REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+)", re.MULTILINE)
_ALLOWED = ("http://", "https://", "mailto:", "#")


def _readme(pyproject: Path) -> Path:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return pyproject.parent / data["project"]["readme"]


def _targets(text: str) -> list[str]:
    without_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    without_code = re.sub(r"`[^`\n]*`", "", without_code)
    return _INLINE_LINK.findall(without_code) + _REFERENCE_LINK.findall(without_code)


@pytest.mark.parametrize("pyproject", PYPROJECTS, ids=lambda p: p.parent.name or "root")
def test_readme_of_a_package_has_only_absolute_links(pyproject: Path):
    readme = _readme(pyproject)
    assert readme.is_file(), f"{readme} is named as the readme but is not there"

    relative = [
        t for t in _targets(readme.read_text(encoding="utf-8")) if not t.startswith(_ALLOWED)
    ]

    assert relative == [], (
        f"{readme.relative_to(REPO_ROOT)} has links that PyPI cannot follow: {relative}. "
        "Use the full https://github.com/zy1233/zycode/blob/main/... address."
    )


def test_the_check_finds_a_relative_link():
    text = "See [spec](../../docs/a.md), ![i](img/x.png), [ok](https://x.y/z) and [up](#top).\n"
    assert _targets(text) == ["../../docs/a.md", "img/x.png", "https://x.y/z", "#top"]
