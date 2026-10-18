//! Which session do `--continue`, `--resume` and `--session-id` mean? Ask the agent.
//!
//! The pager used to answer this by reading the session files of the old Rust agent. It does not
//! read session files any more (ADR1, execution log sections 10.11 and 10.16): the agent owns
//! them, and `session/list` is how an ACP client sees them. This module reads that list (page
//! after page, see [`crate::acp::session_list`]) once the agent is up and before the event loop
//! starts, so a session that does not exist is an error with exit status 1 and not a failure
//! inside the terminal UI.
//!
//! | flag | what is read | what is looked for |
//! |------|--------------|--------------------|
//! | `--continue`, `--resume` | the sessions of the current directory | the latest activity (`updatedAt`) |
//! | `--resume <id>` | the sessions of every directory | the id; the session's own directory goes to `session/load` |
//! | `--resume <title>` | the sessions of the current directory | the one session with that title |
//! | `--session-id <id>` | the sessions of every directory | nothing: the id must be free |

use std::ops::ControlFlow;
use std::path::{Path, PathBuf};

use agent_client_protocol as acp;
use chrono::{DateTime, Utc};
use pi_acp_lib::AcpAgentTx;

use super::session_startup::{MaterializedStartup, SessionStartupIntent, require_uuid_session_id};
use super::session_title_resolve::{is_uuid_shaped, select_listed_by_title, title_miss_hint};
use crate::acp::session_list::{MAX_SESSION_LIST_PAGES, WalkEnd, walk_session_list};

/// One row of `session/list`, reduced to what the startup lookup uses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ListedSession {
    pub(crate) id: String,
    pub(crate) cwd: PathBuf,
    /// Trimmed. `None` when the agent sent none, or only blanks.
    pub(crate) title: Option<String>,
    /// The agent's last-activity time. `None` when it sent none, or one that does not parse.
    pub(crate) updated_at: Option<DateTime<Utc>>,
}

impl From<&acp::SessionInfo> for ListedSession {
    fn from(info: &acp::SessionInfo) -> Self {
        Self {
            id: info.session_id.0.to_string(),
            cwd: info.cwd.clone(),
            title: info
                .title
                .as_deref()
                .map(str::trim)
                .filter(|title| !title.is_empty())
                .map(str::to_owned),
            updated_at: info
                .updated_at
                .as_deref()
                .and_then(|stamp| stamp.parse().ok()),
        }
    }
}

/// The session with the latest activity.
///
/// A row without a time ranks below every row with one. On equal times the earlier row wins:
/// the agent lists the newest-created session first.
pub(crate) fn most_recent(rows: &[ListedSession]) -> Option<&ListedSession> {
    let mut best: Option<&ListedSession> = None;
    for row in rows {
        match best {
            Some(kept) if row.updated_at <= kept.updated_at => {}
            _ => best = Some(row),
        }
    }
    best
}

/// Whether `listed` is the session id `wanted` names. UUIDs compare as UUIDs (letter case and
/// hyphens do not matter); any other id compares as text.
fn same_id(wanted: &str, listed: &str) -> bool {
    match (uuid::Uuid::try_parse(wanted), uuid::Uuid::try_parse(listed)) {
        (Ok(wanted), Ok(listed)) => wanted == listed,
        _ => wanted == listed,
    }
}

/// The directory to load a session in, when it is not the one zypi runs in. `None` means "here",
/// which is also what a row of the resume picker does, and what an agent that sent no directory
/// gets.
fn original_cwd(listed: &Path, here: &Path) -> Option<PathBuf> {
    if listed.as_os_str().is_empty() || listed == here {
        None
    } else {
        Some(listed.to_path_buf())
    }
}

fn resume(session: &ListedSession, here: &Path) -> MaterializedStartup {
    MaterializedStartup::Resume {
        session_id: session.id.clone(),
        original_cwd: original_cwd(&session.cwd, here),
        title: session.title.clone(),
        // The two flags below belong to a session restored from a remote store, which no ACP
        // agent offers; a session the agent lists is always here.
        deferred_local_miss: false,
        suppress_code_restore: false,
    }
}

/// Every session of `cwd`, or of every directory when `cwd` is `None`.
async fn list_all(
    tx: &AcpAgentTx,
    cwd: Option<&Path>,
) -> Result<(Vec<ListedSession>, WalkEnd), acp::Error> {
    let mut rows = Vec::new();
    let end = walk_session_list(tx, cwd, |page| {
        rows.extend(page.sessions.iter().map(ListedSession::from));
        ControlFlow::Continue(())
    })
    .await?;
    Ok((rows, end))
}

