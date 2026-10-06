use chrono::{DateTime, Utc};
use std::borrow::Cow;
use std::io;
use std::path::{Path, PathBuf};

use crate::session::storage::relocation::{RelocationError, RelocationView};
use crate::session::storage::{JsonlStorageAdapter, StorageAdapter};
use crate::util::grok_home::grok_home;
use agent_client_protocol as acp;
use pi_sampling_types::ReasoningEffort;

use crate::session::info::Info;

/// Current chat history format version.
/// - Version 0: Legacy ChatRequestMessage format (default for old sessions)
/// - Version 1: ConversationItem format (used for new sessions)
pub(crate) const CHAT_FORMAT_VERSION: u8 = 1;

/// Maximum Unicode scalars in a session title (`/rename`, dashboard editor,
/// and the `x.ai/session/rename` ext boundary). Counted after control-strip
/// and trim.
pub const MAX_TITLE_SCALARS: usize = 100;

/// C0/C1 plus the bidi/format overrides the dashboard rename editor
/// already rejects. Shared by persist-drop and display-FFFD so the
/// character class cannot drift.
#[inline]
pub fn is_forbidden_title_char(c: char) -> bool {
    c.is_control()
        || matches!(
            c,
            '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}'
        )
}

/// Drop C0/C1 and bidi/format controls, then trim. The ext boundary, pull
/// hydrate, and pager ingest share this so a title cannot carry terminal
/// escapes or RTL overrides into `display_name` / `summary.json`.
///
/// Already-clean input is borrowed (trim is a subslice); only a title
/// that actually contains forbidden chars allocates.
pub fn sanitize_rename_title(title: &str) -> Cow<'_, str> {
    if title.chars().any(is_forbidden_title_char) {
        let mut cleaned: String = title
            .chars()
            .filter(|c| !is_forbidden_title_char(*c))
            .collect();
        let trimmed = cleaned.trim();
        if trimmed.len() != cleaned.len() {
            cleaned = trimmed.to_string();
        }
        Cow::Owned(cleaned)
    } else {
        Cow::Borrowed(title.trim())
    }
}

/// Sanitize then cap. `None` when the result is blank. Overlong titles are
/// truncated (ingest/pull defense); the ext rename path rejects instead.
pub fn sanitize_and_cap_title(title: &str) -> Option<String> {
    let cleaned = sanitize_rename_title(title);
    if cleaned.is_empty() {
        return None;
    }
    if cleaned.chars().count() <= MAX_TITLE_SCALARS {
        Some(cleaned.into_owned())
    } else {
        Some(cleaned.chars().take(MAX_TITLE_SCALARS).collect())
    }
}

use serde::{Deserialize, Serialize};

// /btw side question persistence types

/// A single /btw side question entry persisted to `btw_history.jsonl`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BtwEntry {
    /// Unique ID for this side question.
    pub btw_session_id: String,
    /// The parent session ID.
    pub parent_session_id: String,
    /// When the question was asked.
    pub asked_at: DateTime<Utc>,
    /// The user's question.
    pub question: String,
    /// The model's response (empty if failed).
    pub answer: String,
    /// Model used.
    pub model: String,
    /// Whether the request succeeded.
    pub success: bool,
    /// Error message if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// Model-call attempts made (1 = no retry). Entries written before this
    /// field existed deserialize as 1.
    #[serde(default = "default_btw_attempts")]
    pub attempts: u32,
}

fn default_btw_attempts() -> u32 {
    1
}

// Local feedback persistence types

/// A feedback entry persisted to `~/.grok/sessions/.../feedback.jsonl`.
///
/// Uses a tagged enum so different feedback types are self-describing in the
/// JSONL file (currently only `UserFeedback`).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum LocalFeedbackEntry {
    /// Regular user feedback (spontaneous or solicited via heuristics)
    UserFeedback(UserFeedbackEntry),
}

/// A user feedback entry (thumbs, stars, text, or dismiss).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UserFeedbackEntry {
    pub submitted_at: DateTime<Utc>,
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub turn_number: Option<i64>,
    /// Whether this was a response to a server-initiated FeedbackRequest
    pub solicited: bool,
    /// The feedback request ID (only set for solicited feedback)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    /// True if the user dismissed the feedback request without responding
    #[serde(default, skip_serializing_if = "is_false")]
    pub dismissed: bool,
    /// The full submission payload (omitted when dismissed)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub submission: Option<prod_mc_cli_chat_proxy_types::feedback_types::FeedbackSubmission>,
}

