//! ACP message handling.
//!
//! Routes incoming [`AcpClientMessage`] notifications to the appropriate
//! agent's tracker, queues permission requests for interactive handling,
//! and pi session extension notifications (`legacy ext RPC` and
//! replay-path `legacy ext RPC`).

use agent_client_protocol as acp;
use pi_acp_lib::AcpClientMessage;

use pi_shell::extensions::notification::{
    SessionNotification, SessionUpdate as PiSessionUpdate, is_reauthable_failure,
};
use pi_shell::tools::todo::todo_item_from_plan_entry;
use pi_workspace::permission::bash_command_splitting::BashCommandHighlights;

use crate::acp::meta::NotificationMeta;
use crate::acp::tracker::TurnActivity;
use crate::app::agent::{AgentId, AgentSession, AgentState, };
use crate::notifications::{NotificationEvent, NotificationEventKind};
use crate::scrollback::block::RenderBlock;
use crate::scrollback::blocks::SessionEvent;
use crate::views::permission_view::{
    McpScope, McpScopeState, PermissionFocus, PermissionViewState, 
};

use super::agent_view::{AgentPane, AgentView};
use super::app_view::{ActiveView, AppView};

mod permissions;
mod prompt_origin;
mod routing;
mod session_notification;

#[cfg(test)]
use permissions::{MCP_ARGS_MAX_LINE_CHARS, MCP_ARGS_MAX_LINES, mcp_args_lines};
use permissions::{
    apply_recap_block, handle_permission_request, should_drop_duplicate_auto_recap,
    should_drop_late_auto_recap,
};

// Hub + child modules (via `use super::*`) need sibling symbols in this scope.
use routing::{find_session_match, is_matched_agent_active};

use prompt_origin::{finish_wake_turn, viewer_turn_anchor};
pub(crate) use prompt_origin::{
    is_scheduler_fired_prompt, is_server_initiated_prompt, is_wake_prompt,
    should_adopt_running_prompt,
};


#[cfg(test)]
pub(crate) use session_notification::apply_session_event_for_test;
pub(crate) use session_notification::drop_unexpected_replay;
use session_notification::{advance_reconnect_cursor, confirm_context_used, detect_plan_mode_change, handle_session_notification};

#[cfg(test)]
#[allow(unused_imports)]
use prompt_origin::*;
#[cfg(test)]
#[allow(unused_imports)]
use routing::*;
#[cfg(test)]
#[allow(unused_imports)]
use session_notification::*;

