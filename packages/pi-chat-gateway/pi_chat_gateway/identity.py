"""Who may do what: pairing the owner, and the checks before every action.

Pairing proves two things at once: the person controls the machine (the code is printed in its
terminal) and the chat account (they send the code from it). The code is kept as a hash, is good
for one use and ten minutes, and five wrong tries in an hour lock pairing for that platform.

Version 1 is personal: one owner per platform. A bound group may list more people who can talk
to the agent (``users``) or approve its calls (``approvers``); nobody else is heard.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .config import GatewayConfig
from .store import Owner, Store

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
CODE_TTL_SECONDS = 600.0
MAX_FAILURES_PER_HOUR = 5


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def hash_code(code: str) -> str:
    normalized = "".join(ch for ch in code.upper() if ch.isalnum())
    return hashlib.sha256(normalized.encode("ascii", "ignore")).hexdigest()


class PairResult(Enum):
    OK = "ok"
    BAD = "bad"
    LOCKED = "locked"
    TAKEN = "taken"


class Pairing:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    def create_code(self, platform: str) -> str:
        code = new_code()
        self._store.add_pairing_code(platform, hash_code(code), self._clock() + CODE_TTL_SECONDS)
        return code

    def attempt(
        self, *, platform: str, user_id: str, tenant: str, dm_chat_id: str, code: str
    ) -> PairResult:
        now = self._clock()
        if self._store.owner(platform) is not None:
            return PairResult.TAKEN
        if self._store.pairing_failures_since(platform, now - 3600) >= MAX_FAILURES_PER_HOUR:
            return PairResult.LOCKED
        if not self._store.take_pairing_code(platform, hash_code(code), now):
            self._store.record_pairing_failure(platform, now)
            return PairResult.BAD
        self._store.set_owner(Owner(platform, user_id, tenant, dm_chat_id, now))
        return PairResult.OK


@dataclass(frozen=True)
class Sender:
    platform: str
    tenant: str
    user_id: str


class Authorizer:
    """The rules of section 6 of the design. Nothing here talks to the network."""

    def __init__(self, config: GatewayConfig, store: Store) -> None:
        self._config = config
        self._store = store

    def owner_id(self, platform: str) -> str:
        pinned = self._config.platform(platform)
        if pinned is not None and pinned.owner:
            return pinned.owner
        owner = self._store.owner(platform)
        return owner.user_id if owner else ""

    def tenant_ok(self, platform: str, tenant: str) -> bool:
        """Events from another tenant are dropped. Before pairing nothing is known yet."""
        configured = self._config.platform(platform)
        if configured is None:
            return False
        if configured.tenant:
            return tenant == configured.tenant
        owner = self._store.owner(platform)
        if owner is not None and owner.tenant:
            return tenant == owner.tenant
        return True

    def is_owner(self, sender: Sender) -> bool:
        owner = self.owner_id(sender.platform)
        return (
            bool(owner)
            and sender.user_id == owner
            and self.tenant_ok(sender.platform, sender.tenant)
        )

    def may_talk(self, sender: Sender, chat_id: str, *, is_dm: bool) -> bool:
        if not self.tenant_ok(sender.platform, sender.tenant):
            return False
        if is_dm:
            return self.is_owner(sender)
        channel = self._config.channel(sender.platform, chat_id)
        if channel is None:
            return False
        return self.is_owner(sender) or sender.user_id in channel.users

    def may_approve(self, sender: Sender, chat_id: str) -> bool:
        """*chat_id* is the chat the session lives in (not where the card went)."""
        if not self.tenant_ok(sender.platform, sender.tenant):
            return False
        if self.is_owner(sender):
            return True
        channel = self._config.channel(sender.platform, chat_id)
        return channel is not None and sender.user_id in channel.approvers
