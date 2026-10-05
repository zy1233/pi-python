//! Fork dispatchers and fork placeholder builders.
use super::lifecycle::{dispatch_new_session_inner_with_id, };
use crate::acp::tracker::AcpUpdateTracker;
use crate::app::actions::Effect;
use crate::app::agent::{AgentCommand, AgentId, AgentSession, AgentState};
use crate::app::agent_view::AgentView;
use crate::app::app_view::{ActiveView, AppView};
use crate::app::cancel_latency::TurnEnd;
use crate::app::dispatch::ctx::{SwitchCause, switch_to_agent};
use crate::app::dispatch::modes::inherit_auto_mode;
use crate::scrollback::block::RenderBlock;
use crate::scrollback::blocks::SessionEvent;
use crate::scrollback::state::ScrollbackState;
use std::time::Instant;
/// If `persist_mode` is `Some`, write `mode` into `*field` and append
/// a [`Effect::PersistWorktreeMode`] to `effects` with the given
/// `config_key`.
pub(in crate::app::dispatch) fn apply_persist_worktree_mode(
    field: &mut crate::app::app_view::WorktreeMode,
    effects: &mut Vec<Effect>,
    persist_mode: Option<crate::app::app_view::WorktreeMode>,
    config_key: &'static str,
) {
    if let Some(mode) = persist_mode {
        *field = mode;
        effects.push(Effect::PersistWorktreeMode { mode, config_key });
    }
}
/// Build the two persistence options shared by the fork and new-session
/// worktree question modals ("Always worktree" / "Never worktree").
pub(super) fn worktree_persist_options()
-> [pi_tools::implementations::grok_build::ask_user_question::QuestionOption; 2] {
    use pi_tools::implementations::grok_build::ask_user_question::QuestionOption;
    [
        QuestionOption {
            label: "Always worktree".into(),
            description: "Use worktree and stop asking (reset in config.toml)".into(),
            preview: None,
            id: None,
        },
        QuestionOption {
            label: "Never worktree".into(),
            description: "Skip worktree and stop asking (reset in config.toml)".into(),
            preview: None,
            id: None,
        },
    ]
}
/// Construct the placeholder agent, push discoverability markers, flip
/// the discovery gate, switch to the new agent, and emit the appropriate
/// fork effect (worktree or no-worktree path).
///
/// `worktree == true` reuses the existing
/// [`Effect::CreateWorktreeSession`] pipeline (with `load_session_id`
/// set to the parent session id). `worktree == false` emits the new
/// [`Effect::ForkSession`] which calls `legacy ext RPC` directly.
pub(in crate::app::dispatch) fn dispatch_fork_resolved(
    app: &mut AppView,
    worktree: bool,
    directive: Option<String>,
) -> Vec<Effect> {
    let ActiveView::Agent(parent_id) = app.active_view else {
        return vec![];
    };
    let Some(parent) = app.agents.get(&parent_id) else {
        return vec![];
    };
    let Some(parent_session_id) = parent.session.session_id.clone() else {
        app.show_toast("Cannot fork: session not yet created");
        return vec![];
    };
    let parent_cwd = parent.session.cwd.clone();
    let parent_is_worktree = parent.session.is_worktree;
    let new_id = AgentId(app.next_agent_id);
    app.next_agent_id += 1;
    let new_agent = build_fork_placeholder(app, new_id, parent_id, &parent_cwd, worktree);
    let parent_marker = match directive.as_deref() {
        Some(d) => format!("Forked: {d}"),
        None => "Forked".to_string(),
    };
    let parent_chat_kind = parent.chat_kind || app.chat_mode;
    let parent_conversation_entry = parent.conversation_entry;
    app.agents.insert(new_id, new_agent);
    {
        let agent = app
            .agents
            .get_mut(&new_id)
            .expect("just-inserted agent missing");
        agent.prompt.set_compact(app.appearance.prompt.compact);
        agent.prompt.adopt_slash_mru(app.slash_mru.clone());
        agent.prompt.adopt_command_tags(app.command_tags.clone());
        agent
            .prompt
            .set_contextual_hints(app.contextual_hints.undo, app.contextual_hints.plan_mode);
        agent.set_session_recap_available(app.session_recap_available);
        agent.set_voice_mode_available(app.voice_mode_enabled);
        agent.apply_app_scoped_gates(
            app.sharing_enabled,
            app.usage_visible,
            !app.has_external_auth_provider,
            app.chat_mode,
            app.screen_mode,
            &app.active_announcements,
            &app.tier_restricted_commands,
        );
        agent.chat_kind = parent_chat_kind;
        agent.conversation_entry = parent_conversation_entry;
        agent.apply_credit_balance(app.credit_balance.clone(), app.auto_topup.clone());
        agent
            .prompt
            .slash_controller
            .registry_mut()
            .set_plugins_visible(!app.appearance.disable_plugins);
        agent.pending_fork_banner = Some(crate::app::agent_view::PendingForkBanner {
            parent_sid: parent_session_id.0.to_string(),
            worktree,
        });
        if worktree {
            agent
                .scrollback
                .push_block(RenderBlock::system("Creating worktree\u{2026}".to_string()));
        }
        agent.pending_first_prompt = directive;
    }
    if let Some(parent_mut) = app.agents.get_mut(&parent_id) {
        parent_mut
            .scrollback
            .push_block(RenderBlock::system(parent_marker));
    }
    switch_to_agent(app, new_id, SwitchCause::Fork);
    if worktree {
        vec![Effect::CreateWorktreeSession {
            agent_id: new_id,
            load_session_id: Some(parent_session_id.0.to_string()),
            label: None,
            git_ref: None,
            // Fork resumes the parent session, which carries its own model.
            model_id: None,
            permission_mode_override: None,
            preferred_session_id: None,
            chat_kind: parent_chat_kind,
        }]
    } else {
        vec![Effect::ForkSession {
            agent_id: new_id,
        }]
    }
}
/// Build the placeholder [`AgentView`] for a fork. Centralises the
/// `AgentSession`/spinner construction shared by both worktree and
/// no-worktree branches so the parallel struct literal does not drift.
fn build_fork_placeholder(
    app: &AppView,
    new_id: AgentId,
    parent_id: AgentId,
    parent_cwd: &std::path::Path,
    worktree: bool,
) -> AgentView {
    let mut scrollback = ScrollbackState::new();
    scrollback.set_appearance(app.appearance.clone());
    let mut agent = AgentView::new(
        AgentSession {
            id: new_id,
            acp_tx: app.acp_tx.clone(),
            session_id: None,
            models: app.models.clone(),
            state: AgentState::Idle,
            tracker: AcpUpdateTracker::new(),
            cwd: parent_cwd.to_path_buf(),
            is_worktree: false,
            forked_from: Some(parent_id),
            pending_prompts: std::collections::VecDeque::new(),
            next_queue_id: 0,
            yolo_mode: app.default_yolo,
            auto_mode: inherit_auto_mode(app),
            prompt_history: Vec::new(),
            prompt_history_loading: false,
            loading_replay: false,
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
    let cmd = if worktree {
        AgentCommand::CreateWorktree
    } else {
        AgentCommand::ForkSession
    };
    agent.session.start_command(cmd);
    agent.turn_started_at = Some(Instant::now());
    agent
}
/// Build the discoverability banner for the child agent. Includes the
/// child's session id, the full parent session id, and — when
/// `switch_hint` names a command (the caller passes `/resume`) —
/// a session-switch tip so the user knows how to switch back. No-worktree
/// case appends the dim continuation `(both agents share cwd)`.
///
/// Called in `TaskResult::SessionLoaded` (not at dispatch time) because
/// the child's session id is not known until the backend responds.
pub(in crate::app::dispatch) fn build_child_fork_marker(
    session_id: &str,
    parent_sid: &str,
    worktree: bool,
    switch_hint: Option<&str>,
) -> String {
    let header = if let Some(cmd) = switch_hint {
        format!(
            "Session {session_id} (forked from {parent_sid}), use {cmd} to switch between sessions",
        )
    } else {
        format!("Session {session_id} (forked from {parent_sid})")
    };
    if worktree {
        header
    } else {
        format!("{header}\n  (both agents share cwd)")
    }
}
pub(in crate::app::dispatch) fn dispatch_startup_fork_session(
    app: &mut AppView,
    parent_session_id: String,
    parent_cwd: Option<std::path::PathBuf>,
    new_session_id: Option<String>,
) -> Vec<Effect> {
    if !app.session_startup_allowed() {
        app.deferred_startup.session =
            Some(crate::app::session_startup::DeferredSessionStartup::Fork {
                parent_session_id,
                parent_cwd,
                new_session_id,
            });
        return vec![];
    }
    let (_agent_id, mut effects) = dispatch_new_session_inner_with_id(app, None);
    let agent_id = app
        .agents
        .keys()
        .next_back()
        .copied()
        .expect("fork placeholder agent");
    effects.retain(|e| !matches!(e, Effect::CreateSession { .. }));
    let cwd = parent_cwd.unwrap_or_else(|| app.cwd.clone());
    let parent_is_worktree =
        crate::app::session_startup::parent_session_is_worktree(&parent_session_id, &cwd);
    effects.push(Effect::ForkSession {
        agent_id,
    });
    effects
}
pub(in crate::app::dispatch) fn handle_fork_session_failed(
    app: &mut AppView,
    agent_id: AgentId,
    error: String,
) -> Vec<Effect> {
    tracing::error!(agent = ?agent_id, error = %error, "Fork session failed");
    if let Some(agent) = app.agents.get_mut(&agent_id) {
        agent.pending_extensions_fetch = false;
        agent.session.finish_command();
        let elapsed = agent.turn_elapsed();
        agent.mark_turn_finished(TurnEnd::Aborted);
        agent.pending_first_prompt = None;
        agent.pending_fork_banner = None;
        agent
            .scrollback
            .push_block(RenderBlock::session_event(SessionEvent::TurnFailed {
                error,
                elapsed,
            }));
    }
    vec![]
}
