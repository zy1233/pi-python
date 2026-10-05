//! Sync `managed_config.toml` + `requirements.toml` from the deployment-config endpoint per principal.
//! Overwritten per fetch, evicted on a confirmed identity switch, cleared on logout — so config never leaks across principals.

mod response;

use crate::auth::GrokAuth;
pub(crate) use response::ManagedConfigError;
use response::{ApplyOutcome, ManagedConfigResponse, ManagedConfigSource, verify_signed_envelope};

/// Server-synced policy artifacts. Excludes the sync marker ([`remove_managed_config_files`]
/// removes that last, only on full success).
pub(crate) const MANAGED_ARTIFACT_FILES: [&str; 4] = [
    pi_config::MANAGED_CONFIG_FILENAME,
    pi_config::REQUIREMENTS_FILENAME,
    pi_config::signed_policy::SIGNATURE_SIDECAR_FILE,
    pi_config::signed_policy::MANAGED_IDENTITY_SIDECAR_FILE,
];

/// Delete server-synced files then the marker (never `config.toml`).
fn remove_managed_config_files(home: &std::path::Path) {
    let mut artifacts_removed = true;
    for name in MANAGED_ARTIFACT_FILES {
        artifacts_removed &= remove_synced_file(home, name, "removed managed config file");
    }
    // Marker last, only on full success: crash/error leaves the detector armed for the next start.
    if artifacts_removed {
        remove_synced_file(
            home,
            pi_config::MANAGED_CONFIG_CACHE_FILE,
            "removed managed config file",
        );
    }
    // Best-effort sweep of mid-write `.tmp` leftovers (a concurrent writer's temp may go too —
    // its rename fails and self-heals).
    let atomic_write_tmp_prefixes = [
        format!("{}.", pi_config::MANAGED_CONFIG_CACHE_FILE),
        format!(
            "{}.",
            pi_config::signed_policy::SIGNATURE_SIDECAR_FILE
        ),
        format!(
            "{}.",
            pi_config::signed_policy::MANAGED_IDENTITY_SIDECAR_FILE
        ),
    ];
    if let Ok(entries) = std::fs::read_dir(home) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            let is_write_tmp = name.ends_with(".tmp")
                && atomic_write_tmp_prefixes
                    .iter()
                    .any(|prefix| name.starts_with(prefix.as_str()));
            if is_write_tmp {
                let _ = std::fs::remove_file(entry.path());
            }
        }
    }
}

/// Returns whether the path is gone (removed or already absent); `false` = removal failed.
fn remove_synced_file(home: &std::path::Path, name: &str, why: &str) -> bool {
    let path = home.join(name);
    match remove_managed_path(&path) {
        Ok(true) => {
            tracing::info!(file = %path.display(), "{why}");
            true
        }
        Ok(false) => true,
        Err(e) => {
            tracing::warn!(file = %path.display(), error = %e, "failed to remove managed config file");
            false
        }
    }
}

/// Clear a directory squatting where a managed file is about to be WRITTEN — the atomic
/// rename would fail onto it forever, permanently blocking the self-heal. Best-effort:
/// the write's own error surfaces if clearing fails.
fn clear_squatting_dir(path: &std::path::Path) {
    if std::fs::symlink_metadata(path).is_ok_and(|m| m.is_dir())
        && let Err(e) = remove_managed_path(path)
    {
        tracing::warn!(error = %e, "failed to clear a directory squatting at a managed config path");
    }
}

/// Remove whatever occupies a managed artifact path — a squatting DIRECTORY too, else a
/// dir-squat would block removal and rewrite forever. Only ever called with the fixed
/// managed artifact/marker/sidecar names. `Ok(true)` = removed; `Ok(false)` = already absent.
fn remove_managed_path(path: &std::path::Path) -> std::io::Result<bool> {
    let is_dir = std::fs::symlink_metadata(path).is_ok_and(|m| m.is_dir());
    let result = if is_dir {
        std::fs::remove_dir_all(path)
    } else {
        std::fs::remove_file(path)
    };
    match result {
        Ok(()) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e),
    }
}

/// A team principal is eligible to fetch only if non-expired (an expired token
/// would just 401).
fn eligible_team_principal(auth: GrokAuth) -> Option<GrokAuth> {
    (auth.is_team_principal() && !crate::auth::is_expired(&auth)).then_some(auth)
}

