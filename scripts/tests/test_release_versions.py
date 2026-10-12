"""The version numbers that name the release stay in step (plan decision 16).

``zypi --version`` and the ACP ``agentInfo.version`` must show the version of the release. The TUI
crates kept the version of the upstream project (1.0.8) and the agent said 0.1.0, so a user could
not tell which release a binary came from. The release version is the version of
``pi-agent-cli-lc``, the package that ``zypi`` starts. The two crates whose version ``zypi`` shows,
and ``Cargo.lock``, must carry it: raise them together with the Python packages at each release.

No workflow may set ``GROK_VERSION``. The same variable marks a build as a release build
(``pi_version::IS_DEV_BUILD``), and a release build turns the folder-trust gate on. An unstamped
build, like v0.3.0 and v0.4.0, has it off. To turn it on is a change of behaviour that needs its
own decision. The version number comes from ``Cargo.toml`` instead.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_PACKAGE = REPO_ROOT / "packages" / "pi-agent-cli" / "pyproject.toml"
CARGO_LOCK = REPO_ROOT / "tui" / "Cargo.lock"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# The crates whose version zypi prints: `pi-pager-bin` for `zypi --version`, `pi-version` for
# `pi_version::VERSION` (the welcome page, the update and config version checks).
TUI_CRATES = {
    "pi-pager-bin": REPO_ROOT / "tui" / "crates" / "codegen" / "pi-pager-bin" / "Cargo.toml",
    "pi-version": REPO_ROOT / "tui" / "crates" / "codegen" / "pi-version" / "Cargo.toml",
}

pytestmark = pytest.mark.skipif(
    not (CARGO_LOCK.is_file() and RELEASE_PACKAGE.is_file()),
    reason="needs the monorepo checkout (tui/ and packages/ are not part of an installed wheel)",
)


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _release_version() -> str:
    return _toml(RELEASE_PACKAGE)["project"]["version"]


@pytest.mark.parametrize("crate", sorted(TUI_CRATES))
def test_tui_crate_version_is_the_release_version(crate: str):
    manifest = TUI_CRATES[crate]
    package = _toml(manifest)["package"]

    assert package["name"] == crate
    assert package["version"] == _release_version(), (
        f"`{crate}` is {package['version']} but pi-agent-cli-lc is {_release_version()}: "
        "`zypi` would show the wrong release. "
        f"Set `version` in {manifest.relative_to(REPO_ROOT)} and in tui/Cargo.lock "
        f"to {_release_version()}."
    )


def test_cargo_lock_carries_the_release_version_of_those_crates():
    locked = {
        entry["name"]: entry["version"]
        for entry in _toml(CARGO_LOCK)["package"]
        if entry["name"] in TUI_CRATES
    }

    assert locked == {crate: _release_version() for crate in TUI_CRATES}


def test_no_workflow_sets_grok_version():
    setting = [
        path.name
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
        if "GROK_VERSION" in path.read_text(encoding="utf-8")
    ]

    assert setting == [], (
        f"{setting} set GROK_VERSION. That stamps the version, and it also marks the build as a "
        "release build, which turns on folder trust (plan decision 16). Change the version in "
        "Cargo.toml instead."
    )
