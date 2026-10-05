//! Pure OIDC protocol mechanics: PKCE, discovery, token exchange,
//! refresh_tokens, JWT validation, principal extraction.
//!
//! No `AuthManager` mutation here. The login orchestration is in
//! [`super::login`]; refresh primitives are in [`super::refresh`].
use super::super::config::{ForceLoginTeam, GrokComConfig};
use super::super::{AuthMode, GrokAuth};
use chrono::{Duration, Utc};
use parking_lot::RwLock;
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::LazyLock;
use std::time::{Duration as StdDuration, Instant};
#[derive(Debug, Clone, thiserror::Error)]
pub(super) enum OidcError {
    #[error("OIDC discovery failed: HTTP {status} from {url}")]
    DiscoveryHttp { status: u16, url: String },
    #[error("OIDC token refresh failed: HTTP {status} — {body}")]
    TokenRefreshHttp { status: u16, body: String },
    #[error(
        "This deployment requires logging into {expected}; your login returned {}",
        actual.as_deref().unwrap_or("no team principal")
    )]
    PinnedPrincipalMismatch {
        /// Pre-formatted requirement, e.g. `team <id>` or `one of teams: a, b`.
        expected: String,
        actual: Option<String>,
    },
    #[error(
        "Login is blocked by your administrator: force_login_team_uuid is an empty \
         list, so no team is permitted to sign in"
    )]
    ForceLoginNoPrincipalsAllowed,
}
/// Optionally attach an extra access header when the optional non-production
/// feature is enabled and the request targets a matching first-party host.
pub(crate) fn with_alpha_test_key(
    builder: reqwest::RequestBuilder,
    url: &str,
) -> reqwest::RequestBuilder {
    let _ = url;
    builder
}
/// Extract just the `principal_id` claim for `force_login_team_uuid` matching,
/// regardless of whether `principal_type` is present. A token can carry the
/// team id in `principal_id` without a `principal_type`; the pin must still
/// match it. Matching the id alone is safe because a user id never collides
/// with a team uuid (distinct id spaces), and the server re-validates the
/// signed token anyway. Returns `None` only when no non-empty `principal_id`
/// is present (which `enforce_login_principal` treats as fail-closed).
pub(crate) fn peek_access_token_principal_id(access_token: &str) -> Option<String> {
    #[derive(serde::Deserialize)]
    struct PrincipalIdClaim {
        #[serde(default, alias = "principalId")]
        principal_id: Option<String>,
    }
    jsonwebtoken::dangerous::insecure_decode::<PrincipalIdClaim>(access_token)
        .ok()?
        .claims
        .principal_id
        .filter(|s| !s.is_empty())
}
/// Resolved allowed-team set from the dedicated `force_login_team_uuid` lockdown
/// knob, or `None` (unrestricted). The legacy `oauth2.principal_id` is
/// intentionally NOT an enforcement gate — it only pre-selects the team on the
/// consent page — so deployments that set it for pre-selection keep letting
/// users pick a team (no surprise login failures on upgrade). Pure for testing.
pub(crate) fn resolve_login_principal_policy(
    force_login_team_uuid: Option<&ForceLoginTeam>,
) -> Option<ForceLoginTeam> {
    force_login_team_uuid.cloned()
}
pub(crate) fn login_principal_policy(cfg: &GrokComConfig) -> Option<ForceLoginTeam> {
    resolve_login_principal_policy(cfg.force_login_team_uuid.as_ref())
}
/// Reject a token whose principal isn't allowed, BEFORE persisting (no partial
/// state). A restriction also rejects a token with no principal (else picking
/// "personal" on the consent page defeats it); an empty `AnyOf` fails closed.
///
/// The `actual` principal comes from the access-token claim
/// (`peek_access_token_principal`, an unverified `insecure_decode`). This
/// client-side check is fail-fast UX / defense-in-depth — NOT the security
/// boundary: the server re-validates the signed token on every API call and
/// is authoritative, so a locally tampered token still cannot reach the API.
pub(crate) fn enforce_login_principal(
    policy: Option<&ForceLoginTeam>,
    actual: Option<&str>,
) -> anyhow::Result<()> {
    let allowed: &[String] = match policy {
        None => return Ok(()),
        Some(ForceLoginTeam::Single(id)) => std::slice::from_ref(id),
        Some(ForceLoginTeam::AnyOf(ids)) if ids.is_empty() => {
            tracing::warn!("OIDC: force_login_team_uuid is an empty list; failing closed");
            return Err(anyhow::Error::new(OidcError::ForceLoginNoPrincipalsAllowed));
        }
        Some(ForceLoginTeam::AnyOf(ids)) => ids,
    };
    if let Some(actual) = actual
        && allowed.iter().any(|a| a == actual)
    {
        return Ok(());
    }
    let expected = if allowed.len() == 1 {
        format!("team {}", allowed[0])
    } else {
        format!("one of teams: {}", allowed.join(", "))
    };
    tracing::warn!(
        expected = %expected,
        actual = ?actual,
        "OIDC: login principal does not satisfy required policy; rejecting"
    );
    Err(anyhow::Error::new(OidcError::PinnedPrincipalMismatch {
        expected,
        actual: actual.map(str::to_owned),
    }))
}
#[derive(Debug)]
pub(super) struct OidcUserInfo {
    pub(super) user_id: String,
    pub(super) email: Option<String>,
    pub(super) first_name: Option<String>,
    pub(super) last_name: Option<String>,
    pub(super) profile_image_asset_id: Option<String>,
    pub(super) principal_type: Option<String>,
    pub(super) principal_id: Option<String>,
    pub(super) team_id: Option<String>,
    pub(super) team_name: Option<String>,
    pub(super) team_role: Option<String>,
    pub(super) organization_id: Option<String>,
    pub(super) organization_name: Option<String>,
    pub(super) organization_role: Option<String>,
    pub(super) user_blocked_reason: Option<String>,
    pub(super) team_blocked_reasons: Vec<String>,
    pub(super) coding_data_retention_opt_out: bool,
}
pub(super) fn build_grok_auth(
    tokens: TokenResponse,
    user_info: OidcUserInfo,
    issuer: &str,
    client_id: &str,
) -> GrokAuth {
    let now = Utc::now();
    GrokAuth {
        key: tokens.access_token,
        auth_mode: AuthMode::Oidc,
        create_time: now,
        user_id: user_info.user_id,
        email: user_info.email,
        first_name: user_info.first_name,
        last_name: user_info.last_name,
        profile_image_asset_id: user_info.profile_image_asset_id,
        principal_type: user_info.principal_type,
        principal_id: user_info.principal_id,
        team_id: user_info.team_id,
        team_name: user_info.team_name,
        team_role: user_info.team_role,
        organization_id: user_info.organization_id,
        organization_name: user_info.organization_name,
        organization_role: user_info.organization_role,
        user_blocked_reason: user_info.user_blocked_reason,
        team_blocked_reasons: user_info.team_blocked_reasons,
        coding_data_retention_opt_out: user_info.coding_data_retention_opt_out,
        has_grok_code_access: None,
        refresh_token: tokens.refresh_token,
        expires_at: tokens.expires_in.map(|s| now + Duration::seconds(s as i64)),
        oidc_issuer: Some(issuer.to_owned()),
        oidc_client_id: Some(client_id.to_owned()),
    }
}
#[derive(Debug, Clone, Deserialize)]
pub(super) struct Discovery {
    pub(super) authorization_endpoint: String,
    pub(super) token_endpoint: String,
    #[serde(default)]
    pub(super) jwks_uri: Option<String>,
    #[serde(default)]
    pub(super) id_token_signing_alg_values_supported: Option<Vec<String>>,
}
/// RFC 8414 says discovery clients SHOULD cache. 1h is short enough
/// that an endpoint move propagates within an agent session, long
/// enough that a discovery-endpoint outage no longer blocks token
/// refresh once the doc is cached.
const DISCOVERY_CACHE_TTL: StdDuration = StdDuration::from_secs(3600);
/// Per-issuer cache of `(Discovery, fetched_at)`. Process-global
/// because the discovery doc is identity-free; multiple AuthManagers
/// pointed at the same IdP share one entry.
static DISCOVERY_CACHE: LazyLock<RwLock<HashMap<String, (Discovery, Instant)>>> =
    LazyLock::new(|| RwLock::new(HashMap::new()));
