"""The ``workflow`` tool — registered by the extension's ``activate()``."""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

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
    WorkflowRuntime,
)

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


_RUN_ID_RE = __import__("re").compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _journal_dir() -> Path:
    return Path.home() / ".pi-python" / "workflow-journals"


def _resolve_journal(run_id: str, *, resume: bool = False) -> Any:
    """Load (resume) or create (first run) a journal for *run_id*."""
    from pi_dynamic_workflows.journal import Journal

    jdir = _journal_dir()
    jdir.mkdir(parents=True, exist_ok=True)
    path = jdir / f"{run_id}.jsonl"
    if resume:
        return Journal.load(path)
    return Journal(path)


def _make_workflow_execute(
    executor: SubagentExecutor | None = None,
    cwd: str = ".",
    manager: Any | None = None,
) -> Any:
    """Build the workflow tool's execute function."""
    _executor = executor or MockSubagentExecutor()

    async def workflow_execute(
        tool_call_id: str,
        params: Any,
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
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
            if not _RUN_ID_RE.match(params.resume_from_run_id):
                return AgentToolResult(
                    content=[{"type": "text", "text": "Invalid resume_from_run_id format."}]
                )
            run_id = params.resume_from_run_id
            journal = _resolve_journal(run_id, resume=True)
        else:
            run_id = uuid.uuid4().hex[:12]
            journal = _resolve_journal(run_id)

        # Wire up worktree isolation when requested.
        run_executor = _executor
        wt_mgr = None
        if params.isolation and hasattr(_executor, "_worktree_manager"):
            from pi_dynamic_workflows.worktree import WorktreeManager

            wt_mgr = WorktreeManager(cwd)
            run_executor = _executor.__class__(
                stream_fn=_executor._stream_fn,
                parent_model=_executor._parent_model,
                cwd=cwd,
                get_api_key=_executor._get_api_key,
                tiers=_executor._tiers,
                worktree_manager=wt_mgr,
            )

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

        if params.background and manager is not None:
            await manager.start_background(
                runtime,
                script,
                params.args,
                run_id=run_id,
                tool_call_id=tool_call_id,
                script_name=script_name,
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
            run_result = await runtime.execute(script, params.args, run_id=run_id)
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
) -> Any:
    """Return a ToolDefinition for the workflow tool."""
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="workflow",
        description=(
            "Run a Python workflow that delegates work to subagents with "
            "agent(), optionally composing calls with parallel() and pipeline()."
        ),
        parameters=WorkflowParams,
        execute=_make_workflow_execute(executor, cwd, manager),
        label="Workflow",
        prompt_snippet=(
            "Delegate substantive independent or staged work to subagents with "
            "a Python workflow, optionally composing agent calls with parallel(), "
            "pipeline(), or both"
        ),
        prompt_guidelines=[WORKFLOW_GATE_GUIDELINE],
    )


def _normalize_script(script: str) -> str:
    """Strip optional markdown fences."""
    text = script.strip()
    import re

    fence = re.match(r"^```(?:py|python)?\s*\n([\s\S]*?)\n```$", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text