/// The eligible team principal in `auth.json`, or `None`. Single-team: managed
/// config is a grok.com feature with one grok.com auth.
fn read_active_team_auth() -> Option<GrokAuth> {
    let home = crate::util::grok_home::grok_home();
    let store = crate::auth::read_auth_json(&home.join("auth.json")).ok()?;
    let team = store.values().find(|a| a.is_team_principal())?.clone();
    eligible_team_principal(team)
}

pub(crate) fn has_active_team_auth() -> bool {
    read_active_team_auth().is_some()
}

/// Whether any team principal is signed in, **ignoring expiry** (a cold-start
/// expired token is not a logout). `Err` = `auth.json` unreadable: callers must
/// NOT treat that as a logout — it would wipe enforced policy on a read blip.
fn team_principal_signed_in() -> std::io::Result<bool> {
    let home = crate::util::grok_home::grok_home();
    match crate::auth::read_auth_json(&home.join("auth.json")) {
        Ok(store) => Ok(store.values().any(|a| a.is_team_principal())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e),
    }
}

/// Clear the synced files when no principal could own them: no deployment key
/// configured and no team signed in (logout). A configured deployment key keeps
/// its files (original "never auto-deletes" behavior). Runs at startup and on
/// logout; best-effort.
///
/// **fail_closed:** when the marker or on-disk requirements opt in to fail-closed
/// (or requirements exist but are unreadable), do **not** wipe. A personal/User
/// principal (or signed-out auth) must not escape enforced policy by swapping
/// `auth.json` and letting orphan clear delete the artifacts. Non-fail-closed
/// team policy still clears on logout as before.
pub fn clear_orphan() {
    if resolve_deployment_key().is_some() {
        return;
    }
    match team_principal_signed_in() {
        Ok(true) => return,
        Ok(false) => {}
        Err(e) => {
            tracing::warn!(error = %e, "auth.json unreadable; keeping managed config until it recovers");
            return;
        }
    }
    let home = crate::util::grok_home::grok_home();
    let Some(_lock) = try_lock_managed_config(&home) else {
        return; // another process is syncing; retry next call
    };
    if pi_config::fail_closed_policy_armed_at(&home) {
        tracing::info!(
            "keeping fail_closed managed policy on disk; no team principal present to own a clear"
        );
        return;
    }
    remove_managed_config_files(&home);
}

/// Best-effort cross-process lock serializing apply/remove of the managed-config
/// files (TUI tick vs `grok login` vs prefetch). `None` on contention — the
/// caller skips and retries next cycle.
fn try_lock_managed_config(home: &std::path::Path) -> Option<std::fs::File> {
    use fs2::FileExt;
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(home.join("managed_config.lock"))
        .ok()?;
    file.try_lock_exclusive().ok()?;
    Some(file)
}

/// Retry budget for a sync, pairing the attempt count with a wall-clock cap.
#[derive(Clone, Copy)]
enum SyncBudget {
    /// Background loop and explicit `grok setup`; runs retries to completion.
    Standard,
}

impl SyncBudget {
    /// Total fetch attempts (first try included) for transient failures.
    fn max_attempts(self) -> u32 {
        match self {
            Self::Standard => 5,
        }
    }

}

