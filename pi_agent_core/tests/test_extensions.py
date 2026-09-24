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

    def test_load_same_name_overrides(self) -> None:
        loader = ExtensionLoader()
        api1 = loader.load_callable(sample_extension, name="sample")
        api2 = loader.load_callable(sample_extension, name="sample")
        assert api1 is not api2
        assert len(loader.apis) == 1
        assert loader.apis[0] is api2

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


# ---------------------------------------------------------------------------
# P7-02: failed activate must roll back partial registrations
# ---------------------------------------------------------------------------


class TestFailedActivateRollback:
    def test_partial_registrations_rolled_back(self) -> None:
        """If activate raises after registering a tool, registry is clean."""

        def half_extension(pi: ExtensionAPI) -> None:
            pi.register_tool(GREET_TOOL)
            raise RuntimeError("oops — mid-activate crash")

        loader = ExtensionLoader()
        with pytest.raises(RuntimeError, match="oops"):
            loader.load_callable(half_extension, name="half")
        assert loader.registry.tool_count == 0
        assert "greet" not in loader.registry.get_tools()

    def test_load_all_suppresses_but_rolls_back(self) -> None:
        """load_all suppresses errors but still rolls back the failed one."""

        def ghost_extension(pi: ExtensionAPI) -> None:
            pi.register_tool(GREET_TOOL)
            raise ValueError("ghost crash")

        loader = ExtensionLoader()
        apis = loader.load_all(extra=[ghost_extension], auto_discover=False)
        assert len(apis) == 0
        assert loader.registry.tool_count == 0


# ---------------------------------------------------------------------------
# P7-03: bridge available during activate
# ---------------------------------------------------------------------------


class TestBridgeDuringActivate:
    def test_bridge_injected_before_activate(self) -> None:
        """Extension can call pi.cwd during activate() when bridge is passed."""
        from pi_agent_core.extensions._harness_bridge import HarnessBridge

        captured: dict[str, Any] = {}

        class FakeBridge:
            def inject_tool(self, defn: Any) -> None:
                pass

            def remove_tool(self, name: str) -> None:
                pass

            def get_active_tool_names(self) -> list[str]:
                return []

            def set_active_tool_names(self, names: list[str]) -> None:
                pass

            def get_all_tool_info(self) -> list[Any]:
                return []

            def send_message(self, text: str) -> None:
                pass

            def trigger_prompt(self, text: str) -> None:
                pass

            def add_hook(self, event: str, handler: Any) -> None:
                pass

            def remove_hook(self, event: str, handler: Any) -> None:
                pass

            def get_custom_entries(self, custom_type: str) -> list[Any]:
                return []

            def register_cleanup(self, callback: Any) -> None:
                pass

            def append_entry(self, custom_type: str, data: Any) -> None:
                pass

            async def exec(self, command: str, **kw: Any) -> Any:
                pass

            @property
            def cwd(self) -> str:
                return "/test/cwd"

            @property
            def session_id(self) -> str:
                return "sid-fake"

            @property
            def stream_fn(self) -> Any:
                return None

            @property
            def model(self) -> Any:
                return None

            @property
            def get_api_key_fn(self) -> Any:
                return None

        assert isinstance(FakeBridge(), HarnessBridge)

        def my_ext(pi: ExtensionAPI) -> None:
            captured["cwd"] = pi.cwd
            captured["session_id"] = pi.session_id

        loader = ExtensionLoader()
        loader.load_callable(my_ext, name="mine", bridge=FakeBridge())
        assert captured["cwd"] == "/test/cwd"
        assert captured["session_id"] == "sid-fake"

    def test_without_bridge_cwd_raises(self) -> None:
        """Without bridge, pi.cwd raises RuntimeError."""

        def my_ext(pi: ExtensionAPI) -> None:
            _ = pi.cwd  # should raise

        loader = ExtensionLoader()
        with pytest.raises(RuntimeError, match="not connected"):
            loader.load_callable(my_ext, name="mine")


# ---------------------------------------------------------------------------
# P7-10: set_active_tools validates names
# ---------------------------------------------------------------------------


