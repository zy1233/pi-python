"""Build an AgentHarness bound to coding tools and a JSONL session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from pi_agent_cli.config import (
    CliConfig,
    ModelChoice,
    api_key_getter,
    expand_config_path,
    is_project_relative_path,
    pi_home,
)
from pi_agent_cli.create_harness import build_coding_agent_harness_system_prompt
from pi_agent_cli.extension_trust import ProjectTrust, project_extensions_trusted
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_core.coding_tools import create_all_tools
from pi_agent_core.coding_tools.bash import create_bash_tool
from pi_agent_core.coding_tools.path_utils import normalize_host_path
from pi_agent_core.types import Model, StreamFn
from pi_agent_harness import AgentHarness, AgentHarnessResources, LocalExecutionEnv, Session
from pi_agent_harness.compaction import CompactionSettings
from pi_agent_harness.skills import load_skills


def _detect_vlm_support(provider: str, model_id: str, configured: bool | None = None) -> bool:
    """Auto-detect vision (VLM) support based on provider and model name.

    If ``configured`` is explicitly provided, it takes precedence.
    Known text-only providers (DeepSeek, SiliconFlow via DeepSeek) default to
    False unless the model name contains a VLM indicator (``vl``, ``vision``,
    ``4.1flash``, ``omni``).
    All other providers default to True (OpenAI, Anthropic, etc.).
    """
    if configured is not None:
        return configured
    _TEXT_ONLY_PROVIDERS = {"deepseek", "siliconflow"}
    mid = model_id.lower()
    if provider.lower() in _TEXT_ONLY_PROVIDERS:
        return any(kw in mid for kw in ("vl", "vision", "4.1flash", "omni"))
    return True


def model_for_choice(choice: ModelChoice, *, reasoning: bool = False) -> Model:
    """Harness ``Model`` for a resolved ``ModelChoice``.

    The choice is the ``[model]`` default or a ``[[models]]`` entry. ``reasoning`` (whether
    the model can think, ``CliConfig.model_reasoning``) is a property of the whole session
    configuration, so every choice gets the same value.
    """
    provider = choice.provider or "mock"
    return Model(
        provider=provider,
        model_id=choice.id,
        base_url=choice.base_url,
        supports_images=_detect_vlm_support(provider, choice.id, choice.supports_images),
        reasoning=reasoning,
    )


def api_key_for_choice(choice: ModelChoice) -> Callable[[str], str | None] | None:
    """``get_api_key`` callback for a resolved ``ModelChoice``, scoped to its provider."""
    return api_key_getter(choice.api_key_env, choice.provider or "mock")


def default_stream_fn() -> StreamFn:
    import os

    if os.environ.get("PI_USE_MOCK") == "1":
        from pi_agent_core.tests.mock_stream import mock_text_stream

        return mock_text_stream
    from pi_agent_core.adapters.langchain_stream import langchain_stream

    return langchain_stream


async def load_session_resources(
    *, cwd: str | Path, config: CliConfig, trusted: bool | None = None
) -> AgentHarnessResources:
    if not config.skills_dirs:
        return AgentHarnessResources()
    cwd_s = str(Path(normalize_host_path(str(cwd))).resolve())
    # An entry relative to the project (".pi/skills") finds the project's own skills, and a
    # skill's text goes into the system prompt: only for a trusted project. Absolute and
    # ``~`` entries are the user's own. The session says whether the project is trusted (the
    # user may have said yes in a prompt); without that the configuration decides.
    if trusted is None:
        trusted = project_extensions_trusted(config, cwd_s)
    entries = [item for item in config.skills_dirs if trusted or not is_project_relative_path(item)]
    if not entries:
        return AgentHarnessResources()
    env = LocalExecutionEnv(cwd_s)
    paths = [expand_config_path(item, cwd=cwd_s) for item in entries]
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


def _merge_tools_by_name(*groups: Iterable[Any]) -> list[Any]:
    """Union of tool groups keyed by name; a later group wins on a name collision."""
    merged: dict[str, Any] = {}
    for group in groups:
        for tool in group:
            merged[tool.name] = tool
    return list(merged.values())


async def create_session_harness(
    *,
    session: Session,
    cwd: str | Path,
    config: CliConfig,
    stream_fn: StreamFn,
    resources: AgentHarnessResources | None = None,
    on_tool_call: Callable[[Any], Any | Awaitable[Any]] | None = None,
    home: Path | None = None,
    tools: list[Any] | None = None,
    extensions: list[Any] | None = None,
    trust: ProjectTrust | None = None,
    model_choice: ModelChoice | None = None,
) -> AgentHarness:
    """Build the harness for one session.

    *trust*: whether the project's own extensions, saved workflows, prompt files and skills may
    be used. It is read again whenever the system prompt is built, so an answer given after
    the session started takes effect on the next turn (extensions are the exception: they load
    once, so the caller passes the answer on with ``AgentHarness.set_trust_project_extensions``
    before that; the workflows follow it, being read by the extension that turns them into
    commands as it loads). Without one, the configuration decides.

    *model_choice*: the model to start with, for a session that was last used with another
    one (``/model``). Without one, the ``[model]`` table decides.
    """
    cwd_s = str(Path(normalize_host_path(str(cwd))).resolve())
    home_path = pi_home(home)
    metadata = await session.get_metadata()
    session_id = metadata.id
    session_file = str(getattr(metadata, "path", "") or "")

    harness_holder: dict[str, AgentHarness | None] = {"harness": None}
    tools_list = (
        tools
        if tools is not None
        else _build_tools(
            cwd=cwd_s,
            session_id=session_id,
            session_file=session_file,
            harness_holder=harness_holder,
        )
    )
    resolved_resources = resources or AgentHarnessResources()
    choice = model_choice or config.default_choice()
    model = model_for_choice(choice, reasoning=config.model_reasoning)

    async def system_prompt_callback(ctx: dict[str, Any]) -> str:
        active_tools = ctx.get("active_tools") or tools_list
        active_names = [tool.name for tool in active_tools]
        # Prompt snippets/guidelines are looked up by name in this table. Extension tools
        # are registered after `tools_list` is fixed and the harness passes no "tools" key,
        # so they only exist in `active_tools`; without them every extension tool would be
        # exposed to the LLM but silently missing from the prompt.
        all_tools = _merge_tools_by_name(tools_list, ctx.get("tools") or (), active_tools)
        # Reads prompt files and runs `git` (up to a few seconds on a slow filesystem). On the
        # event loop that froze everything else in the process, ACP cancellation and
        # permission replies included.
        options = await asyncio.to_thread(
            load_system_prompt_options,
            cwd=cwd_s,
            config=config,
            resources=ctx.get("resources") or resolved_resources,
            home=home_path,
            trusted=trust.trusted if trust is not None else None,
        )
        return build_coding_agent_harness_system_prompt(
            cwd=cwd_s,
            tools=all_tools,
            active_tool_names=active_names,
            system_prompt_options=options,
        )

    auto_compact = config.max_turns is not None and config.max_turns >= 30
    harness = AgentHarness(
        session=session,
        model=model,
        stream_fn=stream_fn,
        env=LocalExecutionEnv(cwd_s),
        tools=tools_list,
        resources=resolved_resources,
        get_api_key=api_key_for_choice(choice),
        system_prompt=system_prompt_callback,
        thinking_level=config.thinking_level,
        max_turns=config.max_turns,
        compaction=CompactionSettings(auto_compact=auto_compact),
        auto_discover_extensions=True,
        trust_project_extensions=(
            trust.trusted if trust is not None else project_extensions_trusted(config, cwd_s)
        ),
        home=home_path,
        extensions=extensions or [],
    )
    harness_holder["harness"] = harness
    if on_tool_call is not None:
        harness.on("tool_call", on_tool_call)
    return harness
