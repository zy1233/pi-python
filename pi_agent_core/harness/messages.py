"""Harness-specific message roles and conversion to core LLM messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from pi_agent_core.messages import ImageContent, Message, TextContent, UserMessage
from pi_agent_core.types import AgentMessage

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:\n\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n"

BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back from:\n\n"
)
BRANCH_SUMMARY_SUFFIX = "\n"


class BashExecutionMessage(BaseModel):
    role: Literal["bashExecution"] = "bashExecution"
    command: str
    output: str = ""
    exitCode: int | None = None
    cancelled: bool = False
    truncated: bool = False
    fullOutputPath: str | None = None
    timestamp: int
    excludeFromContext: bool = False


class CustomMessage(BaseModel):
    role: Literal["custom"] = "custom"
    customType: str
    content: str | list[TextContent | ImageContent]
    display: bool
    details: Any = None
    timestamp: int


class BranchSummaryMessage(BaseModel):
    role: Literal["branchSummary"] = "branchSummary"
    summary: str
    fromId: str
    timestamp: int


class CompactionSummaryMessage(BaseModel):
    role: Literal["compactionSummary"] = "compactionSummary"
    summary: str
    tokensBefore: int
    timestamp: int


def bash_execution_to_text(message: BashExecutionMessage) -> str:
    text = f"Ran `{message.command}`\n"
    if message.output:
        text += f"```\n{message.output}\n```"
    else:
        text += "(no output)"
    if message.cancelled:
        text += "\n\n(command cancelled)"
    elif message.exitCode is not None and message.exitCode != 0:
        text += f"\n\nCommand exited with code {message.exitCode}"
    if message.truncated and message.fullOutputPath:
        text += f"\n\n[Output truncated. Full output: {message.fullOutputPath}]"
    return text


def _text_message(content: str, timestamp: int) -> UserMessage:
    return UserMessage(content=[{"type": "text", "text": content}], timestamp=timestamp)


def _custom_content_to_user_content(content: str | list[TextContent | ImageContent]) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)


def harness_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Convert harness custom roles to ordinary core messages.

    This is the H2 landing point for the audit C4 custom AgentMessage protocol:
    core stays permissive, while harness gives the known custom roles a typed
    conversion boundary.
    """
    result: list[Message] = []
    for message in messages:
        role = getattr(message, "role", None)
        if role == "bashExecution":
            bash = BashExecutionMessage.model_validate(message)
            if not bash.excludeFromContext:
                result.append(_text_message(bash_execution_to_text(bash), bash.timestamp))
        elif role == "custom":
            custom = CustomMessage.model_validate(message)
            result.append(
                UserMessage(
                    content=_custom_content_to_user_content(custom.content),
                    timestamp=custom.timestamp,
                )
            )
        elif role == "branchSummary":
            summary = BranchSummaryMessage.model_validate(message)
            result.append(
                _text_message(
                    BRANCH_SUMMARY_PREFIX + summary.summary + BRANCH_SUMMARY_SUFFIX,
                    summary.timestamp,
                )
            )
        elif role == "compactionSummary":
            summary = CompactionSummaryMessage.model_validate(message)
            result.append(
                _text_message(
                    COMPACTION_SUMMARY_PREFIX + summary.summary + COMPACTION_SUMMARY_SUFFIX,
                    summary.timestamp,
                )
            )
        elif role in ("user", "assistant", "toolResult"):
            result.append(message)
    return result
