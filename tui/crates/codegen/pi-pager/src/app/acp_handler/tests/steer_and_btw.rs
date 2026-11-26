//! Unit tests for X9 mid-turn steering and X10 ephemeral `/btw` handling in `pi-pager`.

use super::*;
use crate::app::actions::{Action, Effect};
use crate::app::dispatch::dispatch;
use pi_acp_lib::extensions::btw::{
    BTW_UPDATE_NOTIFICATION, BtwHistoryRole, BtwHistoryTurn, BtwUpdateKind, BtwUpdateNotification,
};
use pi_acp_lib::extensions::steer::{
    QUEUE_UPDATE_NOTIFICATION, QueueUpdateNotification,
};

#[test]
fn trim_btw_history_enforces_32kib_utf8_byte_budget() {
    let mut history = vec![
        BtwHistoryTurn {
            role: BtwHistoryRole::User,
            text: "q1".to_string(),
        },
        BtwHistoryTurn {
            role: BtwHistoryRole::Assistant,
            // 24 KiB UTF-8 (8000 x 3-byte char)
            text: "\u{4f60}".repeat(8000),
        },
        BtwHistoryTurn {
            role: BtwHistoryRole::User,
            text: "q2".to_string(),
        },
        BtwHistoryTurn {
            role: BtwHistoryRole::Assistant,
            // 24 KiB UTF-8
            text: "\u{597d}".repeat(8000),
        },
    ];
    trim_btw_history(&mut history);
    assert_eq!(history.len(), 2);
    assert_eq!(history[0].text, "q2");
    let total_bytes: usize = history.iter().map(|t| t.text.len()).sum();
    assert!(total_bytes <= BTW_MAX_HISTORY_BYTES);
}

#[test]
fn queue_update_cleared_by_abort_prepends_to_non_empty_prompt_draft() {
    let mut app = make_app_with_agent("s-steer-restore");
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .pending_steers
            .push(("st-1".to_string(), "focus on tests".to_string()));
        agent.prompt.set_text("already typing something");
    }

    let payload = QueueUpdateNotification {
        session_id: "s-steer-restore".to_string(),
        steer: vec![],
        cleared_by_abort: true,
        cleared_steer_ids: vec!["st-1".to_string()],
    };
    let raw = serde_json::value::to_raw_value(&payload).unwrap();
    let notif = acp::ExtNotification::new(QUEUE_UPDATE_NOTIFICATION, std::sync::Arc::from(raw));

    assert!(handle_queue_update_notification(&notif, &mut app));
    let agent = &app.agents[&id];
    assert!(agent.pending_steers.is_empty());
    assert_eq!(
        agent.prompt.text(),
        "focus on tests\nalready typing something"
    );
}

#[test]
fn busy_submit_emits_steer_and_btw_runs_in_parallel_without_queueing() {
    let mut app = make_app_with_agent("s-busy");
    app.pi_capabilities.steer = true;
    app.pi_capabilities.btw = true;
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;

    // 1. Plain prompt while busy emits Effect::SteerSession instead of queueing locally.
    let steer_effects = dispatch(Action::SendPrompt("switch to unit tests".into()), &mut app);
    assert!(steer_effects.iter().any(|e| matches!(
        e,
        Effect::SteerSession {
            session_id,
            display_text,
            ..
        } if session_id.0.as_ref() == "s-busy" && display_text == "switch to unit tests"
    )));
    assert!(app.agents[&id].session.pending_prompts.is_empty());
    assert_eq!(app.agents[&id].pending_steers.len(), 1);

    // 2. `/btw <question>` while busy emits Effect::SendBtw without touching pending_prompts.
    let btw_effects = dispatch(Action::SendPrompt("/btw what failed?".into()), &mut app);
    let sent_btw_id = btw_effects.iter().find_map(|e| match e {
        Effect::SendBtw {
            session_id,
            btw_id,
            question,
            ..
        } if session_id.0.as_ref() == "s-busy" && question == "what failed?" => {
            Some(btw_id.clone())
        }
        _ => None,
    });
    let btw_id = sent_btw_id.expect("expected Effect::SendBtw");
    assert!(app.agents[&id].session.pending_prompts.is_empty());
    assert!(app.agents[&id].btw_state.is_some());

    // 3. Streaming btw deltas and done update overlay and record trimmed history.
    let delta_notif = BtwUpdateNotification {
        session_id: "s-busy".to_string(),
        btw_id: btw_id.clone(),
        kind: BtwUpdateKind::TextDelta,
        delta: "exit 137 is OOM".to_string(),
        error: None,
        stop_reason: None,
    };
    let raw_delta = serde_json::value::to_raw_value(&delta_notif).unwrap();
    assert!(handle_btw_update_notification(
        &acp::ExtNotification::new(BTW_UPDATE_NOTIFICATION, std::sync::Arc::from(raw_delta)),
        &mut app,
    ));

    let done_notif = BtwUpdateNotification {
        session_id: "s-busy".to_string(),
        btw_id,
        kind: BtwUpdateKind::Done,
        delta: String::new(),
        error: None,
        stop_reason: Some("end_turn".to_string()),
    };
    let raw_done = serde_json::value::to_raw_value(&done_notif).unwrap();
    assert!(handle_btw_update_notification(
        &acp::ExtNotification::new(BTW_UPDATE_NOTIFICATION, std::sync::Arc::from(raw_done)),
        &mut app,
    ));

    let agent = &app.agents[&id];
    assert!(agent.active_btw_id.is_none());
    assert_eq!(agent.btw_history.len(), 2);
    assert_eq!(agent.btw_history[0].text, "what failed?");
    assert_eq!(agent.btw_history[1].text, "exit 137 is OOM");
}
