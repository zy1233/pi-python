use super::*;
use pi_shell::sampling::error::format_rate_limited_user_message;
/// Stash a live stop-family batch under `stash_pid` for the turn marker
/// to fold. `merge_same_name` merges a same-name repeat instead of standalone.
pub(super) fn stash_live_stop_batch(
    agent: &mut AgentView,
    stash_pid: Option<String>,
    event_name: String,
    hook_entries: Vec<crate::scrollback::blocks::tool::HookRunEntry>,
    merge_same_name: bool,
) {
    if let Some(stale) = agent
        .pending_stop_hooks
        .take_if(|p| p.prompt_id != stash_pid)
    {
        for (name, runs) in stale.groups {
            agent.scrollback.push_lifecycle_hooks(name, runs);
        }
    }
    let pending = agent.pending_stop_hooks.get_or_insert_with(|| {
        super::super::agent_view::PendingStopHooks {
            prompt_id: stash_pid,
            groups: Vec::new(),
        }
    });
    match pending
        .groups
        .iter()
        .position(|(name, _)| *name == event_name)
    {
        Some(idx) if merge_same_name => {
            pending.groups[idx].1.extend(hook_entries);
        }
        Some(_) => {
            agent
                .scrollback
                .push_lifecycle_hooks(event_name, hook_entries);
        }
        None => {
            pending.groups.push((event_name, hook_entries));
        }
    }
}
pub(super) fn refresh_context_used(view: &mut AgentView, used: u64) {
    let total = view.session.models.get_context_window().unwrap_or(0);
    view.apply_context_used(used, total);
}
/// Refresh the bar and record `used` as the confirmed count for a pending
/// compaction message; call only from the `meta.totalTokens` path.
pub(super) fn confirm_context_used(view: &mut AgentView, used: u64) {
    refresh_context_used(view, used);
    view.session.note_context_used(used);
}
/// Replay gate shared by the ACP and pi session-update paths. Returns `true`
/// when the update must be dropped.
///
/// Replay is only expected while a `session/load` is in flight for this agent
/// (fresh-view load or reconnect reload window). Anything else is misrouted —
/// e.g. a leader falling through to broadcast another client's replay, or a
/// replay landing after its reload already timed out — and applying it would
/// append duplicated history below the live transcript. An expected replay is
/// recorded on the open reload window instead (see
/// [`AgentView::mark_reload_replay_seen`]). One `warn!` per incident; the rest
/// of the burst (one line per replayed event) logs at `debug!`.
///
/// After `SessionLoaded` the barrier may release on an Unrelated ACP timeout
/// while remaining `isReplay` still sits behind a foreign head. `late_replay_until`
/// keeps accepting that tail until the first this-session live update or the
/// grace expires.
pub(crate) fn drop_unexpected_replay(
    agent: &mut AgentView,
    meta: &NotificationMeta,
    session_id: &str,
    source: &'static str,
) -> bool {
    if !meta.is_replay {
        agent.late_replay_until = None;
        return false;
    }
    if agent.accepts_replayed_update() {
        agent.mark_reload_replay_seen();
        return false;
    }
    if agent.unexpected_replay_drops == 0 {
        tracing::warn!(
            session_id,
            source,
            event_id = meta.event_id.as_deref(),
            "Dropping unexpected replay update (no session load in flight); further drops logged at debug"
        );
    } else {
        tracing::debug!(
            session_id,
            source,
            event_id = meta.event_id.as_deref(),
            "Dropping unexpected replay update"
        );
    }
    agent.unexpected_replay_drops = agent.unexpected_replay_drops.saturating_add(1);
    true
}
/// Advance the reconnect cursor to an APPLIED update's eventId. Called from
/// every applied arm (Plan, bg-stdout, tracker) — dropped updates (dedup,
/// promptId gate, unexpected replay) deliberately don't move it. Forward-only
/// via [`AgentView::advance_last_seen_event_id`].
pub(super) fn advance_reconnect_cursor(agent: &mut AgentView, meta: &mut NotificationMeta) {
    if let Some(id) = meta.event_id.take() {
        agent.advance_last_seen_event_id(id, meta.event_seq);
    }
}
/// A string field off a turn-terminal notification envelope's `_meta`
/// (the cancel-qualifier keys; absent on older shells).
fn terminal_meta_str<'a>(meta: Option<&'a serde_json::Value>, key: &str) -> Option<&'a str> {
    meta.and_then(|v| v.get(key)).and_then(|v| v.as_str())
}
/// Handle `legacy ext RPC` and replay-path `legacy ext RPC`.
///
/// Routes by `session_id` so events for an inactive agent still mutate that
/// agent's state. The redraw decision is gated on whether the matched agent
/// is the currently visible one.
pub(super) fn handle_session_notification(notif: &acp::ExtNotification, app: &mut AppView) -> bool {
    handle_session_notification_with_origin(notif, app, LifecycleOrigin::Stream)
}
pub(super) fn handle_session_notification_with_origin(
    notif: &acp::ExtNotification,
    app: &mut AppView,
    origin: LifecycleOrigin,
) -> bool {
    let Ok(session_notif) = serde_json::from_str::<SessionNotification>(notif.params.get()) else {
        tracing::warn!("Failed to parse {}", notif.method.as_ref());
        return false;
    };
    let is_api_key_auth = app.is_api_key_auth;
    let matched = match find_session_match(app, &session_notif.session_id) {
        Some(m) => m,
        None => {
            tracing::debug!(
                session_id = session_notif.session_id.0.as_ref(),
                method = notif.method.as_ref(),
                "load-race: session_notification DROPPED — no agent matches session_id"
            );
            return false;
        }
    };
    let parent_id = matched.agent_id();
    let is_active = is_matched_agent_active(app, parent_id);
    let agent = app
        .agents
        .get_mut(&parent_id)
        .expect("find_session_match returned an existing AgentId");
    if matches!(matched, SessionMatch::Child(_)) {
        let child_sid: &str = session_notif.session_id.0.as_ref();
        let changed = handle_child_session_notification(
            session_notif.update,
            child_sid,
            agent,
            is_api_key_auth,
        );
        return changed && is_active;
    }
    let meta = NotificationMeta::from_json(session_notif.meta.as_ref().and_then(|v| v.as_object()));
    if drop_unexpected_replay(
        agent,
        &meta,
        session_notif.session_id.0.as_ref(),
        "session/update",
    ) {
        return false;
    }
    let is_workflow_update = matches!(
        &session_notif.update,
        PiSessionUpdate::WorkflowUpdated { .. }
    );
    let is_subagent_lifecycle =
        if let Some(lifecycle) = classify_subagent_lifecycle(&session_notif.update, origin) {
            match gate_subagent_lifecycle(
                &agent.subagent_sessions,
                &agent.scrollback,
                &mut agent.deferred_subagent_finishes,
                &lifecycle,
                meta.is_replay,
                session_notif.session_id.0.as_ref(),
                meta.event_id.as_deref(),
                &session_notif,
                std::time::Instant::now(),
            ) {
                LifecycleDelivery::Apply => true,
                LifecycleDelivery::DropDuplicate | LifecycleDelivery::AwaitSpawn => {
                    return false;
                }
            }
        } else {
            false
        };
    if !is_workflow_update
        && !is_subagent_lifecycle
        && !meta.is_replay
        && meta.event_seq.is_some_and(|seq| {
            agent
                .last_applied_pi_event_seq
                .is_some_and(|last| seq <= last)
        })
    {
        tracing::debug!(
            session_id = session_notif.session_id.0.as_ref(),
            event_seq = meta.event_seq,
            last_applied = agent.last_applied_pi_event_seq,
            "session update DROPPED by dedup highwater (event_seq <= last_applied)"
        );
        return false;
    }
    let mut status_snapshot_applied = false;
    let mut terminal_outcome: Option<super::super::turn_completion::TerminalApply> = None;
    let mut deferred_subagent_finish: Option<SessionNotification> = None;
    let root_session_id: &str = session_notif.session_id.0.as_ref();
    let changed = match session_notif.update {
        ref update @ (PiSessionUpdate::AutoCompactStarted { .. }
        | PiSessionUpdate::AutoCompactCompleted { .. }
        | PiSessionUpdate::AutoCompactFailed { .. }
        | PiSessionUpdate::AutoCompactCancelled { .. }
        | PiSessionUpdate::RetryState(_)
        | PiSessionUpdate::ImageDropped { .. }
        | PiSessionUpdate::MemoryFlushCompleted { .. }
        | PiSessionUpdate::MemoryDreamCompleted { .. }
        | PiSessionUpdate::MemorySessionSaved { .. }) => {
            let changed = apply_session_event(
                update,
                &mut agent.session,
                &mut agent.scrollback,
                is_api_key_auth,
            );
            if let PiSessionUpdate::AutoCompactCompleted { tokens_after, .. } = update {
                refresh_context_used(agent, *tokens_after);
                agent.todo.update_todos(Vec::new());
            }
            changed
        }
        PiSessionUpdate::ImageCompressed {
            ref images,
            ref message,
        } => apply_image_compressed(agent, images, message),
        PiSessionUpdate::ToolCallDeltaChunk {
            ref name,
            tool_index,
            ..
        } => {
            if meta.is_replay || agent.session.loading_replay || agent.running_wake_turn.is_some() {
                false
            } else {
                let had_activity_before = agent.session.tracker.activity().is_some();
                let changed = agent
                    .session
                    .tracker
                    .note_tool_call_arguments_delta(name.as_deref(), tool_index);
                if !had_activity_before && agent.session.tracker.activity().is_some() {
                    note_first_turn_activity(agent);
                }
                changed
            }
        }
        PiSessionUpdate::TurnCompleted {
            prompt_id,
            stop_reason,
            agent_result,
            ..
        } => {
            if agent.session.loading_replay {
                agent.replayed_terminal_prompts.insert(prompt_id);
                false
            } else if is_wake_prompt(&prompt_id) {
                if agent
                    .running_wake_turn
                    .as_ref()
                    .is_some_and(|wake| wake.prompt_id == prompt_id)
                {
                    agent.running_wake_turn = None;
                }
                agent.finished_wake_prompts.insert(prompt_id.to_string());
                if agent.session.state.is_busy() {
                    if agent.session.state.command_in_flight().is_some() {
                        agent.session.tracker.snapshot_output_epoch();
                    }
                    let errored = matches!(stop_reason.as_str(), "error" | "rate_limit");
                    if errored && agent.failed_wake_marker_for.as_deref() != Some(&*prompt_id) {
                        agent.failed_wake_marker_for = Some(prompt_id.clone());
                        if crate::app::dispatch::scrollback_has_recent_error_banner(
                            &agent.scrollback,
                        ) {
                            false
                        } else {
                            let error = if stop_reason == "rate_limit" {
                                agent_result
                                    .as_deref()
                                    .map(str::to_string)
                                    .unwrap_or_else(|| "rate limited".to_string())
                            } else {
                                crate::app::error_display::format_request_failure(
                                    None,
                                    None,
                                    agent_result.as_deref().unwrap_or("unknown error"),
                                )
                                .message()
                            };
                            agent.push_end_marker_block(
                                crate::scrollback::blocks::SessionEvent::TurnFailed {
                                    error,
                                    elapsed: None,
                                },
                                Vec::new(),
                                Some(prompt_id.clone()),
                            );
                            true
                        }
                    } else {
                        false
                    }
                } else {
                    finish_wake_turn(
                        agent,
                        &prompt_id,
                        &stop_reason,
                        agent_result.as_deref(),
                        terminal_meta_str(
                            session_notif.meta.as_ref(),
                            super::super::turn_completion::CANCEL_TRIGGER_KEY,
                        ),
                        terminal_meta_str(
                            session_notif.meta.as_ref(),
                            super::super::turn_completion::CANCELLATION_CATEGORY_KEY,
                        ),
                    );
                    true
                }
            } else if is_server_initiated_prompt(&prompt_id)
                && !is_scheduler_fired_prompt(&prompt_id)
            {
                if agent.session.state.is_busy() {
                    if agent.session.state.command_in_flight().is_some() {
                        agent.session.tracker.snapshot_output_epoch();
                    }
                    false
                } else {
                    agent.session.tracker.finish_turn(&mut agent.scrollback);
                    true
                }
            } else {
                terminal_outcome =
                    Some(super::super::turn_completion::finalize_turn_from_terminal(
                        agent,
                        root_session_id,
                        super::super::turn_completion::TerminalSignal {
                            prompt_id: Some(&prompt_id),
                            stop_reason: Some(&stop_reason),
                            agent_result: agent_result.as_deref(),
                            cancel_trigger: terminal_meta_str(
                                session_notif.meta.as_ref(),
                                super::super::turn_completion::CANCEL_TRIGGER_KEY,
                            ),
                            cancellation_category: terminal_meta_str(
                                session_notif.meta.as_ref(),
                                super::super::turn_completion::CANCELLATION_CATEGORY_KEY,
                            ),
                        },
                    ));
                false
            }
        }
        PiSessionUpdate::HookAnnotation { message } => {
            if app.appearance.disable_plugins {
                return false;
            }
            tracing::debug!("Hook annotation: {message}");
            agent
                .scrollback
                .push_block(RenderBlock::session_event(SessionEvent::HookAnnotation {
                    message,
                }));
            true
        }
        PiSessionUpdate::HookExecution {
            event_name,
            tool_name: _tool_name,
            prompt_id: batch_prompt_id,
            runs,
        } => {
            use crate::scrollback::blocks::tool::{HookPhase, HookRunEntry, HookRunStatus};
            let hook_entries: Vec<HookRunEntry> = runs
                .into_iter()
                .map(|r| {
                    let status = match r.status {
                        pi_shell::extensions::notification::HookRunStatusDto::Success {
                            elapsed_ms,
                        } => HookRunStatus::Success {
                            elapsed: std::time::Duration::from_millis(elapsed_ms),
                        },
                        pi_shell::extensions::notification::HookRunStatusDto::Skipped => {
                            HookRunStatus::Skipped
                        }
                        pi_shell::extensions::notification::HookRunStatusDto::Failed {
                            error,
                            elapsed_ms,
                            blocked: true,
                        } => HookRunStatus::Blocked {
                            detail: error,
                            elapsed: std::time::Duration::from_millis(elapsed_ms),
                        },
                        pi_shell::extensions::notification::HookRunStatusDto::Failed {
                            error,
                            elapsed_ms,
                            blocked: false,
                        } => HookRunStatus::Failed {
                            error,
                            elapsed: std::time::Duration::from_millis(elapsed_ms),
                        },
                    };
                    HookRunEntry {
                        name: r.name,
                        status,
                        output: r.output,
                    }
                })
                .collect();
            let is_tool_hook = event_name == "pre_tool_use" || event_name == "post_tool_use";
            let is_stop_hook =
                pi_hooks_plugins_types::HookEvent::from_wire(&event_name).is_turn_end();
            if is_tool_hook {
                let phase = if event_name == "pre_tool_use" {
                    HookPhase::Pre
                } else {
                    HookPhase::Post
                };
                if let Some(entry_id) = agent.scrollback.last_tool_call_entry_id() {
                    agent.scrollback.attach_hooks(entry_id, phase, hook_entries);
                }
            } else if is_stop_hook && !meta.is_replay && !agent.session.loading_replay {
                let local_turn_active =
                    agent.session.state.is_turn_running() || agent.session.state.is_cancelling();
                let batch_is_wake = batch_prompt_id.as_deref().is_some_and(is_wake_prompt);
                let foreign_batch = batch_prompt_id.is_some()
                    && agent.session.current_prompt_id.is_some()
                    && batch_prompt_id != agent.session.current_prompt_id
                    && !batch_is_wake;
                if foreign_batch {
                    agent
                        .scrollback
                        .push_lifecycle_hooks(event_name, hook_entries);
                } else if !batch_is_wake && local_turn_active {
                    let stash_pid = batch_prompt_id
                        .clone()
                        .or_else(|| agent.session.current_prompt_id.clone());
                    stash_live_stop_batch(
                        agent,
                        stash_pid,
                        event_name,
                        hook_entries,
                        batch_prompt_id.is_some(),
                    );
                } else if let Some(entry_id) = agent
                    .scrollback
                    .latest_turn_marker_accepting(&event_name, batch_prompt_id.as_deref())
                {
                    agent.scrollback.attach_stop_hooks_to_marker(
                        entry_id,
                        event_name,
                        hook_entries,
                        batch_prompt_id.as_deref(),
                    );
                } else {
                    agent
                        .scrollback
                        .push_lifecycle_hooks(event_name, hook_entries);
                }
            } else {
                agent
                    .scrollback
                    .push_lifecycle_hooks(event_name, hook_entries);
            }
            true
        }
        PiSessionUpdate::SessionSummaryGenerated { session_summary } => {
            let title_is_manual = session_notif.meta.as_ref().and_then(|v| {
                v.get(pi_shell::extensions::notification::TITLE_IS_MANUAL_META_KEY)
                    .and_then(|v| v.as_bool())
            });
            match title_is_manual {
                Some(true) => {
                    if let Some(clean) =
                        pi_shell::session::persistence::sanitize_and_cap_title(
                            &session_summary,
                        )
                    {
                        agent.display_name = Some(clean.clone());
                        agent.generated_session_title = Some(clean);
                        agent.title_unpin_committed = false;
                    }
                }
                other => {
                    let pin = if other == Some(false) {
                        agent.title_unpin_committed = true;
                        agent.display_name.take()
                    } else {
                        None
                    };
                    let decoded = crate::util::decode_html_entities(&session_summary);
                    if let Some(clean) =
                        pi_shell::session::persistence::sanitize_and_cap_title(&decoded)
                    {
                        agent.generated_session_title = Some(clean);
                    } else if other == Some(false)
                        && agent.generated_session_title.as_deref() == pin.as_deref()
                    {
                        agent.generated_session_title = None;
                    }
                }
            }
            true
        }
        PiSessionUpdate::LastTurnSummary {
            summary,
            prompt_id: _,
        } => {
            agent.set_last_turn_summary(Some(summary));
            true
        }
        PiSessionUpdate::SessionRecap { summary, auto } => {
            use crate::scrollback::block::RenderBlock;
            use crate::scrollback::blocks::SessionEvent;
            if should_drop_late_auto_recap(auto, meta.is_replay, agent) {
                tracing::debug!("dropping late auto SessionRecap; CLI not idle for recap");
                false
            } else if should_drop_duplicate_auto_recap(auto, meta.is_replay, &agent.scrollback) {
                tracing::debug!(
                    "dropping duplicate live auto SessionRecap; recap already shown since last user turn"
                );
                app.notification_service.focus_tracker.mark_recap_shown();
                false
            } else {
                app.notification_service.focus_tracker.mark_recap_shown();
                let recap_block = RenderBlock::session_event(SessionEvent::Recap { summary, auto });
                apply_recap_block(agent, auto, recap_block);
                true
            }
        }
        PiSessionUpdate::SessionRecapUnavailable => {
            if meta.is_replay {
                false
            } else if let Some(pending_id) = agent.pending_recap_entry.take() {
                agent.scrollback.remove_entry(pending_id);
                agent.show_toast(crate::app::dispatch::recap_unavailable_toast(
                    crate::app::dispatch::scrollback_has_user_messages(&agent.scrollback),
                ));
                true
            } else {
                false
            }
        }
        PiSessionUpdate::ModelAutoSwitched {
            previous_model_id,
            new_model_id,
            reason,
        } => {
            use crate::scrollback::block::RenderBlock;
            use crate::scrollback::blocks::SessionEvent;
            let available_count = agent.session.models.available.len();
            let available_keys: Vec<&str> = agent
                .session
                .models
                .available
                .keys()
                .take(10)
                .map(|m| m.0.as_ref())
                .collect();
            tracing::warn!(
                session_id = session_notif.session_id.0.as_ref(),
                previous = %previous_model_id,
                new = %new_model_id,
                available_count,
                available_keys = ?available_keys,
                "Model auto-switched: previous model no longer available"
            );
            crate::unified_log::warn(
                "model auto-switched: previous model unavailable",
                Some(session_notif.session_id.0.as_ref()),
                Some(serde_json::json!({
                    "previous_model": previous_model_id.as_str(),
                    "new_model": new_model_id.as_str(),
                    "available_count": available_count,
                    "available_keys": available_keys,
                })),
            );
            agent.scrollback.push_block(RenderBlock::session_event(
                SessionEvent::ModelUnavailable {
                    previous_model_id,
                    new_model_id,
                    reason,
                },
            ));
            true
        }
        PiSessionUpdate::ModelChanged {
            model_id,
            reasoning_effort,
        } => {
            if agent.session.model_switch_pending {
                tracing::debug!(
                    session_id = session_notif.session_id.0.as_ref(),
                    model_id = %model_id,
                    "ignoring ModelChanged broadcast — local switch is in flight"
                );
                return false;
            }
            use pi_shell::sampling::types::ReasoningEffort;
            let new_model_id = acp::ModelId::new(model_id.clone());
            if !agent.session.models.available.contains_key(&new_model_id) {
                if pi_shell::agent::chat_modes::process_chat_mode_enabled() {
                    agent.session.models.available.insert(
                        new_model_id.clone(),
                        acp::ModelInfo::new(new_model_id.clone(), model_id.clone()),
                    );
                } else {
                    tracing::warn!(
                        session_id = session_notif.session_id.0.as_ref(),
                        model_id = %model_id,
                        "ignoring ModelChanged broadcast — model not in local catalog"
                    );
                    return false;
                }
            }
            let effort = reasoning_effort
                .as_deref()
                .and_then(|s| s.parse::<ReasoningEffort>().ok());
            let prev_model = agent.session.models.current.clone();
            let prev_effort = agent.session.models.reasoning_effort;
            agent
                .session
                .models
                .set_current(new_model_id.clone(), effort);
            agent.session.user_model_preference = Some(new_model_id.clone());
            let resolved_effort = agent.session.models.reasoning_effort;
            let actually_changed =
                prev_model.as_ref() != Some(&new_model_id) || prev_effort != resolved_effort;
            if actually_changed {
                tracing::info!(
                    session_id = session_notif.session_id.0.as_ref(),
                    model_id = %model_id,
                    effort = ?resolved_effort,
                    "ModelChanged broadcast applied (remote switch)"
                );
            }
            actually_changed
        }
        PiSessionUpdate::MemoryFiles { files } => {
            let entries = crate::views::memory_modal::build_entries(files);
            let modal_state = crate::views::memory_modal::MemoryModalState::new(entries);
            agent.active_modal = Some(crate::views::modal::ActiveModal::MemoryBrowser {
                state: Box::new(modal_state),
            });
            true
        }
        update @ PiSessionUpdate::WorkflowUpdated { .. } => ingest_workflow_update(agent, update),
        PiSessionUpdate::GoalUpdated {
            goal_id,
            objective,
            status,
            phase,
            token_budget,
            tokens_used,
            elapsed_ms,
            total_deliverables,
            completed_deliverables,
            current_deliverable_id,
            current_deliverable_title,
            current_subagent_role,
            total_worker_rounds,
            total_verify_rounds,
            token_baseline,
            finished_subagent_tokens,
            live_subagent_tokens,
            live_tokens_by_model,
            live_context_pct,
            live_turn_count,
            live_tool_call_count,
            last_event,
            last_event_detail,
            last_event_timestamp,
            pause_message,
            classifier_runs_attempted,
            classifier_max_runs,
            last_classifier_verdict,
            last_classifier_details_path,
            verifying_completion,
            planning,
            ..
        } => {
            let new_status = GoalDisplayStatus::parse(&status);
            let just_completed = new_status == GoalDisplayStatus::Complete
                && agent
                    .goal_state
                    .as_ref()
                    .is_none_or(|g| g.status != GoalDisplayStatus::Complete);
            if status == "cleared" {
                if let Some(g) = agent.goal_state.take() {
                    agent.last_cleared_goal_id = Some(g.goal_id);
                }
                agent.show_goal_detail = false;
                true
            } else if agent.last_cleared_goal_id.as_deref() == Some(goal_id.as_str()) {
                false
            } else {
                let elapsed_floor_ms = agent
                    .goal_state
                    .as_ref()
                    .filter(|g| g.goal_id == goal_id)
                    .map(|g| g.live_elapsed_ms())
                    .unwrap_or(0)
                    .max(elapsed_ms);
                if just_completed {
                    agent.scrollback.push_block(RenderBlock::session_event(
                        SessionEvent::GoalCompleted {
                            elapsed: std::time::Duration::from_millis(elapsed_floor_ms),
                        },
                    ));
                }
                let last_classifier_details_exists = last_classifier_details_path
                    .as_deref()
                    .is_some_and(|p| std::path::Path::new(p).exists());
                agent.goal_state = Some(GoalDisplayState {
                    goal_id,
                    objective,
                    status: new_status,
                    phase: GoalDisplayPhase::parse(&phase),
                    token_budget,
                    tokens_used,
                    elapsed_ms,
                    total_deliverables,
                    completed_deliverables,
                    current_deliverable_id,
                    current_deliverable_title,
                    current_subagent_role,
                    total_worker_rounds,
                    total_verify_rounds,
                    live_subagent_tokens,
                    live_tokens_by_model,
                    live_context_pct,
                    live_turn_count,
                    live_tool_call_count,
                    last_event,
                    last_event_detail,
                    last_event_timestamp,
                    token_baseline,
                    finished_subagent_tokens,
                    deliverables: Vec::new(),
                    pause_message,
                    classifier_runs_attempted,
                    classifier_max_runs,
                    last_classifier_verdict,
                    last_classifier_details_path,
                    last_classifier_details_exists,
                    verifying_completion: verifying_completion.unwrap_or(false),
                    planning: planning.unwrap_or(false),
                    received_at: std::time::Instant::now(),
                    elapsed_floor_ms,
                });
                true
            }
        }
        PiSessionUpdate::InteractionResolved { tool_call_id } => {
            agent.dismiss_resolved_interaction(&tool_call_id)
        }
        PiSessionUpdate::SessionStatus(status) => {
            agent.status_context = Some(*status);
            status_snapshot_applied = true;
            false
        }
        _ => {
            tracing::trace!(
                "Ignoring {}: {:?}",
                notif.method.as_ref(),
                std::mem::discriminant(&session_notif.update)
            );
            return false;
        }
    };
    let mut changed = changed;
    if status_snapshot_applied && is_active {
        app.refresh_status_line_now();
        changed |= app.status_line.take_changed();
    }
    if let Some(agent) = app.agents.get_mut(&parent_id) {
        if let Some(seq) = meta.event_seq
            && !meta.is_replay
            && !is_workflow_update
        {
            agent.last_applied_pi_event_seq = Some(
                agent
                    .last_applied_pi_event_seq
                    .map_or(seq, |last| last.max(seq)),
            );
        }
        if let Some(id) = meta.event_id {
            agent.advance_last_seen_event_id(id, meta.event_seq);
        }
    }
    if let Some(payload) = deferred_subagent_finish {
        if let Some(deferred) = redispatched_subagent_finish(payload) {
            let _ = handle_session_notification(&deferred, app);
        } else {
            tracing::warn!(
                session_id = session_notif.session_id.0.as_ref(),
                "Failed to serialize deferred subagent finish"
            );
        }
    }
    if let Some(outcome) = terminal_outcome {
        return super::super::turn_completion::apply_terminal_outcome(
            outcome, app, parent_id, is_active,
        );
    }
    changed && is_active
}
/// Handle an pi session notification that targets a child (subagent) session.
///
/// Events like compaction, retry, and memory flush are emitted by the child's
/// `acp_session` with the *child's* `session_id`. This routes them to the
/// correct child view and updates `SubagentInfo` where appropriate.
pub(super) fn handle_child_session_notification(
    update: PiSessionUpdate,
    child_sid: &str,
    agent: &mut AgentView,
    is_api_key_auth: bool,
) -> bool {
    match update {
        PiSessionUpdate::AutoCompactStarted { .. }
        | PiSessionUpdate::AutoCompactCompleted { .. }
        | PiSessionUpdate::AutoCompactFailed { .. }
        | PiSessionUpdate::AutoCompactCancelled { .. }
        | PiSessionUpdate::RetryState(_)
        | PiSessionUpdate::MemoryFlushCompleted { .. }
        | PiSessionUpdate::MemoryDreamCompleted { .. }
        | PiSessionUpdate::MemorySessionSaved { .. } => {
            let mut changed = false;
            if let Some(child_view) = agent.child_view_for_live_update_mut(child_sid) {
                changed = apply_child_view_session_event(child_view, &update, is_api_key_auth);
            }
            if let PiSessionUpdate::AutoCompactCompleted { tokens_after, .. } = update
                && let Some(info) = agent.subagent_sessions.get_mut(child_sid)
            {
                info.tokens_used = Some(tokens_after);
                if let Some(cw) = info.context_window_tokens.filter(|&cw| cw > 0) {
                    info.context_usage_pct =
                        Some(pi_token_estimation::usage_percentage_u8(tokens_after, cw));
                }
            }
            changed
        }
        PiSessionUpdate::ToolCallDeltaChunk {
            ref name,
            tool_index,
            ..
        } => {
            let Some(child_view) = agent.subagent_views.get_mut(child_sid) else {
                return false;
            };
            if child_view.session.loading_replay {
                return false;
            }
            let row_live = agent
                .subagent_sessions
                .get(child_sid)
                .is_some_and(|info| !info.finished);
            if !row_live {
                return false;
            }
            if !child_view
                .session
                .tracker
                .note_tool_call_arguments_delta(name.as_deref(), tool_index)
            {
                return false;
            }
            let activity_label = subagent_activity_label(child_view);
            sync_subagent_activity(agent, child_sid, activity_label);
            true
        }
        _ => false,
    }
}
/// Apply one pi session event to a child view: the scrollback/session
/// rendering shared by the live child routing above and the from-disk child
/// replay (`crate::app::subagent::replay_inherited_updates`), so a rebuilt
/// transcript keeps the same compaction/retry markers the live one had.
pub(crate) fn apply_child_view_session_event(
    child_view: &mut AgentView,
    update: &PiSessionUpdate,
    is_api_key_auth: bool,
) -> bool {
    let changed = apply_session_event(
        update,
        &mut child_view.session,
        &mut child_view.scrollback,
        is_api_key_auth,
    );
    if let PiSessionUpdate::AutoCompactCompleted { tokens_after, .. } = update {
        refresh_context_used(child_view, *tokens_after);
    }
    changed
}
/// Apply a compaction or retry event to a session's activity state and scrollback.
///
/// Shared between the root agent and child (subagent) notification paths.
/// Test-only shim so dispatch-level tests can replay real notification
/// sequences (e.g. `RetryState::Retrying` → `Exhausted`) through the
/// production handler — the Retrying arm clears the `in_flight_prompt`
/// rewind stash, which a fixture setting fields directly would miss.
#[cfg(test)]
pub(crate) fn apply_session_event_for_test(
    update: &PiSessionUpdate,
    session: &mut AgentSession,
    scrollback: &mut crate::scrollback::state::ScrollbackState,
) -> bool {
    apply_session_event(update, session, scrollback, false)
}
pub(super) fn apply_session_event(
    update: &PiSessionUpdate,
    session: &mut AgentSession,
    scrollback: &mut crate::scrollback::state::ScrollbackState,
    is_api_key_auth: bool,
) -> bool {
    match update {
        PiSessionUpdate::AutoCompactStarted { percentage, .. } => {
            tracing::info!("Auto-compact started: {percentage}% context used");
            if session.compact_held_prompt.is_none() {
                session.compact_held_prompt = session.in_flight_prompt.clone();
            }
            session.in_flight_prompt = None;
            session.set_compaction_activity(Some(TurnActivity::AutoCompacting));
            scrollback.push_block(RenderBlock::session_event(
                SessionEvent::CompactionStarted {
                    percentage: *percentage,
                },
            ));
            true
        }
        PiSessionUpdate::AutoCompactCompleted {
            tokens_before,
            tokens_after,
            elapsed_ms,
            ..
        } => {
            tracing::info!("Auto-compact completed: {tokens_after} tokens after");
            session.set_compaction_activity(None);
            session.compact_held_prompt = None;
            if session.loading_replay {
                scrollback.push_block(RenderBlock::session_event(
                    SessionEvent::CompactionCompleted {
                        tokens_before: *tokens_before,
                        tokens_after: *tokens_after,
                        elapsed_ms: *elapsed_ms,
                    },
                ));
            } else {
                session.defer_compaction(*tokens_before, *tokens_after, *elapsed_ms);
            }
            true
        }
        PiSessionUpdate::AutoCompactFailed { error } => {
            tracing::error!(error = %error, "Auto-compaction failed");
            session.set_compaction_activity(None);
            scrollback.push_block(RenderBlock::session_event(SessionEvent::CompactionFailed {
                error: error.clone(),
            }));
            true
        }
        PiSessionUpdate::AutoCompactCancelled { .. } => {
            tracing::info!("Auto-compact cancelled");
            session.set_compaction_activity(None);
            session.compact_held_prompt = None;
            scrollback.push_block(RenderBlock::session_event(
                SessionEvent::CompactionCancelled,
            ));
            true
        }
        PiSessionUpdate::RetryState(retry) => {
            tracing::debug!("Retry state: {retry:?}");
            apply_retry_state(retry, session, scrollback, is_api_key_auth);
            true
        }
        PiSessionUpdate::ImageDropped { notes } => {
            let message = notes.join("\n");
            tracing::info!("Image dropped: {message}");
            scrollback.push_block(RenderBlock::system(message));
            true
        }
        _ => false,
    }
}
/// True if the trailing run of session/system blocks contains a
/// [`SessionEvent::CompactionFailed`]. Used so we don't stack a [`SessionEvent::ContextTooLarge`]
/// prompt on top of the compaction handler's "too large to compact" message.
pub(super) fn scrollback_has_recent_compaction_failed(
    scrollback: &crate::scrollback::state::ScrollbackState,
) -> bool {
    use crate::scrollback::block::RenderBlock;
    for idx in (0..scrollback.len()).rev() {
        match scrollback.entry(idx).map(|e| &e.block) {
            Some(RenderBlock::SessionEvent(ev)) => {
                if matches!(ev.event, SessionEvent::CompactionFailed { .. }) {
                    return true;
                }
            }
            Some(RenderBlock::System(_)) => {}
            _ => break,
        }
    }
    false
}
/// Handle an `ImageCompressed` notification. A successful compression is
/// deliberately invisible in the TUI (log-only): it needs no user action,
/// and the model-facing `<image_compression_notice>` reminder is attached
/// to the prompt independently. Only the re-encode *fallback* — the
/// oversized original was KEPT — surfaces, as a persistent scrollback
/// warning (and is re-materialized on session replay).
pub(super) fn apply_image_compressed(
    agent: &mut AgentView,
    images: &[pi_shell::extensions::notification::ImageCompressedEntry],
    message: &str,
) -> bool {
    if images.is_empty() {
        tracing::warn!("Image re-encode fallback: {message}");
        agent
            .scrollback
            .push_block(RenderBlock::system(message.to_owned()));
        return true;
    }
    tracing::info!("Image compressed: {message}");
    false
}
pub(super) fn apply_retry_state(
    retry: &pi_shell::extensions::notification::RetryState,
    session: &mut AgentSession,
    scrollback: &mut crate::scrollback::state::ScrollbackState,
    is_api_key_auth: bool,
) {
    let mut is_credit_limit = false;
    let mut is_reauth = false;
    use pi_shell::extensions::notification::RetryState;
    match retry {
        RetryState::Retrying {
            attempt,
            max_retries,
            reason,
        } => {
            session.set_retry_activity(Some(TurnActivity::Retrying {
                attempt: *attempt,
                max_retries: *max_retries,
                reason: reason.clone(),
            }));
        }
        RetryState::Exhausted {
            attempts,
            reason,
            is_rate_limited: rate_limited,
        } => {
            session.set_retry_activity(None);
            session.rate_limited = *rate_limited;
            if *rate_limited {
                pi_telemetry::session_ctx::log_event(
                    pi_telemetry::events::RateLimitHit {
                        model_id: session
                            .models
                            .current
                            .as_ref()
                            .map(|m| m.0.to_string())
                            .unwrap_or_default(),
                        attempts: *attempts,
                    },
                );
            }
            is_credit_limit = super::super::dispatch::is_credit_limit_error(None, reason);
            let is_free_usage = *rate_limited
                && pi_shell::sampling::error::is_free_usage_exhausted_error(reason);
            if is_credit_limit {
                session.credit_limit_blocked = true;
            } else if is_free_usage {
                session.free_usage_blocked = true;
            } else if !*rate_limited && is_reauthable_failure(None, reason) {
                is_reauth = true;
                scrollback.push_block(RenderBlock::session_event(SessionEvent::ReAuthRequired));
            } else if *rate_limited {
                let error = crate::app::effects::sanitize_user_error(
                    &format_rate_limited_user_message(Some(reason.as_str()), is_api_key_auth),
                );
                scrollback.push_block(RenderBlock::session_event(SessionEvent::RetryFailed {
                    error,
                    error_type: None,
                }));
            } else {
                scrollback.push_block(RenderBlock::session_event(
                    crate::app::error_display::format_request_failure(None, None, reason)
                        .into_session_event(),
                ));
            }
        }
        RetryState::Failed {
            error_type,
            message,
        } => {
            session.set_retry_activity(None);
            let wire = crate::app::error_display::WireErrorType::parse(Some(error_type.as_str()));
            if wire == crate::app::error_display::WireErrorType::EncryptedContentMismatch {
                session.model_incompatible = true;
            }
            is_credit_limit = super::super::dispatch::is_credit_limit_error(None, message);
            if is_credit_limit {
                session.credit_limit_blocked = true;
            } else if is_reauthable_failure(Some(error_type.as_str()), message) {
                is_reauth = true;
                scrollback.push_block(RenderBlock::session_event(SessionEvent::ReAuthRequired));
            } else if wire == crate::app::error_display::WireErrorType::DiskFull {
                if !crate::app::dispatch::scrollback_has_recent_disk_full(scrollback) {
                    scrollback.push_block(RenderBlock::session_event(SessionEvent::DiskFull));
                }
            } else if wire == crate::app::error_display::WireErrorType::ContextLength {
                if !scrollback_has_recent_compaction_failed(scrollback) {
                    scrollback
                        .push_block(RenderBlock::session_event(SessionEvent::ContextTooLarge));
                }
            } else if wire == crate::app::error_display::WireErrorType::EncryptedContentMismatch
                || wire == crate::app::error_display::WireErrorType::LegacyAuth
            {
                scrollback.push_block(RenderBlock::session_event(SessionEvent::RetryFailed {
                    error: message.clone(),
                    error_type: Some(error_type.clone()),
                }));
            } else {
                scrollback.push_block(RenderBlock::session_event(
                    crate::app::error_display::format_request_failure(
                        None,
                        Some(error_type.as_str()),
                        message,
                    )
                    .into_session_event(),
                ));
            }
        }
    }
    if is_credit_limit {
        pi_telemetry::session_ctx::log_event(pi_telemetry::events::CreditLimitHit {
            model_id: session
                .models
                .current
                .as_ref()
                .map(|m| m.0.to_string())
                .unwrap_or_default(),
        });
    } else if !is_reauth {
        session.in_flight_prompt = None;
    }
}
/// Single source of truth for plan-mode state on the pager side.
///
/// The agent emits `CurrentModeUpdate` on every entry and exit — both for
/// user-driven mode switches (Shift+Tab → `session/set_mode`) and for
/// agent-driven `EnterPlanMode` / `ExitPlanMode` tool calls (mapped by the
/// notification bridge).
///
/// Do not be tempted to infer mode from tool-call titles: titles incorporate
/// raw model/user input (Grep pattern, Bash command, search query, ...), so
/// a substring match silently bricks sessions whenever any tool happens to
/// mention `enter_plan_mode`.
///
/// Returns `true` when a `CurrentModeUpdate` was processed so the
/// caller can refresh open settings modals after the per-agent borrow
/// releases.
pub(super) fn detect_plan_mode_change(update: &acp::SessionUpdate, agent: &mut AgentView) -> bool {
    use pi_tools::types::SessionMode;
    let acp::SessionUpdate::CurrentModeUpdate(cmu) = update else {
        return false;
    };
    let mode = SessionMode::from_id(cmu.current_mode_id.0.as_ref());
    let was_active = agent.plan_mode_active;
    let now_active = mode.is_plan();
    agent.plan_mode_active = now_active;
    agent.plan_mode_pending = None;
    if was_active != now_active {
        tracing::info!(
            mode_id = %cmu.current_mode_id.0,
            plan_active = now_active,
            "Plan mode state updated (from CurrentModeUpdate)"
        );
    }
    true
}
