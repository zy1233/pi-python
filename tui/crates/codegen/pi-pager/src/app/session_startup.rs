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
/// Whether a persisted session (or its cwd) is worktree-backed.
pub fn parent_session_is_worktree(session_id: &str, cwd: &Path) -> bool {
    let cwd_str = cwd.to_string_lossy();
    let sessions_root = pi_shell::util::grok_home::grok_home().join("sessions");
    let encoded = pi_shell::util::grok_home::encode_cwd_dirname(&cwd_str);
    let summary_path = sessions_root
        .join(encoded)
        .join(session_id)
        .join("summary.json");
    if let Ok(bytes) = std::fs::read(&summary_path)
        && let Ok(v) = serde_json::from_slice::<serde_json::Value>(&bytes)
    {
        if v.get("session_kind").and_then(|k| k.as_str()) == Some("worktree") {
            return true;
        }
        if v.get("source_workspace_dir")
            .and_then(|k| k.as_str())
            .is_some_and(|s| !s.is_empty())
        {
            return true;
        }
        if v.get("worktree_label")
            .and_then(|k| k.as_str())
            .is_some_and(|s| !s.is_empty())
        {
            return true;
        }
    }
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
                write!(
                    f,
                    "Error: --session-id cannot be combined with --continue or --resume."
                )
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
/// Outcome of async materialization (local resolve / remote restore / preflight).
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
        deferred_local_miss: bool,
        /// Pre-TUI conversation-only remote restore: follow-up `LoadSession`
        /// must send `legacy ext RPC` so agent `[cli] restore_code`
        /// cannot checkout in-place on the new local child.
        suppress_code_restore: bool,
    },
}
/// Whether materialization may resolve a non-id resume arg by title locally.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TitleResolution {
    /// No pre-sandbox pin ran (direct callers, tests): materialization owns
    /// title selection.
    Allowed,
    /// The composition root already pinned — or definitively missed — the
    /// target before the irreversible OS sandbox. Re-selecting by title here
    /// would race a concurrent rename/create and resume a session whose
    /// persisted profile was never checked; a pinned id that vanished must
    /// also never be reinterpreted as a title.
    PinnedPreSandbox,
}
/// Context for [`materialize_startup`] (interactive vs headless share this).
#[derive(Debug, Clone, Copy)]
pub struct MaterializeCtx {
    /// When true, skip process-cwd preflight for `NewWithId` (worktree create
    /// checks the final session cwd later).
    pub has_worktree: bool,
    /// When true, attempt remote restore if the session is not on disk.
    pub allow_remote_restore: bool,
    /// See [`TitleResolution`]; carried from the pre-sandbox pin outcome.
    pub title_resolution: TitleResolution,
    /// CLI `--restore-code`. Remote codebase restore is never applied in-place;
    /// this flag either defers to `--worktree` or refuses the in-place path.
    pub restore_code: bool,
    /// Pre-TUI restore progress on stdout (interactive tty). Headless keeps
    /// stdout as JSON/NDJSON and uses stderr instead.
    pub restore_progress_on_stdout: bool,
}
impl MaterializeCtx {
    /// `--resume` miss bails fast.
    pub const fn default_allow_remote_restore() -> bool {
        false
    }
    pub fn from_pager_args(args: &PagerArgs) -> Self {
        Self {
            has_worktree: args.worktree.is_some(),
            allow_remote_restore: Self::default_allow_remote_restore(),
            title_resolution: if args.resume_target_pinned {
                TitleResolution::PinnedPreSandbox
            } else {
                TitleResolution::Allowed
            },
            restore_code: args.restore_code,
            restore_progress_on_stdout: false,
        }
    }
}
/// Resolve most-recent session id for cwd, or error.
async fn most_recent_session_id(cwd: &str) -> anyhow::Result<(String, Option<String>)> {
    let summaries = pi_shell::session::persistence::list_summaries(Some(cwd)).await?;
    if let Some(first) = summaries.first() {
        return Ok((first.info.id.to_string(), first.display_title_opt()));
    }
    if let Some((id, title)) = find_most_recent_jsonl_session(cwd) {
        return Ok((id, title));
    }
    anyhow::bail!(
        "No session found for current directory. \
         Use 'zypi' to start a new session."
    )
}

