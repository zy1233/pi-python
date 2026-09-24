"""WorkflowManager — manage foreground and background workflow runs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pi_dynamic_workflows.runtime import WorkflowRunResult, WorkflowRuntime

if TYPE_CHECKING:
    from pi_agent_core.extensions._harness_bridge import HarnessBridge

logger = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_S = 30.0


@dataclass
class WorkflowRun:
    """Tracks a single background workflow execution."""

    run_id: str
    task: asyncio.Task[WorkflowRunResult]
    script_name: str
    started_at: float = field(default_factory=time.monotonic)
    tool_call_id: str = ""


def _format_result(run_result: WorkflowRunResult) -> str:
    """Format a workflow run result as Markdown text."""
    budget_info = ""
    if run_result.token_usage.get("spent"):
        budget_info = f"\nToken usage: {run_result.token_usage['spent']} tokens"
        if run_result.token_usage.get("total"):
            budget_info += f" / {run_result.token_usage['total']} budget"

    result_text = ""
    if run_result.result is not None:
        result_text = f"\n\n## Result\n\n{run_result.result}"

    return (
        f"Background workflow **{run_result.meta.name}** completed with "
        f"**{run_result.agent_count}** agent(s) in "
        f"{run_result.duration_ms:.0f}ms.{budget_info}{result_text}"
    )


class WorkflowManager:
    """Manage concurrent workflow runs within a session."""

    def __init__(self, bridge: HarnessBridge) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._bridge = bridge

    async def start_background(
        self,
        runtime: WorkflowRuntime,
        script: str,
        args: Any,
        *,
        run_id: str,
        tool_call_id: str = "",
        script_name: str = "workflow",
    ) -> None:
        """Start a background workflow run using a pre-assigned *run_id*."""

        async def _run() -> WorkflowRunResult:
            return await runtime.execute(script, args, run_id=run_id)

        task = asyncio.create_task(_run())
        run = WorkflowRun(
            run_id=run_id,
            task=task,
            script_name=script_name,
            tool_call_id=tool_call_id,
        )
        self._runs[run_id] = run
        task.add_done_callback(lambda t: asyncio.ensure_future(self._on_complete(run_id)))

    async def _on_complete(self, run_id: str) -> None:
        """Callback when a background run finishes — injects result into session."""
        run = self._runs.pop(run_id, None)
        if run is None:
            return

        try:
            run_result = run.task.result()
            text = _format_result(run_result)
        except asyncio.CancelledError:
            text = f"Background workflow **{run.script_name}** was cancelled."
        except Exception as exc:
            text = f"Background workflow **{run.script_name}** failed: {exc}"

        try:
            self._bridge.trigger_prompt(text)
        except Exception:
            logger.warning("Failed to inject background result for run %s", run_id, exc_info=True)

    @property
    def pending_count(self) -> int:
        return len(self._runs)

    async def cancel_all(self) -> None:
        """Cancel all running background workflows."""
        for run in list(self._runs.values()):
            run.task.cancel()
        self._runs.clear()

    async def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_S) -> None:
        """Wait for all pending runs with a timeout, then cancel stragglers."""
        if not self._runs:
            return
        tasks = [run.task for run in self._runs.values()]
        with contextlib.suppress(Exception):
            await asyncio.wait(tasks, timeout=timeout)
        for run in list(self._runs.values()):
            if not run.task.done():
                run.task.cancel()
        self._runs.clear()
