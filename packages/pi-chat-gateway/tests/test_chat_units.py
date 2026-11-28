"""Unit tests of the gateway's parts: no agent, no network."""

from __future__ import annotations

import asyncio
import os
import re
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from acp.schema import PermissionOption

from pi_chat_gateway import trust
from pi_chat_gateway.agent_env import agent_environment
from pi_chat_gateway.approvals import ApprovalBroker, Resolution, TapResult
from pi_chat_gateway.commands import parse_command
from pi_chat_gateway.config import (
    ConfigError,
    load_credentials,
    parse_config,
    write_private_file,
)
from pi_chat_gateway.identity import Authorizer, Pairing, PairResult, Sender, hash_code
from pi_chat_gateway.render.cards import approval_cards, request_text, turn_card
from pi_chat_gateway.render.text import REDACTED, Redactor, sanitize, split_markdown, utf8_len
from pi_chat_gateway.render.view import TurnView
from pi_chat_gateway.store import BindingKey, Owner, Store
from pi_chat_gateway.texts import TABLES, Texts

# -- texts --------------------------------------------------------------------------------------


def test_every_language_has_the_same_keys_and_placeholders():
    zh, en = TABLES["zh"], TABLES["en"]
    assert zh.keys() == en.keys()
    for key in zh:
        assert set(re.findall(r"{(\w+)}", zh[key])) == set(re.findall(r"{(\w+)}", en[key])), key


# -- config -------------------------------------------------------------------------------------


def _config_data(project: Path, **gateway: object) -> dict:
    return {
        "gateway": gateway,
        "projects": [{"name": "demo", "path": str(project)}],
        "platforms": [{"id": "feishu-cn", "type": "feishu"}],
    }


def test_config_defaults(home: Path, project: Path):
    config = parse_config(_config_data(project), home=home)
    assert config.start_mode == "ask"
    assert config.allow_auto is False
    assert config.approvals_in_dm is True
    assert config.tool_output == "none"
    assert config.project("demo").path == project.resolve()


@pytest.mark.parametrize(
    ("gateway", "needle"),
    [
        ({"start_mode": "always-approve"}, "always-approve"),
        ({"start_mode": "auto"}, "allow_auto"),
        ({"start_mode": "plan"}, "start_mode"),
        ({"tool_output": "full"}, "tool_output"),
        ({"approval_timeout_minutes": 0}, "approval_timeout_minutes"),
        ({"language": "fr"}, "language"),
        ({"env": "magic"}, "env"),
    ],
)
def test_config_refuses(home: Path, project: Path, gateway: dict, needle: str):
    with pytest.raises(ConfigError, match=needle):
        parse_config(_config_data(project, **gateway), home=home)


def test_config_allows_auto_only_when_allowed(home: Path, project: Path):
    config = parse_config(_config_data(project, start_mode="auto", allow_auto=True), home=home)
    assert config.start_mode == "auto"


def test_a_project_path_must_be_absolute_and_exist(home: Path, project: Path):
    data = _config_data(project)
    data["projects"] = [{"name": "demo", "path": "relative/dir"}]
    with pytest.raises(ConfigError, match="absolute"):
        parse_config(data, home=home)
    data["projects"] = [{"name": "demo", "path": str(project / "missing")}]
    with pytest.raises(ConfigError, match="not a directory"):
        parse_config(data, home=home)


def test_a_channel_must_name_a_known_project(home: Path, project: Path):
    data = _config_data(project)
    data["channels"] = [{"platform": "feishu-cn", "chat_id": "oc_1", "project": "other"}]
    with pytest.raises(ConfigError, match="no project"):
        parse_config(data, home=home)


@pytest.mark.skipif(sys.platform == "win32", reason="mode bits")
def test_credentials_that_others_can_read_are_refused(home: Path, project: Path):
    config = parse_config(_config_data(project), home=home)
    path = home / "chat" / "credentials.toml"
    write_private_file(path, '[feishu-cn]\napp_id = "cli_x"\napp_secret = "s3cret-value"\n')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_credentials(config)["feishu-cn"]["app_secret"] == "s3cret-value"
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_credentials(config)


def test_credentials_must_have_the_required_keys(home: Path, project: Path):
    config = parse_config(_config_data(project), home=home)
    write_private_file(home / "chat" / "credentials.toml", '[feishu-cn]\napp_id = "cli_x"\n')
    with pytest.raises(ConfigError, match="app_secret"):
        load_credentials(config)