/// Exponential backoff for retry `attempt` (caller guarantees `attempt >= 1`).
/// Base is 1s; `GROK_DEPLOYMENT_CONFIG_BACKOFF_MS` overrides it for tests.
fn retry_backoff(attempt: u32) -> std::time::Duration {
    let base = std::env::var("GROK_DEPLOYMENT_CONFIG_BACKOFF_MS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(1000);
    std::time::Duration::from_millis(base << attempt.saturating_sub(1))
}

/// Fetch the managed-config response, retrying transient (network / connection
/// interruption / 5xx) failures with exponential backoff. Auth errors fail
/// immediately, mapped via `source` so the message names the rejected credential.
///
/// Routes the whole once-fetch (send + body read + decode) through `crate::http::send_with_retry_escaping_pool`,
/// so a body-phase interruption is retried (not just the send) and the final attempt escapes a
/// poisoned pool on a fresh connection (see that helper for the escape policy).
async fn fetch_managed_config(
    url: &str,
    token: &str,
    source: ManagedConfigSource,
    max_attempts: u32,
    echo_principal: Option<&str>,
) -> Result<ManagedConfigResponse, ManagedConfigError> {
    crate::http::send_with_retry_escaping_pool(
        move |client: reqwest::Client| async move {
            fetch_managed_config_once(&client, url, token, source, echo_principal).await
        },
        max_attempts,
        |e: &ManagedConfigError| e.is_retryable(),
        |attempt| tokio::time::sleep(retry_backoff(attempt)),
    )
    .await
}

/// Persist a fetched response under `home`, converging disk to the served set: served
/// artifacts are overwritten, unserved ones removed — a leftover must not keep enforcing
/// a withdrawn policy or trip the signed absence check. Returns whether anything changed.
fn apply_managed_config(
    home: &std::path::Path,
    body: &ManagedConfigResponse,
) -> std::io::Result<bool> {
    use crate::util::config::atomic_write_string;

    let artifacts = [
        (
            pi_config::MANAGED_CONFIG_FILENAME,
            body.managed_config.as_deref(),
        ),
        (
            pi_config::REQUIREMENTS_FILENAME,
            body.requirements.as_deref(),
        ),
    ];

    let mut changed = false;
    let mut first_err: Option<std::io::Error> = None;
    for (name, content) in artifacts {
        let path = home.join(name);
        match content.filter(|s| !s.is_empty()) {
            Some(content) => {
                clear_squatting_dir(&path);
                match atomic_write_string(&path, content) {
                    Ok(()) => changed = true,
                    Err(e) => {
                        first_err.get_or_insert(e);
                    }
                }
            }
            None => match remove_managed_path(&path) {
                Ok(true) => {
                    tracing::info!("removed managed config artifact the server no longer serves");
                    changed = true;
                }
                Ok(false) => {}
                Err(e) => {
                    first_err.get_or_insert(e);
                }
            },
        }
    }

    if changed {
        tracing::info!("managed config refreshed from server");
    }
    match first_err {
        Some(e) => Err(e),
        None => Ok(changed),
    }
}

/// Map a classified transport failure to a `ManagedConfigError`. Split out from [`map_send_error`]
/// so the mapping (and its retryability) is unit-testable by constructing `TransportFailure` directly.
fn map_transport_failure(failure: crate::http::TransportFailure) -> ManagedConfigError {
    use crate::http::TransportFailureKind;
    match failure.kind {
        TransportFailureKind::CertificateUntrusted => {
            ManagedConfigError::CertificateUntrusted(certificate_detail(
                failure.detail,
                pi_extra_ca::configured_bundle_env(),
                pi_extra_ca::extra_root_ders().len(),
            ))
        }
        TransportFailureKind::CertificateInvalid => {
            ManagedConfigError::CertificateInvalid(failure.detail)
        }
        TransportFailureKind::Unreachable => ManagedConfigError::Network(failure.detail),
        TransportFailureKind::Interrupted => {
            ManagedConfigError::ConnectionInterrupted(failure.detail)
        }
        // A builder/redirect failure is a client-side defect, not a bad server response: terminal.
        TransportFailureKind::Permanent => ManagedConfigError::RequestFailed(failure.detail),
    }
}

/// Names the configured bundle variable; loading is fail-open, so this error
/// can be the only visible symptom.
fn certificate_detail(detail: String, bundle_env: Option<&str>, loaded_roots: usize) -> String {
    match bundle_env {
        Some(env) if loaded_roots == 0 => format!(
            "{detail}; {env} is set but no usable roots were loaded from it: check that the file is readable, contains PEM certificates, and is under the size cap"
        ),
        Some(env) => format!("{detail}; {env} is set: verify it includes the issuing root CA"),
        None => detail,
    }
}

/// Map a `reqwest` send failure to a `ManagedConfigError` via the shared `pi-http` classifier.
fn map_send_error(e: &reqwest::Error) -> ManagedConfigError {
    map_transport_failure(crate::http::TransportFailure::classify(e))
}

async fn fetch_managed_config_once(
    client: &reqwest::Client,
    url: &str,
    token: &str,
    source: ManagedConfigSource,
    echo_principal: Option<&str>,
) -> Result<ManagedConfigResponse, ManagedConfigError> {
    let mut request = client
        .get(url)
        .header("Authorization", format!("Bearer {}", token))
        .timeout(std::time::Duration::from_secs(15));
    // Replay-probe echo (telemetry only). Skip on invalid HeaderValue so a
    // corrupt sidecar never bricks the fetch (echo is fail-open).
    if let Some(nonce) = pi_config::signed_policy::stored_envelope_nonce(
        &crate::util::grok_home::grok_home(),
        echo_principal,
    ) && let Ok(value) = reqwest::header::HeaderValue::from_str(&nonce)
    {
        request = request.header(
            pi_config::signed_policy::MANAGED_CONFIG_NONCE_ECHO_HEADER,
            value,
        );
    }
    let resp = match request.send().await {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            let status = r.status().as_u16();
            tracing::debug!(status, "managed config fetch failed");
            return Err(if status == 401 || status == 403 {
                source.auth_rejected_error()
            } else {
                ManagedConfigError::ServerError { status }
            });
        }
        Err(e) => {
            let err = map_send_error(&e);
            tracing::debug!(error = %err, "managed config fetch error");
            return Err(err);
        }
    };

    // Split the body read from the decode so the FAILING OPERATION disambiguates transport from
    // payload: reqwest tags both a mid-body connection drop and malformed JSON as `Kind::Decode`
    // from `json()`, so reading raw `bytes()` first (any error there is an in-flight transport
    // interruption, retryable) then `from_slice` (any error there is a malformed payload, terminal)
    // avoids fragile error-kind/source inspection.
    let bytes = match resp.bytes().await {
        Ok(b) => b,
        // A body-read failure is an in-flight transport interruption, so it is retryable.
        Err(e) => {
            return Err(ManagedConfigError::ConnectionInterrupted(
                crate::http::error_cause_chain(&e),
            ));
        }
    };
    serde_json::from_slice::<ManagedConfigResponse>(&bytes)
        .map_err(|e| ManagedConfigError::InvalidResponse(e.to_string()))
}

