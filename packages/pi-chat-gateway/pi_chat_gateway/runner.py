"""One chat (or one thread of a group) and its agent session.

A runner sends a message to the agent as a prompt when the session is idle and as a steer
(``pi/session/steer``, X9) while a turn runs; draws the turn as one card that it updates at most
once per interval; turns each permission request into an approval card; answers the project
trust question with no by itself; and runs ``/btw`` (X10) beside the turn.

The session's permission mode is kept to ``ask`` or ``auto``. If the agent reports
``always-approve`` (its ``agent.toml`` says so, say), the runner puts the session back before
anything runs, and refuses to use a session it cannot put back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from acp.helpers import text_block
from acp.schema import RequestPermissionResponse

from . import trust
from .acp_client import SessionRouter
from .approvals import ApprovalBroker, Pending, Resolution, button_options
from .audit import AuditLog
from .backend import AgentError, AgentPool, AgentProcess, SessionStart
from .config import GatewayConfig, ProjectConfig
from .identity import Authorizer
from .platforms.base import Chat, PlatformAdapter
from .render.cards import Button, Card, approval_cards, notice_card, request_text, turn_card
from .render.text import Redactor, preview, sanitize, utf8_len
from .render.throttle import Throttle
from .render.view import TurnView
from .store import Binding, BindingKey, Store
from .texts import Texts

logger = logging.getLogger(__name__)

NOTICE_DELAY = 0.8
BTW_WINDOW_SECONDS = 600.0
BTW_MAX_TURNS = 20
BTW_MAX_BYTES = 32 * 1024
RECENT_SECONDS = 600.0


class TurnSlots:
    """How many turns run at once, over all chats (``max_active_turns``)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0

    def acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


@dataclass
class Services:
    config: GatewayConfig
    t: Texts
    redact: Redactor
    store: Store
    audit: AuditLog
    auth: Authorizer
    pool: AgentPool
    router: SessionRouter
    broker: ApprovalBroker
    adapters: Mapping[str, PlatformAdapter]
    slots: TurnSlots
    clock: Callable[[], float] = time.time
    closing: bool = False  # set when the gateway shuts down: no new agent is started


@dataclass
class _BtwStream:
    btw_id: str
    card_id: str
    parts: list[str] = field(default_factory=list)
    status: str = "running"  # running | done | error
    error: str = ""
    throttle: Throttle | None = None


