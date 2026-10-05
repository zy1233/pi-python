//! Turn cancellation and overdue turn reconciliation.

use super::permissions::drain_permission_queue;
use super::queue::maybe_drain_queue;
use crate::app::actions::Effect;
use crate::app::agent::AgentId;
use crate::app::agent_view::AgentView;
use crate::app::app_view::{ActiveView, AppView};
use crate::app::cancel_latency::{CancelOrigin, TurnEnd};
use crate::scrollback::blocks::SessionEvent;
use std::time::Instant;
use pi_telemetry::events::CancellationScope;

pub(super) fn dispatch_cancel_turn(app: &mut AppView) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };

    // Scoped agent borrow: extract decisions, then release before `do_cancel_turn`.
    {
        let Some(agent) = app.agents.get_mut(&id) else {
            return vec![];
        };
        // Retry path: a cancel was already sent (`TurnCancelling`) but the turn
        // never resolved — the `session/cancel` notification or the turn-end
        // response may have been lost in transit. Re-send instead of silently
        // no-opping (cancel is idempotent on the agent), so Ctrl+C / palette
        // CancelTurn is never a dead key on a stuck "Cancelling…" spinner.
        if agent.session.state.is_cancelling() {
            let Some(session_id) = agent.session.session_id.clone() else {
                return vec![];
            };
            crate::unified_log::info(
                "cancel.retry",
                Some(&session_id.0),
                Some(serde_json::json!({
                    "current_prompt_id": agent.session.current_prompt_id,
                })),
            );
            return vec![emit_cancel_turn(
                agent,
                session_id,
                /* rewind_prompt_id */ None,
            )];
        }
        // Compact owns the pane (`CommandRunning`) even if a leftover wake
        // marker is still set — `/compact` can drain while that marker is live.
        // Must beat the wake early-return or Esc never calls cancel_compact.
        if !agent.session.state.is_compact_running() {
            if agent.running_wake_turn.is_some() {
                // Marker, not idle-only: a local send during a wake start_turn's
                // the pane while the shell front is still the wake. Cancel that
                // wake; the queued user prompt must survive.
                let Some(session_id) = agent.session.session_id.clone() else {
                    return vec![];
                };
                agent.mark_wake_cancel_sent();
                return vec![emit_cancel_turn(
                    agent,
                    session_id,
                    /* rewind_prompt_id */ None,
                )];
            }
            if !agent.session.state.is_turn_running() {
                return vec![];
            }
        }
    }

    do_cancel_turn(app, CancelOrigin::UserGesture)
}

pub(super) fn do_cancel_turn(app: &mut AppView, origin: CancelOrigin) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };
    let cancel_rewind_enabled = app.cancel_rewind_enabled;
    let Some(agent) = app.agents.get_mut(&id) else {
        return vec![];
    };
    cancel_agent_turn(agent, cancel_rewind_enabled, origin)
}

