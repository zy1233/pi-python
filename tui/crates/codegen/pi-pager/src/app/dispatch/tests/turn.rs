//! Tests for turn cancellation, subagent kills, and cancel preferences.

use super::*;

/// Regression (Steer): a queued prompt's parked `session/prompt` RPC
/// can resolve as an *error* — e.g. its `respond_to` is dropped on the
/// agent when the prompt is removed from the shared queue, surfacing as
/// `Internal error: "session failed to respond"`. An `acp::Error` carries
/// no `promptId`, so before the Err-arm gate this error was misattributed
/// to the running turn and rendered as a spurious "Turn failed", detonating
/// an unrelated in-flight turn. The handler now gates the Err arm on the
/// `prompt_id` the pager minted for that RPC: an error whose id is NOT the
/// running turn is discarded; the running turn is left untouched.
#[test]
fn queued_prompt_rpc_error_does_not_kill_running_turn() {
    let _steer = SteerFollowUp::enter();
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // First prompt drains immediately → Running. Capture its prompt_id.
    let effects = dispatch(Action::SendPrompt("running".into()), &mut app);
    let running_pid = match &effects[0] {
        Effect::SendPrompt { prompt_id, .. } => prompt_id.clone(),
        other => panic!("expected SendPrompt, got {other:?}"),
    };
    assert!(app.agents[&id].session.state.is_turn_running());
    assert_eq!(
        app.agents[&id].session.current_prompt_id.as_deref(),
        Some(running_pid.as_str())
    );

    // Second prompt typed while running → immediate server-authoritative
    // send (queued at the agent). Capture its prompt_id.
    let effects = dispatch(Action::SendPrompt("queued".into()), &mut app);
    let queued_pid = match &effects[0] {
        Effect::SendPrompt { prompt_id, .. } => prompt_id.clone(),
        other => panic!("expected immediate SendPrompt, got {other:?}"),
    };
    assert_ne!(running_pid, queued_pid);

    let scrollback_before = app.agents[&id].scrollback.len();

    // The queued prompt is removed; its parked RPC resolves Err.
    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Err("Internal error: session failed to respond".to_string()),
            http_status: None,
            prompt_id: Some(queued_pid.clone()),
        }),
        &mut app,
    );

    // Discarded: no effects, running turn untouched, no "Turn failed" block.
    assert!(
        effects.is_empty(),
        "a queued prompt's RPC error must be discarded, got {effects:?}"
    );
    assert!(
        app.agents[&id].session.state.is_turn_running(),
        "the running turn must survive a queued prompt's RPC error"
    );
    assert_eq!(
        app.agents[&id].session.current_prompt_id.as_deref(),
        Some(running_pid.as_str()),
        "current_prompt_id must still point at the running turn"
    );
    assert_eq!(
        app.agents[&id].scrollback.len(),
        scrollback_before,
        "no TurnFailed block may be pushed for a non-running prompt's error"
    );

    // Sanity: an error for the ACTUAL running prompt is NOT discarded — it
    // ends the turn and renders the failure.
    let _ = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Err("upstream boom".to_string()),
            http_status: None,
            prompt_id: Some(running_pid.clone()),
        }),
        &mut app,
    );
    assert!(
        !app.agents[&id].session.state.is_turn_running(),
        "the running turn's own error must end the turn"
    );
    assert!(
        app.agents[&id].scrollback.len() > scrollback_before,
        "the running turn's own error must render a failure block"
    );
}

#[test]
fn cta_install_done_skills_only_settles_installed_without_fetch() {
    use crate::app::agent_view::CtaPhase;
    use pi_hooks_plugins_types::OutcomeStatus;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let cta = &mut app.agents.get_mut(&id).unwrap().plugin_cta;
        cta.phase = CtaPhase::Installing {
            plugin_relative_path: "plugins/figma".into(),
            name: "figma".into(),
        };
        cta.expects_mcp = false;
    }
    let effects = dispatch(
        Action::TaskComplete(TaskResult::CtaPluginInstallDone {
            agent_id: id,
            plugin_name: "figma".into(),
            result: Ok(cta_outcome(OutcomeStatus::Success, "installed")),
        }),
        &mut app,
    );
    // No MCP fetch, no "Setting up…" flash: straight to Installed.
    assert_eq!(
        app.agents[&id].plugin_cta.phase,
        CtaPhase::Installed {
            name: "figma".into()
        }
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::FetchPluginCtaMcps { .. }))
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::DismissCtaInstalled { .. }))
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::FetchPluginCtaCatalog { .. }))
    );
}

