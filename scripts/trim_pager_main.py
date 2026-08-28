#!/usr/bin/env python3
"""Trim grok-specific handlers from pi-pager-bin main.rs (de-grok Phase 4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = REPO / "tui/crates/codegen/pi-pager-bin/src/main.rs"

DROP_STARTS = {
    "fn apply_headless_args_to_config",
    "fn apply_agent_endpoint_args",
    "fn resolve_agent_profile_path",
    "fn print_serve_startup_info",
    "async fn run_setup_command",
    "async fn run_leader_mgmt",
    "async fn kill_leaders",
    "fn leader_descriptor_json",
    "fn leader_pid",
    "fn print_leader_descriptor",
    "fn leader_info_json",
    "fn resolve_target",
    "async fn connect_to_leader",
    "fn ensure_control_caps",
    "fn workspace_command_env_override",
    "enum WorkspaceGate",
    "fn workspace_command_gate",
    "fn env_flag_enabled",
    "fn fetch_remote_settings",
    "async fn run_workspace_mgmt",
    "fn ensure_workspace_caps",
    "async fn connect_workspace_control",
    "async fn workspace_control",
    "async fn workspace_start",
    "fn render_workspace_payload",
    "struct CachedSession",
    "struct StdioReplayState",
    "impl StdioReplayState",
    "fn cached_session_from_params",
    "const CACHED_METHODS",
    "fn cache_outgoing_acp_state",
    "fn cache_incoming_session_id",
    "async fn replay_acp_state_after_reconnect",
    "fn restored_session_ids_from_replay",
    "fn replay_load_json",
    "async fn replay_request_until_response",
    "fn parse_replay_response",
    "enum ReplayOutcome",
    "const REPLAY_LOAD_REQUEST_ID",
    "const REPLAY_RECV_TIMEOUT",
    "const REPLAY_RESPONSE_DEADLINE",
    "async fn forward_stdio_line_to_leader",
    "const PLUGIN_DIR_LEADER_WARNING",
    "async fn run_agent_command",
    "fn flag_dashboard_at_startup_if_requested",
    "fn is_managed_install",
    "fn get_channel_switch",
    "fn resolve_update_trigger",
    "async fn run_update_command",
    "async fn signal_leaders_to_relaunch",
    "fn stdio_auto_update_enabled",
    "const WORKSPACE_COMMAND_ENV",
}

DROP_TEST_NAMES = {
    "agent_plugin_dir_repeatable_and_canonicalized",
    "leader_socket_flag_is_global_for_subcommands",
    "is_managed_install_matches_only_the_bin_grok_target",
    "stdio_auto_update_enabled_cases",
    "fallback_replay_json_escapes_special_chars",
    "cache_incoming_session_id_from_response",
    "replay_restores_all_cached_sessions",
    "replay_skips_rejected_session_and_restores_the_rest",
    "replay_waits_for_load_response_through_notifications",
    "replay_returns_none_when_load_is_rejected",
    "replay_fallback_load_uses_reserved_string_id",
    "replay_preserves_prior_session_when_new_is_unconfirmed",
    "make_state",
}


def item_start(line: str) -> str | None:
    s = line.lstrip()
    if s.startswith("///"):
        return None
    for prefix in ("async fn ", "fn ", "struct ", "impl ", "enum ", "const ", "type "):
        if s.startswith(prefix):
            return s.split("(")[0].split("{")[0].split("<")[0].strip()
    return None


def skip_block(lines: list[str], i: int) -> int:
    s = lines[i].lstrip()
    if s.startswith("const ") and "{" not in s:
        return i + 1
    depth = 0
    started = False
    j = i
    while j < len(lines):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return j + 1
        if not started and lines[j].strip().endswith(";"):
            return j + 1
        j += 1
    return j


def trim(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    in_tests = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("#[cfg(test)]"):
            in_tests = True
            out.append(line)
            i += 1
            continue
        if in_tests and line.strip().startswith("fn "):
            name = line.strip().split("(")[0].removeprefix("fn ").strip()
            if name in DROP_TEST_NAMES or name.startswith("replay_"):
                while out and (
                    out[-1].strip().startswith("#[")
                    or out[-1].strip().startswith("///")
                    or out[-1].strip() == ""
                ):
                    out.pop()
                i = skip_block(lines, i)
                continue
        if in_tests and line.strip().startswith("async fn "):
            name = line.strip().split("(")[0].removeprefix("async fn ").strip()
            if name.startswith("replay_"):
                while out and (
                    out[-1].strip().startswith("#[")
                    or out[-1].strip().startswith("///")
                    or out[-1].strip() == ""
                ):
                    out.pop()
                i = skip_block(lines, i)
                continue
        if not in_tests:
            j = i
            while j < len(lines) and lines[j].startswith("///"):
                j += 1
            name = item_start(lines[j]) if j < len(lines) else None
            if name and any(name.startswith(p) or name == p for p in DROP_STARTS):
                i = skip_block(lines, j)
                continue
        out.append(line)
        i += 1
    return out


def patch_match_block(text: str) -> str:
    start = text.find("    if let Some(command) = args.command.take() {")
    if start == -1:
        raise RuntimeError("command match block not found")
    end = text.find(
        "    let headless_prompt = pi_pager::headless::HeadlessPrompt::from_args(",
        start,
    )
    if end == -1:
        raise RuntimeError("headless_prompt anchor not found")
    replacement = """    if let Some(command) = args.command.take() {
        match command {
            Command::Version { json } => {
                if json {
                    let payload = serde_json::json!({
                        "currentVersion": env!("VERSION_WITH_COMMIT"),
                        "channel": pi_update::channel_name().unwrap_or("unknown"),
                    });
                    println!("{}", serde_json::to_string(&payload)?);
                } else {
                    write_version(
                        &mut std::io::stdout().lock(),
                        pi_update::channel_label(),
                    )?;
                }
                return Ok(());
            }
            Command::Doctor(_) => {
                unreachable!("doctor was consumed before runtime startup")
            }
            Command::DiskUsage(disk_usage_args) => {
                init_tracing_simple("cli");
                let _otel_guard = pi_telemetry::otel_layer::otel_guard();
                return pi_pager::disk_usage_cmd::run(disk_usage_args);
            }
            Command::Export(export_args) => {
                init_tracing_simple("cli");
                return pi_pager::export_cmd::run(export_args);
            }
            Command::Wrap(ref wrap_args) => {
                return pi_pager::wrap_cmd::run(wrap_args);
            }
            Command::Completions { shell } => {
                pi_pager::completions_cmd::run(shell);
                return Ok(());
            }
        }
    }
