#![cfg_attr(rustfmt, rustfmt::skip)]
use super::*;
use crate::acp::model_state::ModelState;
use crate::acp::tracker::AcpUpdateTracker;
use crate::app::agent::{AgentId, AgentSession, AgentState, InFlightPrompt};
use crate::app::agent_view::AgentView;
use crate::scrollback::entry::EntryId;
use crate::scrollback::state::ScrollbackState;
use std::path::PathBuf;
use std::time::Instant;
use pi_shell::extensions::notification::RetryState;
use pi_shell::extensions::notification::SessionUpdate as PiSessionUpdate;
pub(super) fn make_session(session_id: Option<&str>) -> AgentSession {
    let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
    AgentSession {
        id: AgentId(0),
        acp_tx: tx,
        session_id: session_id.map(acp::SessionId::new),
        models: ModelState::default(),
        state: AgentState::Idle,
        tracker: AcpUpdateTracker::new(),
        cwd: PathBuf::from("/tmp"),
        is_worktree: false,
        forked_from: None,
        pending_prompts: std::collections::VecDeque::new(),
        next_queue_id: 0,
        yolo_mode: false,
        auto_mode: false,
        prompt_history: Vec::new(),
        loading_replay: false,
        restore_degree: None,
        rate_limited: false,
        model_incompatible: false,
        credit_limit_blocked: false,
        free_usage_blocked: false,
        available_commands: Vec::new(),
        available_commands_generation: 0,
        available_tools: None,
        model_switch_pending: false,
        user_model_preference: None,
        deferred_model_switch: None,
        in_flight_prompt: None,
        compact_held_prompt: None,
        current_prompt_id: None,
        created_via_new: false,
    }
}
pub(super) fn make_agent(session_id: Option<&str>) -> AgentView {
    AgentView::new(make_session(session_id), ScrollbackState::new())
}
pub(super) fn permission_req_with_raw_input(
    raw_input: Option<serde_json::Value>,
) -> acp::RequestPermissionRequest {
    let fields = acp::ToolCallUpdateFields::new().raw_input(raw_input);
    acp::RequestPermissionRequest::new(
        acp::SessionId::new(std::sync::Arc::from("s1")),
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new(std::sync::Arc::from("call-1")),
            fields,
        ),
        vec![],
    )
}
pub(super) fn recap_block(text: &str) -> RenderBlock {
    RenderBlock::session_event(SessionEvent::Recap {
        summary: text.to_string(),
        auto: false,
    })
}
pub(super) fn compressed_entry(
    index: usize,
) -> pi_shell::extensions::notification::ImageCompressedEntry {
    pi_shell::extensions::notification::ImageCompressedEntry {
        index,
        original_bytes: 4_200_000,
        compressed_bytes: 780_000,
        original_width: 3024,
        original_height: 1964,
        compressed_width: 1568,
        compressed_height: 1018,
    }
}
/// Most recent `SessionEvent` pushed to the scrollback, if any.
pub(super) fn last_session_event(sb: &ScrollbackState) -> Option<SessionEvent> {
    (0..sb.len())
        .rev()
        .find_map(|i| match sb.get(i).map(|e| &e.block) {
            Some(RenderBlock::SessionEvent(b)) => Some(b.event.clone()),
            _ => None,
        })
}
pub(super) fn make_app_with_agent(session_id: &str) -> AppView {
    let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
    let mut app = AppView::new(tx.clone(), ModelState::default(), Vec::new());
    let id = AgentId(0);
    let agent = make_agent(Some(session_id));
    app.agents.insert(id, agent);
    crate::app::dispatch::switch_to_agent(
        &mut app,
        id,
        crate::app::dispatch::SwitchCause::New,
    );
    app
}
pub(super) fn follow_ups_ext(
    response_id: &str,
    labels: &[&str],
) -> acp::ExtNotification {
    let suggestions: Vec<serde_json::Value> = labels
        .iter()
        .map(|l| serde_json::json!({ "label": l }))
        .collect();
    let params = serde_json::json!({
            "response_id": response_id,
            "suggestions": suggestions,
        });
    acp::ExtNotification::new(
        "pi/follow_ups",
        std::sync::Arc::from(serde_json::value::to_raw_value(&params).unwrap()),
    )
}
pub(super) fn make_token_notification_message(
    session_id: &str,
    total_tokens: u64,
) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let request = acp::SessionNotification::new(
            acp::SessionId::new(session_id),
            acp::SessionUpdate::AgentMessageChunk(
                acp::ContentChunk::new(
                    acp::ContentBlock::Text(acp::TextContent::new("hi")),
                ),
            ),
        )
        .meta(serde_json::json!({
                "totalTokens": total_tokens,
            }).as_object().cloned());
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
use crate::scrollback::block::RenderBlock;
/// Build an `AgentMessageChunk` notification carrying `text` for `session_id`.
pub(super) fn make_agent_chunk_message(
    session_id: &str,
    text: &str,
) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let request = acp::SessionNotification::new(
        acp::SessionId::new(session_id),
        acp::SessionUpdate::AgentMessageChunk(
            acp::ContentChunk::new(acp::ContentBlock::Text(acp::TextContent::new(text))),
        ),
    );
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// `AgentMessageChunk` with `promptId`/`isReplay` + optional `eventId`.
pub(super) fn make_agent_chunk_meta(
    session_id: &str,
    text: &str,
    prompt_id: &str,
    event_id: Option<&str>,
    is_replay: bool,
) -> AcpClientMessage {
    let mut meta = serde_json::Map::new();
    meta.insert("promptId".to_string(), serde_json::json!(prompt_id));
    meta.insert("isReplay".to_string(), serde_json::json!(is_replay));
    if let Some(eid) = event_id {
        meta.insert("eventId".to_string(), serde_json::json!(eid));
    }
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let request = acp::SessionNotification::new(
            acp::SessionId::new(session_id),
            acp::SessionUpdate::AgentMessageChunk(
                acp::ContentChunk::new(
                    acp::ContentBlock::Text(acp::TextContent::new(text)),
                ),
            ),
        )
        .meta(serde_json::Value::Object(meta).as_object().cloned());
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// `promptId`-tagged chunk (no `eventId`) — drives the viewer live-delta path.
pub(super) fn make_agent_chunk_message_with_prompt(
    session_id: &str,
    text: &str,
    prompt_id: &str,
    is_replay: bool,
) -> AcpClientMessage {
    make_agent_chunk_meta(session_id, text, prompt_id, None, is_replay)
}
/// Live (`isReplay=false`) chunk with an optional `eventId`, for dedup tests.
pub(super) fn make_agent_chunk_with_event(
    session_id: &str,
    text: &str,
    prompt_id: &str,
    event_id: Option<&str>,
) -> AcpClientMessage {
    make_agent_chunk_meta(session_id, text, prompt_id, event_id, false)
}
/// Replay-marked chunk with an eventId, as `session/load` emits.
pub(super) fn replay_chunk(
    session_id: &str,
    text: &str,
    event_id: &str,
) -> AcpClientMessage {
    make_agent_chunk_meta(session_id, text, "p-history", Some(event_id), true)
}
/// `Plan` update message with the given entry contents.
pub(super) fn plan_update_msg(
    session_id: &str,
    entries: &[&str],
    event_id: Option<&str>,
    is_replay: bool,
) -> AcpClientMessage {
    let entries = entries
        .iter()
        .map(|content| acp::PlanEntry::new(
            *content,
            acp::PlanEntryPriority::Medium,
            acp::PlanEntryStatus::Pending,
        ))
        .collect();
    let mut meta = serde_json::Map::new();
    meta.insert("isReplay".to_string(), serde_json::json!(is_replay));
    if let Some(eid) = event_id {
        meta.insert("eventId".to_string(), serde_json::json!(eid));
    }
    let (tx, _rx) = tokio::sync::oneshot::channel();
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request: acp::SessionNotification::new(
                acp::SessionId::new(session_id),
                acp::SessionUpdate::Plan(acp::Plan::new(entries)),
            )
            .meta(serde_json::Value::Object(meta).as_object().cloned()),
        response_tx: tx,
    })
}
pub(super) fn pi_model_switch_notif(
    session_id: &str,
    event_id: &str,
) -> acp::ExtNotification {
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::ModelAutoSwitched {
            previous_model_id: "m-old".into(),
            new_model_id: "m-new".into(),
            reason: "gone".into(),
        },
        meta: Some(serde_json::json!({ "eventId": event_id })),
    };
    acp::ExtNotification::new(
        "pi/session/update",
        std::sync::Arc::from(serde_json::value::to_raw_value(&payload).unwrap()),
    )
}
pub(super) fn pi_unhandled_notif(
    session_id: &str,
    event_id: &str,
) -> acp::ExtNotification {
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::MemoryFlushStarted,
        meta: Some(serde_json::json!({ "eventId": event_id })),
    };
    acp::ExtNotification::new(
        "pi/session/update",
        std::sync::Arc::from(serde_json::value::to_raw_value(&payload).unwrap()),
    )
}
/// Build an `agent_message_chunk` notification carrying both `totalTokens`
/// and an explicit `eventId`, for context/dedup interaction tests.
pub(super) fn make_token_notification_with_event(
    session_id: &str,
    total_tokens: u64,
    event_id: &str,
) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let request = acp::SessionNotification::new(
            acp::SessionId::new(session_id),
            acp::SessionUpdate::AgentMessageChunk(
                acp::ContentChunk::new(
                    acp::ContentBlock::Text(acp::TextContent::new("hi")),
                ),
            ),
        )
        .meta(
            serde_json::json!({
                "totalTokens": total_tokens,
                "eventId": event_id,
            })
                .as_object()
                .cloned(),
        );
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// Build an `legacy ext RPC` ext-notification for `session_id`.
pub(super) fn prompt_complete_ext(session_id: &str) -> acp::ExtNotification {
    let raw = serde_json::value::to_raw_value(
            &serde_json::json!({
            "sessionId": session_id,
            "stopReason": "end_turn",
        }),
        )
        .unwrap();
    acp::ExtNotification::new("pi/session/prompt_complete", std::sync::Arc::from(raw))
}
/// Insert a fresh agent at `id` with an optional pre-assigned session id.
pub(super) fn insert_agent(app: &mut AppView, id: AgentId, session_id: Option<&str>) {
    app.agents.insert(id, make_agent(session_id));
}
/// Build a live `AgentMessageChunk` whose meta carries `promptId` plus a
/// `turnStartMs` `start_ms_ago` milliseconds in the past — drives the viewer
/// adoption path with a known authoritative turn start.
pub(super) fn make_viewer_chunk_with_turn_start(
    session_id: &str,
    prompt_id: &str,
    start_ms_ago: i64,
) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let turn_start_ms = chrono::Utc::now().timestamp_millis() - start_ms_ago;
    let request = acp::SessionNotification::new(
            acp::SessionId::new(session_id),
            acp::SessionUpdate::AgentMessageChunk(
                acp::ContentChunk::new(
                    acp::ContentBlock::Text(acp::TextContent::new("driver chunk")),
                ),
            ),
        )
        .meta(
            serde_json::json!({
                "promptId": prompt_id,
                "isReplay": false,
                "turnStartMs": turn_start_ms,
            })
                .as_object()
                .cloned(),
        );
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// Build a durable `TurnCompleted` update on the `legacy ext RPC` rail,
/// optionally stamped `isReplay`. Built through the typed `SessionNotification`
/// so the wire shape can't drift from what the dispatch parses.
pub(super) fn pi_turn_completed_notif(
    session_id: &str,
    prompt_id: &str,
    stop_reason: &str,
    is_replay: bool,
) -> acp::ExtNotification {
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::TurnCompleted {
            prompt_id: prompt_id.into(),
            stop_reason: stop_reason.into(),
            agent_result: None,
            usage: None,
        },
        meta: Some(serde_json::json!({ "isReplay": is_replay })),
    };
    acp::ExtNotification::new(
        "pi/session/update",
        std::sync::Arc::from(serde_json::value::to_raw_value(&payload).unwrap()),
    )
}
/// A live durable `TurnCompleted`, optionally stamped with the shell
/// completion clock (`agentTimestampMs`) the wake marker's elapsed reads.
pub(super) fn pi_wake_turn_completed_notif(
    session_id: &str,
    prompt_id: &str,
    agent_timestamp_ms: Option<i64>,
) -> acp::ExtNotification {
    let mut meta = serde_json::json!({ "isReplay": false });
    if let Some(ms) = agent_timestamp_ms {
        meta["agentTimestampMs"] = ms.into();
    }
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::TurnCompleted {
            prompt_id: prompt_id.into(),
            stop_reason: "end_turn".into(),
            agent_result: None,
            usage: None,
        },
        meta: Some(meta),
    };
    acp::ExtNotification::new(
        "pi/session/update",
        std::sync::Arc::from(serde_json::value::to_raw_value(&payload).unwrap()),
    )
}
/// Switch the active view to `id` via the canonical helper. Wrapping
/// here keeps the source-scan invariant test
/// (`no_direct_active_view_assignment_outside_switch_to_agent`) happy.
pub(super) fn switch_active_to(app: &mut AppView, id: AgentId) {
    crate::app::dispatch::switch_to_agent(
        app,
        id,
        crate::app::dispatch::SwitchCause::Picker,
    );
}
/// Concatenate the text of every `AgentMessage` block in this view's scrollback.
pub(super) fn agent_message_text(view: &AgentView) -> String {
    let mut out = String::new();
    for i in 0..view.scrollback.len() {
        if let Some(entry) = view.scrollback.get(i)
            && let RenderBlock::AgentMessage(msg) = &entry.block
        {
            out.push_str(&msg.text());
        }
    }
    out
}
/// Build a `Plan` notification with one entry per `entries` string.
pub(super) fn make_plan_message(session_id: &str, entries: &[&str]) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let plan_entries = entries
        .iter()
        .map(|content| acp::PlanEntry::new(
            *content,
            acp::PlanEntryPriority::Medium,
            acp::PlanEntryStatus::Pending,
        ))
        .collect();
    let request = acp::SessionNotification::new(
        acp::SessionId::new(session_id),
        acp::SessionUpdate::Plan(acp::Plan::new(plan_entries)),
    );
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// Build an `AvailableCommandsUpdate` notification with the given command names.
pub(super) fn make_commands_update_message(
    session_id: &str,
    names: &[&str],
) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let commands = names
        .iter()
        .map(|name| acp::AvailableCommand::new(*name, String::new()))
        .collect();
    let request = acp::SessionNotification::new(
        acp::SessionId::new(session_id),
        acp::SessionUpdate::AvailableCommandsUpdate(
            acp::AvailableCommandsUpdate::new(commands),
        ),
    );
    AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// Build an `ExtNotification` envelope for `legacy ext RPC`.
