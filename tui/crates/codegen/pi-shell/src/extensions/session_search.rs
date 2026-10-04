//! ACP extension handler for session search (`x.ai/session/search`).
//!
//! Exposes session full-text search as an ACP extension method.
//! The client sends a query and receives ranked results across all
//! (or workspace-filtered) past sessions.
//!
//! ```text
//! JSON-RPC -> mvp_agent.ext_method()
//!          -> session_search::handle()
//!          -> storage::search::execute_search()
//!          -> search_fts::SessionSearchIndex (SQLite FTS5)
//! ```

use serde::Deserialize;
use serde::Serialize;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SearchSessionHit {
    pub session_id: String,
    pub cwd: String,
    /// Session title/summary for display
    pub summary: String,
    /// RFC 3339 formatted updated_at
    pub updated_at: String,
    pub score: f32,
    pub matched_fields: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub snippet: Option<String>,
}
