"""What the gateway needs from a chat platform, and what it gets back from one."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ..render.cards import Card


@dataclass(frozen=True)
class Chat:
    """Where a message goes. A thread is the id of its first message (empty: no thread)."""

    platform: str
    chat_id: str
    is_dm: bool
    thread_id: str = ""


@dataclass(frozen=True)
class ImageRef:
    """An image of an incoming message, fetched only when the gateway decides to use it."""

    message_id: str
    key: str


@dataclass(frozen=True)
class InboundMessage:
    platform: str
    tenant: str
    chat: Chat
    message_id: str
    sender_id: str
    text: str
    event_id: str = ""
    mentions_bot: bool = False
    images: tuple[ImageRef, ...] = ()
    has_files: bool = False


@dataclass(frozen=True)
class InboundAction:
    """A tap on a button of a card the gateway sent."""

    platform: str
    tenant: str
    actor_id: str
    value: Mapping[str, str]
    message_id: str = ""
    chat_id: str = ""
    event_id: str = ""


@dataclass(frozen=True)
class ActionReply:
    """What the platform shows the person who tapped, and the card to put in place, if any."""

    toast: str = ""
    level: str = "info"  # "info" | "success" | "warning" | "error"
    card: Card | None = None


@dataclass(frozen=True)
class Capabilities:
    reactions: bool = True
    threads: bool = True
    max_card_bytes: int = 20_000
    extra: Mapping[str, object] = field(default_factory=dict)


class InboundSink(Protocol):
    async def on_message(self, message: InboundMessage) -> None: ...

    async def on_action(self, action: InboundAction) -> ActionReply: ...


class PlatformAdapter(Protocol):
    id: str
    capabilities: Capabilities

    async def start(self, sink: InboundSink) -> None:
        """Connect and keep delivering events to *sink* until ``stop``."""

    async def stop(self) -> None: ...

    async def send_card(self, chat: Chat, card: Card, *, reply_to: str = "") -> str:
        """Post *card*; with *reply_to*, as a reply (in the thread, for a thread). Its id back."""

    async def send_card_to_user(self, user_id: str, card: Card) -> str:
        """Post *card* to the direct chat with *user_id*. Its id back."""

    async def update_card(self, message_id: str, card: Card) -> None: ...

    async def react(self, message_id: str, reaction: str) -> None:
        """``reaction`` is ``"queued"``, ``"done"`` or ``"failed"``."""

    async def download_image(self, image: ImageRef) -> tuple[bytes, str]:
        """The bytes and the MIME type of an image of an incoming message."""
