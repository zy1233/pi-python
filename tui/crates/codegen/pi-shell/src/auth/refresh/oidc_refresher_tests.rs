//! Unit tests for [`super::oidc_refresher::OidcRefresher`]. Extracted
//! from `oidc_refresher.rs` so the implementation reads top-to-bottom;
//! wired in via `#[path = "oidc_refresher_tests.rs"] mod tests;`.

use super::*;
use crate::auth::GrokAuth;

// ── OIDC refresh E2E with mock IdP ─────────────────────────────────

// ── Near-expiry (5-minute buffer) refresh scenarios ──────────────

// The standalone `try_refresh_session_token` helper that previously
// lived in this module was removed when refresh was centralized in
// `AuthManager`. Its call sites now go through `AuthManager::auth()`
// / `AuthManager::unauthorized_recovery()`, both of which have their
// own coverage in `manager.rs`. The historical regression tests for
// the helper (`try_refresh_respects_auth_type`,
// `auth_type_must_be_session_token_after_session_key_set`) were
// dropped along with the function. The `resolve_credentials`
// invariant they also pinned remains covered by
// `agent::config::tests::{resolve_credentials_sets_auth_type,
// resolve_credentials_no_session_key_returns_api_key}`.

// ── Disk-token retry on invalid_grant ──────────────────────

// ── Sleep-gate E2E (real OidcRefresher + mock IdP) ─────────────────

// ── Transient-blip budget is per-credential ─────────────────────────

/// Minimal `AuthSnapshot` for exercising `record_transient_failure` in
/// isolation (it never reads credential state).
struct EmptySnapshot;
impl AuthSnapshot for EmptySnapshot {
    fn current(&self) -> Option<GrokAuth> {
        None
    }
    fn expired_auth(&self) -> Option<GrokAuth> {
        None
    }
    fn read_disk_auth(&self) -> Option<GrokAuth> {
        None
    }
    fn is_expired(&self) -> bool {
        false
    }
}

/// A fresh credential (e.g. after re-login on this long-lived refresher) must
/// get the full blip budget instead of inheriting a dead credential's count,
/// so a valid token is never escalated to a permanent failure early.
#[test]
fn transient_blip_budget_is_scoped_to_the_credential() {
    let refresher = OidcRefresher::new(Arc::new(EmptySnapshot));
    let key_a = Some("cred-a".to_owned());

    // Accrue blips up to just under the escalation threshold on credential A.
    for _ in 0..MAX_CONSECUTIVE_TRANSIENT_FAILURES - 1 {
        assert!(matches!(
            refresher.record_transient_failure("blip".into(), key_a.clone(), false),
            RefreshOutcome::TransientFailure { .. }
        ));
    }

    // Credential B's first blip must stay transient, not escalate to permanent.
    assert!(
        matches!(
            refresher.record_transient_failure("blip".into(), Some("cred-b".to_owned()), false),
            RefreshOutcome::TransientFailure { .. }
        ),
        "a fresh credential must not inherit a prior credential's blip count",
    );
}

/// Network-unreachable failures (DNS/connect/timeout — the post-wake offline
/// window) must never consume the escalation budget: no amount of them may
/// produce a `PermanentFailure` ("Run `grok login`") verdict, because they
/// prove nothing about the credential. Counted failures accrued before or
/// after are unaffected (the budget is neither consumed nor reset).
#[test]
fn network_unreachable_blips_never_escalate() {
    let refresher = OidcRefresher::new(Arc::new(EmptySnapshot));
    let key = Some("cred-a".to_owned());

    // Far more unreachable blips than the budget: all stay transient.
    for _ in 0..MAX_CONSECUTIVE_TRANSIENT_FAILURES * 3 {
        assert!(
            matches!(
                refresher.record_transient_failure("wifi down".into(), key.clone(), true),
                RefreshOutcome::TransientFailure { .. }
            ),
            "a network-unreachable failure must never escalate to permanent",
        );
    }

    // The budget was not consumed: counted (IdP-reaching) blips still get the
    // full threshold before escalating.
    for _ in 0..MAX_CONSECUTIVE_TRANSIENT_FAILURES - 1 {
        assert!(matches!(
            refresher.record_transient_failure("5xx".into(), key.clone(), false),
            RefreshOutcome::TransientFailure { .. }
        ));
    }
    assert!(
        matches!(
            refresher.record_transient_failure("5xx".into(), key.clone(), false),
            RefreshOutcome::PermanentFailure { .. }
        ),
        "counted blips must still escalate at the threshold",
    );
}
