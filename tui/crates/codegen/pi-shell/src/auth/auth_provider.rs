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

/// Pre-refresh margin: re-mint when the token expires within this window.
pub(crate) const PROVIDER_TOKEN_EXPIRY_SKEW_SECS: u64 = 60;
/// The effective mint timeout is clamped to `[1, this]`. A configured value
/// outside the range is honored up to the bound and draws a parse warning,
/// since a turn waits on the mint.
pub(crate) const PROVIDER_TIMEOUT_CEILING_SECS: u64 = 600;
// 1 MiB
// 64 KiB