#[test]
fn cta_reload_done_skills_only_settles_installed_without_fetch() {
    use crate::app::agent_view::CtaPhase;
    use pi_hooks_plugins_types::OutcomeStatus;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let cta = &mut app.agents.get_mut(&id).unwrap().plugin_cta;
        cta.phase = CtaPhase::AwaitingReload {
            name: "figma".into(),
        };
        cta.expects_mcp = false;
    }
    let effects = dispatch(
        Action::TaskComplete(TaskResult::CtaPluginReloadDone {
            agent_id: id,
            plugin_name: "figma".into(),
            result: Ok(cta_outcome(OutcomeStatus::Success, "reloaded")),
        }),
        &mut app,
    );
    assert_eq!(
        app.agents[&id].plugin_cta.phase,
        CtaPhase::Installed {
            name: "figma".into()
        }
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::FetchPluginCtaMcps { .. }))
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::DismissCtaInstalled { .. }))
    );
}

#[test]
fn cancel_turn_cancels_immediately() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;

    let effects = dispatch(Action::CancelTurn, &mut app);

    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::CancelTurn {
            ..
        }
    ));
    assert!(app.agents[&id].session.state.is_cancelling());
}

#[test]
fn cancel_turn_forwards_trigger_hint_to_effect() {
    // The key/mouse producer sets `cancel_trigger_hint` (here ESC) before
    // dispatching CancelTurn; `do_cancel_turn` must forward it onto
    // `Effect::CancelTurn.trigger` (→ `_meta.cancelTrigger`) and consume it.
    // This is the same plumbing the Ctrl+C end-to-end test exercises; only
    // the `CancelTrigger` value differs across producers (esc/ctrl_c/mouse).
    use crate::app::actions::CancelTrigger;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.cancel_trigger_hint = Some(CancelTrigger::Esc);
    }

    let effects = dispatch(Action::CancelTurn, &mut app);

    assert!(matches!(
        &effects[0],
        Effect::CancelTurn {
            trigger: Some(CancelTrigger::Esc),
            ..
        }
    ));
    // One-shot: consumed when the cancel is built.
    assert_eq!(app.agents[&id].cancel_trigger_hint, None);
}

#[test]
fn cancel_turn_without_trigger_hint_sends_none() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;

    let effects = dispatch(Action::CancelTurn, &mut app);

    assert!(matches!(
        &effects[0],
        Effect::CancelTurn { trigger: None, .. }
    ));
}

#[test]
fn lost_cancel_is_resent_while_still_cancelling() {
    use crate::app::actions::CancelTrigger;
    use crate::app::dispatch::CANCEL_RESEND_GRACE;
    use crate::app::dispatch::reconcile_overdue_cancels;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.cancel_trigger_hint = Some(CancelTrigger::Mouse);
    }
    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(matches!(effects.as_slice(), [Effect::CancelTurn { .. }]));
    assert!(app.agents[&id].session.state.is_cancelling());

    // Inside the grace: nothing fires.
    assert!(reconcile_overdue_cancels(&mut app).is_none());

    // The cancel is lost in transit (no response ever arrives); age it out.
    app.agents
        .get_mut(&id)
        .unwrap()
        .pending_cancel_resend
        .as_mut()
        .unwrap()
        .sent_at = std::time::Instant::now() - CANCEL_RESEND_GRACE;
    let resent = reconcile_overdue_cancels(&mut app).expect("overdue cancel must re-send");
    assert!(
        matches!(
            resent.as_slice(),
            [Effect::CancelTurn {
                trigger: Some(CancelTrigger::Mouse),
                rewind_prompt_id: None,
                ..
            }]
        ),
        "the resend replays the gesture trigger, got {resent:?}"
    );
    assert_eq!(
        app.agents[&id]
            .pending_cancel_resend
            .as_ref()
            .unwrap()
            .attempts,
        2
    );

    // A received `prompt_complete` broadcast proves the cancel landed: the
    // resend stops even though the pane is still cancelling, so it can
    // never race the turn-end reconcile and cancel a promoted queued prompt.
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.pending_cancel_resend.as_mut().unwrap().sent_at =
            std::time::Instant::now() - CANCEL_RESEND_GRACE;
        agent.pending_turn_end_reconcile = Some(crate::app::agent_view::PendingTurnEnd {
            prompt_id: "p1".into(),
            stop_reason: Some("cancelled".into()),
            agent_result: None,
            cancel_trigger: None,
            cancellation_category: None,
            received_at: std::time::Instant::now(),
        });
    }
    assert!(reconcile_overdue_cancels(&mut app).is_none());
    // The record survives, confirmed: the auto-resend is dead, but a manual
    // retry can still read the recorded subagent choice.
    assert!(
        app.agents[&id]
            .pending_cancel_resend
            .as_ref()
            .unwrap()
            .confirmed
    );
    app.agents.get_mut(&id).unwrap().pending_turn_end_reconcile = None;
    assert!(
        reconcile_overdue_cancels(&mut app).is_none(),
        "a confirmed record keeps the auto-resend off after the window closes"
    );

    // Turn resolved: the marker clears and nothing more fires.
    app.agents.get_mut(&id).unwrap().session.state = AgentState::Idle;
    assert!(reconcile_overdue_cancels(&mut app).is_none());
    assert!(app.agents[&id].pending_cancel_resend.is_none());
}

