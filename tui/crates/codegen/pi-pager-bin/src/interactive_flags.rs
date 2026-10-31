//! Flags that an interactive `zypi` session refuses.
//!
//! `zypi` came with a Rust agent runtime that read many command-line flags. The agent behind it
//! now is the Python one, and an interactive session passes it no flag at all: `acp/spawn.rs`
//! starts it with no flag-derived argument, and what goes over the ACP wire is ignored on the
//! Python side. A flag that is accepted and does nothing is worse than a flag that is refused,
//! because the user believes it works. `--deny 'Bash(rm *)'` is the example that matters: the
//! agent still asks about `rm`, and it never refuses the call. `--disable-web-search` leaves the
//! web tools on. So the start fails with exit status 2, names every such flag with the reason,
//! and starts nothing. What each flag does against the real `zypi` is measured by
//! `scripts/tui_pty/flags_probe.py`; the plan, decision 8, has the table.
//!
//! The rule is for the interactive start only. `-p` has its own contract (`print_mode`): it passes
//! a few flags to the agent, warns about the others and goes on. A subcommand takes its own flags.
//! Hidden compatibility flags (`--no-ask-user`, `--hunk-tracker-mode`, ...) stay accepted. They
//! are not in `--help`, and a script of the upstream project can still pass them.
//!
//! Every flag in [`REFUSALS`] is hidden from `--help` (`app/cli.rs`), except the two that `-p`
//! takes (`--rules`, `--system-prompt-override`). A test pins that.

use pi_pager::app::PagerArgs;
use pi_pager::app::cli::OutputFormat;

/// Exit status when the start is refused (the same class as a command-line usage error).
pub(crate) const REFUSED_EXIT_CODE: i32 = 2;

/// A flag that an interactive session refuses.
pub(crate) struct Refusal {
    /// The name in `--help` order and in the message.
    pub(crate) flag: &'static str,
    /// The other names of the same flag. They are named in the message, because the user may have
    /// typed one of them.
    pub(crate) also: &'static [&'static str],
    /// Why the flag does nothing, in plain words, and what to do instead where there is something.
    pub(crate) reason: &'static str,
    /// Whether the user gave the flag.
    given: fn(&PagerArgs) -> bool,
}

impl Refusal {
    /// The name as the message shows it: `--allow (--allowedTools)`.
    fn display(&self) -> String {
        if self.also.is_empty() {
            self.flag.to_string()
        } else {
            format!("{} ({})", self.flag, self.also.join(", "))
        }
    }
}

const NOT_READ: &str = "The Python agent does not read it.";
const HEADLESS: &str =
    "It is an option for headless output, which the Python agent does not implement.";

