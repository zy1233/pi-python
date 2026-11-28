"""Feishu / Lark, through the official SDK ``lark-oapi``.

Events come over the long connection (``lark_oapi.ws.Client``): the gateway connects out, so no
public port is needed. The connection authenticates when it opens and then carries the events in
the clear over TLS. It delivers ``im.message.receive_v1`` (messages) and ``card.action.trigger``
(button taps on cards). In lark-oapi 1.9.0 a card tap arrives as an event frame and the handler's
return value is the callback's answer (a toast and the card to show in place); frames of the old
card-callback type are ignored by the SDK, so the cards use the 2.0 ``callback`` behaviour.

The SDK's long-connection client runs its own event loop, taken from a module global at import
time. It gets a thread and a fresh loop of its own here; handlers hand the events over to the
gateway's loop and return at once. A card tap waits up to 2.5 s for the gateway's answer, inside
the platform's time limit for a callback.

Replies are interactive cards (card JSON 2.0, shared: ``update_multi``), so the gateway can update
them in place and so no message is a plain text that the client would preview links of.

Not verified against a live tenant: the reaction emoji types (``OnIt``, ``DONE``, ``CrossMark``)
and whether Feishu previews links inside a card (the gateway neutralizes links either way).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Mapping
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    Emoji,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

from ..config import PlatformConfig
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

logger = logging.getLogger(__name__)

DOMAINS = {"feishu": lark.FEISHU_DOMAIN, "lark": lark.LARK_DOMAIN}
CALLBACK_WAIT = 2.5
REACTIONS = {"queued": "OnIt", "done": "DONE", "failed": "CrossMark"}
TEMPLATES = {
    "blue": "blue",
    "green": "green",
    "red": "red",
    "grey": "grey",
    "orange": "orange",
    "purple": "purple",
}
TOAST_TYPES = {"info": "info", "success": "success", "warning": "warning", "error": "error"}
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class FeishuError(RuntimeError):
    def __init__(self, what: str, response: Any) -> None:
        code = getattr(response, "code", None)
        msg = getattr(response, "msg", None)
        super().__init__(f"{what} failed: code={code} msg={msg}")
        self.code = code


# -- cards --------------------------------------------------------------------------------------


def card_json(card: Card) -> dict[str, Any]:
    """A ``Card`` as Feishu card JSON 2.0."""
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": block} for block in card.blocks
    ]
    if card.note:
        elements.append({"tag": "markdown", "content": card.note, "text_size": "notation"})
    if card.buttons:
        elements.append(
            {
                "tag": "column_set",
                "flex_mode": "flow",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": button.label},
                                "type": button.style
                                if button.style in ("primary", "danger")
                                else "default",
                                "behaviors": [{"type": "callback", "value": dict(button.value)}],
                            }
                        ],
                    }
                    for button in card.buttons
                ],
            }
        )
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": card.summary or card.title},
        },
        "header": {
            "title": {"tag": "plain_text", "content": card.title},
            "template": TEMPLATES.get(card.color, "blue"),
        },
        "body": {"elements": elements},
    }


def callback_response(reply: ActionReply) -> P2CardActionTriggerResponse:
    body: dict[str, Any] = {}
    if reply.toast:
        body["toast"] = {"type": TOAST_TYPES.get(reply.level, "info"), "content": reply.toast}
    if reply.card is not None:
        body["card"] = {"type": "raw", "data": card_json(reply.card)}
    return P2CardActionTriggerResponse(body)


# -- events -------------------------------------------------------------------------------------


def _text_of_post(content: Any) -> tuple[str, list[str]]:
    """A rich-text (``post``) message: its text and the keys of its images."""
    if isinstance(content, dict) and "content" not in content and content:
        # Older payloads wrap the post in a language key ({"zh_cn": {...}}).
        content = next(iter(content.values()))
    if not isinstance(content, dict):
        return "", []
    lines: list[str] = []
    images: list[str] = []
    title = content.get("title")
    if isinstance(title, str) and title.strip():
        lines.append(title.strip())
    for paragraph in content.get("content") or []:
        parts: list[str] = []
        for item in paragraph or []:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag in ("text", "md") and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif tag == "a":
                parts.append(f"{item.get('text', '')} {item.get('href', '')}".strip())
            elif tag == "img" and isinstance(item.get("image_key"), str):
                images.append(item["image_key"])
            elif tag == "code_block" and isinstance(item.get("text"), str):
                parts.append(f"```{item.get('language', '')}\n{item['text']}\n```")
        lines.append("".join(parts))
    return "\n".join(lines).strip(), images


def _strip_mentions(text: str, mentions: list[Any], bot_open_id: str) -> str:
    for mention in mentions:
        key = getattr(mention, "key", None)
        if not key:
            continue
        mention_id = getattr(getattr(mention, "id", None), "open_id", None)
        if bot_open_id and mention_id == bot_open_id:
            text = text.replace(key, "")
        else:
            text = text.replace(key, f"@{getattr(mention, 'name', '') or 'user'}")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def parse_message_event(data: Any, *, platform_id: str, bot_open_id: str) -> InboundMessage | None:
    """``im.message.receive_v1`` as an ``InboundMessage``; ``None`` for what the gateway ignores
    (messages of bots and apps, the gateway's own included, and malformed events)."""
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    if message is None or sender is None or getattr(sender, "sender_type", None) != "user":
        return None
    open_id = getattr(getattr(sender, "sender_id", None), "open_id", None)
    if not open_id or not message.message_id or not message.chat_id:
        return None
    header = getattr(data, "header", None)
    tenant = getattr(sender, "tenant_key", None) or getattr(header, "tenant_key", None) or ""
    is_dm = message.chat_type == "p2p"
    mentions = list(message.mentions or [])
    mentions_bot = is_dm or any(
        getattr(getattr(m, "id", None), "open_id", None) == bot_open_id for m in mentions
    )
    try:
        content = json.loads(message.content or "{}")
    except json.JSONDecodeError:
        content = {}
    text, image_keys, has_files = "", [], False
    kind = message.message_type
    if kind == "text" and isinstance(content, dict):
        text = str(content.get("text", ""))
    elif kind == "post":
        text, image_keys = _text_of_post(content)
    elif kind == "image" and isinstance(content, dict) and content.get("image_key"):
        image_keys = [content["image_key"]]
    else:
        has_files = kind not in ("text", "post", "image")
    text = _strip_mentions(text, mentions, bot_open_id)
    # A message in a thread names the first message of the thread as its root.
    thread = "" if is_dm else (message.root_id or "")
    return InboundMessage(
        platform=platform_id,
        tenant=tenant,
        chat=Chat(platform_id, message.chat_id, is_dm, thread),
        message_id=message.message_id,
        sender_id=open_id,
        text=text,
        event_id=getattr(header, "event_id", None) or "",
        mentions_bot=mentions_bot,
        images=tuple(ImageRef(message.message_id, key) for key in image_keys),
        has_files=has_files,
    )


def parse_card_action(data: Any, *, platform_id: str) -> InboundAction | None:
    event = getattr(data, "event", None)
    operator = getattr(event, "operator", None)
    action = getattr(event, "action", None)
    open_id = getattr(operator, "open_id", None)
    value = getattr(action, "value", None)
    if not open_id or not isinstance(value, dict):
        return None
    context = getattr(event, "context", None)
    header = getattr(data, "header", None)
    tenant = getattr(operator, "tenant_key", None) or getattr(header, "tenant_key", None) or ""
    return InboundAction(
        platform=platform_id,
        tenant=tenant,
        actor_id=open_id,
        value={str(k): str(v) for k, v in value.items()},
        message_id=getattr(context, "open_message_id", None) or "",
        chat_id=getattr(context, "open_chat_id", None) or "",
        event_id=getattr(header, "event_id", None) or "",
    )


def image_type(data: bytes) -> str:
    for magic, mime in IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


# -- the adapter --------------------------------------------------------------------------------


class FeishuAdapter:
    def __init__(
        self,
        platform: PlatformConfig,
        credentials: Mapping[str, str],
        *,
        client: Any = None,
    ) -> None:
        self.id = platform.id
        self.capabilities = Capabilities(max_card_bytes=20_000)
        self._domain = DOMAINS[platform.domain]
        self._app_id = credentials["app_id"]
        self._app_secret = credentials["app_secret"]
        self._client = client or (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .domain(self._domain)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sink: InboundSink | None = None
        self._thread: threading.Thread | None = None
        self._ws: Any = None
        self.bot_open_id = ""

    # -- life cycle ----------------------------------------------------------------------------

    async def start(self, sink: InboundSink) -> None:
        self._loop = asyncio.get_running_loop()
        self._sink = sink
        self.bot_open_id = await self.fetch_bot_open_id()
        if not self.bot_open_id:
            logger.warning(
                "%s: the bot's open_id is unknown; group @mentions are not recognized", self.id
            )
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_event)
            .register_p2_card_action_trigger(self._on_card_action)
            .build()
        )
        self._ws = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=handler,
            domain=self._domain,
            log_level=lark.LogLevel.WARNING,
        )
        self._thread = threading.Thread(target=self._run_ws, name=f"feishu-{self.id}", daemon=True)
        self._thread.start()

    def _run_ws(self) -> None:
        import lark_oapi.ws.client as ws_client

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_client.loop = loop  # the SDK reads the module global when it runs
        try:
            self._ws.start()
        except Exception:
            logger.exception("%s: the long connection stopped", self.id)

    async def stop(self) -> None:
        # The connection thread is a daemon; it ends with the process.
        self._sink = None

    async def fetch_bot_open_id(self) -> str:
        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({lark.AccessTokenType.TENANT})
            .build()
        )
        try:
            response = await self._client.arequest(request)
            payload = json.loads(response.raw.content or b"{}")
        except Exception:
            logger.warning("%s: reading the bot's identity failed", self.id, exc_info=True)
            return ""
        bot = payload.get("bot") or payload.get("data", {}).get("bot") or {}
        return str(bot.get("open_id") or "")

    # -- events (called on the connection's thread) --------------------------------------------

    def _on_message_event(self, data: Any) -> None:
        sink, loop = self._sink, self._loop
        if sink is None or loop is None:
            return
        message = parse_message_event(data, platform_id=self.id, bot_open_id=self.bot_open_id)
        if message is not None:
            asyncio.run_coroutine_threadsafe(sink.on_message(message), loop)

    def _on_card_action(self, data: Any) -> P2CardActionTriggerResponse:
        sink, loop = self._sink, self._loop
        action = parse_card_action(data, platform_id=self.id)
        if sink is None or loop is None or action is None:
            return P2CardActionTriggerResponse({})
        future = asyncio.run_coroutine_threadsafe(sink.on_action(action), loop)
        try:
            reply = future.result(timeout=CALLBACK_WAIT)
        except FutureTimeout:
            return callback_response(ActionReply(toast="...", level="info"))
        except Exception:
            logger.exception("%s: handling a card tap failed", self.id)
            return P2CardActionTriggerResponse({})
        return callback_response(reply)

    # -- sending -------------------------------------------------------------------------------

    @staticmethod
    def _content(card: Card) -> str:
        return json.dumps(card_json(card), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _message_id(what: str, response: Any) -> str:
        if not response.success():
            raise FeishuError(what, response)
        return response.data.message_id

    async def send_card(self, chat: Chat, card: Card, *, reply_to: str = "") -> str:
        content = self._content(card)
        if reply_to:
            request = (
                ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(content)
                    .reply_in_thread(bool(chat.thread_id))
                    .build()
                )
                .build()
            )
            return self._message_id("reply", await self._client.im.v1.message.areply(request))
        return await self._create("chat_id", chat.chat_id, content)

    async def send_card_to_user(self, user_id: str, card: Card) -> str:
        return await self._create("open_id", user_id, self._content(card))

    async def _create(self, id_type: str, receive_id: str, content: str) -> str:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(content)
                .build()
            )
            .build()
        )
        return self._message_id("send", await self._client.im.v1.message.acreate(request))

    async def update_card(self, message_id: str, card: Card) -> None:
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(PatchMessageRequestBody.builder().content(self._content(card)).build())
            .build()
        )
        response = await self._client.im.v1.message.apatch(request)
        if not response.success():
            raise FeishuError("update", response)

    async def react(self, message_id: str, reaction: str) -> None:
        emoji = REACTIONS.get(reaction)
        if emoji is None:
            return
        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji).build())
                .build()
            )
            .build()
        )
        response = await self._client.im.v1.message_reaction.acreate(request)
        if not response.success():
            raise FeishuError("reaction", response)

    async def download_image(self, image: ImageRef) -> tuple[bytes, str]:
        request = (
            GetMessageResourceRequest.builder()
            .message_id(image.message_id)
            .file_key(image.key)
            .type("image")
            .build()
        )
        response = await self._client.im.v1.message_resource.aget(request)
        if not response.success() or response.file is None:
            raise FeishuError("image download", response)
        data = response.file.read()
        return data, image_type(data)
