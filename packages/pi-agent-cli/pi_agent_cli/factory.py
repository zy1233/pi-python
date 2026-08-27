"""Build an AgentHarness bound to coding tools and a JSONL session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pi_agent_cli.config import CliConfig, expand_config_path, make_get_api_key
from pi_agent_cli.prompt import CODING_SYSTEM_PROMPT
from pi_agent_core.coding_tools import create_all_tools
from pi_agent_core.coding_tools.path_utils import normalize_host_path
from pi_agent_core.types import Model, StreamFn
from pi_agent_harness import AgentHarness, AgentHarnessResources, LocalExecutionEnv, Session
from pi_agent_harness.skills import load_skills


def default_stream_fn() -> StreamFn:
    import os

    if os.environ.get("PI_USE_MOCK") == "1":
        from pi_agent_core.tests.mock_stream import mock_text_stream

        return mock_text_stream
    from pi_agent_core.adapters.langchain_stream import langchain_stream

    return langchain_stream


async def load_session_resources(*, cwd: str | Path, config: CliConfig) -> AgentHarnessResources:
    if not config.skills_dirs:
        return AgentHarnessResources()
    cwd_s = str(Path(normalize_host_path(str(cwd))).resolve())
    env = LocalExecutionEnv(cwd_s)
    paths = [expand_config_path(item, cwd=cwd_s) for item in config.skills_dirs]
    result = await load_skills(env, paths)
    return AgentHarnessResources(skills=result.skills)


def create_session_harness(
    *,
    session: Session,
    cwd: str | Path,
    config: CliConfig,
    stream_fn: StreamFn,
    resources: AgentHarnessResources | None = None,
    on_tool_call: Callable[[Any], Any | Awaitable[Any]] | None = None,
) -> AgentHarness:
    cwd_s = str(Path(normalize_host_path(str(cwd))).resolve())
    tools = list(create_all_tools(cwd_s).values())
    model = Model(
        provider=config.provider,
        model_id=config.model_id,
        base_url=config.base_url,
    )
    harness = AgentHarness(
        session=session,
        model=model,
        stream_fn=stream_fn,
        env=LocalExecutionEnv(cwd_s),
        tools=tools,
        resources=resources or AgentHarnessResources(),
        get_api_key=make_get_api_key(config),
        system_prompt=CODING_SYSTEM_PROMPT,
        thinking_level=config.thinking_level,
        max_turns=config.max_turns,
    )
    if on_tool_call is not None:
        harness.on("tool_call", on_tool_call)
    return harness
