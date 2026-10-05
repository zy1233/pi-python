//! Behavior tests for session transcript replay peeks, prepare, stream, and
//! child path lookup.

use agent_client_protocol as acp;

use super::replay::{
    ReplayLookupFallback, ReplayPathHint, ReplayToolCollapser, ReplayedUpdate,
    line_is_available_commands_update, line_is_dropped_on_replay,
    line_is_in_progress_tool_call_update, prepare_replay_lines, replay_would_emit,
    resolve_replay_updates_path, stream_replay_updates_at, stream_replay_updates_at_hinted};
use super::{
    PromptExtractEvent, ReplayEmission, SUMMARY_FILE, SessionUpdate, SessionUpdateEnvelope,
    UPDATES_FILE, filter_rewind_lines, parse_prompt_extract_event};
use crate::session::wire_tags::AVAILABLE_COMMANDS_UPDATE;

fn acp_envelope(session_update_json: &str) -> String {
    format!(
        r#"{{"timestamp":1,"method":"session/update","params":{{"sessionId":"s","update":{session_update_json}}}}}"#
    )
}

fn pi_envelope(session_update_json: &str) -> String {
    format!(
        r#"{{"timestamp":1,"method":"_x.ai/session/update","params":{{"sessionId":"s","update":{session_update_json}}}}}"#
    )
}

fn acp_envelope_with_meta(session_update_json: &str, meta_json: &str) -> String {
    format!(
        r#"{{"timestamp":1,"method":"session/update","params":{{"sessionId":"s","update":{session_update_json},"_meta":{meta_json}}}}}"#
    )
}

/// A session with no `updates.jsonl` streams nothing, so the emission gate
/// reports `Empty` and forwards no updates.
#[test]
fn stream_replay_updates_at_missing_session_is_empty() {
    let grok_home = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(grok_home.path().join("sessions")).unwrap();

    let mut count = 0usize;
    let emission =
        stream_replay_updates_at("does-not-exist", grok_home.path(), |_| count += 1).unwrap();

    assert_eq!(emission, ReplayEmission::Empty);
    assert_eq!(count, 0);
}

/// A resolvable session whose `updates.jsonl` cannot be read surfaces the
/// error rather than folding to `Empty`, so the caller logs a real fault
/// instead of mistaking it for an absent transcript. (The path is a
/// directory, which `read_to_string` rejects.)
#[test]
fn stream_replay_updates_at_surfaces_read_errors() {
    let grok_home = tempfile::tempdir().unwrap();
    let session_dir = grok_home.path().join("sessions").join("cwd").join("sess");
    std::fs::create_dir_all(&session_dir).unwrap();
    std::fs::write(session_dir.join(SUMMARY_FILE), "{}").unwrap();
    std::fs::create_dir(session_dir.join(UPDATES_FILE)).unwrap();

    let result = stream_replay_updates_at("sess", grok_home.path(), |_| {});
    assert!(
        result.is_err(),
        "read fault must surface, not fold to Empty: {result:?}"
    );
}

#[test]
fn prepare_replay_cursor_skips_to_position() {
    let u1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"old"}}"#,
        r#"{"eventId":"ev1"}"#,
    );
    let a1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"old resp"}}"#,
        r#"{"eventId":"ev2"}"#,
    );
    let u2 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"new"}}"#,
        r#"{"eventId":"ev3"}"#,
    );
    let raw = format!("{u1}\n{a1}\n{u2}\n");

    let prepared = prepare_replay_lines(&raw, Some("ev2"));
    // Should skip ev1 and ev2, return only ev3
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("new"));
}

#[test]
fn prepare_replay_cursor_not_found_returns_all() {
    let u1 = acp_envelope(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
    );
    let raw = format!("{u1}\n");

    let prepared = prepare_replay_lines(&raw, Some("nonexistent"));
    assert_eq!(prepared.lines.len(), 1);
}

/// A resolved cursor is refused when the tail contains an eventId-less
/// line (older-binary history): the line has no client-side dedup and no
/// future cursor can cover it, so an incremental tail would re-apply it.
/// Full replay is the safe fallback.
#[test]
fn prepare_replay_cursor_refused_when_tail_has_event_id_less_line() {
    let a1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"seen"}}"#,
        r#"{"eventId":"ev1"}"#,
    );
    // pi-style line persisted by an older binary: no _meta at all.
    let old_pi = r#"{"timestamp":2,"method":"_x.ai/session/update","params":{"sessionId":"s","update":{"sessionUpdate":"hook_annotation","message":"trailing"}}}"#;
    let raw = format!("{a1}\n{old_pi}\n");

    let prepared = prepare_replay_lines(&raw, Some("ev1"));
    assert_eq!(prepared.lines.len(), 2, "full history is replayed");

    // Same history with the trailing line stamped resolves incrementally.
    let new_pi = r#"{"timestamp":2,"method":"_x.ai/session/update","params":{"sessionId":"s","update":{"sessionUpdate":"hook_annotation","message":"trailing"},"_meta":{"eventId":"ev2"}}}"#;
    let raw = format!("{a1}\n{new_pi}\n");
    let prepared = prepare_replay_lines(&raw, Some("ev1"));
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("trailing"));

    // An id-less ACU in the tail is exempt from the refusal — ACUs are
    // dropped before forwarding, so they can never be re-applied.
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let raw = format!("{a1}\n{acu}\n");
    let prepared = prepare_replay_lines(&raw, Some("ev1"));
    assert!(
        prepared.lines.is_empty(),
        "the ACU is dropped, never forwarded"
    );
}

