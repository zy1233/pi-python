"""The ``workflow`` tool — registered by the extension's ``activate()``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from pi_agent_core.home import pi_home
from pi_agent_core.types import AgentToolResult
from pi_dynamic_workflows.builtin_workflows import (
    BUILTIN_WORKFLOW_NAMES,
    resolve_builtin_workflow,
)
from pi_dynamic_workflows.runtime import (
    MAX_AGENTS_PER_RUN,
    MAX_CONCURRENCY,
    MockSubagentExecutor,
    SubagentExecutor,
    UnavailableSubagentExecutor,
    WorkflowRuntime,
)

logger = logging.getLogger(__name__)

# How long an aborted run may take to wind down (sub-agents stop, worktrees are removed)
# before the tool stops waiting for it and answers anyway.
_UNWIND_GRACE_S = 10.0

_T = TypeVar("_T")

WORKFLOW_GATE_GUIDELINE = (
    "The `workflow` tool runs multi-agent orchestration — it fans decomposable work "
    "out across subagents, and fits tasks shaped like: repo-wide inspection, independent "
    "parallel research/checks, multi-perspective review, or fan-out/fan-in synthesis. "
    "ONLY call it when the user explicitly opts in — via the workflow trigger word, "
    "`/workflows run`, or their own words (e.g. 'run a workflow', 'fan this out', "
    "'并行审一遍'). For any other task — even one that would clearly benefit — do not "
    "call it; you may briefly offer it (with a rough cost) as an option instead."
)


class WorkflowParams(BaseModel):
    script: str | None = Field(
        default=None,
        description=(
            "Python workflow script. Required unless `name` is given. "
            "Use `await agent(prompt, **opts)`, `await parallel([...])`, "
            "`await pipeline(items, *stages)`, `phase(title)`, `log(msg)`. "
            "The script must define an `async def main():` entry point and "
            "call `agent()` at least once. Use `result(value)` to set the "
            "return value."
        ),
    )
    name: str | None = Field(
        default=None,
        description=(
            "Run a built-in workflow by name instead of `script`. "
            f"Built-ins: {', '.join(BUILTIN_WORKFLOW_NAMES)}."
        ),
    )
    args: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON object exposed to the workflow script as global `args`.",
    )
    max_agents: int = Field(
        default=MAX_AGENTS_PER_RUN,
        ge=1,
        le=MAX_AGENTS_PER_RUN,
        description="Agent cap (safety).",
    )
    concurrency: int = Field(
        default=MAX_CONCURRENCY,
        ge=1,
        le=MAX_CONCURRENCY,
        description="Maximum concurrent agents for this run.",
    )
    token_budget: int | None = Field(
        default=None,
        description=(
            "Optional soft token spend gate. Do not set unless the user explicitly supplies a cap."
        ),
    )
    agent_timeout_ms: float | None = Field(
        default=None,
        description="Timeout per agent in milliseconds.",
    )
    isolation: bool = Field(
        default=False,
        description=(
            "Run each subagent in an isolated git worktree. "
            "Requires the project to be inside a git repository. "
            "Worktrees are created per subagent and cleaned up after use."
        ),
    )
    background: bool = Field(
        default=False,
        description=(
            "Run the workflow in the background. The tool returns immediately "
            "with terminate=True; results are injected into the conversation "
            "when the workflow completes."
        ),
    )
    resume_from_run_id: str | None = Field(
        default=None,
        description=(
            "Resume a previous workflow run by replaying its journal. "
            "Host calls whose inputs haven't changed will return cached results."
        ),
    )


def _format_run_result(run_result: Any) -> tuple[str, dict[str, Any]]:
    """Build display text and details dict from a WorkflowRunResult."""
    budget_info = ""
    if run_result.token_usage.get("spent"):
        budget_info = f"\nToken usage: {run_result.token_usage['spent']} tokens"
        if run_result.token_usage.get("total"):
            budget_info += f" / {run_result.token_usage['total']} budget"

    result_text = ""
    if run_result.result is not None:
        result_text = f"\n\n## Result\n\n{run_result.result}"

    text = (
        f"Workflow **{run_result.meta.name}** completed with "
        f"**{run_result.agent_count}** agent(s) in "
        f"{run_result.duration_ms:.0f}ms.{budget_info}{result_text}"
    )
    details = {
        "meta": {
            "name": run_result.meta.name,
            "description": run_result.meta.description,
        },
        "agentCount": run_result.agent_count,
        "durationMs": run_result.duration_ms,
        "tokenUsage": run_result.token_usage,
        "phases": run_result.phases,
        "logs": run_result.logs,
        "runId": run_result.run_id,
    }
    return text, details


class _Aborted(Exception):
    """The turn that started the workflow was aborted while it was running."""


async def _wait_abort(signal: Any) -> None:
    """Return once *signal* is aborted: it offers ``wait_aborted()``, or only a flag."""
    wait_aborted = getattr(signal, "wait_aborted", None)
    if callable(wait_aborted):
        await wait_aborted()
        return
    while not getattr(signal, "aborted", False):
        await asyncio.sleep(0.05)


def _retrieve(task: asyncio.Future[Any]) -> None:
    """Mark the exception of a finished task as seen: nobody is left to await it."""
    if not task.cancelled():
        task.exception()


async def _stop(task: asyncio.Future[Any]) -> None:
    """Cancel *task* and give it ``_UNWIND_GRACE_S`` to wind down (its ``finally`` blocks)."""
    task.cancel()
    await asyncio.wait({task}, timeout=_UNWIND_GRACE_S)
    if task.done():
        _retrieve(task)
        return
    logger.warning(
        "A workflow run did not stop within %.0fs of the abort; leaving it to end on its own",
        _UNWIND_GRACE_S,
    )
    task.add_done_callback(_retrieve)


async def _abortable(start: Callable[[], Awaitable[_T]], signal: Any) -> _T:
    """Run ``start()``. If *signal* is aborted first, stop the run and raise ``_Aborted``.

    The run is stopped by cancelling it, so it unwinds the way any cancelled task does:
    sub-agents stop, worktrees are removed. This returns only after that (or the grace
    period). It also stops the run when this call is itself cancelled.
    """
    if signal is None:
        return await start()
    task = asyncio.ensure_future(start())
    watcher = asyncio.ensure_future(_wait_abort(signal))
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        await _stop(task)
        raise _Aborted
    except asyncio.CancelledError:
        await _stop(task)
        raise
    finally:
        watcher.cancel()


def _cancelled_result(script_name: str, run_id: str) -> AgentToolResult:
    return AgentToolResult(
        content=[
            {
                "type": "text",
                "text": (
                    f"Workflow **{script_name}** was cancelled (run_id: {run_id}); no more "
                    "agents will start. Agents that had already finished are journaled: to carry "
                    f"on, call the tool again with resume_from_run_id={run_id}."
                ),
            }
        ],
        details={"cancelled": True, "runId": run_id},
    )


# ``fullmatch``: ``$`` would also accept a trailing newline, and the id is a file name.
_RUN_ID_RE = re.compile(r"[a-zA-Z0-9_-]{1,128}")


def _journal_dir(home: Path | str | None = None) -> Path:
    """``<pi home>/workflow-journals`` for the session's home (``PI_HOME`` by default)."""
    return pi_home(home) / "workflow-journals"