/// The session with this id, in any directory: pages are read until it shows up.
async fn find_by_id(
    tx: &AcpAgentTx,
    id: &str,
) -> Result<(Option<ListedSession>, WalkEnd), acp::Error> {
    let mut found = None;
    let end = walk_session_list(tx, None, |page| {
        match page
            .sessions
            .iter()
            .find(|info| same_id(id, &info.session_id.0))
        {
            Some(info) => {
                found = Some(ListedSession::from(info));
                ControlFlow::Break(())
            }
            None => ControlFlow::Continue(()),
        }
    })
    .await?;
    Ok((found, end))
}

fn list_error(error: acp::Error) -> anyhow::Error {
    anyhow::anyhow!("could not list the sessions of the agent: {error}")
}

/// What to add to a "not found" when the list may not have been read to its end.
fn cut_note(end: WalkEnd) -> String {
    if end == WalkEnd::Cut {
        format!(
            " (the list of the agent was cut short, after at most {MAX_SESSION_LIST_PAGES} \
             pages, so the session may be in the part that was not read)"
        )
    } else {
        String::new()
    }
}

/// Turn the session flags into what the event loop does first, by asking the agent over ACP.
///
/// `cwd` is the directory zypi runs in. Fails, with a message for the user, when the flags name a
/// session that does not exist, a title that fits more than one session, or an id that is not
/// free.
pub async fn resolve_startup(
    tx: &AcpAgentTx,
    intent: SessionStartupIntent,
    cwd: &Path,
) -> anyhow::Result<MaterializedStartup> {
    let started = std::time::Instant::now();
    let resolved = match intent {
        SessionStartupIntent::NewAuto => return Ok(MaterializedStartup::NewAuto),
        SessionStartupIntent::NewWithId { session_id } => {
            require_uuid_session_id(&session_id)?;
            match find_by_id(tx, &session_id).await {
                Ok((Some(_), _)) => {
                    anyhow::bail!("Session ID {session_id} is already in use.")
                }
                Ok((None, _)) => {}
                // An agent that cannot list sessions cannot say that the id is taken. The check
                // is never stricter than the agent: `session/new` is where it refuses a duplicate.
                Err(error) if error.code == acp::ErrorCode::MethodNotFound => {
                    tracing::warn!(
                        %error,
                        "session/list is not offered; the --session-id check is left to the agent"
                    );
                }
                Err(error) => return Err(list_error(error)),
            }
            MaterializedStartup::NewWithId { session_id }
        }
        SessionStartupIntent::Resume {
            session_id: None,
            most_recent_for_cwd: true,
        } => {
            let (rows, _end) = list_all(tx, Some(cwd)).await.map_err(list_error)?;
            let Some(latest) = most_recent(&rows) else {
                anyhow::bail!(
                    "No session found for current directory. \
                     Use 'zypi' to start a new session."
                );
            };
            resume(latest, cwd)
        }
        SessionStartupIntent::Resume {
            session_id: Some(arg),
            ..
        } if is_uuid_shaped(&arg) => {
            let (found, end) = find_by_id(tx, &arg).await.map_err(list_error)?;
            let Some(session) = found else {
                anyhow::bail!("Session does not exist: {arg}{}", cut_note(end));
            };
            resume(&session, cwd)
        }
        SessionStartupIntent::Resume {
            session_id: Some(arg),
            ..
        } => {
            let (rows, end) = list_all(tx, Some(cwd)).await.map_err(list_error)?;
            let Some(session) = select_listed_by_title(&arg, &rows)? else {
                anyhow::bail!(
                    "Session does not exist: {}{}",
                    title_miss_hint(&arg),
                    cut_note(end)
                );
            };
            tracing::info!(session_id = %session.id, "Session resolved by title");
            resume(session, cwd)
        }
        SessionStartupIntent::Resume {
            session_id: None,
            most_recent_for_cwd: false,
        } => {
            anyhow::bail!("internal: invalid session startup intent (unreachable from CLI flags)")
        }
    };
    tracing::info!(
        elapsed_ms = started.elapsed().as_millis() as u64,
        "startup.session.resolve"
    );
    Ok(resolved)
}

#[cfg(test)]
#[path = "session_resolve_tests.rs"]
mod tests;
