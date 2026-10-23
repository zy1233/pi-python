"""Fixtures of the pi-mcp tests.

These tests need the packages of the extension (``pi_mcp`` and what it uses). A checkout that did
not install them, as the pre-release job of CI does not, collects none of the tests; the header of
the run says so. CI installs ``packages/pi-mcp``, so there the tests always run.
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from mcp_support import Lab

_NEEDED = ("mcp", "langchain_mcp_adapters", "pi_mcp")
_MISSING = [name for name in _NEEDED if importlib.util.find_spec(name) is None]

collect_ignore_glob = ["test_*.py"] if _MISSING else []


def pytest_report_header(config: pytest.Config) -> str | None:
    if _MISSING:
        return f"pi-mcp tests are not collected: not installed: {', '.join(_MISSING)}"
    return None


@pytest.fixture(autouse=True)
def _plain_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """What the machine of the test run has set does not change what the tests see.

    ``PI_HOME`` points into the test directory: an extension keeps its files (logs) there, and
    a test must not write into the real home of the user.
    """
    for name in ("PI_MCP_STARTUP_TIMEOUT", "PI_MCP_CALL_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PI_HOME", str(tmp_path / "pi-home"))


@pytest.fixture()
async def lab(tmp_path: Path) -> AsyncIterator[Lab]:
    from mcp_support import Lab

    laboratory = Lab(tmp_path)
    try:
        yield laboratory
    finally:
        await laboratory.aclose()
