use crate::sampling::{ConversationItem, ToolCall};
/// Verify that the auto-continue prompt (sent after compaction) is also
/// raw text without <user_query> wrapping.
#[test]
fn test_auto_continue_prompt_has_no_user_query_tags() {
    let auto_continue = "Continue with the work described in the summary above. Pick up where you left off based on the 'Current Work' and 'Next Step' sections. If the previous task was completed, confirm completion and await further instructions.";
    let msg = ConversationItem::user(auto_continue);
    let text = msg.text_content();
    assert_eq!(text, auto_continue);
    assert!(
        !text.contains("<user_query>"),
        "Auto-continue prompt must NOT contain <user_query> tags"
    );
}
/// Prove that the sanitizer + validator pipeline produces a valid
/// compacted history even when the raw output has an orphaned ToolResult.
/// This exercises the same code path as `run_compact_inner` in
/// `acp_session.rs`: build → sanitize → validate → (fallback if needed).
#[test]
fn sanitize_then_validate_produces_valid_history() {
    use pi_chat_state::compaction_utils::{
        sanitize_compacted_history, validate_compacted_history,
    };
    let raw = vec![
        ConversationItem::system("sys"),
        ConversationItem::user("<user_query>\ntask\n</user_query>"),
        // Orphan: no preceding assistant with call_ORPHAN
        ConversationItem::tool_result("call_ORPHAN", "Tool call omitted..."),
        // Valid pair
        ConversationItem::assistant_tool_calls(vec![ToolCall {
            id: "call_OK".into(),
            name: "edit".to_string(),
            arguments: "{}".into(),
        }]),
        ConversationItem::tool_result("call_OK", "Tool call omitted..."),
        ConversationItem::user("summary"),
    ];
    let sanitized = sanitize_compacted_history(raw);
    assert_eq!(sanitized.stripped_tool_call_ids, vec!["call_ORPHAN"]);
    let violations = validate_compacted_history(&sanitized.items);
    assert!(
        violations.is_empty(),
        "post-sanitize validation must pass, but found: {violations:?}"
    );
}