class TestBridgeToolValidation:
    def test_set_active_tools_rejects_unknown(self) -> None:
        """Bridge.set_active_tool_names raises for unknown tools."""
        from pi_agent_core.extensions._harness_bridge import HarnessBridge

        class FakeBridge:
            def inject_tool(self, defn: Any) -> None:
                pass

            def remove_tool(self, name: str) -> None:
                pass

            def get_active_tool_names(self) -> list[str]:
                return []

            def set_active_tool_names(self, names: list[str]) -> None:
                if "no-such-tool" in names:
                    raise ValueError(f"Unknown tool(s): {['no-such-tool']}")

            def get_all_tool_info(self) -> list[Any]:
                return []

            def send_message(self, text: str) -> None:
                pass

            def trigger_prompt(self, text: str) -> None:
                pass

            def add_hook(self, event: str, handler: Any) -> None:
                pass

            def remove_hook(self, event: str, handler: Any) -> None:
                pass

            def get_custom_entries(self, custom_type: str) -> list[Any]:
                return []

            def register_cleanup(self, callback: Any) -> None:
                pass

            def append_entry(self, custom_type: str, data: Any) -> None:
                pass

            async def exec(self, command: str, **kw: Any) -> Any:
                pass

            @property
            def cwd(self) -> str:
                return "."

            @property
            def session_id(self) -> str:
                return ""

            @property
            def stream_fn(self) -> Any:
                return None

            @property
            def model(self) -> Any:
                return None

            @property
            def get_api_key_fn(self) -> Any:
                return None

        assert isinstance(FakeBridge(), HarnessBridge)

        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="test"))
        api._set_bridge(FakeBridge())
        with pytest.raises(ValueError, match="Unknown tool"):
            api.set_active_tools(["no-such-tool"])


# ---------------------------------------------------------------------------
# P7R4-03: unsubscribe removes handler from harness hooks
# ---------------------------------------------------------------------------


class TestUnsubscribeRemovesHook:
    def test_unsubscribe_calls_bridge_remove_hook(self) -> None:
        """Unsubscribe from on() also removes handler from bridge hooks."""
        from pi_agent_core.extensions._harness_bridge import HarnessBridge

        removed: list[tuple[str, Any]] = []

        class TrackingBridge:
            def inject_tool(self, defn: Any) -> None:
                pass

            def remove_tool(self, name: str) -> None:
                pass

            def get_active_tool_names(self) -> list[str]:
                return []

            def set_active_tool_names(self, names: list[str]) -> None:
                pass

            def get_all_tool_info(self) -> list[Any]:
                return []

            def send_message(self, text: str) -> None:
                pass

            def trigger_prompt(self, text: str) -> None:
                pass

            def add_hook(self, event: str, handler: Any) -> None:
                pass

            def remove_hook(self, event: str, handler: Any) -> None:
                removed.append((event, handler))

            def get_custom_entries(self, custom_type: str) -> list[Any]:
                return []

            def register_cleanup(self, callback: Any) -> None:
                pass

            def append_entry(self, custom_type: str, data: Any) -> None:
                pass

            async def exec(self, command: str, **kw: Any) -> Any:
                pass

            @property
            def cwd(self) -> str:
                return "."

            @property
            def session_id(self) -> str:
                return ""

            @property
            def stream_fn(self) -> Any:
                return None

            @property
            def model(self) -> Any:
                return None

            @property
            def get_api_key_fn(self) -> Any:
                return None

        assert isinstance(TrackingBridge(), HarnessBridge)

        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="test"))
        api._set_bridge(TrackingBridge())
        api._loading = False

        handler = lambda e: None  # noqa: E731
        unsub = api.on("tool_call", handler)
        assert len(reg.get_event_handlers("tool_call")) == 1

        unsub()
        assert len(reg.get_event_handlers("tool_call")) == 0
        assert len(removed) == 1
        assert removed[0] == ("tool_call", handler)

    async def test_unsubscribe_with_real_harness(self) -> None:
        """Unsubscribe actually prevents handler dispatch in real harness."""
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
        )

        calls: list[str] = []

        def my_ext(pi: ExtensionAPI) -> None:
            unsub = pi.on("tool_call", lambda e: calls.append("called"))
            pi._unsub_ref = unsub  # stash for later use

        harness.load_extension(my_ext)
        await harness._ensure_extensions_loaded()

        api = harness._extension_loader.apis[0]
        unsub_fn = api._unsub_ref  # type: ignore[attr-defined]
        unsub_fn()

        assert "tool_call" not in harness._hooks or (len(harness._hooks.get("tool_call", [])) == 0)


# ---------------------------------------------------------------------------
# P7R4-04: session_start fires after extensions loaded
# ---------------------------------------------------------------------------


class TestSessionStartEvent:
    async def test_session_start_fires(self) -> None:
        """Extensions registered for session_start are called on first prompt."""
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        calls: list[dict[str, Any]] = []

        def ext_with_session_start(pi: ExtensionAPI) -> None:
            pi.on("session_start", lambda e: calls.append(e))

        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
            extensions=[ext_with_session_start],
        )
        await harness.prompt("hello")
        assert len(calls) == 1
        assert calls[0]["type"] == "session_start"


# ---------------------------------------------------------------------------
# P7R4-05: set_active_tools emits event + persists
# ---------------------------------------------------------------------------