pub(super) async fn discover(issuer: &str) -> anyhow::Result<Discovery> {
    let issuer_key = issuer.trim_end_matches('/').to_owned();
    if let Some((doc, at)) = DISCOVERY_CACHE.read().get(&issuer_key)
        && at.elapsed() < DISCOVERY_CACHE_TTL
    {
        return Ok(doc.clone());
    }
    use backon::Retryable;
    let key = issuer_key.clone();
    let doc = (|| {
        let key = key.clone();
        async move { discover_once(&key).await }
    })
    .retry(discovery_retry_policy())
    .await?;
    DISCOVERY_CACHE
        .write()
        .insert(issuer_key, (doc.clone(), Instant::now()));
    Ok(doc)
}
fn discovery_retry_policy() -> backon::ExponentialBuilder {
    backon::ExponentialBuilder::default()
        .with_max_times(2)
        .with_min_delay(StdDuration::from_millis(500))
        .with_max_delay(StdDuration::from_secs(2))
        .with_jitter()
}
async fn discover_once(issuer_key: &str) -> anyhow::Result<Discovery> {
    let url = format!("{issuer_key}/.well-known/openid-configuration");
    tracing::debug!(url = %url, "OIDC: fetching discovery document");
    let resp = with_alpha_test_key(
        crate::http::shared_client()
            .get(&url)
            .timeout(StdDuration::from_secs(10)),
        &url,
    )
    .send()
    .await?;
    if !resp.status().is_success() {
        return Err(anyhow::Error::new(OidcError::DiscoveryHttp {
            status: resp.status().as_u16(),
            url,
        }));
    }
    let doc: Discovery = resp.json().await?;
    tracing::debug!(
        authorization_endpoint = %doc.authorization_endpoint,
        token_endpoint = %doc.token_endpoint,
        jwks_uri = ?doc.jwks_uri,
        id_token_algs = ?doc.id_token_signing_alg_values_supported,
        "OIDC: discovery complete"
    );
    Ok(doc)
}
#[cfg(test)]
pub(super) fn clear_discovery_cache() {
    DISCOVERY_CACHE.write().clear();
}
#[derive(Debug, Deserialize)]
pub(super) struct TokenResponse {
    pub(super) access_token: String,
    #[serde(default)]
    pub(super) refresh_token: Option<String>,
    #[serde(default)]
    pub(super) expires_in: Option<u64>,
}
/// Retry gate for `refresh_tokens`. Defers to `classify_terminal` (the single
/// source of truth): only a recognized terminal code (`invalid_grant`,
/// `invalid_client`) stops retries. Everything else (5xx, 429, bare 4xx, or an
/// unrecognized/RFC-transient code) is retried.
fn is_transient_refresh_error(err: &anyhow::Error) -> bool {
    let Some(OidcError::TokenRefreshHttp { status, body }) = err.downcast_ref::<OidcError>() else {
        return true;
    };
    if *status >= 500 || *status == 429 {
        return true;
    }
    let error_code = serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("error")?.as_str().map(str::to_owned));
    error_code
        .as_deref()
        .and_then(super::refresh::classify_terminal)
        .is_none()
}
/// Up to 3 attempts (1 + 2 retries), 200ms-2s jittered exponential
/// backoff. Bounded so a hard outage still surfaces to the user
/// promptly via the existing `RefreshOutcome::TransientFailure` path.
fn refresh_retry_policy() -> backon::ExponentialBuilder {
    backon::ExponentialBuilder::default()
        .with_max_times(2)
        .with_min_delay(StdDuration::from_millis(200))
        .with_max_delay(StdDuration::from_secs(2))
        .with_jitter()
}
pub(super) async fn refresh_tokens(
    token_endpoint: &str,
    refresh_token: &str,
    client_id: &str,
    principal_type: Option<&str>,
    principal_id: Option<&str>,
) -> anyhow::Result<TokenResponse> {
    use backon::Retryable;
    tracing::debug!(
        token_endpoint = %token_endpoint,
        principal_type = ?principal_type,
        principal_id = ?principal_id,
        "OIDC: refreshing token"
    );
    let probe = super::refresh::SuspendProbe::start();
    (|| {
        refresh_tokens_once(
            token_endpoint,
            refresh_token,
            client_id,
            principal_type,
            principal_id,
        )
    })
    .retry(refresh_retry_policy())
    .when(move |err: &anyhow::Error| {
        if !is_transient_refresh_error(err) {
            return false;
        }
        if probe.straddled_past_grace() {
            crate::unified_log::warn(
                "auth.refresh.retry_suppressed_suspend",
                None,
                Some(serde_json::json!({
                    "suspended_ms": probe.suspended_ms(),
                    "error": err.to_string(),
                })),
            );
            return false;
        }
        true
    })
    .await
}
/// One unretried POST to `token_endpoint`. Errors carry the typed
/// `OidcError::TokenRefreshHttp` so the retry classifier can read the
/// status code and OAuth2 `error` field without re-parsing.
async fn refresh_tokens_once(
    token_endpoint: &str,
    refresh_token: &str,
    client_id: &str,
    principal_type: Option<&str>,
    principal_id: Option<&str>,
) -> anyhow::Result<TokenResponse> {
    let mut params = vec![
        ("grant_type", "refresh_token"),
        ("refresh_token", refresh_token),
        ("client_id", client_id),
    ];
    if let Some(pt) = principal_type {
        params.push(("principal_type", pt));
    }
    if let Some(pid) = principal_id {
        params.push(("principal_id", pid));
    }
    let resp = with_alpha_test_key(
        crate::http::shared_client()
            .post(token_endpoint)
            .form(&params)
            .timeout(StdDuration::from_secs(15)),
        token_endpoint,
    )
    .send()
    .await?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        let error_code = serde_json::from_str::<serde_json::Value>(&body)
            .ok()
            .and_then(|v| v.get("error")?.as_str().map(str::to_owned));
        tracing::warn!(
            http_status = status,
            oauth2_error = ?error_code,
            rt_prefix = pi_auth::bearer_suffix(refresh_token),
            client_id = %client_id,
            principal_type = ?principal_type,
            "OIDC: token refresh HTTP error"
        );
        return Err(anyhow::Error::new(OidcError::TokenRefreshHttp {
            status,
            body,
        }));
    }
    Ok(resp.json().await?)
}
#[cfg(test)]
mod tests {
    
