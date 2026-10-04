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
    /// Live credentials backed by an `AuthManager`. `resolve_async()`
    /// drives memory -> disk -> OIDC refresh; `resolve()` reads the
    /// in-memory cache for sync contexts.
    pub(crate) fn with_auth_manager(mut self, am: Arc<crate::auth::AuthManager>) -> Self {
        self.auth_manager = Some(am);
        self
    }
    /// Return a reference to the internal `AuthManager`, if any.
    pub(crate) fn auth_manager(&self) -> Option<&Arc<crate::auth::AuthManager>> {
        self.auth_manager.as_ref()
    }
    /// Error hint for 401 responses, based on which credential was sent.
    pub(crate) fn auth_error_hint(&self) -> &'static str {
        if self.deployment_key.is_some() {
            "Your GROK_DEPLOYMENT_KEY is invalid or expired. Please contact a team admin."
        } else if self.user_token.is_some() {
            "Your auth token is invalid or expired. Run `grok login` to re-authenticate."
        } else {
            "Not authenticated."
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
mod tests {
    
    
    
    
}
