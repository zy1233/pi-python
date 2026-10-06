//! Compacts the current conversation and generates a summary of the conversation which
//! gets passed to the next turn of the model

// Re-export compaction utilities from pi-chat-state so existing callers
// that import from this module continue to work.

// Single definition in the sampling layer so the sampler's turn-request retry and
// compaction's retry loop agree on size detection.

/// Tests that reconstruct the compacted conversation history exactly as
/// `run_compact` in `acp_session.rs` assembles it, so we can inspect the
/// raw strings of every user message and verify the formatting.
///
/// The compaction summary is wrapped in `<user_query>` tags (consistent with
/// normal user messages), and `<system-reminder>` state context is placed
/// outside, matching the standard format:
///   `<user_query>...summary...</user_query>\n\n<system-reminder>...</system-reminder>`
#[cfg(test)]
#[path = "session_compact_compacted_history_shape_tests.rs"]
mod compacted_history_shape_tests;
