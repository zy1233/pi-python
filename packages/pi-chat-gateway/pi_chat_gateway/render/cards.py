"""Platform-neutral cards: what a turn, an approval or a notice looks like.

An adapter turns a ``Card`` into its platform's format (Feishu: card JSON 2.0). Every string
that came from the agent is passed through ``sanitize`` here, so no adapter can forget it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..texts import Texts
from .text import Redactor, preview, sanitize, split_markdown, tail, utf8_len
from .view import TurnView

# Feishu refuses a card above 30 KB. Leave room for the JSON around the text.
DEFAULT_CARD_BYTES = 20_000
MAX_TOOL_LINES = 8

COLORS = {
    "thinking": "blue",
    "running": "blue",
    "waiting": "orange",
    "done": "green",
    "cancelled": "grey",
    "error": "red",
}
TOOL_ICONS = {"pending": "⏳", "in_progress": "🔧", "completed": "✅", "failed": "❌"}


@dataclass(frozen=True)
class Button:
    label: str
    value: Mapping[str, str]
    style: str = "default"  # "primary" | "danger" | "default"


@dataclass(frozen=True)
class Card:
    title: str
    color: str = "blue"
    blocks: tuple[str, ...] = ()
    buttons: tuple[Button, ...] = ()
    note: str = ""
    summary: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


def _status_label(t: Texts, status: str) -> str:
    return t(f"status_{status}")


def tool_lines(view: TurnView, t: Texts, redact: Redactor | None) -> str:
    lines = list(view.tools.values())
    hidden = max(0, len(lines) - MAX_TOOL_LINES)
    out = []
    if hidden:
        out.append(t("tools_more", count=hidden))
    for line in lines[-MAX_TOOL_LINES:]:
        title = preview(redact(line.title) if redact else line.title, 120)
        icon = TOOL_ICONS.get(line.status, "🔧")
        seconds = ""
        if line.ended is not None:
            seconds = f" · {line.ended - line.started:.1f}s"
        out.append(f"{icon} {_inline_code(title)}{seconds}")
    return "\n".join(out)


def _inline_code(text: str) -> str:
    text = text.replace("`", "'")
    return f"`{text}`"


def _footer(view: TurnView, t: Texts) -> str:
    stop = view.stop_reason or (view.error and "error") or ""
    if view.used is not None and view.size:
        return t("footer_context", stop=stop, used=view.used, size=view.size)
    if view.used is not None:
        return t("footer", stop=stop, used=view.used)
    return stop


def turn_card(
    view: TurnView,
    t: Texts,
    *,
    redact: Redactor | None,
    show_thinking: bool,
    max_bytes: int = DEFAULT_CARD_BYTES,
) -> tuple[Card, list[Card]]:
    """The card of the turn, and, once it is over, any further cards for a long answer."""
    title = f"{t('title')} · {_status_label(t, view.status)}"
    blocks: list[str] = []
    tools = tool_lines(view, t, redact)
    if tools:
        blocks.append(tools)
    budget = max_bytes - utf8_len(tools) - 512
    if show_thinking and view.thought:
        thought = sanitize(view.thought, redact)
        thought_budget = max(512, budget // 4)
        quoted = "\n".join("> " + line for line in tail(thought, thought_budget).split("\n"))
        blocks.append(quoted)
        budget -= utf8_len(quoted)
    answer = sanitize(view.text, redact) if view.text else ""
    overflow: list[Card] = []
    if answer:
        if not view.finished:
            blocks.append(tail(answer, max(1024, budget)))
        else:
            pieces = split_markdown(answer, max(1024, budget))
            blocks.append(pieces[0])
            if len(pieces) > 1:
                blocks.append(t("answer_continued"))
                for index, piece in enumerate(pieces[1:], start=2):
                    part = t("answer_part", index=index, total=len(pieces))
                    overflow.append(
                        Card(
                            title=f"{t('title')} · {part}",
                            color=COLORS[view.status],
                            blocks=(piece,),
                        )
                    )
    if view.error:
        blocks.append(t("agent_failed", error=sanitize(view.error, redact)))
    note = _footer(view, t) if view.finished else ""
    card = Card(
        title=title,
        color=COLORS[view.status],
        blocks=tuple(blocks) or (t("status_thinking") + "...",),
        note=note,
        summary=preview(view.text or title, 60),
    )
    return card, overflow


def request_text(tool_call: Any, fallback_raw_input: Any = None) -> str:
    """What a permission request asks to run, in full, as Markdown (not sanitized yet)."""
    raw = getattr(tool_call, "raw_input", None)
    if raw is None:
        raw = fallback_raw_input
    title = getattr(tool_call, "title", None) or "tool"
    parts = [f"**{title}**"]
    if isinstance(raw, dict):
        shown: set[str] = set()
        for key, language in _SHOWN_FIELDS:
            value = raw.get(key)
            if isinstance(value, str) and value:
                parts.append(f"{key}:\n{_fenced(value, language)}")
                shown.add(key)
        rest = {k: v for k, v in raw.items() if k not in shown}
        if rest:
            parts.append(_fenced(json.dumps(rest, ensure_ascii=False, indent=2), "json"))
    elif raw is not None:
        parts.append(_fenced(str(raw), ""))
    return "\n\n".join(parts)


# The fields of the built-in tools, in the order a person reads them, with a language for the
# code block. Everything else of the input follows as JSON: nothing is left out.
_SHOWN_FIELDS = (
    ("command", "bash"),
    ("path", ""),
    ("code", "python"),
    ("script", "python"),
    ("name", ""),
    ("content", ""),
)


def _fenced(text: str, language: str) -> str:
    """A code block whose fence is longer than any run of backticks in *text*."""
    longest = max((len(run) for run in _backtick_runs(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _backtick_runs(text: str) -> list[str]:
    runs: list[str] = []
    current = ""
    for ch in text:
        if ch == "`":
            current += ch
        elif current:
            runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


def approval_cards(
    *,
    t: Texts,
    request_md: str,
    redact: Redactor | None,
    buttons: list[Button],
    status_line: str = "",
    max_bytes: int = DEFAULT_CARD_BYTES,
) -> tuple[Card, list[Card]]:
    """The approval card and, when the request does not fit, cards that carry the full text.

    The text is never cut silently: if it is too long, the card says so and the rest follows.
    """
    text = sanitize(request_md, redact)
    pieces = split_markdown(text, max_bytes - 1024)
    blocks = [pieces[0]]
    extra_cards: list[Card] = []
    if len(pieces) > 1:
        blocks.append(t("approval_full_below"))
        for index, piece in enumerate(pieces, start=1):
            extra_cards.append(
                Card(
                    title=t("approval_full_part", index=index, total=len(pieces)),
                    color="orange",
                    blocks=(piece,),
                )
            )
    if status_line:
        blocks.append(status_line)
    card = Card(
        title=f"{t('title')} · {t('approval_title')}",
        color="orange" if buttons else "grey",
        blocks=tuple(blocks),
        buttons=tuple(buttons),
        summary=t("approval_title"),
    )
    return card, extra_cards


def notice_card(
    t: Texts, text: str, *, redact: Redactor | None = None, color: str = "grey"
) -> Card:
    body = sanitize(text, redact)
    return Card(
        title=f"{t('title')} · {t('status_notice')}",
        color=color,
        blocks=(body,),
        summary=preview(body, 60),
    )
