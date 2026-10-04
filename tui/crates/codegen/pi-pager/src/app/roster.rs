//! Mirror types for the leader "session roster" wire format.
//!
//! The leader process hosts session actors and exposes a roster API the
//! pager consumes in leader mode (FleetView dashboard):
//!
//! - Request/response `legacy ext RPC` → [`RosterListResponse`].
//! - Broadcast notification `legacy ext RPC` → [`RosterChanged`].
//!
//! These structs mirror the producer-side wire format (camelCase JSON,
//! snake_case activity enum). They are deserialize-only — the pager never
//! produces them.

use serde::Deserialize;

/// Coarse activity state for a roster entry. Wire format is snake_case.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RosterActivity {
    Working,
    Idle,
    NeedsInput,
    Dormant,
    Completed,
    Dead,
}

/// Origin of a roster entry (local leader vs. a remote host). We don't
/// render origin yet, but must parse it without failing.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RosterOrigin {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub host: Option<String>,
}

/// A single session in the leader roster.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RosterEntry {
    pub session_id: String,
    #[serde(default)]
    pub title: Option<String>,
    pub cwd: String,
    #[serde(default)]
    pub is_worktree: bool,
    #[serde(default)]
    pub model_id: Option<String>,
    #[serde(default)]
    pub yolo: bool,
    pub activity: RosterActivity,
    /// Ultra-short summary of the session's most recent turn, shown as the
    /// row's secondary line.
    #[serde(default)]
    pub last_turn_summary: Option<String>,
    #[serde(default)]
    pub resident: bool,
    #[serde(default)]
    pub last_change_unix_ms: i64,
    #[serde(default)]
    pub origin: RosterOrigin,
}

/// Response to `legacy ext RPC`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RosterListResponse {
    #[serde(default)]
    pub sessions: Vec<RosterEntry>,
}

/// Broadcast payload for `legacy ext RPC`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RosterChanged {
    #[serde(default)]
    pub upserted: Vec<RosterEntry>,
    #[serde(default)]
    pub removed: Vec<String>,
}

/// Parse an `legacy ext RPC` ext-response body into a [`RosterListResponse`].
///
/// The agent serializes the response through
/// `ExtMethodResult::success(..).to_ext_response()` (see
/// `pi-shell/src/agent/handlers/session.rs::handle_roster_list`), which
/// wraps the payload in a JSON-RPC-style `{ "result": { "sessions": [...] } }`
/// envelope. A bare `{ "sessions": [...] }` body (no envelope) is tolerated too.
///
/// We MUST unwrap `result` *first*: [`RosterListResponse::sessions`] is
/// `#[serde(default)]` and the struct does not deny unknown fields, so a direct
/// `serde_json::from_str::<RosterListResponse>` on the wrapped body would
/// silently *succeed* with an empty roster (it never finds a top-level
/// `sessions` key, so it defaults to `[]` and ignores the unknown `result`
/// key). That was the original bug: the poll returned an empty roster on every
/// tick and — because [`crate::app::actions::TaskResult::RosterLoaded`] replaces
/// `leader_roster` wholesale — also clobbered any entry delivered by the
/// `legacy ext RPC` broadcast. Mirrors how `Effect::FetchSessionList`
/// unwraps `result` for `legacy ext RPC`.
pub fn parse_roster_list_response(body: &str) -> Option<RosterListResponse> {
    let value: serde_json::Value = serde_json::from_str(body).ok()?;
    let payload = value.get("result").unwrap_or(&value);
    serde_json::from_value::<RosterListResponse>(payload.clone()).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A bare `{ "sessions": [...] }` body (no `result` envelope) must still
    /// parse — the parser tolerates both shapes.
    #[test]
    fn roster_list_response_parses_bare_body() {
        let body = r#"{"sessions":[{"sessionId":"s1","cwd":"/x","isWorktree":false,"yolo":false,"activity":"idle","resident":true,"lastChangeUnixMs":7,"origin":{"kind":"local"}}]}"#;
        let parsed = parse_roster_list_response(body).expect("bare body parses");
        assert_eq!(parsed.sessions.len(), 1);
        assert_eq!(parsed.sessions[0].session_id, "s1");
    }

}
