//! Wire types for `pi/session/steer` (ACP extension registry X9) and
//! `pi/capabilities` advertised in `InitializeResponse._meta`.

use agent_client_protocol as acp;
use serde::{Deserialize, Serialize};

/// ACP extension request name for mid-turn steering (wire name `_pi/session/steer`).
pub const STEER_METHOD: &str = "pi/session/steer";

/// ACP extension notification name for pending steer queue updates (wire name `_pi/queue_update`).
pub const QUEUE_UPDATE_NOTIFICATION: &str = "pi/queue_update";

/// `_meta` key on `InitializeResponse` advertising optional `pi/*` capabilities.
pub const CAPABILITIES_META_KEY: &str = "pi/capabilities";

/// `_meta` key on a live `user_message_chunk` indicating mid-turn steer injection.
pub const STEERED_META_KEY: &str = "pi/steered";

/// `_meta` key on a live `user_message_chunk` carrying the injected steer identifier.
pub const STEER_ID_META_KEY: &str = "pi/steerId";

/// Optional `pi/*` capabilities advertised by the agent in `InitializeResponse._meta["pi/capabilities"]`.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PiCapabilities {
    #[serde(default)]
    pub steer: bool,
    #[serde(default)]
    pub btw: bool,
}

impl PiCapabilities {
    /// Parse `pi/capabilities` from `InitializeResponse.meta`.
    /// Older agents omit the key; every capability defaults to `false`.
    pub fn from_meta(meta: Option<&acp::Meta>) -> Self {
        let Some(caps) = meta
            .and_then(|m| m.get(CAPABILITIES_META_KEY))
            .and_then(|v| v.as_object())
        else {
            return Self::default();
        };
        Self {
            steer: caps.get("steer").and_then(|v| v.as_bool()).unwrap_or(false),
            btw: caps.get("btw").and_then(|v| v.as_bool()).unwrap_or(false),
        }
    }
}

/// Request payload for `pi/session/steer`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SteerSessionRequest {
    pub session_id: String,
    pub prompt: Vec<acp::ContentBlock>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub steer_id: Option<String>,
}

/// Response payload for `pi/session/steer`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SteerSessionResponse {
    pub session_id: String,
    #[serde(default)]
    pub steer_id: String,
    pub queued: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub queue_length: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// One queued steering item in `pi/queue_update`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SteerQueueItem {
    #[serde(default)]
    pub steer_id: String,
    #[serde(default)]
    pub preview: String,
}

/// Notification payload for `pi/queue_update`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QueueUpdateNotification {
    pub session_id: String,
    #[serde(default)]
    pub steer: Vec<SteerQueueItem>,
    #[serde(default)]
    pub cleared_by_abort: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub cleared_steer_ids: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn capabilities_from_meta_handles_absent_and_present() {
        assert_eq!(PiCapabilities::from_meta(None), PiCapabilities::default());
        let empty = serde_json::Map::new();
        assert_eq!(
            PiCapabilities::from_meta(Some(&empty)),
            PiCapabilities::default()
        );
        let full = json!({ "pi/capabilities": { "steer": true, "btw": true } })
            .as_object()
            .unwrap()
            .clone();
        assert_eq!(
            PiCapabilities::from_meta(Some(&full)),
            PiCapabilities {
                steer: true,
                btw: true
            }
        );
    }

    #[test]
    fn steer_request_and_response_roundtrip() {
        let req = SteerSessionRequest {
            session_id: "sess-1".into(),
            prompt: vec![acp::ContentBlock::Text(acp::TextContent::new(
                "switch to pytest",
            ))],
            steer_id: Some("st-1".into()),
        };
        let value = serde_json::to_value(&req).unwrap();
        assert_eq!(value["sessionId"], "sess-1");
        assert_eq!(value["steerId"], "st-1");

        let resp_json = json!({
            "sessionId": "sess-1",
            "steerId": "st-1",
            "queued": true,
            "queueLength": 1,
        });
        let resp: SteerSessionResponse = serde_json::from_value(resp_json).unwrap();
        assert!(resp.queued);
        assert_eq!(resp.queue_length, Some(1));
        assert_eq!(resp.reason, None);

        let idle_json = json!({
            "sessionId": "sess-1",
            "steerId": "st-2",
            "queued": false,
            "reason": "idle",
        });
        let idle: SteerSessionResponse = serde_json::from_value(idle_json).unwrap();
        assert!(!idle.queued);
        assert_eq!(idle.reason.as_deref(), Some("idle"));
    }

    #[test]
    fn queue_update_notification_roundtrip() {
        let raw = json!({
            "sessionId": "sess-1",
            "steer": [{ "steerId": "st-1", "preview": "use pytest" }],
            "clearedByAbort": false,
        });
        let parsed: QueueUpdateNotification = serde_json::from_value(raw).unwrap();
        assert_eq!(parsed.session_id, "sess-1");
        assert_eq!(parsed.steer.len(), 1);
        assert_eq!(parsed.steer[0].steer_id, "st-1");
        assert!(!parsed.cleared_by_abort);
        assert!(parsed.cleared_steer_ids.is_empty());

        let abort_raw = json!({
            "sessionId": "sess-1",
            "steer": [],
            "clearedByAbort": true,
            "clearedSteerIds": ["st-1"],
        });
        let aborted: QueueUpdateNotification = serde_json::from_value(abort_raw).unwrap();
        assert!(aborted.cleared_by_abort);
        assert_eq!(aborted.cleared_steer_ids, vec!["st-1"]);
    }
}