fn cancel_agent_turn(
    agent: &mut AgentView,
    cancel_rewind_enabled: bool,
    origin: CancelOrigin,
) -> Vec<Effect> {
    if agent.session.state.is_compact_running() {
        agent.cancel_and_arm(CancellationScope::Compaction, origin);
        drain_permission_queue(agent);
        let Some(session_id) = agent.session.session_id.clone() else {
            return vec![];
        };
        return vec![emit_cancel_turn(
            agent,
            session_id,
            /* rewind_prompt_id */ None,
        )];
    }
    if agent.running_wake_turn.is_some() {
        let Some(session_id) = agent.session.session_id.clone() else {
            return vec![];
        };
        agent.mark_wake_cancel_sent();
        return vec![emit_cancel_turn(
            agent,
            session_id,
            /* rewind_prompt_id */ None,
        )];
    }
    if !agent.session.state.is_turn_running() {
        return vec![];
    }
    // If the server hasn't emitted any activity yet AND there are no other
    // queued prompts, "rewind" the prompt back into the input box and remove
    // its scrollback block. The cancel notification still flies to the
    // server, but the local turn state is reset to Idle immediately so the
    // UI looks like the user never hit Send.
    //
    // Skip rewind when queued prompts exist: restoring the in-flight prompt
    // to the input box while the next queued prompt drains would mix two
    // user intentions in confusing ways. Fall back to the standard cancel
    // flow in that case.
    //
    // Clearing `current_prompt_id` (via `finish_turn`) is what makes orphan
    // chunks/PR for the cancelled turn get dropped by the `promptId` gate
    // in acp_handler / PromptResponse handler.
    // Minimal mode prints each committed block once into the terminal's native
    // scrollback, and that print can't be "un-printed". A user-prompt block
    // commits immediately (it is never `is_running`), so a just-promoted queued
    // prompt's block is already in native scrollback by the time the user can
    // cancel it. Rewinding then `remove_entry`s it from scrollback *state* while
    // the printed copy stays on screen AND restores the text into the input —
    // showing the prompt twice (dogfood bug: double-Esc on a queued prompt). Skip
    // the rewind when the in-flight block has already committed and fall back to
    // the standard cancel. `committed` is always false in alt-screen / inline, so
    // this is a no-op outside minimal.
    let in_flight_committed = match agent.session.in_flight_prompt.as_ref() {
        Some(stashed) => agent.scrollback.is_committed(stashed.scrollback_entry),
        None => false,
    };
    // The rewind REPLACES the composer with the stashed in-flight prompt.
    // Esc (and the mouse stop / palette cancel) fire with the draft intact —
    // unlike keyboard Ctrl+C, which only cancels on an empty prompt — so a
    // non-empty composer holds a NEWER draft the rewind would clobber.
    // Trigger-agnostic on purpose: fall back to the standard cancel.
    let composer_has_draft = !agent.prompt.text().is_empty() || !agent.prompt.images.is_empty();
    // Captured before `finish_turn` clears it; no id → standard cancel.
    let rewind_prompt_id = agent.session.current_prompt_id.clone();
    let rewinding = cancel_rewind_enabled
        && agent.session.in_flight_prompt.is_some()
        && agent.session.pending_prompts.is_empty()
        && !in_flight_committed
        && !composer_has_draft
        && rewind_prompt_id.is_some();
    if rewinding && let Some(stashed) = agent.session.in_flight_prompt.take() {
        if let Some(pid) = rewind_prompt_id.as_deref() {
            agent.note_rewound_prompt(pid);
        }
        agent.prompt.set_text(&stashed.text);
        agent.prompt.restore_chip_elements(&stashed.chip_elements);
        agent.prompt.set_images(stashed.images);
        agent.prompt.set_cursor(stashed.text.len());
        for id in stashed.combined_scrollback_entries {
            agent.scrollback.remove_entry(id);
        }
        agent.scrollback.remove_entry(stashed.scrollback_entry);
        // Full state reset: tracker cleanup + state Idle + clear timing
        // fields + clear current_prompt_id.
        agent.session.finish_turn(&mut agent.scrollback);
        agent.turn_started_at = None;
        agent.activity_started_at = None;
        agent.last_activity = None;
    } else {
        agent.cancel_and_arm(CancellationScope::Turn, origin);
    }
    drain_permission_queue(agent);
    if let Some(mut pav) = agent.plan_approval_view.take() {
        pav.send_stale_cancel();
        agent.plan_next_comment_id = pav.next_comment_id;
        agent.prompt.restore(pav.stashed_prompt);
        agent.line_viewer = None;
    }

    let Some(session_id) = agent.session.session_id.clone() else {
        return vec![];
    };

    // `rewinding` mirrors the local rewind on the wire so the shell trims
    // its stored copy too.
    vec![emit_cancel_turn(
        agent,
        session_id,
        if rewinding { rewind_prompt_id } else { None },
    )]
}

