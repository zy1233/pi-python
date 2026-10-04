//! Sampling error types.
//!
//! The canonical error types now live in `pi_sampling_types::error`.
//! This module re-exports them and adds `map_sampling_err_to_acp` which
//! depends on `agent_client_protocol::Error` (a grok-shell dependency).

// Re-export everything from the standalone crate.

use agent_client_protocol as acp;

/// ACP error code for rate-limited requests (HTTP 429).
/// Uses the JSON-RPC implementation-defined server error range (-32000 to -32099).
///
/// Contract: set only for actual HTTP 429 responses from the sampling client.
/// Clients derive user-facing text via [`format_rate_limited_user_message`].
/// The desktop path is unchanged: `prompt_complete_fields` still reports the
/// stop reason with no detail.
pub const RATE_LIMITED_ERROR_CODE: i32 = -32003;

/// OAuth / session rate-limit copy (personal plan upgrade path).
pub const RATE_LIMITED_USER_MESSAGE_OAUTH: &str =
    "You\u{2019}ve hit the rate limit for your plan. Upgrade your account or try again later.";

/// API key / team rate-limit copy. Personal grok.com upgrades do not raise API
/// team limits; admins purchase credits or a higher spend-based tier.
/// See https://docs.x.ai/developers/rate-limits#rate-limit-tiers
pub const RATE_LIMITED_USER_MESSAGE_API_KEY: &str = "You\u{2019}ve hit your team\u{2019}s API rate limit. Ask a team admin to purchase more credits for higher limits, or try again later. See https://docs.x.ai/developers/rate-limits#rate-limit-tiers";

/// Well-known free-usage exhaustion code CCP returns on HTTP 429.
/// Matches `prod_util_well_known_errors::SUBSCRIPTION_FREE_USAGE_EXHAUSTED`.
/// sampling-types' `parse_error_bytes` prepends the flat `code` to the
/// flattened message, so this reaches clients embedded in error detail.
pub(crate) const FREE_USAGE_EXHAUSTED_ERROR_CODE: &str = "subscription:free-usage-exhausted";

/// User-facing free-usage exhaustion copy (paywall). Deliberately promises no
/// reset duration — the quota window is backend-config-driven.
pub const FREE_USAGE_USER_MESSAGE: &str = "You\u{2019}ve reached your free Grok Build usage limit for now. Get SuperGrok for much higher limits, or try again later: https://grok.com/supergrok?referrer=grok-build";

/// Whether flattened server detail is free-usage-quota exhaustion (paywall),
/// not transient throttling. Sniffs the well-known code embedded by
/// `parse_error_bytes`.
pub fn is_free_usage_exhausted_error(detail: &str) -> bool {
    detail.contains(FREE_USAGE_EXHAUSTED_ERROR_CODE)
}

/// User-facing text for an ACP -32003 rate-limit error.
///
/// Free-usage code first (consumer-only; intentional before API-key rewrite).
/// API-key + personal SuperGrok upsell → team credits copy. Else the body
/// after stripping `API error (status …):` (SamplingError Display prefix).
/// Empty → OAuth vs API-key fallback. Callers that show this in UI should
/// still run their usual sanitizer (scrub/cap).
pub fn format_rate_limited_user_message(
    server_detail: Option<&str>,
    is_api_key_auth: bool,
) -> String {
    // Free-usage sniff works on the prefixed wire string (`contains` the code).
    if server_detail.is_some_and(is_free_usage_exhausted_error) {
        return FREE_USAGE_USER_MESSAGE.to_string();
    }
    if let Some(detail) = server_detail.map(str::trim).filter(|s| !s.is_empty()) {
        let detail = strip_sampling_api_error_prefix(detail);
        if is_api_key_auth && pushes_consumer_subscription_upsell(detail) {
            return RATE_LIMITED_USER_MESSAGE_API_KEY.to_string();
        }
        return detail.to_string();
    }
    if is_api_key_auth {
        RATE_LIMITED_USER_MESSAGE_API_KEY
    } else {
        RATE_LIMITED_USER_MESSAGE_OAUTH
    }
    .to_string()
}

/// Drop `SamplingError::Api`'s Display prefix so users see the IC body, not
/// `API error (status 429 Too Many Requests): …`.
fn strip_sampling_api_error_prefix(detail: &str) -> &str {
    const PREFIX: &str = "API error (status ";
    const SEP: &str = "): ";
    if let Some(rest) = detail.strip_prefix(PREFIX)
        && let Some(idx) = rest.find(SEP)
    {
        return rest[idx + SEP.len()..].trim();
    }
    detail.trim()
}

/// IC sometimes reuses OAuth free-tier upsell copy on 429s ("upgrade to a Grok
/// subscription" / grok.com/supergrok). That is wrong for API-key / team auth:
/// higher limits come from credits and spend-based rate-limit tiers, not a
/// personal SuperGrok plan.
fn pushes_consumer_subscription_upsell(detail: &str) -> bool {
    let d = detail.to_ascii_lowercase();
    d.contains("grok.com/supergrok") || d.contains("upgrade to a grok subscription")
}