# -- pairing and authorization -----------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_code_pairs_once_and_expires(home: Path):
    store = Store(home)
    clock = _Clock()
    pairing = Pairing(store, clock)
    code = pairing.create_code("p")
    assert pairing.attempt(platform="p", user_id="u1", tenant="t", dm_chat_id="c", code=code) is (
        PairResult.OK
    )
    assert store.owner("p").user_id == "u1"
    # The same code cannot pair again, and the platform now has an owner.
    assert (
        pairing.attempt(platform="p", user_id="u2", tenant="t", dm_chat_id="c", code=code)
        is PairResult.TAKEN
    )
    store.clear_owner("p")
    late = pairing.create_code("p")
    clock.now += 601
    assert (
        pairing.attempt(platform="p", user_id="u2", tenant="t", dm_chat_id="c", code=late)
        is PairResult.BAD
    )


def test_five_wrong_codes_lock_pairing(home: Path):
    store = Store(home)
    pairing = Pairing(store, _Clock())
    good = pairing.create_code("p")
    for _ in range(5):
        assert (
            pairing.attempt(platform="p", user_id="x", tenant="t", dm_chat_id="c", code="AAAA-AAAA")
            is PairResult.BAD
        )
    assert (
        pairing.attempt(platform="p", user_id="x", tenant="t", dm_chat_id="c", code=good)
        is PairResult.LOCKED
    )


def test_codes_are_stored_as_hashes(home: Path):
    store = Store(home)
    code = Pairing(store).create_code("p")
    raw = (home / "chat" / "state.sqlite").read_bytes()
    assert code.replace("-", "").encode() not in raw
    assert hash_code(code.lower()) == hash_code(code)


def test_the_tenant_of_the_owner_is_the_only_tenant(home: Path, project: Path):
    config = parse_config(
        {
            **_config_data(project),
            "channels": [
                {
                    "platform": "feishu-cn",
                    "chat_id": "oc_group",
                    "project": "demo",
                    "users": ["ou_friend"],
                    "approvers": ["ou_lead"],
                }
            ],
        },
        home=home,
    )
    store = Store(home)
    auth = Authorizer(config, store)
    owner = Sender("feishu-cn", "t1", "ou_owner")
    assert auth.tenant_ok("feishu-cn", "anything")  # nothing known before pairing
    store.set_owner(Owner("feishu-cn", "ou_owner", "t1", "oc_dm", 0.0))
    assert auth.may_talk(owner, "oc_dm", is_dm=True)
    assert not auth.may_talk(Sender("feishu-cn", "t2", "ou_owner"), "oc_dm", is_dm=True)
    assert not auth.may_talk(Sender("feishu-cn", "t1", "ou_friend"), "oc_dm", is_dm=True)
    assert auth.may_talk(Sender("feishu-cn", "t1", "ou_friend"), "oc_group", is_dm=False)
    assert not auth.may_talk(Sender("feishu-cn", "t1", "ou_friend"), "oc_other", is_dm=False)
    assert not auth.may_approve(Sender("feishu-cn", "t1", "ou_friend"), "oc_group")
    assert auth.may_approve(Sender("feishu-cn", "t1", "ou_lead"), "oc_group")
    assert auth.may_approve(owner, "oc_group")


def test_store_first_time_sees_each_event_once(home: Path):
    store = Store(home)
    assert store.first_time("e1", 1.0)
    assert not store.first_time("e1", 2.0)
    store.prune(1.0 + 25 * 3600)
    assert store.first_time("e1", 3.0)


def test_store_keeps_bindings(home: Path):
    from pi_chat_gateway.store import Binding

    store = Store(home)
    key = BindingKey("p", "oc_1", "om_root")
    store.put_binding(Binding(key, "demo", "sess-1", 1.0, 2.0))
    assert Store(home).binding(key).session_id == "sess-1"


# -- trust --------------------------------------------------------------------------------------


def _options(*pairs: tuple[str, str]) -> list[PermissionOption]:
    return [PermissionOption(option_id=i, name=i, kind=k) for i, k in pairs]


TRUST_OPTIONS = _options(("dont-trust-project", "reject_once"), ("trust-project", "allow_always"))


def test_the_trust_question_is_always_refused():
    call = SimpleNamespace(tool_call_id="trust-abc", raw_input={"project": "/p", "resources": []})
    assert trust.looks_like_trust_request(call) and trust.has_trust_options(TRUST_OPTIONS)
    assert trust.is_trust_like(call, TRUST_OPTIONS)
    assert trust.refuse(TRUST_OPTIONS).outcome.option_id == "dont-trust-project"


