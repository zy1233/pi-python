"""Workflow runtime — Python script execution with agent/parallel/pipeline globals.

A script does not run in this process. ``WorkflowRuntime.execute`` hands it to a
``ScriptSandbox``: a child process that has only the standard library, no inherited
environment, no way to start other processes and kernel limits on memory and CPU time. Its
``agent()`` calls travel over a pipe to the ``_RuntimeHost`` below, which does the real work
here (agent slots, token budget, journal, the sub-agent executor). The sandbox bounds the
*script*; what bounds a *sub-agent*'s effect is the ``tool_call`` policy every one of its tool
calls goes through (``HarnessSubagentExecutor``'s ``tool_call_gate``).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pi_dynamic_workflows.budget import TokenBudget
from pi_dynamic_workflows.sandbox import SandboxLimits, ScriptSandbox

if TYPE_CHECKING:
    from pi_dynamic_workflows.journal import Journal

logger = logging.getLogger(__name__)

MAX_AGENTS_PER_RUN = 1000
MAX_CONCURRENCY = 16


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Result from a single subagent call."""

    text: str | None = None
    structured: Any = None
    error: str | None = None
    tokens_used: int = 0
    duration_ms: float = 0


@dataclass
class WorkflowMeta:
    name: str = "unnamed"
    description: str = ""
    phases: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkflowRunResult:
    meta: WorkflowMeta
    result: Any = None
    agent_count: int = 0
    duration_ms: float = 0
    token_usage: dict[str, Any] = field(default_factory=dict)
    phases: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    run_id: str = ""


# ---------------------------------------------------------------------------
# Subagent executor protocol
# ---------------------------------------------------------------------------


class SubagentExecutor:
    """Protocol for spawning subagents.  Injected by the WorkflowManager."""

    async def run_agent(
        self,
        prompt: str,
        *,
        tier: str | None = None,
        model: str | None = None,
        label: str | None = None,
        phase: str | None = None,
        timeout_ms: float | None = None,
        schema: dict[str, Any] | None = None,
        cwd: str | None = None,
    ) -> AgentResult:
        """Spawn an isolated subagent and return its result."""
        raise NotImplementedError


class MockSubagentExecutor(SubagentExecutor):
    """Default executor that returns a canned response (for testing)."""

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        return AgentResult(text=f"[mock agent response to: {prompt[:80]}]", tokens_used=100)


class UnavailableSubagentExecutor(SubagentExecutor):
    """Stands in when sub-agents cannot run in this session; ``reason`` says why.

    The ``workflow`` tool checks for it and refuses to run a script. Falling back to
    ``MockSubagentExecutor`` instead made a workflow "complete" with canned
    ``[mock agent response to: ...]`` text, indistinguishable from real work.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        return AgentResult(error=f"Sub-agents are unavailable: {self.reason}")


# ---------------------------------------------------------------------------
# Workflow runtime
# ---------------------------------------------------------------------------


def _check_agent_opts(opts: dict[str, Any]) -> None:
    """What a script passes to ``agent()`` is data from a process we do not trust."""
    for name in ("tier", "model", "label", "phase", "cwd"):
        value = opts.get(name)
        if value is not None and not isinstance(value, str):
            raise TypeError(f"agent() {name} must be a string, not {type(value).__name__}")
    timeout = opts.get("timeout_ms")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int | float)):
        raise TypeError(f"agent() timeout_ms must be a number, not {type(timeout).__name__}")
    schema = opts.get("schema")
    if schema is not None and not isinstance(schema, dict):
        raise TypeError(f"agent() schema must be a dict, not {type(schema).__name__}")


class _RuntimeHost:
    """What the sandbox asks of the runtime on a script's behalf."""

    def __init__(self, runtime: WorkflowRuntime) -> None:
        self._runtime = runtime

    def phase(self, title: str) -> None:
        runtime = self._runtime
        if title not in runtime._phases:
            runtime._phases.append(title)

    def log(self, message: str) -> None:
        self._runtime._logs.append(message)

    def spent(self) -> int:
        return self._runtime.budget.spent()

    async def agent(self, prompt: str, opts: dict[str, Any]) -> Any:
        from pi_dynamic_workflows.journal import _MISS, hash_request
        from pi_dynamic_workflows.paths import resolve_subagent_cwd

        runtime = self._runtime
        _check_agent_opts(opts)

        # A directory outside the project is refused before anything is spent on it:
        # no agent slot, and no answer from the journal either (a resume must not get
        # around a policy that is tighter than the first run's).
        requested_cwd = opts.get("cwd")
        run_cwd = resolve_subagent_cwd(requested_cwd, runtime.cwd) if requested_cwd else None

        # P7-09: atomically check + reserve under lock to prevent
        # concurrent tasks from bypassing the max_agents limit.
        async with runtime._count_lock:
            if runtime._agent_count >= runtime.max_agents:
                raise RuntimeError(
                    f"Agent limit reached ({runtime.max_agents}). "
                    "Increase maxAgents or reduce fan-out."
                )
            if runtime.budget.exceeded():
                raise RuntimeError("Token budget exceeded")
            runtime._agent_count += 1

        # Resolve implicit defaults so journal hash matches actual execution params. The phase
        # a call was made in comes with it: this host answers calls as tasks of its own, so
        # "the latest phase message" could already be a later one.
        resolved_phase = opts.get("phase")
        resolved_cwd = opts.get("cwd") or runtime.cwd
        resolved_timeout = opts.get("timeout_ms") or runtime.timeout_ms

        req_hash: str | None = None
        if runtime._journal is not None:
            deterministic_opts = {
                "tier": opts.get("tier"),
                "model": opts.get("model"),
                "label": opts.get("label"),
                "phase": resolved_phase,
                "schema": opts.get("schema"),
                "cwd": resolved_cwd,
            }
            req_hash = hash_request("agent", {"prompt": prompt, **deterministic_opts})
            cached = runtime._journal.try_replay("agent", req_hash)
            if cached is not _MISS:
                return cached

        async with runtime._semaphore:
            result = await runtime.executor.run_agent(
                prompt,
                tier=opts.get("tier"),
                model=opts.get("model"),
                label=opts.get("label"),
                phase=resolved_phase,
                timeout_ms=resolved_timeout,
                schema=opts.get("schema"),
                cwd=run_cwd or runtime.cwd,
            )
            runtime.budget.add(result.tokens_used)
            if result.error:
                # The script gets None. The journal gets nothing: a failure is not an
                # answer, and a resume (provider back up, timeout raised) must retry it.
                return None
            return_value = result.structured if result.structured is not None else result.text

            if runtime._journal is not None and req_hash is not None:
                runtime._journal.append("agent", req_hash, return_value)

            return return_value


