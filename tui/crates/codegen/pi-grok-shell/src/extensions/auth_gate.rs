use agent_client_protocol as acp;

use crate::auth::{AuthManager, GrokAuth};

/// Require pi auth from a sync context, accepting tokens in the client-side buffer window.
pub(crate) fn require_pi_auth(
    auth_manager: &AuthManager,
    missing_message: &'static str,
    non_pi_message: &'static str,
) -> Result<GrokAuth, acp::Error> {
    let auth = auth_manager
        .current_or_expired()
        .ok_or_else(|| acp::Error::auth_required().data(missing_message))?;
    if !auth.is_pi_auth() {
        return Err(acp::Error::auth_required().data(non_pi_message));
    }
    Ok(auth)
}
