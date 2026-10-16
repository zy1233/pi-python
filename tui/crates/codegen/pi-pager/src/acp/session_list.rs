//! Walking `session/list`: the loop over `nextCursor` that every reader of the list shares.
//!
//! The resume picker (`effects::helpers::fetch_session_list`) and the startup lookup behind
//! `--continue`, `--resume` and `--session-id` (`app::session_resolve`) both ask the agent for
//! its sessions page after page. This is the one place that follows the cursors and decides when
//! to stop, so a misbehaving agent is handled in one way for both.

use std::collections::HashSet;
use std::ops::ControlFlow;
use std::path::Path;

use agent_client_protocol as acp;
use pi_acp_lib::{AcpAgentTx, acp_send};

/// How many `session/list` pages one walk follows. The Python agent's pages hold 50 sessions, so
/// this is 5000 of them: more than a picker can use, and a bound on an agent that never stops
/// handing out cursors.
pub(crate) const MAX_SESSION_LIST_PAGES: usize = 100;

/// How a walk ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WalkEnd {
    /// The last page was read: the agent sent no `nextCursor`.
    Done,
    /// The visitor had what it needed and asked to stop.
    Stopped,
    /// The walk was cut short (an empty or repeated cursor, or [`MAX_SESSION_LIST_PAGES`] pages):
    /// the sessions seen are not all there are.
    Cut,
}

/// Ask the agent for `session/list`, page after page, and hand each response to `visit`.
///
/// `cwd` filters by directory on the agent's side; `None` lists every directory. A response
/// without `nextCursor` is the last page (ACP). The cursor is opaque: it is only ever handed
/// back, never read. The walk ends early when `visit` breaks, on a cursor that is empty or that
/// the agent already gave (it would go round forever), and at [`MAX_SESSION_LIST_PAGES`]; the
/// last two give [`WalkEnd::Cut`] and a warning in the log. An error on any page is the error of
/// the whole walk: a list that silently stops half way would hide sessions.
pub(crate) async fn walk_session_list(
    tx: &AcpAgentTx,
    cwd: Option<&Path>,
    mut visit: impl FnMut(&acp::ListSessionsResponse) -> ControlFlow<()>,
) -> Result<WalkEnd, acp::Error> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut cursor: Option<String> = None;
    for _ in 0..MAX_SESSION_LIST_PAGES {
        let request = acp::ListSessionsRequest::default()
            .cwd(cwd.map(Path::to_path_buf))
            .cursor(cursor.take());
        let resp = acp_send(request, tx).await?;
        if visit(&resp).is_break() {
            return Ok(WalkEnd::Stopped);
        }
        match resp.next_cursor {
            None => return Ok(WalkEnd::Done),
            Some(next) if next.is_empty() || !seen.insert(next.clone()) => {
                tracing::warn!(
                    "session/list: the agent sent an empty or repeated nextCursor; \
                     the pages read so far are all that is used"
                );
                return Ok(WalkEnd::Cut);
            }
            Some(next) => cursor = Some(next),
        }
    }
    tracing::warn!(
        "session/list: stopped after {MAX_SESSION_LIST_PAGES} pages; \
         the pages read so far are all that is used"
    );
    Ok(WalkEnd::Cut)
}