class WorkflowRuntime:
    """Execute a Python workflow script in a sandboxed child process.

    The script has access to:
      - ``agent(prompt, **opts)`` — spawn an isolated subagent
      - ``parallel(thunks)`` — run async callables concurrently
      - ``pipeline(items, *stages)`` — sequential stages, concurrent items
      - ``phase(title)`` — mark the current phase
      - ``log(message)`` — append a workflow-level log line
      - ``args`` — optional JSON value passed from the tool
      - ``cwd`` — working directory
      - ``budget`` — ``{ total, spent(), remaining() }`` (a read-only view)

    Values cross the process boundary as JSON: ``args`` in, ``result(...)`` and what
    ``agent()`` returns out and in. A script that raises makes ``execute`` raise a
    ``WorkflowScriptError`` carrying its message; one that is stopped for using too much
    (memory, CPU, time) or that cannot be run at all raises a ``SandboxError``. Both are
    ``RuntimeError``.
    """

    def __init__(
        self,
        executor: SubagentExecutor,
        *,
        cwd: str = ".",
        max_agents: int = MAX_AGENTS_PER_RUN,
        concurrency: int = MAX_CONCURRENCY,
        token_budget: int | None = None,
        timeout_ms: float | None = None,
        journal: Journal | None = None,
        sandbox_limits: SandboxLimits | None = None,
    ) -> None:
        self.executor = executor
        self.cwd = cwd
        self.max_agents = max_agents
        self.concurrency = concurrency
        self.budget = TokenBudget(total=token_budget)
        self.timeout_ms = timeout_ms
        self.sandbox_limits = sandbox_limits
        self._journal = journal

        self._agent_count = 0
        self._semaphore = asyncio.Semaphore(concurrency)
        self._count_lock = asyncio.Lock()
        self._phases: list[str] = []
        self._logs: list[str] = []
        self._meta = WorkflowMeta()

    async def execute(
        self,
        script: str,
        args: Any = None,
        run_id: str | None = None,
    ) -> WorkflowRunResult:
        """Run the workflow script and return its result."""
        start = time.monotonic()
        run_id = run_id or str(uuid.uuid4())[:12]

        sandbox = ScriptSandbox(self.sandbox_limits)
        try:
            outcome = await sandbox.run(
                script,
                args=args,
                cwd=self.cwd,
                budget_total=self.budget.total,
                host=_RuntimeHost(self),
            )
        except Exception as e:
            logger.error("Workflow script failed: %s", e)
            raise

        meta = self._meta_from(outcome.meta)
        duration = (time.monotonic() - start) * 1000

        return WorkflowRunResult(
            meta=meta,
            result=outcome.result,
            agent_count=self._agent_count,
            duration_ms=duration,
            token_usage=self.budget.to_dict(),
            phases=list(self._phases),
            logs=list(self._logs),
            run_id=run_id,
        )

    def _meta_from(self, raw: dict[str, Any] | None) -> WorkflowMeta:
        """The script's ``meta`` dict, as the script left it, or the default if it has none."""
        if raw is None:
            return self._meta
        phases = raw.get("phases", [])
        return WorkflowMeta(
            name=str(raw.get("name", "unnamed")),
            description=str(raw.get("description", "")),
            phases=phases if isinstance(phases, list) else [],
        )