pub(super) fn make_ext_session_notification(
    session_id: &str,
    update: PiSessionUpdate,
) -> AcpClientMessage {
    make_ext_session_notification_with_method(
        session_id,
        "pi/session_notification",
        update,
    )
}
/// Build an `ExtNotification` envelope with an explicit pi session method.
pub(super) fn make_ext_session_notification_with_method(
    session_id: &str,
    method: &str,
    update: PiSessionUpdate,
) -> AcpClientMessage {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update,
        meta: None,
    };
    let raw = serde_json::value::to_raw_value(&payload).unwrap();
    let request = acp::ExtNotification::new(method, raw.into());
    AcpClientMessage::ExtNotification(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    })
}
/// Build a minimal `RequestPermission` message that carries `session_id`
/// and one `AllowOnce` option.
pub(super) fn make_permission_message(
    session_id: &str,
) -> (
    AcpClientMessage,
    tokio::sync::oneshot::Receiver<Result<acp::RequestPermissionResponse, acp::Error>>,
) {
    use std::sync::Arc;
    let (tx, rx) = tokio::sync::oneshot::channel();
    let request = acp::RequestPermissionRequest::new(
        acp::SessionId::new(session_id),
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new(Arc::from("call-perm-1")),
            acp::ToolCallUpdateFields::default(),
        ),
        vec![acp::PermissionOption::new(
                acp::PermissionOptionId::new(Arc::from("allow-once")),
                "Allow once",
                acp::PermissionOptionKind::AllowOnce,
            )],
    );
    let msg = AcpClientMessage::RequestPermission(pi_acp_lib::AcpArgs {
        request,
        response_tx: tx,
    });
    (msg, rx)
}
/// Build an `legacy ext RPC` carrying
/// `InteractionResolved{tool_call_id}` (the first-answer-wins broadcast that
/// tells every other pane to retract its shared interaction modal).
pub(super) fn interaction_resolved_ext(
    session_id: &str,
    tool_call_id: &str,
) -> acp::ExtNotification {
    let notif = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::InteractionResolved {
            tool_call_id: tool_call_id.into(),
        },
        meta: None,
    };
    let raw = serde_json::value::to_raw_value(&notif).unwrap();
    acp::ExtNotification::new("pi/session_notification", std::sync::Arc::from(raw))
}
pub(super) fn make_model_info(id: &str) -> acp::ModelInfo {
    acp::ModelInfo::new(acp::ModelId::new(std::sync::Arc::from(id)), id.to_string())
}
/// Seed a session's model catalog with the given ids and mark
/// `current_model_id` as the active one (must be in the list). Used by
/// the `ModelChanged` broadcast tests to set up a starting state that
/// the simulated remote/local switch then transitions away from.
pub(super) fn seed_models(agent: &mut AgentView, current: &str, available: &[&str]) {
    for id in available {
        let model_id = acp::ModelId::new(std::sync::Arc::from(*id));
        agent.session.models.available.insert(model_id.clone(), make_model_info(id));
    }
    agent.session.models.current = Some(
        acp::ModelId::new(std::sync::Arc::from(current)),
    );
}
pub(super) fn model_changed_ext(
    session_id: &str,
    model_id: &str,
    reasoning_effort: Option<&str>,
) -> acp::ExtNotification {
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::ModelChanged {
            model_id: model_id.to_string(),
            reasoning_effort: reasoning_effort.map(String::from),
        },
        meta: None,
    };
    let raw = serde_json::value::to_raw_value(&payload).unwrap();
    acp::ExtNotification::new("pi/session_notification", std::sync::Arc::from(raw))
}
pub(super) fn model_changed_ext_with_event(
    session_id: &str,
    model_id: &str,
    event_id: &str,
) -> acp::ExtNotification {
    let payload = SessionNotification {
        session_id: acp::SessionId::new(session_id),
        update: PiSessionUpdate::ModelChanged {
            model_id: model_id.to_string(),
            reasoning_effort: None,
        },
        meta: Some(serde_json::json!({ "eventId": event_id })),
    };
    let raw = serde_json::value::to_raw_value(&payload).unwrap();
    acp::ExtNotification::new("pi/session_notification", std::sync::Arc::from(raw))
}
pub(super) fn make_tool_call_update(title: &str) -> acp::SessionUpdate {
    acp::SessionUpdate::ToolCallUpdate(
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new("tc-1"),
            acp::ToolCallUpdateFields::new()
                .title(Some(title.to_string()))
                .status(Some(acp::ToolCallStatus::Completed)),
        ),
    )
}
pub(super) fn make_tool_call(title: &str) -> acp::SessionUpdate {
    acp::SessionUpdate::ToolCall(
        acp::ToolCall::new(acp::ToolCallId::new("tc-2"), title.to_string())
            .kind(acp::ToolKind::Other)
            .status(acp::ToolCallStatus::Pending)
            .content(vec![])
            .locations(vec![]),
    )
}
pub(super) fn make_current_mode_update(mode_id: &str) -> acp::SessionUpdate {
    acp::SessionUpdate::CurrentModeUpdate(
        acp::CurrentModeUpdate::new(acp::SessionModeId::new(mode_id)),
    )
}
mod permissions;
mod session_events;
mod follow_ups;
mod settings;
mod queue_and_adoption;
mod plan_mode;
mod reconnect;
mod turn_completion;
mod session_routing;
mod interactions;
mod models;
mod git_head;
