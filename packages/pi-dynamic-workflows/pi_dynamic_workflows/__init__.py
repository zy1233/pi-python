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

import contextlib
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
    """Register slash commands for each built-in workflow pattern."""
    for name, desc in BUILTIN_WORKFLOWS.items():
        name.replace("-", "_")
        pi.register_command(
            name,
            description=desc.description,
            handler=lambda args, _n=name: pi.send_message(
                f"To run the {_n} workflow, use the `workflow` tool with:\n"
                f'  name="{_n}"\n'
                f"  args={{...}}\n\n"
                f"Description: {BUILTIN_WORKFLOWS[_n].description}"
            ),
        )


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point — called by the ExtensionLoader."""
    cwd = "."
    with contextlib.suppress(Exception):
        cwd = pi.cwd

    pi.register_tool(create_workflow_tool(cwd=cwd))

    pi.register_command(
        "workflows",
        description="List and manage dynamic workflows",
        handler=lambda args: _handle_workflows_command(pi, args if isinstance(args, str) else ""),
    )

    _register_builtin_commands(pi)