/// Helper for `#[serde(skip_serializing_if)]` on bool fields.
pub(crate) fn is_false(v: &bool) -> bool {
    !v
}

#[cfg(test)]
#[path = "persistence_feedback_tests.rs"]
mod feedback_tests;

pub use pi_shared::session::session_dir;

type RelocationResult<T> = crate::session::storage::relocation::Result<T>;
type SummaryReader = fn(&Path) -> RelocationResult<Summary>;

fn storage_view(sessions_root: &Path) -> RelocationResult<RelocationView> {
    RelocationView::load_for_sessions_root(sessions_root)
}

/// Check if a session exists locally under the given cwd.
///
/// This is the correct check for the `-r` resume path: a session is only
/// "already local" if it lives under the **same** cwd as the current invocation.
/// A session stored under a different cwd does NOT satisfy this check — the
/// caller must still run the remote restore into the requested cwd.
pub fn session_exists_for_cwd(session_id: &str, cwd: &str) -> bool {
    let sessions_root = crate::util::grok_home::grok_home().join("sessions");
    session_exists_for_cwd_in_root(session_id, cwd, &sessions_root)
}

/// A directory is a resumable session only if it has a `summary.json`; this
/// skips `images/`-only stubs that would otherwise hijack `--resume`. Used by
/// the resume/restore resolution path; `find_session_dir_by_id` intentionally
/// stays dir-only for non-resume compatibility.
fn is_persisted_session_dir(session_path: &Path) -> bool {
    session_path.join("summary.json").is_file()
}

/// Inner implementation of `session_exists_for_cwd` with an injectable root.
/// Separated for deterministic tempdir-based tests.
fn session_exists_for_cwd_in_root(session_id: &str, cwd: &str, sessions_root: &Path) -> bool {
    let encoded = crate::util::grok_home::encode_cwd_dirname(cwd);
    let session_path = sessions_root.join(&encoded).join(session_id);
    is_persisted_session_dir(&session_path)
}

/// Find the local child session id that was previously restored from `remote_session_id`
/// in the given `cwd`.
///
/// When a remote session is restored, a new local child is created with
/// `summary.parent_session_id == remote_session_id`.  On a second
/// `grok -r <remote_id>` in the same cwd, this function returns the already-restored
/// child so no duplicate restore is performed.
///
/// If multiple children match (e.g., from pre-fix duplicate restores), the
/// most recently used one is returned.  Selection is fully deterministic:
/// 1. Newest `updated_at` timestamp in `summary.json`
/// 2. Newest session directory mtime as a tie-breaker (catches equal timestamps)
/// 3. Lexicographically largest session id as the final stable tie-breaker
///
/// Returns `Some(local_child_id)` when at least one matching child is found.
/// Returns `None` when no child with `parent_session_id == remote_session_id` exists.
pub fn find_local_child_for_remote(remote_session_id: &str, cwd: &str) -> Option<String> {
    let sessions_root = crate::util::grok_home::grok_home().join("sessions");
    find_local_child_for_remote_in_root(remote_session_id, cwd, &sessions_root)
}

/// Resolve a session ID to one that is available locally under `cwd`.
///
/// Checks in order:
///   1. `session_id` exists directly under `cwd` → returns it as-is.
///   2. A previously restored child of `session_id` exists → returns the child ID.
///   3. Neither found → returns `None` (caller should restore from remote).
pub fn resolve_local_session(session_id: &str, cwd: &str) -> Option<String> {
    if session_exists_for_cwd(session_id, cwd) {
        return Some(session_id.to_string());
    }
    find_local_child_for_remote(session_id, cwd)
}

// Repo-wide session resolution (for worktree resume)

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum LocalSessionResolutionKind {
    ExactCwd,
    RestoredChildInExactCwd,
    SameRepoDifferentCwd,
    RestoredChildInSameRepoDifferentCwd,
}

