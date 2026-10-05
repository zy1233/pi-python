//! Finalizing a turn from a terminal turn signal.
//!
//! The pager learns a turn reached its terminal outcome from two rails: the
//! fire-and-forget `legacy ext RPC` broadcast (the one-release
//! compat path for not-yet-upgraded leaders) and the durable, persisted+replayed
//! `PiSessionUpdate::TurnCompleted`. Both converge on
//! [`finalize_turn_from_terminal`] so the turn-finalize behavior lives in one
//! place — and so a viewer that re-attaches mid-turn can finalize the turn from
//! replay instead of staying stuck on "Waiting…".

use crate::scrollback::blocks::SessionEvent;

use super::agent_view::AgentView;
use super::cancel_latency::TurnEnd;

/// `_meta` key of a cancelled terminal's trigger (`"send_now"`, `"ctrl_c"`, …).
pub(crate) const CANCEL_TRIGGER_KEY: &str = "cancelTrigger";

/// Push a turn-terminal marker ("Turn completed/cancelled/failed").
///
/// All three marker rails route through here: the driver's `PromptResponse`,
/// the lost-RPC reconcile, and the viewer finalize. (Wake turns route through
/// `finish_wake_turn` in acp_handler, which maps their stop reason and calls
/// here only when a marker is due.) `event == None` (bash turns, rate-limit /
/// re-auth UX that replaces the marker) pushes nothing.
pub(super) fn push_turn_terminal_marker(agent: &mut AgentView, event: Option<SessionEvent>) {
    if let Some(event) = event {
        agent.push_end_marker_block(event);
    }
}

/// A turn-terminal signal's wire fields (`turn_completed` params + `_meta`,
/// or the legacy `prompt_complete` payload); parks 1:1 onto
/// [`PendingTurnEnd`](super::agent_view::PendingTurnEnd) when the driver arms
/// the lost-RPC reconcile.
// Test-only Default: production call sites must name every wire field.
#[derive(Clone, Copy)]
#[cfg_attr(test, derive(Default))]
pub(super) struct TerminalSignal<'a> {
    /// The ended turn's `promptId`, when the broadcast carried one.
    pub prompt_id: Option<&'a str>,
    /// `stopReason` (`"cancelled"`, `"end_turn"`, …).
    pub stop_reason: Option<&'a str>,
    /// `agentResult` detail (error text, when present).
    pub agent_result: Option<&'a str>,
}

/// What applying a terminal turn signal did to one agent.
pub(super) enum TerminalApply {
    /// No change: a driver turn the signal does not provably match, or a
    /// duplicate/stale terminal for an already-finished viewer turn.
    Ignored,
    /// Driver: the lost-RPC reconcile was armed. The turn is NOT finished — the
    /// `PromptResponse` RPC owns the driver's lifecycle. Reported as a state
    /// change so the reconcile sweep's animation tick stays scheduled.
    ReconcileArmed,
    /// Viewer: the turn was finished and (for non-rate-limit reasons) a terminal
    /// marker pushed.
    ViewerFinalized,
}