/// Build `Effect::CancelTurn`, consuming the gesture hint and arming the
/// resend reconcile (skipped for a rewind, which leaves no cancelling state).
pub(super) fn emit_cancel_turn(
    agent: &mut crate::app::agent_view::AgentView,
    session_id: agent_client_protocol::SessionId,
    rewind_prompt_id: Option<String>,
) -> Effect {
    let rewind_if_no_output = rewind_prompt_id.is_some();
    let target_prompt_id = if agent.session.state.is_compact_running()
        || matches!(
            agent.session.state,
            crate::app::agent::AgentState::CommandCancelling {
                command: crate::app::agent::AgentCommand::Compact,
            }
        ) {
        agent.session.current_prompt_id.clone()
    } else {
        agent
            .running_wake_turn
            .as_ref()
            .map(|wake| wake.prompt_id.clone())
            .or_else(|| agent.session.current_prompt_id.clone())
    };
    // Prefer the live hint; a hint-less retry (palette) replays the
    // recorded gesture so the shell still arms the wake barrier.
    let trigger = agent
        .cancel_trigger_hint
        .take()
        .or_else(|| agent.pending_cancel_resend.as_ref().map(|p| p.trigger));
    // A local send during a wake adopts the user prompt while the shell
    // front is still the wake. Auto-resend has no prompt id on the wire, so
    // arming it here would cancel the promoted user turn after the grace.
    let desynced_from_wake = agent.running_wake_turn.as_ref().is_some_and(|wake| {
        agent
            .session
            .current_prompt_id
            .as_ref()
            .is_some_and(|pid| pid != &wake.prompt_id)
    });
    // Resend recovery is for user gestures; programmatic cancels (no
    // trigger, e.g. login flows) own their retries. A rewind leaves no
    // cancelling state to key recovery on, so it never arms one either.
    if let Some(trigger) = trigger
        && !rewind_if_no_output
        && !desynced_from_wake
    {
        // Keep `confirmed` across a manual retry: `[stop]` stays clickable
        // while cancelling, and resetting the flag would re-arm auto-resend
        // against a queued prompt the shell may already have promoted.
        let existing = agent
            .pending_cancel_resend
            .as_ref()
            .filter(|p| p.prompt_id == target_prompt_id);
        let confirmed = existing.is_some_and(|p| p.confirmed);
        let attempts = existing.map(|p| p.attempts.max(1)).unwrap_or(1);
        agent.pending_cancel_resend = Some(crate::app::agent_view::PendingCancelResend {
            prompt_id: target_prompt_id,
            sent_at: Instant::now(),
            attempts,
            confirmed,
            trigger,
        });
    }
    Effect::CancelTurn {
        session_id,
        trigger,
        rewind_prompt_id,
    }
}

/// Grace before a still-cancelling pane re-sends its (fire-and-forget,
/// loss-prone) `session/cancel`.
pub(crate) const CANCEL_RESEND_GRACE: std::time::Duration = std::time::Duration::from_secs(3);

/// Resend cap; past it a fresh gesture owns recovery.
pub(crate) const CANCEL_RESEND_MAX_ATTEMPTS: u8 = 3;

/// Re-send the (shell-idempotent) cancel for panes still in a cancelling
/// state past [`CANCEL_RESEND_GRACE`]. Returns `None` when nothing fired.
pub(crate) fn reconcile_overdue_cancels(app: &mut AppView) -> Option<Vec<Effect>> {
    let mut effects = Vec::new();
    for agent in app.agents.values_mut() {
        if let Some(effect) = overdue_cancel_for_agent(agent) {
            effects.push(effect);
        }
    }
    (!effects.is_empty()).then_some(effects)
}

fn overdue_cancel_for_agent(agent: &mut AgentView) -> Option<Effect> {
    // A cancelled wake turn keeps the pane Idle (never adopted), so its
    // cancelling phase lives on `running_wake_turn` instead of the state.
    if !agent.any_cancel_pending() {
        // The turn resolved (or a new one adopted); the marker is stale.
        agent.pending_cancel_resend = None;
        return None;
    }
    let session_id = agent.session.session_id.clone()?;
    // A received `prompt_complete` broadcast proves the cancel landed;
    // the turn-end reconcile owns the exit from here. Resending would
    // race it and could cancel a queued prompt the shell has already
    // promoted.
    if agent.pending_turn_end_reconcile.is_some() {
        if let Some(pending) = agent.pending_cancel_resend.as_mut() {
            pending.confirmed = true;
        }
        return None;
    }
    let pending = agent.pending_cancel_resend.as_mut()?;
    if pending.confirmed
        || pending.attempts >= CANCEL_RESEND_MAX_ATTEMPTS
        || pending.sent_at.elapsed() < CANCEL_RESEND_GRACE
    {
        return None;
    }
    pending.attempts += 1;
    pending.sent_at = Instant::now();
    crate::unified_log::warn(
        "cancel.resend",
        Some(&session_id.0),
        Some(serde_json::json!({
            "attempts": pending.attempts,
            "target_prompt_id": pending.prompt_id,
        })),
    );
    Some(Effect::CancelTurn {
        session_id,
        trigger: Some(pending.trigger),
        rewind_prompt_id: None,
    })
}

