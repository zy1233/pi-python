"""Skill loading, validation, and prompt formatting."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from pathspec import PathSpec

from pi_agent_harness.frontmatter import parse_frontmatter
from pi_agent_harness.types import Skill, SkillDiagnostic, SkillLoadResult

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


async def load_skills(env: Any, paths: list[str]) -> SkillLoadResult:
    result = SkillLoadResult()
    for path in paths:
        await _load_skill_path(env, _clean(path), result, [])
    result.skills.sort(key=lambda skill: skill.name)
    return result


async def load_sourced_skills(
    env: Any, sources: dict[str, list[str]]
) -> dict[str, SkillLoadResult]:
    return {source: await load_skills(env, paths) for source, paths in sources.items()}


def format_skills_for_system_prompt(skills: list[Skill]) -> str:
    visible = [skill for skill in skills if not skill.disableModelInvocation]
    if not visible:
        return ""
    lines = ["<skills>"]
    for skill in visible:
        lines.append(
            f'  <skill name="{_xml_escape(skill.name)}" '
            f'description="{_xml_escape(skill.description)}" '
            f'filePath="{_xml_escape(skill.filePath)}" />'
        )
    lines.append("</skills>")
    return "\n".join(lines)


def format_skill_invocation(skill: Skill, additional_instructions: str | None = None) -> str:
    base_dir = str(PurePosixPath(skill.filePath).parent)
    parts = [
        f"Use skill `{skill.name}`.",
        f"References are relative to {base_dir}.",
        "",
        skill.content,
    ]
    if additional_instructions:
        parts.extend(["", "Additional instructions:", additional_instructions])
    return "\n".join(parts)


async def _load_skill_path(
    env: Any,
    path: str,
    result: SkillLoadResult,
    inherited_patterns: list[str],
) -> None:
    try:
        info = await env.file_info(path)
    except Exception as exc:
        _diagnose(result, "file_info_failed", str(exc), path)
        return
    if info.kind == "file" and path.endswith(".md"):
        await _read_skill_file(env, path, PurePosixPath(path).stem, result)
        return
    if info.kind != "directory":
        return

    patterns = [*inherited_patterns, *await _read_ignore_patterns(env, path)]
    spec = PathSpec.from_lines("gitignore", patterns)
    try:
        children = await env.list_dir(path)
    except Exception as exc:
        _diagnose(result, "list_failed", str(exc), path)
        return

    skill_file = next((child for child in children if child.name == "SKILL.md"), None)
    if skill_file:
        await _read_skill_file(env, _join(path, "SKILL.md"), PurePosixPath(path).name, result)
        return

    for child in children:
        child_path = _join(path, child.name)
        rel = _relative_to_root(path, child_path)
        match_path = f"{rel}/" if child.kind == "directory" else rel
        if spec.match_file(match_path):
            continue
        if child.kind == "directory":
            await _load_skill_path(env, child_path, result, patterns)
        elif child.kind == "file" and child.name.endswith(".md"):
            await _read_skill_file(env, child_path, PurePosixPath(child.name).stem, result)


async def _read_skill_file(env: Any, path: str, default_name: str, result: SkillLoadResult) -> None:
    try:
        raw = await env.read_text_file(path)
        metadata, content = parse_frontmatter(raw)
    except Exception as exc:
        _diagnose(result, "read_failed", str(exc), path)
        return
    name = str(metadata.get("name") or default_name)
    description = str(metadata.get("description") or "").strip()
    disable = bool(
        metadata.get("disable-model-invocation") or metadata.get("disableModelInvocation")
    )
    reason = _validate_skill_metadata(name, description, default_name)
    if reason:
        _diagnose(result, "invalid_metadata", reason, path)
        return
    result.skills.append(
        Skill(
            name=name,
            description=description,
            content=content.strip(),
            filePath=path,
            disableModelInvocation=disable,
        )
    )


async def _read_ignore_patterns(env: Any, path: str) -> list[str]:
    patterns: list[str] = []
    for name in (".gitignore", ".ignore", ".fdignore"):
        ignore_path = _join(path, name)
        if await env.exists(ignore_path):
            patterns.extend((await env.read_text_file(ignore_path)).splitlines())
    return patterns


def _validate_skill_metadata(name: str, description: str, default_name: str) -> str | None:
    if not description or len(description) > 1024:
        return "Skill description is required and must be <= 1024 characters"
    if len(name) > 64 or not SKILL_NAME_RE.match(name):
        return "Skill name must be lowercase kebab-case and <= 64 characters"
    if default_name and name != default_name:
        return f"Skill name `{name}` must match directory or file name `{default_name}`"
    return None


def _diagnose(result: SkillLoadResult, code: str, message: str, path: str) -> None:
    result.diagnostics.append(SkillDiagnostic(code=code, message=message, path=path))


def _join(base: str, name: str) -> str:
    return str(PurePosixPath(base) / name)


def _clean(path: str) -> str:
    return str(PurePosixPath(path))


def _relative_to_root(root: str, path: str) -> str:
    try:
        return str(PurePosixPath(path).relative_to(PurePosixPath(root)))
    except ValueError:
        return path


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )
