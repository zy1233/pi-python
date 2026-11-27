//! Wire types for `pi/session/btw` and `pi/session/btw_cancel` (ACP extension registry X10).

use agent_client_protocol as acp;
use serde::{Deserialize, Serialize};

/// ACP extension request name for ephemeral side chat (wire name `_pi/session/btw`).
pub const BTW_METHOD: &str = "pi/session/btw";

/// ACP extension request name to cancel an in-flight side chat (wire name `_pi/session/btw_cancel`).
pub const BTW_CANCEL_METHOD: &str = "pi/session/btw_cancel";

/// ACP extension notification name for streaming side-chat deltas (wire name `_pi/btw_update`).
pub const BTW_UPDATE_NOTIFICATION: &str = "pi/btw_update";

/// Role in the optional client-side multi-turn BTW history.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BtwHistoryRole {
    User,
    Assistant,
}

/// One previous turn inside the ephemeral BTW side-chat overlay.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BtwHistoryTurn {
    pub role: BtwHistoryRole,
    pub text: String,
}

/// Request payload for `pi/session/btw`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BtwRequest {
    pub session_id: String,
    pub btw_id: String,
    pub prompt: Vec<acp::ContentBlock>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub history: Vec<BtwHistoryTurn>,
}

/// Response payload for `pi/session/btw`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BtwResponse {
    pub session_id: String,
    pub btw_id: String,
    pub stop_reason: String,
    #[serde(default)]
    pub error: Option<String>,
}

/// Request payload for `pi/session/btw_cancel`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BtwCancelRequest {
    pub session_id: String,
    pub btw_id: String,
}

/// Response payload for `pi/session/btw_cancel`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BtwCancelResponse {
    pub session_id: String,
    pub btw_id: String,
    pub cancelled: bool,
}

/// Kind of streaming notification in `pi/btw_update`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BtwUpdateKind {
    ThoughtDelta,
    TextDelta,
    Done,
    Error,
}

/// Notification payload for `pi/btw_update`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BtwUpdateNotification {
    pub session_id: String,
    pub btw_id: String,
    pub kind: BtwUpdateKind,
    #[serde(default)]
    pub delta: String,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub stop_reason: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn btw_request_and_response_roundtrip() {
        let req = BtwRequest {
            session_id: "sess-1".into(),
            btw_id: "btw-1".into(),
            prompt: vec![acp::ContentBlock::Text(acp::TextContent::new(
                "what does foo do?",
            ))],
            history: vec![
                BtwHistoryTurn {
                    role: BtwHistoryRole::User,
                    text: "hi".into(),
                },
                BtwHistoryTurn {
                    role: BtwHistoryRole::Assistant,
                    text: "hello".into(),
                },
            ],
        };
        let value = serde_json::to_value(&req).unwrap();
        assert_eq!(value["sessionId"], "sess-1");
        assert_eq!(value["btwId"], "btw-1");
        assert_eq!(value["history"][0]["role"], "user");
        assert_eq!(value["history"][1]["role"], "assistant");

        let resp: BtwResponse = serde_json::from_value(json!({
            "sessionId": "sess-1",
            "btwId": "btw-1",
            "stopReason": "end_turn",
            "error": null,
        }))
        .unwrap();
        assert_eq!(resp.stop_reason, "end_turn");
        assert_eq!(resp.error, None);
    }

    #[test]
    fn btw_update_and_cancel_roundtrip() {
        let update: BtwUpdateNotification = serde_json::from_value(json!({
            "sessionId": "sess-1",
            "btwId": "btw-1",
            "kind": "text_delta",
            "delta": "hello ",
            "error": null,
        }))
        .unwrap();
        assert_eq!(update.kind, BtwUpdateKind::TextDelta);
        assert_eq!(update.delta, "hello ");

        let done: BtwUpdateNotification = serde_json::from_value(json!({
            "sessionId": "sess-1",
            "btwId": "btw-1",
            "kind": "done",
            "delta": "",
            "error": null,
            "stopReason": "cancelled",
        }))
        .unwrap();
        assert_eq!(done.kind, BtwUpdateKind::Done);
        assert_eq!(done.stop_reason.as_deref(), Some("cancelled"));

        let cancel_req = BtwCancelRequest {
            session_id: "sess-1".into(),
            btw_id: "btw-1".into(),
        };
        let cancel_val = serde_json::to_value(&cancel_req).unwrap();
        assert_eq!(cancel_val["btwId"], "btw-1");

        let cancel_resp: BtwCancelResponse = serde_json::from_value(json!({
            "sessionId": "sess-1",
            "btwId": "btw-1",
            "cancelled": true,
        }))
        .unwrap();
        assert!(cancel_resp.cancelled);
    }
}