#[test]
fn confirmed_stop_retry_does_not_rearm_auto_resend() {
    use crate::app::actions::CancelTrigger;
    use crate::app::dispatch::CANCEL_RESEND_GRACE;
    use crate::app::dispatch::reconcile_overdue_cancels;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.cancel_trigger_hint = Some(CancelTrigger::Mouse);
    }
    assert!(matches!(
        dispatch(Action::CancelTurn, &mut app).as_slice(),
        [Effect::CancelTurn {
            trigger: Some(CancelTrigger::Mouse),
            ..
        }]
    ));

    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.pending_turn_end_reconcile = Some(crate::app::agent_view::PendingTurnEnd {
            prompt_id: "p1".into(),
            stop_reason: Some("cancelled".into()),
            agent_result: None,
            cancel_trigger: None,
            cancellation_category: None,
            received_at: std::time::Instant::now(),
        });
    }
    assert!(reconcile_overdue_cancels(&mut app).is_none());
    assert!(
        app.agents[&id]
            .pending_cancel_resend
            .as_ref()
            .is_some_and(|p| p.confirmed)
    );

    // Gesture retry (hint set, as `[stop]` / Esc do).
    app.agents.get_mut(&id).unwrap().cancel_trigger_hint = Some(CancelTrigger::Mouse);
    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                trigger: Some(CancelTrigger::Mouse),
                ..
            }]
        ),
        "a manual retry still re-sends, got {effects:?}"
    );
    let pending = app.agents[&id]
        .pending_cancel_resend
        .as_ref()
        .expect("resend record must survive");
    assert!(
        pending.confirmed,
        "a confirmed record must stay confirmed across a gesture retry"
    );

    app.agents
        .get_mut(&id)
        .unwrap()
        .pending_cancel_resend
        .as_mut()
        .unwrap()
        .sent_at = std::time::Instant::now() - CANCEL_RESEND_GRACE;
    assert!(
        reconcile_overdue_cancels(&mut app).is_none(),
        "auto-resend must stay off after a confirmed gesture retry"
    );
}

#[test]
fn hintless_retry_replays_recorded_trigger() {
    use crate::app::actions::CancelTrigger;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.cancel_trigger_hint = Some(CancelTrigger::Esc);
    }
    assert!(matches!(
        dispatch(Action::CancelTurn, &mut app).as_slice(),
        [Effect::CancelTurn {
            trigger: Some(CancelTrigger::Esc),
            ..
        }]
    ));

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                trigger: Some(CancelTrigger::Esc),
                ..
            }]
        ),
        "a hint-less retry must replay the recorded trigger, got {effects:?}"
    );
}

#[test]
fn cancel_turn_stops_compact_even_with_stale_wake_marker() {
    use crate::app::actions::CancelTrigger;
    use crate::app::agent::AgentCommand;
    use crate::app::agent_view::RunningWakeTurn;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.start_command(AgentCommand::Compact);
        agent.running_wake_turn = Some(RunningWakeTurn {
            prompt_id: "task-completed-bg1".into(),
            cancel_sent: false,
        });
        agent.cancel_trigger_hint = Some(CancelTrigger::Esc);
    }

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(effects.as_slice(), [Effect::CancelTurn { .. }]),
        "compact cancel must emit, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert!(
        matches!(
            agent.session.state,
            AgentState::CommandCancelling {
                command: AgentCommand::Compact,
            }
        ),
        "Esc during /compact must cancel compact, not only the stale wake, got {:?}",
        agent.session.state
    );
}

#[test]
fn cancel_after_local_send_during_wake_does_not_arm_resend() {
    use crate::app::actions::CancelTrigger;
    use crate::app::agent_view::RunningWakeTurn;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.running_wake_turn = Some(RunningWakeTurn {
            prompt_id: "task-completed-bg1".into(),
            cancel_sent: false,
        });
        agent.start_turn_boundary(Some("user-1"));
        agent.session.current_prompt_id = Some("user-1".into());
        agent.cancel_trigger_hint = Some(CancelTrigger::Esc);
    }

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                trigger: Some(CancelTrigger::Esc),
                rewind_prompt_id: None,
                ..
            }]
        ),
        "must still cancel the shell-front wake, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert!(
        agent.session.state.is_turn_running(),
        "the local user turn is queued on the shell, not cancelled"
    );
    assert!(
        agent.pending_cancel_resend.is_none(),
        "auto-resend would cancel the promoted user turn"
    );
    assert!(
        agent
            .running_wake_turn
            .as_ref()
            .is_some_and(|w| w.cancel_sent),
        "the wake marker must record the cancel"
    );
}

