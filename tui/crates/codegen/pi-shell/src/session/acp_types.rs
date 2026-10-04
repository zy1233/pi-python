//! Public wire types (DTOs) for the ACP session actor.
//!
//! These are the request/response structs exchanged between the agent layer
//! and the session actor. They were extracted from `acp_session.rs` to keep
//! that file focused on behaviour while giving downstream crates a lightweight
//! import path for data types.

use crate::util::config::DEFAULT_AUTO_COMPACT_THRESHOLD_PERCENT;

// ── Session list ───────────────────────────────────────────────────────

// ── Compaction ──────────────────────────────────────────────────────────

// ── Feedback ────────────────────────────────────────────────────────────

// ── Rollout survey ──────────────────────────────────────────────────────

// ── Citations / comments ────────────────────────────────────────────────

// ── Rewind ──────────────────────────────────────────────────────────────

// ── Session info ────────────────────────────────────────────────────────

/// Itemized token usage for one context category, shown as an
/// informational row in `/context`, e.g. the skills listing or the
/// MCP server listing.
///
/// Token counts come from rendering the current state (the skill set, the
/// connected servers), never from parsing conversation text. Once
/// injected, these rows overlap [`ContextInfo::message_tokens`]; a fresh
/// session can show rows before the reminders are injected. Neither
/// estimate counts the `<system-reminder>` wrapper added on injection.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct TokenUsageCategory {
    /// Display label, e.g. `"Skills"` or `"MCP servers"`.
    pub label: String,
    /// Estimated tokens this category costs in context.
    pub tokens: u64,
    /// Short supporting detail. By convention a count followed by a
    /// noun, e.g. `"21 skills"`; the pager right-aligns the leading count
    /// across rows.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// Formats a count with a naively pluralized noun: `"1 skill"`, `"21 skills"`.
pub fn count_detail(count: u64, noun: &str) -> String {
    let suffix = if count == 1 { "" } else { "s" };
    format!("{count} {noun}{suffix}")
}

/// Context usage breakdown for session info.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct ContextInfo {
    pub used: u64,
    pub total: u64,
    pub system_prompt_tokens: u64,
    pub tool_definitions_count: u64,
    pub tool_definitions_tokens: u64,
    pub compaction_count: u64,
    pub turn_count: u64,
    pub tool_call_count: u64,
    /// Total conversation items (system + user + assistant + tool responses).
    pub message_count: u64,
    /// Bytes/4 estimate of all non-system conversation items.
    pub message_tokens: u64,
    pub free_tokens: u64,
    pub usage_pct: u8,
    /// The resolved auto-compact threshold percent (0-100) for the active model
    /// at the time this snapshot was captured. Comes from the 6-tier resolution
    /// (env > user per-model > user global > GB per-model > GB global > 85).
    /// Used by the TUI `/context` view so the displayed “Auto-compact at X%”
    /// always matches the actual trigger (e.g. 65 for grok-build in remote settings).
    #[serde(default = "default_auto_compact_threshold")]
    pub auto_compact_threshold_percent: u8,
    /// Itemized usage rows (skills listing, MCP server listing). Empty on
    /// partial snapshots.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub usage_categories: Vec<TokenUsageCategory>,
}

impl ContextInfo {
    /// Partial snapshot from a notification carrying only used + total.
    /// Breakdown fields default to zero until the next full ContextInfo update.
    pub fn from_notification(used: u64, total: u64) -> Self {
        Self {
            used,
            total,
            usage_pct: pi_token_estimation::usage_percentage_u8(used, total),
            free_tokens: pi_token_estimation::free_tokens(total, used),
            auto_compact_threshold_percent: DEFAULT_AUTO_COMPACT_THRESHOLD_PERCENT,
            ..Self::default()
        }
    }
}

/// Serde default for the new threshold field (keeps old snapshots / partials
/// deserializing without error and gives the historical default of 85).
fn default_auto_compact_threshold() -> u8 {
    DEFAULT_AUTO_COMPACT_THRESHOLD_PERCENT
}

