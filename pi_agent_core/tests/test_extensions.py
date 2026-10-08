"""Tests for the Phase 7 ExtensionAPI skeleton."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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
# P7-17: a command's description is text, or the extension does not load
# ---------------------------------------------------------------------------


class TestCommandDescription:
    """A description goes to clients as ``AvailableCommand.description``, which must be text:
    a client (or the ACP model) that rejects it takes every command down with it."""

    @pytest.mark.parametrize("bad", [None, 5, True, ["a"], {"x": 1}, b"bytes"])
    def test_a_description_that_is_not_text_is_refused(self, bad: Any) -> None:
        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="test-ext"))

        with pytest.raises(TypeError, match="description"):
            api.register_command("hello", description=bad, handler=lambda: None)

        assert "hello" not in reg.get_commands()

    def test_the_error_names_the_extension_and_the_command(self) -> None:
        api = ExtensionAPI(registry=ExtensionRegistry(), meta=ExtensionMeta(name="test-ext"))

        with pytest.raises(TypeError) as raised:
            api.register_command("hello", description=5, handler=lambda: None)  # type: ignore[arg-type]

        assert "test-ext" in str(raised.value)
        assert "/hello" in str(raised.value)

    @pytest.mark.parametrize("fine", ["", "Say hello", "说你好"])
    def test_text_is_accepted(self, fine: str) -> None:
        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="test-ext"))

        api.register_command("hello", description=fine, handler=lambda: None)

        assert reg.get_commands()["hello"].description == fine

    def test_the_description_stays_optional(self) -> None:
        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="test-ext"))

        api.register_command("hello", handler=lambda: None)

        assert reg.get_commands()["hello"].description == ""

    def test_an_extension_that_does_it_fails_its_load_and_the_others_still_load(self) -> None:
        def bad_extension(pi: ExtensionAPI) -> None:
            pi.register_command("fine-one", description="ok", handler=lambda: None)
            pi.register_command("broken", description=None, handler=lambda: None)  # type: ignore[arg-type]

        loader = ExtensionLoader()
        loader.load_all(extra=[bad_extension, sample_extension], auto_discover=False)

        commands = loader.registry.get_commands()
        assert "hello" in commands  # the other extension
        assert "fine-one" not in commands and "broken" not in commands  # rolled back
        (failure,) = loader.failed
        assert "description" in failure.error


# ---------------------------------------------------------------------------
# F7-01: extensions can ask whether the project they run in is trusted
# ---------------------------------------------------------------------------


class TestProjectTrusted:
    @pytest.mark.parametrize("answer", [True, False])
    def test_an_extension_reads_the_harnesss_answer_while_it_activates(self, answer: bool) -> None:
        seen: list[Any] = []

        def ext(pi: ExtensionAPI) -> None:
            seen.append(pi.project_trusted)

        ExtensionLoader().load_callable(
            ext, name="x", bridge=SimpleNamespace(project_trusted=answer)
        )

        assert seen == [answer]

    @pytest.mark.parametrize("answer", [None, 0, 1, "", "yes", object()])
    def test_only_an_actual_yes_is_a_yes(self, answer: Any) -> None:
        """A gate that reads whatever is truthy as a yes lets a mistaken value open it."""
        seen: list[Any] = []

        def ext(pi: ExtensionAPI) -> None:
            seen.append(pi.project_trusted)

        ExtensionLoader().load_callable(
            ext, name="x", bridge=SimpleNamespace(project_trusted=answer)
        )

        assert seen == [False]

    def test_without_a_bridge_it_raises_like_the_other_accessors(self) -> None:
        api = ExtensionAPI(registry=ExtensionRegistry(), meta=ExtensionMeta(name="x"))

        with pytest.raises(RuntimeError, match="not connected"):
            _ = api.project_trusted

    def test_the_bridge_protocol_declares_it(self) -> None:
        from pi_agent_core.extensions._harness_bridge import HarnessBridge

        assert isinstance(vars(HarnessBridge)["project_trusted"], property)


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

            def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
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

            @property
            def tool_call_gate(self) -> Any:
                return None

            @property
            def project_trusted(self) -> bool:
                return False

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

            def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
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

            @property
            def tool_call_gate(self) -> Any:
                return None

            @property
            def project_trusted(self) -> bool:
                return False

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

            def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
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

            @property
            def tool_call_gate(self) -> Any:
                return None

            @property
            def project_trusted(self) -> bool:
                return False

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

        harness.load_extension(ext_old, name="same")
        await harness._ensure_extensions_loaded()
        assert "old-tool" in harness._tools
        assert len(harness._hooks.get("tool_call", [])) == 1

        harness.load_extension(ext_new, name="same")
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

    async def test_failed_override_preserves_live_harness(self) -> None:
        """Failed override must NOT purge old extension's live runtime state."""
        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model
        from pi_agent_harness import AgentHarness, MemorySessionStorage, Session

        session = Session(await MemorySessionStorage.create())
        harness = AgentHarness(
            session=session,
            model=Model(provider="mock", model_id="m1"),
            stream_fn=mock_text_stream,
        )

        def ext_good(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="old-tool",
                    description="old",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )
            pi.on("tool_call", lambda e: None)

        def ext_bad(pi: ExtensionAPI) -> None:
            raise RuntimeError("new activate crashed")

        harness.load_extension(ext_good, name="same")
        await harness._ensure_extensions_loaded()
        assert "old-tool" in harness._tools
        assert len(harness._hooks.get("tool_call", [])) == 1

        with pytest.raises(RuntimeError, match="new activate crashed"):
            harness.load_extension(ext_bad, name="same")

        # Registry restored
        assert "old-tool" in harness._extension_registry.get_tools()
        # Live harness tools/hooks must also survive
        assert "old-tool" in harness._tools
        assert len(harness._hooks.get("tool_call", [])) == 1


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