#[test]
fn stale_cancel_resend_clears_once_pane_is_idle() {
    use crate::app::actions::CancelTrigger;
    use crate::app::dispatch::reconcile_overdue_cancels;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::Idle;
        agent.pending_cancel_resend = Some(crate::app::agent_view::PendingCancelResend {
            prompt_id: None,
            sent_at: std::time::Instant::now(),
            attempts: 3,
            confirmed: true,
            trigger: CancelTrigger::Esc,
        });
    }
    assert!(reconcile_overdue_cancels(&mut app).is_none());
    assert!(
        app.agents[&id].pending_cancel_resend.is_none(),
        "reconcile must drop a stale record once nothing is cancelling"
    );
}

#[test]
fn do_cancel_turn_cancels_running_wake_turn() {
    use crate::app::agent_view::RunningWakeTurn;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.running_wake_turn = Some(RunningWakeTurn {
            prompt_id: "task-completed-bg1".into(),
            cancel_sent: false,
        });
    }

    let effects = super::super::turn::do_cancel_turn(
        &mut app,
        crate::app::cancel_latency::CancelOrigin::UserGesture,
    );
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                rewind_prompt_id: None,
                trigger: None,
                ..
            }]
        ),
        "programmatic cancel must stop a wake turn, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert!(agent.session.state.is_idle());
    assert!(agent.wake_turn_cancelling());
}

#[test]
fn stop_click_cancels_running_wake_turn() {
    use crate::app::actions::CancelTrigger;
    use crate::app::agent_view::RunningWakeTurn;
    use crate::app::dispatch::CANCEL_RESEND_GRACE;
    use crate::app::dispatch::reconcile_overdue_cancels;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.running_wake_turn = Some(RunningWakeTurn {
            prompt_id: "task-completed-bg1".into(),
            cancel_sent: false,
        });
        assert!(
            matches!(agent.wake_display_state(), Some(AgentState::TurnRunning)),
            "a streaming wake turn must offer the running chrome (and [stop])"
        );
        // The mouse handler sets the hint before dispatching CancelTurn.
        agent.cancel_trigger_hint = Some(CancelTrigger::Mouse);
    }

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                trigger: Some(CancelTrigger::Mouse),
                rewind_prompt_id: None,
                ..
            }]
        ),
        "the wake cancel must ride the normal cancel wire, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert!(
        agent.session.state.is_idle(),
        "a wake cancel must not fabricate a local turn"
    );
    assert!(matches!(
        agent.wake_display_state(),
        Some(AgentState::TurnCancelling)
    ));

    // The fire-and-forget cancel is loss-prone: the resend reconcile must
    // stay armed even though the pane never left Idle.
    app.agents
        .get_mut(&id)
        .unwrap()
        .pending_cancel_resend
        .as_mut()
        .unwrap()
        .sent_at = std::time::Instant::now() - CANCEL_RESEND_GRACE;
    assert!(reconcile_overdue_cancels(&mut app).is_some());
}

#[test]
fn cancel_turn_leaves_shared_queue_for_agent_to_drain() {
    use crate::app::prompt_queue::QueueEntryWire;
    // Prompts typed while a turn runs live on the server-authoritative
    // shared queue (broadcast to all attached clients). The agent owns the
    // drain: on cancel the FRONT queued prompt runs next (promoted
    // server-side), so the pager must NOT pull it back into the input or
    // mutate the queue locally — the `pi/queue/changed` rebroadcast is the
    // source of truth.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.shared_queue = vec![
            QueueEntryWire {
                id: "q1".into(),
                version: 3,
                owner: None,
                last_editor: None,
                kind: "prompt".into(),
                text: "first queued".into(),
                position: 0,
                combined_texts: None,
            },
            QueueEntryWire {
                id: "q2".into(),
                version: 4,
                owner: None,
                last_editor: None,
                kind: "prompt".into(),
                text: "second queued".into(),
                position: 1,
                combined_texts: None,
            },
        ];
        assert!(agent.prompt.text().is_empty());
    }

    let effects = dispatch(Action::CancelTurn, &mut app);

    // The input box is left untouched — the front queued prompt is NOT
    // pulled back into it (it runs next on the agent instead).
    assert!(
        app.agents[&id].prompt.text().is_empty(),
        "cancel must not restore a queued prompt into the input"
    );
    // The local mirror is left intact; the agent's rebroadcast drives the
    // queue, so the pager must not predict the post-cancel order.
    let q = &app.agents[&id].shared_queue;
    assert_eq!(
        q.len(),
        2,
        "cancel must not mutate the shared queue locally"
    );
    assert_eq!(q[0].id, "q1");
    assert_eq!(q[1].id, "q2");
    // A plain CancelTurn is emitted (no queued-prompt id threaded, no
    // separate QueueRemove) — the agent tears down the running turn and
    // promotes q1 as the next turn.
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CancelTurn { .. })),
        "must emit CancelTurn, got {effects:?}"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::QueueRemove { .. })),
        "must NOT emit a separate QueueRemove on cancel"
    );
}

