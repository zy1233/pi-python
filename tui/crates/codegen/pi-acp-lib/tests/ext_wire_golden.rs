//! Pins how the pager decodes the pi session-update extension payloads
//! (the session-update notification, `PromptBlockMeta`, `_meta` maps). The golden file was written by
//! the code before the types moved out of `pi-shell`; this test must give the same file after.
//!
//! Regenerate (only on purpose): `UPDATE_GOLDEN=1 cargo test -p <crate> --test ext_wire_golden`.

use pi_acp_lib::extensions::notification::{
    self as n, SessionNotification, is_reauthable_failure, ticks_to_usd,
};
use pi_acp_lib::extensions::prompt_meta::PromptBlockMeta;
use serde_json::{Value, json};

fn envelope(update: Value) -> Value {
    json!({ "sessionId": "sess-1", "update": update })
}

fn upd(name: &str, fields: Value) -> Value {
    let mut obj = fields.as_object().cloned().unwrap_or_default();
    obj.insert("sessionUpdate".into(), json!(name));
    envelope(Value::Object(obj))
}

fn samples() -> Vec<(&'static str, Value)> {
    vec![
        (
            "auto_compact_started",
            upd(
                "auto_compact_started",
                json!({"tokens_used": 164000, "context_window": 200000, "percentage": 82, "reason": "threshold"}),
            ),
        ),
        (
            "auto_compact_completed_old_shell",
            upd(
                "auto_compact_completed",
                json!({"tokens_after": 40000, "summary_preview": null}),
            ),
        ),
        (
            "auto_compact_completed_full",
            upd(
                "auto_compact_completed",
                json!({"tokens_before": 170000, "tokens_after": 40000, "elapsed_ms": 4200, "summary_preview": "We fixed"}),
            ),
        ),
        (
            "auto_compact_failed",
            upd("auto_compact_failed", json!({"error": "boom"})),
        ),
        (
            "auto_compact_cancelled",
            upd(
                "auto_compact_cancelled",
                json!({"reason": "user_cancelled"}),
            ),
        ),
        (
            "auto_continue_completed",
            upd("auto_continue_completed", json!({"total_tokens": 1234})),
        ),
        (
            "memory_flush_started",
            upd("memory_flush_started", json!({})),
        ),
        (
            "memory_flush_completed",
            upd(
                "memory_flush_completed",
                json!({"result": "ok", "path": "/tmp/m.md"}),
            ),
        ),
        (
            "memory_dream_completed_no_path",
            upd("memory_dream_completed", json!({"result": "ok"})),
        ),
        (
            "memory_session_saved",
            upd("memory_session_saved", json!({"path": "/tmp/s.md"})),
        ),
        (
            "retry_retrying",
            upd(
                "retry_state",
                json!({"type": "retrying", "attempt": 2, "max_retries": 5, "reason": "overloaded"}),
            ),
        ),
        (
            "retry_exhausted_default_flag",
            upd(
                "retry_state",
                json!({"type": "exhausted", "attempts": 5, "reason": "overloaded"}),
            ),
        ),
        (
            "retry_failed_disk_full",
            upd(
                "retry_state",
                json!({"type": "failed", "error_type": n::DISK_FULL_ERROR_TYPE, "message": n::DISK_FULL_USER_MESSAGE}),
            ),
        ),
        (
            "auto_recovery_started",
            upd(
                "auto_recovery_started",
                json!({"attempt": 1, "max_retries": 3, "error": "e", "delay_ms": 500}),
            ),
        ),
        (
            "auto_recovery_exhausted",
            upd(
                "auto_recovery_exhausted",
                json!({"attempts": 3, "error": "e"}),
            ),
        ),
        (
            "relay_connected",
            upd(
                "relay_sync_status",
                json!({"status": "connected", "share_url": "https://example.test/s"}),
            ),
        ),
        (
            "relay_disconnected",
            upd("relay_sync_status", json!({"status": "disconnected"})),
        ),
        (
            "feedback_request",
            upd(
                "feedback_request",
                json!({"request_id": "r1", "tier": "tier1", "prompt": "How?", "dismissible": true,
                       "trigger_type": "t", "trigger_condition": "c", "trigger_reason": "r",
                       "stars": true, "thumbs": false, "text": true}),
            ),
        ),
        (
            "session_summary_generated",
            upd(
                "session_summary_generated",
                json!({"session_summary": "Fix the flaky test"}),
            ),
        ),
        (
            "session_recap",
            upd(
                "session_recap",
                json!({"summary": "Did things", "auto": true}),
            ),
        ),
        (
            "session_recap_default_auto",
            upd("session_recap", json!({"summary": "Did things"})),
        ),
        (
            "session_recap_unavailable",
            upd("session_recap_unavailable", json!({})),
        ),
        (
            "last_turn_summary",
            upd(
                "last_turn_summary",
                json!({"summary": "s", "prompt_id": "p1"}),
            ),
        ),
        (
            "model_auto_switched",
            upd(
                "model_auto_switched",
                json!({"previous_model_id": "a", "new_model_id": "b", "reason": "limit"}),
            ),
        ),
        (
            "model_changed",
            upd(
                "model_changed",
                json!({"model_id": "m", "reasoning_effort": "high"}),
            ),
        ),
        (
            "tool_call_delta_chunk",
            upd(
                "tool_call_delta_chunk",
                json!({"tool_call_id": "tc1", "tool_index": 0, "name": "bash", "arguments_delta": "{\"c"}),
            ),
        ),
        (
            "image_compressed",
            upd(
                "image_compressed",
                json!({"images": [{"index": 0, "original_bytes": 9000, "compressed_bytes": 3000,
                                   "original_width": 4000, "original_height": 3000,
                                   "compressed_width": 2000, "compressed_height": 1500}],
                       "message": "Compressed 1 image"}),
            ),
        ),
        (
            "image_dropped",
            upd("image_dropped", json!({"notes": ["too large"]})),
        ),
        (
            "memory_files",
            upd(
                "memory_files",
                json!({"files": [{"path": "/m", "source": "project", "size_bytes": 12, "modified_epoch_secs": 1700000000}]}),
            ),
        ),
        (
            "pending_interaction_permission",
            upd(
                "pending_interaction",
                json!({"tool_call_id": "tc9", "kind": "permission"}),
            ),
        ),
        (
            "pending_interaction_plan_approval",
            upd(
                "pending_interaction",
                json!({"tool_call_id": "tc9", "kind": "plan_approval"}),
            ),
        ),
        (
            "interaction_resolved",
            upd("interaction_resolved", json!({"tool_call_id": "tc9"})),
        ),
        (
            "turn_completed_with_usage",
            upd(
                "turn_completed",
                json!({"prompt_id": "p1", "stop_reason": "end_turn", "agent_result": "done",
                       "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120,
                                 "cachedReadTokens": 50, "cacheCreationTokens": 0,
                                 "reasoningTokens": 5, "modelCalls": 2, "apiDurationMs": 900,
                                 "costUsdTicks": 12345, "costIsPartial": true,
                                 "modelUsage": {"m": {"inputTokens": 100}},
                                 "numTurns": 3, "usageIsIncomplete": true}}),
            ),
        ),
        (
            "turn_completed_minimal",
            upd(
                "turn_completed",
                json!({"prompt_id": "p2", "stop_reason": "cancelled"}),
            ),
        ),
        (
            "response_started",
            upd(
                "response_started",
                json!({"message_id": "m1", "model": "x", "input_tokens": 3}),
            ),
        ),
        (
            "reasoning_completed",
            upd("reasoning_completed", json!({"signature": "sig"})),
        ),
        (
            "response_completed",
            upd(
                "response_completed",
                json!({"message_id": "m1", "stop_reason": "end_turn",
                       "usage": {"input_tokens": 1, "output_tokens": 2,
                                 "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4,
                                 "reasoning_tokens": 5}}),
            ),
        ),
        (
            "task_backgrounded",
            upd(
                "task_backgrounded",
                json!({"tool_call_id": "tc", "task_id": "t1", "command": "sleep 9", "cwd": "/w",
                       "output_file": "/tmp/o"}),
            ),
        ),
        (
            "scheduled_task_created",
            upd(
                "scheduled_task_created",
                json!({"task_id": "t", "prompt": "p", "human_schedule": "daily", "next_fire_at": null}),
            ),
        ),
        (
            "scheduled_task_deleted_default_reason",
            upd("scheduled_task_deleted", json!({"task_id": "t"})),
        ),
        (
            "monitor_event",
            upd(
                "monitor_event",
                json!({"task_id": "t", "description": "d", "event_text": "e"}),
            ),
        ),
        (
            "subagent_spawned",
            upd(
                "subagent_spawned",
                json!({"subagent_id": "sa", "parent_session_id": "ps", "child_session_id": "cs",
                       "subagent_type": "explore", "description": "look"}),
            ),
        ),
        (
            "subagent_finished",
            upd(
                "subagent_finished",
                json!({"subagent_id": "sa", "child_session_id": "cs", "status": "completed",
                       "tool_calls": 3, "turns": 2, "duration_ms": 10}),
            ),
        ),
        (
            "rewind_marker",
            upd(
                "rewind_marker",
                json!({"target_prompt_index": 2, "created_at": "2026-11-02T00:00:00Z"}),
            ),
        ),
        (
            "workflow_updated_minimal",
            upd(
                "workflow_updated",
                json!({"run_id": "w", "name": "n", "objective": "o", "status": "running", "elapsed_ms": 5}),
            ),
        ),
        (
            "goal_updated_minimal",
            upd(
                "goal_updated",
                json!({"goal_id": "g", "objective": "o", "status": "active", "phase": "work",
                       "elapsed_ms": 5, "total_deliverables": 0, "completed_deliverables": 0,
                       "total_worker_rounds": 1, "total_verify_rounds": 0,
                       "last_classifier_verdict": "not_achieved"}),
            ),
        ),
        (
            "diff_review",
            upd(
                "diff_review",
                json!({"content": [{"type": "diff", "path": "/a.rs", "oldText": "a", "newText": "b"}]}),
            ),
        ),
        (
            "unknown_variant_falls_back",
            upd("a_variant_from_the_future", json!({"x": 1})),
        ),
        (
            "meta_title_is_manual_false",
            json!({"sessionId": "s", "update": {"sessionUpdate": "session_summary_generated",
                   "session_summary": "t"}, "_meta": {"titleIsManual": false}}),
        ),
        (
            "meta_with_prompt_id_and_extra_keys",
            json!({"sessionId": "s", "update": {"sessionUpdate": "memory_flush_started"},
                   "_meta": {"promptId": "p1", "vendor/unknown": [1, 2]}}),
        ),
        (
            "bad_missing_required_field",
            upd("auto_compact_started", json!({"tokens_used": 1})),
        ),
        ("bad_not_an_object", json!("nope")),
    ]
}