/// Unified session info data returned by GetSessionInfo.
/// One query, all the fields needed for /session-info and /context.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionInfoData {
    /// Agent definition name for this session (e.g. `grok-build`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_name: Option<String>,
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_display_name: Option<String>,
    pub resolved_model_id: Option<String>,
    pub model_fingerprint: Option<String>,
    /// Catalog opt-in to display the served-checkpoint fingerprint for this model.
    #[serde(default)]
    pub show_model_fingerprint: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_backend: Option<String>,
    /// Gateway chat conversation id when this session is gateway-proxied.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<String>,
    pub turns: u64,
    /// Current turn (0-based).
    /// Matches the `turn_number` used in TurnStarted events, traces, and rewinds.
    #[serde(default)]
    pub turn_index: u64,
    pub context: ContextInfo,
}

/// Whether this model slug supports showing checkpoint identity (resolved model ID, fingerprint).
pub(crate) fn is_coding_model_slug(model: &str) -> bool {
    matches!(model, "grok-build" | "grok-4.5")
}

/// Display gate for the model fingerprint: server/catalog opt-in OR the built-in coding-slug default.
pub fn should_show_model_fingerprint(catalog_flag: bool, model_slug: &str) -> bool {
    catalog_flag || is_coding_model_slug(model_slug)
}

/// Calculate and format the model name for display.
pub fn model_display_name(
    name: Option<&str>,
    model: &str,
    resolved: Option<&str>,
    show_resolved: bool,
) -> String {
    // If the catalogue entry has a name, that's the displayed model.
    if let Some(n) = name {
        return n.to_string();
    }

    // For displaying the resolved model slug from the API response.
    if show_resolved {
        return match resolved.filter(|r| *r != model) {
            Some(r) => format!("{model} ({r})"),
            None => model.to_string(),
        };
    }

    // There's no resolved model slug, we display the request model slug.
    model.to_string()
}

/// Full wire response for `x.ai/session/info`.
///
/// Wraps `SessionInfoData` with session-level fields (`session_id`, `cwd`)
/// that come from the agent layer rather than the session actor.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionInfoResponse {
    pub session_id: String,
    pub cwd: String,
    #[serde(flatten)]
    pub data: SessionInfoData,
}

// ── Feedback context ────────────────────────────────────────────────────

// ── Startup hints ───────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn should_show_model_fingerprint_truth_table() {
        // Catalog opt-in shows the fingerprint even for a non-coding slug.
        assert!(should_show_model_fingerprint(true, "non-coding"));
        // Coding slugs always show, even without the catalog flag.
        assert!(should_show_model_fingerprint(false, "grok-build"));
        assert!(should_show_model_fingerprint(false, "grok-4.5"));
        // Non-coding slug without the flag stays hidden.
        assert!(!should_show_model_fingerprint(false, "some-other"));
    }

    

    // ── RewindMode serialization ──────────────────────────────────────

    // ── RewindRequest backwards compatibility ─────────────────────────

    // ── RewindResponse fields ─────────────────────────────────────────

    // ── RewindPointInfo.has_file_changes ──────────────────────────────

    #[test]
    fn context_info_from_notification_computes_derived_fields() {
        let c = ContextInfo::from_notification(50_000, 200_000);
        assert_eq!(c.used, 50_000);
        assert_eq!(c.total, 200_000);
        assert_eq!(c.usage_pct, 25);
        assert_eq!(c.free_tokens, 150_000);
        assert_eq!(c.system_prompt_tokens, 0);
        assert_eq!(c.message_count, 0);
        assert_eq!(c.compaction_count, 0);
    }

    #[test]
    fn context_info_from_notification_zero_total() {
        let c = ContextInfo::from_notification(100, 0);
        assert_eq!(c.usage_pct, 0);
        assert_eq!(c.free_tokens, 0);
    }

}