// ── available_commands_update skip (T1) + single-pass equivalence ─────────

#[test]
fn acu_line_detection_exact_and_no_false_positive() {
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    assert!(line_is_available_commands_update(&acu));

    // A user message that merely mentions the phrase must NOT match.
    let user_mentions = acp_envelope(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"what is available_commands_update?"}}"#,
    );
    assert!(!line_is_available_commands_update(&user_mentions));
}

/// The anchor must reject the discriminant when it sits inside `_meta` (not
/// at the `params.update` position) — the real update here is a non-ACU.
#[test]
fn acu_anchor_ignores_discriminant_in_meta() {
    let line = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"hi"}}"#,
        r#"{"sessionUpdate":"available_commands_update"}"#,
    );
    // The exact `"sessionUpdate":"available_commands_update"` substring IS
    // present (in _meta), but it's not anchored to `"update":{`.
    assert!(line.contains(r#""sessionUpdate":"available_commands_update""#));
    assert!(!line_is_available_commands_update(&line));
}

/// A NON-ACU line whose `_meta` embeds an ACU-shaped object must not be dropped:
/// typed peek reads `params.update.sessionUpdate`, not nested `_meta`.
#[test]
fn acu_confirm_rejects_nested_update_anchor_in_meta() {
    let line = acp_envelope_with_meta(
        r#"{"sessionUpdate":"tool_call","toolCallId":"t","title":"x"}"#,
        r#"{"echo":{"update":{"sessionUpdate":"available_commands_update","availableCommands":[]}}}"#,
    );
    assert!(line.contains(&*AVAILABLE_COMMANDS_UPDATE));
    assert!(!line_is_available_commands_update(&line));

    // And the non-ACU line survives replay (is not dropped).
    let raw = format!("{line}\n");
    let prepared = prepare_replay_lines(&raw, None);
    assert_eq!(prepared.lines.len(), 1, "non-ACU line must not be dropped");
    assert!(prepared.lines[0].contains("tool_call"));
}

/// Pin the cross-crate assumption behind [`line_is_available_commands_update`]:
/// the structural `params.update` serializes BEFORE the optional `_meta`. Run a
/// genuine ACU through the real write path ([`SessionUpdateEnvelope::from_update`])
/// and assert its first `"update":` precedes any `"_meta":`, and the detector accepts it.
#[test]
fn acu_real_write_path_serializes_update_before_meta() {
    let notif = acp::SessionNotification::new(
        acp::SessionId::new("s"),
        acp::SessionUpdate::AvailableCommandsUpdate(acp::AvailableCommandsUpdate::new(vec![])),
    )
    .meta(serde_json::json!({ "eventId": "ev1" }).as_object().cloned());
    let envelope =
        SessionUpdateEnvelope::from_update(&SessionUpdate::Acp(Box::new(notif))).unwrap();
    let line = serde_json::to_string(&envelope).unwrap();

    assert!(line_is_available_commands_update(&line));
}

#[test]
fn prepare_replay_drops_available_commands_update() {
    let u = acp_envelope(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
    );
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let a = acp_envelope(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"yo"}}"#,
    );
    let raw = format!("{u}\n{acu}\n{a}\n");

    let prepared = prepare_replay_lines(&raw, None);
    // ACU dropped; the two real updates kept in original order.
    assert_eq!(prepared.lines.len(), 2);
    assert!(
        prepared
            .lines
            .iter()
            .all(|l| !l.contains("available_commands_update"))
    );
    assert!(prepared.lines[0].contains("hi"));
    assert!(prepared.lines[1].contains("yo"));
}

#[test]
fn prepare_replay_rewind_truncates_and_drops_acu() {
    let u0 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"p0"}}"#,
        r#"{"totalTokens":5}"#,
    );
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let a0 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"a0"}}"#,
        r#"{"totalTokens":7}"#,
    );
    let rw = pi_envelope(
        r#"{"sessionUpdate":"rewind_marker","target_prompt_index":0,"created_at":"2024-01-01"}"#,
    );
    let u1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"p1"}}"#,
        r#"{"totalTokens":9}"#,
    );
    let raw = format!("{u0}\n{acu}\n{a0}\n{rw}\n{u1}\n");

    let prepared = prepare_replay_lines(&raw, None);
    // Rewind to 0 kills u0/a0; ACU dropped; only the new p1 survives.
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("p1"));
}