fn find_local_child_for_remote_in_root(
    remote_session_id: &str,
    cwd: &str,
    sessions_root: &Path,
) -> Option<String> {
    let encoded = crate::util::grok_home::encode_cwd_dirname(cwd);
    let cwd_dir = sessions_root.join(&encoded);
    if !cwd_dir.exists() {
        return None;
    }

    // Collect all matching children.  Multiple can exist when a user ran
    // `grok -r <remote_id>` before this fix was deployed.
    // Tuple: (updated_at, dir_mtime_nanos, session_id) — all sorted descending.
    let mut candidates: Vec<(String, u128, String)> = Vec::new();

    let entries = std::fs::read_dir(&cwd_dir).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let summary_path = path.join("summary.json");
        if !summary_path.exists() {
            continue;
        }
        // Parse minimum fields without deserializing the full Summary,
        // so we don't fail on missing/extra fields from older/newer formats.
        if let Ok(raw) = std::fs::read_to_string(&summary_path)
            && let Ok(partial) = serde_json::from_str::<serde_json::Value>(&raw)
            && partial.get("parent_session_id").and_then(|v| v.as_str()) == Some(remote_session_id)
            && let Some(session_id) = path.file_name().and_then(|n| n.to_str())
        {
            let updated_at = partial
                .get("updated_at")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            // Directory mtime as a tie-breaker for equal updated_at values.
            let dir_mtime = std::fs::metadata(&path)
                .and_then(|m| m.modified())
                .map(|t| {
                    t.duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_nanos())
                        .unwrap_or(0)
                })
                .unwrap_or(0);
            candidates.push((updated_at, dir_mtime, session_id.to_string()));
        }
    }

    // Sort descending by all three keys for full determinism.
    candidates.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)).then(b.2.cmp(&a.2)));
    candidates.into_iter().next().map(|(_, _, id)| id)
}

/// Check if a session exists locally by session ID.
/// Searches across ALL cwd directories under `~/.grok/sessions/`.
///
/// Use `session_exists_for_cwd` instead when the target cwd is known
/// (e.g., the `-r` resume path) to avoid false-positive matches.
/// Find a session by ID across **all** CWD directories under `~/.grok/sessions/`.
///
/// Unlike [`resolve_local_session`] which only checks a single CWD,
/// this scans every encoded-CWD subdirectory. Returns the decoded CWD path
/// that contains the session, or `None` if not found anywhere.
///
/// This is used by the pager's `--resume` to find sessions that were created
/// in a different CWD (e.g., a worktree) than the one the user is currently in.
pub fn resolve_local_session_any_cwd(session_id: &str) -> Option<String> {
    resolve_local_session_any_cwd_result(session_id)
        .ok()
        .flatten()
}

pub(crate) fn resolve_local_session_any_cwd_result(session_id: &str) -> io::Result<Option<String>> {
    resolve_local_session_any_cwd_in_root(session_id, &grok_home().join("sessions"))
        .map_err(io::Error::other)
}

fn resolve_local_session_any_cwd_in_root(
    session_id: &str,
    sessions_root: &Path,
) -> Result<Option<String>, crate::session::storage::relocation::RelocationError> {
    let Some(session_path) = storage_view(sessions_root)?.find_persisted_session_dir(session_id)?
    else {
        return Ok(None);
    };
    Ok(session_path
        .parent()
        .and_then(crate::util::grok_home::decode_cwd_from_dirname))
}

pub(crate) fn find_persisted_session_dir_by_id_result(
    session_id: &str,
) -> io::Result<Option<PathBuf>> {
    find_persisted_session_dir_by_id_in_root_result(session_id, &grok_home().join("sessions"))
}

pub(crate) fn find_persisted_session_dir_by_id_in_root_result(
    session_id: &str,
    sessions_root: &Path,
) -> io::Result<Option<PathBuf>> {
    storage_view(sessions_root)
        .and_then(|view| view.find_persisted_session_dir(session_id))
        .map_err(io::Error::other)
}

#[cfg(test)]
fn session_exists_in_root(session_id: &str, sessions_root: &Path) -> bool {
    find_persisted_session_dir_by_id_in_root_result(session_id, sessions_root)
        .is_ok_and(|path| path.is_some())
}

