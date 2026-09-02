"""Single-turn headless prompt (`python -m pi_agent_cli -p`). No TUI, no ACP stdio."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from pi_agent_cli.config import CliConfig, load_config, pi_home
from pi_agent_cli.factory import create_session_harness, default_stream_fn, load_session_resources
from pi_agent_harness import JsonlSessionRepo


@dataclass(frozen=True)
class HeadlessPromptOverrides:
    """CLI overrides for prompt assembly (headless only; wins over agent.toml)."""

    system_prompt: str | None = None
    system_prompt_file: str | Path | None = None
    append_system_prompt: str | None = None
    append_system_prompt_file: str | Path | None = None
    no_context_files: bool | None = None


def apply_prompt_overrides(
    config: CliConfig,
    overrides: HeadlessPromptOverrides,
) -> CliConfig:
    """Return config with headless CLI prompt flags applied."""
    updates: dict[str, object] = {}

    custom_prompt = overrides.system_prompt
    if custom_prompt is None and overrides.system_prompt_file is not None:
        custom_prompt = Path(overrides.system_prompt_file).read_text(encoding="utf-8")
    if custom_prompt is not None:
        updates["custom_system_prompt"] = custom_prompt
        updates["custom_system_prompt_file"] = None

    append_prompt = overrides.append_system_prompt
    if append_prompt is None and overrides.append_system_prompt_file is not None:
        append_prompt = Path(overrides.append_system_prompt_file).read_text(encoding="utf-8")
    if append_prompt is not None:
        updates["append_system_prompt"] = append_prompt
        updates["append_system_prompt_file"] = None

    if overrides.no_context_files is not None:
        updates["no_context_files"] = overrides.no_context_files

    return replace(config, **updates) if updates else config


def assistant_text(message: object) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", None) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def prompt_from_json(raw: str) -> str:
    data = json.loads(raw)
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "".join(
            str(block.get("text") or "")
            for block in data
            if isinstance(block, dict) and block.get("type") == "text"
        )
    raise ValueError("prompt JSON must be a string or a list of content blocks")


def resolve_print_prompt(
    *,
    print_prompt: str | None,
    prompt_json: str | None,
    prompt_file: str | Path | None,
) -> str:
    if print_prompt is not None:
        return print_prompt
    if prompt_json is not None:
        return prompt_from_json(prompt_json)
    if prompt_file is not None:
        return Path(prompt_file).read_text(encoding="utf-8")
    raise ValueError("one of -p/--print, --prompt-json, or --prompt-file is required")


async def run_print(
    prompt: str,
    *,
    cwd: str | Path | None = None,
    home: str | Path | None = None,
    prompt_overrides: HeadlessPromptOverrides | None = None,
) -> int:
    """Create a JSONL session, run one harness turn, print assistant text."""
    text = prompt.strip()
    if not text:
        print("error: empty prompt", flush=True)
        return 2
    home_path = pi_home(home)
    sessions_dir = home_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    cwd_s = str(Path(cwd).resolve() if cwd is not None else Path.cwd())
    config = replace(load_config(home_path), permission="auto")
    if prompt_overrides is not None:
        config = apply_prompt_overrides(config, prompt_overrides)
    repo = JsonlSessionRepo(sessions_dir)
    session = await repo.create({"cwd": cwd_s})
    resources = await load_session_resources(cwd=cwd_s, config=config)
    harness = await create_session_harness(
        session=session,
        cwd=cwd_s,
        config=config,
        stream_fn=default_stream_fn(),
        resources=resources,
    )
    message = await harness.prompt(text)
    out = assistant_text(message)
    print(out, end="" if out.endswith("\n") else "\n")
    return 0
