"""Run the sandbox tests against the frozen script process when asked to.

A frozen build starts the script's process from a program of its own (``child.py`` frozen
alone), not from the interpreter. ``PI_SANDBOX_FROZEN_CHILD=<path of that program>`` makes
every test in this folder use it, as a frozen build would, so that the whole suite checks
the program on the platform that built it (the build workflow does this on each platform).
Without the variable nothing changes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pi_dynamic_workflows.sandbox import host

FROZEN_CHILD_ENV = "PI_SANDBOX_FROZEN_CHILD"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "interpreter_child: replaces the interpreter or child.py of the script process, so it "
        "cannot run against a frozen script process",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not os.environ.get(FROZEN_CHILD_ENV):
        return
    skip = pytest.mark.skip(reason="it replaces the interpreter that a frozen build does not use")
    for item in items:
        if "interpreter_child" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _frozen_script_process(monkeypatch: pytest.MonkeyPatch) -> None:
    path = os.environ.get(FROZEN_CHILD_ENV)
    if not path:
        return
    program = Path(path)
    assert program.is_file(), f"{FROZEN_CHILD_ENV} names {program}, which is not a file"
    monkeypatch.setattr(host, "_is_frozen", lambda: True)
    monkeypatch.setattr(host, "frozen_child_executable", lambda: program)
