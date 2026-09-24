"""pi-dynamic-workflows — Dynamic workflow orchestration for pi-python.

Provides:
  - ``workflow`` tool for multi-agent orchestration via Python scripts
  - ``/workflows`` command for listing and managing runs
  - 5 built-in workflow patterns: deep-research, adversarial-review,
    code-review, multi-perspective, codebase-audit
  - Model tier routing (small/medium/big)
  - Token budget tracking

Install: ``pip install pi-dynamic-workflows-py``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pi_dynamic_workflows.builtin_workflows import (
    BUILTIN_WORKFLOW_NAMES,
    BUILTIN_WORKFLOWS,
)
from pi_dynamic_workflows.builtin_workflows import (
    resolve_builtin_workflow as resolve_builtin_workflow,
)
from pi_dynamic_workflows.workflow_tool import create_workflow_tool

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

logger = logging.getLogger(__name__)


def _handle_workflows_command(pi: ExtensionAPI, args: str) -> None:
    """Handler for ``/workflows`` command."""
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"

    if sub == "list" or not sub:
        names = ", ".join(BUILTIN_WORKFLOW_NAMES)
        pi.send_message(f"Available built-in workflows: {names}")
    elif sub == "help":
        lines = ["## Built-in Workflow Patterns\n"]
        for name, desc in BUILTIN_WORKFLOWS.items():
            lines.append(f"- **{name}**: {desc.description}")
        lines.append("\nUse the `workflow` tool with `name` parameter to run a built-in pattern.")
        pi.send_message("\n".join(lines))
    else:
        pi.send_message(f"Unknown subcommand: {sub}\nUsage: /workflows [list|help]")


def _register_builtin_commands(pi: ExtensionAPI) -> None:
    """Register slash commands for each built-in workflow pattern.

    These are passthrough commands: advertised for client autocomplete
    but forwarded to the LLM so it invokes the ``workflow`` tool.
    """
    _noop = lambda args: None  # noqa: E731
    for name, desc in BUILTIN_WORKFLOWS.items():
        pi.register_command(
            name,
            description=desc.description,
            handler=_noop,
            passthrough=True,
        )


def _try_build_real_executor(pi: ExtensionAPI) -> tuple:
    """Try to build a HarnessSubagentExecutor from the bridge. Returns (executor, manager)."""
    try:
        bridge = pi._require_bridge()
        stream_fn = getattr(bridge, "stream_fn", None)
        model = getattr(bridge, "model", None)
        get_api_key = getattr(bridge, "get_api_key_fn", None)
        if stream_fn is None or model is None:
            return None, None

        from pi_dynamic_workflows.manager import WorkflowManager
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        executor = HarnessSubagentExecutor(
            stream_fn=stream_fn,
            parent_model=model,
            cwd=pi.cwd,
            get_api_key=get_api_key,
        )
        manager = WorkflowManager(bridge)
        return executor, manager
    except Exception:
        logger.debug("Could not build real executor; falling back to mock", exc_info=True)
        return None, None


def _register_saved_workflows(pi: ExtensionAPI) -> None:
    """Scan saved workflow directories and register slash commands."""
    try:
        from pi_dynamic_workflows.store import WorkflowStore

        store = WorkflowStore(cwd=pi.cwd)
        for wf in store.scan():
            if wf.name in BUILTIN_WORKFLOW_NAMES:
                continue
            pi.register_command(
                wf.name,
                description=wf.description or f"Run saved workflow: {wf.name}",
                handler=lambda args, _wf=wf: pi.send_message(
                    f"To run the saved workflow **{_wf.name}**, use the `workflow` tool with:\n"
                    f"  script=<contents of {_wf.path}>\n\n"
                    f"Description: {_wf.description}"
                ),
            )
    except Exception:
        logger.debug("Saved workflow scan failed", exc_info=True)


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point — called by the ExtensionLoader."""
    executor, manager = _try_build_real_executor(pi)

    pi.register_tool(create_workflow_tool(executor=executor, cwd=pi.cwd, manager=manager))

    pi.register_command(
        "workflows",
        description="List and manage dynamic workflows",
        handler=lambda args: _handle_workflows_command(pi, args if isinstance(args, str) else ""),
    )

    _register_builtin_commands(pi)
    _register_saved_workflows(pi)

    if manager is not None:
        bridge = pi._require_bridge()
        bridge.register_cleanup(manager.shutdown)
