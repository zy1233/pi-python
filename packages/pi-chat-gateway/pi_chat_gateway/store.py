"""The gateway's own state: ``<home>/chat/state.sqlite``.

It holds who paired as owner, the pairing codes, which chat or thread maps to which session,
and the ids of the events already handled. It holds no conversation: the agent's JSONL session
files stay the only record of what was said. ``zycode-chat pair`` writes a code here while the
gateway runs, so both processes open the same file (SQLite handles that).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

STATE_FILE = Path("chat") / "state.sqlite"
# How long the id of a handled event is kept. Platforms redeliver within minutes; a day is plenty.
SEEN_TTL_SECONDS = 24 * 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    platform TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant TEXT NOT NULL DEFAULT '',
    dm_chat_id TEXT NOT NULL DEFAULT '',
    paired_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pairing_failures (
    platform TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    thread_key TEXT NOT NULL,
    project TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id, thread_key)
);
CREATE TABLE IF NOT EXISTS seen_events (
    key TEXT PRIMARY KEY,
    at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Owner:
    platform: str
    user_id: str
    tenant: str
    dm_chat_id: str
    paired_at: float


@dataclass(frozen=True)
class BindingKey:
    """A direct chat (``thread_key`` empty: its current session) or one thread of a group."""

    platform: str
    chat_id: str
    thread_key: str = ""


@dataclass(frozen=True)
class Binding:
    key: BindingKey
    project: str
    session_id: str
    created_at: float
    updated_at: float


class Store:
    def __init__(self, home: Path) -> None:
        self.path = home / STATE_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            # Create it private before SQLite opens it.
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- owners --------------------------------------------------------------------------------

    def owner(self, platform: str) -> Owner | None:
        with self._lock:
            row = self._db.execute(
                "SELECT platform, user_id, tenant, dm_chat_id, paired_at FROM owners "
                "WHERE platform = ?",
                (platform,),
            ).fetchone()
        return Owner(*row) if row else None

    def set_owner(self, owner: Owner) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO owners VALUES (?, ?, ?, ?, ?)",
                (owner.platform, owner.user_id, owner.tenant, owner.dm_chat_id, owner.paired_at),
            )

    def update_owner_dm(self, platform: str, dm_chat_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE owners SET dm_chat_id = ? WHERE platform = ?", (dm_chat_id, platform)
            )

    def clear_owner(self, platform: str) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute("DELETE FROM owners WHERE platform = ?", (platform,))
        return cursor.rowcount > 0

    # -- pairing -------------------------------------------------------------------------------

    def add_pairing_code(self, platform: str, code_hash: str, expires_at: float) -> None:
        with self._lock, self._db:
            # One open code per platform: a new one replaces the old.
            self._db.execute(
                "DELETE FROM pairing_codes WHERE platform = ? AND used = 0", (platform,)
            )
            self._db.execute(
                "INSERT INTO pairing_codes VALUES (?, ?, ?, 0)", (code_hash, platform, expires_at)
            )

    def take_pairing_code(self, platform: str, code_hash: str, now: float) -> bool:
        """Use the code if it is open, unexpired and for this platform. One use only."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE pairing_codes SET used = 1 WHERE code_hash = ? AND platform = ? "
                "AND used = 0 AND expires_at > ?",
                (code_hash, platform, now),
            )
        return cursor.rowcount == 1

    def record_pairing_failure(self, platform: str, now: float) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO pairing_failures VALUES (?, ?)", (platform, now))

    def pairing_failures_since(self, platform: str, since: float) -> int:
        with self._lock:
            (count,) = self._db.execute(
                "SELECT COUNT(*) FROM pairing_failures WHERE platform = ? AND at > ?",
                (platform, since),
            ).fetchone()
        return int(count)

    # -- bindings ------------------------------------------------------------------------------

    def binding(self, key: BindingKey) -> Binding | None:
        with self._lock:
            row = self._db.execute(
                "SELECT project, session_id, created_at, updated_at FROM bindings "
                "WHERE platform = ? AND chat_id = ? AND thread_key = ?",
                (key.platform, key.chat_id, key.thread_key),
            ).fetchone()
        if row is None:
            return None
        project, session_id, created_at, updated_at = row
        return Binding(key, project, session_id, created_at, updated_at)

    def put_binding(self, binding: Binding) -> None:
        key = binding.key
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO bindings VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key.platform,
                    key.chat_id,
                    key.thread_key,
                    binding.project,
                    binding.session_id,
                    binding.created_at,
                    binding.updated_at,
                ),
            )

    # -- handled events ------------------------------------------------------------------------

    def first_time(self, key: str, now: float) -> bool:
        """True the first time *key* is seen; a redelivered event gets False."""
        with self._lock, self._db:
            cursor = self._db.execute("INSERT OR IGNORE INTO seen_events VALUES (?, ?)", (key, now))
        return cursor.rowcount == 1

    def prune(self, now: float) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM seen_events WHERE at < ?", (now - SEEN_TTL_SECONDS,))
            self._db.execute("DELETE FROM pairing_codes WHERE expires_at < ?", (now - 3600,))
            self._db.execute("DELETE FROM pairing_failures WHERE at < ?", (now - 3600,))
