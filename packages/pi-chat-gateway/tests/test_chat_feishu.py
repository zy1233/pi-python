"""The Feishu adapter, with event payloads built by hand and a fake SDK client: no network.

What these tests cannot show: that a live tenant accepts the cards and the reaction types, and
that it delivers ``card.action.trigger`` over the long connection the way lark-oapi 1.9.0 reads
it. The README lists them as not verified.
"""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

lark = pytest.importorskip("lark_oapi")

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: E402
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger  # noqa: E402

from pi_chat_gateway.config import PlatformConfig  # noqa: E402
from pi_chat_gateway.platforms.base import ActionReply, Chat, ImageRef  # noqa: E402
from pi_chat_gateway.platforms.feishu import (  # noqa: E402
    FeishuAdapter,
    callback_response,
    card_json,
    image_type,
    parse_card_action,
    parse_message_event,
)
from pi_chat_gateway.render.cards import Button, Card  # noqa: E402

BOT = "ou_bot"


def _event(
    message: dict, *, sender_type: str = "user", sender: str = "ou_owner"
) -> P2ImMessageReceiveV1:
    payload = {
        "schema": "2.0",
        "header": {"event_id": "ev_1", "event_type": "im.message.receive_v1", "tenant_key": "tk"},
        "event": {
            "sender": {
                "sender_id": {"open_id": sender},
                "sender_type": sender_type,
                "tenant_key": "tk",
            },
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "hello"}),
                **message,
            },
        },
    }
    return lark.JSON.unmarshal(json.dumps(payload), P2ImMessageReceiveV1)


def test_a_direct_message():
    message = parse_message_event(_event({}), platform_id="f", bot_open_id=BOT)
    assert message.chat == Chat("f", "oc_1", True, "")
    assert (message.text, message.sender_id, message.tenant) == ("hello", "ou_owner", "tk")
    assert message.mentions_bot and message.event_id == "ev_1"


def test_a_group_mention_and_a_thread_reply():
    mention = {"key": "@_user_1", "id": {"open_id": BOT}, "name": "zycode"}
    other = {"key": "@_user_2", "id": {"open_id": "ou_bob"}, "name": "Bob"}
    root = parse_message_event(
        _event(
            {
                "chat_type": "group",
                "content": json.dumps({"text": "@_user_1 ask @_user_2 about it"}),
                "mentions": [mention, other],
            }
        ),
        platform_id="f",
        bot_open_id=BOT,
    )
    assert root.mentions_bot and root.text == "ask @Bob about it"
    assert root.chat.thread_id == ""  # the gateway starts a thread on it
    reply = parse_message_event(
        _event(
            {"chat_type": "group", "root_id": "om_root", "content": json.dumps({"text": "more"})}
        ),
        platform_id="f",
        bot_open_id=BOT,
    )
    assert reply.chat.thread_id == "om_root" and not reply.mentions_bot


def test_messages_of_apps_are_ignored():
    assert (
        parse_message_event(_event({}, sender_type="app"), platform_id="f", bot_open_id=BOT) is None
    )


def test_rich_text_images_and_files():
    post = {
        "title": "Bug",
        "content": [
            [{"tag": "text", "text": "see "}, {"tag": "a", "text": "log", "href": "https://x.y"}],
            [{"tag": "img", "image_key": "img_1"}],
        ],
    }
    message = parse_message_event(
        _event({"message_type": "post", "content": json.dumps(post)}),
        platform_id="f",
        bot_open_id=BOT,
    )
    assert message.text == "Bug\nsee log https://x.y" and message.images == (
        ImageRef("om_1", "img_1"),
    )
    image = parse_message_event(
        _event({"message_type": "image", "content": json.dumps({"image_key": "img_2"})}),
        platform_id="f",
        bot_open_id=BOT,
    )
    assert image.images == (ImageRef("om_1", "img_2"),) and image.text == ""
    file = parse_message_event(
        _event({"message_type": "file", "content": json.dumps({"file_key": "f"})}),
        platform_id="f",
        bot_open_id=BOT,
    )
    assert file.has_files


def _card_action(value: dict) -> P2CardActionTrigger:
    payload = {
        "schema": "2.0",
        "header": {"event_id": "ev_tap", "event_type": "card.action.trigger", "tenant_key": "tk"},
        "event": {
            "operator": {"open_id": "ou_owner", "tenant_key": "tk"},
            "action": {"tag": "button", "value": value},
            "context": {"open_message_id": "om_card", "open_chat_id": "oc_1"},
        },
    }
    return lark.JSON.unmarshal(json.dumps(payload), P2CardActionTrigger)


def test_a_card_tap():
    action = parse_card_action(_card_action({"a": "approve", "r": "r1"}), platform_id="f")
    assert action.actor_id == "ou_owner" and action.tenant == "tk"
    assert dict(action.value) == {"a": "approve", "r": "r1"}
    assert (action.message_id, action.chat_id, action.event_id) == ("om_card", "oc_1", "ev_tap")


