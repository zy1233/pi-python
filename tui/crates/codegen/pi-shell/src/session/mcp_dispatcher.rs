//! Session-actor side `StatusDispatcher` for MCP client events.
//!
//! Receives [`pi_mcp::servers::McpClientEvent`]s emitted by:
//! - per-client transport-liveness watchers
//!   ([`pi_mcp::liveness`]),
//! - the [`pi_mcp::servers::GrokClientHandler`] (server-pushed
//!   `tools/list_changed` and `resources/list_changed`),
//! - the `ensure_initialized` success/failure path,
//! - the session MCP config diff path (`UpdateMcpServers` / toggle).
//!
//! Coalesces events in a **50 ms tumbling window** keyed by
//! `(server_name, McpClientEventKind)`. Two events with the same key
//! collapse into the latest one — e.g. an MCP server bursting 100
//! `tools/list_changed` notifications inside 10 ms produces exactly
//! one ACP push.
//!
//! Each surviving entry is emitted as an ACP
//! [`agent_client_protocol::ExtNotification`] with method
//! `x.ai/mcp/server_status` and the payload schema defined by
//! [`McpServerStatusPayload`].
//!
//! ## Doc-comment ↔ implementation contract
//!
//! - Coalescing window is exactly 50 ms, tumbling — events received
//!   during the window are buffered, then flushed on the next tick.
//! - Per `(server, kind)` collapse: the *latest* event wins (events
//!   inserted into a `HashMap` are overwritten by later inserts).
//! - `ConfigDiff` is fanned out per-server, **not** stored as a
//!   single event in the buffer.
//! - The bounded auto-restart task wires in: after a
//!   window flush, the dispatcher hands off each `TransportClosed` /
//!   `HandshakeFailed` key to
//!   [`crate::session::mcp_restart::maybe_schedule_restart`], which
//!   applies the stdio-only / shutting-down / configured-and-enabled
//!   guard rails before spawning
//!   [`crate::session::mcp_restart::auto_restart_stdio`]. The
//!   dispatcher itself stays single-purpose: coalesce + push.

use serde::{Deserialize, Serialize};

use crate::extensions::mcp::{ McpServerSource};

/// Method name for the ACP push.
pub const SERVER_STATUS_METHOD: &str = "x.ai/mcp/server_status";

/// JSON payload pushed over ACP. Fields written in camelCase per ACP
/// convention.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct McpServerStatusPayload {
    /// Owning session id.
    pub session_id: String,
    /// MCP server name (`managed_gateway:linear`, `github`, ...).
    pub name: String,
    /// `managed` for gateway catalog ids (`managed_gateway:*`), else `local`.
    pub source: McpServerSource,
    /// Current status — see [`McpServerStatus`].
    pub status: McpServerStatus,
    /// What drove the status change. See [`McpServerStatusReason`].
    pub reason: McpServerStatusReason,
    /// Optional human-readable detail. Surfaces the full handshake /
    /// transport error reason to the UI verbatim — no sanitization or
    /// truncation — so failures are easy to debug.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    /// Reserved for future use; always `null` today; may fill
    /// this with the post-restart tool list so the client can
    /// re-render without a follow-up `mcp/list` round-trip.
    pub tools: Option<serde_json::Value>,
}

/// Status enum surfaced to the wire. Lowercase serialization to
/// match the existing pager `McpSessionStatus` family.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum McpServerStatus {
    /// Client is in [`pi_mcp::servers::ClientStateKind::Ready`]
    /// and the transport is healthy.
    Ready,
    /// Per-server handshake is in flight, or a restart is being
    /// debounced.
    Initializing,
    /// Transport closed, handshake failed, or the server is
    /// disabled/unconfigured.
    Unavailable,
    /// OAuth required but not yet acquired.
    NeedsAuth,
}

/// Reason a status delta was emitted. Lowercase + snake_case
/// serialization to keep the wire schema stable.
///
/// `RestartSucceeded` / `RestartFailed` are reserved for the
/// auto-restart path. `Initialized` is emitted for the first-time
/// `Ready` transition out of `ensure_initialized` — distinguishing
/// a brand-new handshake from a successful re-handshake (`Ready →
/// restart_succeeded` was the wire before).
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum McpServerStatusReason {
    TransportClosed,
    HandshakeFailed,
    ConfigAdded,
    ConfigRemoved,
    ConfigChanged,
    Disabled,
    AuthExpired,
    /// First-time successful handshake (a new server transitioned
    /// from `Initializing` → `Ready`). Every
    /// `McpClientEvent::Ready` maps to this reason.
    Initialized,
    /// A watcher fired `TransportClosed`, the
    /// auto-restart path re-handshook, and the new handshake
    /// succeeded.
    RestartSucceeded,
    /// The auto-restart path exhausted retries.
    RestartFailed,
    /// Old leaders still emit this after reactive reauth. Not produced anymore.
    ManagedTokenRefreshed,
}

#[cfg(test)]
mod tests {
    use super::*;
    

    #[test]
    fn managed_token_refreshed_still_deserializes() {
        let reason: McpServerStatusReason =
            serde_json::from_str("\"managed_token_refreshed\"").unwrap();
        assert_eq!(reason, McpServerStatusReason::ManagedTokenRefreshed);
    }

    // ── Integration test: end-to-end run_dispatcher
    //    with restart_actions wired. Pre-fix, `flush_window` marked
    //    `shutting_down` on every `TransportClosed`, which then
    //    short-circuited `maybe_schedule_restart` and caused
    //    auto-restart to never fire in production. This test drives a
    //    real `run_dispatcher` task end-to-end and — critically —
    //    wires the production `SharedShutdownState`
    //    into the test mock's `is_in_shutting_down` so the dispatcher
    //    ↔ actions binding is genuinely exercised. Pre-fix flush
    //    semantics would mark `"svr"` in the shared state, the mock
    //    would observe it, and the test would FAIL — closing the
    //    real regression loop.

}