    use super::*;
    /// Enforcement matrix: None passes; Single/AnyOf require a match (and reject
    /// a no-principal token); empty AnyOf fails closed.
    #[test]
    fn enforce_login_principal_matrix() {
        assert!(enforce_login_principal(None, None).is_ok());
        assert!(enforce_login_principal(None, Some("team-abc")).is_ok());
        let single = ForceLoginTeam::Single("team-abc".into());
        assert!(enforce_login_principal(Some(&single), Some("team-abc")).is_ok());
        let err = enforce_login_principal(Some(&single), Some("team-other")).unwrap_err();
        assert_eq!(
            err.to_string(),
            "This deployment requires logging into team team-abc; \
             your login returned team-other",
        );
        let err = enforce_login_principal(Some(&single), None).unwrap_err();
        assert_eq!(
            err.to_string(),
            "This deployment requires logging into team team-abc; \
             your login returned no team principal",
        );
        let any_of = ForceLoginTeam::AnyOf(vec!["team-a".into(), "team-b".into()]);
        assert!(enforce_login_principal(Some(&any_of), Some("team-b")).is_ok());
        let err = enforce_login_principal(Some(&any_of), Some("team-c")).unwrap_err();
        assert_eq!(
            err.to_string(),
            "This deployment requires logging into one of teams: team-a, team-b; \
             your login returned team-c",
        );
        let err = enforce_login_principal(Some(&ForceLoginTeam::AnyOf(vec![])), Some("team-a"))
            .unwrap_err();
        assert_eq!(
            err.to_string(),
            "Login is blocked by your administrator: force_login_team_uuid is an empty \
             list, so no team is permitted to sign in",
        );
    }
    /// Only the dedicated `force_login_team_uuid` knob produces an enforcement
    /// policy; the legacy `oauth2.principal_id` is pre-select-only and never an
    /// enforcement gate (regression guard for the upgrade-behavior concern).
    #[test]
    fn resolve_login_principal_policy_uses_force_login_team_only() {
        assert_eq!(resolve_login_principal_policy(None), None);
        assert_eq!(
            resolve_login_principal_policy(Some(&ForceLoginTeam::Single("team-locked".into()))),
            Some(ForceLoginTeam::Single("team-locked".into())),
        );
        assert_eq!(
            resolve_login_principal_policy(Some(&ForceLoginTeam::AnyOf(vec![
                "a".into(),
                "b".into()
            ]))),
            Some(ForceLoginTeam::AnyOf(vec!["a".into(), "b".into()])),
        );
    }
    /// Discovery is cached for `DISCOVERY_CACHE_TTL`: the second call
    /// to `discover()` for the same issuer hits the cache and does not
    /// fetch over HTTP. Without this, every refresh pays a discovery
    /// round-trip and a discovery-endpoint blip blocks token refresh.
    #[tokio::test]
    async fn discover_uses_cache_within_ttl() {
        use std::sync::atomic::{AtomicU32, Ordering};
        clear_discovery_cache();
        let hits = std::sync::Arc::new(AtomicU32::new(0));
        let hits_for_handler = hits.clone();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let issuer = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let issuer_for_handler = issuer.clone();
        let app = axum::Router::new().route(
            "/.well-known/openid-configuration",
            axum::routing::get(move || {
                let b = issuer_for_handler.clone();
                let counter = hits_for_handler.clone();
                async move {
                    counter.fetch_add(1, Ordering::SeqCst);
                    axum::Json(serde_json::json!({
                        "authorization_endpoint": format!("{b}/authorize"),
                        "token_endpoint": format!("{b}/token"),
                    }))
                }
            }),
        );
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let _ = discover(&issuer).await.unwrap();
        let _ = discover(&issuer).await.unwrap();
        let _ = discover(&issuer).await.unwrap();
        assert_eq!(
            hits.load(Ordering::SeqCst),
            1,
            "discover() must hit the network exactly once across 3 calls (cache TTL = 1h)"
        );
        server.abort();
    }
    /// `refresh_tokens` retries on a transient 503 and succeeds on the
    /// next attempt. Without backon, a single IdP blip during refresh
    /// surfaces to the user as a chat failure.
    #[tokio::test]
    async fn refresh_tokens_retries_on_transient_5xx() {
        use std::sync::atomic::{AtomicU32, Ordering};
        let hits = std::sync::Arc::new(AtomicU32::new(0));
        let hits_for_handler = hits.clone();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let app = axum::Router::new().route(
            "/token",
            axum::routing::post(move || {
                let counter = hits_for_handler.clone();
                async move {
                    let n = counter.fetch_add(1, Ordering::SeqCst) + 1;
                    if n == 1 {
                        (
                            axum::http::StatusCode::SERVICE_UNAVAILABLE,
                            "upstream busy".to_string(),
                        )
                    } else {
                        (
                            axum::http::StatusCode::OK,
                            r#"{"access_token":"new-at","expires_in":3600}"#.to_string(),
                        )
                    }
                }
            }),
        );
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let token_endpoint = format!("http://127.0.0.1:{port}/token");
        let resp = refresh_tokens(&token_endpoint, "rt", "client", None, None)
            .await
            .expect("transient 5xx must be retried until success");
        assert_eq!(resp.access_token, "new-at");
        assert_eq!(
            hits.load(Ordering::SeqCst),
            2,
            "first attempt fails 503, second succeeds — exactly 2 hits"
        );
        server.abort();
    }
    /// Terminal OAuth2 errors (`invalid_grant`, `invalid_client`) MUST
    /// NOT be retried -- retrying a revoked grant just wastes time and
    /// risks rate-limit. Verifies `is_transient_refresh_error` correctly
    /// classifies typed 4xx as terminal.
    #[tokio::test]
    async fn refresh_tokens_does_not_retry_terminal_invalid_grant() {
        use std::sync::atomic::{AtomicU32, Ordering};
        let hits = std::sync::Arc::new(AtomicU32::new(0));
        let hits_for_handler = hits.clone();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let app = axum::Router::new().route(
            "/token",
            axum::routing::post(move || {
                let counter = hits_for_handler.clone();
                async move {
                    counter.fetch_add(1, Ordering::SeqCst);
                    (
                        axum::http::StatusCode::BAD_REQUEST,
                        r#"{"error":"invalid_grant","error_description":"refresh token revoked"}"#
                            .to_string(),
                    )
                }
            }),
        );
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let token_endpoint = format!("http://127.0.0.1:{port}/token");
        let err = refresh_tokens(&token_endpoint, "rt", "client", None, None)
            .await
            .expect_err("invalid_grant is terminal");
        assert!(
            err.to_string().contains("400") || err.to_string().contains("invalid_grant"),
            "error must surface the IdP rejection, got: {err}"
        );
        assert_eq!(
            hits.load(Ordering::SeqCst),
            1,
            "terminal OAuth2 error must NOT be retried (exactly 1 hit)"
        );
        server.abort();
    }
    /// A 4xx carrying an OAuth2 code that is NOT a recognized terminal one
    /// (e.g. RFC 6749 `temporarily_unavailable`) must be retried, not given up
    /// on. The retry gate defers to `classify_terminal`, so only the recognized
    /// terminal codes stop retries; everything else is transient.
    #[tokio::test]
    async fn refresh_tokens_retries_on_coded_transient_error() {
        use std::sync::atomic::{AtomicU32, Ordering};
        let hits = std::sync::Arc::new(AtomicU32::new(0));
        let hits_for_handler = hits.clone();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let app = axum::Router::new().route(
            "/token",
            axum::routing::post(move || {
                let counter = hits_for_handler.clone();
                async move {
                    let n = counter.fetch_add(1, Ordering::SeqCst) + 1;
                    if n == 1 {
                        (
                            axum::http::StatusCode::BAD_REQUEST,
                            r#"{"error":"temporarily_unavailable"}"#.to_string(),
                        )
                    } else {
                        (
                            axum::http::StatusCode::OK,
                            r#"{"access_token":"new-at","expires_in":3600}"#.to_string(),
                        )
                    }
                }
            }),
        );
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let token_endpoint = format!("http://127.0.0.1:{port}/token");
        let resp = refresh_tokens(&token_endpoint, "rt", "client", None, None)
            .await
            .expect("a non-terminal coded 4xx must be retried until success");
        assert_eq!(resp.access_token, "new-at");
        assert_eq!(
            hits.load(Ordering::SeqCst),
            2,
            "temporarily_unavailable must be retried (1 fail + 1 success = 2 hits)"
        );
        server.abort();
    }
}
