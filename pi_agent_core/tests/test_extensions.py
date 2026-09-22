"""Tests for the Phase 7 ExtensionAPI skeleton."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pi_agent_core.extensions import (
    ExtensionAPI,
    ExtensionLoader,
    ExtensionRegistry,
    ToolDefinition,
)
from pi_agent_core.extensions.types import ExtensionMeta
from pi_agent_core.types import AgentToolResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class GreetParams(BaseModel):
    name: str


async def greet_execute(
    tool_call_id: str,
    params: Any,
    signal: Any = None,
    on_update: Any = None,
) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": f"Hello, {params.name}!"}])


GREET_TOOL = ToolDefinition(
    name="greet",
    description="Generate a greeting",
    parameters=GreetParams,
    execute=greet_execute,
    label="Greeting",
    prompt_snippet="Greet someone by name",
    prompt_guidelines=["Use greet when the user asks to say hello."],
)


def sample_extension(pi: ExtensionAPI) -> None:
    pi.register_tool(GREET_TOOL)
    pi.register_command("hello", description="Say hello", handler=lambda args: None)
    pi.on("tool_call", lambda event: None)


# ---------------------------------------------------------------------------
# ExtensionRegistry
# ---------------------------------------------------------------------------


class TestExtensionRegistry:
    def test_add_and_get_tool(self) -> None:
        reg = ExtensionRegistry()
        reg.add_tool(GREET_TOOL)
        assert "greet" in reg.get_tools()
        assert reg.tool_count == 1

    def test_add_and_get_command(self) -> None:
        from pi_agent_core.extensions.types import CommandDef

        reg = ExtensionRegistry()
        cmd = CommandDef(name="test", description="Test cmd", handler=lambda: None)
        reg.add_command(cmd)
        assert "test" in reg.get_commands()
        assert reg.command_count == 1

    def test_event_handler_add_remove(self) -> None:
        from pi_agent_core.extensions.types import EventRegistration

        reg = ExtensionRegistry()
        registration = EventRegistration(event="tool_call", handler=lambda e: None)
        reg.add_event_handler(registration)
        assert len(reg.get_event_handlers("tool_call")) == 1
        reg.remove_event_handler(registration)
        assert len(reg.get_event_handlers("tool_call")) == 0

    def test_clear(self) -> None:
        reg = ExtensionRegistry()
        reg.add_tool(GREET_TOOL)
        reg.clear()
        assert reg.tool_count == 0

    def test_summary(self) -> None:
        reg = ExtensionRegistry()
        reg.add_tool(GREET_TOOL)
        summary = reg.summary()
        assert "greet" in summary["tools"]


# ---------------------------------------------------------------------------
# ExtensionAPI
# ---------------------------------------------------------------------------


class TestExtensionAPI:
    def test_register_tool_into_registry(self) -> None:
        reg = ExtensionRegistry()
        meta = ExtensionMeta(name="test-ext")
        api = ExtensionAPI(registry=reg, meta=meta)
        api.register_tool(GREET_TOOL)
        assert "greet" in reg.get_tools()

    def test_register_command(self) -> None:
        reg = ExtensionRegistry()
        meta = ExtensionMeta(name="test-ext")
        api = ExtensionAPI(registry=reg, meta=meta)
        api.register_command("hello", description="Say hello", handler=lambda: None)
        assert "hello" in reg.get_commands()

    def test_on_returns_unsubscribe(self) -> None:
        reg = ExtensionRegistry()
        meta = ExtensionMeta(name="test-ext")
        api = ExtensionAPI(registry=reg, meta=meta)
        unsub = api.on("tool_call", lambda e: None)
        assert len(reg.get_event_handlers("tool_call")) == 1
        unsub()
        assert len(reg.get_event_handlers("tool_call")) == 0

    def test_require_bridge_raises_without_bridge(self) -> None:
        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="x"))
        with pytest.raises(RuntimeError, match="not connected"):
            api.get_active_tools()

    def test_extension_name(self) -> None:
        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="my-ext"))
        assert api.extension_name == "my-ext"


# ---------------------------------------------------------------------------
# ExtensionLoader
# ---------------------------------------------------------------------------


class TestExtensionLoader:
    def test_load_callable(self) -> None:
        loader = ExtensionLoader()
        api = loader.load_callable(sample_extension, name="sample")
        assert api.extension_name == "sample"
        assert "greet" in loader.registry.get_tools()
        assert "hello" in loader.registry.get_commands()

    def test_load_same_name_skipped(self) -> None:
        loader = ExtensionLoader()
        api1 = loader.load_callable(sample_extension, name="sample")
        api2 = loader.load_callable(sample_extension, name="sample")
        assert api1 is api2
        assert len(loader.apis) == 1

    def test_load_all_with_extra(self) -> None:
        loader = ExtensionLoader()
        apis = loader.load_all(extra=[sample_extension], auto_discover=False)
        assert len(apis) == 1

    def test_discover_directory(self, tmp_path: Path) -> None:
        ext_file = tmp_path / "my_ext.py"
        ext_file.write_text(
            "from pi_agent_core.extensions import ExtensionAPI\n"
            "def activate(pi: ExtensionAPI) -> None:\n"
            "    pi.register_command('dir_cmd', description='from dir', handler=lambda: None)\n"
        )
        loader = ExtensionLoader()
        fns = loader.discover_directory(tmp_path)
        assert len(fns) == 1
        loader.load_callable(fns[0], name="my_ext")
        assert "dir_cmd" in loader.registry.get_commands()

    def test_discover_empty_directory(self, tmp_path: Path) -> None:
        loader = ExtensionLoader()
        fns = loader.discover_directory(tmp_path)
        assert fns == []

    def test_discover_nonexistent_directory(self) -> None:
        loader = ExtensionLoader()
        fns = loader.discover_directory("/nonexistent/path")
        assert fns == []

    def test_failing_extension_raises(self) -> None:
        def bad_extension(pi: ExtensionAPI) -> None:
            raise ValueError("boom")

        loader = ExtensionLoader()
        with pytest.raises(ValueError, match="boom"):
            loader.load_callable(bad_extension, name="bad")


# ---------------------------------------------------------------------------
# Integration: tool definition → AgentTool protocol
# ---------------------------------------------------------------------------


class TestToolDefinitionConversion:
    def test_tool_from_definition(self) -> None:
        from pi_agent_harness.agent_harness import _tool_from_definition

        tool = _tool_from_definition(GREET_TOOL)
        assert tool.name == "greet"
        assert tool.description == "Generate a greeting"
        assert tool.label == "Greeting"

    async def test_tool_execute(self) -> None:
        from pi_agent_harness.agent_harness import _tool_from_definition

        tool = _tool_from_definition(GREET_TOOL)
        result = await tool.execute("tc-1", GreetParams(name="World"))
        assert any("Hello, World!" in block.get("text", "") for block in result.content)