class TestSetActiveToolsEvent:
    async def test_bridge_set_active_tools_queues_session_write(self) -> None:
        """Bridge.set_active_tool_names queues a pending session write."""
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
        )
        await harness._ensure_extensions_loaded()
        bridge = harness._extension_bridge
        assert bridge is not None

        initial_tools = list(harness.active_tool_names)
        assert len(harness.pending_session_writes) == 0

        bridge.set_active_tool_names(initial_tools)
        writes = [w for w in harness.pending_session_writes if w["type"] == "active_tools_change"]
        assert len(writes) == 1
        assert writes[0]["active_tool_names"] == initial_tools


# ---------------------------------------------------------------------------
# P7R4-06: same-name extension override replaces first
# ---------------------------------------------------------------------------


class TestSameNameOverride:
    def test_second_extension_overrides_first(self) -> None:
        """Loading an extension with the same name replaces the first."""

        def ext_a(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="tool-a",
                    description="from A",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )
            pi.register_command("cmd-a", description="A cmd", handler=lambda: None)

        def ext_b(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="tool-b",
                    description="from B",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )
            pi.register_command("cmd-b", description="B cmd", handler=lambda: None)

        loader = ExtensionLoader()
        loader.load_callable(ext_a, name="same")
        assert "tool-a" in loader.registry.get_tools()
        assert "cmd-a" in loader.registry.get_commands()

        loader.load_callable(ext_b, name="same")
        assert "tool-a" not in loader.registry.get_tools()
        assert "cmd-a" not in loader.registry.get_commands()
        assert "tool-b" in loader.registry.get_tools()
        assert "cmd-b" in loader.registry.get_commands()
        assert len(loader.apis) == 1
        assert loader.apis[0].extension_name == "same"

    async def test_override_cleans_live_harness(self) -> None:
        """Same-name override removes old tools/hooks from live harness."""
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
        )

        def ext_old(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="old-tool",
                    description="old",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )
            pi.on("tool_call", lambda e: None)

        def ext_new(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="new-tool",
                    description="new",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )

        harness.load_extension(ext_old)
        await harness._ensure_extensions_loaded()
        assert "old-tool" in harness._tools
        assert len(harness._hooks.get("tool_call", [])) == 1

        harness.load_extension(ext_new)
        assert "old-tool" not in harness._tools
        assert "new-tool" in harness._tools
        assert len(harness._hooks.get("tool_call", [])) == 0

    def test_failed_override_rolls_back(self) -> None:
        """If new extension's activate() fails, old extension is restored."""

        def ext_good(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="good-tool",
                    description="good",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )

        def ext_bad(pi: ExtensionAPI) -> None:
            raise RuntimeError("new activate crashed")

        loader = ExtensionLoader()
        loader.load_callable(ext_good, name="same")
        assert "good-tool" in loader.registry.get_tools()

        with pytest.raises(RuntimeError, match="new activate crashed"):
            loader.load_callable(ext_bad, name="same")

        # Old extension must be fully restored
        assert "good-tool" in loader.registry.get_tools()
        assert len(loader.apis) == 1
        assert loader.apis[0].extension_name == "same"


# ---------------------------------------------------------------------------
# P7R5-02: dynamic on() enters live hooks
# ---------------------------------------------------------------------------


class TestDynamicOnEntersLiveHooks:
    async def test_dynamic_on_adds_to_live_hooks(self) -> None:
        """on() after loading injects handler into harness hooks."""
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
        )

        captured_api: list[ExtensionAPI] = []

        def my_ext(pi: ExtensionAPI) -> None:
            captured_api.append(pi)

        harness.load_extension(my_ext)
        await harness._ensure_extensions_loaded()

        api = captured_api[0]
        calls: list[str] = []
        handler = lambda e: calls.append("fired")  # noqa: E731
        unsub = api.on("tool_call", handler)

        assert handler in harness._hooks.get("tool_call", [])

        unsub()
        assert handler not in harness._hooks.get("tool_call", [])


# ---------------------------------------------------------------------------
# P7R5-03: goal restore from session entries
# ---------------------------------------------------------------------------


class TestGoalRestore:
    async def test_restore_goal_state_from_session(self) -> None:
        """session_start restores goal state from custom entries."""
        from pi_goal_x import activate as goal_activate

        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        await session.append_custom_entry(
            "goal_state",
            {
                "description": "Build CLI",
                "steps": [
                    {"description": "Design", "status": "done"},
                    {"description": "Implement", "status": "in_progress"},
                ],
                "completed": False,
                "summary": None,
            },
        )

        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
            extensions=[goal_activate],
        )
        await harness.prompt("hello")

        # Verify goal was restored by calling goal_update — should NOT say "No active goal"
        tools = harness._extension_registry.get_tools()
        goal_update = tools["goal_update"]
        from pi_goal_x.goal_tool import GoalUpdateParams

        result = await goal_update.execute("tc-1", GoalUpdateParams(step_index=0, status="done"))
        text = result.content[0]["text"]
        assert "No active goal" not in text
