"""Helpers shared by the gateway tests: a gateway with a fake platform and a real agent."""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from pathlib import Path

from pi_chat_gateway.config import GatewayConfig, PlatformConfig, ProjectConfig
from pi_chat_gateway.gateway import Gateway
from pi_chat_gateway.platforms.fake import FakePlatform
from pi_chat_gateway.store import Owner, Store
from pi_chat_gateway.texts import Texts

OWNER = "ou_owner"
TENANT = "tenant-1"
SECRET = "SECRET-VALUE-0123456789"
T = Texts("zh")


def make_config(home: Path, project: Path, **overrides: object) -> GatewayConfig:
    config = GatewayConfig(
        home=home,
        projects=(ProjectConfig("demo", project),),
        platforms=(PlatformConfig(id="fake", type="feishu"),),
        env_mode="inherit",
        update_interval=0.2,
        approval_timeout=60.0,
    )
    return replace(config, **overrides)


@contextlib.asynccontextmanager
async def running_gateway(
    config: GatewayConfig,
    *,
    command: tuple[str, ...],
    agent_env: Mapping[str, str] | None = None,
    paired: bool = True,
    base_env: Mapping[str, str] | None = None,
) -> AsyncIterator[tuple[Gateway, FakePlatform]]:
    platform = FakePlatform("fake", TENANT)
    if agent_env:
        config = replace(config, agent_env={**config.agent_env, **agent_env})
    store = Store(config.home)
    if paired:
        store.set_owner(Owner("fake", OWNER, TENANT, "oc_dm", 0.0))
    gateway = Gateway(
        config,
        {"fake": {"app_id": "cli_test_app", "app_secret": SECRET}},
        [platform],
        store=store,
        base_env=dict(base_env) if base_env is not None else dict(os.environ),
        agent_command=command,
    )
    await gateway.start()
    try:
        yield gateway, platform
    finally:
        await gateway.close()


def finished_turn(platform: FakePlatform):
    """The first turn card whose title says the turn is over (done, stopped or failed)."""
    ends = (T("status_done"), T("status_cancelled"), T("status_error"))
    for sent in platform.sent:
        if any(sent.card.title.endswith(end) for end in ends):
            return sent
    return None


def notices_text(platform: FakePlatform) -> str:
    return "\n".join(s.text for s in platform.sent)
