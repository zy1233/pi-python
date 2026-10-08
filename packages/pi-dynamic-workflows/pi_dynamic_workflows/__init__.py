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
from typing import TYPE_CHECKING, Any

from pi_dynamic_workflows.builtin_workflows import (
    BUILTIN_WORKFLOW_NAMES,
    BUILTIN_WORKFLOWS,
)
from pi_dynamic_workflows.builtin_workflows import (
    resolve_builtin_workflow as resolve_builtin_workflow,
)
from pi_dynamic_workflows.runtime import UnavailableSubagentExecutor
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


def _unavailable(reason: str, *, exc_info: bool = False) -> tuple[None, None, str]:
    logger.warning(
        "Workflow sub-agents are unavailable: %s. The `workflow` tool will say so instead of "
        "running scripts.",
        reason,
        exc_info=exc_info,
    )
    return None, None, reason


def _build_executor(pi: ExtensionAPI) -> tuple[Any, Any, str | None]:
    """``(executor, manager, None)``; or ``(None, None, reason)`` when sub-agents cannot run.

    The reason is logged as a warning and reaches the user through the ``workflow`` tool.
    """
    try:
        bridge = pi._require_bridge()
    except Exception as exc:
        return _unavailable(f"the extension is not connected to a harness ({exc})")

    stream_fn = getattr(bridge, "stream_fn", None)
    model = getattr(bridge, "model", None)
    if stream_fn is None or model is None:
        return _unavailable("the harness provides no stream_fn/model to run agents with")

    try:
        from pi_dynamic_workflows.manager import WorkflowManager
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        # Sub-agents run on harnesses of their own; the gate is how the session's
        # tool_call policy (permission prompts, extension hooks) reaches their tool calls.
        tool_call_gate = getattr(bridge, "tool_call_gate", None)
        if tool_call_gate is None:
            logger.warning(
                "Harness bridge exposes no tool_call_gate: workflow sub-agents will run "
                "outside the session's tool_call policy (permission prompts included)."
            )
        executor = HarnessSubagentExecutor(
            stream_fn=stream_fn,
            parent_model=model,
            cwd=pi.cwd,
            get_api_key=getattr(bridge, "get_api_key_fn", None),
            tool_call_gate=tool_call_gate,
        )
        manager = WorkflowManager(bridge)
    except Exception as exc:
        return _unavailable(f"{type(exc).__name__}: {exc}", exc_info=True)
    return executor, manager, None


def _try_build_real_executor(pi: ExtensionAPI) -> tuple:
    """Build a HarnessSubagentExecutor from the bridge. Returns ``(executor, manager)``, or
    ``(None, None)`` (with a logged warning) when sub-agents cannot run in this session."""
    executor, manager, _reason = _build_executor(pi)
    return executor, manager


# The extension's own commands. A saved workflow of the same name would take one over
# (the registry keeps the last registration), so none may.
_RESERVED_COMMAND_NAMES = frozenset({"workflows", *BUILTIN_WORKFLOW_NAMES})


def _project_trusted(pi: ExtensionAPI) -> bool:
    """Whether the user vouched for the project; ``False`` whenever that cannot be told.

    Not knowing is read as no: an older core has no ``project_trusted``, and a gate that
    opened for a missing answer would be no gate.
    """
    try:
        return pi.project_trusted is True
    except Exception as exc:
        logger.warning(
            "Cannot read `pi.project_trusted` (%s: %s): the project's own saved workflows "
            "are not loaded.",
            type(exc).__name__,
            exc,
        )
        return False


def _register_saved_workflows(pi: ExtensionAPI) -> None:
    """Scan the saved workflow directories and register a slash command for each script.

    The user's own (``<pi home>/workflows``) always are. The project's
    (``<cwd>/.pi-python/workflows``) are only when the user vouched for the project
    (``pi.project_trusted``): a repository's scripts would otherwise become commands, with a
    description it wrote, as the session opens (audit F7-01). A script cannot take the place
    of one of this extension's own commands, and one that cannot be registered does not stop
    the others (audit P7-17).
    """
    try:
        from pi_dynamic_workflows.store import WorkflowStore

        store = WorkflowStore(cwd=pi.cwd, pi_home=pi.home, include_project=_project_trusted(pi))
        workflows = store.scan()
    except Exception:
        logger.warning("Saved workflow scan failed; no saved workflow commands", exc_info=True)
        return
    for wf in workflows:
        if wf.name in _RESERVED_COMMAND_NAMES:
            logger.warning(
                "Saved workflow %r (%s) is not registered as a command: /%s is one of this "
                "extension's own commands.",
                wf.name,
                wf.path,
                wf.name,
            )
            continue
        try:
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
            logger.warning(
                "Saved workflow %r (%s) could not be registered as a command",
                wf.name,
                wf.path,
                exc_info=True,
            )


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point — called by the ExtensionLoader."""
    executor, manager, reason = _build_executor(pi)
    if executor is None:
        # Never the mock: the tool then refuses, and says why, rather than "completing"
        # workflows against canned answers.
        executor = UnavailableSubagentExecutor(reason or "unknown reason")

    pi.register_tool(
        create_workflow_tool(executor=executor, cwd=pi.cwd, manager=manager, home=pi.home)
    )

    pi.register_command(
        "workflows",
        description="List and manage dynamic workflows",
        handler=lambda args: _handle_workflows_command(pi, args if isinstance(args, str) else ""),
    )

    _register_builtin_commands(pi)
    _register_saved_workflows(pi)

    if manager is not None:
        bridge = pi._require_bridge()
        # Closing the session ends its background runs (it does not wait them out).
        bridge.register_cleanup(manager.close)
