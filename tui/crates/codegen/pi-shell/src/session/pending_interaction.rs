//! Per-session pending-interaction registry.
//!
//! Permissions, `ask_user_question`, and plan approval are **blocking ACP
//! reverse-requests**: the agent parks a tool-loop future on an in-memory
//! oneshot and waits for the driver to answer. While such a request is open we
//! record it here, keyed by `tool_call_id` (stable, lives in the transcript →
//! survives reconnect). This registry is the single source of truth for "what
//! is pending right now" and is read by the roster to surface
//! [`crate::agent::roster::RosterActivity::NeedsInput`].
//!
//! Pending interactions are **requests, not notifications** — they are never
//! persisted. We broadcast `pending_interaction` / `interaction_resolved`
//! **fire-and-forget** via the gateway (same idiom as
//! [`crate::session::summary`]); the routing layer fans them to every
//! subscriber because they carry a `sessionId`.

/// Which kind of blocking reverse-request is pending.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PendingKind {
    /// `request_permission` for a tool action.
    Permission,
    /// `x.ai/ask_user_question`.
    Question,
    /// `x.ai/exit_plan_mode` plan approval.
    PlanApproval,
    McpElicitation,
}

#[cfg(test)]
mod tests {}