/// Inner implementation with injectable root for testing.
pub(crate) fn find_summary_by_session_id_in_root(
    session_id: &str,
    sessions_root: &Path,
) -> Option<Summary> {
    let path = storage_view(sessions_root)
        .ok()?
        .find_persisted_session_dir(session_id)
        .ok()
        .flatten()?;
    read_summary_from_dir(&path).ok()
}

fn read_summary_from_dir(session_dir: &Path) -> RelocationResult<Summary> {
    let path = session_dir.join("summary.json");
    let bytes = std::fs::read(&path).map_err(|error| RelocationError::Io {
        operation: "read",
        path: path.clone(),
        source: error,
    })?;
    serde_json::from_slice(&bytes).map_err(|source| RelocationError::Json { path, source })
}

/// The most recently updated local session summary for `cwd` (by
/// `last_active_at` else `updated_at`), or `None` if there are no local sessions
/// for that cwd. Sync and local-only — suitable for the startup path that must
/// resolve the sandbox profile before the (irreversible) OS sandbox is applied.
fn most_recent_local_summary_for_cwd_in_root(cwd: &str, sessions_root: &Path) -> Option<Summary> {
    most_recent_local_summary_for_cwd_in_view(
        cwd,
        &storage_view(sessions_root).ok()?,
        read_summary_from_dir,
    )
    .ok()
    .flatten()
}

fn most_recent_local_summary_for_cwd_in_view(
    cwd: &str,
    view: &RelocationView,
    read_summary: SummaryReader,
) -> RelocationResult<Option<Summary>> {
    let mut best: Option<Summary> = None;
    for session_dir in view.session_dirs(Some(cwd))? {
        let summary = match read_summary(&session_dir) {
            Ok(summary) => summary,
            Err(RelocationError::Json { .. }) => continue,
            Err(RelocationError::Io { source, .. }) if source.kind() == io::ErrorKind::NotFound => {
                continue;
            }
            Err(error) => return Err(error),
        };
        if summary.is_hidden() {
            continue;
        }
        if best.as_ref().is_none_or(|current| {
            let time = summary.last_active_at.unwrap_or(summary.updated_at);
            let current_time = current.last_active_at.unwrap_or(current.updated_at);
            time > current_time
                || (time == current_time && summary.info.id.0.as_ref() < current.info.id.0.as_ref())
        }) {
            best = Some(summary);
        }
    }
    Ok(best)
}

/// Sync, local-only session summaries for `cwd` (hidden sessions filtered).
/// For startup paths that must resolve a resume target before the
/// irreversible OS sandbox is applied; async callers use [`list_summaries`].
///
/// Listing failures propagate so pre-sandbox callers can fail closed;
/// individual unreadable summaries are skipped, matching the async path's
/// tolerance for a single corrupt file.
pub fn local_summaries_for_cwd_sync(cwd: &str) -> io::Result<Vec<Summary>> {
    local_summaries_for_cwd_sync_in_root(cwd, &grok_home().join("sessions"))
}

fn local_summaries_for_cwd_sync_in_root(
    cwd: &str,
    sessions_root: &Path,
) -> io::Result<Vec<Summary>> {
    let view = storage_view(sessions_root).map_err(io::Error::other)?;
    let dirs = view.session_dirs(Some(cwd)).map_err(io::Error::other)?;
    Ok(dirs
        .iter()
        .filter_map(|dir| read_summary_from_dir(dir).ok())
        .filter(|s| !s.is_hidden())
        .collect())
}

/// Best-effort lookup of the sandbox profile persisted with a session that is
/// about to be resumed, used at startup to restore the session's profile before
/// the (irreversible) OS sandbox is applied.
///
/// - `session_id`: the explicit id from `--resume <id>` / `--load <id>` /
///   `-s <id>`. Resolved directly across all cwds, then — for a remote id that
///   was restored into a local child — via that child's `parent_session_id`.
/// - `cwd`: the current working directory. Used to resolve a remote id to its
///   local child, and as the lookup key for `-c` / `--continue` and bare
///   `--resume` (most-recent-for-cwd).
///
/// Returns `None` when not resuming, the session isn't found locally, or it has
/// no persisted profile (sessions created before this was tracked) — callers
/// then fall back to the normal config/CLI resolution.
pub fn resumed_session_sandbox_profile(
    session_id: Option<&str>,
    cwd: Option<&str>,
) -> Option<String> {
    resumed_session_sandbox_profile_in_root(session_id, cwd, &grok_home().join("sessions"))
}

