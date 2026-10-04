//! MCP extension methods and business logic.
//!
//! - `x.ai/mcp/list` — list available MCP servers (agent-scoped or session-annotated)
//! - `x.ai/mcp/call` — invoke an MCP tool directly, outside the LLM loop
//! - `x.ai/mcp/servers_updated` — local/plugin catalog after launch-dir discovery
//!   or a folder-trust grant (not gateway connectors)
//! - `x.ai/mcp/server_status` — per-server delta pushed by the
//!   `StatusDispatcher` (transport-closed pollers, handshake failures,
//!   config diffs, server-pushed list-changed notifications). See
//!   [`crate::session::mcp_dispatcher`] for the coalescing /
//!   payload-shaping logic. Re-exported below so other crates have a
//!   single import point.

use serde::Deserialize;
use serde::Serialize;
// Re-export the `x.ai/mcp/server_status` schema +
// method constant from the dispatcher module so external callers
// have a single import point alongside the other `x.ai/mcp/*`
// types.
//
// The canonical definitions still live in
// [`crate::session::mcp_dispatcher`] because their primary consumer
// is the dispatcher loop (and the unit tests there). The
// `session → extensions` direction is the inverse of the typical
// `extensions → session` flow, but moving the types here would
// require either making the dispatcher import from `extensions`
// (same inversion) or duplicating the schema. Leaving the
// re-export here keeps the single import-point ergonomic without
// duplicating definitions.
pub use crate::session::mcp_dispatcher::{
    McpServerStatus, McpServerStatusPayload, McpServerStatusReason, SERVER_STATUS_METHOD,
};

fn default_true() -> bool {
    true
}

/// MCP server config for the `mcp/list` catalog response.
///
/// Distinct from `acp::McpServer` (session/new input) because:
/// - HTTP: exposes `scope`/`scope_id`/`scope_name` for connector selection, NOT headers (auth tokens stay private)
/// - Stdio: same structure but optimized for JSON wire format
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum McpServerConfig {
    #[serde(rename = "http")]
    Http {
        url: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        scope: Option<String>,
        #[serde(rename = "scopeId", skip_serializing_if = "Option::is_none")]
        scope_id: Option<String>,
        #[serde(rename = "scopeName", skip_serializing_if = "Option::is_none")]
        scope_name: Option<String>,
    },
    #[serde(rename = "stdio")]
    Stdio {
        command: std::path::PathBuf,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        args: Vec<String>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        env: Vec<McpEnvVar>,
    },
    #[serde(rename = "managedGateway")]
    ManagedGateway,
}

#[derive(Debug, Clone, Serialize)]
pub struct McpEnvVar {
    pub name: String,
    pub value: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum McpServerSource {
    Managed,
    Local,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct McpToolEntry {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(rename = "_meta", default, skip_serializing_if = "Option::is_none")]
    pub meta: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub icons: Vec<pi_mcp::servers::McpIcon>,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

// ── Internal types (not serialized to wire) ─────────────────────────

// ── Notification: mcp/servers_updated ────────────────────────────────