/// Deployment id reported for `deployment_key` on chat requests, credential
/// snapshots, and OTel: the **server** GrokBuildDeployment UUID (the id
/// server-side dashboards filter on) when the managed-config sync marker was
/// written by this same key (fingerprint match), else UUIDv5 of the key.
/// `None` key (team/OAuth) → `None`, never a stale marker value.
pub(crate) fn resolve_deployment_id(deployment_key: Option<&str>) -> Option<String> {
    let key = deployment_key.filter(|k| !k.is_empty())?;
    crate::config::managed_deployment_id(&deployment_key_fingerprint(key))
        .or_else(|| Some(crate::agent::config::deployment_id_from_key(key)))
}

/// Resolve deployment key from `GROK_DEPLOYMENT_KEY` env var, then config files.
pub(crate) fn resolve_deployment_key() -> Option<String> {
    let config_val = crate::config::load_effective_config()
        .map_err(|e| tracing::warn!("failed to load config files for deployment key: {e}"))
        .ok()
        .and_then(|root| {
            root.get("endpoints")?
                .get("deployment_key")?
                .as_str()
                .map(|s| s.to_owned())
        });
    crate::agent::config::resolve_string_flag(
        None,
        "GROK_DEPLOYMENT_KEY",
        config_val.as_deref(),
        None,
    )
    .map(|r| r.value)
}

/// One-way blake3 fingerprint of a deployment key — the deploy-key identity (see [`crate::config::ServingIdentity`]).
/// Deterministic so the same key matches its marker; the raw key is never written to disk.
fn deployment_key_fingerprint(key: &str) -> String {
    blake3::hash(key.as_bytes()).to_hex().to_string()
}

/// Whether managed config fetching is enabled (env > config.toml > default true).
/// Callers doing auto-fetch should check this; explicit user actions (grok setup) skip it.
///
/// Overlay-free: reads the raw config layers via
/// [`crate::config::ConfigLayers::effective_config_base_without_overlay`] rather
/// than the overlay-inclusive effective config, so a `GROK_CONFIG` overlay cannot
/// suppress the requirements/managed-config sync (a policy-enforcement gate, like
/// `remote_fetch`; see the overlay-free contract in `ConfigLayers::env_overlay`).
/// Requirements/MDM still clamp through the base merge.
pub fn is_fetch_enabled() -> bool {
    if let Some(v) = crate::agent::config::env_bool("GROK_MANAGED_CONFIG") {
        return v;
    }
    crate::config::ConfigLayers::load()
        .ok()
        .and_then(|layers| managed_config_enabled_from_layers(&layers))
        .unwrap_or(true)
}

/// `[features] managed_config` from the raw (overlay-free) config layers, or
/// `None` when unset. Split out so the overlay-free contract is unit-testable
/// without touching disk.
fn managed_config_enabled_from_layers(layers: &crate::config::ConfigLayers) -> Option<bool> {
    layers
        .effective_config_base_without_overlay()
        .get("features")?
        .get("managed_config")?
        .as_bool()
}

