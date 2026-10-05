//! Tests for feedback / remember / btw / recap dispatchers.

use crate::app::dispatch::recap_unavailable_toast;

#[test]
fn recap_unavailable_toast_empty_vs_with_messages() {
    assert_eq!(recap_unavailable_toast(false), "No messages yet");
    assert_eq!(recap_unavailable_toast(true), "Couldn't generate recap");
}
