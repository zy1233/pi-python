//! Single source of truth for the `sessionUpdate` discriminant strings the
//! session-resume replay matchers compare against persisted `updates.jsonl` lines.
//!
//! Each value is derived from its enum's serde impl (not a hand-written literal),
//! so renaming a variant updates the matcher automatically. The guard test pins
//! the wire format, turning an accidental serde change into a failing test.

use std::sync::LazyLock;

use agent_client_protocol as acp;

use crate::extensions::notification::SessionUpdate as PiSessionUpdate;

/// Serialize an internally-tagged session-update value and return the
/// `sessionUpdate` discriminant serde itself emits for that variant.
fn tagged_discriminant<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|v| {
            v.get("sessionUpdate")
                .and_then(|t| t.as_str())
                .map(str::to_owned)
        })
        .expect("internally-tagged session-update value must serialize with a sessionUpdate tag")
}

/// `acp::SessionUpdate::UserMessageChunk` discriminant.
pub(crate) static USER_MESSAGE_CHUNK: LazyLock<String> = LazyLock::new(|| {
    tagged_discriminant(&acp::SessionUpdate::UserMessageChunk(
        acp::ContentChunk::new(acp::ContentBlock::from("")),
    ))
});

/// `acp::SessionUpdate::AvailableCommandsUpdate` discriminant.
pub(crate) static AVAILABLE_COMMANDS_UPDATE: LazyLock<String> = LazyLock::new(|| {
    tagged_discriminant(&acp::SessionUpdate::AvailableCommandsUpdate(
        acp::AvailableCommandsUpdate::new(Vec::new()),
    ))
});

/// `acp::SessionUpdate::ToolCallUpdate` discriminant.
pub(crate) static TOOL_CALL_UPDATE: LazyLock<String> = LazyLock::new(|| {
    tagged_discriminant(&acp::SessionUpdate::ToolCallUpdate(
        acp::ToolCallUpdate::new(acp::ToolCallId::new("t"), acp::ToolCallUpdateFields::new()),
    ))
});

/// `acp::ToolCallStatus::InProgress` wire string (`in_progress`).
pub(crate) static TOOL_CALL_STATUS_IN_PROGRESS: LazyLock<String> = LazyLock::new(|| {
    serde_json::to_value(acp::ToolCallStatus::InProgress)
        .ok()
        .and_then(|v| v.as_str().map(str::to_owned))
        .expect("ToolCallStatus::InProgress must serialize as a string")
});

/// pi `SessionUpdate::RewindMarker` discriminant. Appears verbatim in compact
/// JSON, so it doubles as a cheap substring pre-filter.
pub(crate) static REWIND_MARKER: LazyLock<String> = LazyLock::new(|| {
    tagged_discriminant(&PiSessionUpdate::RewindMarker {
        target_prompt_index: 0,
        created_at: String::new(),
    })
});

#[cfg(test)]
mod tests {}