#[test]
fn cancel_turn_when_idle_does_nothing() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    assert!(app.agents[&id].session.state.is_idle());

    let effects = dispatch(Action::CancelTurn, &mut app);

    assert!(effects.is_empty());
    assert!(app.agents[&id].session.state.is_idle());
}

#[test]
fn cancel_turn_when_already_cancelling_resends_cancel() {
    // A cancel that was sent but never resolved (lost notification or
    // lost turn-end response) used to make every further
    // Esc a silent no-op, permanently stranding the pane on
    // "Cancelling…". Cancelling again must RE-SEND the (idempotent)
    // cancel instead.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnCancelling;

    let effects = dispatch(Action::CancelTurn, &mut app);

    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                ..
            }]
        ),
        "cancel while cancelling must re-send the cancel, got {effects:?}"
    );
    assert!(app.agents[&id].session.state.is_cancelling());
}

/// The latched-cancel deadlock: cancel sent → state
/// `TurnCancelling` → the turn's PromptResponse RPC is lost → nothing can
/// ever exit the state. The armed broadcast marker must finish the turn
/// after the grace window.
#[test]
fn reconcile_finishes_cancelling_turn_after_grace() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnCancelling;
        agent.session.current_prompt_id = Some("pid-stuck".into());
    }
    arm_reconcile(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1),
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_some(), "an overdue marker must fire the reconcile");
    let agent = &app.agents[&id];
    assert!(
        agent.session.state.is_idle(),
        "reconcile must exit TurnCancelling"
    );
    assert!(agent.session.current_prompt_id.is_none());
    assert!(agent.pending_turn_end_reconcile.is_none());
    let has_cancelled_marker = (0..agent.scrollback.len()).any(|i| {
        matches!(
            agent.scrollback.entry(i).map(|e| &e.block),
            Some(RenderBlock::SessionEvent(ev))
                if matches!(ev.event, SessionEvent::TurnCancelled { .. })
        )
    });
    assert!(
        has_cancelled_marker,
        "reconcile must surface the 'Turn cancelled' marker"
    );
}

/// A lost-RPC reconcile for a send-now cancel (`_meta.cancelTrigger: "send_now"`) pushes no marker.
#[test]
fn reconcile_suppresses_send_now_cancel_marker() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.session.current_prompt_id = Some("pid-stuck".into());
    }
    arm_reconcile_with_trigger(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        Some("send_now"),
        TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1),
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_some(), "the overdue reconcile must still fire");
    let agent = &app.agents[&id];
    assert!(agent.session.state.is_idle(), "the turn still finishes");
    let has_marker = (0..agent.scrollback.len()).any(|i| {
        matches!(
            agent.scrollback.entry(i).map(|e| &e.block),
            Some(RenderBlock::SessionEvent(ev))
                if matches!(
                    ev.event,
                    SessionEvent::TurnCancelled { .. } | SessionEvent::TurnCompleted { .. }
                )
        )
    });
    assert!(
        !has_marker,
        "a send-now cancel reconcile must not push a cancelled (or substitute \
         completed) marker"
    );
}

/// A lost-RPC reconcile for a hook-denied cancel consumes the parked
/// `cancellationCategory` and renders the blocked-by-a-hook marker, not
/// "cancelled by user".
#[test]
fn reconcile_renders_hook_denied_marker_from_parked_category() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.session.current_prompt_id = Some("pid-stuck".into());
    }
    arm_reconcile_with_meta(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        None,
        Some(crate::app::turn_completion::HOOK_DENIED_CATEGORY),
        TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1),
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_some(), "the overdue reconcile must fire");
    let agent = &app.agents[&id];
    assert!(agent.session.state.is_idle());
    let has_blocked_marker = (0..agent.scrollback.len()).any(|i| {
        matches!(
            agent.scrollback.entry(i).map(|e| &e.block),
            Some(RenderBlock::SessionEvent(ev))
                if matches!(ev.event, SessionEvent::TurnBlockedByHook { .. })
        )
    });
    assert!(
        has_blocked_marker,
        "the reconcile must surface the blocked-by-a-hook marker"
    );
}

