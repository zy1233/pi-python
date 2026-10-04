//! Row model for sessions the dashboard lists without a live agent view.
//!
//! Today the only producer is the local on-disk session list
//! (`Effect::FetchDashboardSessions`, see `app::effects::helpers`), whose
//! entries are always [`RosterActivity::Dormant`]. The `Roster*` names and the
//! richer activity states are inherited from the removed shared-leader session
//! roster; they are plain in-process types now, with no wire format.

/// Coarse activity state of a listed session.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RosterActivity {
    Working,
    Idle,
    NeedsInput,
    Dormant,
    Completed,
    Dead,
}

/// Where a listed session lives (`kind`, plus the host for a remote one).
/// Not rendered yet.
#[derive(Debug, Clone, Default)]
pub struct RosterOrigin {
    pub kind: String,
    pub host: Option<String>,
}

/// A single listed session.
#[derive(Debug, Clone)]
pub struct RosterEntry {
    pub session_id: String,
    pub title: Option<String>,
    pub cwd: String,
    pub is_worktree: bool,
    pub model_id: Option<String>,
    pub yolo: bool,
    pub activity: RosterActivity,
    /// Ultra-short summary of the session's most recent turn, shown as the
    /// row's secondary line.
    pub last_turn_summary: Option<String>,
    pub resident: bool,
    pub last_change_unix_ms: i64,
    pub origin: RosterOrigin,
}
