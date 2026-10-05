//! Feedback, remember-note, btw, and recap dispatchers.


/// Toast when a manual `/recap` produces no summary. Empty sessions get a clear
/// empty-state message; anything else (model failure, empty summary, etc.) keeps
/// the generic failure toast.
pub(crate) fn recap_unavailable_toast(has_user_messages: bool) -> &'static str {
    if has_user_messages {
        "Couldn't generate recap"
    } else {
        "No messages yet"
    }
}

/// Whether scrollback already has a user prompt. Scans entries (not
/// `turn_count`) so it stays correct during `begin_batch`/`end_batch` session
/// load, when `push` defers `rebuild_turns` and `turn_count` can stay 0 while
/// replayed prompts are already present.
pub(crate) fn scrollback_has_user_messages(
    scrollback: &crate::scrollback::state::ScrollbackState,
) -> bool {
    scrollback
        .iter_entries()
        .any(|(_, entry)| entry.block.is_user_prompt())
}

// TaskResult handlers.