/// Handle an ACP notification (session update, permission request, etc.).
///
/// Returns `true` if the active view was visually affected (needs redraw).
/// Notifications are routed to the agent whose `session_id` matches, even when
/// that agent is not the currently active view -- streaming chunks for a
/// background agent must still land in its own scrollback so the user sees
/// the full turn after switching back.
pub(crate) fn handle(msg: AcpClientMessage, app: &mut AppView) -> bool {
    match msg {
        AcpClientMessage::SessionNotification(notif) => {
            let mut meta = NotificationMeta::from_json(notif.request.meta.as_ref());

            let affected = match find_session_match(app, &notif.request.session_id) {
                Some(id) => {
                    let is_active = is_matched_agent_active(app, id);
                    let agent = app
                        .agents
                        .get_mut(&id)
                        .expect("find_session_match returned an existing AgentId");

                    // Live-only dedup: a per-session `eventId` highwater drops
                    // re-delivered live duplicates (leader fan-out, reconnect
                    // re-emit). Replay is EXEMPT — the per-process counter resets
                    // each resume, so persisted history concatenates non-monotonic
                    // 0..N runs; gating it by the highwater would latch a pre-reset
                    // peak and truncate the restored transcript. Replayed
                    // history is authoritative + ordered, so it always renders and
                    // never seeds the highwater.
                    //
                    // Premise: ACP-stream live delivery is in id order —
                    // actor ACP lines (chunks and the plan-mode
                    // `CurrentModeUpdate`s) are stamped at `event_tx` enqueue
                    // time and drained FIFO. The pi stream is direct-emitted
                    // and keeps a SEPARATE highwater (see the pi dedup in
                    // `handle_session_notification`). Residual class: ACP
                    // lines that skip `event_tx` — the bridge's bash stdout
                    // (no `event_tx` surface) and the turn-start user echo —
                    // can mint an id after, but deliver before, queued
                    // lower-id lines; with chunk buffering off on pager
                    // sessions that window is one actor drain hop (accepted).
                    let dedup_drop = !meta.is_replay
                        && meta.event_seq.is_some_and(|seq| {
                            agent.last_applied_event_seq.is_some_and(|last| seq <= last)
                        });
                    if let Some(seq) = meta.event_seq
                        && !meta.is_replay
                        && !dedup_drop
                    {
                        agent.last_applied_event_seq = Some(seq);
                    }

                    if drop_unexpected_replay(
                        agent,
                        &meta,
                        notif.request.session_id.0.as_ref(),
                        "session/update",
                    ) {
                        notif.response_tx.send(Ok(())).ok();
                        return false;
                    }

                    // Re-derive the per-turn viewer flag from prompt-id
                    // ownership BEFORE the adopt/drop gate below.
                    //
                    // `attached_as_viewer` starts true on a `session/load`
                    // attach and is cleared when this client sends its own
                    // prompt — but a client that has driven a turn can later
                    // VIEW a turn ANOTHER client drives (a `/loop` cron, or a
                    // plain prompt typed in a different pane). Left sticky-false,
                    // the gate dropped those deltas and the pane rendered
                    // nothing. A non-synthetic prompt id this client never
                    // originated is another client's turn → view it; one it
                    // originated is its own → drive it (strict gate).
                    //
                    // Server-initiated / auto-wake turns (synthetic prompt ids)
                    // are excluded: they have no client finish path, so they
                    // must not flip the role (see the adopt gate below).
                    //
                    // Only re-derive on a real, non-replay, non-duplicate delta
                    // that does NOT match the active turn.
                    if !dedup_drop
                        && !meta.is_replay
                        && let Some(notif_pid) = meta.prompt_id.as_deref()
                        && agent.session.current_prompt_id.as_deref() != Some(notif_pid)
                        && !is_server_initiated_prompt(notif_pid)
                    {
                        agent.attached_as_viewer = !agent.is_self_originated_prompt(notif_pid);
                    }

                    // Store context usage and turn timing on agent state.
                    //
                    // Gate on `!dedup_drop`: a deduped delta is an
                    // already-applied or stale out-of-order event (its
                    // `eventId` is `<=` the highwater). A fresher event has
                    // already advanced the highwater and set newer `totalTokens`
                    // / `turnStartMs`, so applying the stale values here would
                    // REGRESS them. This is the replay/live-overlap case (leader
                    // fan-out, reconnect, re-emit after the gate): a historical
                    // replay delta carrying a LOWER `totalTokens` arriving after
                    // a live one would otherwise drop the context bar below the
                    // real usage. The dedup already drops the render; the
                    // token/timing state must respect it too.
                    if !dedup_drop {
                        if let Some(tokens) = meta.total_tokens {
                            confirm_context_used(agent, tokens);
                        }
                        if let Some(ts) = meta.turn_start_ms {
                            agent.turn_start_ms = Some(ts);
                            agent.turn_start_ms_prompt = meta.prompt_id.clone();
                        }
                    }

                    let mut plan_mode_modal_refresh_needed = false;

                    // Extract Plan updates before passing to tracker (tracker skips them).
                    let mutated = if dedup_drop {
                        tracing::debug!(
                            session_id = notif.request.session_id.0.as_ref(),
                            event_seq = meta.event_seq,
                            last_applied = agent.last_applied_event_seq,
                            is_replay = meta.is_replay,
                            "load-race: session/update DROPPED by dedup highwater (event_seq <= last_applied)"
                        );
                        // Already-applied event delivered again — drop it (do not
                        // re-render). Not a mutation, so no redraw.
                        false
                    } else if let acp::SessionUpdate::Plan(plan) = notif.request.update {
                        let items: Vec<_> = plan
                            .entries
                            .into_iter()
                            .map(todo_item_from_plan_entry)
                            .collect();
                        agent.todo.update_todos(items);
                        agent.mark_reload_todo_update();
                        advance_reconnect_cursor(agent, &mut meta);
                        !meta.is_replay && !agent.session.loading_replay
                    } else if !meta.is_replay
                        && let Some(notif_pid) = meta.prompt_id.as_ref()
                        && agent.session.current_prompt_id.as_ref() != Some(notif_pid)
                        && !((agent.session.current_prompt_id.is_none()
                            || agent
                                .session
                                .current_prompt_id
                                .as_deref()
                                .is_some_and(is_server_initiated_prompt))
                            && is_server_initiated_prompt(notif_pid))
                        && !agent.attached_as_viewer
                    {
                        tracing::debug!(
                            session_id = notif.request.session_id.0.as_ref(),
                            notif_prompt_id = meta.prompt_id.as_deref(),
                            current_prompt_id = agent.session.current_prompt_id.as_deref(),
                            attached_as_viewer = agent.attached_as_viewer,
                            loading_replay = agent.session.loading_replay,
                            "load-race: session/update DROPPED by promptId-mismatch gate on a non-viewer (stale/rewound-turn guard)"
                        );
                        // The notification's `promptId` does not match the
                        // currently-active prompt. Drop — belongs to a rewound
                        // turn or stale in-flight work.
                        //
                        // EXCEPTION (multi-client / leader mode): a viewer
                        // (`attached_as_viewer`) is watching a session another
                        // client is driving. It has no turn of its own, so a
                        // mismatching `promptId` is NOT stale — it is the
                        // driver's live (or next) turn. Fall through to the
                        // adoption branch below so the delta renders instead of
                        // freezing the viewer at its load snapshot. This is
                        // scoped to viewers so a locally-created driver's
                        // post-rewind stale-chunk drop is preserved (a driver
                        // always has `attached_as_viewer == false`).
                        !agent.session.loading_replay
                    } else {
                        // Adopt a mismatching `promptId` so subsequent chunks for
                        // the same turn match and render — but ONLY for a viewer
                        // watching another client's turn.
                        //
                        // Server-initiated / auto-wake turns (synthetic prompt
                        // ids) are deliberately NOT adopted here: they have no
                        // client finish path (no PromptResponse, no
                        // prompt_complete), so occupying `current_prompt_id`
                        // would strand the turn-status and make later turns'
                        // PromptResponses get discarded. Their content still
                        // renders — the drop gate above passes synthetic deltas
                        // through when `current_prompt_id` is None/synthetic.
                        if let Some(notif_pid) = meta.prompt_id.as_ref()
                            && agent.session.current_prompt_id.as_ref() != Some(notif_pid)
                            && agent.attached_as_viewer
                        {
                            agent.session.current_prompt_id = Some(notif_pid.clone());
                            // A viewer adopting another client's new turn: drop
                            // the prior turn's chips but KEEP the seen ring so a
                            // stale prior-turn replay stays rejected. The adopted
                            // turn's own follow_ups (if already applied then
                            // cleared here) still re-render: `apply_follow_ups`
                            // matches their stamped `promptId` to the now-current
                            // `current_prompt_id` set just above.
                            agent.clear_follow_ups();
                            // The adopted turn's follow_ups may have arrived on
                            // the ext channel BEFORE this session/update (separate
                            // channels) and been buffered — render them now that
                            // the turn is current.
                            agent.flush_pending_follow_ups(notif_pid);
                        }
                        // A live delta for a wake prompt id: record the
                        // streaming wake turn so the stop affordance can offer
                        // a cancel for it. Lifecycle on
                        // `note_streaming_wake_turn`.
                        if !meta.is_replay
                            && let Some(notif_pid) = meta.prompt_id.as_deref()
                            && is_wake_prompt(notif_pid)
                        {
                            agent.note_streaming_wake_turn(notif_pid);
                        }

                        // Detect plan mode transitions from tool call completions.
                        plan_mode_modal_refresh_needed |=
                            detect_plan_mode_change(&notif.request.update, agent);

                        let had_activity_before = agent.session.tracker.activity().is_some();
                        let update = notif.request.update;
                        let user_echo = matches!(update, acp::SessionUpdate::UserMessageChunk(_));
                        agent
                            .session
                            .handle_update(update, &meta, &mut agent.scrollback);
                        // Skip user echo: shell broadcasts it before history commit.
                        if !user_echo
                            && !meta.is_replay
                            && !agent.session.loading_replay
                            && meta.prompt_id.as_deref()
                                == agent.session.current_prompt_id.as_deref()
                        {
                            agent.front_message_committed = true;
                        }
                        // Once the server has emitted any activity (chunk, tool,
                        // retry, etc.), the in-flight prompt can no longer be
                        // "rewound" by Ctrl+C. Clear the stash on the transition.
                        if !had_activity_before && agent.session.tracker.activity().is_some() {
                            note_first_turn_activity(agent);
                        }

                        // Drain pending ACP commands immediately after handle_update.
                        // This is the SINGLE generation bump site — ensures exactly
                        // one bump per AvailableCommandsUpdate received.
                        if let Some(commands) = agent.session.tracker.take_pending_acp_commands() {
                            agent.session.available_commands = commands;
                            agent.session.available_commands_generation += 1;
                        }
                        // Tools list arrives in the same update's `meta` payload.
                        // Stash it on the session so the per-frame sync in
                        // `app_view.rs` can push it through to the slash registry
                        // alongside the command catalog.
                        if let Some(tools) = agent.session.tracker.take_pending_acp_tools() {
                            agent.session.available_tools = Some(tools.into_iter().collect());
                        }
                        for entry_id in agent.session.tracker.take_pending_edit_hl() {
                            agent.submit_edit_highlight(entry_id);
                        }

                        // Viewer chrome (leader / multi-client). A viewer has no
                        // turn of its own and never calls start_turn(), so it
                        // would stay `Idle` — hiding the "⠿ Responding…" status
                        // line, the elapsed/token counter, and the Ctrl+c:cancel
                        // footer hint (all gated on
                        // `AgentState::TurnRunning`). Enter TurnRunning whenever a
                        // turn is in flight (a prompt id is adopted) and we are
                        // not already running.
                        //
                        // This is placed AFTER `handle_update` (not in the adopt
                        // block above) on purpose: the adopt block only fires on
                        // a prompt-id MISMATCH and is suppressed during the
                        // `loading_replay` window. A client that reattaches
                        // MID-turn adopts the running id during its replay window
                        // (TurnRunning suppressed there) and then receives
                        // post-load deltas that MATCH `current_prompt_id` — which
                        // skip the adopt block — so it would never flip to
                        // TurnRunning. Checking here on every applied live viewer
                        // delta closes that gap, independent of whether the load
                        // response conveyed `runningPromptId`, of delta ordering,
                        // and of whether a given delta carries a prompt id.
                        //
                        // Do NOT call start_turn(): it resets the tracker and
                        // arms `expect_user_echo()`, which would corrupt the
                        // driver's live stream. We only flip state + stamp the
                        // elapsed timer (monotonic: only on the Idle→TurnRunning
                        // transition).
                        //
                        // Enter TurnRunning only for an adoptable prompt — see
                        // `should_adopt_running_prompt` (true iff the turn has a
                        // terminal `prompt_complete` exit). This is what lets a
                        // viewer (and the dashboard's locally-tracked row, which
                        // reads live turn state) show a running `/loop` session as
                        // Working without stranding "Responding…" forever on an
                        // exit-less auto-wake / server-initiated turn.
                        if agent.attached_as_viewer
                            && !meta.is_replay
                            && !agent.session.loading_replay
                            && agent
                                .session
                                .current_prompt_id
                                .as_deref()
                                .is_some_and(should_adopt_running_prompt)
                            && !matches!(agent.session.state, AgentState::TurnRunning)
                        {
                            agent.session.state = AgentState::TurnRunning;
                            // Back-date from the authoritative `turnStartMs` so a
                            // viewer's elapsed matches the driver's instead of
                            // starting at the time-to-first-delta.
                            agent.turn_started_at = Some(viewer_turn_anchor(agent.turn_start_ms));
                        }

                        advance_reconnect_cursor(agent, &mut meta);

                        !meta.is_replay && !agent.session.loading_replay
                    };

                    if plan_mode_modal_refresh_needed {
                        crate::app::dispatch::refresh_open_settings_modals(app);
                    }
                    // Mutation always happens; redraw only when the matched
                    // agent is the visible one.
                    mutated && is_active
                }
                None => {
                    tracing::debug!(
                        session_id = notif.request.session_id.0.as_ref(),
                        agent_count = app.agents.len(),
                        "load-race: session/update DROPPED — no agent matches session_id (view not loaded yet?)"
                    );
                    false
                }
            };
            notif.response_tx.send(Ok(())).ok();
            affected
        }
        AcpClientMessage::RequestPermission(perm) => handle_permission_request(perm, app),
        AcpClientMessage::ExtNotification(ext) => {
            let affected = handle_ext_notification(&ext.request, app);
            ext.response_tx.send(Ok(())).ok();
            affected
        }
        AcpClientMessage::ExtMethod(ext) => handle_ext_method(ext),
        AcpClientMessage::WaitForTerminalExit(args) => {
            args.response_tx
                .send(Err(crate::acp::wait_for_exit_not_supported("pager")))
                .ok();
            false
        }
        _ => false,
    }
}

/// The turn's first activity (tracker flip None→Some): the server has started
/// producing, so drop the Ctrl+C rewind stash and log TTFA once per turn.
/// Shared by `handle_update` and the `ToolCallDeltaChunk` arm so the rails can't drift.
pub(super) fn note_first_turn_activity(agent: &mut AgentView) {
    agent.session.in_flight_prompt = None;

    // Log initial TTFA once per turn (activity flips None→Some each loop).
    if let Some(started) = agent.turn_started_at
        && agent.first_activity_logged_for != Some(started)
    {
        agent.first_activity_logged_for = Some(started);
        let activity_label = agent
            .session
            .tracker
            .activity()
            .map(|a| a.as_label())
            .unwrap_or("unknown");
        let ttfa_ms = started.elapsed().as_millis() as u64;
        let sid = agent.session.session_id.as_ref().map(|s| s.0.as_ref());
        crate::unified_log::info(
            "turn.first_activity",
            sid,
            Some(serde_json::json!({
                "ttfa_ms": ttfa_ms,
                "activity": activity_label,
            })),
        );
    }
}

/// Handle an extension notification.
///
/// Standard ACP agents (pi-agent-cli) never emit the legacy vendor namespace.
/// Ignore those notifications so leftover UI paths cannot depend on them.
fn handle_ext_notification(notif: &acp::ExtNotification, app: &mut AppView) -> bool {
    let method = notif.method.as_ref();
    if crate::acp::vendor::is_vendor_ext_method(method) {
        tracing::debug!(method, "ignoring vendor ext notification (standard ACP only)");
        return false;
    }
    if crate::acp::is_session_update_ext_method(method) {
        return handle_session_notification(notif, app);
    }
    false
}

/// Handle an ACP `ext_method` request (blocking request that expects a response).
///
/// The pager registers no reverse `ext_method` handlers: every request gets a
/// `method_not_found` error. Legacy vendor requests are acknowledged with an
/// empty result so a stray vendor agent is not left waiting on a reply.
fn handle_ext_method(ext: pi_acp_lib::AcpArgs<acp::ExtRequest>) -> bool {
    let method = ext.request.method.as_ref();
    if crate::acp::vendor::is_vendor_ext_method(method) {
        tracing::debug!(method, "ignoring vendor ext_method (standard ACP only)");
        let dummy = serde_json::from_value(serde_json::json!({}))
            .or_else(|_| serde_json::from_value(serde_json::Value::Null));
        match dummy {
            Ok(value) => {
                ext.response_tx.send(Ok(value)).ok();
            }
            Err(_) => {
                ext.response_tx
                    .send(Err(acp::Error::new(-32601, format!("Method not found: {method}"))))
                    .ok();
            }
        }
        return false;
    }
    tracing::warn!("Unknown ext_method: {method}");
    ext.response_tx
        .send(Err(acp::Error::new(
            -32601,
            format!("Method not found: {method}"),
        )))
        .ok();
    false
}

#[cfg(test)]
mod tests;