# ---------------------------------------------------------------------------
# P7-13: ExtensionLoader.load() accepts module or callable
# ---------------------------------------------------------------------------


class TestLoaderLoadAlias:
    def test_load_callable(self) -> None:
        """load() accepts a bare activate function."""

        def my_activate(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="via-load",
                    description="test",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )

        loader = ExtensionLoader()
        api = loader.load(my_activate, name="test-ext")
        assert "via-load" in loader.registry.get_tools()
        assert api.extension_name == "test-ext"

    def test_load_module_object(self) -> None:
        """load() accepts a module-like object with an activate attribute."""
        from types import SimpleNamespace

        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(
                ToolDefinition(
                    name="via-module",
                    description="test",
                    parameters=GreetParams,
                    execute=greet_execute,
                )
            )

        fake_module = SimpleNamespace(activate=activate)
        loader = ExtensionLoader()
        api = loader.load(fake_module, name="mod-ext")
        assert "via-module" in loader.registry.get_tools()
        assert api.extension_name == "mod-ext"

    def test_load_rejects_invalid(self) -> None:
        """load() raises TypeError for objects without activate."""
        loader = ExtensionLoader()
        with pytest.raises(TypeError, match="Cannot resolve"):
            loader.load(42, name="bad")


# ---------------------------------------------------------------------------
# P7-02: project-local extensions are opt-in
# ---------------------------------------------------------------------------
#
# Importing ``<cwd>/.pi-python/extensions`` executes whatever the repository ships, the
# moment a session opens. The project directory is therefore scanned only when trusted;
# the user's own directory and installed entry points are not affected.


def _write_extension(directory: Path, name: str, marker: Path, *, package: bool = False) -> None:
    """An extension whose *import* leaves ``marker`` behind (the hazard being guarded)."""
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def activate(pi):\n"
        f"    pi.register_command({name!r}, description='ext', handler=lambda args: None)\n"
    )
    if package:
        (directory / name).mkdir(parents=True, exist_ok=True)
        (directory / name / "__init__.py").write_text(source, encoding="utf-8")
    else:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.py").write_text(source, encoding="utf-8")