/// Fetch managed config + requirements and write to `~/.grok/`, trying the
/// deployment key first, then a signed-in team. `Ok(false)` when neither applies.
pub async fn sync() -> Result<bool, ManagedConfigError> {
    Ok(sync_with_budget(SyncBudget::Standard, None).await?.wrote)
}

struct SyncOutcome {
    wrote: bool,
}

impl SyncOutcome {
    /// Reports only what callers render; marker identity fields live in [`apply_fetched`].
    fn from_fetch(outcome: &ApplyOutcome) -> Self {
        Self {
            wrote: outcome.wrote(),
        }
    }
}

/// A server response paired with the credential that fetched it.
enum FetchedConfig {
    DeploymentKey {
        key: String,
        body: ManagedConfigResponse,
    },
    Team {
        auth: Box<GrokAuth>,
        body: ManagedConfigResponse,
    },
    /// No deployment key configured and no eligible team signed in.
    NoPrincipal,
}

/// Fetches the configuration for the current principal without touching disk:
/// the deployment key first, then a signed-in team. The installing sync and the
/// read-only `grok setup --json` both build on this.
async fn fetch_for_principal(
    budget: SyncBudget,
    team_override: Option<GrokAuth>,
) -> Result<FetchedConfig, ManagedConfigError> {
    let max_attempts = budget.max_attempts();
    // Resolve from the merged config (managed_config_url > cli_chat_proxy_base_url,
    // including the enterprise single-endpoint derivation) so endpoint overrides
    // are honored and the bearer isn't sent to the public default.
    let url =
        crate::agent::config::EndpointsConfig::from_effective_config().resolve_managed_config_url();

    let team_auth = team_override.or_else(read_active_team_auth);

    if let Some(dk) = resolve_deployment_key() {
        let source = ManagedConfigSource::DeploymentKey;
        // Echo binds to the deployment this key last synced (marker-bound; None
        // on first sync or after a key rotation — then there is nothing to echo).
        let echo_principal = crate::config::managed_deployment_id(&deployment_key_fingerprint(&dk));
        match fetch_managed_config(&url, &dk, source, max_attempts, echo_principal.as_deref()).await
        {
            // A rejected dk (stale env/config) must not starve a valid team
            // sign-in: fall through. Network/5xx do NOT — same unreachable
            // server, double the latency for nothing.
            Err(ManagedConfigError::DeploymentKeyRejected) if team_auth.is_some() => {
                tracing::warn!("deployment key rejected; falling back to the team session token");
            }
            Err(e) => return Err(e),
            // Fall through to the team only when the dk has no config row: an apply
            // converges disk to the served set, and the empty dk body must not delete
            // the team's files. Gate on row existence, not content (which can serve empty).
            Ok(body) if !body.config_exists() && team_auth.is_some() => {
                tracing::debug!("deployment key has no config; trying the team principal");
            }
            Ok(body) => return Ok(FetchedConfig::DeploymentKey { key: dk, body }),
        }
    }

    // The proxy resolves the team from the principal and returns its config.
    if let Some(auth) = team_auth {
        let body = fetch_managed_config(
            &url,
            &auth.key,
            ManagedConfigSource::TeamOauth,
            max_attempts,
            auth.team_id.as_deref(),
        )
        .await?;
        return Ok(FetchedConfig::Team {
            auth: Box::new(auth),
            body,
        });
    }

    Ok(FetchedConfig::NoPrincipal)
}

/// `team_override` pins a specific team principal (the just-authenticated one,
/// post-login) instead of re-deriving the team from `auth.json`; `None` uses
/// [`read_active_team_auth`]. Marker is written under the lock by [`apply_fetched`].
async fn sync_with_budget(
    budget: SyncBudget,
    team_override: Option<GrokAuth>,
) -> Result<SyncOutcome, ManagedConfigError> {
    match fetch_for_principal(budget, team_override).await? {
        FetchedConfig::DeploymentKey { key, body } => {
            let source = ManagedConfigSource::DeploymentKey;
            let fingerprint = deployment_key_fingerprint(&key);
            let outcome = apply_fetched(
                &body,
                source,
                body.deployment_id.as_deref(),
                Some(&fingerprint),
            )?;
            Ok(SyncOutcome::from_fetch(&outcome))
        }
        FetchedConfig::Team { auth, body } => {
            let source = ManagedConfigSource::TeamOauth;
            // Team identity is bound via principal (team id), not a key fingerprint.
            let outcome = apply_fetched(&body, source, auth.team_id.as_deref(), None)?;
            Ok(SyncOutcome::from_fetch(&outcome))
        }
        FetchedConfig::NoPrincipal => Ok(SyncOutcome { wrote: false }),
    }
}

