//! Session actor command enum and associated public types.
//!
//! `SessionCommand` defines the message protocol used to drive a session
//! actor. It was extracted from `acp_session.rs` to keep the actor
//! implementation focused on behaviour.
/// `_meta.cancellationCategory` of a hook-denied cancel; the pager matches it
/// to render the blocked-by-a-hook marker.
pub const HOOK_DENIED_CATEGORY: &str = "HookDenied";
#[cfg(test)]
mod cancellation_category_meta_tests {
        
}
#[cfg(test)]
mod cancel_trigger_tests {
    }
