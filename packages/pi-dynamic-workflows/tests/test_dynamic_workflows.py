"""Tests for pi-dynamic-workflows extension."""

from __future__ import annotations

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
from pi_dynamic_workflows.workflow_tool import (
    WorkflowParams,
    _normalize_script,
    create_workflow_tool,
)

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
        assert resolve_tier("small") is not None
        assert resolve_tier("big") is not None
        assert resolve_tier("unknown") is None
        assert resolve_tier(None) is None


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


class TestActivate:
    def test_activate_registers_tool_and_commands(self) -> None:
        from pi_dynamic_workflows import activate

        from pi_agent_core.extensions import ExtensionAPI, ExtensionRegistry
        from pi_agent_core.extensions.types import ExtensionMeta

        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="pi-dynamic-workflows"))
        activate(api)

        tools = reg.get_tools()
        assert "workflow" in tools

        commands = reg.get_commands()
        assert "workflows" in commands
        assert "deep-research" in commands
        assert "code-review" in commands