/// Apply under the cross-process lock (`Skipped` if contended — holder's sync supersedes).
/// `new_principal` / `new_key_fingerprint` are the serving identity for pre-write eviction.
fn apply_fetched(
    body: &ManagedConfigResponse,
    source: ManagedConfigSource,
    new_principal: Option<&str>,
    new_key_fingerprint: Option<&str>,
) -> std::io::Result<ApplyOutcome> {
    // Verify before lock/persist: prior trusted policy survives a bad fetch. Pure so a
    // lock-skip never reports Applied for an envelope that would have failed.
    let verified = if pi_config::signed_policy::verification_active() {
        match verify_signed_envelope(body, active_team_id_any_expiry().as_deref()) {
            Ok(verified) => Some(verified),
            Err(e) => {
                tracing::warn!("managed config signature rejected; not persisting: {e}");
                return Ok(ApplyOutcome::SignatureRejected);
            }
        }
    } else {
        None
    };
    let signed_deployment_id = verified
        .as_ref()
        .and_then(|v| v.payload.deployment_id.clone());
    let home = crate::util::grok_home::grok_home();
    let Some(_lock) = try_lock_managed_config(&home) else {
        tracing::debug!("managed config locked by another process; skipping apply");
        return Ok(ApplyOutcome::Skipped);
    };
    // Credential may have vanished mid-fetch (logout → clear_orphan); don't restore it.
    if !credential_present(source) {
        tracing::info!("credential gone since fetch started; skipping apply");
        return Ok(ApplyOutcome::Skipped);
    }
    // Confirmed switch: evict first so omitted artifacts from the prior principal don't stick.
    // Same locked `home` as the flock + marker write (no re-resolve).
    if crate::config::managed_config_identity_changed_at(&home, new_principal, new_key_fingerprint)
    {
        evict_prior_managed_config(&home);
    }
    let wrote = apply_managed_config(&home, body)?;
    // Sidecar after policy files so a present sidecar covers the final set; clear dir squats
    // that would fail the atomic rename forever.
    if let Some(verified) = verified {
        clear_squatting_dir(&home.join(pi_config::signed_policy::SIGNATURE_SIDECAR_FILE));
        pi_config::signed_policy::write_sidecar(&home, &verified.sidecar)?;
        // Disk errors are fatal, like the policy sidecar's.
        if let Some(claim_sidecar) =
            verified_claim_sidecar(body, served_principal_of(&verified.payload))
        {
            clear_squatting_dir(
                &home.join(pi_config::signed_policy::MANAGED_IDENTITY_SIDECAR_FILE),
            );
            pi_config::signed_policy::write_managed_identity_sidecar(&home, &claim_sidecar)?;
        }
    }
    // Marker last, still under the lock: written post-release, a concurrent purge could
    // delete the files it describes. A squatting dir would fail the atomic rename forever.
    clear_squatting_dir(&home.join(pi_config::MANAGED_CONFIG_CACHE_FILE));
    crate::config::mark_managed_config_synced_at(
        &home,
        crate::config::SyncMarker {
            // DK: prefer verified payload deployment id (signed-empty only has it there).
            // Team: always the serving team — a deployment-signed envelope must not rebind it.
            principal: if new_key_fingerprint.is_some() {
                signed_deployment_id.as_deref().or(new_principal)
            } else {
                new_principal
            },
            had_managed_config: body.has_managed_config(),
            had_requirements: body.has_requirements(),
            key_fingerprint: new_key_fingerprint,
            fail_closed: body.requirements_fail_closed(),
        },
    );
    Ok(ApplyOutcome::Applied { wrote })
}

/// The principal a verified payload binds: `deployment_id`, else `team_id` (server parity).
fn served_principal_of(payload: &pi_config::signed_policy::SignedPayload) -> Option<&str> {
    payload
        .deployment_id
        .as_deref()
        .or(payload.team_id.as_deref())
}