/// The single-pass implementation must match an independent reference that
/// drops ACU then applies the (canonical) rewind filter — for a mixed input.
#[test]
fn prepare_replay_single_pass_matches_reference() {
    let lines_src = [
        acp_envelope_with_meta(
            r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"p0"}}"#,
            r#"{"totalTokens":3}"#,
        ),
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#),
        acp_envelope_with_meta(
            r#"{"sessionUpdate":"tool_call_update","toolCallId":"t","status":"completed"}"#,
            r#"{"totalTokens":11}"#,
        ),
        acp_envelope(
            r#"{"sessionUpdate":"tool_call_update","toolCallId":"t","status":"in_progress"}"#,
        ),
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#),
        acp_envelope(
            r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"a0"}}"#,
        ),
    ];
    let raw = format!("{}\n", lines_src.join("\n"));

    let reference: Vec<&str> = filter_rewind_lines(
        raw.lines()
            .filter(|l| !l.trim().is_empty() && !line_is_dropped_on_replay(l))
            .collect(),
    );

    let prepared = prepare_replay_lines(&raw, None);
    assert_eq!(prepared.lines, reference);
}

/// The prompt-extract fast-reject must not be fooled by lines that merely
/// contain the discriminant substring inside their content — the full parse
/// still classifies them by the real `sessionUpdate` tag.
#[test]
fn fast_reject_handles_discriminant_substring_in_content() {
    let line = acp_envelope(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"the user_message_chunk format"}}"#,
    );
    assert_eq!(
        parse_prompt_extract_event(&line),
        PromptExtractEvent::NotUserMessage
    );
}

/// A user prompt whose text contains the literal escaped-JSON ACU
/// discriminant must NOT be dropped as an `available_commands_update` — the
/// `"update":{` anchor only matches the real structural discriminant, not the
/// escaped fragment in content.
#[test]
fn acu_drop_ignores_escaped_json_in_content() {
    let line = acp_envelope(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"paste: {\"sessionUpdate\":\"available_commands_update\"}"}}"#,
    );
    // The bare phrase appears in the (escaped) content, but it's not at the
    // structural `"update":{"sessionUpdate":...` position, so it's kept.
    assert!(line.contains("available_commands_update"));
    assert!(!line_is_available_commands_update(&line));

    let raw = format!("{line}\n");
    let prepared = prepare_replay_lines(&raw, None);
    assert_eq!(prepared.lines.len(), 1, "user prompt must survive replay");
    assert!(prepared.lines[0].contains("available_commands_update"));
}

/// An idle client reconnecting with the cursor pointing at the LAST persisted
/// event — an ACU (the post-load re-advertise) — must resolve the cursor on the
/// ACU-inclusive set rather than fall back to full replay.
#[test]
fn prepare_replay_cursor_on_dropped_acu_resolves() {
    let u = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
        r#"{"eventId":"ev1"}"#,
    );
    let a = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"yo"}}"#,
        r#"{"eventId":"ev2"}"#,
    );
    let acu = acp_envelope_with_meta(
        r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#,
        r#"{"eventId":"ev3"}"#,
    );
    let raw = format!("{u}\n{a}\n{acu}\n");

    // Cursor == the ACU's eventId → resolved; nothing after → no replay,
    // and crucially NOT a full replay.
    let prepared = prepare_replay_lines(&raw, Some("ev3"));
    assert!(prepared.lines.is_empty(), "client is already caught up");

    // Cursor == ev1 → replay ev2, ev3; the ACU (ev3) is dropped from the tail.
    let prepared = prepare_replay_lines(&raw, Some("ev1"));
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("yo"));
}

/// A trailing `rewind_marker` empties the live set and yields
/// `last_tokens == 0` (the `unwrap_or(0)` path).
#[test]
fn prepare_replay_trailing_rewind_marker_empties() {
    let u0 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"p0"}}"#,
        r#"{"totalTokens":5}"#,
    );
    let rw = pi_envelope(
        r#"{"sessionUpdate":"rewind_marker","target_prompt_index":0,"created_at":"2024-01-01"}"#,
    );
    let raw = format!("{u0}\n{rw}\n");
    let prepared = prepare_replay_lines(&raw, None);
    assert!(prepared.lines.is_empty());
}

/// An ACU as the final line is dropped without disturbing tokens.
#[test]
fn prepare_replay_trailing_acu_dropped() {
    let u = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
        r#"{"totalTokens":7}"#,
    );
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let raw = format!("{u}\n{acu}\n");
    let prepared = prepare_replay_lines(&raw, None);
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("hi"));
}

