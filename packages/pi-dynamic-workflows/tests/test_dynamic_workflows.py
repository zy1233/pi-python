"""Tests for pi-dynamic-workflows extension."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from pi_dynamic_workflows.budget import TokenBudget
from pi_dynamic_workflows.builtin_workflows import (
    BUILTIN_WORKFLOW_NAMES,
    generate_adversarial_review,
    generate_code_review,
    generate_deep_research,
    generate_multi_perspective,
    resolve_builtin_workflow,
)
from pi_dynamic_workflows.journal import _MISS, Journal, hash_request
from pi_dynamic_workflows.model_routing import (
    ModelRoute,
    ModelRoutingConfig,
    resolve_model_for_phase,
    resolve_tier,
)
from pi_dynamic_workflows.runtime import (
    AgentResult,
    MockSubagentExecutor,
    SubagentExecutor,
    WorkflowRuntime,
)
from pi_dynamic_workflows.store import WorkflowStore, extract_meta
from pi_dynamic_workflows.workflow_tool import (
    WorkflowParams,
    _normalize_script,
    create_workflow_tool,
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: Any) -> None:
    """Redirect Path.home() to tmp_path so tests never touch the real HOME (nor a PI_HOME)."""
    monkeypatch.delenv("PI_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------


class TestTokenBudget:
    def test_unlimited(self) -> None:
        b = TokenBudget()
        assert b.remaining() is None
        assert not b.exceeded()
        b.add(1000)
        assert b.spent() == 1000
        assert not b.exceeded()

    def test_with_limit(self) -> None:
        b = TokenBudget(total=500)
        assert b.remaining() == 500
        b.add(300)
        assert b.remaining() == 200
        assert not b.exceeded()
        b.add(200)
        assert b.remaining() == 0
        assert b.exceeded()

    def test_to_dict(self) -> None:
        b = TokenBudget(total=1000)
        b.add(250)
        d = b.to_dict()
        assert d["total"] == 1000
        assert d["spent"] == 250
        assert d["remaining"] == 750


# ---------------------------------------------------------------------------
# ModelRouting
# ---------------------------------------------------------------------------


class TestModelRouting:
    def test_no_phase(self) -> None:
        config = ModelRoutingConfig(default_model="default")
        assert resolve_model_for_phase(None, config) == "default"

    def test_exact_match(self) -> None:
        config = ModelRoutingConfig(
            default_model="default",
            routes=[ModelRoute(phase_pattern="Research", model="gpt-4")],
        )
        assert resolve_model_for_phase("Research", config) == "gpt-4"
        assert resolve_model_for_phase("Other", config) == "default"

    def test_regex_match(self) -> None:
        config = ModelRoutingConfig(
            routes=[ModelRoute(phase_pattern="review.*", model="claude", use_regex=True)],
        )
        assert resolve_model_for_phase("review-code", config) == "claude"

    def test_resolve_tier(self) -> None:
        # Built-in defaults are per provider (DEFAULT_MODEL_TIERS_BY_PROVIDER).
        assert resolve_tier("small", provider="anthropic") == "anthropic/claude-sonnet-4-20250514"
        assert resolve_tier("medium", provider="anthropic") == "anthropic/claude-sonnet-4-20250514"
        assert resolve_tier("big", provider="anthropic") == "anthropic/claude-opus-4-20250514"
        assert resolve_tier("unknown", provider="anthropic") is None
        assert resolve_tier(None, provider="anthropic") is None

    @pytest.mark.parametrize("provider", ["deepseek", "openai", "mock", "", None])
    @pytest.mark.parametrize("tier", ["small", "medium", "big"])
    def test_default_tiers_never_leave_the_parent_provider(
        self, provider: str | None, tier: str
    ) -> None:
        """The defaults used to name Anthropic models for *every* parent, so a DeepSeek
        run silently became an Anthropic run (and was sent the DeepSeek key)."""
        assert resolve_tier(tier, provider=provider) is None

    def test_default_tier_lookup_ignores_provider_case(self) -> None:
        assert resolve_tier("big", provider="Anthropic") == "anthropic/claude-opus-4-20250514"

    def test_explicit_tiers_win_and_do_not_fall_back_to_defaults(self) -> None:
        tiers = {"small": "openai/gpt-4o-mini"}
        assert resolve_tier("small", tiers, provider="anthropic") == "openai/gpt-4o-mini"
        assert resolve_tier("big", tiers, provider="anthropic") is None


# ---------------------------------------------------------------------------
# WorkflowRuntime
# ---------------------------------------------------------------------------


class RecordingExecutor(SubagentExecutor):
    """Records all agent calls for testing."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        self.calls.append({"prompt": prompt, **kwargs})
        return AgentResult(text=f"Response to: {prompt[:50]}", tokens_used=50)


