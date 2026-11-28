"""``zycode-chat``: set up, pair and run the chat gateway.

zycode-chat setup     write chat.toml (first time) and the credentials, then test them
zycode-chat pair      print a one-time code; send "/pair <code>" to the bot in a direct chat
zycode-chat unpair    forget the owner of a platform
zycode-chat run       run the gateway in the foreground
zycode-chat status    the configuration, the owners and the chats that have a session
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import signal
import sys
import tomllib
from pathlib import Path

from pi_agent_core.home import pi_home

from . import __version__
from .config import (
    ConfigError,
    GatewayConfig,
    config_path,
    credentials_path,
    load_config,
    load_credentials,
    write_private_file,
)
from .store import Store

logger = logging.getLogger("pi_chat_gateway")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _template(project_name: str, project_path: Path, platform_id: str, domain: str) -> str:
    return f"""# zycode-chat, the chat gateway. Only the gateway reads this file, never the agent.
# Design: docs/specs/2026-11-28-chat-gateway-design.md in the zycode repository.

[gateway]
start_mode = "ask"               # ask | auto (auto needs allow_auto = true); never always-approve
allow_auto = false
approval_timeout_minutes = 30    # an approval nobody answers is rejected
approvals_in_dm = true           # approvals of group sessions go to the owner's direct chat
idle_agent_minutes = 20
max_active_turns = 3
show_thinking = false
env = "login-shell"              # login-shell | inherit: the environment of the agents
language = "zh"                  # zh | en: the gateway's own words
# agent_command = ["zycode-agent"]

[[projects]]
name = {_toml_string(project_name)}
path = {_toml_string(str(project_path))}

[[platforms]]
id = {_toml_string(platform_id)}
type = "feishu"
domain = {_toml_string(domain)}      # feishu (open.feishu.cn) | lark (open.larksuite.com)
# tenant = ""                   # empty: the tenant of the owner, learned at pairing

# [[channels]]                  # a group chat bound to a project
# platform = {_toml_string(platform_id)}
# chat_id = "oc_..."
# project = {_toml_string(project_name)}
# users = []                    # besides the owner, who may talk to the agent there
# approvers = []                # besides the owner, who may approve its calls
"""


FEISHU_STEPS = """\
On the Feishu / Lark developer console (an app of your tenant):
  1. Add the Bot capability.
  2. Permissions: receive the messages users send to the bot (direct chats and @mentions in
     groups), send messages as the app, read message resources (images), and add reactions.
     Use the names the console shows; they differ between Feishu and Lark.
  3. Events and callbacks: choose "receive events through a long connection"; subscribe to
     im.message.receive_v1, and add the callback card.action.trigger (long connection too).
     Run `zycode-chat run` while you save, so the console can see the connection.
  4. Publish a version of the app and have it approved in your tenant.