/// Rewind + cursor + ACU together, with explicit expected values.
#[test]
fn prepare_replay_rewind_then_cursor_with_acu() {
    let u0 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"p0"}}"#,
        r#"{"eventId":"e0","totalTokens":2}"#,
    );
    let a0 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"a0"}}"#,
        r#"{"eventId":"e1"}"#,
    );
    let acu0 =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let rw = pi_envelope(
        r#"{"sessionUpdate":"rewind_marker","target_prompt_index":0,"created_at":"2024-01-01"}"#,
    );
    let u1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"p1"}}"#,
        r#"{"eventId":"e2","totalTokens":9}"#,
    );
    let acu1 =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let a1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"a1"}}"#,
        r#"{"eventId":"e3","totalTokens":12}"#,
    );
    let raw = format!("{u0}\n{a0}\n{acu0}\n{rw}\n{u1}\n{acu1}\n{a1}\n");

    // Rewind to 0 kills u0/a0/acu0; surviving live = [u1(e2), acu1, a1(e3)].
    // Cursor on e2 → tail = [acu1, a1]; drop acu1 → lines = [a1].
    let prepared = prepare_replay_lines(&raw, Some("e2"));
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("a1"));
}

fn persist_acp_update(update: acp::SessionUpdate) -> String {
    persist_acp_update_with_meta(update, None)
}

fn persist_pi_update(update: crate::extensions::notification::SessionUpdate) -> String {
    let notif = crate::extensions::notification::SessionNotification {
        session_id: acp::SessionId::new("s"),
        update,
        meta: None,
    };
    let envelope =
        SessionUpdateEnvelope::from_update(&SessionUpdate::Pi(Box::new(notif))).unwrap();
    serde_json::to_string(&envelope).unwrap()
}

fn persist_acp_update_with_meta(
    update: acp::SessionUpdate,
    meta: Option<serde_json::Value>,
) -> String {
    let mut notif = acp::SessionNotification::new(acp::SessionId::new("s"), update);
    if let Some(meta) = meta {
        notif = notif.meta(meta.as_object().cloned());
    }
    let envelope =
        SessionUpdateEnvelope::from_update(&SessionUpdate::Acp(Box::new(notif))).unwrap();
    serde_json::to_string(&envelope).unwrap()
}

fn fat_in_progress_update() -> acp::SessionUpdate {
    acp::SessionUpdate::ToolCallUpdate(acp::ToolCallUpdate::new(
        acp::ToolCallId::new("t1"),
        acp::ToolCallUpdateFields::new()
            .status(Some(acp::ToolCallStatus::InProgress))
            .content(Some(vec![acp::ToolCallContent::from(
                acp::ContentBlock::Text(acp::TextContent::new("x".repeat(64 * 1024))),
            )]))
            .raw_output(Some(serde_json::json!({
                "type": "bash",
                "output": vec![1u8; 256],
            }))),
    ))
}