/// Arm lost-`PromptResponse` reconcile for the driver turn we own.
///
/// - **Exact** `prompt_id` match → arm (canonical).
/// - **Missing** wire `promptId` (`None` or empty) → arm on `current_prompt_id`
///   only when the turn is not mid-tool/thinking/compact/retry (legacy /
///   broken `TurnCompleted` payloads).
/// - **Non-empty mismatch** → ignore (stale/peer terminal must not kill a
///   newer live turn after grace).
///
/// Never clobber an existing arm for a different pid; keep earliest
/// `received_at` when re-arming the same pid.
fn arm_driver_turn_end_reconcile(
    agent: &mut AgentView,
    session_id: &str,
    signal: TerminalSignal<'_>,
) -> bool {
    let TerminalSignal {
        prompt_id,
        stop_reason,
        agent_result,
    } = signal;
    if agent.session.loading_replay {
        return false;
    }
    if !(agent.session.state.is_turn_running() || agent.session.state.is_cancelling()) {
        return false;
    }
    let Some(current) = agent.session.current_prompt_id.clone() else {
        return false;
    };

    let (arm_pid, arm_via) = match prompt_id {
        Some(pid) if pid == current.as_str() => (current, "exact"),
        Some("") => {
            if driver_mid_active_work(agent) {
                return false;
            }
            (current, "empty_wire_pid")
        }
        Some(_) => return false,
        None => {
            if driver_mid_active_work(agent) {
                return false;
            }
            (current, "missing_wire_pid")
        }
    };

    if let Some(pending) = agent.pending_turn_end_reconcile.as_ref() {
        if pending.prompt_id != arm_pid {
            return false;
        }
        // Same pid already armed — keep earliest received_at; refresh outcome.
        let received_at = pending.received_at;
        agent.pending_turn_end_reconcile = Some(super::agent_view::PendingTurnEnd {
            prompt_id: arm_pid.clone(),
            stop_reason: stop_reason.map(str::to_string),
            agent_result: agent_result.map(str::to_string),
            received_at,
        });
        crate::unified_log::info(
            "turn.end_reconcile.armed",
            Some(session_id),
            Some(serde_json::json!({
                "prompt_id": arm_pid,
                "wire_prompt_id": prompt_id,
                "arm_via": arm_via,
                "stop_reason": stop_reason,
                "refreshed": true,
            })),
        );
        return true;
    }

    crate::unified_log::info(
        "turn.end_reconcile.armed",
        Some(session_id),
        Some(serde_json::json!({
            "prompt_id": arm_pid,
            "wire_prompt_id": prompt_id,
            "arm_via": arm_via,
            "stop_reason": stop_reason,
        })),
    );
    agent.pending_turn_end_reconcile = Some(super::agent_view::PendingTurnEnd {
        prompt_id: arm_pid,
        stop_reason: stop_reason.map(str::to_string),
        agent_result: agent_result.map(str::to_string),
        received_at: std::time::Instant::now(),
    });
    true
}

/// Formatted `TurnFailed` marker for an errored turn, or `None` when a
/// dedicated banner (re-auth, overflow, disk-full, request-failed) already
/// covers the failure.
pub(in crate::app) fn turn_failed_event(
    scrollback: &crate::scrollback::state::ScrollbackState,
    agent_result: Option<&str>,
    elapsed: std::time::Duration,
) -> Option<SessionEvent> {
    if super::dispatch::scrollback_has_recent_error_banner(scrollback) {
        return None;
    }
    let raw = agent_result.unwrap_or("unknown error");
    Some(SessionEvent::TurnFailed {
        error: crate::app::error_display::format_request_failure(None, None, raw).message(),
        elapsed: Some(elapsed),
    })
}

fn driver_mid_active_work(agent: &AgentView) -> bool {
    use crate::acp::tracker::TurnActivity;
    // A write whose delta stream died is positive evidence of a dead stream;
    // whatever shows through it (an open thinking block, an earlier tool)
    // must not block lost-response recovery.
    if agent.session.tracker.has_stale_tool_call_write() {
        return false;
    }
    match agent.session.tracker.activity() {
        Some(
            TurnActivity::ToolRunning { .. }
            | TurnActivity::Thinking
            | TurnActivity::AutoCompacting
            | TurnActivity::Retrying { .. }
            | TurnActivity::WritingToolCall(_),
        ) => true,
        Some(TurnActivity::Responding | TurnActivity::Waiting(_)) | None => false,
    }
}

