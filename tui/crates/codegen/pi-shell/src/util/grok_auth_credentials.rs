use reqwest::RequestBuilder;
use std::sync::Arc;
/// Credentials for authenticating with grok backend services.
///
/// Two construction modes:
/// - `with_auth_manager(am)` — live mode. `resolve_async()` drives
///   `AuthManager::get_valid_token()` (memory -> disk -> OIDC refresh).
/// - `new(token)` — static mode. For one-shot callers that don't have
///   an `AuthManager` (visibility checks, bundle fetches, tests).
///
/// Deployment key (enterprise) sends bare `Bearer`, routed to management key auth.
/// User token (pi users) sends `Bearer` + `X-PI-Token-Auth: pi-cli`.
/// Deployment key takes precedence when both are present.
#[derive(Clone)]
pub(crate) struct GrokAuthCredentials {
    pub user_token: Option<String>,
    pub deployment_key: Option<String>,
    pub alpha_test_key: Option<String>,
    /// Live auth source. When set, `resolve_async()` drives the full
    /// refresh chain; `resolve()` reads the in-memory cache.
    auth_manager: Option<Arc<crate::auth::AuthManager>>,
}
impl std::fmt::Debug for GrokAuthCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GrokAuthCredentials")
            .field(
                "user_token",
                &self.user_token.as_ref().map(|_| "<redacted>"),
            )
            .field(
                "deployment_key",
                &self.deployment_key.as_ref().map(|_| "<redacted>"),
            )
            .field(
                "mode",
                &if self.auth_manager.is_some() {
                    "live"
                } else {
                    "static"
                },
            )
            .finish()
    }
}
impl GrokAuthCredentials {
    /// Static credentials from a snapshot token. No refresh capability.
    pub fn new(user_token: Option<String>) -> Self {
        Self {
            user_token,
            deployment_key: None,
            alpha_test_key: None,
            auth_manager: None,
        }
    }
    pub(crate) fn apply(&self, builder: RequestBuilder, base_url: &str) -> RequestBuilder {
        let builder = if let Some(ref key) = self.deployment_key {
            builder.header("Authorization", format!("Bearer {}", key))
        } else if let Some(ref token) = self.user_token {
            builder
                .header("Authorization", format!("Bearer {}", token))
                .header(
                    obfstr::obfstr!("X-PI-Token-Auth"),
                    obfstr::obfstr!("pi-cli"),
                )
        } else {
            builder
        };
        let _ = base_url;
        builder
    }
}
impl pi_auth::HttpAuth for GrokAuthCredentials {
    fn apply(&self, builder: RequestBuilder, base_url: &str) -> RequestBuilder {
        GrokAuthCredentials::apply(self, builder, base_url)
    }
}
#[cfg(test)]
mod tests {}
