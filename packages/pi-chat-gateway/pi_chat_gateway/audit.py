"""``<home>/chat/audit.log``: one JSON line per pairing, approval and refused action.

It records who did what and when, never the text of a message.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUDIT_FILE = Path("chat") / "audit.log"


class AuditLog:
    def __init__(self, home: Path) -> None:
        self.path = home / AUDIT_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, **fields: Any) -> None:
        record = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
            if sys.platform != "win32":
                os.chmod(self.path, 0o600)
