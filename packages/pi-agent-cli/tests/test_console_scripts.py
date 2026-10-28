"""The package has a console script with its own name, so `uvx pi-agent-cli-lc` works.

`uvx <package>` runs the executable that has the name of the package, and it stops with an error
when the package has none (uv 0.7 and later). The ACP registry starts a PyPI agent only in this
form, `uvx <package> <args>`: it cannot pass `--from`. The old name `pi-agent-cli` stays for the
people who use it.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
ENTRY_POINT = "pi_agent_cli.__main__:main"


def _project() -> dict[str, Any]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_a_script_has_the_name_of_the_package() -> None:
    project = _project()
    assert project["scripts"].get(project["name"]) == ENTRY_POINT


def test_the_old_script_name_stays() -> None:
    assert _project()["scripts"].get("pi-agent-cli") == ENTRY_POINT


def test_the_entry_point_can_be_imported() -> None:
    module_name, _, function_name = ENTRY_POINT.partition(":")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, function_name))
