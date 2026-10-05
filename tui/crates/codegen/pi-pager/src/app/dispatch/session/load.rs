//! Session loading, session pickers, and deep-search dispatchers.
use super::session_list::dispatch_fetch_session_list;
use crate::acp::tracker::AcpUpdateTracker;
use crate::app::actions::Effect;
use crate::app::agent::{AgentCommand, AgentId, AgentSession, AgentState};
use crate::app::agent_view::AgentView;
use crate::app::app_view::AppView;
use crate::app::cancel_latency::TurnEnd;
use crate::app::dispatch::ctx::{
    SwitchCause, get_active_agent, get_active_agent_mut, switch_to_agent, with_active_agent,
};
use crate::app::dispatch::modes::inherit_auto_mode;
use crate::app::dispatch::prompt::defer_to_open_reload_window;
use crate::app::dispatch::queue::maybe_drain_queue;
use crate::app::dispatch::status::notify_session_ready;
use crate::scrollback::block::RenderBlock;
use crate::scrollback::blocks::SessionEvent;
use crate::scrollback::state::ScrollbackState;
use agent_client_protocol as acp;
/// Create a placeholder agent and load an existing session by ID.
///
/// `session_cwd` overrides the CWD in the `LoadSessionRequest`. This is needed
/// when resuming a session that was created in a different CWD (e.g., a worktree).
pub(in crate::app::dispatch) fn dispatch_load_session(
    app: &mut AppView,
    session_id: String,
    session_cwd: Option<std::path::PathBuf>,
) -> Vec<Effect> {
    if !app.session_startup_allowed() {
        app.deferred_startup.session =
            Some(crate::app::session_startup::DeferredSessionStartup::Load {
                session_id,
                session_cwd,
            });
        return vec![];
    }
    dispatch_load_session_ungated(app, session_id, session_cwd)
}
/// Clear `session_id` from any existing agent that already owns the given
/// session, then return a freshly constructed [`acp::SessionId`].
///
/// Without this, `find_session_match` finds the stale agent first (IndexMap
/// insertion order) and routes all ACP notifications to it instead of the
/// new agent.
pub(in crate::app::dispatch) fn clear_stale_session_id(
    app: &mut AppView,
    session_id: &str,
) -> acp::SessionId {
    let sid = acp::SessionId::new(session_id);
    for agent in app.agents.values_mut() {
        if agent.session.session_id.as_ref() == Some(&sid) {
            agent.unbind_session_id();
        }
    }
    sid
}
/// If a local agent already owns this id, focus it.
///
/// - Eager `session_id` + leftover load placeholder after `SessionLoadFailed`
///   is not "open" — reissue load instead of focusing.
pub(in crate::app::dispatch) fn focus_if_session_already_open(
    app: &mut AppView,
    session_id: &str,
) -> Option<AgentId> {
    let existing_id = app.agents.iter().find_map(|(id, a)| {
        let sid_ok = a
            .session
            .session_id
            .as_ref()
            .is_some_and(|sid| &*sid.0 == session_id);
        if !sid_ok {
            return None;
        }
        if a.loading_placeholder_id.is_some() && !a.session.loading_replay {
            return None;
        }
        Some(*id)
    })?;
    switch_to_agent(app, existing_id, SwitchCause::Load);
    Some(existing_id)
}
fn dispatch_load_session_ungated(
    app: &mut AppView,
    session_id: String,
    session_cwd: Option<std::path::PathBuf>,
) -> Vec<Effect> {
    invalidate_picker_fetch_on_dismiss(app);
    if focus_if_session_already_open(app, &session_id).is_some() {
        return vec![];
    }
    let acp_session_id = clear_stale_session_id(app, &session_id);
    let agent_id = AgentId(app.next_agent_id);
    app.next_agent_id += 1;
    let mut scrollback = ScrollbackState::new();
    scrollback.set_appearance(app.appearance.clone());
    let loading_msg = if matches!(app.restore_code, Some(true)) {
        format!("Restoring code for session {}...", &session_id)
    } else {
        format!("Loading session {}...", &session_id)
    };
    let loading_placeholder_id = scrollback.push_block(RenderBlock::system(loading_msg));
    let agent = AgentView::new(
        AgentSession {
            id: agent_id,
            acp_tx: app.acp_tx.clone(),
            session_id: Some(acp_session_id),
            models: app.models.clone(),
            state: AgentState::Idle,
            tracker: AcpUpdateTracker::new(),
            cwd: session_cwd.clone().unwrap_or_else(|| app.cwd.clone()),
            is_worktree: crate::app::session_startup::parent_session_is_worktree(
                &session_id,
                session_cwd.as_deref().unwrap_or(app.cwd.as_path()),
            ),
            forked_from: None,
            pending_prompts: std::collections::VecDeque::new(),
            next_queue_id: 0,
            yolo_mode: app.default_yolo,
            auto_mode: inherit_auto_mode(app),
            prompt_history: Vec::new(),
            loading_replay: true,
            restore_degree: None,
            rate_limited: false,
            model_incompatible: false,
            credit_limit_blocked: false,
            free_usage_blocked: false,
            available_commands: app.bootstrap_acp_commands.clone(),
            available_commands_generation: 1,
            available_tools: None,
            model_switch_pending: false,
            user_model_preference: None,
            deferred_model_switch: app.deferred_model_switch_from_cli(),
            in_flight_prompt: None,
            compact_held_prompt: None,
            current_prompt_id: None,
            created_via_new: false,
        },
        scrollback,
    );
    app.agents.insert(agent_id, agent);
    let agent_mut = app.agents.get_mut(&agent_id).unwrap();
    agent_mut.attached_as_viewer = true;
    agent_mut.begin_replay_window();
    agent_mut.loading_placeholder_id = Some(loading_placeholder_id);
    agent_mut.prompt.set_compact(app.appearance.prompt.compact);
    agent_mut.prompt.adopt_slash_mru(app.slash_mru.clone());
    agent_mut
        .prompt
        .adopt_command_tags(app.command_tags.clone());
    agent_mut
        .prompt
        .set_contextual_hints(app.contextual_hints.undo, app.contextual_hints.plan_mode);
    agent_mut.set_voice_mode_available(app.voice_mode_enabled);
    agent_mut.scrollback.begin_batch();
    if matches!(app.restore_code, Some(true)) {
        agent_mut.session.start_command(AgentCommand::RestoreCode);
        agent_mut.turn_started_at = Some(std::time::Instant::now());
    }
    agent_mut.apply_app_scoped_gates(
        app.sharing_enabled,
        app.usage_visible,
        !app.has_external_auth_provider,
        app.screen_mode,
        &app.active_announcements,
        &app.tier_restricted_commands,
    );
    agent_mut.apply_credit_balance(app.credit_balance.clone(), app.auto_topup.clone());
    switch_to_agent(app, agent_id, SwitchCause::Load);
    vec![Effect::LoadSession {
        agent_id,
        session_id,
        session_cwd,
    }]
}
/// Load the session selected in the session picker.
pub(in crate::app::dispatch) fn dispatch_pick_session(
    app: &mut AppView,
    index: usize,
) -> Vec<Effect> {
    use crate::views::modal::ActiveModal;
    let mut picker_dismissed = false;
    let entry_data = if let Some(agent) = get_active_agent_mut(app) {
        if let Some(ActiveModal::SessionPicker { entries, .. }) = agent.active_modal.as_mut() {
            let data = entries
                .as_ref()
                .and_then(|s| s.get(index))
                .map(|e| (e.id.clone(), e.cwd.clone()));
            agent.active_modal = None;
            picker_dismissed = true;
            data
        } else {
            None
        }
    } else {
        None
    };
    if picker_dismissed {
        invalidate_picker_fetch_on_dismiss(app);
    }
    let (session_id, cwd) = match entry_data {
        Some(d) => d,
        None => {
            let sessions = match app.session_picker_entries.take() {
                Some(s) => s,
                None => return vec![],
            };
            if !picker_dismissed {
                invalidate_picker_fetch_on_dismiss(app);
            }
            let entry = match sessions.get(index) {
                Some(e) => e,
                None => return vec![],
            };
            let d = (entry.id.clone(), entry.cwd.clone());
            app.session_picker_loading = false;
            app.session_picker_state.set_query("");
            app.session_picker_state.search_active = false;
            app.session_picker_state.expanded.clear();
            d
        }
    };
    if focus_if_session_already_open(app, &session_id).is_some() {
        return vec![];
    }
    let local_cwd = app.cwd.to_string_lossy().to_string();
    if pi_shell::session::resolve_local_session(&session_id, &local_cwd).is_some() {
        return dispatch_load_session(app, session_id, None);
    }
    if let Some(original_cwd) = pi_shell::session::resolve_local_session_any_cwd(&session_id) {
        return dispatch_load_session(
            app,
            session_id,
            Some(std::path::PathBuf::from(original_cwd)),
        );
    }
    // In pi-python architecture, sessions are managed by the ACP agent backend (JsonlSessionRepo)
    // rather than grok's legacy ~/.pi-python/sessions/<encoded_cwd>/<session_id>/summary.json.
    // When selected from the session picker, the session is already known to the ACP agent.
    let session_cwd = if cwd.is_empty() || cwd == local_cwd {
        None
    } else {
        Some(std::path::PathBuf::from(cwd))
    };
    dispatch_load_session(app, session_id, session_cwd)
}
fn keep_picker_entry(
    entry: &crate::app::app_view::SessionPickerEntry,
    source: &str,
    session_id: &str,
    match_id_only: bool,
) -> bool {
    if match_id_only {
        entry.id != session_id
    } else {
        entry.source != source || entry.id != session_id
    }
}
/// Remove a deleted session identity from the modal session picker and the
/// welcome-screen picker, then re-anchor the selection on a real row.
///
/// Called after [`crate::app::actions::TaskResult::DeleteSessionComplete`] so
/// the just-deleted entry vanishes from the open list without a full refetch.
pub(in crate::app::dispatch) fn remove_session_from_pickers(
    app: &mut AppView,
    source: &str,
    session_id: &str,
    match_id_only: bool,
) {
    use crate::views::modal::ActiveModal;
    use crate::views::session_picker::build_entry_map;
    if let Some(agent) = get_active_agent_mut(app)
        && let Some(ActiveModal::SessionPicker {
            entries,
            state,
            pending_delete,
            ..
        }) = agent.active_modal.as_mut()
    {
        if pending_delete
            .as_ref()
            .is_some_and(|pd| pd.source == source && pd.session_id == session_id)
        {
            *pending_delete = None;
        }
        if let Some(list) = entries.as_mut() {
            list.retain(|entry| keep_picker_entry(entry, source, session_id, match_id_only));
        }
        let current_repo =
            crate::views::session_picker::repo_name_from_cwd(&agent.session.cwd.to_string_lossy());
        let map = build_entry_map(
            entries.as_deref(),
            state.query(),
            true,
            Some(current_repo.as_str()),
        );
        reanchor_grouped_selection(state, &map);
    }
    if app
        .session_picker_pending_delete
        .as_ref()
        .is_some_and(|pd| pd.source == source && pd.session_id == session_id)
    {
        app.session_picker_pending_delete = None;
    }
    if let Some(list) = app.session_picker_entries.as_mut() {
        list.retain(|entry| keep_picker_entry(entry, source, session_id, match_id_only));
    }
    let welcome_current_repo =
        crate::views::session_picker::repo_name_from_cwd(&app.cwd.to_string_lossy());
    let welcome_map = build_entry_map(
        app.session_picker_entries.as_deref(),
        app.session_picker_state.query(),
        app.session_picker_grouped,
        Some(welcome_current_repo.as_str()),
    );
    reanchor_grouped_selection(&mut app.session_picker_state, &welcome_map);
}
/// Clamp `state.selected` to a selectable slot in a grouped picker `map`
/// (`Some` = selectable row, `None` = non-selectable header).
pub(in crate::app::dispatch) fn reanchor_grouped_selection<T>(
    state: &mut crate::views::picker::PickerState,
    map: &[Option<T>],
) {
    state.scroll_offset = None;
    if map.is_empty() {
        state.selected = 0;
        return;
    }
    let mut sel = state.selected.min(map.len() - 1);
    while sel > 0 && map[sel].is_none() {
        sel -= 1;
    }
    if map[sel].is_none() {
        sel = map.iter().position(|e| e.is_some()).unwrap_or(0);
    }
    state.selected = sel;
}
pub(in crate::app::dispatch) fn session_picker_entry_matches(
    app: &AppView,
    source: &str,
    session_id: &str,
) -> bool {
    use crate::views::modal::ActiveModal;
    if let Some(agent) = get_active_agent(app)
        && let Some(ActiveModal::SessionPicker { entries, .. }) = agent.active_modal.as_ref()
    {
        return entries.as_ref().is_some_and(|entries| {
            entries
                .iter()
                .any(|entry| entry.source == source && entry.id == session_id)
        });
    }
    app.session_picker_entries.as_ref().is_some_and(|entries| {
        entries
            .iter()
            .any(|entry| entry.source == source && entry.id == session_id)
    })
}
/// Toggle the expanded card of a session row in the active picker (the modal
/// when one is open, otherwise the welcome picker).
pub(in crate::app::dispatch) fn toggle_session_card(
    app: &mut AppView,
    source: &str,
    session_id: &str,
) -> Vec<Effect> {
    use crate::views::modal::ActiveModal;
    fn toggle(expanded: &mut std::collections::HashSet<usize>, idx: usize) {
        if !expanded.remove(&idx) {
            expanded.insert(idx);
        }
    }
    if let Some(agent) = get_active_agent_mut(app)
        && let Some(ActiveModal::SessionPicker {
            entries: Some(entries),
            state,
            ..
        }) = agent.active_modal.as_mut()
    {
        if let Some(idx) = entries
            .iter()
            .position(|entry| entry.source == source && entry.id == session_id)
        {
            toggle(&mut state.expanded, idx);
        }
        return vec![];
    }
    if let Some(idx) = app.session_picker_entries.as_ref().and_then(|entries| {
        entries
            .iter()
            .position(|entry| entry.source == source && entry.id == session_id)
    }) {
        toggle(&mut app.session_picker_state.expanded, idx);
    }
    vec![]
}
#[allow(clippy::too_many_arguments)]
pub(in crate::app::dispatch) fn handle_session_loaded(
    app: &mut AppView,
    agent_id: AgentId,
    session_id: acp::SessionId,
    new_models: Option<acp::SessionModelState>,
    code_restored: bool,
    restore_summary: Option<String>,
    restore_degree: Option<pi_workspace::session::git::RestoreDegree>,
    running_prompt_id: Option<String>,
    scheduler_background_loops: Option<bool>,
) -> Vec<Effect> {
    tracing::info!(
        "Session loaded for agent {:?} session {:?}",
        agent_id,
        session_id,
    );
    if let Some(agent) = app.agents.get_mut(&agent_id) {
        if defer_to_open_reload_window(agent, agent_id, "SessionLoaded") {
            return vec![];
        }
        let hydrate_sid = session_id.clone();
        agent.bind_session_id(session_id);
        agent.scheduler_background_loops = scheduler_background_loops;
        agent.scrollback.end_batch();
        agent.session.loading_replay = false;
        agent.arm_late_replay_grace();
        agent.session.restore_degree = restore_degree;
        agent.session.finish_turn(&mut agent.scrollback);
        agent.mark_turn_finished(TurnEnd::Aborted);
        if let Some(placeholder_id) = agent.loading_placeholder_id.take() {
            agent.scrollback.remove_entry(placeholder_id);
        }
        if let Some(m) = new_models {
            app.models = Some(m).into();
            agent.session.models = app.models.clone();
        }
        let deferred = crate::app::dispatch::session::lifecycle::apply_deferred_model_switch(
            agent,
            app.cli_effort_token.as_deref(),
        );
        match (code_restored, restore_summary.as_deref()) {
            (true, Some(s)) => {
                agent
                    .scrollback
                    .push_block(RenderBlock::system(format!("\u{2713} Code restored: {s}")));
            }
            (false, Some(s)) => {
                agent.scrollback.push_block(RenderBlock::system(format!(
                    "\u{26A0} Code restore failed: {s}"
                )));
            }
            _ => {}
        }
        let adopting = running_prompt_id
            .as_deref()
            .is_some_and(|pid| agent.should_adopt_running_prompt(pid));
        let preserve = running_prompt_id.as_deref().filter(|_| adopting);
        agent.reset_follow_ups_for_reload_preserving(preserve);
        if adopting && let Some(running_pid) = running_prompt_id {
            agent.adopt_running_prompt(running_pid);
        } else {
            agent.scrollback.finish_all_running();
        }
        let mut effects = Vec::new();
        let drain = maybe_drain_queue(agent);
        effects.extend(drain.effects);
        agent.seed_prompt_history_from_scrollback();
        effects.push(Effect::FetchBilling {
            agent_id,
            silent: true,
        });
        if let Some(switch) = deferred {
            agent.session.model_switch_pending = true;
            effects.push(Effect::SwitchModel {
                agent_id,
                session_id: hydrate_sid.clone(),
                model_id: switch.model_id,
                effort: switch.effort,
                prev_model_id: switch.prev_model_id,
                config_option_id: agent.session.models.config_option_id.clone(),
            });
        }
        effects.push(Effect::RegisterActiveSession {
            session_id: hydrate_sid,
            cwd: agent.session.cwd.display().to_string(),
        });
        notify_session_ready(&app.notification_service, agent);
        crate::memory_release::release_retained_memory("session-load-replay");
        return effects;
    }
    vec![]
}
pub(in crate::app::dispatch) fn handle_session_load_failed(
    app: &mut AppView,
    agent_id: AgentId,
    session_id: acp::SessionId,
    error: String,
) -> Vec<Effect> {
    tracing::error!(agent = ?agent_id, session = ?session_id, error = %error, "Session load failed");
    if let Some(agent) = app.agents.get_mut(&agent_id) {
        if defer_to_open_reload_window(agent, agent_id, "SessionLoadFailed") {
            return vec![];
        }
        agent.pending_extensions_fetch = false;
        agent.session.finish_command();
        agent.mark_turn_finished(TurnEnd::Aborted);
        agent.scrollback.end_batch();
        agent.session.loading_replay = false;
        agent
            .scrollback
            .push_block(RenderBlock::session_event(SessionEvent::TurnFailed {
                error: format!("Couldn't load session: {error}"),
                elapsed: None,
            }));
    }
    vec![]
}
pub(in crate::app::dispatch) fn dispatch_show_session_picker(app: &mut AppView) -> Vec<Effect> {
    use crate::views::modal::ActiveModal;
    with_active_agent(app, |agent| {
        agent.active_modal = Some(ActiveModal::SessionPicker {
            state: crate::views::picker::PickerState::default(),
            entries: None,
            loading: true,
            previous_palette: None,
            window: crate::views::modal_window::ModalWindowState::new(),
            pending_delete: None,
        });
    });
    dispatch_fetch_session_list(app)
}
/// The picker (modal `/resume` or welcome screen) was dismissed without a
/// pick. Its own fields die with it, but a still-current in-flight list
/// fetch would fall through to the welcome picker fields in
/// `handle_session_list_loaded` — repopulating a picker the user just
/// closed. Invalidate it (same seq idiom as `dispatch_fetch_session_list`).
pub(in crate::app::dispatch) fn dispatch_session_picker_closed(app: &mut AppView) -> Vec<Effect> {
    invalidate_picker_fetch_on_dismiss(app);
    vec![]
}
/// Fetch invalidation shared by EVERY picker-dismissal path:
/// modal Esc/mouse close, modal and welcome picks, and the welcome-screen
/// Esc. A modal close must NOT bump — only the plain list fetch exists and
/// its response lands on the hidden welcome fields (last-write-wins). A
/// WELCOME dismissal must bump and drop the loading flag: the welcome view
/// survives the close, so a still-loading flag holds `show_picker` in a
/// spinner limbo that ignores input until the late response lands and
/// resurrects the picker.
fn invalidate_picker_fetch_on_dismiss(app: &mut AppView) {
    let welcome_dismissal = matches!(app.active_view, crate::app::app_view::ActiveView::Welcome);
    if welcome_dismissal {
        app.session_picker_list_seq += 1;
    }
    if welcome_dismissal {
        app.session_picker_loading = false;
    }
}