fn find_most_recent_jsonl_session(target_cwd: &str) -> Option<(String, Option<String>)> {
    let sessions_dir = pi_shell::util::grok_home::grok_home().join("sessions");
    let read_dir = std::fs::read_dir(sessions_dir).ok()?;
    let mut files: Vec<PathBuf> = read_dir
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.extension()
                .and_then(|ext| ext.to_str())
                .is_some_and(|ext| ext == "jsonl")
        })
        .collect();
    files.sort_by(|a, b| b.cmp(a));

    let norm_target = normalize_cwd_for_comparison(target_cwd);

    for path in files {
        if let Ok(file) = std::fs::File::open(&path) {
            use std::io::BufRead;
            let mut reader = std::io::BufReader::new(file);
            let mut first_line = String::new();
            if reader.read_line(&mut first_line).is_ok() && !first_line.is_empty() {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&first_line) {
                    if let Some(cwd) = v.get("cwd").and_then(|c| c.as_str()) {
                        if normalize_cwd_for_comparison(cwd) == norm_target {
                            if let Some(id) = v.get("id").and_then(|i| i.as_str()) {
                                return Some((id.to_string(), None));
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

fn normalize_cwd_for_comparison(cwd: &str) -> String {
    cwd.replace('\\', "/").trim_end_matches('/').to_lowercase()
}
/// `--restore-code` without `--worktree` on a remote miss: refuse in-place checkout.
const REMOTE_RESTORE_NEEDS_WORKTREE: &str = "--restore-code on a remote session requires --worktree \
     (refusing to check out snapshot code into the current directory)";
/// `--worktree` resume without `--restore-code`: conversation only.
pub(crate) const WORKTREE_NO_RESTORE_CODE_NOTICE: &str =
    "Snapshot code will not be restored into the worktree; pass --restore-code to restore it.";
/// Preflight: preferred id must be a UUID and not a persisted session under `cwd`.
///
/// Agent `session/new` rejects non-UUID `_meta.sessionId`; fail fast here so
/// CLI users get a clear error before ACP.
pub fn ensure_session_id_available(session_id: &str, cwd: &str) -> anyhow::Result<()> {
    if uuid::Uuid::try_parse(session_id).is_err() {
        anyhow::bail!("Error: --session-id must be a valid UUID (got '{session_id}').");
    }
    if pi_shell::session::persistence::session_exists_for_cwd(session_id, cwd) {
        anyhow::bail!("Error: Session ID {session_id} is already in use.");
    }
    Ok(())
}
/// Materialize CLI intent into a concrete startup plan (I/O + remote restore).
pub async fn materialize_startup(
    ctx: MaterializeCtx,
    intent: SessionStartupIntent,
) -> anyhow::Result<MaterializedStartup> {
    let cwd = std::env::current_dir()
        .map_err(|e| anyhow::anyhow!("Failed to get cwd: {e}"))?
        .to_string_lossy()
        .to_string();
    materialize_startup_for_cwd(ctx, intent, &cwd).await
}
/// Same as [`materialize_startup`] but with an explicit process cwd (tests / headless).
pub async fn materialize_startup_for_cwd(
    ctx: MaterializeCtx,
    intent: SessionStartupIntent,
    cwd: &str,
) -> anyhow::Result<MaterializedStartup> {
    match intent {
        SessionStartupIntent::NewAuto => Ok(MaterializedStartup::NewAuto),
        SessionStartupIntent::NewWithId { session_id } => {
            if !ctx.has_worktree {
                ensure_session_id_available(&session_id, cwd)?;
            } else if uuid::Uuid::try_parse(&session_id).is_err() {
                anyhow::bail!("Error: --session-id must be a valid UUID (got '{session_id}').");
            }
            Ok(MaterializedStartup::NewWithId { session_id })
        }
        SessionStartupIntent::Resume {
            session_id: None,
            most_recent_for_cwd: true,
        } => {
            let started = std::time::Instant::now();
            let (id, title) = most_recent_session_id(cwd).await?;
            tracing::info!(
                source = "local",
                elapsed_ms = started.elapsed().as_millis() as u64,
                "startup.continue.resolve"
            );
            Ok(MaterializedStartup::Resume {
                session_id: id,
                original_cwd: None,
                title,
                deferred_local_miss: false,
                suppress_code_restore: false,
            })
        }
        SessionStartupIntent::Resume {
            session_id: Some(session_id),
            ..
        } => {
            let r = resolve_existing_session(ctx, &session_id, cwd).await?;
            Ok(MaterializedStartup::Resume {
                session_id: r.id,
                original_cwd: r.original_cwd,
                title: r.title,
                deferred_local_miss: r.deferred_local_miss,
                suppress_code_restore: r.suppress_code_restore,
            })
        }
        SessionStartupIntent::Resume {
            session_id: None,
            most_recent_for_cwd: false,
        } => {
            anyhow::bail!("internal: invalid session startup intent (unreachable from CLI flags)")
        }
    }
}
struct ResolvedExisting {
    id: String,
    original_cwd: Option<PathBuf>,
    title: Option<String>,
    /// True only for the worktree-defer arm: the target missed local
    /// id/title resolution.
    deferred_local_miss: bool,
    suppress_code_restore: bool,
}
/// Resolve an existing session for strict resume (local / any-cwd / remote / worktree defer).
async fn resolve_existing_session(
    ctx: MaterializeCtx,
    session_id: &str,
    cwd: &str,
) -> anyhow::Result<ResolvedExisting> {
    if let Some(local_id) = pi_shell::session::resolve_local_session(session_id, cwd) {
        tracing::info!(session_id = %session_id, local_id = %local_id, "Session found locally");
        if !in_place_restore_code_allowed(ctx.restore_code, ctx.has_worktree, session_id, &local_id)
        {
            anyhow::bail!("{REMOTE_RESTORE_NEEDS_WORKTREE}");
        }
        return Ok(ResolvedExisting {
            id: local_id,
            original_cwd: None,
            title: None,
            deferred_local_miss: false,
            suppress_code_restore: false,
        });
    }
    if let Some(original_cwd) = pi_shell::session::resolve_local_session_any_cwd(session_id) {
        tracing::info!(
            session_id = %session_id,
            original_cwd = %original_cwd,
            "Session found locally under different CWD"
        );
        eprintln!(
            "Session {} found locally (originally in {})",
            session_id, original_cwd
        );
        return Ok(ResolvedExisting {
            id: session_id.to_string(),
            original_cwd: Some(PathBuf::from(original_cwd)),
            title: None,
            deferred_local_miss: false,
            suppress_code_restore: false,
        });
    }
    let arg_is_uuid = super::session_title_resolve::is_uuid_shaped(session_id);
    if !arg_is_uuid
        && ctx.title_resolution == TitleResolution::Allowed
        && let Some(resolved) = resolve_session_by_title(session_id, cwd).await?
    {
        return Ok(resolved);
    }
    // In pi-python, if an explicit UUID target is requested without a worktree,
    // allow delegating to the backend ACP agent without requiring legacy grok summary.json
    if arg_is_uuid && !ctx.has_worktree {
        return Ok(ResolvedExisting {
            id: session_id.to_string(),
            original_cwd: None,
            title: None,
            deferred_local_miss: false,
            suppress_code_restore: false,
        });
    }
    match plan_remote_miss(ctx, arg_is_uuid) {
        RemoteMissPlan::DeferToWorktree {
            deferred_local_miss,
        } => {
            tracing::info!(
                session_id = %session_id,
                restore_code = ctx.restore_code,
                "Session not found locally; deferring restore to worktree resume handler"
            );
            eprintln!(
                "Session {:?} not found locally; it will be restored into the new worktree.",
                session_id
            );
            if !ctx.restore_code {
                eprintln!("{WORKTREE_NO_RESTORE_CODE_NOTICE}");
            }
            Ok(ResolvedExisting {
                id: session_id.to_string(),
                original_cwd: None,
                title: None,
                deferred_local_miss,
                suppress_code_restore: !ctx.restore_code,
            })
        }
        RemoteMissPlan::RejectInPlaceCodeRestore { title_miss_hint } => {
            if title_miss_hint {
                anyhow::bail!(
                    "{REMOTE_RESTORE_NEEDS_WORKTREE}; {}",
                    super::session_title_resolve::title_miss_hint(session_id)
                );
            }
            anyhow::bail!("{REMOTE_RESTORE_NEEDS_WORKTREE}")
        }
        RemoteMissPlan::NotFound { title_miss_hint } => {
            if title_miss_hint {
                anyhow::bail!(
                    "Session does not exist: {}",
                    super::session_title_resolve::title_miss_hint(session_id)
                );
            }
            anyhow::bail!("Session does not exist")
        }
        RemoteMissPlan::RestoreConversation => {
            let restored = restore_session_from_remote(session_id).await;
            if arg_is_uuid {
                return restored;
            }
            restored.map_err(|e| {
                anyhow::anyhow!(
                    "{e:#}; {}",
                    super::session_title_resolve::title_miss_hint(session_id)
                )
            })
        }
    }
}
/// Policy after local id/title resolution misses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RemoteMissPlan {
    DeferToWorktree {
        deferred_local_miss: bool,
    },
    RejectInPlaceCodeRestore {
        title_miss_hint: bool,
    },
    /// Pre-TUI restore of session state + memory only (never codebase).
    RestoreConversation,
    NotFound {
        title_miss_hint: bool,
    },
}
/// Whether `--restore-code` may run in-place for this local hit.
///
/// `resolved_id != requested_id` means the CLI handle was a remote UUID that
/// only exists as a previously restored child — still a remote session, so
/// snapshot checkout requires `--worktree`.
pub(crate) fn in_place_restore_code_allowed(
    restore_code: bool,
    has_worktree: bool,
    requested_id: &str,
    resolved_id: &str,
) -> bool {
    if !restore_code || has_worktree {
        return true;
    }
    requested_id == resolved_id
}
pub(crate) fn plan_remote_miss(ctx: MaterializeCtx, arg_is_uuid: bool) -> RemoteMissPlan {
    if ctx.has_worktree {
        return RemoteMissPlan::DeferToWorktree {
            deferred_local_miss: !arg_is_uuid,
        };
    }
    if !ctx.allow_remote_restore {
        return RemoteMissPlan::NotFound {
            title_miss_hint: !arg_is_uuid,
        };
    }
    if ctx.restore_code {
        return RemoteMissPlan::RejectInPlaceCodeRestore {
            title_miss_hint: !arg_is_uuid,
        };
    }
    RemoteMissPlan::RestoreConversation
}
/// Remote-restore tail of [`resolve_existing_session`], split out so non-id
/// targets can wrap every failure with the title-miss hint.
///
/// Always restores session state + memory only. Codebase checkout is refused
/// in-place ([`RemoteMissPlan::RejectInPlaceCodeRestore`]) or deferred to the
/// worktree handler.
///
/// On timeout the future is cancelled; partial JSONL may already be on disk
/// and is recovered via a local-child scan of the remote id.
async fn restore_session_from_remote(session_id: &str) -> anyhow::Result<ResolvedExisting> {
    let raw_config = pi_shell::config::load_effective_config()
        .map_err(|e| anyhow::anyhow!("Failed to load config: {}", e))?;
    if let Some((false, source)) =
        pi_shell::util::config::session_registry_local_override_sourced(Some(&raw_config))
    {
        anyhow::bail!(
            "Session does not exist locally (session registry is disabled by {})",
            source.label()
        );
    }
    anyhow::bail!(
        "Session {session_id:?} not found locally; remote restore is not available in {}",
        crate::brand::CLI_NAME,
    );
}
/// Resolve a non-id resume arg as a session title among local sessions for `cwd`.
///
/// Matching/disambiguation rules live in [`super::session_title_resolve`]
/// (shared with the pre-sandbox saved-profile peek); this adds the cwd-scoped
/// listing and the resolved-id announcement. The arg is matched in memory and
/// never used as a filesystem path.
async fn resolve_session_by_title(
    arg: &str,
    cwd: &str,
) -> anyhow::Result<Option<ResolvedExisting>> {
    let summaries = pi_shell::session::persistence::list_summaries(Some(cwd)).await?;
    let Some(chosen) = super::session_title_resolve::select_by_title(arg, &summaries)? else {
        return Ok(None);
    };
    let id = chosen.info.id.to_string();
    tracing::info!(session_id = %id, "Session resolved by title");
    eprintln!("Resuming session {} (matched by title)", id);
    Ok(Some(ResolvedExisting {
        id,
        original_cwd: None,
        title: chosen.display_title_opt(),
        deferred_local_miss: false,
        suppress_code_restore: false,
    }))
}
#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    fn parse(args: &[&str]) -> PagerArgs {
        PagerArgs::try_parse_from(args).unwrap()
    }
    #[test]
    fn parent_session_is_worktree_detects_standalone_marker() {
        let main = crate::test_util::TempGitRepo::init("main-only");
        let clone = main.standalone_clone("wt-branch");
        assert!(clone.path.join(".git").is_dir());
        assert!(parent_session_is_worktree("any-sid", &clone.path));
        assert!(!parent_session_is_worktree("any-sid", &main.path));
    }
    #[test]
    fn parent_session_is_worktree_detects_linked_git_worktree() {
        let main = crate::test_util::TempGitRepo::init("main-only");
        let wt = main.add_linked_worktree("wt", "wt-branch");
        assert!(wt.join(".git").is_file());
        assert!(parent_session_is_worktree("any-sid", &wt));
        assert!(!parent_session_is_worktree("any-sid", &main.path));
    }
    #[test]
    fn parent_session_is_worktree_relocated_gitdir_file_is_not_worktree() {
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
        assert!(!parent_session_is_worktree("any-sid", &repo_path));
    }
    #[test]
    fn parent_session_is_worktree_plain_clone_nested_in_linked_worktree() {
        let main = crate::test_util::TempGitRepo::init("main-only");
        let wt = main.add_linked_worktree("wt", "wt-branch");
        let nested = wt.join("vendor").join("dep");
        crate::test_util::init_git_repo_on_branch(&nested, "dep-branch");
        assert!(!parent_session_is_worktree("any-sid", &nested));
        assert!(!parent_session_is_worktree("any-sid", &main.path));
    }
    #[test]
    fn parent_session_is_worktree_broken_gitdir_file_is_last_resort_true() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("broken");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".git"), "not-a-gitdir\n").unwrap();
        assert!(git2::Repository::discover(&dir).is_err());
        assert!(parent_session_is_worktree("any-sid", &dir));
    }
    #[serial_test::serial(GROK_HOME)]
    #[test]
    fn parent_session_is_worktree_summary_session_kind() {
        let mut fx = crate::test_util::GrokHomeFixture::new();
        let repo = crate::test_util::TempGitRepo::init("main");
        let cwd = repo.path.to_string_lossy().to_string();
        fx.write_summary(
            &cwd,
            "sid-kind",
            serde_json::json!({ "session_kind": "worktree" }),
        );
        assert!(parent_session_is_worktree("sid-kind", &repo.path));
    }
    #[serial_test::serial(GROK_HOME)]
    #[test]
    fn parent_session_is_worktree_summary_source_workspace_dir() {
        let mut fx = crate::test_util::GrokHomeFixture::new();
        let repo = crate::test_util::TempGitRepo::init("main");
        let cwd = repo.path.to_string_lossy().to_string();
        fx.write_summary(
            &cwd,
            "sid-src",
            serde_json::json!({ "source_workspace_dir": "/src/main" }),
        );
        assert!(parent_session_is_worktree("sid-src", &repo.path));
        fx.write_summary(
            &cwd,
            "sid-src-empty",
            serde_json::json!({ "source_workspace_dir": "" }),
        );
        assert!(!parent_session_is_worktree("sid-src-empty", &repo.path));
    }
    #[serial_test::serial(GROK_HOME)]
    #[test]
    fn parent_session_is_worktree_summary_worktree_label() {
        let mut fx = crate::test_util::GrokHomeFixture::new();
        let repo = crate::test_util::TempGitRepo::init("main");
        let cwd = repo.path.to_string_lossy().to_string();
        fx.write_summary(
            &cwd,
            "sid-label",
            serde_json::json!({ "worktree_label": "my-wt" }),
        );
        assert!(parent_session_is_worktree("sid-label", &repo.path));
        fx.write_summary(
            &cwd,
            "sid-label-empty",
            serde_json::json!({ "worktree_label": "" }),
        );
        assert!(!parent_session_is_worktree("sid-label-empty", &repo.path));
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
    fn ensure_rejects_non_uuid() {
        let err = ensure_session_id_available("my-run-1", "/tmp/does-not-matter").unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("must be a valid UUID"),
            "unexpected message: {msg}"
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
    /// hardcoded `false` here once disabled it everywhere.
    #[test]
    fn remote_restore_follows_compiled_restore_stack() {
        assert_eq!(
            MaterializeCtx::from_pager_args(&parse(&["grok"])).allow_remote_restore,
            false
        );
    }
    #[test]
    fn from_pager_args_does_not_probe_tty_for_progress() {
        assert!(
            !MaterializeCtx::from_pager_args(&parse(&["grok"])).restore_progress_on_stdout,
            "stdout vs stderr is decided at the composition root, not from_pager_args"
        );
    }
    #[test]
    fn materialize_ctx_restore_code_follows_cli_flag() {
        assert!(!MaterializeCtx::from_pager_args(&parse(&["grok"])).restore_code);
        assert!(!MaterializeCtx::from_pager_args(&parse(&["grok", "-r", "abc"])).restore_code);
        assert!(
            MaterializeCtx::from_pager_args(&parse(&["grok", "-r", "abc", "--restore-code"]))
                .restore_code
        );
        let wt = MaterializeCtx::from_pager_args(&parse(&[
            "grok",
            "-r",
            "abc",
            "--restore-code",
            "--worktree",
        ]));
        assert!(wt.restore_code);
        assert!(wt.has_worktree);
    }
    fn remote_miss_ctx(restore_code: bool, has_worktree: bool) -> MaterializeCtx {
        MaterializeCtx {
            has_worktree,
            allow_remote_restore: true,
            title_resolution: TitleResolution::Allowed,
            restore_code,
            restore_progress_on_stdout: false,
        }
    }
    #[test]
    fn in_place_restore_code_allowed_blocks_restored_remote_child() {
        assert!(in_place_restore_code_allowed(
            true, false, "local-id", "local-id"
        ));
        assert!(!in_place_restore_code_allowed(
            true,
            false,
            "remote-uuid",
            "restored-child"
        ));
        assert!(in_place_restore_code_allowed(
            true,
            true,
            "remote-uuid",
            "restored-child"
        ));
        assert!(in_place_restore_code_allowed(
            false,
            false,
            "remote-uuid",
            "restored-child"
        ));
    }
    #[test]
    fn plan_remote_miss_restore_code_false_restores_conversation_only() {
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(false, false), true),
            RemoteMissPlan::RestoreConversation
        );
    }
    #[test]
    fn plan_remote_miss_restore_code_true_without_worktree_is_rejected() {
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(true, false), true),
            RemoteMissPlan::RejectInPlaceCodeRestore {
                title_miss_hint: false,
            }
        );
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(true, false), false),
            RemoteMissPlan::RejectInPlaceCodeRestore {
                title_miss_hint: true,
            }
        );
    }
    #[test]
    fn plan_remote_miss_worktree_defers_without_restore_code() {
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(false, true), true),
            RemoteMissPlan::DeferToWorktree {
                deferred_local_miss: false,
            }
        );
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(false, true), false),
            RemoteMissPlan::DeferToWorktree {
                deferred_local_miss: true,
            }
        );
    }
    #[test]
    fn plan_remote_miss_restore_code_true_with_worktree_defers() {
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(true, true), true),
            RemoteMissPlan::DeferToWorktree {
                deferred_local_miss: false,
            }
        );
        assert_eq!(
            plan_remote_miss(remote_miss_ctx(true, true), false),
            RemoteMissPlan::DeferToWorktree {
                deferred_local_miss: true,
            }
        );
    }
    #[test]
    fn worktree_no_restore_code_notice_mentions_flag() {
        assert!(WORKTREE_NO_RESTORE_CODE_NOTICE.contains("--restore-code"));
    }
    /// `--restore-code --worktree` stays on the existing defer path.
    #[tokio::test]
    async fn remote_miss_restore_code_with_worktree_defers() {
        let id = "no such remote target";
        let out = materialize_startup_for_cwd(
            remote_miss_ctx(true, true),
            SessionStartupIntent::Resume {
                session_id: Some(id.into()),
                most_recent_for_cwd: false,
            },
            "/nonexistent/cwd/for/remote-miss-code-wt",
        )
        .await
        .unwrap();
        match out {
            MaterializedStartup::Resume {
                session_id,
                deferred_local_miss,
                suppress_code_restore,
                ..
            } => {
                assert_eq!(session_id, id);
                assert!(deferred_local_miss, "non-uuid defer must flag a title miss");
                assert!(
                    !suppress_code_restore,
                    "--restore-code --worktree must still restore snapshot code"
                );
            }
            other => panic!("expected Resume, got {other:?}"),
        }
    }
    #[tokio::test]
    async fn remote_miss_worktree_without_restore_code_suppresses_snapshot() {
        let id = "no such remote target";
        let out = materialize_startup_for_cwd(
            remote_miss_ctx(false, true),
            SessionStartupIntent::Resume {
                session_id: Some(id.into()),
                most_recent_for_cwd: false,
            },
            "/nonexistent/cwd/for/remote-miss-wt-no-code",
        )
        .await
        .unwrap();
        match out {
            MaterializedStartup::Resume {
                session_id,
                suppress_code_restore,
                ..
            } => {
                assert_eq!(session_id, id);
                assert!(suppress_code_restore);
            }
            other => panic!("expected Resume, got {other:?}"),
        }
    }
    /// Without `--chat` an unknown id still goes through disk/GCS resolution
    /// (pinned via `allow_remote_restore: false` → strict "does not exist").
    #[ignore = "pi-python: grok-specific feature not supported"]
    #[tokio::test]
    async fn materialize_resume_id_without_chat_still_resolves_on_disk() {
        let ctx = MaterializeCtx {
            has_worktree: false,
            allow_remote_restore: false,
            title_resolution: TitleResolution::Allowed,
            restore_code: false,
            restore_progress_on_stdout: false,
        };
        let err = materialize_startup_for_cwd(
            ctx,
            SessionStartupIntent::Resume {
                session_id: Some("00000000-dead-beef-0000-000000000000".into()),
                most_recent_for_cwd: false,
            },
            "/nonexistent/cwd/for/build-resume-test",
        )
        .await
        .unwrap_err();
        assert!(
            err.to_string().contains("does not exist"),
            "unexpected error: {err}"
        );
    }
    mod resume_by_title {
        use super::*;
        use crate::test_util::GrokHomeFixture;
        fn local_ctx() -> MaterializeCtx {
            MaterializeCtx {
                has_worktree: false,
                allow_remote_restore: false,
                title_resolution: TitleResolution::Allowed,
                restore_code: false,
                restore_progress_on_stdout: false,
            }
        }
        async fn resume(arg: &str, cwd: &str) -> anyhow::Result<MaterializedStartup> {
            materialize_startup_for_cwd(
                local_ctx(),
                SessionStartupIntent::Resume {
                    session_id: Some(arg.into()),
                    most_recent_for_cwd: false,
                },
                cwd,
            )
            .await
        }
        /// Also covers letter-case insensitivity: the query case differs from
        /// the stored title.
        #[serial_test::serial(GROK_HOME)]
        #[tokio::test]
        async fn title_fallback_resumes_single_match_case_insensitively() {
            let mut fx = GrokHomeFixture::new();
            let cwd_str = fx.cwd_str();
            let id = "bbbbbbbb-1111-2222-3333-444444444444";
            fx.write_summary(
                &cwd_str,
                id,
                serde_json::json!({ "generated_title": "Fix Login Bug", "title_is_manual": true }),
            );
            fx.write_summary(
                &cwd_str,
                "bbbbbbbb-1111-2222-3333-555555555555",
                serde_json::json!({ "generated_title": "Other Work" }),
            );
            match resume("fix login bug", &cwd_str).await.unwrap() {
                MaterializedStartup::Resume {
                    session_id,
                    original_cwd,
                    title,
                    ..
                } => {
                    assert_eq!(session_id, id);
                    assert!(original_cwd.is_none());
                    assert_eq!(title.as_deref(), Some("Fix Login Bug"));
                }
                other => panic!("expected Resume, got {other:?}"),
            }
        }
        /// Id resolution stays authoritative: when the arg is an on-disk
        /// session id, the title fallback is never consulted even though
        /// another session carries that exact title.
        #[serial_test::serial(GROK_HOME)]
        #[tokio::test]
        async fn id_hit_beats_title_fallback() {
            let mut fx = GrokHomeFixture::new();
            let cwd_str = fx.cwd_str();
            fx.write_summary(
                &cwd_str,
                "release-notes",
                serde_json::json!({ "generated_title": "id-owner" }),
            );
            fx.write_summary(
                &cwd_str,
                "cccccccc-1111-2222-3333-444444444444",
                serde_json::json!({ "generated_title": "release-notes", "title_is_manual": true }),
            );
            match resume("release-notes", &cwd_str).await.unwrap() {
                MaterializedStartup::Resume {
                    session_id, title, ..
                } => {
                    assert_eq!(session_id, "release-notes");
                    assert!(title.is_none());
                }
                other => panic!("expected Resume, got {other:?}"),
            }
        }
        /// Provenance for the worktree failure hint: only the defer arm (a
        /// local id/title miss under `--worktree`) flags the target; a
        /// resolved local id — even a legacy non-UUID one — never does.
        #[serial_test::serial(GROK_HOME)]
        #[tokio::test]
        async fn worktree_defer_flags_local_miss_and_local_hit_does_not() {
            let mut fx = GrokHomeFixture::new();
            let cwd_str = fx.cwd_str();
            fx.write_summary(&cwd_str, "release-notes", serde_json::json!({}));
            let worktree_ctx = MaterializeCtx {
                has_worktree: true,
                ..local_ctx()
            };
            let resume_intent = |arg: &str| SessionStartupIntent::Resume {
                session_id: Some(arg.into()),
                most_recent_for_cwd: false,
            };
            let hit =
                materialize_startup_for_cwd(worktree_ctx, resume_intent("release-notes"), &cwd_str)
                    .await
                    .unwrap();
            match hit {
                MaterializedStartup::Resume {
                    session_id,
                    deferred_local_miss,
                    ..
                } => {
                    assert_eq!(session_id, "release-notes");
                    assert!(!deferred_local_miss, "resolved id must not flag a miss");
                }
                other => panic!("expected Resume, got {other:?}"),
            }
            let miss = materialize_startup_for_cwd(
                worktree_ctx,
                resume_intent("no such target"),
                &cwd_str,
            )
            .await
            .unwrap();
            match miss {
                MaterializedStartup::Resume {
                    session_id,
                    deferred_local_miss,
                    ..
                } => {
                    assert_eq!(session_id, "no such target");
                    assert!(deferred_local_miss, "defer must flag the local miss");
                }
                other => panic!("expected Resume, got {other:?}"),
            }
            let uuid_miss = materialize_startup_for_cwd(
                worktree_ctx,
                resume_intent("99999999-9999-4999-8999-999999999999"),
                &cwd_str,
            )
            .await
            .unwrap();
            match uuid_miss {
                MaterializedStartup::Resume {
                    deferred_local_miss,
                    ..
                } => {
                    assert!(
                        !deferred_local_miss,
                        "UUID defer must not flag a title-capable miss"
                    );
                }
                other => panic!("expected Resume, got {other:?}"),
            }
        }
    }
}