/// The flags, in the order of `--help`.
pub(crate) static REFUSALS: &[Refusal] = &[
    Refusal {
        flag: "--allow",
        also: &["--allowedTools"],
        reason: "The Python agent gets no permission rules. It asks about each tool call. \
                 Use --always-approve to approve all calls.",
        given: |args| !args.allow_rules.is_empty(),
    },
    Refusal {
        flag: "--deny",
        also: &["--disallowedTools"],
        reason: "The Python agent gets no permission rules, so it does not refuse a call that \
                 matches. It asks about each tool call.",
        given: |args| !args.deny_rules.is_empty(),
    },
    Refusal {
        flag: "--verbatim",
        also: &[],
        reason: HEADLESS,
        given: |args| args.verbatim,
    },
    Refusal {
        flag: "--output-format",
        also: &[],
        reason: HEADLESS,
        given: |args| args.output_format != OutputFormat::Plain,
    },
    Refusal {
        flag: "--include-partial-messages",
        also: &[],
        reason: HEADLESS,
        given: |args| args.include_partial_messages,
    },
    Refusal {
        flag: "--json-schema",
        also: &[],
        reason: HEADLESS,
        given: |args| args.json_schema.is_some(),
    },
    Refusal {
        flag: "--reasoning-effort",
        also: &["--effort"],
        reason: "The Python agent gets no reasoning level from the client. \
                 Set thinking_level in agent.toml.",
        given: |args| args.reasoning_effort.is_some(),
    },
    Refusal {
        flag: "--rules",
        also: &["--append-system-prompt"],
        reason: "Only -p passes it to the agent. Put the text in .pi/APPEND_SYSTEM.md, \
                 or use -p.",
        given: |args| args.rules.is_some(),
    },
    Refusal {
        flag: "--system-prompt-override",
        also: &["--system-prompt"],
        reason: "Only -p passes it to the agent. Put the text in .pi/SYSTEM.md, or use -p.",
        given: |args| args.system_prompt_override.is_some(),
    },
    Refusal {
        flag: "--worktree",
        also: &["-w"],
        reason: "The Python agent has no git worktree sessions.",
        given: |args| args.worktree.is_some(),
    },
    Refusal {
        flag: "--worktree-ref",
        also: &["--ref"],
        reason: "The Python agent has no git worktree sessions.",
        given: |args| args.worktree_ref.is_some(),
    },
    Refusal {
        flag: "--restore-code",
        also: &[],
        reason: "The Python agent keeps no snapshot of the repository, so it has no code to restore.",
        given: |args| args.restore_code,
    },
    Refusal {
        flag: "--no-plan",
        also: &[],
        reason: "The Python agent has no plan mode to switch off.",
        given: |args| args.no_plan,
    },
    Refusal {
        flag: "--no-subagents",
        also: &[],
        reason: NOT_READ,
        given: |args| args.no_subagents,
    },
    Refusal {
        flag: "--agent",
        also: &[],
        reason: "The Python agent has no agent profiles. It does not read it.",
        given: |args| args.agent.is_some(),
    },
    Refusal {
        flag: "--agents",
        also: &[],
        reason: NOT_READ,
        given: |args| args.agents_json.is_some(),
    },
    Refusal {
        flag: "--tools",
        also: &[],
        reason: "The Python agent does not read it. Its tools do not change.",
        given: |args| args.cli_tools.is_some(),
    },
    Refusal {
        flag: "--disallowed-tools",
        also: &[],
        reason: "The Python agent does not read it. Its tools do not change.",
        given: |args| args.cli_disallowed_tools.is_some(),
    },
    Refusal {
        flag: "--max-turns",
        also: &[],
        reason: NOT_READ,
        given: |args| args.max_turns.is_some(),
    },
    Refusal {
        flag: "--disable-web-search",
        also: &[],
        reason: "The Python agent does not read it. Its web tools (web_search, fetch_url) stay on.",
        given: |args| args.disable_web_search,
    },
    Refusal {
        flag: "--oauth",
        also: &[],
        reason: "zypi has no sign-in screen with the Python agent. \
                 Run python -m pi_agent_cli --setup to set the model and the key.",
        given: |args| args.oauth,
    },
];

/// The refused flags that the user gave, in `--help` order. Empty when the start may go on.
pub(crate) fn refused(args: &PagerArgs) -> Vec<&'static Refusal> {
    REFUSALS
        .iter()
        .filter(|refusal| (refusal.given)(args))
        .collect()
}

