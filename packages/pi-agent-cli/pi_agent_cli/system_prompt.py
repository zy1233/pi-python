"""System prompt construction (port of pi ``packages/coding-agent/src/core/system-prompt.ts``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pi_agent_harness.skills import format_skills_for_system_prompt
from pi_agent_harness.types import Skill

DEFAULT_SELECTED_TOOLS: tuple[str, ...] = ("read", "bash", "edit", "write")

_PI_PYTHON_DOCS = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ContextFile:
    path: str
    content: str


@dataclass
class BuildSystemPromptOptions:
    cwd: str
    custom_prompt: str | None = None
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] | None = None
    prompt_guidelines: list[str] | None = None
    append_system_prompt: str | None = None
    context_files: list[ContextFile] | None = None
    skills: list[Skill] | None = field(default_factory=list)


def _docs_paths() -> tuple[str, str, str]:
    readme = (_PI_PYTHON_DOCS / "README.md").resolve()
    docs = (_PI_PYTHON_DOCS / "docs").resolve()
    return str(readme), str(docs), str(docs)


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Build the system prompt with tools, guidelines, context, and skills."""
    prompt_cwd = options.cwd.replace("\\", "/")
    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""
    context_files = options.context_files or []
    skills = options.skills or []
    tools = options.selected_tools or list(DEFAULT_SELECTED_TOOLS)

    if options.custom_prompt:
        prompt = options.custom_prompt
        if append_section:
            prompt += append_section
        prompt = _append_project_context(prompt, context_files)
        custom_has_read = "read" in tools
        if custom_has_read and skills:
            prompt += format_skills_for_system_prompt(skills)
        prompt += f"\nCurrent working directory: {prompt_cwd}\n"
        return prompt

    visible_tools = [
        name for name in tools if options.tool_snippets and options.tool_snippets.get(name)
    ]
    if visible_tools and options.tool_snippets:
        tools_list = "\n".join(f"- {name}: {options.tool_snippets[name]}" for name in visible_tools)
    else:
        tools_list = "(none)"

    guidelines_list: list[str] = []
    guidelines_set: set[str] = set()

    def add_guideline(guideline: str) -> None:
        if guideline in guidelines_set:
            return
        guidelines_set.add(guideline)
        guidelines_list.append(guideline)

    has_bash = "bash" in tools
    has_grep = "grep" in tools
    has_find = "find" in tools
    has_ls = "ls" in tools
    has_read = "read" in tools

    if has_bash and not has_grep and not has_find and not has_ls:
        add_guideline("Use bash for file operations like ls, rg, find")

    for guideline in options.prompt_guidelines or []:
        normalized = guideline.strip()
        if normalized:
            add_guideline(normalized)

    add_guideline("Be concise in your responses")
    add_guideline("Show file paths clearly when working with files")

    guidelines = "\n".join(f"- {item}" for item in guidelines_list)
    readme_path, docs_path, examples_path = _docs_paths()

    intro = (
        "You are an expert coding assistant operating inside pi, a coding agent harness. "
        "You help users by reading files, executing commands, editing code, and writing new files."
    )
    docs_intro = (
        "Pi documentation (read only when the user asks about pi itself, its SDK, "
        "extensions, themes, skills, or TUI):"
    )
    docs_lines = [
        f"- Main documentation: {readme_path}",
        f"- Additional docs: {docs_path}",
        f"- Examples: {examples_path} (extensions, custom tools, SDK)",
        (
            "- When reading pi docs or examples, resolve docs/... under Additional docs "
            "and examples/... under Examples, not the current working directory"
        ),
        (
            "- When asked about: extensions, themes, skills, prompt templates, TUI components, "
            "keybindings, SDK integrations, custom providers, adding models, pi packages, "
            "environment variables"
        ),
        (
            "- When working on pi topics, read the docs and follow .md cross-references "
            "before implementing"
        ),
        (
            "- Always read pi .md files completely and follow links to related docs "
            "before implementing"
        ),
    ]

    prompt = "\n".join(
        [
            intro,
            "",
            "Available tools:",
            tools_list,
            "",
            "In addition to the tools above, you may have access to other custom tools "
            "depending on the project.",
            "",
            "Guidelines:",
            guidelines,
            "",
            docs_intro,
            *docs_lines,
        ]
    )

    if append_section:
        prompt += append_section

    prompt = _append_project_context(prompt, context_files)

    if has_read and skills:
        prompt += format_skills_for_system_prompt(skills)

    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def _append_project_context(prompt: str, context_files: list[ContextFile]) -> str:
    if not context_files:
        return prompt
    parts = [
        prompt,
        "",
        "<project_context>",
        "",
        "Project-specific instructions and guidelines:",
        "",
    ]
    for item in context_files:
        parts.append(f'<project_instructions path="{_xml_escape_attr(item.path)}">')
        parts.append(item.content)
        parts.append("</project_instructions>")
        parts.append("")
    parts.append("</project_context>")
    return "\n".join(parts)


def normalize_tool_snippet(snippet: str) -> str:
    """Collapse whitespace in tool snippets (pi ``create-harness.ts``)."""
    return re.sub(r"\s+", " ", snippet.replace("\r\n", " ").replace("\n", " ")).strip()


def _xml_escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )
