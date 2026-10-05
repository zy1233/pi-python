//! Unit tests for the turn-finalize rails in [`super`] (`turn_completion`),
//! split out via `#[path]` to keep the module itself small.

use super::*;
use crate::app::agent::AgentState;
use crate::scrollback::block::RenderBlock;
use crate::scrollback::blocks::SessionEventBlock;
use crate::scrollback::state::ScrollbackState;
use std::path::PathBuf;
use std::time::Instant;

fn last_session_event(sb: &ScrollbackState) -> Option<SessionEvent> {
    (0..sb.len())
        .rev()
        .find_map(|i| match sb.get(i).map(|e| &e.block) {
            Some(RenderBlock::SessionEvent(b)) => Some(b.event.clone()),
            _ => None,
        })
}

/// A viewer in TurnRunning with an adopted prompt id, ready to be finalized.
fn running_viewer(prompt_id: &str) -> AgentView {
    let mut agent = super::super::agent_view::test_agent_view(Some("s1"), PathBuf::from("/tmp"));
    agent.attached_as_viewer = true;
    agent.session.start_turn(&mut agent.scrollback);
    agent.session.current_prompt_id = Some(prompt_id.into());
    agent.turn_started_at = Some(Instant::now());
    agent
}

/// A driver in TurnRunning with a local prompt id (default
/// `attached_as_viewer == false`).
fn running_driver(prompt_id: &str) -> AgentView {
    let mut agent = super::super::agent_view::test_agent_view(Some("s1"), PathBuf::from("/tmp"));
    agent.session.start_turn(&mut agent.scrollback);
    agent.session.current_prompt_id = Some(prompt_id.into());
    agent.turn_started_at = Some(Instant::now());
    agent
}

#[test]
fn viewer_finalize_idles_and_pushes_completed_marker() {
    let mut agent = running_viewer("p1");
    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::ViewerFinalized));
    assert!(agent.session.state.is_idle());
    assert!(agent.session.current_prompt_id.is_none());
    assert!(agent.turn_started_at.is_none());
    assert!(matches!(
        last_session_event(&agent.scrollback),
        Some(SessionEvent::TurnCompleted { .. })
    ));
}




#[test]
fn viewer_finalize_duplicate_terminal_is_noop() {
    let mut agent = running_viewer("p1");
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    let len_after_first = agent.scrollback.len();

    // A duplicate/stale terminal for the now-finished turn does nothing.
    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::Ignored));
    assert!(agent.session.state.is_idle());
    assert_eq!(
        agent.scrollback.len(),
        len_after_first,
        "a duplicate terminal must not push a second marker"
    );
}

