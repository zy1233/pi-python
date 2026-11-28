"""Making the agent's Markdown safe to post, and cutting it to the size of a card.

Everything a chat platform shows leaves the machine and stays on the platform's servers. Three
things are done to text before it goes out (design section 7):

1. **Redaction.** Known secret values (the gateway's credentials, the values in ``local.env``)
   are replaced exactly, then a few common token formats. Best effort, not a guarantee.
2. **No fetching on the platform's side.** A link that the platform previews, or a Markdown image
   that it renders, makes the platform's servers fetch a URL the model wrote, with no click
   needed; a prompt injection can put data into that URL. So images become plain text, and
   links and bare URLs are shown as code (not clickable, not previewed).
3. **No markup of the platform.** Card Markdown has tags of its own (``<at id=all>`` mentions
   everyone in Feishu). A ``<`` that starts a tag gets a zero-width space after it, so it shows
   as text.

Code blocks and inline code are left as they are, except for redaction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

REDACTED = "[redacted]"
_ZWSP = "​"
# Values shorter than this are not replaced exactly: they would hit ordinary words.
_MIN_SECRET_LENGTH = 8

_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
    ),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
]

_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)$")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1", re.S)
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<((?:https?|ftp|mailto):[^>\s]+)>", re.I)
_BARE_URL = re.compile(r"\b((?:https?|ftp)://[^\s<>`\"']+)", re.I)
_TAG_START = re.compile(r"<(?=[A-Za-z/!?])")
_TRAILING = ".,;:!?)]}'\""


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        values = sorted(
            {s for s in secrets if s and len(s) >= _MIN_SECRET_LENGTH}, key=len, reverse=True
        )
        self._exact = re.compile("|".join(re.escape(v) for v in values)) if values else None

    def __call__(self, text: str) -> str:
        if self._exact is not None:
            text = self._exact.sub(REDACTED, text)
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text


def _code_span(text: str) -> str:
    ticks = "``" if "`" in text else "`"
    return f"{ticks}{text}{ticks}" if ticks == "`" else f"{ticks} {text} {ticks}"


def _split_url(url: str) -> tuple[str, str]:
    """A bare URL and the punctuation after it that belongs to the sentence."""
    end = len(url)
    while end > 0 and url[end - 1] in _TRAILING:
        end -= 1
    return url[:end], url[end:]


def _neutralize_plain(text: str) -> str:
    """Links, images and tags in text that is not code."""
    holders: list[str] = []

    def hold(value: str) -> str:
        holders.append(value)
        return f"\x00{len(holders) - 1}\x00"

    text = _IMAGE.sub(
        lambda m: hold(f"[image: {m.group(1) or 'untitled'}] {_code_span(m.group(2))}"), text
    )
    text = _LINK.sub(lambda m: hold(f"{m.group(1)} ({_code_span(m.group(2))})"), text)
    text = _AUTOLINK.sub(lambda m: hold(_code_span(m.group(1))), text)

    def bare(m: re.Match[str]) -> str:
        url, tail = _split_url(m.group(1))
        return hold(_code_span(url)) + tail

    text = _BARE_URL.sub(bare, text)
    text = _TAG_START.sub("<" + _ZWSP, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: holders[int(m.group(1))], text)


def _neutralize_line_runs(text: str) -> str:
    """Inline code stays; the rest of a paragraph is neutralized."""
    out: list[str] = []
    last = 0
    for match in _INLINE_CODE.finditer(text):
        out.append(_neutralize_plain(text[last : match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(_neutralize_plain(text[last:]))
    return "".join(out)


def sanitize(text: str, redact: Redactor | None = None) -> str:
    """The agent's Markdown, safe for a card: redacted, no fetchable links, no platform tags."""
    text = text.replace("\x00", "")  # the placeholders of _neutralize_plain use it
    if redact is not None:
        text = redact(text)
    lines = text.split("\n")
    out: list[str] = []
    prose: list[str] = []
    fence: str | None = None
    for line in lines:
        if fence is None:
            match = _FENCE.match(line)
            if match:
                if prose:
                    out.append(_neutralize_line_runs("\n".join(prose)))
                    prose = []
                fence = match.group("fence")
                out.append(line)
            else:
                prose.append(line)
        else:
            out.append(line)
            stripped = line.strip()
            if stripped.startswith(fence[0] * len(fence)) and set(stripped) <= {fence[0]}:
                fence = None
    if prose:
        out.append(_neutralize_line_runs("\n".join(prose)))
    return "\n".join(out)


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


@dataclass(frozen=True)
class _Fence:
    marker: str
    info: str


def split_markdown(text: str, max_bytes: int) -> list[str]:
    """Cut Markdown into pieces of at most *max_bytes* UTF-8 bytes.

    Cuts fall between paragraphs where possible, then between lines, and only inside a line
    when one line is too long. A code block that is cut is closed at the end of a piece and
    opened again, with its info string, at the start of the next.
    """
    if max_bytes < 64:
        raise ValueError("max_bytes is too small to hold a piece")
    if utf8_len(text) <= max_bytes:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    fence: _Fence | None = None

    def close_piece() -> None:
        nonlocal current, size
        if not current:
            return
        body = "\n".join(current)
        if fence is not None:
            body += "\n" + fence.marker
        pieces.append(body)
        current = []
        size = 0
        if fence is not None:
            reopen = fence.marker + fence.info
            current.append(reopen)
            size = utf8_len(reopen) + 1

    reserve = 16  # room for a closing fence
    for line in text.split("\n"):
        for chunk in _cut_line(line, max_bytes - reserve - 32):
            added = utf8_len(chunk) + 1
            if size + added > max_bytes - reserve and current:
                close_piece()
            current.append(chunk)
            size += added
        match = _FENCE.match(line)
        if fence is None and match:
            fence = _Fence(match.group("fence"), match.group("info"))
        elif fence is not None and _closes(line, fence.marker):
            fence = None
    if current:
        pieces.append("\n".join(current))
    return [piece for piece in pieces if piece.strip()]


def _closes(line: str, marker: str) -> bool:
    """Whether *line* closes a code block that *marker* opened."""
    stripped = line.strip()
    return stripped.startswith(marker[0] * len(marker)) and set(stripped) <= {marker[0]}


def _cut_line(line: str, limit: int) -> list[str]:
    if utf8_len(line) <= limit:
        return [line]
    out: list[str] = []
    current = ""
    for ch in line:
        if utf8_len(current) + utf8_len(ch) > limit:
            out.append(current)
            current = ""
        current += ch
    if current:
        out.append(current)
    return out


def tail(text: str, max_bytes: int, marker: str = "...") -> str:
    """The end of *text* within *max_bytes*: a turn in progress shows its latest output."""
    if utf8_len(text) <= max_bytes:
        return text
    encoded = text.encode("utf-8")[-(max_bytes - utf8_len(marker) - 1) :]
    kept = encoded.decode("utf-8", "ignore")
    newline = kept.find("\n")
    if 0 <= newline < len(kept) // 2:
        kept = kept[newline + 1 :]
    return f"{marker}\n{kept}"


def preview(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."
