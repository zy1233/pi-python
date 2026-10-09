"""Where zypi and its agent are, and a throw-away home to run them in.

The environment handed to zypi is built from nothing (``base_env``): whatever the calling shell
exports (``PI_HOME``, ``PI_AGENT_COMMAND``, ``NO_COLOR``, ...) never reaches a run, so a profile
that points ``PI_HOME`` at a real home cannot make one of these scripts write there.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT_TESTS = REPO / "packages" / "pi-agent-cli" / "tests"


def find_zypi(explicit: str | None = None) -> str:
    """The zypi binary: ``--zypi``, then ``$ZYPI``, then ``tui/target/{debug,release}/zypi``.

    Cargo writes elsewhere when ``CARGO_TARGET_DIR`` is set (CI and WSL setups do that), so say
    where the binary is rather than hope for the default.
    """
    candidates = [explicit, os.environ.get("ZYPI")]
    for profile in ("debug", "release"):
        candidates.append(str(REPO / "tui" / "target" / profile / "zypi"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise SystemExit(
        "zypi binary not found: pass --zypi PATH or set ZYPI "
        "(build it with `cd tui && cargo build -p pi-pager-bin`)"
    )


def agent_python(explicit: str | None = None) -> str:
    """The interpreter with ``pi_agent_cli``: ``--python``, then ``$PI_PYTHON``, then this one."""
    return explicit or os.environ.get("PI_PYTHON") or sys.executable


def scratch(prefix: str) -> Path:
    """A temp dir with ``home`` (``PI_HOME``), ``user`` (``HOME``) and ``work`` (the cwd)."""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    for sub in ("home", "user", "work"):
        (root / sub).mkdir()
    return root


def base_env(root: Path, **extra: str) -> dict[str, str]:
    """The environment of a run in ``root``: only what the TUI needs, plus ``extra``."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(root / "user"),
        "LANG": "C.UTF-8",
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "PI_HOME": str(root / "home"),
    }
    env.update(extra)
    return env


def marker(root: Path) -> str:
    """What ``pty_term.processes_with_env`` looks for: the ``PI_HOME`` of this run."""
    return f"PI_HOME={root / 'home'}"


def remove(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