#[test]
fn in_progress_peek_uses_write_path_serde_shape() {
    let in_progress = persist_acp_update(fat_in_progress_update());
    assert!(
        line_is_in_progress_tool_call_update(&in_progress),
        "write-path InProgress with fat rawOutput must be skipped"
    );

    let spaced = in_progress.replace(r#""status":"in_progress""#, r#""status": "in_progress""#);
    assert!(
        line_is_in_progress_tool_call_update(&spaced),
        "pretty-spaced status must still peek as InProgress"
    );

    let reordered = format!(
        r#"{{"timestamp":1,"method":"session/update","params":{{"sessionId":"s","update":{{"toolCallId":"t1","status":"in_progress","sessionUpdate":"tool_call_update","content":[{{"type":"text","text":"x"}}]}}}}}}"#
    );
    assert!(
        line_is_in_progress_tool_call_update(&reordered),
        "sessionUpdate need not be first in the update object"
    );

    let completed = persist_acp_update(acp::SessionUpdate::ToolCallUpdate(
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new("t1"),
            acp::ToolCallUpdateFields::new().status(Some(acp::ToolCallStatus::Completed)),
        ),
    ));
    assert!(!line_is_in_progress_tool_call_update(&completed));

    let failed = persist_acp_update(acp::SessionUpdate::ToolCallUpdate(
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new("t1"),
            acp::ToolCallUpdateFields::new().status(Some(acp::ToolCallStatus::Failed)),
        ),
    ));
    assert!(!line_is_in_progress_tool_call_update(&failed));

    let start_meta = persist_acp_update(acp::SessionUpdate::ToolCallUpdate(
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new("t1"),
            acp::ToolCallUpdateFields::new().title(Some("bash ls".into())),
        ),
    ));
    assert!(!line_is_in_progress_tool_call_update(&start_meta));

    let tool_call = persist_acp_update(acp::SessionUpdate::ToolCall(
        acp::ToolCall::new(acp::ToolCallId::new("t1"), "bash")
            .status(acp::ToolCallStatus::InProgress),
    ));
    assert!(
        !line_is_in_progress_tool_call_update(&tool_call),
        "ToolCall (not tool_call_update) must be kept"
    );

    let prefix_trap = persist_acp_update(fat_in_progress_update()).replace(
        r#""status":"in_progress""#,
        r#""status":"in_progress_extended""#,
    );
    assert!(
        !line_is_in_progress_tool_call_update(&prefix_trap),
        "in_progress_extended must not match InProgress"
    );

    let user = persist_acp_update(acp::SessionUpdate::UserMessageChunk(
        acp::ContentChunk::new(acp::ContentBlock::from(
            r#"paste: {"sessionUpdate":"tool_call_update","status":"in_progress"}"#,
        )),
    ));
    assert!(
        !line_is_in_progress_tool_call_update(&user),
        "user content substring must not skip the line"
    );

    let nested = persist_acp_update_with_meta(
        acp::SessionUpdate::ToolCallUpdate(acp::ToolCallUpdate::new(
            acp::ToolCallId::new("t1"),
            acp::ToolCallUpdateFields::new()
                .status(Some(acp::ToolCallStatus::Completed))
                .raw_output(Some(serde_json::json!({
                    "echo": {"update": {"sessionUpdate": "tool_call_update", "status": "in_progress"}}
                }))),
        )),
        Some(serde_json::json!({
            "note": {"update": {"sessionUpdate": "tool_call_update", "status": "in_progress"}}
        })),
    );
    assert!(
        nested.contains("in_progress"),
        "fixture must embed nested InProgress"
    );
    assert!(!line_is_in_progress_tool_call_update(&nested));

    let acu = persist_acp_update(acp::SessionUpdate::AvailableCommandsUpdate(
        acp::AvailableCommandsUpdate::new(vec![]),
    ));
    let mixed = format!("{in_progress}\n{completed}\n{acu}\n");
    let prepared = prepare_replay_lines(&mixed, None);
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("completed"));
}

#[test]
fn prepare_replay_cursor_on_dropped_in_progress_resolves() {
    let u = acp_envelope_with_meta(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
        r#"{"eventId":"ev1"}"#,
    );
    let ip = acp_envelope_with_meta(
        r#"{"sessionUpdate":"tool_call_update","toolCallId":"t","status":"in_progress"}"#,
        r#"{"eventId":"ev2"}"#,
    );
    let a = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"yo"}}"#,
        r#"{"eventId":"ev3"}"#,
    );
    let raw = format!("{u}\n{ip}\n{a}\n");
    let prepared = prepare_replay_lines(&raw, Some("ev2"));
    assert_eq!(prepared.lines.len(), 1);
    assert!(prepared.lines[0].contains("yo"));
}

#[test]
fn prepare_replay_id_less_in_progress_in_tail_does_not_force_full_replay() {
    let a1 = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"seen"}}"#,
        r#"{"eventId":"ev1"}"#,
    );
    let ip = acp_envelope(
        r#"{"sessionUpdate":"tool_call_update","toolCallId":"t","status":"in_progress"}"#,
    );
    let raw = format!("{a1}\n{ip}\n");
    let prepared = prepare_replay_lines(&raw, Some("ev1"));
    assert!(prepared.lines.is_empty());
}

