"""Small uuidv7 generator used for pi-compatible session entry IDs."""

from __future__ import annotations

import secrets
import time

_LAST_MS = 0
_SEQ = 0


def uuid7() -> str:
    """Return a 32-character hex UUIDv7-like value.

    Python 3.11/3.12 do not ship uuid.uuid7. This implementation preserves the
    two properties the session layer needs: millisecond timestamp prefix and
    monotonic lexical ordering for IDs generated within the same process.
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
    return f"{value:032x}"
