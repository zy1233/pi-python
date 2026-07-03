"""Tests for the LangChain BaseTool -> AgentTool adapter."""

from __future__ import annotations

from typing import Annotated

import pytest
from langchain_core.tools import InjectedToolCallId, ToolException, tool
from pydantic import BaseModel

from pi_agent_core.adapters.langchain_tools import from_langchain_tool, from_langchain_tools
from pi_agent_core.validation import validate_tool_arguments


class _Signal:
    def __init__(self, aborted: bool = False):
        self.aborted = aborted


@tool
def add_numbers(a: int, b: int) -> str:
    """Add two numbers."""
    return str(a + b)


@tool
async def async_echo(text: str) -> str:
    """Echo text asynchronously."""
    return text.upper()


@tool
def echo_call_id(x: int, tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
    """Echo the injected tool call id."""
    return f"{x}:{tool_call_id}"


@tool(response_format="content_and_artifact")
def with_artifact(q: str) -> tuple[str, dict]:
    """Return content plus a machine-readable artifact."""
    return f"summary for {q}", {"rows": [1, 2, 3]}


@tool
def block_output() -> list:
    """Return LangChain content blocks including images."""
    return [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "image", "base64": "BBBB", "mime_type": "image/jpeg"},
    ]


@tool
def hard_failure() -> str:
    """Always raises."""
    raise ToolException("kaboom")


@tool
def soft_failure() -> str:
    """Raises, but the LangChain tool swallows it into an error ToolMessage."""
    raise ToolException("soft kaboom")


soft_failure.handle_tool_error = True


# --- schema extraction ---


def test_schema_comes_from_tool_call_schema():
    wrapped = from_langchain_tool(add_numbers)
    assert wrapped.name == "add_numbers"
    assert wrapped.label == "add_numbers"
    assert wrapped.description == "Add two numbers."
    params = wrapped.parameters
    assert isinstance(params, type) and issubclass(params, BaseModel)
    assert set(params.model_json_schema()["properties"]) == {"a", "b"}


def test_schema_excludes_injected_arguments():
    wrapped = from_langchain_tool(echo_call_id)
    schema = wrapped.parameters.model_json_schema()
    assert set(schema["properties"]) == {"x"}


def test_protocol_surface():
    wrapped = from_langchain_tool(add_numbers)
    assert wrapped.execution_mode is None
    assert wrapped.prepare_arguments is None


def test_from_langchain_tools_plural():
    wrapped = from_langchain_tools([add_numbers, with_artifact])
    assert [t.name for t in wrapped] == ["add_numbers", "with_artifact"]


# --- execution & result normalization ---


async def test_execute_returns_single_text_block():
    wrapped = from_langchain_tool(add_numbers)
    result = await wrapped.execute("t1", {"a": 2, "b": 3})
    assert result.content == [{"type": "text", "text": "5"}]
    assert result.details is None


async def test_validated_pydantic_params_end_to_end():
    wrapped = from_langchain_tool(add_numbers)
    tool_call = {"id": "1", "name": "add_numbers", "arguments": {"a": 40, "b": 2}}
    params = validate_tool_arguments(wrapped, tool_call)
    assert isinstance(params, BaseModel)
    result = await wrapped.execute("1", params)
    assert result.content == [{"type": "text", "text": "42"}]


async def test_async_tool():
    wrapped = from_langchain_tool(async_echo)
    result = await wrapped.execute("t1", {"text": "hi"})
    assert result.content == [{"type": "text", "text": "HI"}]


async def test_injected_tool_call_id_is_filled_from_execute():
    wrapped = from_langchain_tool(echo_call_id)
    result = await wrapped.execute("call_42", {"x": 5})
    assert result.content == [{"type": "text", "text": "5:call_42"}]


async def test_artifact_lands_in_details():
    wrapped = from_langchain_tool(with_artifact)
    result = await wrapped.execute("t1", {"q": "sales"})
    assert result.content == [{"type": "text", "text": "summary for sales"}]
    assert result.details == {"artifact": {"rows": [1, 2, 3]}}


async def test_content_blocks_map_to_text_and_image():
    wrapped = from_langchain_tool(block_output)
    result = await wrapped.execute("t1", {})
    assert result.content == [
        {"type": "text", "text": "hi"},
        {"type": "image", "data": "AAAA", "mimeType": "image/png"},
        {"type": "image", "data": "BBBB", "mimeType": "image/jpeg"},
    ]


# --- errors & abort ---


async def test_tool_exception_bubbles():
    wrapped = from_langchain_tool(hard_failure)
    with pytest.raises(ToolException, match="kaboom"):
        await wrapped.execute("t1", {})


async def test_handled_tool_error_message_reraises():
    wrapped = from_langchain_tool(soft_failure)
    with pytest.raises(RuntimeError, match="soft kaboom"):
        await wrapped.execute("t1", {})


async def test_aborted_signal_raises_before_invoking():
    wrapped = from_langchain_tool(add_numbers)
    with pytest.raises(RuntimeError, match="Operation aborted"):
        await wrapped.execute("t1", {"a": 1, "b": 2}, signal=_Signal(aborted=True))