"""
    return text[:start] + replacement + text[end:]


def main() -> None:
    src = subprocess.check_output(
        ["git", "-C", str(REPO), "show", "HEAD:tui/crates/codegen/pi-grok-pager-bin/src/main.rs"],
        text=True,
    ).replace("pi_grok_", "pi_")
    lines = trim(src.splitlines(keepends=True))
    text = "".join(lines)
    text = text.replace(
        "use pi_pager::app::{\n"
        "    AgentCmd, Command, HeadlessArgs, LeaderMgmtArgs, LeaderMgmtCommand, LeaderMode,\n"
        "    LeaderTargetArgs, PagerArgs, join_early_prefetch, resolve_leader_mode,\n"
        "    resolve_use_leader,\n"
        "    warn_leader_disabled_by_sandbox,\n"
        "};\n"
        "use pi_pager::app::{WorkspaceMgmtArgs, WorkspaceMgmtCommand, WorkspaceStartArgs};",
        "use pi_pager::app::{Command, PagerArgs};",
    )
    old_identity = (
        "fn process_identity(command: Option<&Command>, is_interactive: bool) "
        "-> Option<ProcessIdentity> {\n"
        "    use pi_telemetry::process_info::LeaderMode::Standalone;\n"
        "    let (entrypoint, interactivity) = match command {\n"
        "        Some(Command::Agent(_)) => return None,\n"
        "        Some(Command::Dashboard) => return None,\n"
        "        Some(Command::Login { .. }) => (Entrypoint::Cli, Interactivity::Interactive),\n"
        "        Some(\n"
        "            Command::Inspect { .. }\n"
        "            | Command::Doctor(_)\n"
        "            | Command::Leader(_)\n"
        "            | Command::Logout\n"
        "            | Command::Mcp(_)\n"
        "            | Command::Plugin(_)\n"
        "            | Command::Memory(_)\n"
        "            | Command::Models\n"
        "            | Command::Sessions(_)\n"
        "            | Command::Setup { .. }\n"
        "            | Command::Share(_)\n"
        "            | Command::Wrap(_)\n"
        "            | Command::Export(_)\n"
        "            | Command::Trace(_)\n"
        "            | Command::Update { .. }\n"
        "            | Command::Version { .. }\n"
        "            | Command::Completions { .. }\n"
        "            | Command::Worktree(_)\n"
        "            | Command::DiskUsage(_)\n"
        "            | Command::Workspace(_),\n"
        "        ) => (Entrypoint::Cli, Interactivity::Unattended),\n"
        "        None if is_interactive => return None,\n"
        "        None => (Entrypoint::Headless, Interactivity::Unattended),\n"
        "    };\n"
        "    Some(ProcessIdentity {\n"
        "        entrypoint,\n"
        "        leader: Standalone,\n"
        "        interactivity,\n"
        "    })\n"
        "}"
    )
    new_identity = (
        "fn process_identity(command: Option<&Command>, is_interactive: bool) "
        "-> Option<ProcessIdentity> {\n"
        "    use pi_telemetry::process_info::LeaderMode::Standalone;\n"
        "    let (entrypoint, interactivity) = match command {\n"
        "        Some(Command::Doctor(_) | Command::Wrap(_) | Command::Export(_) "
        "| Command::DiskUsage(_))\n"
        "        | Some(Command::Version { .. })\n"
        "        | Some(Command::Completions { .. }) "
        "=> (Entrypoint::Cli, Interactivity::Unattended),\n"
        "        None if is_interactive => return None,\n"
        "        None => (Entrypoint::Headless, Interactivity::Unattended),\n"
        "    };\n"
        "    Some(ProcessIdentity {\n"
        "        entrypoint,\n"
        "        leader: Standalone,\n"
        "        interactivity,\n"
        "    })\n"
        "}"
    )
    text = text.replace(old_identity, new_identity)
    text = text.replace("    flag_dashboard_at_startup_if_requested(&mut args)?;\n", "")
    text = text.replace('client_name: "grok-pager"', 'client_name: "zypi"')
    text = patch_match_block(text)
    MAIN.write_text(text, encoding="utf-8")
    print(f"wrote {MAIN} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