pub fn error_detail_from_data(data: &serde_json::Value) -> Option<String> {
    if let Some(m) = data.get("message").and_then(|v| v.as_str()) {
        return Some(m.to_owned());
    }
    if let Some(s) = data.as_str() {
        return Some(s.to_owned());
    }
    data.get("detail")
        .and_then(|v| v.as_str())
        .map(str::to_owned)
}

pub fn http_status_from_error(err: &acp::Error) -> Option<u16> {
    err.data
        .as_ref()?
        .get("http_status")?
        .as_u64()
        .map(|s| s as u16)
}

#[cfg(test)]
mod tests {
    use super::*;
    

    #[test]
    fn rate_limited_fallback_oauth_vs_api_key() {
        assert_eq!(
            format_rate_limited_user_message(None, false),
            RATE_LIMITED_USER_MESSAGE_OAUTH
        );
        assert_eq!(
            format_rate_limited_user_message(None, true),
            RATE_LIMITED_USER_MESSAGE_API_KEY
        );
        assert!(RATE_LIMITED_USER_MESSAGE_OAUTH.contains("Upgrade your account"));
        assert!(RATE_LIMITED_USER_MESSAGE_API_KEY.contains("team"));
        assert!(RATE_LIMITED_USER_MESSAGE_API_KEY.contains("credits"));
        assert!(
            RATE_LIMITED_USER_MESSAGE_API_KEY
                .contains("https://docs.x.ai/developers/rate-limits#rate-limit-tiers")
        );
        assert!(!RATE_LIMITED_USER_MESSAGE_API_KEY.contains("Upgrade your account"));
    }

    #[test]
    fn format_rate_limited_surfaces_nonempty_server_detail() {
        let body = "The service is temporarily at capacity. Please retry your request shortly.";
        // Production detail is SamplingError::Api Display (prefixed).
        let wire = format!("API error (status 429 Too Many Requests): {body}");
        assert_eq!(format_rate_limited_user_message(Some(&wire), false), body);
        assert_eq!(format_rate_limited_user_message(Some(&wire), true), body);

        // Team console rate-limit copy has no personal SuperGrok upsell — surface as-is.
        let team = "resource-exhausted: Too many requests for team abc. See https://console.x.ai/team/default/rate-limits.";
        let team_wire = format!("API error (status 429 Too Many Requests): {team}");
        assert_eq!(
            format_rate_limited_user_message(Some(&team_wire), true),
            team
        );
        assert_eq!(
            format_rate_limited_user_message(Some("slow down"), false),
            "slow down"
        );
    }

    #[test]
    fn format_rate_limited_api_key_rewrites_consumer_subscription_upsell() {
        let body = "Some resource has been exhausted: You are sending requests too quickly. \
             Please slow down, or upgrade to a Grok subscription for higher limits: \
             https://grok.com/supergrok";
        let wire = format!("API error (status 429 Too Many Requests): {body}");
        // OAuth keeps the IC body (personal plan upgrade is correct).
        assert_eq!(format_rate_limited_user_message(Some(&wire), false), body);
        // API key must not push grok.com SuperGrok — team credits / rate-limit tiers.
        assert_eq!(
            format_rate_limited_user_message(Some(&wire), true),
            RATE_LIMITED_USER_MESSAGE_API_KEY
        );
    }

    #[test]
    fn format_rate_limited_strips_api_error_display_prefix() {
        let body = "The service is temporarily at capacity.";
        let wire = format!("API error (status 429 Too Many Requests): {body}");
        assert_eq!(format_rate_limited_user_message(Some(&wire), false), body);
        assert!(!format_rate_limited_user_message(Some(&wire), false).contains("API error"));
    }

    #[test]
    fn is_free_usage_exhausted_error_sniffs_well_known_code() {
        assert!(is_free_usage_exhausted_error(
            "subscription:free-usage-exhausted: You have used all your free usage."
        ));
        assert!(is_free_usage_exhausted_error(
            "API error (status 429): subscription:free-usage-exhausted quota hit"
        ));
        assert!(!is_free_usage_exhausted_error("throttled"));
        assert!(!is_free_usage_exhausted_error(
            "The service is temporarily at capacity."
        ));
    }

    #[test]
    fn format_rate_limited_free_usage_uses_paywall_copy() {
        let wire = "API error (status 429 Too Many Requests): \
            subscription:free-usage-exhausted: You have used all your free usage.";
        assert_eq!(
            format_rate_limited_user_message(Some(wire), false),
            FREE_USAGE_USER_MESSAGE
        );
        // Free-usage code is consumer-only; still wins for API-key callers.
        assert_eq!(
            format_rate_limited_user_message(Some(wire), true),
            FREE_USAGE_USER_MESSAGE
        );
    }

    #[test]
    fn format_rate_limited_empty_detail_uses_auth_aware_fallback() {
        assert_eq!(
            format_rate_limited_user_message(None, false),
            RATE_LIMITED_USER_MESSAGE_OAUTH
        );
        assert_eq!(
            format_rate_limited_user_message(Some(""), false),
            RATE_LIMITED_USER_MESSAGE_OAUTH
        );
        assert_eq!(
            format_rate_limited_user_message(None, true),
            RATE_LIMITED_USER_MESSAGE_API_KEY
        );
        assert_eq!(
            format_rate_limited_user_message(Some("   "), true),
            RATE_LIMITED_USER_MESSAGE_API_KEY
        );
    }

}
