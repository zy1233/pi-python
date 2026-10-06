"""Shared helpers for the sandbox tests: a scripted host, a script runner, process liveness."""

from __future__ import annotations

import os
import sys
import textwrap
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from pi_dynamic_workflows.sandbox import SandboxLimits, ScriptOutcome, ScriptSandbox

# What a script that has got out of its namespace has in hand. ``agent`` is a function of the
# script process's own module, so its ``__globals__`` are that module's: real builtins, the
# imported ``os`` and ``sys``, the pipe to the host. This is the first escape of the
# audit (docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md); tests use it to stand in for a script
# written to get out. ``attempt`` reports an error instead of letting it end the script.
PRELUDE = """
def _imp(name):
    b = agent.__globals__["__builtins__"]
    return (getattr(b, "__import__", None) or b["__import__"])(name)

def _wire():
    return agent.__globals__["_out_fd"]

def attempt(fn):
    try:
        return "ok: " + repr(fn())
    except Exception as e:
        return type(e).__name__ + ": " + str(e)
"""

SPIN = "while True:\n    pass"
WALL_SECONDS = 120.0  # what ``run`` allows a run that has no limit of its own


def script(body: str, *, prelude: bool = True) -> str:
    """A workflow script whose ``main`` is *body*."""
    inner = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    return (PRELUDE if prelude else "") + "\nasync def main():\n" + inner + "\n"


class FakeHost:
    """The runtime's side of a run, reduced to what the sandbox asks of it."""

    def __init__(
        self, agent: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None
    ) -> None:
        self.logs: list[str] = []
        self.phases: list[str] = []
        self.prompts: list[str] = []
        self.opts: list[dict[str, Any]] = []
        self.tokens = 0
        self._agent = agent

    async def agent(self, prompt: str, opts: dict[str, Any]) -> Any:
        self.prompts.append(prompt)
        self.opts.append(opts)
        self.tokens += 10
        if self._agent is not None:
            return await self._agent(prompt, opts)
        return f"answer:{prompt}"

    def phase(self, title: str) -> None:
        self.phases.append(title)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def spent(self) -> int:
        return self.tokens


async def run(
    body: str,
    *,
    host: FakeHost | None = None,
    limits: SandboxLimits | None = None,
    audit_hook: bool = True,
    args: Any = None,
    budget_total: int | None = None,
    whole_script: bool = False,
) -> ScriptOutcome:
    """Run *body* (the body of ``main``, or a whole script) in a sandbox.

    A run nothing else bounds is given a long wall clock, so that a regression which leaves
    it waiting for ever fails the test instead of hanging the suite.
    """
    text = body if whole_script else script(body)
    limits = limits or SandboxLimits()
    if limits.wall_seconds is None:
        limits = replace(limits, wall_seconds=WALL_SECONDS)
    sandbox = ScriptSandbox(limits, audit_hook=audit_hook)
    return await sandbox.run(
        text, args=args, cwd=".", budget_total=budget_total, host=host or FakeHost()
    )


def alive(pid: int) -> bool:
    """Is a process with this id still running?"""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
