"""The build of the agent program installs the versions in ``packaging/agent/constraints.txt``.

Without pins, a build takes the newest version of every dependency on the day of the build
(LangChain, OpenAI, pydantic, ``httpx``, ``mcp``). Nobody tested that mix, and the same commit gives
a different program on a different day. ``docs/specs/2026-10-31-agent-binaries.md`` (section 2.3)
has the reason and the way to make the pins again.

These tests install and resolve nothing (they need no network). They check that the files agree:

* the pins are exact (``name==version``), and none is a package of this repository;
* the ``build`` job installs with the file, and its list of packages is the list of
  ``constraints.in`` (the input that makes the file);
* every direct dependency of the installed packages has a pin, so a new dependency does not float;
* each pin is inside the bounds of the ``pyproject.toml`` files;
* the LangChain packages that the adapters subclass have an upper bound.

When a test here fails because a dependency was added or a bound was changed, make the pins again
(the command is in ``constraints.in``), run ``pytest`` and ``packaging/agent/smoke.py`` with them,
and commit the new ``constraints.txt``.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "packaging" / "agent"
CONSTRAINTS_IN = AGENT_DIR / "constraints.in"
CONSTRAINTS_TXT = AGENT_DIR / "constraints.txt"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-binaries.yml"
INSTALL_STEP = "Install the packages and PyInstaller"

# The pins come from one resolution for Python 3.12 (the ``setup-python`` of the build job). This
# makes a marker like ``python_version < '3.12'`` false here, whatever Python runs the test.
BUILD_ENVIRONMENT = {"python_version": "3.12", "python_full_version": "3.12.0", "extra": ""}

# The LangChain packages whose classes the adapters subclass or call (``pi_agent_core/adapters/``).
# Each needs an upper bound: ``test_langchain_private_api.py`` explains why.
LANGCHAIN_BOUNDED = {
    "langchain-core",
    "langchain-openai",
    "langchain-anthropic",
    "langchain-deepseek",
}
UPPER_BOUND_OPERATORS = {"<", "<=", "==", "~="}

pytestmark = pytest.mark.skipif(
    not ((AGENT_DIR / "build.py").is_file() and WORKFLOW.is_file()),
    reason="needs the monorepo checkout (packaging/ and .github/ are not in an installed wheel)",
)


# ---- readers -------------------------------------------------------------------------------


def _lines(path: Path) -> list[str]:
    """The lines of a requirements file that are not empty and not a comment."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _pins() -> list[Requirement]:
    pins = []
    for line in _lines(CONSTRAINTS_TXT):
        try:
            pins.append(Requirement(line))
        except InvalidRequirement as exc:
            pytest.fail(f"constraints.txt: this line is not a requirement: {line!r} ({exc})")
    return pins


def _pin_version(pin: Requirement) -> str:
    (clause,) = pin.specifier
    return clause.version


def _project_files() -> list[Path]:
    return [
        REPO_ROOT / "pyproject.toml",
        *sorted((REPO_ROOT / "packages").glob("*/pyproject.toml")),
    ]


def _projects() -> dict[str, dict[str, Any]]:
    """The ``[project]`` table of each package of this repository, by normalized name."""
    projects = {}
    for path in _project_files():
        table = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        projects[canonicalize_name(table["name"])] = table
    return projects


def _declared(table: dict[str, Any]) -> Iterator[Requirement]:
    """Every requirement that a project declares: its dependencies and all its extras."""
    for text in table.get("dependencies", []):
        yield Requirement(text)
    for texts in table.get("optional-dependencies", {}).values():
        for text in texts:
            yield Requirement(text)


def _applies(requirement: Requirement) -> bool:
    return requirement.marker is None or requirement.marker.evaluate(BUILD_ENVIRONMENT)


def _installed_projects() -> dict[str, dict[str, Any]]:
    """The packages of this repository that the build installs (the paths in ``constraints.in``)."""
    projects = _projects()
    installed = {}
    for line in _lines(CONSTRAINTS_IN):
        if not line.startswith("."):
            continue
        manifest = REPO_ROOT / line / "pyproject.toml"
        assert manifest.is_file(), f"constraints.in: `{line}` has no pyproject.toml"
        name = canonicalize_name(
            tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["name"]
        )
        installed[name] = projects[name]
    return installed


def _direct_requirements(installed: dict[str, dict[str, Any]]) -> Iterator[Requirement]:
    """What the build asks for from the index: the dependencies of the installed packages.

    A dependency on another package of this repository with extras, like
    ``pi-agent-core-lc[openai]``, brings the requirements of those extras.
    """
    projects = _projects()
    for table in installed.values():
        for text in table.get("dependencies", []):
            requirement = Requirement(text)
            if not _applies(requirement):
                continue
            name = canonicalize_name(requirement.name)
            if name not in projects:
                yield requirement
                continue
            for extra in requirement.extras:
                for extra_text in projects[name].get("optional-dependencies", {}).get(extra, []):
                    extra_requirement = Requirement(extra_text)
                    if _applies(extra_requirement):
                        yield extra_requirement