#[test]
fn stream_replay_collapses_tool_call_and_skips_in_progress() {
    let home = tempfile::tempdir().unwrap();
    let sid = "child-collapse";
    let dir = home.path().join("sessions").join("cwd").join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let tool = acp_envelope(
        r#"{"sessionUpdate":"tool_call","toolCallId":"t1","title":"bash","status":"pending"}"#,
    );
    let start =
        acp_envelope(r#"{"sessionUpdate":"tool_call_update","toolCallId":"t1","title":"bash ls"}"#);
    let ip = acp_envelope(
        r#"{"sessionUpdate":"tool_call_update","toolCallId":"t1","status":"in_progress"}"#,
    );
    let done = acp_envelope(
        r#"{"sessionUpdate":"tool_call_update","toolCallId":"t1","status":"completed"}"#,
    );
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    let raw = format!("{tool}\n{start}\n{ip}\n{done}\n{acu}\n");
    std::fs::write(dir.join(UPDATES_FILE), &raw).unwrap();

    // Line peeks must drop ACU + InProgress before serde; collapse still runs.
    let prepared = prepare_replay_lines(&raw, None);
    assert_eq!(prepared.lines.len(), 3, "tool + start-meta + completed");

    let mut updates = Vec::new();
    let emission = stream_replay_updates_at(sid, home.path(), |u| updates.push(u)).unwrap();
    assert_eq!(emission, ReplayEmission::Emitted);
    assert_eq!(updates.len(), 1, "collapsed to one completed ToolCall");
    match &updates[0] {
        acp::SessionUpdate::ToolCall(tc) => {
            assert_eq!(tc.status, acp::ToolCallStatus::Completed);
            assert_eq!(tc.title, "bash ls");
        }
        other => panic!("expected ToolCall, got {other:?}"),
    }
}

#[test]
fn stream_replay_forwards_completed_tool_call_update_without_base() {
    let home = tempfile::tempdir().unwrap();
    let cwd = "/tmp/orphan-complete";
    let encoded = pi_config::encode_cwd_dirname(cwd);
    let sid = "child-orphan-complete";
    let dir = home.path().join("sessions").join(&encoded).join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let done = acp_envelope(
        r#"{"sessionUpdate":"tool_call_update","toolCallId":"t1","status":"completed","title":"solo"}"#,
    );
    std::fs::write(dir.join(UPDATES_FILE), format!("{done}\n")).unwrap();
    let mut updates = Vec::new();
    let _ = stream_replay_updates_at_hinted(
        sid,
        home.path(),
        ReplayPathHint {
            parent_cwd: Some(std::path::Path::new(cwd)),
            child_cwd: None,
            ..Default::default()
        },
        |u| {
            if let ReplayedUpdate::Acp(u, _) = u {
                updates.push(u)
            }
        },
    )
    .unwrap();
    assert_eq!(updates.len(), 1);
    assert!(matches!(&updates[0], acp::SessionUpdate::ToolCallUpdate(_)));
}

/// Persisted pi child events (compaction, retry) are forwarded in file
/// order alongside the ACP stream, so a rebuilt child view keeps its
/// non-ACP markers, but they never count toward `Emitted`.
#[test]
fn stream_replay_forwards_pi_updates_in_file_order() {
    let home = tempfile::tempdir().unwrap();
    let sid = "child-pi";
    let dir = home.path().join("sessions").join("cwd").join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let compact = persist_pi_update(
        crate::extensions::notification::SessionUpdate::AutoCompactCompleted {
            tokens_before: Some(1_000),
            tokens_after: 100,
            elapsed_ms: Some(5),
            summary_preview: None,
        },
    );
    let msg = acp_envelope(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"hi"}}"#,
    );
    std::fs::write(dir.join(UPDATES_FILE), format!("{compact}\n{msg}\n")).unwrap();

    let mut kinds = Vec::new();
    let emission =
        stream_replay_updates_at_hinted(sid, home.path(), ReplayPathHint::default(), |u| {
            kinds.push(matches!(u, ReplayedUpdate::Pi(_)));
        })
        .unwrap();
    assert_eq!(emission, ReplayEmission::Emitted);
    assert_eq!(kinds, vec![true, false], "pi then acp, in file order");
}

/// The stream forwards each persisted line's `_meta` alongside the ACP
/// update, so a child rebuild can restore original timestamps
/// (`agentTimestampMs`) instead of stamping entries at rebuild time.
#[test]
fn stream_replay_forwards_persisted_line_meta() {
    let home = tempfile::tempdir().unwrap();
    let sid = "child-meta";
    let dir = home.path().join("sessions").join("cwd").join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let msg = acp_envelope_with_meta(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"hi"}}"#,
        r#"{"agentTimestampMs":1700000000000}"#,
    );
    std::fs::write(dir.join(UPDATES_FILE), format!("{msg}\n")).unwrap();

    let mut metas = Vec::new();
    let emission =
        stream_replay_updates_at_hinted(sid, home.path(), ReplayPathHint::default(), |u| {
            if let ReplayedUpdate::Acp(_, meta) = u {
                metas.push(meta);
            }
        })
        .unwrap();
    assert_eq!(emission, ReplayEmission::Emitted);
    assert_eq!(metas.len(), 1);
    let meta = metas[0]
        .as_ref()
        .expect("persisted _meta must be forwarded");
    assert_eq!(
        meta.get("agentTimestampMs").and_then(|v| v.as_i64()),
        Some(1_700_000_000_000)
    );
}

/// A transcript holding only pi events replays them but stays `Empty`:
/// eviction decisions must not settle on a file the client cannot rebuild
/// transcript content from.
#[test]
fn pi_only_transcript_forwards_but_stays_empty() {
    let home = tempfile::tempdir().unwrap();
    let sid = "child-pi-only";
    let dir = home.path().join("sessions").join("cwd").join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let retry = persist_pi_update(crate::extensions::notification::SessionUpdate::RetryState(
        crate::extensions::notification::RetryState::Retrying {
            attempt: 1,
            max_retries: 3,
            reason: "overloaded".into(),
        },
    ));
    std::fs::write(dir.join(UPDATES_FILE), format!("{retry}\n")).unwrap();

    let mut pi = 0usize;
    let emission =
        stream_replay_updates_at_hinted(sid, home.path(), ReplayPathHint::default(), |u| {
            if matches!(u, ReplayedUpdate::Pi(_)) {
                pi += 1;
            }
        })
        .unwrap();
    assert_eq!(pi, 1);
    assert_eq!(emission, ReplayEmission::Empty);
}

