"""Packaging of the Phase 7 extension distributions (audit P7-15).

The extension packages live in this repository but ship as separate wheels, so an installation
resolves them against whatever ``pi-agent-core-lc`` is already there. The workspace always installs
the checkout, which hides two mistakes from every other test:

* importing a distribution the package never declared (``pi-dynamic-workflows-py`` imports
  ``pi_agent_harness`` for its sub-agents but did not depend on it, so a plain ``pip install`` gave
  a workflow tool that could only say "sub-agents unavailable");
* declaring a floor older than the API the code calls (``pi_agent_core.extensions`` does not exist
  before 0.4.0, yet all three declared ``>=0.3.0``).

The extensions are released together with the core, so their floor is the workspace version: bumping
the release without raising the floors fails here, in CI, instead of after the wheels are published.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"
EXTENSIONS = ("pi-web-access", "pi-goal-x", "pi-dynamic-workflows")

pytestmark = pytest.mark.skipif(
    not all((PACKAGES / name / "pyproject.toml").is_file() for name in EXTENSIONS),
    reason="needs the monorepo checkout (packages/ is not part of the installed wheel)",
)


class Distribution(NamedTuple):
    name: str  # canonical distribution name
    version: Version
    imports: tuple[str, ...]  # top-level modules the wheel provides


def _pyproject(directory: Path) -> dict[str, Any]:
    return tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))


def _distribution(directory: Path) -> Distribution:
    meta = _pyproject(directory)
    wheel = meta["tool"]["hatch"]["build"]["targets"]["wheel"]
    return Distribution(
        canonicalize_name(meta["project"]["name"]),
        Version(meta["project"]["version"]),
        tuple(wheel["packages"]),
    )


def _workspace() -> dict[str, Distribution]:
    """Every distribution of the repository, by canonical name."""
    directories = [ROOT, *sorted(p for p in PACKAGES.iterdir() if (p / "pyproject.toml").is_file())]
    found = [_distribution(directory) for directory in directories]
    return {dist.name: dist for dist in found}


def _imported_modules(package_dir: Path) -> set[str]:
    """Top-level modules imported anywhere in the package, lazy imports included."""
    imported: set[str] = set()
    for source in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    return imported


def _dependencies(directory: Path) -> dict[str, Requirement]:
    meta = _pyproject(directory)
    requirements = (Requirement(text) for text in meta["project"].get("dependencies", []))
    return {canonicalize_name(requirement.name): requirement for requirement in requirements}


def _import_dir(directory: Path) -> Path:
    return directory / _distribution(directory).imports[0]


@pytest.mark.parametrize("package", EXTENSIONS)
def test_every_workspace_distribution_the_code_imports_is_declared(package: str):
    directory = PACKAGES / package
    declared = _dependencies(directory)
    provider = {module: dist.name for dist in _workspace().values() for module in dist.imports}
    own = set(_distribution(directory).imports)

    needed = {provider[m] for m in _imported_modules(_import_dir(directory)) - own if m in provider}

    assert needed <= declared.keys(), (
        f"{package} imports {sorted(needed - declared.keys())} without declaring it in "
        f"[project] dependencies; a plain `pip install` would not bring it in"
    )


@pytest.mark.parametrize("package", EXTENSIONS)
def test_the_floor_of_every_workspace_dependency_is_the_release_it_ships_with(package: str):
    workspace = _workspace()
    directory = PACKAGES / package
    declared = _dependencies(directory)
    ours = {name: req for name, req in declared.items() if name in workspace}
    assert ours, "an extension must depend on the core it extends"

    for name, requirement in ours.items():
        floors = [Version(s.version) for s in requirement.specifier if s.operator == ">="]
        assert floors == [workspace[name].version], (
            f"{package} requires {requirement}, but the checkout is {name} "
            f"{workspace[name].version}: raise the floor to `>={workspace[name].version}` "
            f"(the extensions are released together with the core)"
        )


@pytest.mark.parametrize("package", EXTENSIONS)
def test_workspace_dependencies_resolve_to_the_checkout_in_uv(package: str):
    directory = PACKAGES / package
    sources = _pyproject(directory).get("tool", {}).get("uv", {}).get("sources", {})
    workspace = _workspace()

    for name in _dependencies(directory).keys() & workspace.keys():
        assert sources.get(name) == {"workspace": True}, (
            f"{package} needs `[tool.uv.sources] {name} = {{ workspace = true }}`, otherwise "
            f"`uv sync` resolves it from the index instead of this checkout"
        )