def _resolve_journal(run_id: str, *, resume: bool = False, home: Path | str | None = None) -> Any:
    """Load (resume) or create (first run) a journal for *run_id*."""
    from pi_dynamic_workflows.journal import Journal

    jdir = _journal_dir(home)
    jdir.mkdir(parents=True, exist_ok=True)
    path = jdir / f"{run_id}.jsonl"
    if resume:
        return Journal.load(path)
    return Journal(path)


def _make_workflow_execute(
    executor: SubagentExecutor | None = None,
    cwd: str = ".",
    manager: Any | None = None,
    *,
    home: Path | str | None = None,
) -> Any:
    """Build the workflow tool's execute function.

    ``executor=None`` means the mock (tests). The extension itself passes an
    ``UnavailableSubagentExecutor`` when it cannot build a real one; the tool then says so
    instead of running scripts against canned answers.
    """
    _executor = executor or MockSubagentExecutor()

    async def workflow_execute(
        tool_call_id: str,
        params: Any,
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        if isinstance(_executor, UnavailableSubagentExecutor):
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "The workflow tool cannot run in this session: sub-agents are "
                            f"unavailable ({_executor.reason})."
                        ),
                    }
                ]
            )

        script: str | None = None
        script_name = "workflow"

        if params.name:
            resolved = resolve_builtin_workflow(params.name, params.args)
            if resolved is None:
                return AgentToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": (
                                f'No built-in workflow named "{params.name}". '
                                f"Available: {', '.join(BUILTIN_WORKFLOW_NAMES)}."
                            ),
                        }
                    ]
                )
            script = resolved.script
            script_name = resolved.name
        elif params.script:
            script = _normalize_script(params.script)
        else:
            return AgentToolResult(
                content=[{"type": "text", "text": "workflow requires either `script` or `name`."}]
            )

        # Assign a stable run_id first; resume reuses the caller's id.
        if params.resume_from_run_id:
            if not _RUN_ID_RE.fullmatch(params.resume_from_run_id):
                return AgentToolResult(
                    content=[{"type": "text", "text": "Invalid resume_from_run_id format."}]
                )
            run_id = params.resume_from_run_id
            journal = _resolve_journal(run_id, resume=True, home=home)
        else:
            run_id = uuid.uuid4().hex[:12]
            journal = _resolve_journal(run_id, home=home)

        # Wire up worktree isolation when requested.
        run_executor = _executor
        wt_mgr = None
        if params.isolation and hasattr(_executor, "with_worktree_manager"):
            from pi_dynamic_workflows.worktree import WorktreeManager

            wt_mgr = WorktreeManager(cwd)
            run_executor = _executor.with_worktree_manager(wt_mgr)

        runtime = WorkflowRuntime(
            run_executor,
            cwd=cwd,
            max_agents=params.max_agents,
            concurrency=params.concurrency,
            token_budget=params.token_budget,
            timeout_ms=params.agent_timeout_ms,
            journal=journal,
        )

        if params.background and manager is None:
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Background workflows require a harness bridge (WorkflowManager). "
                            "Run without `background=True`, or ensure the extension is loaded "
                            "with a connected AgentHarness."
                        ),
                    }
                ]
            )

        if getattr(signal, "aborted", False):  # cancelled before it began: start nothing
            return _cancelled_result(script_name, run_id)

        if params.background and manager is not None:
            await manager.start_background(
                runtime,
                script,
                params.args,
                run_id=run_id,
                tool_call_id=tool_call_id,
                script_name=script_name,
                cleanup=wt_mgr.cleanup_all if wt_mgr is not None else None,
            )
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            f"Workflow **{script_name}** is running in the background "
                            f"(run_id: {run_id}). Results will be delivered when it completes."
                        ),
                    }
                ],
                terminate=True,
            )

        try:
            run_result = await _abortable(
                lambda: runtime.execute(script, params.args, run_id=run_id), signal
            )
        except _Aborted:
            return _cancelled_result(script_name, run_id)
        except Exception as e:
            return AgentToolResult(content=[{"type": "text", "text": f"Workflow failed: {e}"}])
        finally:
            if wt_mgr is not None:
                with contextlib.suppress(Exception):
                    await wt_mgr.cleanup_all()

        if run_result.agent_count == 0:
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Workflow script must call agent() at least once; "
                            "this workflow did not run any subagents."
                        ),
                    }
                ]
            )

        text, details = _format_run_result(run_result)
        return AgentToolResult(
            content=[{"type": "text", "text": text}],
            details=details,
        )

    return workflow_execute