/// Grace window between a driver-side `legacy ext RPC`
/// broadcast and that turn's `session/prompt` RPC response, after which
/// [`reconcile_overdue_turn_ends`] finishes the turn from the broadcast. The
/// healthy-path gap is milliseconds (the shell emits the broadcast just
/// before writing the RPC response), so an expiry means the response is
/// genuinely lost, not merely slow.
pub(crate) const TURN_END_RECONCILE_GRACE: std::time::Duration = std::time::Duration::from_secs(2);

/// Finish turns whose end was announced by `legacy ext RPC`
/// but whose `session/prompt` RPC response never arrived.
///
/// The RPC response is the driver's only turn-state exit, and it can be lost
/// in leader response routing / reconnect races (the loss left the TUI
/// latched in `TurnCancelling` — Esc dead, prompts piling into a queue
/// that never drains — until a restart). The
/// broadcast is armed in `handle_prompt_complete` and disarmed by a matching
/// `TaskResult::PromptResponse`; whatever is still armed past
/// [`TURN_END_RECONCILE_GRACE`] is reconciled here with the essential subset
/// of the PromptResponse teardown (state, marker, queue drain).
///
/// Returns `None` when nothing fired; `Some(effects)` (possibly empty) when
/// at least one agent was reconciled, so the caller forces a redraw.
pub(crate) fn reconcile_overdue_turn_ends(app: &mut AppView) -> Option<Vec<Effect>> {
    let overdue: Vec<AgentId> = app
        .agents
        .iter()
        .filter(|(_, a)| {
            a.pending_turn_end_reconcile
                .as_ref()
                .is_some_and(|p| p.received_at.elapsed() >= TURN_END_RECONCILE_GRACE)
        })
        .map(|(id, _)| *id)
        .collect();
    if overdue.is_empty() {
        return None;
    }

    let mut fired = false;
    let mut effects = Vec::new();
    for id in overdue {
        let Some(agent) = app.agents.get_mut(&id) else {
            continue;
        };
        let Some(pending) = agent.pending_turn_end_reconcile.take() else {
            continue;
        };

        let still_ours =
            agent.session.current_prompt_id.as_deref() == Some(pending.prompt_id.as_str());
        let busy = agent.session.state.is_turn_running() || agent.session.state.is_cancelling();
        if !still_ours || !busy {
            // The turn already resolved through the normal path (or a new
            // turn was adopted); the marker is stale.
            continue;
        }

        fired = true;
        let was_cancelling = agent.session.state.is_cancelling()
            || pending.stop_reason.as_deref() == Some("cancelled");
        let elapsed = agent.turn_elapsed().unwrap_or_default();
        crate::unified_log::warn(
            "turn.end_reconciled_from_broadcast",
            agent.session.session_id.as_ref().map(|s| s.0.as_ref()),
            Some(serde_json::json!({
                "prompt_id": pending.prompt_id,
                "stop_reason": pending.stop_reason,
                "was_cancelling": was_cancelling,
                "grace_ms": TURN_END_RECONCILE_GRACE.as_millis() as u64,
            })),
        );

        agent.session.finish_turn(&mut agent.scrollback);
        let event = if was_cancelling {
            Some(SessionEvent::TurnCancelled { elapsed })
        } else {
            match pending.stop_reason.as_deref() {
                // Rate limits drive a dedicated driver UX via the retry
                // notifications (already delivered); no extra marker.
                Some("rate_limit") => None,
                Some("error") => crate::app::turn_completion::turn_failed_event(
                    &agent.scrollback,
                    pending.agent_result.as_deref(),
                    elapsed,
                ),
                _ => Some(SessionEvent::TurnCompleted {
                    elapsed: Some(elapsed),
                }),
            }
        };
        crate::app::turn_completion::push_turn_terminal_marker(agent, event);

        agent.mark_turn_finished(TurnEnd::Completed);
        agent.activity_started_at = None;
        agent.last_activity = None;
        drain_permission_queue(agent);
        if agent.bash_turn {
            agent.bash_turn = false;
            agent.scrollback.goto_bottom();
        }
        agent.cron_task_id = None;

        let drain = maybe_drain_queue(agent);
        effects.extend(drain.effects);
    }
    fired.then_some(effects)
}

// TaskResult handlers.

