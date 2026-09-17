//! Legacy grok-build vendor extension namespace (defensive filtering only).
//!
//! Standard ACP agents (pi-agent-cli) never emit these methods; the pager
//! drops them at ingress. Constants are centralized here so the rest of the
//! codebase does not scatter vendor prefix strings.

/// Legacy vendor extension method prefix (`legacy ext RPC`).
pub const VENDOR_EXT_PREFIX: &str = "x.ai/";

/// Replay-path variant with leading underscore (`_legacy ext RPC`).
pub const VENDOR_EXT_PREFIX_ALT: &str = "_x.ai/";

/// Return true when `method` belongs to the legacy vendor extension namespace.
pub fn is_vendor_ext_method(method: &str) -> bool {
    method.starts_with(VENDOR_EXT_PREFIX) || method.starts_with(VENDOR_EXT_PREFIX_ALT)
}

/// Return true when `key` is a legacy vendor `_meta` key.
pub fn is_vendor_meta_key(key: &str) -> bool {
    key.starts_with(VENDOR_EXT_PREFIX) || key.starts_with(VENDOR_EXT_PREFIX_ALT)
}

/// Legacy session notification ext method (replay barrier classification only).
pub fn session_notification_method() -> &'static str {
    "x.ai/session_notification"
}

/// Legacy session update ext method (replay barrier classification only).
pub fn session_update_method() -> &'static str {
    "x.ai/session/update"
}