/// Finalize a turn from a terminal signal, shared by the `prompt_complete`
/// broadcast and the durable `TurnCompleted` update so both behave identically.
///
/// DRIVER (`!attached_as_viewer`): the `PromptResponse` RPC owns the turn
/// lifecycle (it carries context this signal lacks: error classes, rewind
/// bookkeeping, adoption hand-off), so do NOT finish the turn here — that would
/// race/double-finish on every normal turn end (the signal is emitted BEFORE the
/// RPC response is written). But the RPC response can be LOST in transit (leader
/// response routing / reconnect races), and
/// it is the ONLY exit from `TurnRunning`/`TurnCancelling`. So when the signal
/// refers to the turn this client is driving (exact pid, or missing/empty pid
/// while not mid-tool), arm a deferred reconcile: if the RPC lands within the
/// grace window it disarms this (see `TaskResult::PromptResponse`); otherwise
/// the event loop finishes the turn from it (`reconcile_overdue_turn_ends`).
///
/// VIEWER (`attached_as_viewer`): a viewer adopts the driver's turn and never
/// receives its `PromptResponse`, so this is its only non-interactive exit from
/// `TurnRunning`. Finish the turn and push the "Turn completed/cancelled/failed"
/// marker mapped from [`TerminalSignal::stop_reason`]. Idempotent: a
/// duplicate/stale terminal for an already-finished turn pushes nothing and
/// returns [`TerminalApply::Ignored`].
pub(super) fn finalize_turn_from_terminal(
    agent: &mut AgentView,
    session_id: &str,
    signal: TerminalSignal<'_>,
) -> TerminalApply {
    let TerminalSignal {
        stop_reason,
        agent_result,
        ..
    } = signal;
    if !agent.attached_as_viewer {
        if arm_driver_turn_end_reconcile(agent, session_id, signal) {
            return TerminalApply::ReconcileArmed;
        }
        return TerminalApply::Ignored;
    }

    // Viewer: the driver's turn ended — exit TurnRunning. Only act when a turn
    // is actually in progress so a stray/duplicate signal is harmless (a
    // duplicate finds the turn already finished here and pushes no marker).
    if !agent.session.state.is_busy() && agent.session.current_prompt_id.is_none() {
        return TerminalApply::Ignored;
    }

    // Capture elapsed BEFORE `mark_turn_finished()` clears `turn_started_at`. The
    // anchor was back-dated from the authoritative `turnStartMs` on adoption, so
    // this reads the same wall-clock duration the driver shows.
    let elapsed = agent.turn_elapsed().unwrap_or_default();
    agent.session.finish_turn(&mut agent.scrollback);

    // A viewer never receives the driver's `PromptResponse` RPC — the source of
    // the driver's "Worked for X" marker. Surface the equivalent here.
    // The signal only carries a coarse `stop_reason` (no doom-loop category, no
    // driver-local rate-limit / re-auth context), so map it to the closest event:
    let event = match stop_reason {
        Some("cancelled") => Some(SessionEvent::TurnCancelled { elapsed }),
        // Rate limits drive a dedicated UX on the driver and are not actionable
        // from a viewer — don't surface a stray "Turn failed" line.
        Some("rate_limit") => None,
        Some("error") => turn_failed_event(&agent.scrollback, agent_result, elapsed),
        // end_turn / max_tokens / max_turn_requests / refusal / unknown → done.
        _ => Some(SessionEvent::TurnCompleted {
            elapsed: Some(elapsed),
        }),
    };
    push_turn_terminal_marker(agent, event);

    agent.mark_turn_finished(TurnEnd::Completed);

    TerminalApply::ViewerFinalized
}

/// Map a [`finalize_turn_from_terminal`] outcome to the redraw/tick bool that
/// BOTH terminal rails (`prompt_complete` and the live `TurnCompleted`) RETURN
/// DIRECTLY, applying the viewer-finalize side effect. Keeping this mapping in
/// one place is load-bearing: the live `TurnCompleted` arm must return this
/// instead of routing through `changed && is_active` (see below).
///
/// - `Ignored` -> `false`.
/// - `ReconcileArmed` -> `true` UNCONDITIONALLY (not gated on visibility). The
///   lost-RPC reconcile sweep rides the animation tick, and the event loop only
///   re-arms the tick when a batch reports a change. A background-tab driver
///   (`is_active == false`) that armed the reconcile must still report the change
///   or `reconcile_overdue_turn_ends` never fires and the turn strands on
///   "Waiting…" — the exact bug this rail fixes.
/// - `ViewerFinalized` -> `true` only when `is_active`.
pub(super) fn apply_terminal_outcome(outcome: TerminalApply, is_active: bool) -> bool {
    match outcome {
        TerminalApply::Ignored => false,
        TerminalApply::ReconcileArmed => true,
        TerminalApply::ViewerFinalized => is_active,
    }
}

#[cfg(test)]
#[path = "turn_completion/tests.rs"]
mod tests;
