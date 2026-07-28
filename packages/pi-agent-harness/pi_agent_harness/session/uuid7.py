"""Small uuidv7 generator used for pi-compatible session entry IDs."""

from __future__ import annotations

import secrets
import time

_LAST_MS = 0
_SEQ = 0


def uuid7() -> str:
    """Return a canonical 36-character hyphenated UUIDv7 string.

    Python 3.11/3.12 do not ship uuid.uuid7. This implementation preserves the
    properties the session layer needs: millisecond timestamp prefix, monotonic
    lexical ordering for IDs generated within the same process, and pi's
    `uuidv7()` textual format (8-4-4-4-12, lowercase hex).
    """
    global _LAST_MS, _SEQ

    now_ms = int(time.time() * 1000)
    if now_ms <= _LAST_MS:
        now_ms = _LAST_MS
        _SEQ = (_SEQ + 1) & 0x0FFF
        if _SEQ == 0:
            now_ms += 1
    else:
        _SEQ = secrets.randbits(12)
    _LAST_MS = now_ms

    timestamp = now_ms & ((1 << 48) - 1)
    rand_b = secrets.randbits(62)

    value = (timestamp << 80) | (0x7 << 76) | (_SEQ << 64) | (0b10 << 62) | rand_b
    raw = f"{value:032x}"
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