/// Older-shell fallback on the reconcile rail: no wire trigger, armed expectation.
#[test]
fn reconcile_suppresses_expected_send_now_cancel_without_wire_trigger() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.session.current_prompt_id = Some("pid-stuck".into());
        agent.expect_send_now_cancel = Some("p-next".into());
    }
    arm_reconcile(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1),
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_some());
    let agent = &app.agents[&id];
    let has_cancelled = (0..agent.scrollback.len()).any(|i| {
        matches!(
            agent.scrollback.entry(i).map(|e| &e.block),
            Some(RenderBlock::SessionEvent(ev))
                if matches!(ev.event, SessionEvent::TurnCancelled { .. })
        )
    });
    assert!(!has_cancelled, "expected send-now cancel renders no marker");
    assert!(
        agent.expect_send_now_cancel.is_none(),
        "the expectation is consumed by the reconcile"
    );
}

#[test]
fn reconcile_waits_for_grace_window() {
    // A freshly-armed marker means the RPC response may still be in
    // flight (healthy path: it lands milliseconds after the broadcast) —
    // do not touch the turn yet.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnCancelling;
        agent.session.current_prompt_id = Some("pid-stuck".into());
    }
    arm_reconcile(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        std::time::Duration::ZERO,
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_none());
    let agent = &app.agents[&id];
    assert!(agent.session.state.is_cancelling());
    assert!(
        agent.pending_turn_end_reconcile.is_some(),
        "marker must stay armed until grace expires"
    );
}

#[test]
fn reconcile_drops_stale_marker_when_turn_already_resolved() {
    // The normal path won the race (PromptResponse finished the turn, or
    // a new turn was adopted): the marker is stale and must be dropped
    // without touching state or pushing a marker.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let scrollback_before = app.agents[&id].scrollback.len();
    arm_reconcile(
        &mut app,
        id,
        "pid-old",
        "end_turn",
        TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1),
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_none(), "stale marker must not fire");
    let agent = &app.agents[&id];
    assert!(agent.session.state.is_idle());
    assert!(agent.pending_turn_end_reconcile.is_none());
    assert_eq!(agent.scrollback.len(), scrollback_before);
}

#[test]
fn reconcile_applies_stashed_running_adoption() {
    // The failing sequence: queued prompt promoted server-side while the
    // cancelled turn's response was lost. The reconcile must hand the pane
    // to the promoted prompt (turn-start shim), not strand it Idle.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnCancelling;
        agent.session.current_prompt_id = Some("pid-stuck".into());
    }
    // The leader's running_prompt_id broadcast arrived mid-teardown and
    // was stashed (same as the PromptResponse path).
    app.pending_running_adoptions.insert(
        id,
        crate::app::acp_handler::PendingRunningAdoption {
            prompt_id: "pid-next".into(),
            text: Some("queued prompt".into()),
            combined_texts: None,
            kind: "prompt".into(),
        },
    );
    arm_reconcile(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1),
    );

    let fired = reconcile_overdue_turn_ends(&mut app);

    assert!(fired.is_some());
    let agent = &app.agents[&id];
    assert_eq!(
        agent.session.current_prompt_id.as_deref(),
        Some("pid-next"),
        "the stashed adoption must be applied after the reconcile"
    );
    assert!(
        matches!(agent.session.state, AgentState::TurnRunning),
        "the promoted prompt is the new running turn"
    );
    assert!(!app.pending_running_adoptions.contains_key(&id));
}

/// The reconcile rail's `stop_reason == "error"` arm: formats the raw
/// agent_result and skips the marker when a dedicated banner already
/// explains the failure.
#[test]
fn reconcile_error_formats_marker_and_defers_to_banner() {
    fn run(with_banner: bool) -> Option<String> {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.session.state = AgentState::TurnRunning;
            agent.session.current_prompt_id = Some("pid-stuck".into());
            if with_banner {
                agent.scrollback.push_block(RenderBlock::session_event(
                    SessionEvent::RequestFailed {
                        status: Some(500),
                        headline: "Server error (500)".into(),
                        detail: String::new(),
                    },
                ));
            }
            agent.pending_turn_end_reconcile = Some(crate::app::agent_view::PendingTurnEnd {
                prompt_id: "pid-stuck".into(),
                stop_reason: Some("error".into()),
                agent_result: Some("boom".into()),
                cancel_trigger: None,
                cancellation_category: None,
                received_at: std::time::Instant::now()
                    - (TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1)),
            });
        }
        let fired = reconcile_overdue_turn_ends(&mut app);
        assert!(
            fired.is_some(),
            "the overdue reconcile must finish the turn"
        );
        let agent = &app.agents[&id];
        (0..agent.scrollback.len()).find_map(|i| {
            match agent.scrollback.entry(i).map(|e| &e.block) {
                Some(RenderBlock::SessionEvent(ev)) => match &ev.event {
                    SessionEvent::TurnFailed { error, .. } => Some(error.clone()),
                    _ => None,
                },
                _ => None,
            }
        })
    }

    assert_eq!(
        run(false).as_deref(),
        Some("Request failed: boom. Try sending again."),
        "the raw agent_result must render as a formatted marker"
    );
    assert_eq!(
        run(true),
        None,
        "a dedicated banner must suppress the reconcile's TurnFailed marker"
    );
}

