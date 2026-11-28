"""The environment of the agents the gateway starts.

Two rules (design section 7.2):

1. No credential of the gateway reaches an agent. The model can read the agent's environment
   (``bash env``), so a variable whose value is one of the platform credentials is dropped, and
   so is every ``ZYCODE_CHAT_*`` variable.
2. The tools see the user's usual ``PATH``. A gateway started as a service gets the service
   manager's environment, where ``PATH`` often has only the system folders, and ``bash`` would not
   find the user's ``node`` or ``cargo`` (audit 2026-11-28, RM-18). With ``env = "login-shell"``
   the gateway asks the user's login shell for its environment once, as VS Code does.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

GATEWAY_PREFIX = "ZYCODE_CHAT_"
_SHELL_TIMEOUT = 10.0


def login_shell_environment(shell: str | None = None) -> dict[str, str] | None:
    """The environment of ``$SHELL -l``, or ``None`` when it cannot be read (Windows, errors)."""
    if sys.platform == "win32":
        return None
    shell = shell or os.environ.get("SHELL") or "/bin/sh"
    try:
        result = subprocess.run(
            [shell, "-l", "-c", "env -0"],
            capture_output=True,
            timeout=_SHELL_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not read the login shell environment (%s): %s", shell, exc)
        return None
    if result.returncode != 0 or not result.stdout:
        logger.warning("the login shell %s gave no environment (exit %s)", shell, result.returncode)
        return None
    env: dict[str, str] = {}
    for item in result.stdout.split(b"\0"):
        name, sep, value = item.partition(b"=")
        if sep and name:
            env[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env or None


def agent_environment(
    *,
    base: Mapping[str, str],
    secrets: Iterable[str],
    extra: Mapping[str, str],
    home: Path,
) -> dict[str, str]:
    secret_values = {value for value in secrets if value}
    env = {
        name: value
        for name, value in base.items()
        if not name.startswith(GATEWAY_PREFIX) and value not in secret_values
    }
    for name, value in extra.items():
        if value in secret_values or name.startswith(GATEWAY_PREFIX):
            raise ValueError(f"agent_env: {name} would hand a credential to the agent")
        env[name] = value
    env["PI_HOME"] = str(home)
    return env
