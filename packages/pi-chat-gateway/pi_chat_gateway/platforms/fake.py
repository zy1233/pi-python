"""An in-memory platform for tests: it records what the gateway sends and lets a test speak."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from dataclasses import dataclass, field

from ..render.cards import Card
from .base import (
    ActionReply,
    Capabilities,
    Chat,
    ImageRef,
    InboundAction,
    InboundMessage,
    InboundSink,
)


@dataclass
class Sent:
    message_id: str
    chat: Chat | None
    user_id: str
    reply_to: str
    cards: list[Card] = field(default_factory=list)  # every version, the newest last

    @property
    def card(self) -> Card:
        return self.cards[-1]

    @property
    def text(self) -> str:
        card = self.card
        return "\n".join([card.title, *card.blocks, card.note])


class FakePlatform:
    def __init__(self, platform_id: str = "fake", tenant: str = "tenant-1") -> None:
        self.id = platform_id
        self.tenant = tenant
        self.capabilities = Capabilities(max_card_bytes=20_000)
        self.sent: list[Sent] = []
        self.reactions: list[tuple[str, str]] = []
        self.images: dict[str, tuple[bytes, str]] = {}
        self.sink: InboundSink | None = None
        self._ids = itertools.count(1)
        self.changed = asyncio.Event()

    async def start(self, sink: InboundSink) -> None:
        self.sink = sink

    async def stop(self) -> None:
        self.sink = None

    def _next_id(self) -> str:
        return f"om_{next(self._ids)}"

    async def send_card(self, chat: Chat, card: Card, *, reply_to: str = "") -> str:
        sent = Sent(self._next_id(), chat, "", reply_to, [card])
        self.sent.append(sent)
        self.changed.set()
        return sent.message_id

    async def send_card_to_user(self, user_id: str, card: Card) -> str:
        sent = Sent(self._next_id(), None, user_id, "", [card])
        self.sent.append(sent)
        self.changed.set()
        return sent.message_id

    async def update_card(self, message_id: str, card: Card) -> None:
        for sent in self.sent:
            if sent.message_id == message_id:
                sent.cards.append(card)
                self.changed.set()
                return
        raise KeyError(message_id)

    async def react(self, message_id: str, reaction: str) -> None:
        self.reactions.append((message_id, reaction))
        self.changed.set()

    async def download_image(self, image: ImageRef) -> tuple[bytes, str]:
        return self.images[image.key]

    # -- the test's side -----------------------------------------------------------------------

    async def say(
        self,
        text: str,
        *,
        user: str = "ou_owner",
        chat_id: str = "oc_dm",
        is_dm: bool = True,
        thread_id: str = "",
        mentions_bot: bool = True,
        images: tuple[ImageRef, ...] = (),
        message_id: str | None = None,
    ) -> str:
        assert self.sink is not None, "the gateway has not started this platform"
        message_id = message_id or self._next_id()
        await self.sink.on_message(
            InboundMessage(
                platform=self.id,
                tenant=self.tenant,
                chat=Chat(self.id, chat_id, is_dm, thread_id),
                message_id=message_id,
                sender_id=user,
                text=text,
                event_id=f"ev_{message_id}",
                mentions_bot=mentions_bot,
                images=images,
            )
        )
        return message_id

    async def tap(
        self, message_id: str, button_label: str, *, user: str = "ou_owner"
    ) -> ActionReply:
        assert self.sink is not None
        sent = self.find(message_id)
        # Newest version first; an old version stands for a client that still shows old buttons.
        button = next(
            b for card in reversed(sent.cards) for b in card.buttons if b.label == button_label
        )
        return await self.sink.on_action(
            InboundAction(
                platform=self.id,
                tenant=self.tenant,
                actor_id=user,
                value=dict(button.value),
                message_id=message_id,
                event_id=f"tap_{next(self._ids)}",
            )
        )

    def find(self, message_id: str) -> Sent:
        return next(s for s in self.sent if s.message_id == message_id)

    def with_buttons(self) -> list[Sent]:
        return [s for s in self.sent if s.card.buttons]

    async def wait_for(self, predicate, timeout: float = 30.0) -> None:
        """Wait until *predicate()* is true; it is checked whenever something is sent."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("the platform never saw what the test waited for")
            self.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), min(remaining, 0.5))
