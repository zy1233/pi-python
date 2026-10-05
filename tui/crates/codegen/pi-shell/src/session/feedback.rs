//! Feedback request heuristics for Grok Code sessions.
//!
//! This module implements the feedback request decision logic based on session signals.
//! It uses tiered probability sampling to request feedback at appropriate moments
//! without overwhelming users.

use serde::{Deserialize, Serialize};

// Re-export shared feedback API wire types to avoid duplication
pub(crate) use prod_mc_cli_chat_proxy_types::feedback_types::FeedbackMode;

/// Feedback request tier with associated probability and criteria.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum FeedbackTier {
    /// Tier 1: Standard engagement (0.05% sample rate)
    /// Triggered after sustained engagement without issues
    Tier1,
    /// Tier 2: Complex session (0.02% sample rate)
    /// Triggered after complex sessions with some friction
    Tier2,
    /// Tier 3: Recovery/completion (0.01% sample rate)
    /// Triggered after recovery from issues or session end
    Tier3,
}

/// Describes the specific condition that triggered a feedback request.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TriggerCondition {
    /// Tier that was triggered
    pub tier: FeedbackTier,
    /// Specific condition that was met (e.g., "turns >= 10 AND tool_calls >= 5 AND compactions >= 2 AND cancellations == 0")
    pub condition: String,
    /// Actual signal values at trigger time
    pub signal_snapshot: TriggerSignalSnapshot,
}

/// Snapshot of signal values at the time feedback was triggered.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TriggerSignalSnapshot {
    pub turn_count: u32,
    pub tool_calls_count: u32,
    pub compactions_count: u32,
    pub errors_count: u32,
    pub cancellations_count: u32,
    pub has_reverted: bool,
}

impl TriggerCondition {
    /// Get a human-readable trigger reason.
    pub(crate) fn trigger_reason(&self) -> String {
        let snapshot = &self.signal_snapshot;
        match self.tier {
            FeedbackTier::Tier1 => format!(
                "Tier 1: Sustained engagement (turns={}, tools={}, compactions={}, no cancellations)",
                snapshot.turn_count, snapshot.tool_calls_count, snapshot.compactions_count
            ),
            FeedbackTier::Tier2 => format!(
                "Tier 2: Complex session with errors (turns={}, tools={}, compactions={}, errors={})",
                snapshot.turn_count,
                snapshot.tool_calls_count,
                snapshot.compactions_count,
                snapshot.errors_count
            ),
            FeedbackTier::Tier3 => format!(
                "Tier 3: Recovery from friction (turns={}, cancellations={}, reverted={})",
                snapshot.turn_count, snapshot.cancellations_count, snapshot.has_reverted
            ),
        }
    }
}

/// A feedback request to be sent to the client.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct FeedbackRequest {
    /// Unique ID for this feedback request
    pub request_id: String,
    /// The session this request is for
    pub session_id: String,
    /// The tier that triggered this request
    pub tier: FeedbackTier,
    /// What kind of feedback to collect
    pub feedback_mode: FeedbackMode,
    pub stars: bool,
    pub thumbs: bool,
    pub text: bool,
    /// Human-readable prompt to show the user
    pub prompt: String,
    /// Whether this is a non-intrusive/dismissible request
    pub dismissible: bool,
    /// Trigger type identifier (e.g., "tier1_engagement", "tier2_complex_recovery")
    pub trigger_type: String,
    /// The specific condition that triggered this request (includes actual signal values)
    pub trigger_condition: TriggerCondition,
    /// Additional context for the client
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<serde_json::Value>,
}

#[cfg(test)]
mod tests {}
