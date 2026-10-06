use agent_client_protocol as acp;

/// Env var that, when set, advertises `pi.api_key` as a viable auth method.
///
/// Kept as a constant so test code and the production check stay in sync.
pub(crate) const PI_API_KEY_ENV_VAR: &str = "PI_API_KEY";

/// Legacy env var name. Checked as a fallback when `PI_API_KEY` is not set,
/// so existing deployments that use the old name keep working.
pub(crate) const LEGACY_PI_API_KEY_ENV_VAR: &str = "GROK_CODE_PI_API_KEY";

/// Read the API key from the environment.
///
/// Checks `PI_API_KEY` first, then falls back to the legacy
/// `GROK_CODE_PI_API_KEY` for backward compatibility.
pub(crate) fn read_pi_api_key_env() -> Result<String, std::env::VarError> {
    std::env::var(PI_API_KEY_ENV_VAR).or_else(|_| std::env::var(LEGACY_PI_API_KEY_ENV_VAR))
}

/// Returns `true` if either `PI_API_KEY` or `GROK_CODE_PI_API_KEY` is set.
pub fn has_pi_api_key_env() -> bool {
    read_pi_api_key_env().is_ok()
}

/// ACP session auth method. Use `is_session_based_method` for classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthMethodKind {
    PiApiKey,
    CachedToken,
    GrokCom,
    Oidc,
    Unknown,
}

impl AuthMethodKind {
    pub fn from_id(id: &acp::AuthMethodId) -> Self {
        match id.0.as_ref() {
            PI_API_KEY_METHOD_ID => Self::PiApiKey,
            CACHED_TOKEN_AUTH_METHOD_ID => Self::CachedToken,
            GROK_COM_METHOD_ID => Self::GrokCom,
            OIDC_METHOD_ID => Self::Oidc,
            _ => Self::Unknown,
        }
    }

    /// Requires user interaction (browser, OIDC redirect, or external auth command).
    pub fn needs_interactive_login(self) -> bool {
        matches!(self, Self::GrokCom | Self::Oidc)
    }
}

/// Error when `preferred_method=api_key` but no key/BYOK credentials exist.
pub const PREFERRED_API_KEY_UNAVAILABLE: &str = "preferred_method=api_key but no API key is configured (set PI_API_KEY or model api_key/env_key in config.toml).";

pub const PI_API_KEY_METHOD_ID: &str = "pi.api_key";

pub(crate) const CACHED_TOKEN_AUTH_METHOD_ID: &str = "cached_token";

pub const GROK_COM_METHOD_ID: &str = "grok.com";

pub(crate) const OIDC_METHOD_ID: &str = "oidc";

#[cfg(test)]
mod tests {
    use super::*;

    use serial_test::serial;

    use pi_test_support::EnvGuard;

    // ── Helpers ─────────────────────────────────────────────────────────

    // build_auth_methods regression: pin production call-site ordering.
    // Reordering so `pi.api_key` is after login methods must fail the tests below.

    // ── End-to-end: enterprise TOML -> resolved models -> build_auth_methods ─

    /// When both `PI_API_KEY` and `GROK_CODE_PI_API_KEY` are set,
    /// the new name takes precedence.
    #[test]
    #[serial]
    fn new_env_var_takes_precedence_over_legacy() {
        let _new = EnvGuard::set(PI_API_KEY_ENV_VAR, "new-key");
        let _legacy = EnvGuard::set(LEGACY_PI_API_KEY_ENV_VAR, "old-key");
        assert_eq!(read_pi_api_key_env().unwrap(), "new-key");
    }

    // -- grok login --legacy regression coverage ------------------------
    //
    // `grok login --legacy` produces a GrokAuth with `auth_mode: WebLogin`,
    // `oidc_issuer: None`, and no `expires_at` (30-day hardcoded TTL).
    // When this token is present via the `GROK_AUTH` env var (or via legacy
    // scope fallback in auth.json), `AuthManager::new` returns it from
    // `current()`, feeding `has_cached_token = true` into `build_auth_methods`.
    // This puts `cached_token` first so `startup_auth_metadata()` returns
    // `needs_login = false` -- legacy users get frictionless auth, no login
    // screen.
    //
    // This test pins the env-var path (highest priority in AuthManager) end-
    // to-end. A regression in GROK_AUTH JSON parsing or in auth method
    // ordering would send legacy-token users to the login screen.

    // ── preferred_method pin (fail-closed) ──────────────────────────────
}
