"""The ``workflow`` tool — registered by the extension's ``activate()``."""

from __future__ import annotations

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


def _make_workflow_execute(
    executor: SubagentExecutor | None = None,
    cwd: str = ".",
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
        elif params.script:
            script = _normalize_script(params.script)
        else:
            return AgentToolResult(
                content=[{"type": "text", "text": "workflow requires either `script` or `name`."}]
            )

        runtime = WorkflowRuntime(
            _executor,
            cwd=cwd,
            max_agents=params.max_agents,
            concurrency=params.concurrency,
            token_budget=params.token_budget,
            timeout_ms=params.agent_timeout_ms,
        )

        try:
            run_result = await runtime.execute(script, params.args)
        except Exception as e:
            return AgentToolResult(content=[{"type": "text", "text": f"Workflow failed: {e}"}])

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

        budget_info = ""
        if run_result.token_usage.get("spent"):
            budget_info = f"\nToken usage: {run_result.token_usage['spent']} tokens"
            if run_result.token_usage.get("total"):
                budget_info += f" / {run_result.token_usage['total']} budget"

        result_text = ""
        if run_result.result is not None:
            result_text = f"\n\n## Result\n\n{run_result.result}"

        return AgentToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"Workflow **{run_result.meta.name}** completed with "
                        f"**{run_result.agent_count}** agent(s) in "
                        f"{run_result.duration_ms:.0f}ms.{budget_info}{result_text}"
                    ),
                }
            ],
            details={
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
            },
        )

    return workflow_execute


def create_workflow_tool(
    executor: SubagentExecutor | None = None,
    cwd: str = ".",
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
        execute=_make_workflow_execute(executor, cwd),
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