#[test]
fn viewer_finalize_stop_reason_to_marker_mapping() {
    // cancelled → Turn cancelled.
    let mut agent = running_viewer("p1");
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("cancelled"),
            ..Default::default()
        },
    );
    assert!(matches!(
        last_session_event(&agent.scrollback),
        Some(SessionEvent::TurnCancelled { .. })
    ));

    // error (+agentResult) → TurnFailed with formatted text.
    let mut agent = running_viewer("p1");
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("error"),
            agent_result: Some(r#"API error (status 500): {"error":"boom"}"#),
            ..Default::default()
        },
    );
    match last_session_event(&agent.scrollback) {
        Some(SessionEvent::TurnFailed { error, .. }) => {
            assert_eq!(
                error,
                "Server error (500): Something went wrong on our side. Wait a minute and send again."
            );
        }
        other => panic!("expected TurnFailed, got {other:?}"),
    }

    // error with a dedicated banner already in the trailing run → the
    // banner explains the failure; no redundant TurnFailed marker.
    let mut agent = running_viewer("p1");
    agent
        .scrollback
        .push_block(RenderBlock::session_event(SessionEvent::RequestFailed {
            status: Some(500),
            headline: "Server error (500)".into(),
            detail: String::new(),
        }));
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("error"),
            ..Default::default()
        },
    );
    assert!(
        !matches!(
            last_session_event(&agent.scrollback),
            Some(SessionEvent::TurnFailed { .. })
        ),
        "a trailing RequestFailed banner must suppress the viewer's TurnFailed"
    );

    // …but a banner buried behind a substantive block is a previous turn's:
    // the trailing-run scan stops and the marker is pushed.
    let mut agent = running_viewer("p1");
    agent
        .scrollback
        .push_block(RenderBlock::session_event(SessionEvent::RequestFailed {
            status: Some(500),
            headline: "Server error (500)".into(),
            detail: String::new(),
        }));
    agent.scrollback.push_block(RenderBlock::user_prompt("hi"));
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("error"),
            ..Default::default()
        },
    );
    assert!(
        matches!(
            last_session_event(&agent.scrollback),
            Some(SessionEvent::TurnFailed { .. })
        ),
        "a banner behind a substantive block must not suppress the marker"
    );

    // rate_limit → finished, but no marker (not actionable from a viewer).
    let mut agent = running_viewer("p1");
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("rate_limit"),
            ..Default::default()
        },
    );
    assert!(agent.session.state.is_idle());
    assert!(
        last_session_event(&agent.scrollback).is_none(),
        "rate_limit must not push a marker on a viewer"
    );

    // unknown/other reason → Turn completed (the catch-all).
    let mut agent = running_viewer("p1");
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("max_tokens"),
            ..Default::default()
        },
    );
    assert!(matches!(
        last_session_event(&agent.scrollback),
        Some(SessionEvent::TurnCompleted { .. })
    ));
}

#[test]
fn driver_arms_reconcile_and_does_not_finish() {
    let mut agent = running_driver("p1");
    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("cancelled"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::ReconcileArmed));
    assert!(
        matches!(agent.session.state, AgentState::TurnRunning),
        "the driver's turn must NOT be finished — the PromptResponse RPC owns it"
    );
    let pending = agent
        .pending_turn_end_reconcile
        .as_ref()
        .expect("the driver's awaited turn must arm a reconcile");
    assert_eq!(pending.prompt_id, "p1");
    assert_eq!(pending.stop_reason.as_deref(), Some("cancelled"));
}

#[test]
fn driver_mismatched_prompt_id_does_not_arm() {
    // Stale/peer terminal must not arm reconcile on a different live turn.
    let mut agent = running_driver("p1");
    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p-other"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::Ignored));
    assert!(agent.pending_turn_end_reconcile.is_none());
    assert!(matches!(agent.session.state, AgentState::TurnRunning));
}

#[test]
fn driver_missing_prompt_id_arms_against_current_when_idle_in_turn() {
    let mut agent = running_driver("p1");
    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::ReconcileArmed));
    assert_eq!(
        agent.pending_turn_end_reconcile.as_ref().unwrap().prompt_id,
        "p1"
    );
}

fn stream_agent_text(agent: &mut AgentView, text: &str) {
    use crate::acp::meta::NotificationMeta;
    use agent_client_protocol as acp;
    let meta = NotificationMeta::default();
    let _ = agent.session.tracker.handle_update(
        acp::SessionUpdate::AgentMessageChunk(acp::ContentChunk::new(acp::ContentBlock::Text(
            acp::TextContent::new(text),
        ))),
        &meta,
        &mut agent.scrollback,
    );
}

/// Missing wire pid still arms lost-PR reconcile (full teardown
/// lives in `reconcile_overdue_turn_ends` / PromptResponse).
#[test]
fn repro_terminal_without_prompt_id_arms_reconcile_for_lost_pr() {
    let mut agent = running_driver("p1");
    stream_agent_text(&mut agent, "done");
    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::ReconcileArmed));
    assert!(agent.pending_turn_end_reconcile.is_some());
    assert!(
        matches!(agent.session.state, AgentState::TurnRunning),
        "driver stays TurnRunning until PR or overdue reconcile"
    );
    assert_eq!(
        agent.session.tracker.activity(),
        Some(crate::acp::tracker::TurnActivity::Responding)
    );
}