#[test]
fn cancel_after_first_activity_does_not_restore() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    dispatch(Action::SendPrompt("keep me".into()), &mut app);
    assert!(app.agents[&id].session.in_flight_prompt.is_some());
    // Simulate that the server emitted activity (the acp_handler
    // clear-on-first-activity hook would have cleared this).
    app.agents.get_mut(&id).unwrap().session.in_flight_prompt = None;

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::CancelTurn { .. }));

    // Prompt was NOT restored; user-prompt block stays; state is
    // the normal TurnCancelling (not the rewind-Idle).
    assert!(app.agents[&id].prompt.text().is_empty());
    assert_eq!(app.agents[&id].scrollback.len(), 1);
    assert!(app.agents[&id].session.state.is_cancelling());

    // PromptResponse arrives — TurnCancelled banner is pushed.
    dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    // user_prompt + TurnCancelled banner.
    assert_eq!(app.agents[&id].scrollback.len(), 2);
}

/// Ctrl+C rewind of a locally-drained combined turn must remove *every*
/// per-segment user bubble (not just the last) and restore the joined text.
#[test]
fn cancel_rewind_removes_all_combined_segment_blocks() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let (first_id, last_id) = {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.session.current_prompt_id = Some("p-combo".into());
        // One bubble per original follow-up, as a combined drain paints them.
        let first_id = agent
            .scrollback
            .push_block(RenderBlock::user_prompt("first"));
        let last_id = agent
            .scrollback
            .push_block(RenderBlock::user_prompt("second"));
        agent.session.in_flight_prompt = Some(crate::app::agent::InFlightPrompt {
            text: "first\n\nsecond".into(),
            images: Vec::new(),
            scrollback_entry: last_id,
            combined_scrollback_entries: vec![first_id],
            chip_elements: Vec::new(),
        });
        (first_id, last_id)
    };

    let _ = dispatch(Action::CancelTurn, &mut app);

    let agent = &app.agents[&id];
    assert!(
        agent.scrollback.index_of_id(first_id).is_none(),
        "the earlier segment bubble must also be removed on rewind"
    );
    assert!(
        agent.scrollback.index_of_id(last_id).is_none(),
        "the primary segment bubble must be removed on rewind"
    );
    assert_eq!(
        agent.prompt.text(),
        "first\n\nsecond",
        "the joined combined text is restored into the composer"
    );
}

/// A cancel landing before first server activity must NOT rewind the stashed
/// in-flight prompt over a NEWER composer draft. Esc (and the mouse stop /
/// palette cancel) fire with the draft intact — unlike keyboard Ctrl+C,
/// which only cancels on an empty prompt — so the no-output rewind falls back
/// to the standard cancel and the draft survives.
#[test]
fn cancel_with_newer_draft_skips_no_output_rewind_and_keeps_draft() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let sent_id = {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.session.current_prompt_id = Some("p-sent".into());
        let sent_id = agent
            .scrollback
            .push_block(RenderBlock::user_prompt("sent prompt"));
        agent.session.in_flight_prompt = Some(crate::app::agent::InFlightPrompt {
            text: "sent prompt".into(),
            images: Vec::new(),
            scrollback_entry: sent_id,
            combined_scrollback_entries: Vec::new(),
            chip_elements: Vec::new(),
        });
        // Typed WHILE the turn was starting — newer than the stash.
        agent.prompt.set_text("newer draft");
        sent_id
    };

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(effects.as_slice(), [Effect::CancelTurn { .. }]),
        "cancel still flies to the server, got {effects:?}"
    );

    let agent = &app.agents[&id];
    assert_eq!(
        agent.prompt.text(),
        "newer draft",
        "the composer draft must survive the cancel (no rewind clobber)"
    );
    assert!(
        agent.scrollback.index_of_id(sent_id).is_some(),
        "standard cancel keeps the sent prompt's block (no rewind removal)"
    );
    assert!(
        agent.session.state.is_cancelling(),
        "standard cancel path (TurnCancelling), not the rewind-Idle"
    );
}