def test_anything_that_looks_like_trust_is_refused_too():
    odd = SimpleNamespace(tool_call_id="trust-x", raw_input=None)
    options = _options(("ok", "allow_once"), ("no", "reject_once"))
    assert trust.is_trust_like(odd, options)
    assert trust.refuse(options).outcome.option_id == "no"
    sneaky = SimpleNamespace(tool_call_id="call_1", raw_input={})
    assert trust.is_trust_like(sneaky, _options(("trust-project", "allow_once")))
    assert trust.refuse(_options(("trust-project", "allow_once"))).outcome.outcome == "cancelled"
    plain = SimpleNamespace(tool_call_id="call_2", raw_input={"command": "ls"})
    assert not trust.is_trust_like(plain, options)


# -- approvals ----------------------------------------------------------------------------------

TOOL_OPTIONS = _options(("allow-once", "allow_once"), ("reject-once", "reject_once"))


async def test_the_first_valid_tap_wins_and_strangers_are_refused():
    closed = []
    broker = ApprovalBroker(timeout=60, on_closed=closed.append)
    pending = broker.open(session_key="k", chat_id="c", title="bash", options=TOOL_OPTIONS)

    def ok(p):
        return True

    def stranger(p):
        return False

    result, _ = broker.tap(
        request_id=pending.request_id,
        nonce="wrong",
        option_id="allow-once",
        actor="a",
        may_approve=ok,
    )
    assert result is TapResult.STALE
    result, _ = broker.tap(
        request_id=pending.request_id,
        nonce=pending.nonce,
        option_id="allow-once",
        actor="s",
        may_approve=stranger,
    )
    assert result is TapResult.FORBIDDEN
    result, _ = broker.tap(
        request_id=pending.request_id,
        nonce=pending.nonce,
        option_id="allow-always",
        actor="a",
        may_approve=ok,
    )
    assert result is TapResult.BAD_OPTION
    result, _ = broker.tap(
        request_id=pending.request_id,
        nonce=pending.nonce,
        option_id="allow-once",
        actor="a",
        may_approve=ok,
    )
    assert result is TapResult.OK
    assert (await pending.future).outcome.option_id == "allow-once"
    assert pending.resolution is Resolution.ALLOWED and closed == [pending]
    again, _ = broker.tap(
        request_id=pending.request_id,
        nonce=pending.nonce,
        option_id="reject-once",
        actor="a",
        may_approve=ok,
    )
    assert again is TapResult.UNKNOWN


async def test_an_unanswered_approval_is_rejected():
    broker = ApprovalBroker(timeout=0.05)
    pending = broker.open(session_key="k", chat_id="c", title="bash", options=TOOL_OPTIONS)
    response = await asyncio.wait_for(pending.future, 2)
    assert response.outcome.option_id == "reject-once"
    assert pending.resolution is Resolution.TIMEOUT


async def test_allow_always_is_never_a_button():
    broker = ApprovalBroker(timeout=60)
    options = _options(("a", "allow_always"), ("o", "allow_once"), ("r", "reject_once"))
    pending = broker.open(session_key="k", chat_id="c", title="x", options=options)
    assert [o.option_id for o in pending.options] == ["o", "r"]
    broker.cancel_session("k")
    assert (await pending.future).outcome.outcome == "cancelled"


# -- commands -----------------------------------------------------------------------------------


def test_commands():
    assert parse_command("/btw what is this?").arg == "what is this?"
    assert parse_command("  /STOP ").name == "stop"
    assert parse_command("/skill:review") is None  # the agent's own commands pass through
    assert parse_command("hello /new") is None


# -- rendering ----------------------------------------------------------------------------------


def test_links_and_images_are_not_fetchable():
    text = (
        "See [the docs](https://evil.example/?q=SECRET) and ![x](https://evil.example/p.png) "
        "or https://evil.example/raw, <https://evil.example/auto>."
    )
    out = sanitize(text)
    assert "](https://" not in out and "![" not in out
    assert "`https://evil.example/raw`," in out
    assert "`https://evil.example/auto`" in out
    assert "the docs (`https://evil.example/?q=SECRET`)" in out


