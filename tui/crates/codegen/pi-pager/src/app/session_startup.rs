//! Canonical session-selection CLI intent.
//!
//! Built once from CLI flags and consumed by interactive resolve, the event
//! loop, and headless mode so resume / new-with-id are not re-derived
//! in three places.
use super::cli::PagerArgs;
use std::path::{Path, PathBuf};
/// Session-create intent deferred until [`AppView::session_startup_allowed`].
///
/// Replaces the prior matrix of `startup_load_session` + cwd tuple + ad-hoc
/// preferred-only replay.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeferredSessionStartup {
    /// Strict resume (`-r` / `-c` / picker load).
    Load {
        session_id: String,
        session_cwd: Option<PathBuf>,
    },
    /// Client-chosen id (`--session-id`), also mirrored into `preferred_session_id`.
    NewWithId { session_id: String },
}
/// One owner for every action deferred behind auth/folder-trust startup gates.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeferredStartupActions {
    pub session: Option<DeferredSessionStartup>,
    pub preferred_session_id: Option<String>,
    pub worktree: bool,
    pub worktree_label: Option<String>,
    pub worktree_ref: Option<String>,
    pub new_session: bool,
    pub prompt: Option<String>,
}
impl DeferredStartupActions {
    pub fn is_empty(&self) -> bool {
        self == &Self::default()
    }
    pub fn take(&mut self) -> Self {
        std::mem::take(self)
    }
}
/// Whether `cwd` is a git worktree (a linked one, or a standalone clone made by `--worktree`).
///
/// Looks at the directory only. It used to read the `summary.json` of the session too; the agent
/// owns its sessions now and the pager reads no session files (ADR1).
pub fn cwd_is_worktree(cwd: &Path) -> bool {
    if let Some(info) = crate::git_info::compute_cwd_git_info(cwd) {
        return info.is_worktree;
    }
    for dir in cwd.ancestors() {
        let git = dir.join(".git");
        if git.is_file() {
            return true;
        }
        if git.is_dir() {
            return std::fs::read_to_string(git.join("grok-worktree-source"))
                .is_ok_and(|s| !s.trim().is_empty());
        }
    }
    false
}
/// Pure interpretation of session-selection CLI flags (no I/O).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionStartupIntent {
    /// Fresh session; agent picks the ID.
    NewAuto,
    /// Fresh session with a client-chosen ID (must not exist under cwd).
    NewWithId { session_id: String },
    /// Load an existing session (strict — never create).
    Resume {
        /// `None` means resolve most-recent for cwd at materialize time.
        session_id: Option<String>,
        most_recent_for_cwd: bool,
    },
}
/// Flag combinations that clap allows but we reject at runtime.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StartupFlagError {
    /// `--session-id` together with resume/continue/load.
    SessionIdWithResume,
}
impl std::fmt::Display for StartupFlagError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SessionIdWithResume => {
                write!(f, "--session-id cannot be combined with --continue or --resume.")
            }
        }
    }
}
impl std::error::Error for StartupFlagError {}
/// Inputs shared by interactive CLI and headless (no clap dependency).
#[derive(Debug, Clone, Copy)]
pub struct SessionStartupFlags<'a> {
    pub session_id: Option<&'a str>,
    /// Explicit resume id from `-r` / `--resume` (not the empty most-recent sentinel).
    pub resume_session_id: Option<&'a str>,
    /// `--resume` with no value (most recent for cwd).
    pub resume_most_recent: bool,
    pub continue_last_session: bool,
}
/// Classify session-selection flags into a single intent (no I/O).
pub fn session_startup_intent_from_flags(
    f: SessionStartupFlags<'_>,
) -> Result<SessionStartupIntent, StartupFlagError> {
    let has_resume_id = f.resume_session_id.is_some();
    let most_recent = f.resume_most_recent || f.continue_last_session;
    let has_resume_or_continue = has_resume_id || most_recent;
    if let Some(sid) = f.session_id {
        if has_resume_or_continue {
            return Err(StartupFlagError::SessionIdWithResume);
        }
        return Ok(SessionStartupIntent::NewWithId {
            session_id: sid.to_owned(),
        });
    }
    if let Some(id) = f.resume_session_id {
        return Ok(SessionStartupIntent::Resume {
            session_id: Some(id.to_owned()),
            most_recent_for_cwd: false,
        });
    }
    if most_recent {
        return Ok(SessionStartupIntent::Resume {
            session_id: None,
            most_recent_for_cwd: true,
        });
    }
    Ok(SessionStartupIntent::NewAuto)
}
impl PagerArgs {
    /// Classify session-selection flags into a single intent (no I/O).
    pub fn session_startup_intent(&self) -> Result<SessionStartupIntent, StartupFlagError> {
        session_startup_intent_from_flags(SessionStartupFlags {
            session_id: self.session_id.as_deref(),
            resume_session_id: self.session_to_resume(),
            resume_most_recent: self.resume_most_recent(),
            continue_last_session: self.continue_last_session,
        })
    }
}
/// What the start-up flags came to once the agent had answered
/// ([`super::session_resolve::resolve_startup`]).
#[derive(Debug, Clone)]
pub enum MaterializedStartup {
    /// Create a new session with an agent-chosen ID (or defer to welcome).
    NewAuto,
    /// Create a new session with this ID (`session/new` meta.sessionId).
    NewWithId { session_id: String },
    /// Strict load of an existing session.
    Resume {
        session_id: String,
        original_cwd: Option<PathBuf>,
        title: Option<String>,
        /// The target missed local id/title resolution and was deferred to
        /// the worktree resume handler; worktree failure messages append the
        /// no-match hint only for this outcome (never inferred from shape).
        /// Always `false` for a session the agent listed: a miss is an error
        /// before the terminal starts, never a deferral.
        deferred_local_miss: bool,
        /// Pre-TUI conversation-only remote restore: follow-up `LoadSession`
        /// must send `legacy ext RPC` so agent `[cli] restore_code`
        /// cannot checkout in-place on the new local child. Always `false`
        /// for a session the agent listed (the remote restore path is gone
        /// from the standard ACP client; plan item 1.P6 removes the rest).
        suppress_code_restore: bool,
    },
}
/// `--session-id` must be a UUID.
///
/// Agent `session/new` rejects non-UUID `_meta.sessionId`; fail fast here so
/// CLI users get a clear error before ACP.
pub(crate) fn require_uuid_session_id(session_id: &str) -> anyhow::Result<()> {
    if uuid::Uuid::try_parse(session_id).is_err() {
        anyhow::bail!("--session-id must be a valid UUID (got '{session_id}').");
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    fn parse(args: &[&str]) -> PagerArgs {
        PagerArgs::try_parse_from(args).unwrap()
    }
    #[test]
    fn cwd_is_worktree_detects_standalone_marker() {
        let main = crate::test_util::TempGitRepo::init("main-only");
        let clone = main.standalone_clone("wt-branch");
        assert!(clone.path.join(".git").is_dir());
        assert!(cwd_is_worktree(&clone.path));
        assert!(!cwd_is_worktree(&main.path));
    }
    #[test]
    fn cwd_is_worktree_detects_linked_git_worktree() {
        let main = crate::test_util::TempGitRepo::init("main-only");
        let wt = main.add_linked_worktree("wt", "wt-branch");
        assert!(wt.join(".git").is_file());
        assert!(cwd_is_worktree(&wt));
        assert!(!cwd_is_worktree(&main.path));
    }
    #[test]
    fn cwd_is_worktree_relocated_gitdir_file_is_not_worktree() {
        let tmp = tempfile::tempdir().unwrap();
        let repo_path = tmp.path().join("sub");
        crate::test_util::init_git_repo_on_branch(&repo_path, "main");
        let git_dir = tmp.path().join("modules").join("sub");
        std::fs::create_dir_all(git_dir.parent().unwrap()).unwrap();
        std::fs::rename(repo_path.join(".git"), &git_dir).unwrap();
        std::fs::write(
            repo_path.join(".git"),
            format!("gitdir: {}\n", git_dir.display()),
        )
        .unwrap();
        assert!(repo_path.join(".git").is_file());
        assert!(
            git2::Repository::discover(&repo_path).is_ok(),
            "relocated gitdir must still discover"
        );
        assert!(!cwd_is_worktree(&repo_path));
    }
    #[test]
    fn cwd_is_worktree_plain_clone_nested_in_linked_worktree() {
        let main = crate::test_util::TempGitRepo::init("main-only");
        let wt = main.add_linked_worktree("wt", "wt-branch");
        let nested = wt.join("vendor").join("dep");
        crate::test_util::init_git_repo_on_branch(&nested, "dep-branch");
        assert!(!cwd_is_worktree(&nested));
        assert!(!cwd_is_worktree(&main.path));
    }
    #[test]
    fn cwd_is_worktree_broken_gitdir_file_is_last_resort_true() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("broken");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".git"), "not-a-gitdir\n").unwrap();
        assert!(git2::Repository::discover(&dir).is_err());
        assert!(cwd_is_worktree(&dir));
    }
    #[test]
    fn deferred_startup_owner_take_is_atomic() {
        let mut actions = DeferredStartupActions {
            session: Some(DeferredSessionStartup::NewWithId {
                session_id: "client-id".into(),
            }),
            prompt: Some("prompt".into()),
            ..Default::default()
        };
        assert!(!actions.is_empty());
        let snapshot = actions.take();
        assert!(actions.is_empty());
        assert!(snapshot.session.is_some());
        assert_eq!(snapshot.prompt.as_deref(), Some("prompt"));
    }
    #[test]
    fn intent_default_is_new_auto() {
        assert_eq!(
            parse(&["grok"]).session_startup_intent().unwrap(),
            SessionStartupIntent::NewAuto
        );
    }
    #[test]
    fn intent_resume_id() {
        assert_eq!(
            parse(&["grok", "--resume", "abc"])
                .session_startup_intent()
                .unwrap(),
            SessionStartupIntent::Resume {
                session_id: Some("abc".into()),
                most_recent_for_cwd: false,
            }
        );
    }
    #[test]
    fn intent_resume_empty_is_most_recent() {
        assert_eq!(
            parse(&["grok", "--resume"])
                .session_startup_intent()
                .unwrap(),
            SessionStartupIntent::Resume {
                session_id: None,
                most_recent_for_cwd: true,
            }
        );
    }
    #[test]
    fn intent_continue() {
        assert_eq!(
            parse(&["grok", "-c"]).session_startup_intent().unwrap(),
            SessionStartupIntent::Resume {
                session_id: None,
                most_recent_for_cwd: true,
            }
        );
    }
    #[test]
    fn intent_session_id_alone_is_new_with_id() {
        assert_eq!(
            parse(&["grok", "--session-id", "my-id"])
                .session_startup_intent()
                .unwrap(),
            SessionStartupIntent::NewWithId {
                session_id: "my-id".into(),
            }
        );
    }
    #[test]
    fn intent_session_id_with_resume_errors() {
        let err = parse(&["grok", "-r", "a", "-s", "b"])
            .session_startup_intent()
            .unwrap_err();
        assert_eq!(err, StartupFlagError::SessionIdWithResume);
    }
    #[test]
    fn intent_from_flags_matches_pager_args() {
        let args = parse(&["grok", "-r", "old"]);
        let from_flags = session_startup_intent_from_flags(SessionStartupFlags {
            session_id: None,
            resume_session_id: Some("old"),
            resume_most_recent: false,
            continue_last_session: false,
        })
        .unwrap();
        assert_eq!(from_flags, args.session_startup_intent().unwrap());
    }
    #[test]
    fn require_uuid_session_id_rejects_non_uuid() {
        let err = require_uuid_session_id("my-run-1").unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("must be a valid UUID") && msg.contains("my-run-1"),
            "unexpected message: {msg}"
        );
        assert!(
            !msg.starts_with("Error:"),
            "main prints the `Error: ` prefix itself: {msg}"
        );
    }

    #[test]
    fn require_uuid_session_id_accepts_a_uuid() {
        require_uuid_session_id("8f0c6f64-5a8b-4a39-9a3e-0a7f2f6d1c11").unwrap();
    }

    #[test]
    fn flag_conflict_message_has_no_error_prefix() {
        let msg = StartupFlagError::SessionIdWithResume.to_string();
        assert!(msg.contains("--session-id"), "{msg}");
        assert!(
            !msg.starts_with("Error:"),
            "main prints the `Error: ` prefix itself: {msg}"
        );
    }
    #[test]
    fn deferred_session_intent_variants_are_distinct() {
        let load = DeferredSessionStartup::Load {
            session_id: "s".into(),
            session_cwd: None,
        };
        let nid = DeferredSessionStartup::NewWithId {
            session_id: "s".into(),
        };
        assert_ne!(load, nid);
    }
}
