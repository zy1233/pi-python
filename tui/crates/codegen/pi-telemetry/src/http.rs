//! Origin/client identification used by the telemetry engine.
//!
//! [`OriginClientInfo`] identifies the client that originated a request (used
//! for User-Agent rendering and event labelling). It is defined here, in a
//! leaf crate, so neither the telemetry engine nor the HTTP helpers depend on
//! shell internals.

use serde::{Deserialize, Serialize};

/// Identity of the client that originated the request, used for
/// User-Agent rendering. The shell layer composes this with platform
/// info into a final UA string.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OriginClientInfo {
    pub product: String,
    pub version: Option<String>,
}

/// Construct an [`OriginClientInfo`] from `GROK_CLIENT_NAME` /
/// `GROK_CLIENT_VERSION` env vars. Returns `None` when `GROK_CLIENT_NAME`
/// is unset. Free function (not an inherent method) because the type lives
/// in another crate.
pub fn origin_client_info_from_env() -> Option<OriginClientInfo> {
    std::env::var("GROK_CLIENT_NAME")
        .ok()
        .map(|product| OriginClientInfo {
            product,
            version: std::env::var("GROK_CLIENT_VERSION").ok(),
        })
}