class TestWorkflowRuntime:
    async def test_simple_script(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd="/test")

        script = """
meta = {"name": "test_wf", "description": "A test workflow"}

async def main():
    r = await agent("Hello world")
    result(r)
"""
        run_result = await runtime.execute(script)
        assert run_result.meta.name == "test_wf"
        assert run_result.agent_count == 1
        assert len(executor.calls) == 1
        assert run_result.result is not None

    async def test_parallel(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
meta = {"name": "parallel_test"}

async def main():
    results = await parallel([
        lambda: agent("Task 1"),
        lambda: agent("Task 2"),
        lambda: agent("Task 3"),
    ])
    result(len([r for r in results if r]))
"""
        run_result = await runtime.execute(script)
        assert run_result.agent_count == 3
        assert len(executor.calls) == 3
        assert run_result.result == 3

    async def test_pipeline(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
meta = {"name": "pipeline_test"}

async def main():
    items = ["a", "b"]
    results = await pipeline(
        items,
        lambda prev, orig, idx: agent(f"Stage 1: {orig}"),
        lambda prev, orig, idx: agent(f"Stage 2: {prev}"),
    )
    result(len(results))
"""
        run_result = await runtime.execute(script)
        assert run_result.agent_count == 4  # 2 items x 2 stages
        assert run_result.result == 2

    async def test_phase_tracking(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
meta = {"name": "phase_test"}

async def main():
    phase("Setup")
    await agent("setup task")
    phase("Execute")
    await agent("execute task")
"""
        run_result = await runtime.execute(script)
        assert run_result.phases == ["Setup", "Execute"]

    async def test_logging(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
async def main():
    log("Starting workflow")
    await agent("task")
    log("Done")
"""
        run_result = await runtime.execute(script)
        assert run_result.logs == ["Starting workflow", "Done"]

    async def test_args_passed(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
async def main():
    await agent(f"Query: {args['question']}")
"""
        await runtime.execute(script, args={"question": "What is Python?"})
        assert "What is Python?" in executor.calls[0]["prompt"]

    async def test_agent_limit(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, max_agents=2)

        script = """
async def main():
    await agent("1")
    await agent("2")
    await agent("3")
"""
        with pytest.raises(RuntimeError, match="Agent limit"):
            await runtime.execute(script)

    async def test_budget_exceeded(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, token_budget=40)

        script = """
async def main():
    await agent("1")
    await agent("2")
"""
        with pytest.raises(RuntimeError, match="budget exceeded"):
            await runtime.execute(script)

    async def test_mock_executor(self) -> None:
        executor = MockSubagentExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
async def main():
    r = await agent("test prompt")
    result(r)
"""
        run_result = await runtime.execute(script)
        assert run_result.agent_count == 1
        assert "mock agent response" in (run_result.result or "")

    async def test_agent_limit_concurrent(self) -> None:
        """P7-09: max_agents must not be bypassed by concurrent parallel() calls."""
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, max_agents=2, concurrency=2)

        script = """
async def main():
    results = await parallel([
        lambda: agent("a"),
        lambda: agent("b"),
        lambda: agent("c"),
    ])
    errors = [r for r in results if isinstance(r, RuntimeError)]
    if errors:
        raise errors[0]
"""
        with pytest.raises(RuntimeError, match="Agent limit"):
            await runtime.execute(script)
        assert runtime._agent_count <= 2

    async def test_parallel_hands_a_failing_thunk_back_as_its_result(self) -> None:
        """A failure is that thunk's result; the others still run to completion."""
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
async def boom():
    raise ValueError("no")

async def main():
    results = await parallel([lambda: agent("a"), boom, lambda: agent("b")])
    result([isinstance(r, ValueError) for r in results])
"""
        run_result = await runtime.execute(script)

        assert run_result.result == [False, True, False]
        assert len(executor.calls) == 2

    async def test_pipeline_hands_a_failing_item_back_as_its_result(self) -> None:
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor)

        script = """
async def stage(prev, orig, idx):
    if orig == "bad":
        raise ValueError("no")
    return await agent("ok " + orig)

async def main():
    results = await pipeline(["good", "bad", "fine"], stage)
    result([isinstance(r, ValueError) for r in results])
"""
        run_result = await runtime.execute(script)

        assert run_result.result == [False, True, False]
        assert len(executor.calls) == 2


# ---------------------------------------------------------------------------
# Built-in workflows
# ---------------------------------------------------------------------------


class TestBuiltinWorkflows:
    def test_all_patterns_exist(self) -> None:
        expected = [
            "deep-research",
            "adversarial-review",
            "code-review",
            "multi-perspective",
            "codebase-audit",
        ]
        for name in expected:
            assert name in BUILTIN_WORKFLOW_NAMES

    def test_deep_research_resolve(self) -> None:
        inv = resolve_builtin_workflow("deep-research", {"question": "What is AI?"})
        assert inv is not None
        assert "deep_research" in inv.script
        assert "agent(" in inv.script

    def test_code_review_resolve(self) -> None:
        inv = resolve_builtin_workflow("code-review", {"diff": "some diff"})
        assert inv is not None
        assert "code_review" in inv.script

    def test_multi_perspective_resolve(self) -> None:
        inv = resolve_builtin_workflow("multi-perspective", {"topic": "Python vs Rust"})
        assert inv is not None
        assert "multi_perspective" in inv.script

    def test_codebase_audit_resolve(self) -> None:
        inv = resolve_builtin_workflow(
            "codebase-audit",
            {"scope": "src/", "checks": ["security", "performance"]},
        )
        assert inv is not None
        assert "codebase_audit" in inv.script

    def test_unknown_returns_none(self) -> None:
        assert resolve_builtin_workflow("nonexistent") is None

    def test_generated_scripts_are_valid_python(self) -> None:
        scripts = [
            generate_deep_research(),
            generate_adversarial_review(),
            generate_code_review(),
            generate_multi_perspective("test topic", ["a", "b"]),
        ]
        for script in scripts:
            compile(script, "<test>", "exec")


# ---------------------------------------------------------------------------
# Workflow tool
# ---------------------------------------------------------------------------


class TestWorkflowTool:
    def test_create_tool_definition(self) -> None:
        tool = create_workflow_tool()
        assert tool.name == "workflow"
        assert tool.prompt_snippet is not None
        assert len(tool.prompt_guidelines) > 0

    async def test_execute_with_script(self) -> None:
        tool = create_workflow_tool()
        result = await tool.execute(
            "tc-1",
            WorkflowParams(
                script="""
meta = {"name": "test"}
async def main():
    r = await agent("hello")
    result(r)
""",
            ),
        )
        assert "completed" in result.content[0]["text"]

    async def test_execute_with_name(self) -> None:
        tool = create_workflow_tool()
        result = await tool.execute(
            "tc-1",
            WorkflowParams(name="deep-research", args={"question": "What is Python?"}),
        )
        text = result.content[0]["text"]
        assert "completed" in text or "deep_research" in text

    async def test_execute_unknown_name(self) -> None:
        tool = create_workflow_tool()
        result = await tool.execute(
            "tc-1",
            WorkflowParams(name="nonexistent"),
        )
        assert "No built-in workflow" in result.content[0]["text"]

    async def test_execute_no_script_or_name(self) -> None:
        tool = create_workflow_tool()
        result = await tool.execute("tc-1", WorkflowParams())
        assert "requires" in result.content[0]["text"]


# ---------------------------------------------------------------------------
# Script normalization
# ---------------------------------------------------------------------------


class TestNormalizeScript:
    def test_strips_fences(self) -> None:
        s = "```python\nprint('hello')\n```"
        assert _normalize_script(s) == "print('hello')"

    def test_strips_py_fences(self) -> None:
        s = "```py\nx = 1\n```"
        assert _normalize_script(s) == "x = 1"

    def test_no_fences(self) -> None:
        s = "x = 1"
        assert _normalize_script(s) == "x = 1"


# ---------------------------------------------------------------------------
# activate() entry point
# ---------------------------------------------------------------------------


class _BridgeStub:
    """Minimal bridge stub so pi.cwd works during activate."""

    def inject_tool(self, defn):
        pass

    def remove_tool(self, name):
        pass

    def get_active_tool_names(self):
        return []

    def set_active_tool_names(self, names):
        pass

    def get_all_tool_info(self):
        return []

    def send_message(self, text):
        pass

    def trigger_prompt(self, text):
        pass

    def trigger_message(self, custom_type, text, *, details=None):
        pass

    def add_hook(self, event, handler):
        pass

    def remove_hook(self, event, handler):
        pass

    def get_custom_entries(self, custom_type):
        return []

    def register_cleanup(self, callback):
        pass

    def append_entry(self, custom_type, data):
        pass

    async def exec(self, command, **kw):
        pass

    @property
    def cwd(self):
        return "."

    @property
    def session_id(self):
        return ""

    @property
    def stream_fn(self):
        return None

    @property
    def model(self):
        return None

    @property
    def get_api_key_fn(self):
        return None

    @property
    def tool_call_gate(self):
        return None


class TestActivate:
    def test_activate_registers_tool_and_commands(self) -> None:
        from pi_dynamic_workflows import activate

        from pi_agent_core.extensions import ExtensionLoader, ExtensionRegistry
        from pi_agent_core.extensions._harness_bridge import HarnessBridge

        assert isinstance(_BridgeStub(), HarnessBridge)

        reg = ExtensionRegistry()
        loader = ExtensionLoader(reg)
        loader.load_callable(activate, name="pi-dynamic-workflows", bridge=_BridgeStub())

        tools = reg.get_tools()
        assert "workflow" in tools

        commands = reg.get_commands()
        assert "workflows" in commands
        assert "deep-research" in commands
        assert "code-review" in commands


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


class TestJournal:
    def test_append_and_replay(self) -> None:
        journal = Journal()
        h = hash_request("agent", {"prompt": "hello"})
        journal.append("agent", h, "response-1")
        assert journal.entry_count == 1

        journal2 = Journal()
        journal2._entries = list(journal._entries)
        cached = journal2.try_replay("agent", h)
        assert cached == "response-1"

    def test_replay_miss_returns_sentinel(self) -> None:
        journal = Journal()
        h = hash_request("agent", {"prompt": "hello"})
        result = journal.try_replay("agent", h)
        assert result is _MISS

    # What a miss does (nothing) and how requests are matched: test_journal_replay.py.

    def test_persistence_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        journal = Journal(path)
        h = hash_request("agent", {"prompt": "persist"})
        journal.append("agent", h, {"text": "ok"})
        assert path.exists()

        loaded = Journal.load(path)
        assert loaded.entry_count == 1
        cached = loaded.try_replay("agent", h)
        assert cached == {"text": "ok"}

    def test_hash_determinism(self) -> None:
        h1 = hash_request("agent", {"prompt": "hello", "tier": "small"})
        h2 = hash_request("agent", {"prompt": "hello", "tier": "small"})
        h3 = hash_request("agent", {"prompt": "hello", "tier": "big"})
        assert h1 == h2
        assert h1 != h3


# ---------------------------------------------------------------------------
# Journal integration with WorkflowRuntime
# ---------------------------------------------------------------------------


class TestRuntimeJournal:
    async def test_journal_caches_agent_calls(self) -> None:
        executor = RecordingExecutor()
        journal = Journal()

        runtime = WorkflowRuntime(executor, cwd="/test", journal=journal)
        script = """
meta = {"name": "journal_test"}
async def main():
    r = await agent("hello world")
    result(r)
"""
        run_result = await runtime.execute(script)
        assert run_result.agent_count == 1
        assert len(executor.calls) == 1
        assert journal.entry_count == 1

        executor2 = RecordingExecutor()
        journal2 = Journal()
        journal2._entries = list(journal._entries)

        runtime2 = WorkflowRuntime(executor2, cwd="/test", journal=journal2)
        run_result2 = await runtime2.execute(script)
        assert run_result2.agent_count == 1
        assert len(executor2.calls) == 0
        assert run_result2.result == run_result.result


# ---------------------------------------------------------------------------
# extract_meta
# ---------------------------------------------------------------------------


class TestExtractMeta:
    def test_extracts_meta_dict(self) -> None:
        script = """
meta = {
    "name": "test-wf",
    "description": "A test",
    "when_to_use": "testing",
}

async def main():
    pass
"""
        m = extract_meta(script)
        assert m["name"] == "test-wf"
        assert m["description"] == "A test"
        assert m["when_to_use"] == "testing"

    def test_missing_meta_returns_empty(self) -> None:
        assert extract_meta("x = 1") == {}

    def test_invalid_syntax_returns_empty(self) -> None:
        assert extract_meta("def (broken") == {}

    def test_non_literal_meta_returns_empty(self) -> None:
        assert extract_meta("meta = some_function()") == {}


# ---------------------------------------------------------------------------
# WorkflowStore
# ---------------------------------------------------------------------------


class TestWorkflowStore:
    def test_scan_empty(self, tmp_path: Path) -> None:
        store = WorkflowStore(cwd=str(tmp_path), home=tmp_path / "home")
        assert store.scan() == []

    def test_scan_user_dir(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        wf_dir = home / ".pi-python" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "my-review.py").write_text(
            'meta = {"name": "my-review", "description": "Review stuff"}\n'
            "async def main():\n    await agent('review')\n",
            encoding="utf-8",
        )
        store = WorkflowStore(cwd=str(tmp_path), home=home)
        workflows = store.scan()
        assert len(workflows) == 1
        assert workflows[0].name == "my-review"
        assert workflows[0].source == "user"

    def test_scan_project_dir(self, tmp_path: Path) -> None:
        proj_wf = tmp_path / ".pi-python" / "workflows"
        proj_wf.mkdir(parents=True)
        (proj_wf / "lint.py").write_text(
            'meta = {"name": "lint", "description": "Lint all"}\nasync def main(): pass\n',
            encoding="utf-8",
        )
        store = WorkflowStore(cwd=str(tmp_path), home=tmp_path / "nohome")
        workflows = store.scan()
        assert len(workflows) == 1
        assert workflows[0].name == "lint"
        assert workflows[0].source == "project"

    def test_resolve(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".pi-python" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "my-wf.py").write_text(
            'meta = {"name": "my-wf", "description": "X"}\nasync def main(): pass\n',
            encoding="utf-8",
        )
        store = WorkflowStore(cwd=str(tmp_path))
        assert store.resolve("my-wf") is not None
        assert store.resolve("nonexistent") is None

    def test_save_project(self, tmp_path: Path) -> None:
        store = WorkflowStore(cwd=str(tmp_path))
        path = store.save_project("new-wf", 'meta = {"name": "new-wf"}\nasync def main(): pass\n')
        assert path.exists()
        assert path.name == "new-wf.py"

    def test_save_rejects_bad_name(self, tmp_path: Path) -> None:
        store = WorkflowStore(cwd=str(tmp_path))
        with pytest.raises(ValueError, match="Invalid workflow name"):
            store.save_project("Bad Name!", "x = 1")

    def test_save_no_clobber(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".pi-python" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "existing.py").write_text("x = 1")
        store = WorkflowStore(cwd=str(tmp_path))
        with pytest.raises(FileExistsError):
            store.save_project("existing", "y = 2")

    def test_skips_invalid_name(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".pi-python" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "Bad Name.py").write_text('meta = {"name": "Bad Name"}\nasync def main(): pass\n')
        store = WorkflowStore(cwd=str(tmp_path))
        assert store.scan() == []


# ---------------------------------------------------------------------------
# HarnessSubagentExecutor
# ---------------------------------------------------------------------------


class TestHarnessSubagentExecutor:
    async def test_runs_prompt_returns_result(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model

        parent_model = Model(provider="mock", model_id="m1")
        executor = HarnessSubagentExecutor(
            stream_fn=mock_text_stream,
            parent_model=parent_model,
            cwd=".",
        )
        result = await executor.run_agent("Hello")
        assert result.text is not None
        assert "mock" in result.text.lower() or len(result.text) > 0
        assert result.error is None
        assert result.duration_ms >= 0

    async def test_tier_routing(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model

        parent_model = Model(provider="mock", model_id="m1")
        executor = HarnessSubagentExecutor(
            stream_fn=mock_text_stream,
            parent_model=parent_model,
            cwd=".",
            tiers={"small": "mock/fast", "big": "mock/large"},
        )
        result = await executor.run_agent("Hello", tier="small")
        assert result.error is None

    def test_tier_keeps_a_non_anthropic_parent_on_its_own_model(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.types import Model

        parent = Model(provider="deepseek", model_id="deepseek-chat", base_url="https://gw/v1")
        executor = HarnessSubagentExecutor(stream_fn=None, parent_model=parent)

        for tier in ("small", "medium", "big"):
            assert executor._resolve_model(tier, None) is parent

    def test_tier_uses_anthropic_defaults_only_for_an_anthropic_parent(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.types import Model

        parent = Model(provider="anthropic", model_id="claude-haiku", base_url="https://gw/v1")
        executor = HarnessSubagentExecutor(stream_fn=None, parent_model=parent)

        big = executor._resolve_model("big", None)

        assert (big.provider, big.model_id) == ("anthropic", "claude-opus-4-20250514")
        assert big.base_url == "https://gw/v1"  # same provider => same endpoint

    def test_model_override_on_the_same_provider_keeps_the_parent_endpoint(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.types import Model

        parent = Model(provider="openai", model_id="gpt-4o", base_url="https://gw/v1")
        executor = HarnessSubagentExecutor(stream_fn=None, parent_model=parent)

        for spec in ("gpt-4o-mini", "openai/gpt-4o-mini"):
            model = executor._resolve_model(None, spec)
            assert (model.provider, model.model_id) == ("openai", "gpt-4o-mini")
            assert model.base_url == "https://gw/v1"

    def test_model_override_on_another_provider_drops_the_parent_endpoint(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.types import Model

        parent = Model(provider="openai", model_id="gpt-4o", base_url="https://gw/v1")
        executor = HarnessSubagentExecutor(stream_fn=None, parent_model=parent)

        model = executor._resolve_model(None, "anthropic/claude-sonnet-4-20250514")

        assert (model.provider, model.model_id) == ("anthropic", "claude-sonnet-4-20250514")
        assert model.base_url is None  # the parent's gateway must not front another vendor

    async def test_subagent_asks_for_the_key_of_the_provider_it_actually_uses(self) -> None:
        """End to end: which provider a sub-agent runs on, and which key it is handed."""
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model

        seen: list[tuple[str, str, str | None]] = []

        async def recording_stream(model, context, options=None):
            seen.append((model.provider, model.model_id, options.api_key if options else None))
            return await mock_text_stream(model, context, options)

        keys = {"deepseek": "deepseek-secret"}  # provider-scoped, like make_get_api_key
        executor = HarnessSubagentExecutor(
            stream_fn=recording_stream,
            parent_model=Model(provider="deepseek", model_id="deepseek-chat"),
            cwd=".",
            get_api_key=lambda provider: keys.get(provider),
        )

        await executor.run_agent("hi", tier="small")  # must stay on the parent's provider
        await executor.run_agent("hi", model="anthropic/claude-x")  # explicit override

        assert seen == [
            ("deepseek", "deepseek-chat", "deepseek-secret"),
            ("anthropic", "claude-x", None),
        ]

    async def test_timeout(self) -> None:
        from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

        from pi_agent_core.types import Model

        async def slow_stream(model, context, options=None):
            await asyncio.sleep(10)

        parent_model = Model(provider="mock", model_id="m1")
        executor = HarnessSubagentExecutor(
            stream_fn=slow_stream,
            parent_model=parent_model,
            cwd=".",
        )
        result = await executor.run_agent("Hello", timeout_ms=50)
        assert result.error == "timeout"


# ---------------------------------------------------------------------------
# WorkflowManager (background runs)
# ---------------------------------------------------------------------------


class TestWorkflowManager:
    async def test_start_background_and_complete(self) -> None:
        from pi_dynamic_workflows.manager import WorkflowManager

        messages: list[str] = []

        class FakeBridge:
            def send_message(self, text: str) -> None:
                pass

            def trigger_prompt(self, text: str) -> None:
                pass

            def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
                messages.append(text)

            def register_cleanup(self, callback: Any) -> None:
                pass

        manager = WorkflowManager(FakeBridge())  # type: ignore[arg-type]
        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=".")

        script = (
            'meta = {"name": "bg-test"}\n'
            'async def main():\n    await agent("hi")\n    result("done")\n'
        )
        await manager.start_background(
            runtime, script, None, run_id="abc123", script_name="bg-test"
        )
        assert manager.pending_count == 1

        for _ in range(1000):  # the script runs in a process of its own: allow it time to start
            if manager.pending_count == 0:
                break
            await asyncio.sleep(0.01)
        assert manager.pending_count == 0
        assert len(messages) == 1
        assert "bg-test" in messages[0]

    async def test_cancel_all(self) -> None:
        from pi_dynamic_workflows.manager import WorkflowManager

        class FakeBridge:
            def send_message(self, text: str) -> None:
                pass

            def trigger_prompt(self, text: str) -> None:
                pass

            def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
                pass

            def register_cleanup(self, callback: Any) -> None:
                pass

        manager = WorkflowManager(FakeBridge())  # type: ignore[arg-type]

        async def slow_execute(script, args=None):
            await asyncio.sleep(100)

        executor = RecordingExecutor()
        runtime = WorkflowRuntime(executor, cwd=".")

        script = (
            'meta = {"name": "slow"}\n'
            "async def main():\n    import asyncio\n"
            "    await asyncio.sleep(100)\n"
        )

        await manager.start_background(runtime, script, None, run_id="slow001")
        assert manager.pending_count == 1
        await manager.cancel_all()
        assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# Workflow tool: background + resume params
# ---------------------------------------------------------------------------


class TestWorkflowToolExtended:
    async def test_background_returns_terminate(self) -> None:
        from pi_dynamic_workflows.manager import WorkflowManager

        class FakeBridge:
            def send_message(self, text: str) -> None:
                pass

            def trigger_prompt(self, text: str) -> None:
                pass

            def trigger_message(self, custom_type: str, text: str, *, details: Any = None) -> None:
                pass

            def register_cleanup(self, callback: Any) -> None:
                pass

        manager = WorkflowManager(FakeBridge())  # type: ignore[arg-type]
        tool = create_workflow_tool(manager=manager)

        result = await tool.execute(
            "tc-bg",
            WorkflowParams(
                script='meta = {"name": "bg"}\nasync def main():\n    await agent("x")\n',
                background=True,
            ),
        )
        assert result.terminate is True
        assert "background" in result.content[0]["text"].lower()
        await manager.shutdown(timeout=2)

    async def test_resume_from_run_id(self, tmp_path: Path) -> None:
        journal_dir = tmp_path / ".pi-python" / "workflow-journals"
        journal_dir.mkdir(parents=True)
        journal_path = journal_dir / "test-run.jsonl"

        journal = Journal(journal_path)
        h = hash_request("agent", {"prompt": "hello"})
        journal.append("agent", h, "cached-result")

        tool = create_workflow_tool()
        result = await tool.execute(
            "tc-resume",
            WorkflowParams(
                script=(
                    'meta = {"name": "resume-test"}\n'
                    "async def main():\n"
                    '    r = await agent("hello")\n'
                    "    result(r)\n"
                ),
                resume_from_run_id="test-run",
            ),
        )
        assert "resume-test" in result.content[0]["text"]


# ---------------------------------------------------------------------------
# P7R3-04: journal phase hash includes implicit phase
# ---------------------------------------------------------------------------


class TestJournalPhaseHash:
    async def test_different_implicit_phase_not_cached(self) -> None:
        """Same prompt in different phases must NOT share journal cache."""
        executor = RecordingExecutor()
        journal = Journal()

        runtime = WorkflowRuntime(executor, cwd=".", journal=journal)
        script = (
            'meta = {"name": "phase-hash"}\n'
            "async def main():\n"
            '    phase("A")\n'
            '    r1 = await agent("same-prompt")\n'
            '    phase("B")\n'
            '    r2 = await agent("same-prompt")\n'
            "    result([r1, r2])\n"
        )
        run_result = await runtime.execute(script)
        assert run_result.agent_count == 2
        assert len(executor.calls) == 2

    async def test_same_phase_is_cached_on_replay(self) -> None:
        """Same prompt + same phase should be replayed from journal."""
        executor1 = RecordingExecutor()
        journal = Journal()

        runtime1 = WorkflowRuntime(executor1, cwd=".", journal=journal)
        script = (
            'meta = {"name": "replay"}\n'
            "async def main():\n"
            '    phase("X")\n'
            '    r = await agent("hello")\n'
            "    result(r)\n"
        )
        run_result1 = await runtime1.execute(script)
        assert len(executor1.calls) == 1

        executor2 = RecordingExecutor()
        journal2 = Journal()
        journal2._entries = list(journal._entries)
        runtime2 = WorkflowRuntime(executor2, cwd=".", journal=journal2)
        run_result2 = await runtime2.execute(script)
        assert len(executor2.calls) == 0
        assert run_result2.result == run_result1.result


# ---------------------------------------------------------------------------
# P7R3-05: resume_from_run_id path traversal validation
# ---------------------------------------------------------------------------


class TestRunIdValidation:
    async def test_invalid_run_id_rejected(self) -> None:
        tool = create_workflow_tool()
        result = await tool.execute(
            "tc-invalid",
            WorkflowParams(
                script='meta = {"name": "x"}\nasync def main(): pass\n',
                resume_from_run_id="../../etc/passwd",
            ),
        )
        assert "Invalid" in result.content[0]["text"]

    async def test_valid_run_id_accepted(self) -> None:
        from pi_dynamic_workflows.workflow_tool import _RUN_ID_RE

        assert _RUN_ID_RE.match("abc123def456")
        assert _RUN_ID_RE.match("test-run")
        assert not _RUN_ID_RE.match("../escape")
        assert not _RUN_ID_RE.match("")


# ---------------------------------------------------------------------------
# P7R3-06: 64MB journal file-size limit enforced
# ---------------------------------------------------------------------------


class TestJournalFileLimit:
    def test_append_respects_max_file_bytes(self, tmp_path: Path) -> None:
        from pi_dynamic_workflows.journal import MAX_FILE_BYTES

        path = tmp_path / "j.jsonl"
        journal = Journal(path)

        journal.append("agent", "h1", "small")
        assert path.exists()

        big_result = "x" * (MAX_FILE_BYTES + 1)
        journal.append("agent", "h2", big_result)
        assert journal.entry_count == 1


# ---------------------------------------------------------------------------
# P7R3-07: background=True without manager returns error
# ---------------------------------------------------------------------------


class TestBackgroundNoManager:
    async def test_background_without_manager_returns_error(self) -> None:
        tool = create_workflow_tool(manager=None)
        result = await tool.execute(
            "tc-no-mgr",
            WorkflowParams(
                script='meta = {"name": "bg"}\nasync def main():\n    await agent("x")\n',
                background=True,
            ),
        )
        assert "Background workflows require" in result.content[0]["text"]
        assert result.terminate is None


# ---------------------------------------------------------------------------
# P7R3-08: project workflows override user on name collision
# ---------------------------------------------------------------------------


class TestWorkflowStorePriority:
    def test_project_overrides_user(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        user_dir = home / ".pi-python" / "workflows"
        user_dir.mkdir(parents=True)
        (user_dir / "my-wf.py").write_text(
            'meta = {"name": "my-wf", "description": "user version"}\nasync def main(): pass\n',
            encoding="utf-8",
        )

        proj_dir = tmp_path / ".pi-python" / "workflows"
        proj_dir.mkdir(parents=True)
        (proj_dir / "my-wf.py").write_text(
            'meta = {"name": "my-wf", "description": "project version"}\nasync def main(): pass\n',
            encoding="utf-8",
        )

        store = WorkflowStore(cwd=str(tmp_path), home=home)
        workflows = store.scan()
        names = [wf.name for wf in workflows]
        assert names.count("my-wf") == 1
        wf = next(w for w in workflows if w.name == "my-wf")
        assert wf.source == "project"
        assert wf.description == "project version"


# ---------------------------------------------------------------------------
# P7R3-09: WorkflowStore atomic save failure cleans up
# ---------------------------------------------------------------------------


class TestWorkflowStoreSaveFailure:
    def test_link_failure_cleans_tmp(self, tmp_path: Path, monkeypatch: Any) -> None:
        store = WorkflowStore(cwd=str(tmp_path))
        proj_dir = tmp_path / ".pi-python" / "workflows"
        proj_dir.mkdir(parents=True)

        def failing_link(src: Any, dst: Any) -> None:
            raise OSError("simulated link failure")

        monkeypatch.setattr(os, "link", failing_link)

        with pytest.raises(OSError, match="simulated"):
            store.save_project("test-wf", 'meta = {"name": "test-wf"}\nasync def main(): pass\n')

        tmp_files = list(proj_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Temporary files not cleaned: {tmp_files}"


# ---------------------------------------------------------------------------
# P7R4-07: WorkflowStore no-clobber is TOCTOU-free (uses os.link)
# ---------------------------------------------------------------------------


class TestWorkflowStoreNoClobberAtomic:
    def test_concurrent_save_does_not_clobber(self, tmp_path: Path) -> None:
        """Two saves of the same name: one succeeds, one gets FileExistsError."""
        store = WorkflowStore(cwd=str(tmp_path))
        script_a = 'meta = {"name": "race"}\nasync def main(): pass\n'
        script_b = 'meta = {"name": "race"}\nasync def main(): result(1)\n'

        store.save_project("race", script_a)

        with pytest.raises(FileExistsError):
            store.save_project("race", script_b)

        saved = (tmp_path / ".pi-python" / "workflows" / "race.py").read_text()
        assert saved == script_a


# ---------------------------------------------------------------------------
# P7R6-01: worktree snapshot baseline — collect_diff returns only agent delta
# ---------------------------------------------------------------------------


class TestWorktreeSnapshotBaseline:
    """Verify that snapshot mode commits a baseline so apply_changes
    only writes back the agent's delta, not the pre-existing dirty state."""

    @pytest.fixture()
    def git_repo(self, tmp_path: Path) -> Path:
        """Create a minimal git repo with one committed file."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        (repo / "a.txt").write_text("original\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        return repo

    @pytest.mark.asyncio()
    async def test_dirty_source_apply_succeeds(self, git_repo: Path) -> None:
        """When source has dirty changes, apply_changes must still succeed."""
        import uuid

        from pi_dynamic_workflows.worktree import WorktreeManager

        (git_repo / "a.txt").write_text("dirty\n")

        mgr = WorktreeManager(str(git_repo))
        wt = await mgr.create(session_id=f"dirty-{uuid.uuid4().hex[:8]}")

        assert (Path(wt) / "a.txt").read_text() == "dirty\n"

        (Path(wt) / "a.txt").write_text("agent-change\n")

        diff = await mgr.collect_diff(wt)
        assert b"agent-change" in diff
        assert b"original" not in diff

        await mgr.apply_changes(wt)
        assert (git_repo / "a.txt").read_text() == "agent-change\n"

        await mgr.cleanup(wt)

    @pytest.mark.asyncio()
    async def test_clean_source_apply_succeeds(self, git_repo: Path) -> None:
        """Clean source (no dirty state) still works correctly."""
        import uuid

        from pi_dynamic_workflows.worktree import WorktreeManager

        mgr = WorktreeManager(str(git_repo))
        wt = await mgr.create(session_id=f"clean-{uuid.uuid4().hex[:8]}")

        (Path(wt) / "a.txt").write_text("agent-only\n")

        diff = await mgr.collect_diff(wt)
        assert b"agent-only" in diff

        await mgr.apply_changes(wt)
        assert (git_repo / "a.txt").read_text() == "agent-only\n"

        await mgr.cleanup(wt)


# ---------------------------------------------------------------------------
# Sub-agent tool calls go through the parent's tool_call gate (audit P7-01)
# ---------------------------------------------------------------------------
#
# Each sub-agent runs on a fresh harness with no hooks, so the session's permission
# layer never saw its bash/edit/write calls. The executor takes the parent's
# ``tool_call_gate`` and puts every sub-agent tool call through it.

_ONE_AGENT = (
    'meta = {"name": "one"}\nasync def main():\n    await agent("go")\n    result("done")\n'
)


def _write_once_stream(path: str, replies: list[tuple[bool, str]] | None = None):
    """A sub-agent LLM that asks to write ``path`` once (call id ``call_1``), then answers.

    ``replies`` collects what the sub-agent was told about its call: ``(isError, text)``.
    """
    from pi_agent_core.event_stream import AssistantMessageEventStream
    from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
    from pi_agent_core.types import DoneEvent, StartEvent

    async def stream(model, context, options=None):
        results = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
        if results:
            if replies is not None:
                replies.extend((r.isError, r.content[0]["text"]) for r in results)
            return await mock_text_stream(model, context, options)
        call = {
            "type": "toolCall",
            "id": "call_1",
            "name": "write",
            "arguments": {"path": path, "content": "written\n"},
        }
        partial = _base_partial(model, [call])
        partial.stopReason = "toolUse"
        events = AssistantMessageEventStream()
        events.push(StartEvent(partial=partial.model_copy(deep=True)))
        events.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        events.set_final_message(partial)
        events.end()
        return events

    return stream


def _gated_executor(stream_fn: Any, cwd: Path, gate: Any = None) -> Any:
    from pi_dynamic_workflows.subagent import HarnessSubagentExecutor

    from pi_agent_core.types import Model

    return HarnessSubagentExecutor(
        stream_fn=stream_fn,
        parent_model=Model(provider="mock", model_id="m1"),
        cwd=str(cwd),
        tool_call_gate=gate,
    )


class TestSubagentToolCallGate:
    async def test_a_blocked_call_never_runs_and_the_subagent_is_told(self, tmp_path: Path):
        target = tmp_path / "blocked.txt"
        replies: list[tuple[bool, str]] = []
        calls: list[tuple[str, str, dict[str, Any], Any]] = []

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            calls.append((tool_call_id, tool_name, tool_input, origin))
            return {"block": True, "reason": "User denied permission"}

        executor = _gated_executor(_write_once_stream(str(target), replies), tmp_path, gate)
        result = await executor.run_agent("write it", label="writer")

        assert result.error is None
        assert not target.exists()
        ((is_error, text),) = replies
        assert is_error is True
        assert "User denied permission" in text
        ((_, tool_name, tool_input, origin),) = calls
        assert tool_name == "write"
        assert tool_input["path"] == str(target)
        assert origin == {"kind": "subagent", "cwd": str(tmp_path), "label": "writer"}

    async def test_an_allowed_call_runs(self, tmp_path: Path):
        target = tmp_path / "allowed.txt"
        calls: list[str] = []

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            calls.append(tool_name)
            return None

        executor = _gated_executor(_write_once_stream(str(target)), tmp_path, gate)
        result = await executor.run_agent("write it")

        assert result.error is None
        assert calls == ["write"]
        assert target.read_text(encoding="utf-8") == "written\n"

    async def test_the_origin_carries_no_label_when_the_script_gave_none(self, tmp_path: Path):
        origins: list[Any] = []

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            origins.append(origin)
            return None

        executor = _gated_executor(_write_once_stream(str(tmp_path / "x.txt")), tmp_path, gate)
        await executor.run_agent("write it")

        assert origins == [{"kind": "subagent", "cwd": str(tmp_path)}]

    async def test_without_a_gate_there_is_no_policy_to_inherit(self, tmp_path: Path):
        """Library default for a standalone executor; the extension always passes the gate."""
        target = tmp_path / "free.txt"

        executor = _gated_executor(_write_once_stream(str(target)), tmp_path)
        await executor.run_agent("write it")

        assert target.read_text(encoding="utf-8") == "written\n"

    async def test_a_failing_gate_fails_closed(self, tmp_path: Path):
        target = tmp_path / "unknown.txt"
        replies: list[tuple[bool, str]] = []

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            raise RuntimeError("permission backend down")

        executor = _gated_executor(_write_once_stream(str(target), replies), tmp_path, gate)
        await executor.run_agent("write it")

        assert not target.exists()  # no verdict is not a yes
        assert [is_error for is_error, _ in replies] == [True]

    async def test_tool_call_ids_do_not_clash_across_subagent_runs(self, tmp_path: Path):
        """Every sub-agent LLM here calls its tool ``call_1``; parallel runs and the parent
        session would all share that id, and permission cards are keyed by it."""
        ids: list[str] = []

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            ids.append(tool_call_id)
            return {"block": True, "reason": "no"}

        stream = _write_once_stream(str(tmp_path / "x.txt"))
        executor = _gated_executor(stream, tmp_path, gate)
        await asyncio.gather(executor.run_agent("a"), executor.run_agent("b"))

        assert len(ids) == 2
        assert len(set(ids)) == 2
        assert "call_1" not in ids
        assert all(i.endswith("call_1") for i in ids)  # the model's own id is still visible

    def test_with_worktree_manager_keeps_every_setting_including_the_gate(self):
        from pi_agent_core.types import Model

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            return None

        base = _gated_executor(None, Path("/w"), gate)
        base._get_api_key = lambda provider: "k"
        base._tiers = {"small": "mock/x"}
        base._parent_model = Model(provider="mock", model_id="m1")
        manager = object()

        derived = base.with_worktree_manager(manager)

        assert derived is not base
        assert derived._worktree_manager is manager
        assert base._worktree_manager is None
        for name in ("_stream_fn", "_parent_model", "_cwd", "_get_api_key", "_tiers"):
            assert getattr(derived, name) is getattr(base, name), name
        assert derived._tool_call_gate is gate

    async def test_isolated_subagents_still_go_through_the_gate(self, tmp_path: Path):
        """``isolation=True`` rebuilds the executor around a worktree manager; the gate
        must survive that (it is a security control, not a tuning knob)."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        for args in (
            ["init"],
            ["config", "user.email", "test@test"],
            ["config", "user.name", "test"],
        ):
            subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)
        (repo / "a.txt").write_text("original\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True
        )

        calls: list[tuple[str, Any]] = []

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            calls.append((tool_name, origin))
            return {"block": True, "reason": "User denied permission"}

        executor = _gated_executor(_write_once_stream("note.txt"), repo, gate)
        tool = create_workflow_tool(executor=executor, cwd=str(repo))
        result = await tool.execute("tc-1", WorkflowParams(script=_ONE_AGENT, isolation=True))

        assert "completed" in result.content[0]["text"]
        assert [name for name, _ in calls] == ["write"]
        assert calls[0][1]["cwd"] != str(repo)  # it ran in the worktree, and was still asked
        assert not (repo / "note.txt").exists()


