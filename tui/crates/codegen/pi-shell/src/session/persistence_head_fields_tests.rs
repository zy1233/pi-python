use super::*;

#[test]
fn summary_deserializes_without_head_fields_backward_compat() {
    // Simulate an old summary.json that lacks head_commit/head_branch.
    let json = r#"{
            "info": { "id": "old-session", "cwd": "/tmp" },
            "session_summary": "",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "num_messages": 0,
            "num_chat_messages": 0,
            "current_model_id": "test-model"
        }"#;
    let summary: Summary = serde_json::from_str(json).unwrap();
    assert!(summary.head_commit.is_none());
    assert!(summary.head_branch.is_none());
}

#[test]
fn summary_relocation_metadata_is_backward_compatible() {
    let json = r#"{
            "info": { "id": "old-session", "cwd": "/tmp" },
            "session_summary": "",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "num_messages": 0,
            "num_chat_messages": 0,
            "current_model_id": "test-model"
        }"#;
    let summary: Summary = serde_json::from_str(json).unwrap();
    assert_eq!(summary.cwd_generation, 0);
    assert!(summary.previous_cwd.is_none());
    assert!(summary.pending_cwd_switch_reminder.is_none());
    assert_eq!(summary.cwd_switch_bookkeeping_generation, 0);

    let serialized = serde_json::to_value(summary).unwrap();
    for field in [
        "cwd_generation",
        "previous_cwd",
        "pending_cwd_switch_reminder",
        "cwd_switch_bookkeeping_generation",
    ] {
        assert!(serialized.get(field).is_none());
    }
}
