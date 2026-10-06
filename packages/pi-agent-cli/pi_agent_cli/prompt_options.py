"""Resolve ``BuildSystemPromptOptions`` from config, cwd, and harness resources."""

from __future__ import annotations

from pathlib import Path

from pi_agent_cli.config import CliConfig, pi_home
from pi_agent_cli.context_files import (
    discover_context_files,
    load_append_system_prompt_file,
    load_system_prompt_file,
)
from pi_agent_cli.extension_trust import project_extensions_trusted
from pi_agent_cli.git_context import format_git_status, snapshot_git_context
from pi_agent_cli.system_prompt import BuildSystemPromptOptions
from pi_agent_harness.types import AgentHarnessResources


def load_system_prompt_options(
    *,
    cwd: str | Path,
    config: CliConfig,
    resources: AgentHarnessResources | None = None,
    home: Path | None = None,
    trusted: bool | None = None,
) -> BuildSystemPromptOptions:
    """Build prompt options from agent.toml, context files, and loaded skills.

    *trusted*: whether the project's own prompt files may be used. The session knows (the
    user may have said yes in a prompt); when it does not say, the configuration decides.
    """
    cwd_s = str(Path(cwd).resolve())
    home_path = pi_home(home)
    # The project's own .pi/SYSTEM.md and .pi/APPEND_SYSTEM.md decide what the model is
    # told, so they count only for a trusted project (AGENTS.md / CLAUDE.md do not, as
    # upstream loads those whatever the trust).
    if trusted is None:
        trusted = project_extensions_trusted(config, cwd_s)

    custom_prompt = config.custom_system_prompt
    if custom_prompt is None and config.custom_system_prompt_file:
        custom_prompt = (
            Path(config.custom_system_prompt_file).expanduser().read_text(encoding="utf-8")
        )
    if custom_prompt is None:
        custom_prompt = load_system_prompt_file(cwd=cwd_s, home=home_path, include_project=trusted)

    append_prompt = config.append_system_prompt
    if append_prompt is None and config.append_system_prompt_file:
        append_prompt = (
            Path(config.append_system_prompt_file).expanduser().read_text(encoding="utf-8")
        )
    if append_prompt is None:
        append_prompt = load_append_system_prompt_file(
            cwd=cwd_s, home=home_path, include_project=trusted
        )

    context_files = (
        None if config.no_context_files else discover_context_files(cwd=cwd_s, home=home_path)
    )
    skills = list(resources.skills) if resources and resources.skills else []
    git_status = _git_status_block(cwd_s, config)

    return BuildSystemPromptOptions(
        cwd=cwd_s,
        custom_prompt=custom_prompt,
        append_system_prompt=append_prompt,
        context_files=context_files,
        skills=skills,
        git_status=git_status,
    )


def _git_status_block(cwd: str, config: CliConfig) -> str | None:
    if not config.git_enabled:
        return None
    snapshot = snapshot_git_context(
        cwd,
        timeout_seconds=config.git_timeout_seconds,
        max_lines=config.git_max_status_lines,
    )
    if snapshot is None:
        return None
    return format_git_status(snapshot)