class TestProjectExtensionTrust:
    @pytest.fixture()
    def dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        from types import SimpleNamespace

        home = tmp_path / "home"
        project = tmp_path / "project"
        home.mkdir()
        project.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        return SimpleNamespace(
            user_ext=home / ".pi-python" / "extensions",
            project=project,
            project_ext=project / ".pi-python" / "extensions",
            marker=tmp_path / "imported.marker",
        )

    def test_untrusted_project_extension_is_not_imported(self, dirs: Any) -> None:
        _write_extension(dirs.project_ext, "proj_untrusted_a", dirs.marker)
        loader = ExtensionLoader()

        assert loader.discover_default_dirs(str(dirs.project)) == []

        assert not dirs.marker.exists()  # its code never ran

    def test_untrusted_project_extension_is_reported_not_silently_dropped(
        self, dirs: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        _write_extension(dirs.project_ext, "proj_untrusted_b", dirs.marker)
        loader = ExtensionLoader()

        with caplog.at_level(logging.WARNING, logger="pi_agent_core.extensions.loader"):
            loader.discover_default_dirs(str(dirs.project))

        ((skipped),) = loader.skipped
        assert skipped.directory == dirs.project_ext
        assert skipped.names == ("proj_untrusted_b.py",)
        assert any(
            "proj_untrusted_b.py" in record.getMessage() and record.levelno == logging.WARNING
            for record in caplog.records
        )

    def test_trusted_project_extension_is_loaded(self, dirs: Any) -> None:
        _write_extension(dirs.project_ext, "proj_trusted", dirs.marker)
        loader = ExtensionLoader()

        fns = loader.discover_default_dirs(str(dirs.project), trust_project=True)

        assert len(fns) == 1
        assert dirs.marker.exists()
        assert loader.skipped == []

    def test_package_style_project_extension_is_skipped_without_import(self, dirs: Any) -> None:
        _write_extension(dirs.project_ext, "proj_pkg", dirs.marker, package=True)
        loader = ExtensionLoader()

        assert loader.discover_default_dirs(str(dirs.project)) == []

        assert not dirs.marker.exists()
        assert [s.names for s in loader.skipped] == [("proj_pkg",)]

    def test_user_extension_loads_whatever_the_project_trust(self, dirs: Any) -> None:
        _write_extension(dirs.user_ext, "user_ext_a", dirs.marker)
        loader = ExtensionLoader()

        fns = loader.discover_default_dirs(str(dirs.project))

        assert len(fns) == 1
        assert dirs.marker.exists()
        assert loader.skipped == []

    def test_a_session_in_the_home_directory_does_not_call_the_user_directory_skipped(
        self, dirs: Any
    ) -> None:
        """With cwd = home, ``<home>/.pi-python/extensions`` is the user's own directory and
        also the "project" one. It loads, and nothing is reported as skipped."""
        _write_extension(dirs.user_ext, "user_in_home", dirs.marker)
        loader = ExtensionLoader()

        fns = loader.discover_default_dirs(str(dirs.user_ext.parent.parent))

        assert len(fns) == 1
        assert dirs.marker.exists()
        assert loader.skipped == []

    def test_the_home_directory_is_scanned_once_when_the_project_is_trusted(
        self, dirs: Any
    ) -> None:
        _write_extension(dirs.user_ext, "user_in_home_once", dirs.marker)
        loader = ExtensionLoader()

        fns = loader.discover_default_dirs(str(dirs.user_ext.parent.parent), trust_project=True)

        assert len(fns) == 1  # not once as the user directory and again as the project's

    def test_files_the_scanner_ignores_are_not_reported(self, dirs: Any) -> None:
        dirs.project_ext.mkdir(parents=True)
        (dirs.project_ext / "_private.py").write_text("x = 1\n", encoding="utf-8")
        (dirs.project_ext / "notes.txt").write_text("hello\n", encoding="utf-8")
        (dirs.project_ext / "not_a_package").mkdir()
        loader = ExtensionLoader()

        assert loader.discover_default_dirs(str(dirs.project)) == []

        assert loader.skipped == []  # nothing that would have been imported

    def test_a_project_without_an_extensions_directory_skips_nothing(self, dirs: Any) -> None:
        loader = ExtensionLoader()

        assert loader.discover_default_dirs(str(dirs.project)) == []

        assert loader.skipped == []

    def test_load_all_leaves_untrusted_project_extensions_alone_by_default(
        self, dirs: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_extension(dirs.project_ext, "proj_via_load_all", dirs.marker)
        loader = ExtensionLoader()
        monkeypatch.setattr(loader, "discover_entry_points", lambda: [])

        loader.load_all(cwd=str(dirs.project), auto_discover=True)

        assert "proj_via_load_all" not in loader.registry.get_commands()
        assert not dirs.marker.exists()

    def test_load_all_loads_project_extensions_once_trusted(
        self, dirs: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_extension(dirs.project_ext, "proj_via_load_all_trusted", dirs.marker)
        loader = ExtensionLoader()
        monkeypatch.setattr(loader, "discover_entry_points", lambda: [])

        loader.load_all(cwd=str(dirs.project), auto_discover=True, trust_project_extensions=True)

        assert "proj_via_load_all_trusted" in loader.registry.get_commands()

    def test_explicit_directories_are_the_callers_choice_and_still_load(self, dirs: Any) -> None:
        _write_extension(dirs.project_ext, "proj_explicit", dirs.marker)
        loader = ExtensionLoader()

        assert len(loader.discover_directory(dirs.project_ext)) == 1
        assert dirs.marker.exists()
