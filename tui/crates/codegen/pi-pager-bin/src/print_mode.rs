//! Guards for `zypi -p`.
//!
//! Print mode hands the whole run to the agent process (`python -m pi_agent_cli -p ...`) before
//! any of the TUI start-up code runs. Two things follow, and neither may happen silently:
//!
//! - the OS sandbox is applied later, in `async_main`, so a sandbox that was asked for would be
//!   skipped: print mode refuses to start instead;
//! - only the flags the agent understands are forwarded, so every other flag the user gave is
//!   reported as ignored.
//!
//! The full flag contract is plan item 1.P6 (`docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`).

use std::ffi::{OsStr, OsString};

use pi_pager::app::PagerArgs;
use pi_pager::app::cli::OutputFormat;
use pi_shell::config::RequestedSandbox;

/// Exit status when print mode refuses to run (the same class as a command-line usage error).
pub(crate) const REFUSED_EXIT_CODE: i32 = 2;

/// The variable that tells the agent which process started it (read by `pi_agent_cli`).
///
/// The TUI's agent finds out that zypi is gone from EOF on its stdin. Here stdin is the user's own
/// terminal, so the agent gets zypi's pid instead and stops once that is no longer its parent;
/// without it, killing only zypi (`kill -9`, `kill <pid>`, an IDE's stop button) leaves the agent
/// and the tools it is running going on the terminal.
pub(crate) const PARENT_PID_ENV: &str = "PI_AGENT_PARENT_PID";

/// The agent process for `-p`: `program` with `args`, told which process started it.
pub(crate) fn agent_command(program: &OsStr, args: &[OsString]) -> std::process::Command {
    let mut command = std::process::Command::new(program);
    command
        .args(args)
        .env(PARENT_PID_ENV, std::process::id().to_string());
    command
}

/// The message printed when print mode refuses to start because `requested` cannot be applied.
pub(crate) fn sandbox_refusal(requested: &RequestedSandbox) -> String {
    let way_out = if requested.overridable {
        "Pass --sandbox off to run without a sandbox, or use the interactive UI."
    } else {
        "A deployment requirement fixes this profile; use the interactive UI."
    };
    // clap folds `GROK_SANDBOX` into `--sandbox`, so the resolver reports both as `cli`.
    let set_by = match requested.source.as_str() {
        "cli" => "--sandbox or GROK_SANDBOX",
        "env" => "GROK_SANDBOX",
        "config" => "[sandbox].profile in config.toml",
        "requirement" => "a deployment requirement",
        other => other,
    };
    format!(
        "error: print mode (-p) cannot apply the '{}' sandbox profile (set by {set_by}), and the \
         agent is not started without it. {way_out}",
        requested.profile
    )
}

/// The flags the user gave that print mode does not forward to the agent, in `--help` order.
pub(crate) fn ignored_flags(args: &PagerArgs) -> Vec<&'static str> {
    let given = [
        ("--model", args.model.is_some()),
        ("--reasoning-effort", args.reasoning_effort.is_some()),
        ("--output-format", args.output_format != OutputFormat::Plain),
        ("--include-partial-messages", args.include_partial_messages),
        ("--json-schema", args.json_schema.is_some()),
        ("--verbatim", args.verbatim),
        ("--always-approve", args.yolo),
        ("--allow", !args.allow_rules.is_empty()),
        ("--deny", !args.deny_rules.is_empty()),
        ("--permission-mode", args.permission_mode_flag.is_some()),
        ("--max-turns", args.max_turns.is_some()),
        ("--resume", args.resume_session.is_some()),
        ("--load", args.load_session.is_some()),
        ("--continue", args.continue_last_session),
        ("--session-id", args.session_id.is_some()),
        ("--worktree", args.worktree.is_some()),
        ("--no-plan", args.no_plan),
        ("--no-subagents", args.no_subagents),
        ("--agent", args.agent.is_some()),
        ("--agents", args.agents_json.is_some()),
        ("--tools", args.cli_tools.is_some()),
        ("--disallowed-tools", args.cli_disallowed_tools.is_some()),
        ("--disable-web-search", args.disable_web_search),
    ];
    given
        .into_iter()
        .filter_map(|(flag, is_given)| is_given.then_some(flag))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser as _;

    fn parse(extra: &[&str]) -> PagerArgs {
        let mut argv = vec!["zypi", "-p", "hello"];
        argv.extend_from_slice(extra);
        PagerArgs::try_parse_from(argv).unwrap()
    }

    #[test]
    fn flags_the_agent_receives_are_not_reported() {
        let args = parse(&[
            "--cwd",
            "/tmp",
            "--system-prompt",
            "s",
            "--rules",
            "r",
            "--no-context-files",
        ]);
        assert!(ignored_flags(&args).is_empty());
    }

    #[test]
    fn flags_the_agent_does_not_receive_are_reported() {
        let args = parse(&[
            "--model",
            "m",
            "--output-format",
            "json",
            "--yolo",
            "--allow",
            "Bash(ls)",
            "--max-turns",
            "3",
            "--resume",
            "abc",
            "--no-plan",
        ]);
        assert_eq!(
            ignored_flags(&args),
            [
                "--model",
                "--output-format",
                "--always-approve",
                "--allow",
                "--max-turns",
                "--resume",
                "--no-plan"
            ]
        );
    }

    #[test]
    fn the_agent_is_told_which_process_started_it() {
        let command = agent_command(OsStr::new("agent"), &[OsString::from("-p")]);
        assert_eq!(command.get_program(), OsStr::new("agent"));
        assert_eq!(command.get_args().collect::<Vec<_>>(), [OsStr::new("-p")]);
        let pid = std::process::id().to_string();
        let given: Vec<_> = command
            .get_envs()
            .filter(|(name, _)| *name == OsStr::new(PARENT_PID_ENV))
            .collect();
        assert_eq!(
            given,
            [(OsStr::new(PARENT_PID_ENV), Some(OsStr::new(&pid)))]
        );
    }

    #[test]
    fn the_default_output_format_is_not_a_flag_the_user_gave() {
        let args = parse(&["--output-format", "plain"]);
        assert!(ignored_flags(&args).is_empty());
    }

    fn requested(overridable: bool) -> RequestedSandbox {
        RequestedSandbox {
            profile: "workspace".into(),
            source: if overridable { "cli" } else { "requirement" }.into(),
            overridable,
        }
    }

    #[test]
    fn the_refusal_names_the_profile_where_it_came_from_and_the_way_out() {
        let message = sandbox_refusal(&requested(true));
        assert!(message.contains("'workspace'"), "{message}");
        assert!(
            message.contains("set by --sandbox or GROK_SANDBOX"),
            "{message}"
        );
        assert!(message.contains("--sandbox off"), "{message}");
    }

    #[test]
    fn a_required_profile_is_not_offered_an_override() {
        let message = sandbox_refusal(&requested(false));
        assert!(
            message.contains("set by a deployment requirement"),
            "{message}"
        );
        assert!(!message.contains("--sandbox off"), "{message}");
    }

    #[test]
    fn a_configured_profile_points_at_the_config_key() {
        let mut from_config = requested(true);
        from_config.source = "config".into();
        let message = sandbox_refusal(&from_config);
        assert!(
            message.contains("set by [sandbox].profile in config.toml"),
            "{message}"
        );
    }
}