def _install_command() -> list[str]:
    """The words of the ``pip install`` command in the install step of the workflow."""
    text = WORKFLOW.read_text(encoding="utf-8")
    steps = list(re.finditer(r"^ *- name: (?P<name>.+)$", text, re.MULTILINE))
    starts = [m for m in steps if m.group("name").strip() == INSTALL_STEP]
    assert len(starts) == 1, (
        f"agent-binaries.yml must have one step named {INSTALL_STEP!r}, it has {len(starts)}"
    )
    begin = starts[0].end()
    following = [m.start() for m in steps if m.start() > begin]
    step_text = text[begin : following[0] if following else len(text)]
    run = re.search(
        r"^(?P<indent> *)run: *>-?[ \t]*\n(?P<body>(?:(?P=indent) +\S.*\n?)+)",
        step_text,
        re.MULTILINE,
    )
    assert run, f"the step {INSTALL_STEP!r} has no `run: >-` block that this test can read"
    return shlex.split(" ".join(line.strip() for line in run.group("body").splitlines()))


# ---- the pins ------------------------------------------------------------------------------


def test_every_pin_is_one_exact_version():
    pins = _pins()
    assert pins, "constraints.txt has no pins"
    for pin in pins:
        clauses = list(pin.specifier)
        assert len(clauses) == 1 and clauses[0].operator == "==", (
            f"constraints.txt: `{pin}` is not an exact pin (name==version)"
        )
        assert "*" not in clauses[0].version, f"constraints.txt: `{pin}` has a wildcard"
        assert not pin.extras, f"constraints.txt: `{pin}` has extras; a constraint cannot have them"
        assert pin.url is None, f"constraints.txt: `{pin}` is a URL, not a pin"


def test_no_pin_is_a_package_of_this_repository():
    """pip would refuse to install a local package whose version is not the pinned one."""
    local = set(_projects())
    pinned_local = sorted(pin.name for pin in _pins() if canonicalize_name(pin.name) in local)
    assert not pinned_local, (
        f"constraints.txt pins packages of this repository: {pinned_local}. "
        "Make the file again with `--no-emit-package` for each of them (see constraints.in)."
    )


def test_every_direct_dependency_of_the_installed_packages_has_a_pin():
    pinned = {canonicalize_name(pin.name) for pin in _pins()}
    missing = sorted(
        {
            canonicalize_name(requirement.name)
            for requirement in _direct_requirements(_installed_projects())
            if canonicalize_name(requirement.name) not in pinned
        }
    )
    assert not missing, (
        f"These dependencies of the packages that the build installs have no pin: {missing}. "
        "A dependency with no pin takes the newest version on the day of the build. "
        "Make constraints.txt again (the command is in constraints.in)."
    )


def test_pyinstaller_is_pinned():
    """The build tool is part of the program too: a new PyInstaller changes the binary."""
    assert "pyinstaller" in {canonicalize_name(pin.name) for pin in _pins()}


def test_every_pin_is_inside_the_bounds_of_the_pyproject_files():
    pins: dict[str, list[Requirement]] = {}
    for pin in _pins():
        pins.setdefault(canonicalize_name(pin.name), []).append(pin)

    outside = []
    for path in _project_files():
        table = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        for requirement in _declared(table):
            if not _applies(requirement):
                continue
            for pin in pins.get(canonicalize_name(requirement.name), []):
                if not requirement.specifier.contains(_pin_version(pin), prereleases=True):
                    outside.append(
                        f"{pin} is outside `{requirement}` ({path.relative_to(REPO_ROOT)})"
                    )
    assert not outside, (
        "A pin is outside a bound. Change the bound on purpose, or make the pins again:\n"
        + "\n".join(outside)
    )


# ---- the workflow --------------------------------------------------------------------------


def test_build_job_installs_with_the_constraints_file():
    command = _install_command()
    assert command[:2] == ["pip", "install"], f"unexpected install command: {command}"
    assert "-c" in command or "--constraint" in command, (
        "the install step of agent-binaries.yml has no `-c packaging/agent/constraints.txt`: "
        "the binary would use the newest dependencies of the day"
    )
    flag = "-c" if "-c" in command else "--constraint"
    path = command[command.index(flag) + 1]
    assert (REPO_ROOT / path).resolve() == CONSTRAINTS_TXT.resolve(), (
        f"the install step uses `{path}`, not packaging/agent/constraints.txt"
    )


def test_install_step_and_constraints_in_ask_for_the_same_packages():
    command = _install_command()
    requested = []
    skip_next = False
    for word in command[2:]:
        if skip_next:
            skip_next = False
        elif word in {"-c", "--constraint"}:
            skip_next = True
        elif not word.startswith("-"):
            requested.append(word)
    assert sorted(requested) == sorted(_lines(CONSTRAINTS_IN)), (
        "The packages of the install step in agent-binaries.yml and the lines of "
        "packaging/agent/constraints.in must be the same list. Change both together, "
        "then make constraints.txt again."
    )


# ---- the bounds ----------------------------------------------------------------------------


def test_langchain_packages_that_the_adapters_use_have_an_upper_bound():
    unbounded = []
    for path in _project_files():
        table = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        for requirement in _declared(table):
            if canonicalize_name(requirement.name) not in LANGCHAIN_BOUNDED:
                continue
            if not any(
                clause.operator in UPPER_BOUND_OPERATORS for clause in requirement.specifier
            ):
                unbounded.append(f"`{requirement}` in {path.relative_to(REPO_ROOT)}")
    assert not unbounded, (
        "These requirements have no upper bound, but the adapters override private methods of "
        "these packages (pi_agent_core/adapters/): " + ", ".join(unbounded) + ". "
        "Add `<N` for the next major version."
    )