def test_card_json_is_a_shared_2_0_card_with_callback_buttons():
    card = Card(
        title="zycode · Approval needed",
        color="orange",
        blocks=("**bash**", "```bash\nls\n```"),
        buttons=(Button("Allow once", {"a": "approve", "o": "allow-once"}, "primary"),),
        note="end_turn",
    )
    data = card_json(card)
    assert data["schema"] == "2.0" and data["config"]["update_multi"] is True
    assert data["header"]["template"] == "orange"
    elements = data["body"]["elements"]
    assert [e["tag"] for e in elements] == ["markdown", "markdown", "markdown", "column_set"]
    button = elements[-1]["columns"][0]["elements"][0]
    assert button["type"] == "primary"
    assert button["behaviors"] == [
        {"type": "callback", "value": {"a": "approve", "o": "allow-once"}}
    ]
    response = callback_response(ActionReply(toast="ok", level="success", card=card))
    marshaled = json.loads(lark.JSON.marshal(response))
    assert marshaled["toast"] == {"type": "success", "content": "ok"}
    assert marshaled["card"]["type"] == "raw" and marshaled["card"]["data"]["schema"] == "2.0"


def test_image_types():
    assert image_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert image_type(b"\xff\xd8\xffrest") == "image/jpeg"
    assert image_type(b"RIFF\x00\x00\x00\x00WEBPrest") == "image/webp"


# -- the adapter with a fake client -------------------------------------------------------------


def _ok(**data):
    return SimpleNamespace(success=lambda: True, data=SimpleNamespace(**data), code=0, msg="")


class _Calls:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def record(self, name: str, result):
        async def call(request):
            self.calls.append((name, request))
            return result

        return call


def _client(calls: _Calls):
    message = SimpleNamespace(
        acreate=calls.record("create", _ok(message_id="om_new")),
        areply=calls.record("reply", _ok(message_id="om_reply")),
        apatch=calls.record("patch", _ok()),
    )
    reaction = SimpleNamespace(acreate=calls.record("reaction", _ok()))
    resource = SimpleNamespace(
        aget=calls.record(
            "resource",
            SimpleNamespace(success=lambda: True, file=io.BytesIO(b"\x89PNG\r\n\x1a\nxx")),
        )
    )
    info = SimpleNamespace(
        raw=SimpleNamespace(content=json.dumps({"bot": {"open_id": BOT}}).encode())
    )
    return SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message=message, message_reaction=reaction, message_resource=resource
            )
        ),
        arequest=calls.record("bot_info", info),
    )


def _adapter(calls: _Calls) -> FeishuAdapter:
    platform = PlatformConfig(id="f", type="feishu", domain="lark")
    return FeishuAdapter(platform, {"app_id": "cli_x", "app_secret": "s"}, client=_client(calls))


async def test_sending_replying_updating_and_reacting():
    calls = _Calls()
    adapter = _adapter(calls)
    assert await adapter.fetch_bot_open_id() == BOT
    card = Card(title="t", blocks=("b",))
    assert await adapter.send_card(Chat("f", "oc_1", True), card) == "om_new"
    _, create = calls.calls[-1]
    assert create.receive_id_type == "chat_id" and create.request_body.receive_id == "oc_1"
    assert create.request_body.msg_type == "interactive"
    assert json.loads(create.request_body.content)["schema"] == "2.0"
    assert (
        await adapter.send_card(Chat("f", "oc_g", False, "om_root"), card, reply_to="om_root")
        == "om_reply"
    )
    _, reply = calls.calls[-1]
    assert reply.message_id == "om_root" and reply.request_body.reply_in_thread is True
    await adapter.send_card_to_user("ou_owner", card)
    assert calls.calls[-1][1].receive_id_type == "open_id"
    await adapter.update_card("om_new", card)
    assert calls.calls[-1][0] == "patch" and calls.calls[-1][1].message_id == "om_new"
    await adapter.react("om_1", "queued")
    assert calls.calls[-1][1].request_body.reaction_type.emoji_type == "OnIt"
    data, mime = await adapter.download_image(ImageRef("om_1", "img_1"))
    assert mime == "image/png" and data.startswith(b"\x89PNG")


async def test_a_tap_is_answered_from_the_gateways_loop():
    adapter = _adapter(_Calls())
    replies = []

    class Sink:
        async def on_message(self, message):
            return None

        async def on_action(self, action):
            replies.append(action)
            return ActionReply(toast="done", level="success", card=Card(title="closed"))

    adapter._loop = asyncio.get_running_loop()
    adapter._sink = Sink()
    # The SDK calls the handler on the connection's own thread.
    response = await asyncio.to_thread(adapter._on_card_action, _card_action({"a": "approve"}))
    assert replies and replies[0].value["a"] == "approve"
    assert (
        response.toast.content == "done"
        and response.card.data["header"]["title"]["content"] == "closed"
    )