class SessionRunner:
    def __init__(
        self, services: Services, key: BindingKey, chat: Chat, project: ProjectConfig
    ) -> None:
        self.s = services
        self.t = services.t
        self.key = key
        self.chat = chat
        self.project = project
        self.adapter = services.adapters[key.platform]
        self.session_id = ""
        self.agent: AgentProcess | None = None
        self.mode: str | None = None
        self.desired_mode = services.config.start_mode
        self.config_options: list[Any] = []
        self.commands: list[str] = []
        self.anchor = ""  # the latest message of this chat, to reply to in a thread
        self.busy = False
        self.view: TurnView | None = None
        self.card_id = ""
        self.throttle: Throttle | None = None
        self.turn_task: asyncio.Task[None] | None = None
        self.queued: deque[tuple[list[Any], str]] = deque()
        self.steers: dict[str, tuple[str, str]] = {}  # steer id -> (message id, preview)
        self._lock = asyncio.Lock()
        self._notes: list[str] = []
        self._notes_task: asyncio.Task[None] | None = None
        self._btw_history: list[dict[str, str]] = []
        self._btw_until = 0.0
        self._btw: dict[str, _BtwStream] = {}
        self._approval_titles: dict[str, str] = {}

    # -- sending -------------------------------------------------------------------------------

    def _reply_to(self) -> str:
        return self.anchor if self.chat.thread_id else ""

    async def send(self, card: Card) -> str:
        return await self.adapter.send_card(self.chat, card, reply_to=self._reply_to())

    async def notify(self, text: str, *, color: str = "grey") -> None:
        """A notice now. The text is sanitized like everything else that is posted."""
        try:
            await self.send(notice_card(self.t, text, redact=self.s.redact, color=color))
        except Exception:
            logger.warning("sending a notice failed", exc_info=True)

    def note_later(self, text: str) -> None:
        """Collect agent messages that come outside a turn, and send them together."""
        self._notes.append(text)
        if self._notes_task is None or self._notes_task.done():
            self._notes_task = asyncio.get_running_loop().create_task(self._send_notes())

    async def _send_notes(self) -> None:
        await asyncio.sleep(NOTICE_DELAY)
        notes, self._notes = self._notes, []
        if notes:
            await self.notify("\n\n".join(notes))

    # -- the session ---------------------------------------------------------------------------

    def _binding(self) -> Binding | None:
        return self.s.store.binding(self.key)

    def _save_binding(self) -> None:
        now = self.s.clock()
        old = self._binding()
        self.s.store.put_binding(
            Binding(
                key=self.key,
                project=self.project.name,
                session_id=self.session_id,
                created_at=old.created_at if old else now,
                updated_at=now,
            )
        )

    async def ensure_session(self, *, fresh: bool = False) -> bool:
        """An agent and a session that is safe to prompt. ``False``: the user has been told why."""
        if self.s.closing:
            return False
        try:
            agent = await self.s.pool.get(self.project)
        except AgentError as exc:
            await self.notify(self.t("agent_start_failed", error=str(exc)), color="red")
            return False
        if agent is self.agent and self.session_id in agent.sessions and not fresh:
            return True
        self.agent = agent
        start: SessionStart | None = None
        binding = self._binding()
        wanted = "" if fresh else (binding.session_id if binding else "")
        if wanted:
            try:
                start = await agent.resume_session(wanted)
            except Exception as exc:
                logger.warning("resuming %s failed; starting a new session", wanted, exc_info=True)
                await self.notify(self.t("resume_failed", error=str(exc) or wanted), color="orange")
        if start is None:
            try:
                start = await agent.new_session()
            except Exception as exc:
                await self.notify(self.t("agent_failed", error=str(exc)), color="red")
                return False
        if self.session_id and self.session_id != start.session_id:
            self.s.router.forget(self.session_id)
        self.session_id = start.session_id
        self.mode = start.mode
        self.config_options = start.config_options
        self.s.router.register(self.session_id, self)
        self._save_binding()
        return await self._enforce_mode()

    async def _enforce_mode(self) -> bool:
        if self.mode == self.desired_mode:
            return True
        assert self.agent is not None
        try:
            await self.agent.set_mode(self.session_id, self.desired_mode)
        except Exception:
            logger.warning("setting the mode of %s failed", self.session_id, exc_info=True)
            if self.mode == "always-approve":
                await self.notify(self.t("mode_unsafe", mode=self.mode), color="red")
                return False
            return True
        if self.mode == "always-approve":
            await self.notify(
                self.t("mode_forced_back", mode=self.mode, target=self.desired_mode), color="orange"
            )
        self.mode = self.desired_mode
        return True

    # -- turns ---------------------------------------------------------------------------------

    async def submit(self, blocks: list[Any], message_id: str, text: str) -> None:
        self.anchor = message_id
        async with self._lock:
            if self.busy:
                await self._steer(blocks, message_id, text)
                return
            await self._start_turn(blocks, message_id)

    async def _start_turn(self, blocks: list[Any], message_id: str) -> None:
        if not self.s.slots.acquire():
            await self.notify(self.t("too_busy", limit=self.s.slots.limit), color="orange")
            return
        started = False
        try:
            if not await self.ensure_session():
                return
            self.busy = True
            self.view = TurnView()
            card, _ = self._turn_card()
            self.card_id = await self.send(card)
            self.throttle = Throttle(self.s.config.update_interval, self._update_turn_card)
            self.throttle.poke()  # anything that came in while the first card was on its way
            self.turn_task = asyncio.get_running_loop().create_task(self._run_turn(blocks))
            started = True
        except Exception as exc:
            logger.warning("starting a turn failed", exc_info=True)
            await self.notify(self.t("agent_failed", error=str(exc)), color="red")
        finally:
            if not started:
                self.busy = False
                self.s.slots.release()

    def _turn_card(self) -> tuple[Card, list[Card]]:
        assert self.view is not None
        return turn_card(
            self.view,
            self.t,
            redact=self.s.redact,
            show_thinking=self.s.config.show_thinking,
            max_bytes=self.adapter.capabilities.max_card_bytes,
        )

    async def _update_turn_card(self) -> None:
        if self.view is None or not self.card_id:
            return
        card, _ = self._turn_card()
        await self.adapter.update_card(self.card_id, card)

    async def _run_turn(self, blocks: list[Any]) -> None:
        view = self.view
        assert view is not None and self.agent is not None
        try:
            response = await self.agent.prompt(self.session_id, blocks)
            view.finish(getattr(response, "stop_reason", None))
        except asyncio.CancelledError:
            view.fail(self.t("turn_interrupted"))
            raise
        except Exception as exc:
            view.fail(str(exc) or type(exc).__name__)
        finally:
            await self._finish_turn()

    async def _finish_turn(self) -> None:
        view = self.view
        self.s.broker.cancel_session(self.key)
        if self.throttle is not None:
            await self.throttle.flush_now()
            await self.throttle.close()
        if view is not None:
            _, overflow = self._turn_card()
            for card in overflow:
                with contextlib.suppress(Exception):
                    await self.send(card)
        self.busy = False
        self.s.slots.release()
        if self.queued and not self.s.closing:
            blocks, message_id = self.queued.popleft()
            asyncio.get_running_loop().create_task(self._start_queued(blocks, message_id))

    async def _start_queued(self, blocks: list[Any], message_id: str) -> None:
        async with self._lock:
            if self.busy:
                self.queued.appendleft((blocks, message_id))
                return
            await self._start_turn(blocks, message_id)

    async def _steer(self, blocks: list[Any], message_id: str, text: str) -> None:
        agent = self.agent
        if agent is None or not agent.info.steer:
            self.queued.append((blocks, message_id))
            await self._react(message_id, "queued")
            return
        steer_id = uuid.uuid4().hex
        try:
            result = await agent.steer(self.session_id, steer_id, blocks)
        except Exception:
            logger.warning("steer failed", exc_info=True)
            await self.notify(self.t("busy_later"), color="orange")
            return
        if result.get("queued") is True:
            self.steers[steer_id] = (message_id, preview(text, 60))
            await self._react(message_id, "queued")
            return
        if result.get("reason") == "idle":
            # The run ended while our prompt call had not returned yet: next in line.
            self.queued.append((blocks, message_id))
            await self._react(message_id, "queued")
            return
        await self.notify(self.t("busy_later"), color="orange")

    async def _react(self, message_id: str, reaction: str) -> None:
        if not message_id:
            return
        try:
            await self.adapter.react(message_id, reaction)
        except Exception:
            logger.info("a reaction failed; sending a line instead", exc_info=True)
            if reaction == "queued":
                await self.notify(self.t("steer_queued"))

    async def stop(self) -> None:
        if not self.busy or self.agent is None:
            await self.notify(self.t("nothing_running"))
            return
        try:
            await self.agent.cancel(self.session_id)
        finally:
            # ACP: the client answers every open permission request of the session as cancelled.
            self.s.broker.cancel_session(self.key)
        await self.notify(self.t("stop_sent"))

    # -- what the agent sends ------------------------------------------------------------------

    async def on_update(self, update: Any) -> None:
        kind = getattr(update, "session_update", None)
        if kind == "user_message_chunk":
            meta = getattr(update, "field_meta", None) or {}
            steer_id = meta.get("pi/steerId") if meta.get("pi/steered") is True else None
            if isinstance(steer_id, str) and steer_id in self.steers:
                message_id, _ = self.steers.pop(steer_id)
                await self._react(message_id, "done")
            return
        if kind == "current_mode_update":
            self.mode = getattr(update, "current_mode_id", None)
            if self.mode == "always-approve":
                await self._enforce_mode()
            return
        if kind == "config_option_update":
            self.config_options = list(getattr(update, "config_options", None) or [])
            return
        if kind == "available_commands_update":
            self.commands = [c.name for c in getattr(update, "available_commands", None) or []]
            return
        if self.busy and self.view is not None:
            if self.view.apply(update) and self.throttle is not None:
                self.throttle.poke()
            return
        if kind == "agent_message_chunk":
            content = getattr(update, "content", None)
            text = getattr(content, "text", "") if getattr(content, "type", None) == "text" else ""
            if text.strip():
                self.note_later(text)

    async def on_queue_update(self, params: dict[str, Any]) -> None:
        cleared = params.get("clearedSteerIds")
        if not isinstance(cleared, list):
            return
        for steer_id in cleared:
            entry = self.steers.pop(steer_id, None) if isinstance(steer_id, str) else None
            if entry is None:
                continue
            message_id, text = entry
            await self._react(message_id, "failed")
            await self.notify(self.t("steer_not_delivered", preview=text), color="orange")

    async def on_permission(self, tool_call: Any, options: list[Any]) -> RequestPermissionResponse:
        if trust.is_trust_like(tool_call, options):
            return await self._refuse_trust(tool_call, options)
        buttons_for = button_options(options)
        if not any(o.kind == "allow_once" for o in buttons_for):
            # Nothing the chat may say yes to (an ``allow_always``-only request, say).
            self.s.audit.write("approval_refused", reason="no allow_once", session=self.session_id)
            return trust.refuse(options)
        title = getattr(tool_call, "title", None) or "tool"
        fallback = self.view.raw_input_of(tool_call.tool_call_id) if self.view else None
        detail = request_text(tool_call, fallback)
        pending = self.s.broker.open(
            session_key=self.key, chat_id=self.chat.chat_id, title=title, options=options
        )
        pending.detail = detail
        try:
            await self._post_approval(pending)
            if self.view is not None:
                self.view.waiting()
                if self.throttle is not None:
                    self.throttle.poke()
            response = await asyncio.shield(pending.future)
        except asyncio.CancelledError:
            self.s.broker.cancel(pending.request_id)
            raise
        finally:
            if self.view is not None:
                self.view.resumed()
                if self.throttle is not None:
                    self.throttle.poke()
        self.s.audit.write(
            "approval",
            platform=self.key.platform,
            session=self.session_id,
            tool=preview(title, 120),
            outcome=pending.resolution.value if pending.resolution else "",
            actor=pending.actor,
        )
        return response

    async def _refuse_trust(self, tool_call: Any, options: list[Any]) -> RequestPermissionResponse:
        explanation = ""
        if self.view is not None and not self.view.tools and self.view.text:
            # The agent puts its explanation right before the question; it is not the answer.
            explanation = self.view.text
            self.view.text_parts.clear()
        self.s.audit.write(
            "trust_refused",
            platform=self.key.platform,
            session=self.session_id,
            project=trust.project_of(tool_call) or str(self.project.path),
            shape_ok=trust.looks_like_trust_request(tool_call) and trust.has_trust_options(options),
        )
        text = self.t("trust_rejected")
        await self.notify(f"{explanation}\n\n{text}" if explanation else text, color="orange")
        return trust.refuse(options)

    def _buttons(self, pending: Pending) -> list[Button]:
        out = []
        for option in pending.options:
            if option.kind == "allow_once":
                label, style = self.t("btn_allow_once"), "primary"
            elif option.kind == "reject_once":
                label, style = self.t("btn_reject"), "danger"
            else:
                label, style = option.name, "danger"
            out.append(
                Button(
                    label=label,
                    style=style,
                    value={
                        "a": "approve",
                        "r": pending.request_id,
                        "n": pending.nonce,
                        "o": option.option_id,
                    },
                )
            )
        return out

    async def _post_approval(self, pending: Pending) -> None:
        card, extra = approval_cards(
            t=self.t,
            request_md=pending.detail,
            redact=self.s.redact,
            buttons=self._buttons(pending),
            max_bytes=self.adapter.capabilities.max_card_bytes,
        )
        owner = self.s.auth.owner_id(self.key.platform)
        to_dm = not self.chat.is_dm and self.s.config.approvals_in_dm and bool(owner)
        if to_dm:
            for part in extra:
                await self.adapter.send_card_to_user(owner, part)
            message_id = await self.adapter.send_card_to_user(owner, card)
            await self.notify(self.t("approval_sent_to_dm"), color="orange")
        else:
            for part in extra:
                await self.send(part)
            message_id = await self.send(card)
        pending.card_targets.append((self.key.platform, message_id))

    def closed_approval_card(self, pending: Pending) -> Card:
        when = datetime.now().strftime("%H:%M")
        who = self._who(pending.actor)
        line = {
            Resolution.ALLOWED: self.t("approval_allowed", who=who, time=when),
            Resolution.REJECTED: self.t("approval_rejected", who=who, time=when),
            Resolution.TIMEOUT: self.t("approval_timeout"),
            Resolution.CANCELLED: self.t("approval_cancelled"),
        }.get(pending.resolution or Resolution.CANCELLED, "")
        card, _ = approval_cards(
            t=self.t,
            request_md=pending.detail,
            redact=self.s.redact,
            buttons=[],
            status_line=f"**{line}**",
            max_bytes=self.adapter.capabilities.max_card_bytes,
        )
        return card

    def _who(self, actor: str) -> str:
        if not actor:
            return ""
        return "owner" if actor == self.s.auth.owner_id(self.key.platform) else actor

    # -- /btw ----------------------------------------------------------------------------------

    async def btw(self, question: str, message_id: str) -> None:
        self.anchor = message_id
        if question.strip().lower() == "end":
            self._btw_history, self._btw_until = [], 0.0
            for stream in list(self._btw.values()):
                if self.agent is not None and stream.status == "running":
                    with contextlib.suppress(Exception):
                        await self.agent.btw_cancel(self.session_id, stream.btw_id)
            await self.notify(self.t("btw_ended"))
            return
        if not question.strip():
            await self.notify(self.t("btw_usage"))
            return
        async with self._lock:
            if not self.busy and not await self.ensure_session():
                return
        agent = self.agent
        if agent is None or not agent.info.btw:
            await self.notify(self.t("btw_unsupported"))
            return
        now = time.monotonic()
        if now > self._btw_until:
            self._btw_history = []
        btw_id = uuid.uuid4().hex
        card_id = await self.send(Card(title=self.t("btw_title"), color="purple", blocks=("...",)))
        stream = _BtwStream(btw_id=btw_id, card_id=card_id)
        stream.throttle = Throttle(self.s.config.update_interval, lambda: self._update_btw(stream))
        self._btw[btw_id] = stream
        try:
            result = await agent.btw(
                self.session_id, btw_id, [text_block(question)], self._btw_history
            )
            if result.get("error"):
                stream.status, stream.error = "error", str(result.get("error"))
            elif stream.status == "running":
                stream.status = "done"
        except Exception as exc:
            stream.status, stream.error = "error", str(exc)
        finally:
            await stream.throttle.flush_now()
            await stream.throttle.close()
            self._btw.pop(btw_id, None)
        answer = "".join(stream.parts)
        if stream.status == "done" and answer:
            self._btw_history = _trim_history(
                [
                    *self._btw_history,
                    {"role": "user", "text": question},
                    {"role": "assistant", "text": answer},
                ]
            )
            self._btw_until = time.monotonic() + BTW_WINDOW_SECONDS

    async def _update_btw(self, stream: _BtwStream) -> None:
        body = sanitize("".join(stream.parts), self.s.redact) or "..."
        blocks = [body]
        if stream.status == "error":
            blocks.append(self.t("btw_failed", error=sanitize(stream.error, self.s.redact)))
        color = {"running": "purple", "done": "purple", "error": "red"}[stream.status]
        await self.adapter.update_card(
            stream.card_id, Card(title=self.t("btw_title"), color=color, blocks=tuple(blocks))
        )

    async def on_btw_update(self, params: dict[str, Any]) -> None:
        stream = self._btw.get(params.get("btwId", ""))
        if stream is None:
            return
        kind = params.get("kind")
        if kind == "text_delta" and isinstance(params.get("delta"), str):
            stream.parts.append(params["delta"])
        elif kind == "done":
            stream.status = "done"
        elif kind == "error":
            stream.status, stream.error = "error", str(params.get("error") or "")
        if stream.throttle is not None:
            stream.throttle.poke()

    # -- settings ------------------------------------------------------------------------------

    async def set_mode(self, mode: str) -> None:
        mode = mode.strip().lower()
        auto = ", auto" if self.s.config.allow_auto else ""
        if not mode:
            await self.notify(
                self.t("mode_current", mode=self.mode or self.desired_mode, auto=auto)
            )
            return
        if mode in ("always-approve", "yolo", "bypasspermissions"):
            await self.notify(self.t("mode_refused_always"), color="orange")
            return
        if mode == "auto" and not self.s.config.allow_auto:
            await self.notify(self.t("mode_refused_auto"), color="orange")
            return
        if mode not in ("ask", "auto"):
            await self.notify(self.t("mode_unknown", mode=mode, auto=auto), color="orange")
            return
        self.desired_mode = mode
        if self.session_id and self.agent is not None and self.agent.alive:
            await self._enforce_mode()
        await self.notify(self.t("mode_set", mode=mode))

    async def option(self, category: str, label_key: str, value: str) -> None:
        label = self.t(label_key)
        async with self._lock:
            if not self.busy and not await self.ensure_session():
                return
        option = next(
            (o for o in self.config_options if getattr(o, "category", None) == category), None
        )
        if option is None:
            await self.notify(self.t("option_missing", label=label))
            return
        values = _option_values(option)
        if not value.strip():
            await self.notify(
                self.t(
                    "option_list",
                    label=label,
                    current=getattr(option, "current_value", ""),
                    values=", ".join(values),
                )
            )
            return
        if value not in values:
            await self.notify(
                self.t("option_unknown", label=label, value=value, values=", ".join(values)),
                color="orange",
            )
            return
        assert self.agent is not None
        try:
            updated = await self.agent.set_config_option(self.session_id, option.id, value)
        except Exception as exc:
            await self.notify(self.t("agent_failed", error=str(exc)), color="red")
            return
        if updated:
            self.config_options = updated
        await self.notify(self.t("option_set", label=label, value=value))

    async def status(self) -> None:
        await self.notify(
            self.t(
                "status_line",
                project=self.project.name,
                session=self.session_id or self.t("session_none"),
                mode=self.mode or self.desired_mode,
                state=self.t("state_busy" if self.busy else "state_idle"),
            )
        )

    async def new_session(self) -> None:
        async with self._lock:
            if self.busy:
                await self.notify(self.t("busy_later"), color="orange")
                return
            if await self.ensure_session(fresh=True):
                await self.notify(self.t("new_session", project=self.project.name))

    async def list_sessions(self) -> list[Any]:
        agent = await self.s.pool.get(self.project)
        return await agent.list_sessions()

    async def resume(self, session_id: str, title: str) -> None:
        async with self._lock:
            if self.busy:
                await self.notify(self.t("busy_later"), color="orange")
                return
            binding = self._binding()
            now = self.s.clock()
            self.s.store.put_binding(
                Binding(
                    self.key,
                    self.project.name,
                    session_id,
                    binding.created_at if binding else now,
                    now,
                )
            )
            if self.session_id:
                self.s.router.forget(self.session_id)
            self.session_id = ""
            if await self.ensure_session() and self.session_id == session_id:
                await self.notify(self.t("resume_done", title=title or session_id))

    def agent_exited(self) -> None:
        """The agent process is gone: open approvals end; the turn's prompt call fails by itself."""
        self.s.broker.cancel_session(self.key)
        for stream in self._btw.values():
            stream.status, stream.error = "error", self.t("agent_exited")
            if stream.throttle is not None:
                stream.throttle.poke()


def _option_values(option: Any) -> list[str]:
    values: list[str] = []
    for item in getattr(option, "options", None) or []:
        if hasattr(item, "value"):
            values.append(item.value)
        for inner in getattr(item, "options", None) or []:
            if hasattr(inner, "value"):
                values.append(inner.value)
    return values


def _trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    history = history[-BTW_MAX_TURNS:]
    while history and sum(utf8_len(h["text"]) for h in history) > BTW_MAX_BYTES:
        history = history[2:] if len(history) > 1 else []
    return history


def is_recent(updated_at: Any, now: float) -> bool:
    """Whether a session changed in the last ten minutes (``updated_at`` is ISO 8601)."""
    if not isinstance(updated_at, str) or not updated_at:
        return False
    try:
        moment = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - moment.timestamp() < RECENT_SECONDS
