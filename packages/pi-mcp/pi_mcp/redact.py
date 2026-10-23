"""Keeping tokens out of what the user and the model read (design §6.1).

The ``env``, ``headers``, ``url`` and ``args`` of a server that the client passes often hold
tokens. They do not go into a log line, a notice, the ``/mcp`` output or an error text. The
errors of ``httpx`` name the whole URL, so an error text is cleaned (``redact``) before anyone
sees it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, unquote, urlsplit

if TYPE_CHECKING:
    from pi_agent_core.extensions import ClientMcpServer

MASK = "***"

_MIN_SECRET_CHARS = 4
"""A value with fewer characters is not masked: it would hide ordinary words, and it is no token."""

_TOKEN_SEGMENT_CHARS = 16
"""A segment of a URL path with at least this many characters can be a token (``/mcp/<token>``)."""


def secrets_of(server: ClientMcpServer) -> tuple[str, ...]:
    """The strings of *server* that must not appear in a text, longest first.

    That is the URL (and the parts of it that carry a secret), every header value (and the token
    behind a scheme such as ``Bearer``), and every value of ``env``.
    """
    found: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and len(value) >= _MIN_SECRET_CHARS:
            found.add(value)

    for _name, value in server.env:
        add(value)
    for _name, value in server.headers:
        add(value)
        words = value.split()
        if len(words) > 1:
            add(words[-1])  # "Bearer <token>": the token alone may be printed too
    if server.url:
        add(server.url)
        try:
            parts = urlsplit(server.url)
        except ValueError:
            parts = None
        if parts is not None:
            if parts.password:
                add(parts.password)
                add(unquote(parts.password))
            for _key, value in parse_qsl(parts.query, keep_blank_values=False):
                add(value)
            for segment in parts.path.split("/"):
                if len(segment) >= _TOKEN_SEGMENT_CHARS:
                    add(segment)
    return tuple(sorted(found, key=len, reverse=True))


def redact(text: str, secrets: Iterable[str]) -> str:
    """*text* with every string of *secrets* replaced by ``***``."""
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, MASK)
    return text


def one_line(text: str) -> str:
    """*text* on one line: runs of whitespace (and control characters) become one space."""
    return re.sub(r"[\s\x00-\x1f\x7f]+", " ", text).strip()
