//! Model auth providers (`[auth_provider.<name>]`).
//!
//! A model opts in with `auth_provider = "<name>"`; the named table declares a
//! command that prints a fresh bearer token, which this module mints, caches,
//! and rotates for that model's requests.
//!
//! The minted token stays in memory only ([`AUTH_PROVIDER_SLOTS`] and chat
//! state, never `auth.json`); the command is a credential helper that owns its
//! own durable storage and OAuth2 refresh. See "Where model auth providers fit
//! (and don't)"
//! in `docs/internal/AUTH.md`.
//!
//! This is distinct from the `AuthCredentialProvider` HTTP consumers in
//! [`crate::auth::credential_provider`].

/// One named `[auth_provider.<name>]` table, honored only from the trusted
/// config layers (`parse_auth_providers`). A new field here needs a
/// `parse_auth_providers` warning decision.
#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Deserialize)]
#[serde(default)]
pub struct AuthProviderConfig {
    /// Command to run; without `args` it uses the platform shell, with `args` it execs directly.
    pub command: String,
    /// Command arguments; when set (even empty) the command execs directly.
    pub args: Option<Vec<String>>,
    /// Fallback token lifetime used when the output carries no `expires_in`.
    pub token_ttl_secs: Option<u64>,
    /// Max seconds to wait for the command (default 30, clamped to 1..=600).
    pub timeout_secs: Option<u64>,
    /// Working directory for the command; a leading `~` expands to home.
    pub cwd: Option<String>,
}

impl AuthProviderConfig {
    pub(crate) fn is_usable(&self) -> bool {
        !self.command.trim().is_empty()
    }
}

/// A model's reference to a named auth provider, built by `resolve_model_list`.
#[derive(Clone, serde::Serialize, serde::Deserialize)]
#[serde(from = "AuthProviderRefData", into = "AuthProviderRefData")]
pub struct AuthProviderRef {
    pub(crate) name: String,
    pub(crate) config: AuthProviderConfig,
    slot: ProviderSlot,
    /// `true` once the trusted table is attached. A ref revived from bytes is
    /// `false` and never mints or reads until [`AuthProviderRef::attach_trusted_config`]
    /// joins the shared slot for its name.
    resolved: bool,
    fail_closed: bool,
}

#[derive(serde::Serialize, serde::Deserialize)]
struct AuthProviderRefData {
    name: String,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    fail_closed: bool,
}

impl From<AuthProviderRefData> for AuthProviderRef {
    fn from(data: AuthProviderRefData) -> Self {
        if data.fail_closed {
            AuthProviderRef::fail_closed(data.name)
        } else {
            AuthProviderRef::unresolved(data.name)
        }
    }
}

impl From<AuthProviderRef> for AuthProviderRefData {
    fn from(provider: AuthProviderRef) -> Self {
        Self {
            name: provider.name,
            fail_closed: provider.fail_closed,
        }
    }
}

impl AuthProviderRef {

    /// The in-memory form of a ref revived from bytes;
    /// [`AuthProviderRef::attach_trusted_config`] resolves it.
    pub(crate) fn unresolved(name: String) -> Self {
        Self {
            name,
            config: AuthProviderConfig::default(),
            slot: ProviderSlot::default(),
            resolved: false,
            fail_closed: false,
        }
    }

    pub(crate) fn fail_closed(name: String) -> Self {
        Self {
            name,
            config: AuthProviderConfig::default(),
            slot: ProviderSlot::default(),
            resolved: true,
            fail_closed: true,
        }
    }

}

/// Ignores the slot; a deserialized ref compares unequal until resolution
/// re-attaches its config.
impl PartialEq for AuthProviderRef {
    fn eq(&self, other: &Self) -> bool {
        self.name == other.name && self.config == other.config
    }
}

impl Eq for AuthProviderRef {}

impl std::fmt::Debug for AuthProviderRef {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AuthProviderRef")
            .field("name", &self.name)
            .field("config", &self.config)
            .field("resolved", &self.resolved)
            .finish_non_exhaustive()
    }
}

struct MintedProviderToken {
    token: String,
    /// Handed back to the command on the next run; never sent on the wire.
    refresh_token: Option<String>,
    /// Drives the 401 fresh-mint guard.
    minted_at: std::time::Instant,
    expires_at: Option<chrono::DateTime<chrono::Utc>>,
    /// The table version that minted the token; a different version reads as
    /// stale (see [`token_identity`]), so edits re-mint.
    minted_with: AuthProviderConfig,
}

/// The async lock is held across the command run, single-flighting mints
/// per provider name (shared across sessions). This dedupes concurrent
/// successes; a persistently failing helper is retried per waiter, each bounded
/// by the timeout clamp.
type ProviderSlot = std::sync::Arc<tokio::sync::Mutex<Option<MintedProviderToken>>>;

/// Pre-refresh margin: re-mint when the token expires within this window.
pub(crate) const PROVIDER_TOKEN_EXPIRY_SKEW_SECS: u64 = 60;
/// The effective mint timeout is clamped to `[1, this]`. A configured value
/// outside the range is honored up to the bound and draws a parse warning,
/// since a turn waits on the mint.
pub(crate) const PROVIDER_TIMEOUT_CEILING_SECS: u64 = 600;
 // 1 MiB
 // 64 KiB