/// Armed, the overdue reconcile would force-finish the live turn mid-write.
#[test]
fn driver_missing_prompt_id_ignored_during_tool_call_write() {
    let mut agent = running_driver("p1");
    assert!(
        agent
            .session
            .tracker
            .note_tool_call_arguments_delta(Some("spawn_subagent"), 0)
    );
    assert!(matches!(
        agent.session.tracker.activity(),
        Some(crate::acp::tracker::TurnActivity::WritingToolCall(_))
    ));

    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::Ignored));
    assert!(
        agent.pending_turn_end_reconcile.is_none(),
        "mid-write terminal must not arm the lost-PR reconcile"
    );
    assert!(matches!(agent.session.state, AgentState::TurnRunning));
}

/// A dead stream mid-write must not block lost-response recovery.
#[test]
fn driver_missing_prompt_id_arms_when_tool_call_write_is_stale() {
    let mut agent = running_driver("p1");
    agent
        .session
        .tracker
        .note_tool_call_arguments_delta(Some("spawn_subagent"), 0);
    agent.session.tracker.backdate_last_tool_call_delta(
        crate::acp::tracker::WRITING_DELTA_STALE_AFTER + std::time::Duration::from_secs(1),
    );

    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::ReconcileArmed));
    assert_eq!(
        agent.pending_turn_end_reconcile.as_ref().unwrap().prompt_id,
        "p1"
    );
}

/// Exact pid still arms (control).
#[test]
fn recovery_mode_matching_turn_completed_arms_reconcile_for_lost_pr() {
    let mut agent = running_driver("p1");
    stream_agent_text(&mut agent, "done");

    let outcome = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    assert!(matches!(outcome, TerminalApply::ReconcileArmed));
    assert!(matches!(agent.session.state, AgentState::TurnRunning));
    assert!(agent.pending_turn_end_reconcile.is_some());
}

/// Re-arm same pid keeps earliest received_at (does not extend grace forever).
#[test]
fn driver_rearm_same_pid_preserves_received_at() {
    let mut agent = running_driver("p1");
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    let first = agent
        .pending_turn_end_reconcile
        .as_ref()
        .unwrap()
        .received_at;
    std::thread::sleep(std::time::Duration::from_millis(5));
    let _ = finalize_turn_from_terminal(
        &mut agent,
        "s1",
        TerminalSignal {
            prompt_id: Some("p1"),
            stop_reason: Some("end_turn"),
            ..Default::default()
        },
    );
    let second = agent
        .pending_turn_end_reconcile
        .as_ref()
        .unwrap()
        .received_at;
    assert_eq!(first, second);
}

// ── End markers: always the plain event text (work lives in the status row) ──

/// The newest session-event marker block.
fn last_marker_block(agent: &AgentView) -> &SessionEventBlock {
    (0..agent.scrollback.len())
        .rev()
        .find_map(|i| match agent.scrollback.get(i).map(|e| &e.block) {
            Some(RenderBlock::SessionEvent(b)) => Some(b),
            _ => None,
        })
        .expect("a session-event marker must exist")
}

#[test]
fn workless_marker_renders_legacy_text() {
    let mut agent = running_driver("p1");

    push_turn_terminal_marker(
        &mut agent,
        Some(SessionEvent::TurnCompleted {
            elapsed: Some(std::time::Duration::from_secs(2)),
        }),
    );

    let block = last_marker_block(&agent);
    assert_eq!(block.event.message(), "Worked for 2.0s");
}

#[test]
fn no_event_pushes_no_marker() {
    // Bash turns and the rate-limit / re-auth UX replace the marker.
    let mut agent = running_driver("p1");
    let len_before = agent.scrollback.len();

    push_turn_terminal_marker(&mut agent, None);

    assert_eq!(agent.scrollback.len(), len_before);
}