Then: `zycode-chat pair`, and send the code to the bot in a direct chat."""


def cmd_setup(args: argparse.Namespace) -> int:
    home = pi_home()
    path = config_path(home)
    if path.exists():
        print(f"{path} exists; it is not changed.")
        try:
            config = load_config(home)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        platform_ids = [p.id for p in config.platforms]
        platform_id = args.platform or (platform_ids[0] if len(platform_ids) == 1 else "")
        if platform_id not in platform_ids:
            print(
                f"error: pick a platform with --platform ({', '.join(platform_ids)})",
                file=sys.stderr,
            )
            return 1
    else:
        project_name = _ask("Project name", Path.cwd().name)
        project_path = Path(_ask("Project folder (absolute)", str(Path.cwd()))).expanduser()
        if not project_path.is_absolute() or not project_path.is_dir():
            print(f"error: {project_path} is not an absolute path to a folder", file=sys.stderr)
            return 1
        platform_id = args.platform or "feishu"
        domain = _ask("Domain: feishu (China) or lark (international)", "feishu")
        if domain not in ("feishu", "lark"):
            print("error: the domain is feishu or lark", file=sys.stderr)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _template(project_name, project_path.resolve(), platform_id, domain), "utf-8"
        )
        print(f"wrote {path}")
    app_id = _ask("App ID (cli_...)")
    app_secret = getpass.getpass("App Secret (not shown): ").strip()
    if not app_id or not app_secret:
        print("error: both the App ID and the App Secret are needed", file=sys.stderr)
        return 1
    _write_credentials(home, platform_id, {"app_id": app_id, "app_secret": app_secret})
    print(f"wrote {credentials_path(home)} (only you can read it)")
    try:
        config = load_config(home)
        credentials = load_credentials(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    bot = asyncio.run(_check_feishu(config, credentials, platform_id))
    if bot:
        print(f"the credentials work: the bot's open_id is {bot}")
    else:
        print(
            "warning: could not read the bot's identity; check the App ID, the App Secret and "
            "that the Bot capability is on"
        )
    print()
    print(FEISHU_STEPS)
    return 0


def _write_credentials(home: Path, platform_id: str, values: dict[str, str]) -> None:
    path = credentials_path(home)
    existing: dict = {}
    if path.is_file():
        existing = tomllib.loads(path.read_text(encoding="utf-8"))
    existing[platform_id] = values
    lines = ["# zycode-chat credentials. Keep this file to yourself (mode 600).", ""]
    for table, entries in existing.items():
        if not isinstance(entries, dict):
            continue
        lines.append(f"[{table}]")
        for key, value in entries.items():
            if isinstance(value, str):
                lines.append(f"{key} = {_toml_string(value)}")
        lines.append("")
    write_private_file(path, "\n".join(lines))


async def _check_feishu(config: GatewayConfig, credentials: dict, platform_id: str) -> str:
    from .platforms.feishu import FeishuAdapter

    platform = config.platform(platform_id)
    adapter = FeishuAdapter(platform, credentials[platform_id])
    return await adapter.fetch_bot_open_id()


def _one_platform(config: GatewayConfig, wanted: str | None) -> str | None:
    ids = [p.id for p in config.platforms]
    if wanted:
        return wanted if wanted in ids else None
    return ids[0] if len(ids) == 1 else None


def cmd_pair(args: argparse.Namespace) -> int:
    from .identity import Pairing

    config = load_config()
    platform_id = _one_platform(config, args.platform)
    if platform_id is None:
        print("error: pick a platform with --platform", file=sys.stderr)
        return 1
    store = Store(config.home)
    owner = store.owner(platform_id)
    if owner is not None:
        print(
            f"{platform_id} already has an owner ({owner.user_id}). Run `zycode-chat unpair` first."
        )
        return 1
    code = Pairing(store).create_code(platform_id)
    print(f"Send this to the bot in a direct chat within 10 minutes:\n\n    /pair {code}\n")
    return 0


def cmd_unpair(args: argparse.Namespace) -> int:
    config = load_config()
    platform_id = _one_platform(config, args.platform)
    if platform_id is None:
        print("error: pick a platform with --platform", file=sys.stderr)
        return 1
    removed = Store(config.home).clear_owner(platform_id)
    print(f"{platform_id}: owner removed" if removed else f"{platform_id}: no owner")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    store = Store(config.home)
    print(f"home: {config.home}")
    print(f"start mode: {config.start_mode} (auto allowed: {config.allow_auto})")
    for project in config.projects:
        print(f"project {project.name}: {project.path}")
    for platform in config.platforms:
        owner = store.owner(platform.id)
        who = f"owner {owner.user_id} (tenant {owner.tenant or '?'})" if owner else "not paired"
        print(f"platform {platform.id} ({platform.type}, {platform.domain}): {who}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .gateway import Gateway

    config = load_config()
    credentials = load_credentials(config)
    _setup_logging(config.home, args.verbose)
    adapters = []
    for platform in config.platforms:
        if platform.type == "feishu":
            try:
                from .platforms.feishu import FeishuAdapter
            except ImportError:
                print(
                    "error: the Feishu adapter needs lark-oapi: "
                    "pip install 'pi-chat-gateway-py[feishu]'",
                    file=sys.stderr,
                )
                return 1
            adapters.append(FeishuAdapter(platform, credentials[platform.id]))
    gateway = Gateway(config, credentials, adapters)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
                loop.add_signal_handler(sig, gateway.stop)
        await gateway.run_forever()

    logger.info("zycode-chat %s: starting", __version__)
    asyncio.run(main())
    return 0


def _setup_logging(home: Path, verbose: bool) -> None:
    log_dir = home / "chat" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(log_dir / "gateway.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zycode-chat", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"zycode-chat {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
        ("setup", cmd_setup, "write chat.toml and the credentials, and test them"),
        ("pair", cmd_pair, "print a one-time pairing code"),
        ("unpair", cmd_unpair, "forget the owner of a platform"),
        ("run", cmd_run, "run the gateway in the foreground"),
        ("status", cmd_status, "show the configuration and the owners"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        if name in ("setup", "pair", "unpair"):
            sub.add_argument("--platform", help="the id of a [[platforms]] entry")
        if name == "run":
            sub.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        code = args.handler(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
