//! `SessionHandle` — the `Clone + Send` proxy for interacting with a session actor.
//!
//! Callers hold a `SessionHandle` and send `SessionCommand` messages via the
//! internal channel. Extracted from `acp_session.rs` to keep the actor
//! implementation focused on behaviour.
/// `_meta` key carrying [`SessionHandle::scheduler_background_loops`] on the
/// `session/new` and `session/load` responses. Defined here so the shell that
/// publishes it and the clients that read it share one spelling.
pub const SCHEDULER_BACKGROUND_LOOPS_META_KEY: &str = "x.ai/schedulerBackgroundLoops";
