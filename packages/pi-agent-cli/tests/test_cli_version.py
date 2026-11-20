"""``python -m pi_agent_cli --version``: one line, ``<name> <version>``, exit status 0.

The line comes from the object that fills ``agentInfo`` in ``initialize``, so the two cannot
differ. The flag must not read the home (no ``agent.toml``, no ``local.env``), start the first-run
setup or open the network, so these tests run it in a subprocess with a home that holds a broken
``agent.toml``: if the flag read it, the run would fail or write a file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pi_agent_cli.agent import _AGENT_INFO

_BROKEN_TOML = "this is [[[ not toml\n"


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PI_HOME": str(home), "PATH": "", "PYTHONIOENCODING": "utf-8"}
    # SystemRoot and similar variables are needed to start Python on Windows; they carry no config.
    for name in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP"):
        if name in os.environ:
            env[name] = os.environ[name]
    return subprocess.run(
        [sys.executable, "-m", "pi_agent_cli", *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def test_version_prints_the_agent_info_name_and_version(tmp_path: Path):
    result = _run(tmp_path, "--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{_AGENT_INFO.name} {_AGENT_INFO.version}"]
    assert result.stderr == ""


def test_version_does_not_touch_the_home(tmp_path: Path):
    config = tmp_path / "agent.toml"
    config.write_text(_BROKEN_TOML, encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    result = _run(tmp_path, "--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{_AGENT_INFO.name} {_AGENT_INFO.version}"
    assert config.read_text(encoding="utf-8") == _BROKEN_TOML
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_version_cannot_be_combined_with_a_prompt(tmp_path: Path):
    result = _run(tmp_path, "--version", "-p", "hi")

    assert result.returncode == 2
    assert result.stdout == ""