class TestExecutorGateWiring:
    @staticmethod
    def _pi(bridge: Any) -> Any:
        class _Pi:
            cwd = "."

            def _require_bridge(self):
                return bridge

        return _Pi()

    def test_the_extension_hands_the_bridges_gate_to_the_executor(self):
        from pi_dynamic_workflows import _try_build_real_executor

        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model

        async def gate(tool_call_id, tool_name, tool_input, *, origin=None):
            return None

        class _GatedBridge(_BridgeStub):
            @property
            def stream_fn(self):
                return mock_text_stream

            @property
            def model(self):
                return Model(provider="mock", model_id="m1")

            @property
            def tool_call_gate(self):
                return gate

        executor, _manager = _try_build_real_executor(self._pi(_GatedBridge()))

        assert executor is not None
        assert executor._tool_call_gate is gate

    def test_a_bridge_without_a_gate_is_reported(self, caplog: pytest.LogCaptureFixture):
        """An old or third-party harness has nothing to inherit; say so instead of
        silently running sub-agents outside the session's policy."""
        import logging

        from pi_dynamic_workflows import _try_build_real_executor

        from pi_agent_core.tests.mock_stream import mock_text_stream
        from pi_agent_core.types import Model

        class _NoGateBridge:
            stream_fn = staticmethod(mock_text_stream)
            model = Model(provider="mock", model_id="m1")
            get_api_key_fn = None

            def register_cleanup(self, callback):
                pass

        with caplog.at_level(logging.WARNING, logger="pi_dynamic_workflows"):
            executor, _ = _try_build_real_executor(self._pi(_NoGateBridge()))

        assert executor is not None
        assert executor._tool_call_gate is None
        assert any("tool_call_gate" in record.getMessage() for record in caplog.records)
