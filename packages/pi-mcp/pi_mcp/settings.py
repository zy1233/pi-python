"""The limits of pi-mcp.

Two of them can be changed with an environment variable (there is no configuration file, as
Phase 6 decided). The others are constants of the code.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

from pi_agent_core.coding_tools.truncate import DEFAULT_MAX_BYTES

logger = logging.getLogger(__name__)

STARTUP_TIMEOUT_ENV = "PI_MCP_STARTUP_TIMEOUT"
CALL_TIMEOUT_ENV = "PI_MCP_CALL_TIMEOUT"

DEFAULT_STARTUP_TIMEOUT = 60.0
"""Seconds from the start of a server to the end of its tool list. Then it counts as failed."""

DEFAULT_CALL_TIMEOUT = 120.0
"""Seconds a tool call may take."""

BARRIER_WAIT = 10.0
"""The first turn waits this long for servers that are still starting, then goes on without them."""

CLOSE_GRACE = 5.0
"""A server gets this long to end by itself when the session closes. Then its task is cancelled."""

MAX_AUTO_RECONNECTS = 3
"""Reconnects in a row that tool calls start by themselves, for one server. A call that gets an
answer (or ``/mcp reconnect``) starts the count again: this only stops a server that is broken
from being started over and over."""

PING_TIMEOUT = 3.0
"""How long ``/mcp`` waits for a server to answer a ping."""

MAX_RESULT_BYTES = DEFAULT_MAX_BYTES
"""The text of a tool result is cut at this size (the limit of the built-in ``read`` tool)."""

REASON_LIMIT = 300
"""A reason in a message to the user has at most this many characters."""

MAX_LOG_BYTES = 512 * 1024
"""A server log that is larger than this is started over."""


@dataclass(frozen=True)
class Settings:
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT
    call_timeout: float = DEFAULT_CALL_TIMEOUT

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """The settings, with the environment variables applied (a bad value is ignored)."""
        env = os.environ if environ is None else environ
        return cls(
            startup_timeout=_seconds(env, STARTUP_TIMEOUT_ENV, DEFAULT_STARTUP_TIMEOUT),
            call_timeout=_seconds(env, CALL_TIMEOUT_ENV, DEFAULT_CALL_TIMEOUT),
        )


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        logger.warning("%s must be a positive number of seconds; using %g", name, default)
        return default
    return value
