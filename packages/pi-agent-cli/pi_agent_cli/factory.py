"""Build an AgentHarness bound to coding tools and a JSONL session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pi_agent_cli.config import CliConfig, expand_config_path, make_get_api_key, pi_home
from pi_agent_cli.create_harness import build_coding_agent_harness_system_prompt
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_core.coding_tools import create_all_tools
from pi_agent_core.coding_tools.bash import create_bash_tool
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


def _build_tools(
    *,
    cwd: str,
    session_id: str,
    session_file: str,
    harness_holder: dict[str, AgentHarness | None],
) -> list[Any]:
    def prepare_pi_env() -> dict[str, str]:
        harness = harness_holder.get("harness")
        if harness is None:
            return {}
        env: dict[str, str] = {
            "PI_SESSION_ID": session_id,
            "PI_SESSION_FILE": session_file,
            "PI_PROVIDER": harness.model.provider,
            "PI_MODEL": harness.model.model_id,
        }
        if harness.thinking_level:
            env["PI_REASONING_LEVEL"] = str(harness.thinking_level)
        return env

    tools_dict = create_all_tools(cwd)
    tools_dict["bash"] = create_bash_tool(
        cwd,
        expose_session_environment=True,
        prepare_env=prepare_pi_env,
    )
    return list(tools_dict.values())


async def create_session_harness(
    *,
    session: Session,
    cwd: str | Path,
    config: CliConfig,
    stream_fn: StreamFn,
    resources: AgentHarnessResources | None = None,
    on_tool_call: Callable[[Any], Any | Awaitable[Any]] | None = None,
    home: Path | None = None,
) -> AgentHarness:
    cwd_s = str(Path(normalize_host_path(str(cwd))).resolve())
    home_path = pi_home(home)
    metadata = await session.get_metadata()
    session_id = metadata.id
    session_file = str(getattr(metadata, "path", "") or "")

    harness_holder: dict[str, AgentHarness | None] = {"harness": None}
    tools = _build_tools(
        cwd=cwd_s,
        session_id=session_id,
        session_file=session_file,
        harness_holder=harness_holder,
    )
    resolved_resources = resources or AgentHarnessResources()
    model = Model(
        provider=config.provider,
        model_id=config.model_id,
        base_url=config.base_url,
    )

    async def system_prompt_callback(ctx: dict[str, Any]) -> str:
        active_tools = ctx.get("active_tools") or tools
        active_names = [tool.name for tool in active_tools]
        all_tools = list(ctx.get("tools") or tools)
        options = load_system_prompt_options(
            cwd=cwd_s,
            config=config,
            resources=ctx.get("resources") or resolved_resources,
            home=home_path,
        )
        return build_coding_agent_harness_system_prompt(
            cwd=cwd_s,
            tools=all_tools,
            active_tool_names=active_names,
            system_prompt_options=options,
        )

    harness = AgentHarness(
        session=session,
        model=model,
        stream_fn=stream_fn,
        env=LocalExecutionEnv(cwd_s),
        tools=tools,
        resources=resolved_resources,
        get_api_key=make_get_api_key(config),
        system_prompt=system_prompt_callback,
        thinking_level=config.thinking_level,
        max_turns=config.max_turns,
    )
    harness_holder["harness"] = harness
    if on_tool_call is not None:
        harness.on("tool_call", on_tool_call)
    return harness