def create_workflow_tool(
    executor: SubagentExecutor | None = None,
    cwd: str = ".",
    manager: Any | None = None,
    *,
    home: Path | str | None = None,
) -> Any:
    """Return a ToolDefinition for the workflow tool.

    ``home`` is the pi-python home directory (``PI_HOME`` / ``~/.pi-python`` when omitted);
    run journals are written below it. ``executor=None`` uses the mock executor (tests).
    """
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="workflow",
        description=(
            "Run a Python workflow that delegates work to subagents with "
            "agent(), optionally composing calls with parallel() and pipeline()."
        ),
        parameters=WorkflowParams,
        execute=_make_workflow_execute(executor, cwd, manager, home=home),
        label="Workflow",
        prompt_snippet=(
            "Delegate substantive independent or staged work to subagents with "
            "a Python workflow, optionally composing agent calls with parallel(), "
            "pipeline(), or both"
        ),
        prompt_guidelines=[WORKFLOW_GATE_GUIDELINE],
        # No ``annotations``, deliberately: the sub-agents can write files and run commands,
        # so starting a workflow is a decision for the user, and the CLI's ``ask`` mode asks
        # about every tool that declares nothing.
    )


def _normalize_script(script: str) -> str:
    """Strip optional markdown fences."""
    text = script.strip()
    import re

    fence = re.match(r"^```(?:py|python)?\s*\n([\s\S]*?)\n```$", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text
