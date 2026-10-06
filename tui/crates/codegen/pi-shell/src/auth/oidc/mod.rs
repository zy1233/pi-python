//! OIDC authentication: protocol, login, and refresh submodules.

pub(crate) mod protocol;
pub(crate) mod refresh;

pub(crate) use protocol::{
    enforce_login_principal, login_principal_policy, peek_access_token_principal_id,
};
pub(crate) use refresh::{OidcRefreshResult, oidc_token_exchange};