/// The eviction probe verifies EMISSION, not file size: it must agree with
/// what [`stream_replay_updates_at_hinted`] would actually emit, because a
/// `true` licenses dropping the only in-memory transcript copy. Non-empty
/// content that replays `Empty` — pi-only lines, a torn/unparseable line,
/// a start-only ToolCallUpdate — must report false.
#[test]
fn replay_would_emit_requires_an_emitting_acp_line() {
    let home = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(home.path().join("sessions")).unwrap();
    assert!(!replay_would_emit("nope", home.path(), ReplayPathHint::default()).unwrap());

    let sid = "child-probe";
    let dir = home.path().join("sessions").join("cwd").join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let probe = |contents: &str| {
        std::fs::write(dir.join(UPDATES_FILE), contents).unwrap();
        replay_would_emit(sid, home.path(), ReplayPathHint::default()).unwrap()
    };

    assert!(!probe(""), "empty file");
    assert!(!probe("{}\n"), "non-empty but unparseable envelope");
    assert!(
        !probe(r#"{"method":"session/update","params":{"sessionId":"s","update":{"session"#),
        "torn line (crash mid-write)"
    );
    let pi_only = persist_pi_update(
        crate::extensions::notification::SessionUpdate::AutoCompactCompleted {
            tokens_before: Some(1_000),
            tokens_after: 100,
            elapsed_ms: Some(5),
            summary_preview: None,
        },
    );
    assert!(
        !probe(&format!("{pi_only}\n")),
        "pi events alone never count as Emitted"
    );
    let acu =
        acp_envelope(r#"{"sessionUpdate":"available_commands_update","availableCommands":[]}"#);
    assert!(!probe(&format!("{acu}\n")), "catalog lines are dropped");
    let orphan_start =
        acp_envelope(r#"{"sessionUpdate":"tool_call_update","toolCallId":"t1","title":"bash"}"#);
    assert!(
        !probe(&format!("{orphan_start}\n")),
        "a baseless non-completed ToolCallUpdate never emits"
    );

    let msg = acp_envelope(
        r#"{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"hi"}}"#,
    );
    assert!(probe(&format!("{msg}\n")), "a content line emits");
    let tool = acp_envelope(
        r#"{"sessionUpdate":"tool_call","toolCallId":"t1","title":"bash","status":"pending"}"#,
    );
    assert!(
        probe(&format!("{tool}\n")),
        "a start-only ToolCall emits via the EOF pending flush"
    );
    let done = acp_envelope(
        r#"{"sessionUpdate":"tool_call_update","toolCallId":"t1","status":"completed"}"#,
    );
    assert!(
        probe(&format!("{done}\n")),
        "a completed ToolCallUpdate emits even without its base"
    );
    assert!(
        probe(&format!("{pi_only}\n{msg}\n")),
        "an emitting line after non-emitting ones is found"
    );
}

#[test]
fn child_fast_path_finds_updates_under_parent_encoded_cwd() {
    let home = tempfile::tempdir().unwrap();
    let cwd = "/work/proj";
    let encoded = pi_config::encode_cwd_dirname(cwd);
    let sid = "child-fast";
    let dir = home.path().join("sessions").join(&encoded).join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    std::fs::write(dir.join(UPDATES_FILE), "{}\n").unwrap();
    let path = resolve_replay_updates_path(
        sid,
        home.path(),
        ReplayPathHint {
            parent_cwd: Some(std::path::Path::new(cwd)),
            child_cwd: None,
            ..Default::default()
        },
    )
    .unwrap()
    .expect("fast path must find sibling updates.jsonl");
    assert_eq!(path, dir.join(UPDATES_FILE));
}

#[test]
fn child_fast_path_finds_updates_under_child_cwd() {
    let home = tempfile::tempdir().unwrap();
    let parent_cwd = "/work/parent";
    let child_cwd = "/work/wt";
    let encoded = pi_config::encode_cwd_dirname(child_cwd);
    let sid = "child-wt";
    let dir = home.path().join("sessions").join(&encoded).join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    std::fs::write(dir.join(UPDATES_FILE), "{}\n").unwrap();
    let path = resolve_replay_updates_path(
        sid,
        home.path(),
        ReplayPathHint {
            parent_cwd: Some(std::path::Path::new(parent_cwd)),
            child_cwd: Some(std::path::Path::new(child_cwd)),
            ..Default::default()
        },
    )
    .unwrap()
    .expect("child_cwd fast path must find worktree updates.jsonl");
    assert_eq!(path, dir.join(UPDATES_FILE));
}

#[test]
fn child_lookup_falls_back_when_fast_path_misses() {
    let home = tempfile::tempdir().unwrap();
    let other = pi_config::encode_cwd_dirname("/other/cwd");
    let sid = "relocated-child";
    let dir = home.path().join("sessions").join(&other).join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let line = acp_envelope(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
    );
    std::fs::write(dir.join(UPDATES_FILE), format!("{line}\n")).unwrap();
    let mut count = 0usize;
    let emission = stream_replay_updates_at_hinted(
        sid,
        home.path(),
        ReplayPathHint {
            parent_cwd: Some(std::path::Path::new("/parent/cwd")),
            child_cwd: None,
            ..Default::default()
        },
        |_| count += 1,
    )
    .unwrap();
    assert_eq!(emission, ReplayEmission::Emitted);
    assert_eq!(
        count, 1,
        "RelocationView fallback must still stream the child"
    );
}

#[test]
fn child_lookup_hinted_only_skips_scan_when_fast_path_misses() {
    let home = tempfile::tempdir().unwrap();
    let other = pi_config::encode_cwd_dirname("/other/cwd");
    let sid = "relocated-child-hinted";
    let dir = home.path().join("sessions").join(&other).join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let line = acp_envelope(
        r#"{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hi"}}"#,
    );
    std::fs::write(dir.join(UPDATES_FILE), format!("{line}\n")).unwrap();
    let hint = ReplayPathHint {
        parent_cwd: Some(std::path::Path::new("/parent/cwd")),
        child_cwd: None,
        fallback: ReplayLookupFallback::HintedOnly,
    };
    let path = resolve_replay_updates_path(sid, home.path(), hint).unwrap();
    assert!(
        path.is_none(),
        "HintedOnly must not scan when cwd hints miss"
    );
    let mut count = 0usize;
    let emission = stream_replay_updates_at_hinted(sid, home.path(), hint, |_| count += 1).unwrap();
    assert_eq!(emission, ReplayEmission::Empty);
    assert_eq!(
        count, 0,
        "HintedOnly miss must not stream a foreign-cwd file"
    );
}

#[test]
fn child_lookup_missing_session_is_empty() {
    let home = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(home.path().join("sessions")).unwrap();
    let path = resolve_replay_updates_path(
        "no-such",
        home.path(),
        ReplayPathHint {
            parent_cwd: Some(std::path::Path::new("/tmp")),
            child_cwd: None,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(path.is_none());
}

#[test]
fn replay_tool_collapser_keeps_precompleted_tool_call() {
    let mut collapser = ReplayToolCollapser::new();
    let tc = acp::SessionUpdate::ToolCall(
        acp::ToolCall::new(acp::ToolCallId::new("t"), "done")
            .status(acp::ToolCallStatus::Completed),
    );
    let out = collapser.push(tc);
    assert!(matches!(out, Some(acp::SessionUpdate::ToolCall(_))));
}

#[test]
fn stream_replay_eof_flushes_start_only_tool_call() {
    let home = tempfile::tempdir().unwrap();
    let sid = "child-eof-flush";
    let dir = home.path().join("sessions").join("cwd").join(sid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    let tool = persist_acp_update(acp::SessionUpdate::ToolCall(
        acp::ToolCall::new(acp::ToolCallId::new("t1"), "bash").status(acp::ToolCallStatus::Pending),
    ));
    let start = persist_acp_update(acp::SessionUpdate::ToolCallUpdate(
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new("t1"),
            acp::ToolCallUpdateFields::new().title(Some("bash ls".into())),
        ),
    ));
    let ip = persist_acp_update(fat_in_progress_update());
    std::fs::write(dir.join(UPDATES_FILE), format!("{tool}\n{start}\n{ip}\n")).unwrap();
    let mut updates = Vec::new();
    let _ = stream_replay_updates_at(sid, home.path(), |u| updates.push(u)).unwrap();
    assert_eq!(
        updates.len(),
        1,
        "EOF take_pending must emit the start-only ToolCall"
    );
    match &updates[0] {
        acp::SessionUpdate::ToolCall(tc) => assert_eq!(tc.title, "bash ls"),
        other => panic!("expected ToolCall, got {other:?}"),
    }
}

#[test]
fn child_lookup_sees_session_created_after_prior_miss() {
    let home = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(home.path().join("sessions")).unwrap();
    assert!(
        resolve_replay_updates_path("late-child", home.path(), ReplayPathHint::default())
            .unwrap()
            .is_none()
    );
    let dir = home.path().join("sessions").join("cwd").join("late-child");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(SUMMARY_FILE), "{}").unwrap();
    std::fs::write(dir.join(UPDATES_FILE), "{}\n").unwrap();
    let path =
        resolve_replay_updates_path("late-child", home.path(), ReplayPathHint::default()).unwrap();
    assert_eq!(path.as_deref(), Some(dir.join(UPDATES_FILE).as_path()));
}