/// The fetched claim envelope, if it verifies and binds to the served principal.
/// `None` skips (old server / unverifiable / foreign): a bad claim must not fail
/// the apply — it only hardens the policy sidecar.
fn verified_claim_sidecar(
    body: &ManagedConfigResponse,
    served_principal: Option<&str>,
) -> Option<pi_config::signed_policy::SignatureEnvelope> {
    use pi_config::signed_policy::now_unix;
    let sidecar = body.managed_identity_sidecar()?;
    // Unclamped wall clock, like the policy verify: a fresh claim heals an inflated floor.
    let claim = match pi_config::signed_policy::verify_fetched_claim(&sidecar, now_unix()) {
        Ok(claim) => claim,
        Err(e) => {
            tracing::debug!("is-managed claim did not verify; not persisting it: {e}");
            return None;
        }
    };
    if !claim_binds_to(&claim, served_principal) {
        tracing::debug!("is-managed claim is bound to a different principal; not persisting it");
        return None;
    }
    Some(sidecar)
}

/// The persist rule: a verified claim persists only when bound to the served principal.
fn claim_binds_to(
    claim: &pi_config::signed_policy::ManagedIdentityClaim,
    served_principal: Option<&str>,
) -> bool {
    served_principal == Some(claim.principal.as_str())
}

/// Evict the prior principal's policy artifacts on a confirmed switch; this apply then
/// writes the new set and rebinds the marker. Includes the sidecars — a verification-inactive
/// build must not leave the prior tenant's sidecar to read foreign-bound on a signing build.
fn evict_prior_managed_config(home: &std::path::Path) {
    for name in MANAGED_ARTIFACT_FILES {
        remove_synced_file(home, name, "evicted prior principal's artifact");
    }
}

/// Whether the credential a fetch used is still present. Mirrors the
/// expiry-agnostic, fail-safe checks `clear_orphan` uses (an unreadable
/// `auth.json` keeps, not drops).
fn credential_present(source: ManagedConfigSource) -> bool {
    match source {
        ManagedConfigSource::DeploymentKey => resolve_deployment_key().is_some(),
        ManagedConfigSource::TeamOauth => team_principal_signed_in().unwrap_or(true),
    }
}

/// Whether a credential exists that `grok setup` could install config for.
pub fn has_principal() -> bool {
    resolve_deployment_key().is_some() || read_active_team_auth().is_some()
}

/// The serving identity for an optional team id: a configured deployment key always
/// wins (keyed on its fingerprint), else the team, else none. The two public views
/// differ only in how the team id is resolved (expiry-filtered vs expiry-ignoring).
fn serving_identity_from(team_id: Option<String>) -> crate::config::ServingIdentity {
    use crate::config::ServingIdentity;
    if let Some(key) = resolve_deployment_key() {
        return ServingIdentity::DeploymentKey {
            fingerprint: deployment_key_fingerprint(&key),
        };
    }
    // Blank = unknown; trimmed (same rule as the marker write) so whitespace isn't identity.
    match crate::config::normalize_identity(team_id.as_deref()) {
        Some(team_id) => ServingIdentity::Team(team_id),
        None => ServingIdentity::None,
    }
}

/// The identity to check the cache against for whoever serves now: a configured deployment key wins
/// (else the active team, else none).
pub fn current_serving_identity() -> crate::config::ServingIdentity {
    serving_identity_from(read_active_team_auth().and_then(|a| a.team_id))
}

/// The client's team_id, IGNORING token expiry (the binding must survive the cold-start
/// expired window). Must NOT special-case a configured deployment key — that would
/// disable envelope binding for a real team user. Used at fetch time to bind the envelope.
pub(crate) fn active_team_id_any_expiry() -> Option<String> {
    let home = crate::util::grok_home::grok_home();
    let store = crate::auth::read_auth_json(&home.join("auth.json")).ok()?;
    store
        .values()
        .find(|a| a.is_team_principal())
        // Blank → None, trimmed: a malformed/padded auth.json team_id must read as the SAME
        // identity everywhere it feeds — the gate, the tenant-switch purge, and the envelope
        // binding (an untrimmed id here would fail `check_fetch_identity` against a trimmed
        // signed payload forever).
        .and_then(|a| crate::config::normalize_identity(a.team_id.as_deref()))
}

// Tests in a sibling file (they dwarf the module) but a child module, for private access.
#[cfg(test)]
#[path = "managed_config/tests.rs"]
mod tests;
