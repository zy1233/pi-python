"""Tool annotations: MCP-style hints about what a tool does.

The CLI's permission layer asks about every tool call unless the tool says it only reads
(``pi_agent_cli.permissions``). The hints travel on the tool object; the harness copies them
onto ``ToolCallEvent`` (tested in the harness package).
"""

from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from pi_agent_core.adapters.langchain_tools import from_langchain_tool
from pi_agent_core.coding_tools import ALL_TOOL_NAMES, READ_ONLY_TOOL_NAMES, create_all_tools
from pi_agent_core.coding_tools._base import CodingTool
from pi_agent_core.extensions.types import ToolDefinition
from pi_agent_core.tools import SimpleTool


class _Params(BaseModel):
    pass


async def _run(*_args):
    return None


def _lookup(q: str) -> str:
    return q


def _lc_tool(**metadata) -> StructuredTool:
    return StructuredTool.from_function(
        func=_lookup,
        name="lookup",
        description="Look something up",
        metadata=metadata or None,
    )


# --- the tool types carry the hints --------------------------------------------------


def test_a_tool_declares_nothing_unless_told_to():
    assert SimpleTool("t", "d", "T", _Params, _run).annotations is None
    assert CodingTool("t", "d", "T", _Params, _run).annotations is None
    assert ToolDefinition("t", "d", _Params, _run).annotations is None


def test_the_hints_travel_with_the_tool():
    hints = {"readOnlyHint": True, "openWorldHint": False}

    assert SimpleTool("t", "d", "T", _Params, _run, annotations=hints).annotations == hints
    assert CodingTool("t", "d", "T", _Params, _run, annotations=hints).annotations == hints
    assert ToolDefinition("t", "d", _Params, _run, annotations=hints).annotations == hints


# --- the built-in tools --------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ALL_TOOL_NAMES))
def test_exactly_the_inspection_tools_say_they_only_read(tmp_path, name):
    tool = create_all_tools(str(tmp_path))[name]

    hints = getattr(tool, "annotations", None) or {}

    assert (hints.get("readOnlyHint") is True) is (name in READ_ONLY_TOOL_NAMES)


@pytest.mark.parametrize("name", sorted(ALL_TOOL_NAMES - set(READ_ONLY_TOOL_NAMES)))
def test_a_tool_that_changes_things_makes_no_claim_either_way(tmp_path, name):
    """Silence, not ``readOnlyHint: False``: a tool that declares nothing is asked about."""
    assert getattr(create_all_tools(str(tmp_path))[name], "annotations", None) is None


def test_the_hints_of_one_tool_instance_are_not_shared_with_the_next(tmp_path):
    first = create_all_tools(str(tmp_path))["read"]
    second = create_all_tools(str(tmp_path))["read"]

    first.annotations["readOnlyHint"] = False

    assert second.annotations == {"readOnlyHint": True}


# --- LangChain / MCP tools -----------------------------------------------------------


def test_mcp_hints_on_a_langchain_tool_become_annotations():
    """``langchain-mcp-adapters`` copies an MCP tool's annotations into ``tool.metadata``."""
    tool = _lc_tool(readOnlyHint=True, openWorldHint=False, title="Look up", server="docs")

    assert from_langchain_tool(tool).annotations == {"readOnlyHint": True, "openWorldHint": False}


def test_all_four_mcp_hints_are_picked_up():
    hints = {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }

    assert from_langchain_tool(_lc_tool(**hints)).annotations == hints


def test_a_langchain_tool_without_hints_declares_nothing():
    assert from_langchain_tool(_lc_tool()).annotations is None
    assert from_langchain_tool(_lc_tool(server="docs")).annotations is None


def test_a_hint_that_is_not_a_boolean_is_not_taken_at_its_word():
    tool = _lc_tool(readOnlyHint="yes", destructiveHint=1, idempotentHint=None, openWorldHint=[])

    assert from_langchain_tool(tool).annotations is None
