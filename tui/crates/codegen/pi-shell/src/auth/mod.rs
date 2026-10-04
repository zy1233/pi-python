pub(crate) mod attribution;
mod auth_provider;
mod config;
pub mod credential_provider;
#[path = "devbox_login_stub.rs"]
pub(crate) mod devbox_login;
pub mod error;
mod external_auth;
mod flow;
pub(crate) mod manager;
mod model;
pub mod oidc;
pub(crate) mod recovery;
pub(crate) mod refresh;
mod storage;
mod token_output;
pub(crate) mod token_type;
pub(crate) use auth_provider::{AuthProviderConfig, AuthProviderRef};
pub(crate) use auth_provider::{
    PROVIDER_TIMEOUT_CEILING_SECS, PROVIDER_TOKEN_EXPIRY_SKEW_SECS};
pub use config::{
    ForceLoginTeam, GrokComConfig, OAuth2ProviderConfig, OidcAuthConfig, PreferredAuthMethod,
    PI_OAUTH2_ISSUER, is_pi_oauth2_issuer, pi_oauth2_issuer,
};
pub(crate) use config::{
    force_login_team_from_env, force_login_team_from_requirements, resolve_force_login_team,
};
pub(crate) use external_auth::refresh_with_command;
mod meta;
pub(crate) use error::RefreshTokenError;
pub use manager::{AuthManager, shared_api_key_provider};
pub use meta::{AuthMeta, GateInfo};
pub use model::{AuthMode, GrokAuth, lookup_auth};
pub(crate) use model::{TOKEN_TTL, default_coding_data_retention_opt_out, is_expired};
pub use storage::{clear_api_key, read_api_key, read_auth_json, store_api_key};
