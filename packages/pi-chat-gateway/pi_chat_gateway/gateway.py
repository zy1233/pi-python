"""The gateway: chat events in, runners and agents behind, cards out.

Every incoming event goes through the same gate, in this order: seen before (dropped); a
pairing code in a direct chat; the tenant; may this person talk here (``Authorizer``); in a
group, is the bot mentioned. Whatever fails the gate is dropped without an answer, so a
stranger learns nothing. Taps on buttons go through the same tenant and person checks.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from acp.helpers import image_block, text_block

from pi_agent_cli.config import read_local_env

from .acp_client import GatewayClient, SessionRouter
from .agent_env import agent_environment, login_shell_environment
from .approvals import ApprovalBroker, Pending, TapResult
from .audit import AuditLog
from .backend import AgentPool, AgentProcess, resolve_agent_command
from .commands import parse_command
from .config import GatewayConfig, ProjectConfig, all_secret_values
from .identity import Authorizer, Pairing, PairResult, Sender
from .platforms.base import ActionReply, Chat, InboundAction, InboundMessage, PlatformAdapter
from .render.cards import Button, Card, notice_card
from .render.text import Redactor, preview
from .runner import Services, SessionRunner, TurnSlots, is_recent
from .store import Binding, BindingKey, Store
from .texts import Texts

logger = logging.getLogger(__name__)

HOUSEKEEPING_SECONDS = 60.0
RESUME_LIST_LIMIT = 10


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        credentials: Mapping[str, Mapping[str, str]],
        adapters: Iterable[PlatformAdapter],
        *,
        store: Store | None = None,
        base_env: Mapping[str, str] | None = None,
        agent_command: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config
        self.t = Texts(config.language)
        secrets = all_secret_values(credentials)
        local_values = [v for v in read_local_env(config.home).values() if v]
        self.redact = Redactor([*secrets, *local_values])
        self.store = store or Store(config.home)
        self.audit = AuditLog(config.home)
        self.auth = Authorizer(config, self.store)
        self.pairing = Pairing(self.store)
        self.router = SessionRouter()
        self.client = GatewayClient(self.router)
        if base_env is None:
            base_env = (
                login_shell_environment() if config.env_mode == "login-shell" else None
            ) or dict(os.environ)
        env = agent_environment(
            base=base_env, secrets=secrets, extra=config.agent_env, home=config.home
        )
        self.pool = AgentPool(
            config,
            client=self.client,
            command=agent_command or resolve_agent_command(config),
            env=env,
            on_exit=self._agent_exited,
        )
        self.broker = ApprovalBroker(
            timeout=config.approval_timeout, on_closed=self._approval_closed
        )
        self.adapters: dict[str, PlatformAdapter] = {a.id: a for a in adapters}
        self.services = Services(
            config=config,
            t=self.t,
            redact=self.redact,
            store=self.store,
            audit=self.audit,
            auth=self.auth,
            pool=self.pool,
            router=self.router,
            broker=self.broker,
            adapters=self.adapters,
            slots=TurnSlots(config.max_active_turns),
        )
        self.runners: dict[BindingKey, SessionRunner] = {}
        self._dm_projects: dict[tuple[str, str], str] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._stopped = asyncio.Event()

    # -- life cycle ----------------------------------------------------------------------------

    async def start(self) -> None:
        for platform in self.config.platforms:
            if platform.id not in self.adapters:
                raise ValueError(f"no adapter for platform {platform.id!r}")
        for adapter in self.adapters.values():
            await adapter.start(self)
        self._spawn(self._housekeeping())

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stopped.wait()
        finally:
            await self.close()

    def stop(self) -> None:
        self._stopped.set()

    async def close(self) -> None:
        self.services.closing = True
        for adapter in self.adapters.values():
            with contextlib.suppress(Exception):
                await adapter.stop()
        for task in list(self._background):
            task.cancel()
        await self.pool.close_all()
        self.store.close()

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(HOUSEKEEPING_SECONDS)
            try:
                await self.pool.reap_idle(self._project_busy)
                self.store.prune(time.time())
            except Exception:
                logger.warning("housekeeping failed", exc_info=True)

    def _project_busy(self, project: str) -> bool:
        return any(r.busy for r in self.runners.values() if r.project.name == project)

    def _agent_exited(self, agent: AgentProcess) -> None:
        for runner in self.runners.values():
            if runner.agent is agent:
                runner.agent_exited()

    # -- messages ------------------------------------------------------------------------------

    async def on_message(self, message: InboundMessage) -> None:
        try:
            await self._on_message(message)
        except Exception:
            logger.exception("handling a message failed")

    async def _on_message(self, message: InboundMessage) -> None:
        if not self.store.first_time(
            f"{message.platform}:{message.event_id or message.message_id}", time.time()
        ):
            return
        adapter = self.adapters.get(message.platform)
        if adapter is None:
            return
        command = parse_command(message.text)
        if command is not None and command.name == "pair":
            await self._pair(adapter, message, command.arg)
            return
        sender = Sender(message.platform, message.tenant, message.sender_id)
        if not self.auth.may_talk(sender, message.chat.chat_id, is_dm=message.chat.is_dm):
            self.audit.write(
                "ignored",
                platform=message.platform,
                user=message.sender_id,
                chat=message.chat.chat_id,
                reason="not allowed",
            )
            return
        if not message.chat.is_dm and not message.mentions_bot:
            return
        if message.chat.is_dm and self.auth.is_owner(sender):
            owner = self.store.owner(message.platform)
            if owner is not None and owner.dm_chat_id != message.chat.chat_id:
                self.store.update_owner_dm(message.platform, message.chat.chat_id)

        runner, message = await self._runner_for(adapter, message, command)
        if runner is None:
            return
        if command is not None:
            await self._command(runner, sender, command.name, command.arg, message)
            return
        if message.has_files:
            await runner.notify(self.t("file_unsupported"), color="orange")
        blocks = await self._blocks(runner, adapter, message)
        if blocks:
            await runner.submit(blocks, message.message_id, message.text)

    async def _pair(self, adapter: PlatformAdapter, message: InboundMessage, code: str) -> None:
        chat = message.chat
        if not chat.is_dm:
            await adapter.send_card(chat, notice_card(self.t, self.t("pair_dm_only")))
            return
        result = self.pairing.attempt(
            platform=message.platform,
            user_id=message.sender_id,
            tenant=message.tenant,
            dm_chat_id=chat.chat_id,
            code=code,
        )
        self.audit.write(
            "pair", platform=message.platform, user=message.sender_id, result=result.value
        )
        key = {
            PairResult.OK: "pair_ok",
            PairResult.BAD: "pair_bad",
            PairResult.LOCKED: "pair_locked",
            PairResult.TAKEN: "pair_taken",
        }[result]
        color = "green" if result is PairResult.OK else "orange"
        await adapter.send_card(chat, notice_card(self.t, self.t(key), color=color))

    def _key_for(self, message: InboundMessage) -> BindingKey:
        chat = message.chat
        if chat.is_dm:
            return BindingKey(chat.platform, chat.chat_id, "")
        return BindingKey(chat.platform, chat.chat_id, chat.thread_id or message.message_id)

    def _names(self) -> str:
        return ", ".join(p.name for p in self.config.projects)

    async def _project_for(
        self, adapter: PlatformAdapter, message: InboundMessage, key: BindingKey, command: Any
    ) -> tuple[ProjectConfig | None, InboundMessage]:
        chat = message.chat
        if not chat.is_dm:
            channel = self.config.channel(chat.platform, chat.chat_id)
            return (self.config.project(channel.project) if channel else None), message
        binding = self.store.binding(key)
        chosen = self._dm_projects.get((chat.platform, chat.chat_id))
        text = message.text.strip()
        if text.startswith("#"):
            name, _, rest = text[1:].partition(" ")
            project = self.config.project(name)
            if project is not None:
                self._dm_projects[(chat.platform, chat.chat_id)] = project.name
                return project, _with_text(message, rest.strip())
        if chosen:
            return self.config.project(chosen), message
        if binding is not None and self.config.project(binding.project) is not None:
            return self.config.project(binding.project), message
        if len(self.config.projects) == 1:
            return self.config.projects[0], message
        if command is not None and command.name in ("help", "project"):
            return None, message
        await adapter.send_card(
            chat, notice_card(self.t, self.t("choose_project", names=self._names()))
        )
        return None, message

    async def _runner_for(
        self, adapter: PlatformAdapter, message: InboundMessage, command: Any
    ) -> tuple[SessionRunner | None, InboundMessage]:
        """The runner of the chat or thread, and the message without a leading ``#project``."""
        key = self._key_for(message)
        if command is not None and command.name == "project":
            await self._choose_project(adapter, message, key, command.arg)
            return None, message
        project, message = await self._project_for(adapter, message, key, command)
        if project is None:
            if command is not None and command.name == "help":
                await adapter.send_card(message.chat, notice_card(self.t, self.t("help")))
            return None, message
        runner = self.runners.get(key)
        if runner is None or runner.project.name != project.name:
            chat = Chat(
                message.chat.platform,
                message.chat.chat_id,
                message.chat.is_dm,
                "" if message.chat.is_dm else key.thread_key,
            )
            runner = SessionRunner(self.services, key, chat, project)
            self.runners[key] = runner
        return runner, message

    async def _choose_project(
        self, adapter: PlatformAdapter, message: InboundMessage, key: BindingKey, name: str
    ) -> None:
        chat = message.chat
        if not chat.is_dm:
            channel = self.config.channel(chat.platform, chat.chat_id)
            fixed = channel.project if channel else ""
            await adapter.send_card(chat, notice_card(self.t, self.t("project_fixed", name=fixed)))
            return
        project = self.config.project(name.strip())
        if project is None:
            await adapter.send_card(
                chat,
                notice_card(self.t, self.t("unknown_project", name=name, names=self._names())),
            )
            return
        runner = self.runners.get(key)
        if runner is not None and runner.busy:
            await runner.notify(self.t("busy_later"), color="orange")
            return
        self._dm_projects[(chat.platform, chat.chat_id)] = project.name
        if runner is not None and runner.project.name != project.name:
            del self.runners[key]
        binding = self.store.binding(key)
        if binding is not None and binding.project != project.name:
            self.store.put_binding(Binding(key, project.name, "", binding.created_at, time.time()))
        await adapter.send_card(chat, notice_card(self.t, self.t("project_set", name=project.name)))

    async def _command(
        self, runner: SessionRunner, sender: Sender, name: str, arg: str, message: InboundMessage
    ) -> None:
        owner = self.auth.is_owner(sender)
        runner.anchor = message.message_id
        if name == "help":
            text = self.t("help")
            if runner.commands:
                text += "\n" + self.t(
                    "help_agent_commands", commands=" ".join("/" + c for c in runner.commands)
                )
            await runner.notify(text)
        elif name == "status":
            await runner.status()
        elif name == "btw":
            await runner.btw(arg, message.message_id)
        elif name == "stop":
            await runner.stop()
        elif not owner:
            self.audit.write("refused", platform=sender.platform, user=sender.user_id, command=name)
        elif name == "new":
            await runner.new_session()
        elif name == "resume":
            await self._resume_list(runner)
        elif name == "mode":
            await runner.set_mode(arg)
        elif name == "model":
            await runner.option("model", "label_model", arg)
        elif name == "thinking":
            await runner.option("thought_level", "label_thinking", arg)

    async def _resume_list(self, runner: SessionRunner) -> None:
        try:
            sessions = await runner.list_sessions()
        except Exception as exc:
            await runner.notify(self.t("agent_failed", error=str(exc)), color="red")
            return
        if not sessions:
            await runner.notify(self.t("resume_none"))
            return
        now = time.time()
        lines, buttons = [], []
        for index, info in enumerate(sessions[:RESUME_LIST_LIMIT], start=1):
            title = preview(getattr(info, "title", None) or info.session_id, 60)
            recent = (
                self.t("resume_recent") if is_recent(getattr(info, "updated_at", None), now) else ""
            )
            lines.append(f"{index}. {title} {recent}".rstrip())
            buttons.append(
                Button(
                    label=f"{self.t('resume_button')} {index}",
                    value={
                        "a": "resume",
                        "s": info.session_id,
                        "c": runner.key.chat_id,
                        "k": runner.key.thread_key,
                        "x": title,
                    },
                )
            )
        card = Card(
            title=f"{self.t('title')} · {self.t('resume_title')}",
            color="blue",
            blocks=("\n".join(lines),),
            buttons=tuple(buttons),
        )
        await runner.send(card)

    async def _blocks(
        self, runner: SessionRunner, adapter: PlatformAdapter, message: InboundMessage
    ) -> list[Any]:
        blocks: list[Any] = []
        if message.text.strip():
            blocks.append(text_block(message.text))
        limit = self.config.max_image_bytes
        for image in message.images:
            try:
                data, mime = await adapter.download_image(image)
            except Exception:
                logger.warning("downloading an image failed", exc_info=True)
                await runner.notify(self.t("image_failed"), color="orange")
                continue
            if len(data) > limit:
                await runner.notify(
                    self.t("image_too_large", limit=limit // (1024 * 1024)), color="orange"
                )
                continue
            blocks.append(image_block(base64.b64encode(data).decode("ascii"), mime))
        return blocks

    # -- taps ----------------------------------------------------------------------------------

    async def on_action(self, action: InboundAction) -> ActionReply:
        try:
            return await self._on_action(action)
        except Exception:
            logger.exception("handling a tap failed")
            return ActionReply(toast=self.t("approval_gone"), level="error")

    async def _on_action(self, action: InboundAction) -> ActionReply:
        if action.event_id and not self.store.first_time(
            f"{action.platform}:{action.event_id}", time.time()
        ):
            return ActionReply()
        sender = Sender(action.platform, action.tenant, action.actor_id)
        if not self.auth.tenant_ok(action.platform, action.tenant):
            return ActionReply(toast=self.t("approval_forbidden"), level="error")
        kind = action.value.get("a")
        if kind == "approve":
            return self._tap_approval(sender, action)
        if kind == "resume":
            return await self._tap_resume(sender, action)
        return ActionReply()

    def _tap_approval(self, sender: Sender, action: InboundAction) -> ActionReply:
        value = action.value
        result, pending = self.broker.tap(
            request_id=value.get("r", ""),
            nonce=value.get("n", ""),
            option_id=value.get("o", ""),
            actor=sender.user_id,
            may_approve=lambda p: self.auth.may_approve(sender, p.chat_id),
        )
        if result is TapResult.OK and pending is not None:
            runner = self._runner_of(pending)
            card = runner.closed_approval_card(pending) if runner else None
            return ActionReply(toast=self.t("approval_ok"), level="success", card=card)
        if result is TapResult.FORBIDDEN:
            self.audit.write(
                "approval_forbidden",
                platform=sender.platform,
                user=sender.user_id,
                request=value.get("r", ""),
            )
            return ActionReply(toast=self.t("approval_forbidden"), level="error")
        return ActionReply(toast=self.t("approval_gone"), level="warning")

    async def _tap_resume(self, sender: Sender, action: InboundAction) -> ActionReply:
        if not self.auth.is_owner(sender):
            return ActionReply(toast=self.t("approval_forbidden"), level="error")
        value = action.value
        key = BindingKey(sender.platform, value.get("c", ""), value.get("k", ""))
        runner = self.runners.get(key)
        if runner is None:
            return ActionReply(toast=self.t("approval_gone"), level="warning")
        self._spawn(runner.resume(value.get("s", ""), value.get("x", "")))
        return ActionReply(toast=self.t("approval_ok"), level="success")

    def _runner_of(self, pending: Pending) -> SessionRunner | None:
        key = pending.session_key
        return self.runners.get(key) if isinstance(key, BindingKey) else None

    def _approval_closed(self, pending: Pending) -> None:
        runner = self._runner_of(pending)
        if runner is None:
            return
        card = runner.closed_approval_card(pending)
        for platform_id, message_id in pending.card_targets:
            adapter = self.adapters.get(platform_id)
            if adapter is not None:
                self._spawn(_update_quietly(adapter, message_id, card))


def _with_text(message: InboundMessage, text: str) -> InboundMessage:
    return replace(message, text=text)


async def _update_quietly(adapter: PlatformAdapter, message_id: str, card: Card) -> None:
    try:
        await adapter.update_card(message_id, card)
    except Exception:
        logger.info("updating a closed approval card failed", exc_info=True)