/// The message printed when the start is refused: one line for each flag, then the way out.
pub(crate) fn refusal(refused: &[&Refusal]) -> String {
    let mut message =
        String::from("error: zypi did not start. The Python agent does not support these flags:\n");
    for flag in refused {
        message.push_str(&format!("  {}: {}\n", flag.display(), flag.reason));
    }
    message.push_str(
        "Remove the flags and run zypi again. Run zypi --help to see the flags that work.",
    );
    message
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::{CommandFactory as _, Parser as _};

    fn parse(argv: &[&str]) -> PagerArgs {
        let mut full = vec!["zypi"];
        full.extend_from_slice(argv);
        PagerArgs::try_parse_from(full).unwrap()
    }

    fn names(argv: &[&str]) -> Vec<&'static str> {
        refused(&parse(argv)).iter().map(|r| r.flag).collect()
    }

    /// Every refused flag, with a value where it needs one, and the flags that clap makes it
    /// travel with (`--worktree-ref` needs `--worktree`, `--restore-code` needs `--resume`).
    const ONE_EACH: &[(&[&str], &str)] = &[
        (&["--allow", "Bash(ls)"], "--allow"),
        (&["--deny", "Bash(rm *)"], "--deny"),
        (&["--verbatim"], "--verbatim"),
        (&["--output-format", "json"], "--output-format"),
        (
            &["--include-partial-messages"],
            "--include-partial-messages",
        ),
        (&["--json-schema", "{}"], "--json-schema"),
        (&["--reasoning-effort", "high"], "--reasoning-effort"),
        (&["--rules", "r"], "--rules"),
        (
            &["--system-prompt-override", "s"],
            "--system-prompt-override",
        ),
        (&["--worktree", "w"], "--worktree"),
        (&["--worktree", "--worktree-ref", "main"], "--worktree-ref"),
        (&["--resume", "abc", "--restore-code"], "--restore-code"),
        (&["--no-plan"], "--no-plan"),
        (&["--no-subagents"], "--no-subagents"),
        (&["--agent", "a"], "--agent"),
        (&["--agents", "{}"], "--agents"),
        (&["--tools", "bash"], "--tools"),
        (&["--disallowed-tools", "bash"], "--disallowed-tools"),
        (&["--max-turns", "3"], "--max-turns"),
        (&["--disable-web-search"], "--disable-web-search"),
        (&["--oauth"], "--oauth"),
    ];

    #[test]
    fn a_start_without_these_flags_is_not_refused() {
        assert!(names(&[]).is_empty());
        assert!(
            names(&[
                "--cwd",
                "/tmp",
                "--debug",
                "--model",
                "m",
                "--always-approve",
                "--permission-mode",
                "bypassPermissions",
                "--resume",
                "abc",
                "--no-alt-screen",
                "--minimal",
                "fix the bug",
            ])
            .is_empty()
        );
        assert!(names(&["--continue"]).is_empty());
        assert!(names(&["--session-id", "7b7bd7c2-6a4f-4b1d-9a37-0d3f6f6a1c11"]).is_empty());
    }

    #[test]
    fn hidden_compatibility_flags_stay_accepted() {
        let given = names(&[
            "--trust",
            "--no-ask-user",
            "--experimental-memory",
            "--hunk-tracker-mode",
            "off",
            "--storage-mode",
            "local",
            "--client-identifier",
            "x",
            "--no-auto-update",
            "--terminal",
            "--fs-read",
            "--fs-write",
            "--todo-gate",
            "--compaction-mode",
            "summary",
        ]);
        assert!(given.is_empty(), "{given:?}");
    }

    #[test]
    fn each_refused_flag_is_found_alone() {
        for (argv, expected) in ONE_EACH {
            let given = names(argv);
            // `--worktree-ref` cannot be given without `--worktree`: the only extra name.
            let companion = (*expected == "--worktree-ref").then_some("--worktree");
            let others: Vec<_> = given
                .iter()
                .filter(|flag| *flag != expected && Some(**flag) != companion)
                .collect();
            assert!(given.contains(expected), "{argv:?} gave {given:?}");
            assert!(others.is_empty(), "{argv:?} also gave {others:?}");
        }
        // No flag of the table may be missing from the cases above.
        for refusal in REFUSALS {
            assert!(
                ONE_EACH.iter().any(|(_, flag)| *flag == refusal.flag),
                "{} has no case",
                refusal.flag
            );
        }
    }

    #[test]
    fn the_other_names_of_a_flag_are_found_too() {
        assert_eq!(names(&["--allowedTools", "Bash(ls)"]), ["--allow"]);
        assert_eq!(names(&["--disallowedTools", "Bash(rm *)"]), ["--deny"]);
        assert_eq!(names(&["--effort", "high"]), ["--reasoning-effort"]);
        assert_eq!(names(&["--append-system-prompt", "r"]), ["--rules"]);
        assert_eq!(
            names(&["--system-prompt", "s"]),
            ["--system-prompt-override"]
        );
        assert_eq!(names(&["-w"]), ["--worktree"]);
        assert_eq!(
            names(&["-w", "--ref", "main"]),
            ["--worktree", "--worktree-ref"]
        );
    }

    #[test]
    fn the_default_output_format_is_not_a_flag_the_user_gave() {
        assert!(names(&["--output-format", "plain"]).is_empty());
    }

    #[test]
    fn flags_are_listed_in_help_order_whatever_the_order_given() {
        assert_eq!(
            names(&[
                "--oauth",
                "--max-turns",
                "2",
                "--no-plan",
                "--allow",
                "Bash(ls)"
            ]),
            ["--allow", "--no-plan", "--max-turns", "--oauth"]
        );
    }

    #[test]
    fn the_message_names_each_flag_with_its_reason_and_the_way_out() {
        let args = parse(&["--effort", "high", "--worktree", "w"]);
        let message = refusal(&refused(&args));
        assert!(
            message.starts_with("error: zypi did not start."),
            "{message}"
        );
        assert!(
            message.contains(
                "  --reasoning-effort (--effort): The Python agent gets no reasoning level"
            ),
            "{message}"
        );
        assert!(
            message.contains("  --worktree (-w): The Python agent has no git worktree sessions."),
            "{message}"
        );
        assert!(!message.contains("--allow"), "{message}");
        assert!(
            message.ends_with("to see the flags that work."),
            "{message}"
        );
    }

    #[test]
    fn every_flag_has_a_reason_that_ends_in_a_full_stop() {
        for refusal in REFUSALS {
            assert!(
                refusal.reason.ends_with('.'),
                "{}: {}",
                refusal.flag,
                refusal.reason
            );
            assert!(!refusal.reason.contains("  "), "{}", refusal.flag);
        }
    }

    /// The table and `--help` agree: a flag that is refused is not offered. The two flags that `-p`
    /// takes stay in `--help`, and their text says that only `-p` takes them.
    #[test]
    fn refused_flags_are_hidden_from_help_except_the_two_that_p_takes() {
        const KEPT: [&str; 2] = ["--rules", "--system-prompt-override"];
        let command = PagerArgs::command();
        for refusal in REFUSALS {
            let long = refusal.flag.trim_start_matches("--");
            let arg = command
                .get_arguments()
                .find(|arg| arg.get_long() == Some(long))
                .unwrap_or_else(|| panic!("{} is not a flag of zypi", refusal.flag));
            if KEPT.contains(&refusal.flag) {
                assert!(!arg.is_hide_set(), "{} must stay in --help", refusal.flag);
                let help = arg.get_help().map(ToString::to_string).unwrap_or_default();
                assert!(help.contains("-p only"), "{}: {help}", refusal.flag);
            } else {
                assert!(arg.is_hide_set(), "{} must be hidden", refusal.flag);
            }
        }
    }

    /// The names a user may type must be the ones clap knows: a typo in `also` would send the
    /// user to a flag that does not exist.
    #[test]
    fn the_other_names_exist() {
        let command = PagerArgs::command();
        for refusal in REFUSALS {
            let arg = command
                .get_arguments()
                .find(|arg| arg.get_long() == Some(refusal.flag.trim_start_matches("--")))
                .unwrap();
            for name in refusal.also {
                let known = if let Some(short) = name.strip_prefix('-').filter(|n| n.len() == 1) {
                    arg.get_short() == short.chars().next()
                } else {
                    let long = name.trim_start_matches("--");
                    arg.get_all_aliases()
                        .into_iter()
                        .flatten()
                        .any(|alias| alias == long)
                };
                assert!(known, "{name} is not another name of {}", refusal.flag);
            }
        }
    }
}
