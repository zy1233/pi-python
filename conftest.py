"""Keeps the developer's own environment out of the tests.

``PI_HOME`` moves every pi-python user-level file (config, extensions, sessions, workflows).
Tests build their own homes, mostly by pointing ``Path.home()`` at a temporary directory, and
must not find the real one instead: a shell that exports ``PI_HOME`` (a WSL shell sharing the
Windows home, say) made four tests fail on Linux while they passed on a clean machine. A test
that needs ``PI_HOME`` sets it itself; that happens after this fixture has run.

``PI_TRUST_PROJECT_EXTENSIONS`` is the same kind of leak: a developer who exports it to run
their own projects headlessly would find every "the project is not trusted" test passing or
failing depending on the shell.
"""

from __future__ import annotations

import pytest

_AMBIENT = ("PI_HOME", "PI_TRUST_PROJECT_EXTENSIONS")


@pytest.fixture(autouse=True)
def _no_ambient_pi_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AMBIENT:
        monkeypatch.delenv(name, raising=False)