def test_platform_tags_are_shown_as_text():
    out = sanitize('<at id=all></at> hi <font color="red">x</font>')
    assert "<at" not in out and "<font" not in out
    assert "<​at" in out


def test_code_is_left_alone_except_for_secrets():
    text = (
        "```bash\ncurl https://example.com <at id=all>\nexport KEY=sk-abcdefghijklmnopqrstuvwx\n```"
    )
    out = sanitize(text, Redactor())
    assert "curl https://example.com <at id=all>" in out
    assert "sk-abcdefghijklmnopqrstuvwx" not in out and REDACTED in out
    assert sanitize("`<at id=all>` and https://x.y") == "`<at id=all>` and `https://x.y`"


def test_known_secret_values_are_redacted_exactly():
    redact = Redactor(["value-of-a-secret", "short"])
    out = sanitize("a value-of-a-secret b short", redact)
    assert "value-of-a-secret" not in out
    assert "short" in out  # too short to replace safely


def test_split_keeps_code_blocks_closed():
    body = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    text = f"intro\n\n```python\n{body}\n```\n\nend"
    pieces = split_markdown(text, 2000)
    assert len(pieces) > 1
    for piece in pieces:
        assert utf8_len(piece) <= 2000
        assert piece.count("```") % 2 == 0, piece[:80]
    assert pieces[1].startswith("```python")


def test_split_counts_utf8_bytes():
    text = "中" * 3000  # 9000 bytes
    pieces = split_markdown(text, 1000)
    assert all(utf8_len(p) <= 1000 for p in pieces)
    assert "".join(pieces) == text


def test_the_turn_card_shows_tools_and_the_answer():
    view = TurnView(clock=lambda: 1.0)
    view.apply(
        SimpleNamespace(
            session_update="tool_call",
            tool_call_id="c1",
            title="bash: npm test",
            kind="execute",
            status="in_progress",
            raw_input={"command": "npm test"},
        )
    )
    view.apply(
        SimpleNamespace(
            session_update="agent_message_chunk",
            content=SimpleNamespace(type="text", text="All green. https://ci.example/run/1"),
        )
    )
    view.finish("end_turn")
    card, extra = turn_card(view, Texts("en"), redact=None, show_thinking=False)
    body = "\n".join(card.blocks)
    assert "`bash: npm test`" in body
    assert "`https://ci.example/run/1`" in body
    assert card.title.endswith("Done") and card.note.startswith("end_turn")
    assert extra == []


def test_a_long_answer_continues_in_more_cards():
    view = TurnView()
    view.apply(
        SimpleNamespace(
            session_update="agent_message_chunk",
            content=SimpleNamespace(type="text", text="\n\n".join("p" * 900 for _ in range(30))),
        )
    )
    view.finish("end_turn")
    card, extra = turn_card(view, Texts("en"), redact=None, show_thinking=False, max_bytes=6000)
    assert extra, "the rest of the answer goes into more cards"
    assert all(utf8_len("\n".join(c.blocks)) < 6000 for c in [card, *extra])


def test_an_approval_shows_the_whole_request():
    command = "\n".join(f"echo step {i}" for i in range(3000))
    call = SimpleNamespace(title="bash", raw_input={"command": command, "timeout": 30})
    detail = request_text(call)
    assert '"timeout": 30' in detail
    _, extra = approval_cards(
        t=Texts("en"), request_md=detail, redact=None, buttons=[], max_bytes=8000
    )
    assert extra, "a request that does not fit is sent in full, in more cards"
    joined = "\n".join(block for c in extra for block in c.blocks)
    assert "echo step 0" in joined and "echo step 2999" in joined


# -- the agent's environment --------------------------------------------------------------------


def test_no_credential_reaches_the_agent(tmp_path: Path):
    env = agent_environment(
        base={
            "PATH": "/usr/bin",
            "FEISHU_COPY": "the-app-secret-123",
            "ZYCODE_CHAT_TOKEN": "x",
            "HOME": "/h",
        },
        secrets=["the-app-secret-123"],
        extra={"PI_USE_MOCK": "1"},
        home=tmp_path,
    )
    assert "FEISHU_COPY" not in env and "ZYCODE_CHAT_TOKEN" not in env
    assert env["PI_HOME"] == str(tmp_path) and env["PI_USE_MOCK"] == "1"
    with pytest.raises(ValueError):
        agent_environment(
            base={},
            secrets=["the-app-secret-123"],
            extra={"X": "the-app-secret-123"},
            home=tmp_path,
        )
