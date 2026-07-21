"""Prompt template loading and shell-like argument substitution."""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

from pi_agent_harness.frontmatter import parse_frontmatter
from pi_agent_harness.types import FileSystem, PromptTemplate

ARG_PATTERN = re.compile(r"\$\{@:([0-9]+)(?::([0-9]+))?\}|\$([0-9]+)|\$@|\$ARGUMENTS")


async def load_prompt_templates(env: FileSystem, paths: list[str]) -> list[PromptTemplate]:
    templates: list[PromptTemplate] = []
    for path in paths:
        info = await env.file_info(path)
        if info.kind == "directory":
            for child in await env.list_dir(path):
                if child.kind == "file" and child.name.endswith(".md"):
                    templates.append(await _load_template_file(env, _join(path, child.name)))
        elif info.kind == "file":
            templates.append(await _load_template_file(env, path))
    templates.sort(key=lambda template: template.name)
    return templates


def parse_command_args(text: str) -> list[str]:
    return shlex.split(text)


def substitute_args(content: str, args: list[str]) -> str:
    joined = " ".join(args)

    def replace(match: re.Match[str]) -> str:
        start = match.group(1)
        length = match.group(2)
        positional = match.group(3)
        token = match.group(0)
        if token in ("$@", "$ARGUMENTS"):
            return joined
        if positional is not None:
            idx = int(positional) - 1
            return args[idx] if 0 <= idx < len(args) else ""
        if start is not None:
            idx = int(start) - 1
            selected = args[idx:]
            if length is not None:
                selected = selected[: int(length)]
            return " ".join(selected)
        return token

    return ARG_PATTERN.sub(replace, content)


async def _load_template_file(env: FileSystem, path: str) -> PromptTemplate:
    raw = await env.read_text_file(path)
    metadata, body = parse_frontmatter(raw)
    name = PurePosixPath(path).stem
    description = metadata.get("description")
    if not description:
        description = body.strip().splitlines()[0][:60] if body.strip() else None
    return PromptTemplate(name=name, description=description, content=body.strip())


def _join(base: str, name: str) -> str:
    return str(PurePosixPath(base) / name)