fn collect() -> Value {
    let mut out = Vec::new();
    for (name, input) in samples() {
        let decoded = serde_json::from_value::<SessionNotification>(input.clone());
        let entry = match decoded {
            Ok(d) => json!({
                "name": name,
                "decoded": true,
                "debug": format!("{:?}", d),
                "reserialized": serde_json::to_value(&d).unwrap(),
            }),
            Err(_) => json!({ "name": name, "decoded": false }),
        };
        out.push(entry);
    }

    let prompt_meta: Vec<Value> = [
        json!({"bash_command": "ls -la"}),
        json!({}),
        json!({"bash_command": null}),
        json!({"bash_command": "x", "other": 1}),
        json!({"bash_command": 7}),
    ]
    .into_iter()
    .map(
        |v| match serde_json::from_value::<PromptBlockMeta>(v.clone()) {
            Ok(m) => json!({ "input": v, "debug": format!("{:?}", m),
                         "reserialized": serde_json::to_value(&m).unwrap() }),
            Err(_) => json!({ "input": v, "decoded": false }),
        },
    )
    .collect();

    let usage_in =
        json!({"inputTokens": 7, "modelUsage": {"a": {"outputTokens": 2}}, "numTurns": 1});
    let usage: n::PromptUsage = serde_json::from_value(usage_in.clone()).unwrap();

    json!({
        "session_updates": out,
        "prompt_block_meta": prompt_meta,
        "prompt_usage": { "input": usage_in, "debug": format!("{:?}", usage),
                          "reserialized": serde_json::to_value(&usage).unwrap() },
        "constants": {
            "title_is_manual_meta_key_suffix": n::TITLE_IS_MANUAL_META_KEY.rsplit('/').next(),
            "disk_full_error_type": n::DISK_FULL_ERROR_TYPE,
            "disk_full_user_message": n::DISK_FULL_USER_MESSAGE,
        },
        "is_reauthable_failure": [
            is_reauthable_failure(Some("auth"), ""),
            is_reauthable_failure(Some("legacy_auth"), ""),
            is_reauthable_failure(Some("auth_transient"), "Unauthorized (401)"),
            is_reauthable_failure(None, "Unauthorized (401)"),
            is_reauthable_failure(None, "other"),
        ],
        "ticks_to_usd": [ticks_to_usd(0), ticks_to_usd(10_000_000_000), ticks_to_usd(-5_000_000_000)],
    })
}

#[test]
fn ext_wire_decoding_matches_golden_file() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/ext_wire_golden.json"
    );
    let actual = serde_json::to_string_pretty(&collect()).unwrap() + "\n";
    if std::env::var_os("UPDATE_GOLDEN").is_some() {
        std::fs::write(path, &actual).unwrap();
        return;
    }
    let expected = std::fs::read_to_string(path).expect("golden file");
    assert_eq!(actual, expected, "extension wire decoding changed");
}