#[test]
fn entry_title_strips_skill_xml_from_generated_title() {
    use crate::views::session_title::entry_title;
    let mut app = test_app_with_agent();
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.generated_session_title = Some(
        "<command-name>implement</command-name>\n\
             <command-message>/implement</command-message>\n\
             <command-args>fix the rendering bug</command-args>"
            .into(),
    );
    let title = entry_title(&app.agents[&AgentId(0)]);
    assert_eq!(title, "/implement fix the rendering bug");
}

#[test]
fn entry_title_strips_skill_xml_from_first_prompt() {
    use crate::scrollback::block::RenderBlock;
    use crate::views::session_title::entry_title;
    let mut app = test_app_with_agent();
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.scrollback.push_block(RenderBlock::user_prompt(
        "<command-name>deploy</command-name>\n\
             <command-message>/deploy</command-message>",
    ));
    let title = entry_title(&app.agents[&AgentId(0)]);
    assert_eq!(title, "/deploy");
}

#[test]
fn prompt_history_loaded_sanitizes_skill_xml() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let prompts = vec![
        "<command-name>review</command-name>\n\
             <command-message>/review</command-message>\n\
             <command-args>198653</command-args>"
            .into(),
        "plain prompt".into(),
        "<command-name>deploy</command-name>".into(),
    ];

    dispatch(
        Action::TaskComplete(TaskResult::PromptHistoryLoaded {
            agent_id: id,
            prompts,
        }),
        &mut app,
    );

    let history = &app.agents[&id].session.prompt_history;
    assert_eq!(history[0], "/review 198653");
    assert_eq!(history[1], "plain prompt");
    assert_eq!(history[2], "/deploy");
}







#[test]
fn fork_failure_force_idle_drops_a_live_cancel_anchor() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let _ = super::super::turn::do_cancel_turn(
        &mut app,
        crate::app::cancel_latency::CancelOrigin::UserGesture,
    );
    assert!(
        app.agents[&id].cancel_latency.is_some(),
        "the cancel armed the anchor"
    );

    let _ = super::super::session::fork::handle_fork_session_failed(&mut app, id, "boom".into());

    assert!(
        app.agents[&id].cancel_latency.is_none(),
        "the fork teardown drops the anchor (no leak into a later settle)"
    );
}

#[test]
fn settled_cancel_emits_latency_from_arm_anchor_once() {
    use crate::app::cancel_latency::{CancelLatency, CancelOrigin, TurnEnd};
    use std::time::{Duration, Instant};
    use pi_telemetry::events::CancellationScope;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;

    let _ = super::super::turn::do_cancel_turn(&mut app, CancelOrigin::UserGesture);
    assert_eq!(
        app.agents[&id].cancel_latency.map(|c| c.scope),
        Some(CancellationScope::Turn),
        "the real arm path armed a Turn-scoped anchor"
    );

    let t0 = Instant::now();
    let agent = app.agents.get_mut(&id).unwrap();
    agent.cancel_latency = Some(CancelLatency::new(t0, CancellationScope::Turn));

    let event = agent
        .settle_cancel(TurnEnd::Completed, t0 + Duration::from_millis(50))
        .expect("a settled turn emits the pending anchor");
    assert_eq!(event.latency_ms, 50);
    assert_eq!(event.scope, CancellationScope::Turn);

    assert!(
        agent
            .settle_cancel(TurnEnd::Completed, t0 + Duration::from_millis(999))
            .is_none(),
        "the anchor is consumed, so a second settle emits nothing"
    );

    agent.cancel_latency = Some(CancelLatency::new(t0, CancellationScope::Turn));
    assert!(
        agent
            .settle_cancel(TurnEnd::Aborted, t0 + Duration::from_millis(50))
            .is_none(),
        "a torn-down turn discards the anchor unmeasured"
    );
}

#[test]
fn cancel_and_arm_anchors_before_the_cancel_teardown() {
    use crate::app::cancel_latency::CancelOrigin;
    use pi_telemetry::events::CancellationScope;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    crate::app::agent_view::test_fixtures::add_running_execute(app.agents.get_mut(&id).unwrap());

    let agent = app.agents.get_mut(&id).unwrap();
    assert!(
        agent.scrollback.last().is_some_and(|e| e.is_running),
        "the running tool entry is live before the cancel"
    );

    agent.cancel_and_arm(CancellationScope::Turn, CancelOrigin::UserGesture);

    let requested_at = agent
        .cancel_latency
        .expect("the user gesture armed the anchor")
        .requested_at;
    let teardown_at = agent
        .scrollback
        .last()
        .and_then(|e| e.finished_at)
        .expect("the cancel teardown finished the running entry");
    assert!(
        requested_at <= teardown_at,
        "the latency anchor must be sampled before the cancel teardown finishes the turn"
    );
}