fn resumed_session_sandbox_profile_in_root(
    session_id: Option<&str>,
    cwd: Option<&str>,
    sessions_root: &Path,
) -> Option<String> {
    if let Some(id) = session_id.filter(|s| !s.is_empty()) {
        // Direct match by id (across all cwds).
        if let Some(summary) = find_summary_by_session_id_in_root(id, sessions_root) {
            return summary.sandbox_profile;
        }
        // A remote id resumes into a local child (fresh id, `parent_session_id`
        // = remote id). Mirror the canonical resume path so the peek doesn't
        // miss the restored session's saved profile.
        if let Some(cwd) = cwd
            && let Some(child) = find_local_child_for_remote_in_root(id, cwd, sessions_root)
        {
            return find_summary_by_session_id_in_root(&child, sessions_root)
                .and_then(|s| s.sandbox_profile);
        }
        return None;
    }
    if let Some(cwd) = cwd {
        return most_recent_local_summary_for_cwd_in_root(cwd, sessions_root)
            .and_then(|s| s.sandbox_profile);
    }
    None
}

fn is_zero(value: &u64) -> bool {
    *value == 0
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PendingCwdSwitchReminder {
    pub cwd_generation: u64,
    pub previous_cwd: String,
    #[serde(alias = "cwd")]
    pub destination_cwd: String,
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub destination_project_instructions: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Summary {
    pub info: Info,
    /// Monotonic generation of the authoritative cwd in `info.cwd`.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub cwd_generation: u64,
    /// Cwd immediately preceding the current generation.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_cwd: Option<String>,
    /// Reminder staged for exactly-once append during relocation completion.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pending_cwd_switch_reminder: Option<PendingCwdSwitchReminder>,
    /// Latest switch generation reflected in `num_chat_messages` bookkeeping.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub cwd_switch_bookkeeping_generation: u64,
    pub session_summary: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub num_messages: usize,
    #[serde(default)]
    pub num_chat_messages: usize,
    pub current_model_id: acp::ModelId,
    /// Parent session ID if this session was forked from another session
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent_session_id: Option<String>,
    /// Timestamp when this session was forked (only set for forked sessions)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub forked_at: Option<DateTime<Utc>>,
    /// Collection ID for telemetry trace uploads (one per session)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub collection_id: Option<String>,
    /// Next telemetry trace turn id (monotonic, persisted).
    /// Used to generate unique turn ids for telemetry metadata/filenames even across rewinds.
    #[serde(default)]
    pub next_trace_turn: u64,
    /// Chat history format version:
    /// - 0 (default): Legacy ChatRequestMessage format
    /// - 1: ConversationItem format
    #[serde(default)]
    pub chat_format_version: u8,
    /// Stable display path for forked sessions.
    ///
    /// When set, the system prompt's `Workspace Path` and prompt metadata
    /// paths show this value instead of the real worktree/overlay path
    /// (`info.cwd`). Persisted so the override survives session
    /// restore/reload without the caller needing to resend it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_display_cwd: Option<String>,
    /// What created this session: `"fork"`, `"subagent"`, `"subagent_fork"`, etc.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_kind: Option<String>,
    /// How the session's initial context was bootstrapped: `"new"` or `"forked"`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fork_context_source: Option<String>,
    /// The parent prompt/turn ID that triggered this fork.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fork_parent_prompt_id: Option<String>,
    /// Number of conversation items inherited from the parent session.
    /// During compaction, items below this index are preserved as-is
    /// (the "inherited prefix"). Only items after this boundary are
    /// summarized. `None` means no inherited prefix (non-forked session).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inherited_prefix_len: Option<usize>,
    /// Visibility override. None = default for `session_kind`, Some = explicit.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hidden: Option<bool>,
    /// The original workspace directory this worktree session was spawned from.
    /// Used by clients to group worktree sessions under their source workspace
    /// regardless of the worktree's actual `cwd`. Only set when
    /// `session_kind == "worktree"`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_workspace_dir: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub git_root_dir: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub git_remotes: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub head_commit: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub head_branch: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    /// Absolute path to the `.grok` directory, used by reconstruction.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grok_home: Option<String>,
    /// When the session last had content added (user or model messages).
    /// Only advanced locally by `append_update` / `append_chat_message`;
    /// never touched by remote registry operations or metadata-only writes.
    /// `None` for sessions created before this field was added.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_active_at: Option<DateTime<Utc>>,
    /// LLM-generated session title persisted separately from `session_summary`.
    /// When present, this is preferred for display over `session_summary`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub generated_title: Option<String>,
    /// True when `generated_title` was set by a manual `/rename` (vs auto LLM
    /// title). Manual titles render inline in the prompt's top border on
    /// resume.
    #[serde(default, skip_serializing_if = "is_false")]
    pub title_is_manual: bool,
    /// Human-readable label for the worktree directory (e.g. "nuke-v-tables").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worktree_label: Option<String>,
    /// The agent definition name that was active when the session was last saved.
    /// Used during session resume to avoid re-deriving from the (mutable) model
    /// catalog — if the model is removed or its `agent_type` changes between
    /// sessions, this persisted value ensures the correct harness is restored.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_name: Option<String>,
    /// The OS sandbox profile this session ran under (e.g. "workspace",
    /// "strict", "off", or a custom name). Persisted so a resumed session is
    /// restored to the same profile instead of silently falling back to the
    /// config default — which would otherwise break commands that worked before
    /// (a stricter profile denies filesystem/network the session relied on).
    /// `None` for sessions created before this field existed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sandbox_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<ReasoningEffort>,
    /// Ultra-short summary of the most recent successful turn, shown as the
    /// dashboard row's secondary line (via the roster for non-attached
    /// clients). Displayed until replaced by the next successful turn (or
    /// cleared by a conversation rewind).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_turn_summary: Option<String>,
    /// Prompt id of the turn `last_turn_summary` describes (provenance).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_turn_summary_prompt_id: Option<String>,
    /// Bounded preview of the most recent session recap ("where was I"),
    /// persisted so listing surfaces (`/resume`, `/session-info`) can show it
    /// whenever available. Distinct from `last_turn_summary` (a summary of the
    /// final turn only). Regenerated on demand by `/recap`; this holds the last
    /// committed value.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_recap: Option<String>,
}

/// Current `grok_home` as a UTF-8 string, or `None` if the path isn't valid UTF-8.
pub(crate) fn grok_home_string() -> Option<String> {
    crate::util::grok_home::grok_home()
        .to_str()
        .map(String::from)
}

impl Summary {
    pub(crate) fn new(info: &Info, model_id: acp::ModelId) -> std::io::Result<Self> {
        let git_metadata = pi_workspace::session::git::resolve_persisted_session_git_metadata_sync(
            std::path::Path::new(&info.cwd),
        );
        Ok(Self {
            info: info.clone(),
            cwd_generation: 0,
            previous_cwd: None,
            pending_cwd_switch_reminder: None,
            cwd_switch_bookkeeping_generation: 0,
            session_summary: String::new(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            num_messages: 0,
            num_chat_messages: 0,
            current_model_id: model_id,
            parent_session_id: None,
            forked_at: None,
            collection_id: None,
            next_trace_turn: 0,
            chat_format_version: CHAT_FORMAT_VERSION,
            prompt_display_cwd: None,
            session_kind: None,
            fork_context_source: None,
            fork_parent_prompt_id: None,
            inherited_prefix_len: None,
            hidden: None,
            source_workspace_dir: None,
            git_root_dir: git_metadata.git_root_dir,
            git_remotes: git_metadata.git_remotes,
            head_commit: git_metadata.head_commit,
            head_branch: git_metadata.head_branch,
            request_id: None,
            grok_home: grok_home_string(),
            last_active_at: None,
            generated_title: None,
            title_is_manual: false,
            worktree_label: crate::session::worktree::lookup_worktree_label(&info.cwd),
            agent_name: None,
            sandbox_profile: None,
            reasoning_effort: None,
            last_turn_summary: None,
            last_turn_summary_prompt_id: None,
            last_recap: None,
        })
    }

    /// Whether this session should be excluded from history listings.
    pub fn is_hidden(&self) -> bool {
        self.hidden.unwrap_or(
            self.session_kind
                .as_deref()
                .is_some_and(|k| k.starts_with("subagent")),
        )
    }

    /// Preferred display title: `generated_title` if non-empty, else `session_summary`.
    pub fn display_title(&self) -> &str {
        self.generated_title
            .as_deref()
            .map(|t| t.trim())
            .filter(|t| !t.is_empty())
            .unwrap_or(&self.session_summary)
    }

    /// [`Self::display_title`] as an `Option`, `None` when blank.
    pub fn display_title_opt(&self) -> Option<String> {
        let title = self.display_title().trim();
        (!title.is_empty()).then(|| title.to_string())
    }

    /// The manually-`/rename`d title (trimmed), `None` for auto-generated or
    /// blank titles. Binds to `generated_title` — the field `title_is_manual`
    /// describes — never the `session_summary` display fallback, so a stale
    /// flag over a blank manual title can't relabel an auto summary as
    /// manual. When `Some`, it equals [`Self::display_title_opt`] (a
    /// non-blank `generated_title` wins the display chain).
    pub fn manual_title_opt(&self) -> Option<String> {
        self.title_is_manual
            .then_some(self.generated_title.as_deref())
            .flatten()
            .map(str::trim)
            .filter(|t| !t.is_empty())
            .map(str::to_owned)
    }
}

#[cfg(test)]
#[path = "persistence_head_fields_tests.rs"]
mod head_fields_tests;

#[cfg(test)]
#[path = "persistence_generated_title_tests.rs"]
mod generated_title_tests;

/// List session summaries, optionally filtered by cwd (absolute path string).
/// Returns summaries sorted by `last_active_at` (else `updated_at`) descending.
fn recover_session_relocations_in(root: &Path) -> crate::session::storage::relocation::Result<()> {
    crate::session::storage::relocation::RelocationStorage::new(root.into()).recover_all()
}

pub async fn list_summaries(cwd: Option<&str>) -> io::Result<Vec<Summary>> {
    let root_dir = crate::util::grok_home::grok_home();
    let recovery_root = root_dir.clone();
    tokio::task::spawn_blocking(move || recover_session_relocations_in(&recovery_root))
        .await
        .map_err(io::Error::other)?
        .map_err(io::Error::other)?;
    let storage: Box<dyn StorageAdapter> = Box::new(JsonlStorageAdapter::with_root(root_dir));
    storage.list_sessions(cwd).await
}

/// List the `limit` most recently modified session summaries across all
/// workspaces. Uses stat-based mtime sorting to avoid reading every
/// summary file on disk; final order uses `last_active_at` else `updated_at`.
pub async fn list_recent_summaries(limit: usize) -> io::Result<Vec<Summary>> {
    let root_dir = crate::util::grok_home::grok_home();
    let recovery_root = root_dir.clone();
    tokio::task::spawn_blocking(move || recover_session_relocations_in(&recovery_root))
        .await
        .map_err(io::Error::other)?
        .map_err(io::Error::other)?;
    let storage = JsonlStorageAdapter::with_root(root_dir);
    storage.list_sessions_recent(limit).await
}

// Session folder TTL cleanup

#[cfg(test)]
#[path = "persistence_agent_name_persistence_tests.rs"]
mod agent_name_persistence_tests;

#[cfg(test)]
#[path = "persistence_session_exists_tests.rs"]
mod session_exists_tests;

#[cfg(test)]
#[path = "persistence_find_summary_by_session_id_tests.rs"]
mod find_summary_by_session_id_tests;

#[cfg(test)]
#[path = "persistence_resumed_sandbox_profile_tests.rs"]
mod resumed_sandbox_profile_tests;

#[cfg(test)]
#[path = "persistence_session_exists_for_cwd_tests.rs"]
mod session_exists_for_cwd_tests;

#[cfg(test)]
#[path = "persistence_find_local_child_tests.rs"]
mod find_local_child_tests;

#[cfg(test)]
#[path = "persistence_resolve_local_session_tests.rs"]
mod resolve_local_session_tests;

#[cfg(test)]
#[path = "persistence_repo_wide_resolution_tests.rs"]
mod repo_wide_resolution_tests;
