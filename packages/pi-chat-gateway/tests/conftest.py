"""Fixtures of the chat gateway tests.

The tests need the package (``pi_chat_gateway``) and the agent (``pi_agent_cli``). A checkout that
did not install them collects none of the tests; the header of the run says so. The Feishu adapter
tests also need ``lark_oapi`` and skip without it (``test_feishu.py``).

The integration tests start real agents over ACP stdio: ``python -m pi_agent_cli`` with the mock
LLM, and ``packages/pi-agent-cli/tests/_tool_agent.py``, whose first turn calls one tool. Nothing
goes to the network.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_NEEDED = ("pi_chat_gateway", "pi_agent_cli")
_MISSING = [name for name in _NEEDED if importlib.util.find_spec(name) is None]

collect_ignore_glob = ["test_*.py"] if _MISSING else []

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_AGENT = REPO_ROOT / "packages" / "pi-agent-cli" / "tests" / "_tool_agent.py"


def pytest_report_header(config: pytest.Config) -> str | None:
    if _MISSING:
        return f"pi-chat-gateway tests are not collected: not installed: {', '.join(_MISSING)}"
    return None


@pytest.fixture(autouse=True)
def _plain_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The home of the test run is in the test directory, never the user's real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PI_HOME", str(home))
    for name in ("PI_USE_MOCK", "PI_TEST_TOOL_CALL", "PI_TEST_PERMISSION", "PI_TEST_TOOL_PIDFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return Path(os.environ["PI_HOME"])


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def mock_agent_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "pi_agent_cli")


def tool_agent_command() -> tuple[str, ...]:
    if not TOOL_AGENT.is_file():
        pytest.skip("needs the monorepo checkout (packages/pi-agent-cli/tests/_tool_agent.py)")
    return (sys.executable, str(TOOL_AGENT))
