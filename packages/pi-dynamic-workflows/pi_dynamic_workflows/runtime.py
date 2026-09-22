"""Workflow runtime — sandboxed Python script execution with agent/parallel/pipeline globals."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pi_dynamic_workflows.budget import TokenBudget

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


# ---------------------------------------------------------------------------
# Workflow runtime
# ---------------------------------------------------------------------------


class WorkflowRuntime:
    """Execute a Python workflow script with sandboxed globals.

    The script has access to:
      - ``agent(prompt, **opts)`` — spawn an isolated subagent
      - ``parallel(thunks)`` — run async callables concurrently
      - ``pipeline(items, *stages)`` — sequential stages, concurrent items
      - ``phase(title)`` — mark the current phase
      - ``log(message)`` — append a workflow-level log line
      - ``args`` — optional JSON value passed from the tool
      - ``cwd`` — working directory
      - ``budget`` — ``{ total, spent(), remaining() }``
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
    ) -> None:
        self.executor = executor
        self.cwd = cwd
        self.max_agents = max_agents
        self.concurrency = concurrency
        self.budget = TokenBudget(total=token_budget)
        self.timeout_ms = timeout_ms

        self._agent_count = 0
        self._semaphore = asyncio.Semaphore(concurrency)
        self._current_phase: str | None = None
        self._phases: list[str] = []
        self._logs: list[str] = []
        self._meta = WorkflowMeta()

    async def execute(
        self,
        script: str,
        args: Any = None,
    ) -> WorkflowRunResult:
        """Run the workflow script and return its result."""
        start = time.monotonic()
        run_id = str(uuid.uuid4())[:12]

        result_holder: dict[str, Any] = {"value": None}

        namespace = self._build_namespace(args, result_holder)

        try:
            code = compile(script, "<workflow>", "exec")
            await self._exec_in_namespace(code, namespace)
        except Exception as e:
            logger.error("Workflow script failed: %s", e)
            raise

        meta = self._extract_meta(namespace)
        duration = (time.monotonic() - start) * 1000

        return WorkflowRunResult(
            meta=meta,
            result=result_holder["value"],
            agent_count=self._agent_count,
            duration_ms=duration,
            token_usage=self.budget.to_dict(),
            phases=list(self._phases),
            logs=list(self._logs),
            run_id=run_id,
        )

    def _build_namespace(self, args: Any, result_holder: dict[str, Any]) -> dict[str, Any]:
        """Build the sandboxed globals for the workflow script."""
        runtime = self

        async def agent_fn(prompt: str, **opts: Any) -> str | Any:
            if runtime._agent_count >= runtime.max_agents:
                raise RuntimeError(
                    f"Agent limit reached ({runtime.max_agents}). "
                    "Increase maxAgents or reduce fan-out."
                )
            if runtime.budget.exceeded():
                raise RuntimeError("Token budget exceeded")

            async with runtime._semaphore:
                runtime._agent_count += 1
                result = await runtime.executor.run_agent(
                    prompt,
                    tier=opts.get("tier"),
                    model=opts.get("model"),
                    label=opts.get("label"),
                    phase=opts.get("phase") or runtime._current_phase,
                    timeout_ms=opts.get("timeout_ms") or runtime.timeout_ms,
                    schema=opts.get("schema"),
                    cwd=opts.get("cwd") or runtime.cwd,
                )
                runtime.budget.add(result.tokens_used)
                if result.error:
                    return None
                return result.structured if result.structured is not None else result.text

        async def parallel_fn(thunks: list[Any]) -> list[Any]:
            tasks = [asyncio.ensure_future(fn()) for fn in thunks]
            return list(await asyncio.gather(*tasks, return_exceptions=True))

        async def pipeline_fn(items: list[Any], *stages: Any) -> list[Any]:
            async def run_item(item: Any, index: int) -> Any:
                value = item
                for stage in stages:
                    value = await stage(value, item, index)
                return value

            tasks = [asyncio.ensure_future(run_item(item, i)) for i, item in enumerate(items)]
            return list(await asyncio.gather(*tasks, return_exceptions=True))

        def phase_fn(title: str, **opts: Any) -> None:
            runtime._current_phase = title
            if title not in runtime._phases:
                runtime._phases.append(title)

        def log_fn(message: str) -> None:
            runtime._logs.append(str(message))

        def set_result(value: Any) -> None:
            result_holder["value"] = value

        namespace: dict[str, Any] = {
            "agent": agent_fn,
            "parallel": parallel_fn,
            "pipeline": pipeline_fn,
            "phase": phase_fn,
            "log": log_fn,
            "args": args,
            "cwd": self.cwd,
            "budget": self.budget,
            "result": set_result,
            "meta": {},
            "__builtins__": {
                "print": log_fn,
                "len": len,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "list": list,
                "dict": dict,
                "set": set,
                "tuple": tuple,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "None": None,
                "True": True,
                "False": False,
                "isinstance": isinstance,
                "sorted": sorted,
                "reversed": reversed,
                "min": min,
                "max": max,
                "sum": sum,
                "any": any,
                "all": all,
                "abs": abs,
                "round": round,
                "repr": repr,
                "type": type,
                "hasattr": hasattr,
                "getattr": getattr,
                "setattr": setattr,
                "Exception": Exception,
                "RuntimeError": RuntimeError,
                "ValueError": ValueError,
                "TypeError": TypeError,
                "KeyError": KeyError,
                "IndexError": IndexError,
            },
        }
        return namespace

    async def _exec_in_namespace(self, code: Any, namespace: dict[str, Any]) -> None:
        """Execute compiled code, awaiting top-level coroutines."""
        exec(code, namespace)
        main = namespace.get("main")
        if main is not None and asyncio.iscoroutinefunction(main):
            await main()

    def _extract_meta(self, namespace: dict[str, Any]) -> WorkflowMeta:
        """Extract ``meta`` dict from the script namespace."""
        raw = namespace.get("meta", {})
        if isinstance(raw, dict):
            return WorkflowMeta(
                name=raw.get("name", "unnamed"),
                description=raw.get("description", ""),
                phases=raw.get("phases", []),
            )
        return self._meta
