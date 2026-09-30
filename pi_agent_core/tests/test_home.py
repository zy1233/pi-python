"""Where pi-python keeps its user-level files (audit P7-13)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pi_agent_core.home import HOME_ENV, pi_home


@pytest.fixture()
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.delenv(HOME_ENV, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_default_is_dot_pi_python_in_the_user_home(user_home: Path):
    assert pi_home() == user_home / ".pi-python"


def test_the_environment_replaces_the_default(
    user_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "elsewhere"))

    assert pi_home() == tmp_path / "elsewhere"


def test_an_explicit_override_beats_the_environment(
    user_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "from-env"))

    assert pi_home(tmp_path / "explicit") == tmp_path / "explicit"
    assert pi_home(str(tmp_path / "as-text")) == tmp_path / "as-text"


def test_a_tilde_is_expanded(user_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(HOME_ENV, "~/state")

    assert pi_home() == user_home / "state"
    assert pi_home("~/other") == user_home / "other"


def test_an_empty_environment_value_is_ignored(user_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(HOME_ENV, "")

    assert pi_home() == user_home / ".pi-python"


def test_a_test_starts_without_pi_home():
    """The probe of the test below; on its own it only says the variable is not exported."""
    assert HOME_ENV not in os.environ


def test_a_pi_home_exported_by_the_shell_does_not_reach_the_tests():
    """``conftest.py`` clears it. Tests that point ``Path.home()`` at a temporary directory
    (extension trust, notices) failed on a machine that exports it, such as a WSL shell that
    shares the Windows home."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"{Path(__file__).resolve()}::test_a_test_starts_without_pi_home",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        env={**os.environ, HOME_ENV: "/somewhere/the/shell/exported"},
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
