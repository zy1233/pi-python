//! Tests for session loading, restore, pickers, and deep search.
use super::*;
#[test]
fn session_loaded_with_restore_shows_summary_in_scrollback() {
    let mut app = test_app();
    dispatch(
        Action::LoadSession("sess-restore".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-restore"),
            models: None,
            code_restored: true,
            restore_summary: Some(
                "checked out abc12345, staged: true, unstaged: false, untracked: 3".into(),
            ),
            restore_degree: Some(pi_workspace::session::git::RestoreDegree::Full),
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::RegisterActiveSession { .. }))
    );
    let has_restore_msg = app.agents[&id]
        .scrollback
        .entries_in_range(0..app.agents[&id].scrollback.len())
        .iter()
        .any(|e| matches!(&e.block, RenderBlock::System(s) if s.text.contains("Code restored")));
    assert!(has_restore_msg, "expected restore summary in scrollback");
    assert_eq!(
        app.agents[&id].session.restore_degree,
        Some(pi_workspace::session::git::RestoreDegree::Full),
        "SessionLoaded must store restore_degree on the session"
    );
}
/// A resumed session whose replay left entries marked running (bg tasks,
/// scheduler runs, tools cut off when the previous process died) must
/// sweep them once the load lands without a live turn to adopt — a stuck
/// running entry otherwise holds `needs_animation()` open forever
/// (permanent ~30fps tick+redraw loop on an idle TUI).
#[test]
fn session_loaded_without_adoption_finishes_replayed_running_entries() {
    let mut app = test_app();
    dispatch(
        Action::LoadSession("sess-stuck".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .scrollback
            .push_block(RenderBlock::tool_call("Run something", "info", true));
        agent.scrollback.set_last_running(true);
        assert!(agent.scrollback.needs_animation());
    }
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-stuck"),
            models: None,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    let agent = app.agents.get(&id).unwrap();
    assert!(
        !agent.scrollback.has_running_entries(),
        "replayed running entries must be finished when no turn is adopted"
    );
}
/// Resume into a cwd with `.git/grok-worktree-source` sets `session.is_worktree`.
#[test]
fn load_session_marks_standalone_worktree_cwd() {
    let mut app = test_app();
    let main = crate::test_util::TempGitRepo::init("main-only");
    let clone = main.standalone_clone("wt-branch");
    dispatch(
        Action::LoadSession("sess-wt".into(), Some(clone.path.clone()), false),
        &mut app,
    );
    assert!(
        app.agents[&AgentId(0)].session.is_worktree,
        "resume into a standalone grok worktree must set session.is_worktree"
    );
    assert_eq!(app.agents[&AgentId(0)].session.cwd, clone.path);
}
#[test]
fn load_session_plain_repo_is_not_worktree() {
    let mut app = test_app();
    let repo = crate::test_util::TempGitRepo::init("main");
    dispatch(
        Action::LoadSession("sess-plain-git".into(), Some(repo.path.clone()), false),
        &mut app,
    );
    assert!(!app.agents[&AgentId(0)].session.is_worktree);
}
/// Cross-cwd resume anchors the agent cwd to the resolved origin cwd.
#[test]
fn load_session_anchors_agent_cwd_to_resolved_session_cwd() {
    let mut app = test_app();
    let process_cwd = app.cwd.clone();
    let origin_cwd = PathBuf::from("/some/other/origin-cwd");
    assert_ne!(origin_cwd, process_cwd, "test precondition");
    dispatch(
        Action::LoadSession("sess-xcwd".into(), Some(origin_cwd.clone()), false),
        &mut app,
    );
    assert_eq!(
        app.agents[&AgentId(0)].session.cwd,
        origin_cwd,
        "cross-cwd resume must anchor the agent cwd to the session's origin cwd"
    );
}
/// With no resolved cwd (`None`), the agent cwd stays the process cwd.
#[test]
fn load_session_falls_back_to_process_cwd_when_no_session_cwd() {
    let mut app = test_app();
    let process_cwd = app.cwd.clone();
    dispatch(
        Action::LoadSession("sess-samecwd".into(), None, false),
        &mut app,
    );
    assert_eq!(
        app.agents[&AgentId(0)].session.cwd,
        process_cwd,
        "same-cwd resume must keep the agent cwd at the process cwd"
    );
}
/// A stale fresh-view load resolving inside an open reconnect reload
/// window must not close the window: flipping `loading_replay` would make
/// the replay gate drop the rest of the reconnect replay (a truncated
/// transcript reported as a successful restore).
/// The original purge site: completing a session load (no reload
/// window open) drops the replay transient and must purge exactly once.
#[test]
fn session_loaded_purges_replay_transient() {
    use crate::memory_release::test_support;
    test_support::install_counting_hook();
    let mut app = test_app();
    dispatch(
        Action::LoadSession("sess-purge".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    let before = test_support::calls();
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-purge"),
            models: None,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert_eq!(
        test_support::calls(),
        before + 1,
        "load completion must purge the dropped replay transient exactly once"
    );
}
/// SessionLoaded path also surfaces a warning banner when the server
/// reported `code_restored: false` with a summary.
#[test]
fn session_loaded_with_restore_failure_shows_warning_banner() {
    let mut app = test_app();
    dispatch(
        Action::LoadSession("sess-fail".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-fail"),
            models: None,
            code_restored: false,
            restore_summary: Some(
                "restore aborted (checkout failed); stash skipped: MERGE_HEAD present".into(),
            ),
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    let entries = app.agents[&id]
        .scrollback
        .entries_in_range(0..app.agents[&id].scrollback.len());
    let warn = entries.iter().find_map(|e| match &e.block {
        RenderBlock::System(s) if s.text.contains("Code restore failed") => Some(&s.text),
        _ => None,
    });
    let text = warn.expect("warning banner missing").as_str();
    assert!(
        text.starts_with('\u{26A0}'),
        "expected ⚠ prefix, got: {text}"
    );
    assert!(text.contains("MERGE_HEAD present"));
    assert!(
        !entries.iter().any(|e| matches!(
            &e.block,
            RenderBlock::System(s) if s.text.contains("Code restored")
        )),
        "success banner must not appear on failure"
    );
}
#[test]
fn session_loaded_without_restore_no_summary() {
    let mut app = test_app();
    dispatch(
        Action::LoadSession("sess-plain".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-plain"),
            models: None,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::RegisterActiveSession { .. }))
    );
    let has_restore_msg = app.agents[&id]
        .scrollback
        .entries_in_range(0..app.agents[&id].scrollback.len())
        .iter()
        .any(|e| matches!(&e.block, RenderBlock::System(s) if s.text.contains("Code restored")));
    assert!(!has_restore_msg, "should not have restore summary");
}
/// A second `SessionLoaded` without a restore must reset
/// `restore_degree` to `None`, not keep a stale `Some(Full)` from a
/// previous load.
#[test]
fn session_loaded_without_restore_resets_restore_degree() {
    let mut app = test_app();
    dispatch(Action::LoadSession("sess-r2".into(), None, false), &mut app);
    let id = AgentId(0);
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-r2"),
            models: None,
            code_restored: true,
            restore_summary: Some("checked out abc".into()),
            restore_degree: Some(pi_workspace::session::git::RestoreDegree::Full),
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert_eq!(
        app.agents[&id].session.restore_degree,
        Some(pi_workspace::session::git::RestoreDegree::Full)
    );
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-r2"),
            models: None,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert!(
        app.agents[&id].session.restore_degree.is_none(),
        "second load without restore must clear stale degree"
    );
}
#[test]
fn load_session_seeds_available_commands_from_bootstrap() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![acp::AvailableCommand::new(
        "session-info".to_string(),
        "Show session info".to_string(),
    )];
    dispatch(
        Action::LoadSession("sess-123".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    assert_eq!(app.agents[&id].session.available_commands.len(), 1);
    assert_eq!(
        app.agents[&id].session.available_commands[0].name,
        "session-info"
    );
    assert_eq!(app.agents[&id].session.available_commands_generation, 1);
}
/// Known session id resume always emits LoadSession, never CreateSession.
#[test]
fn resume_known_session_id_loads_not_creates() {
    let mut app = test_app();
    let effects = dispatch(
        Action::LoadSession("resume-known-id".into(), None, false),
        &mut app,
    );
    assert!(
            effects
                .iter()
                .any(|e| matches!(e, Effect::LoadSession { session_id, .. } if session_id == "resume-known-id")),
            "expected LoadSession, got {effects:?}"
        );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::CreateSession { .. })),
        "resume must never CreateSession"
    );
}
/// Completing a mid-session login restores the agent view instead of
/// running the startup load-session flow.
#[test]
fn auth_complete_restores_view_after_mid_session_login() {
    let mut app = test_app_with_agent();
    dispatch(Action::Login, &mut app);
    let seq = authenticating_seq(&app);
    assert_eq!(app.active_view, ActiveView::Welcome);
    dispatch(
        Action::TaskComplete(TaskResult::AuthComplete {
            request_seq: seq,
            meta: None,
        }),
        &mut app,
    );
    assert_eq!(app.active_view, ActiveView::Agent(AgentId(0)));
    assert_eq!(app.auth_return_view, None);
    assert!(matches!(app.auth_state, AuthState::Done));
}
#[test]
fn session_loaded_does_not_enqueue_prompts() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let queue_before = app.agents[&id].session.pending_prompts.len();
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: "test-session".into(),
            models: None,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert_eq!(
        app.agents[&id].session.pending_prompts.len(),
        queue_before,
        "loading a session must not enqueue a prompt"
    );
}
#[test]
fn reanchor_grouped_selection_lands_on_a_row() {
    use crate::views::picker::PickerState;
    let map: Vec<Option<()>> = vec![None, Some(()), Some(())];
    let mut st = PickerState::default();
    st.selected = 9;
    reanchor_grouped_selection(&mut st, &map);
    assert_eq!(st.selected, 2);
    let mut st = PickerState::default();
    reanchor_grouped_selection(&mut st, &map);
    assert_eq!(st.selected, 1);
    let empty: Vec<Option<()>> = vec![];
    let mut st = PickerState::default();
    st.selected = 5;
    reanchor_grouped_selection(&mut st, &empty);
    assert_eq!(st.selected, 0);
}
#[test]
fn entry_title_loading_when_no_session_id() {
    use crate::views::session_title::entry_title;
    let mut app = test_app_with_agent();
    if let Some(a) = app.agents.get_mut(&AgentId(0)) {
        a.session.session_id = None;
    }
    let title = entry_title(&app.agents[&AgentId(0)]);
    assert_eq!(title, "loading...");
}
/// Regression: SessionLoaded must clear stale running entries from replay.
/// Without the finish_turn call, Execute blocks that were InProgress when the
/// session was last active stay orphaned as "running" forever.
#[test]
fn session_loaded_clears_stale_running_entries() {
    use crate::acp::meta::NotificationMeta;
    use std::sync::Arc;
    let mut app = test_app();
    dispatch(
        Action::LoadSession("sess-stale".into(), None, false),
        &mut app,
    );
    let id = AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    let meta = NotificationMeta::default();
    agent.session.handle_update(
        acp::SessionUpdate::ToolCall(
            acp::ToolCall::new(
                acp::ToolCallId::new(Arc::from("tc-stale")),
                "Execute `sleep 999`".to_string(),
            )
            .kind(acp::ToolKind::Execute)
            .status(acp::ToolCallStatus::Pending)
            .content(vec![])
            .locations(vec![]),
        ),
        &meta,
        &mut agent.scrollback,
    );
    agent.session.handle_update(
        acp::SessionUpdate::ToolCallUpdate(acp::ToolCallUpdate::new(
            acp::ToolCallId::new(Arc::from("tc-stale")),
            acp::ToolCallUpdateFields::new().status(Some(acp::ToolCallStatus::InProgress)),
        )),
        &meta,
        &mut agent.scrollback,
    );
    assert!(!agent.scrollback.is_empty());
    assert!(
        agent.scrollback.needs_animation(),
        "scrollback should have running entries before SessionLoaded",
    );
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoaded {
            agent_id: id,
            session_id: acp::SessionId::new("sess-stale"),
            models: None,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
            running_prompt_id: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert!(
        !app.agents[&id].scrollback.needs_animation(),
        "no entries should be animating after SessionLoaded",
    );
}
#[test]
fn resume_focuses_existing_agent_for_open_session() {
    let mut app = test_app();
    dispatch(Action::NewSession, &mut app);
    let agent_0 = AgentId(0);
    dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: agent_0,
            session_id: "wt-sess-1".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    dispatch(Action::NewSession, &mut app);
    let agent_1 = AgentId(1);
    dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: agent_1,
            session_id: "new-sess-2".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    let count_before = app.agents.len();
    let effects = dispatch(
        Action::LoadSession("wt-sess-1".into(), None, false),
        &mut app,
    );
    assert!(matches!(app.active_view, ActiveView::Agent(id) if id == agent_0));
    assert_eq!(app.agents.len(), count_before);
    assert!(effects.is_empty());
    assert_eq!(
        app.agents[&agent_0].session.session_id,
        Some(acp::SessionId::new("wt-sess-1"))
    );
    assert_eq!(
        app.agents[&agent_1].session.session_id,
        Some(acp::SessionId::new("new-sess-2"))
    );
}
#[test]
fn resume_unknown_session_still_creates_new_agent() {
    let mut app = test_app();
    dispatch(Action::NewSession, &mut app);
    dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: AgentId(0),
            session_id: "sess-aaa".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    let effects = dispatch(
        Action::LoadSession("sess-never-open".into(), None, false),
        &mut app,
    );
    let new_id = AgentId(1);
    assert!(matches!(app.active_view, ActiveView::Agent(id) if id == new_id));
    assert_eq!(app.agents.len(), 2);
    assert!(effects.iter().any(|e| matches!(
        e,
        Effect::LoadSession {
            agent_id,
            session_id,
            ..
        } if *agent_id == new_id && session_id == "sess-never-open"
    )));
}
/// Conversation resume must not focus a Build agent that shares the same id.
#[test]
fn resume_conversation_does_not_focus_build_id_collision() {
    let mut app = test_app();
    dispatch(Action::NewSession, &mut app);
    let agent_0 = AgentId(0);
    dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: agent_0,
            session_id: "shared-id".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    assert!(!app.agents[&agent_0].chat_kind);
    let count_before = app.agents.len();
    let effects = dispatch(
        Action::LoadSession("shared-id".into(), None, true),
        &mut app,
    );
    assert_eq!(app.agents.len(), count_before + 1);
    assert!(effects.iter().any(|e| matches!(
        e,
        Effect::LoadSession {
            session_id,
            chat_kind: true,
            ..
        } if session_id == "shared-id"
    )));
    assert!(!app.agents[&agent_0].chat_kind);
}
/// Under sticky `--chat`, agents stamp `chat_kind=true` even for build loads;
/// resume with conversation-entry false must still focus the open agent.
#[test]
fn resume_under_chat_mode_focuses_despite_entry_false() {
    let mut app = test_app();
    app.chat_mode = true;
    dispatch(Action::NewSession, &mut app);
    let agent_0 = AgentId(0);
    dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: agent_0,
            session_id: "chat-mode-sess".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    app.agents.get_mut(&agent_0).unwrap().chat_kind = true;
    dispatch(Action::NewSession, &mut app);
    let agent_1 = AgentId(1);
    dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: agent_1,
            session_id: "other".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );
    app.agents.get_mut(&agent_1).unwrap().chat_kind = true;
    let count_before = app.agents.len();
    let effects = dispatch(
        Action::LoadSession("chat-mode-sess".into(), None, false),
        &mut app,
    );
    assert!(effects.is_empty());
    assert_eq!(app.agents.len(), count_before);
    assert!(matches!(app.active_view, ActiveView::Agent(id) if id == agent_0));
}
/// After SessionLoadFailed, retrying resume must reissue LoadSession.
#[test]
fn resume_after_load_failed_reissues_load() {
    let mut app = test_app();
    let effects = dispatch(
        Action::LoadSession("fail-then-retry".into(), None, false),
        &mut app,
    );
    let agent_0 = AgentId(0);
    assert!(effects.iter().any(|e| matches!(
        e,
        Effect::LoadSession { agent_id, .. } if *agent_id == agent_0
    )));
    assert!(app.agents[&agent_0].loading_placeholder_id.is_some());
    dispatch(
        Action::TaskComplete(TaskResult::SessionLoadFailed {
            agent_id: agent_0,
            session_id: acp::SessionId::new("fail-then-retry"),
            error: "transient".into(),
        }),
        &mut app,
    );
    assert!(!app.agents[&agent_0].session.loading_replay);
    assert!(app.agents[&agent_0].loading_placeholder_id.is_some());
    let count_before = app.agents.len();
    let effects = dispatch(
        Action::LoadSession("fail-then-retry".into(), None, false),
        &mut app,
    );
    assert!(
        effects.iter().any(|e| matches!(
            e,
            Effect::LoadSession {
                agent_id,
                session_id,
                ..
            } if *agent_id != agent_0 && session_id == "fail-then-retry"
        )),
        "retry after failure must emit LoadSession for a new agent, got {effects:?}"
    );
    assert_eq!(app.agents.len(), count_before + 1);
}
#[test]
fn minimal_new_session_queues_welcome_card() {
    let mut app = test_app();
    app.screen_mode = crate::app::ScreenMode::Minimal;
    let _ = dispatch(Action::NewSession, &mut app);
    assert!(
        app.minimal_state.welcome_pending,
        "a fresh minimal session should queue the welcome card"
    );
}
#[test]
fn non_minimal_new_session_does_not_queue_welcome_card() {
    let mut app = test_app();
    app.screen_mode = crate::app::ScreenMode::Inline;
    let _ = dispatch(Action::NewSession, &mut app);
    assert!(
        !app.minimal_state.welcome_pending,
        "the welcome card is minimal-only"
    );
}
/// Welcome-screen variant of the conversation-row pick.
#[test]
fn pick_conversation_row_from_welcome_dispatches_direct_chat_load() {
    let mut app = test_app();
    app.session_picker_entries = Some(vec![make_conversation_entry("conv-pick-2")]);
    let effects = dispatch(Action::PickSession(0), &mut app);
    assert!(
        matches!(
            &effects[..],
            [Effect::LoadSession {
                session_id,
                session_cwd: None,
                chat_kind: true,
                ..
            }] if session_id == "conv-pick-2"
        ),
        "expected a direct chat LoadSession, got {effects:?}"
    );
}
/// Pins Esc-during-load: with the previous entries still visible and a
/// refetch in flight, Esc must really dismiss the picker — drop the loading
/// flag (a lingering flag holds `show_picker` in a spinner limbo that ignores
/// input) and stale the fetch so its late response cannot resurrect the
/// picker.
#[test]
fn build_welcome_esc_during_load_dismisses_without_resurrection() {
    use crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};
    let mut app = test_app();
    assert!(!app.chat_mode);
    let _ = dispatch(Action::FetchSessionList, &mut app);
    let seq = app.session_picker_list_seq;
    assert!(app.session_picker_loading);
    app.session_picker_entries = Some(vec![make_picker_entry("previous-1", "/repo")]);
    let esc = Event::Key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    let out = app.handle_input(&esc);
    assert!(
        matches!(
            out,
            crate::app::app_view::InputOutcome::Action(Action::SessionPickerClosed)
        ),
        "welcome Esc must surface SessionPickerClosed, got {out:?}"
    );
    assert!(
        app.session_picker_entries.is_none(),
        "Esc clears the welcome picker"
    );
    let _ = dispatch(Action::SessionPickerClosed, &mut app);
    assert!(
        !app.session_picker_loading,
        "dismissal must end the loading limbo (`show_picker` keys off it)"
    );
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("native-late", "/repo")],
            seq,
        }),
        &mut app,
    );
    assert!(
        app.session_picker_entries.is_none(),
        "late native response must not resurrect the closed picker"
    );
}
/// The spinner-only loading picker (nothing landed yet) still owns Esc: it
/// must dismiss the picker instead of dead-keying into the menu it covers.
#[test]
fn build_welcome_esc_dismisses_spinner_only_loading_picker() {
    use crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};
    let mut app = test_app();
    let _ = dispatch(Action::FetchSessionList, &mut app);
    assert!(app.session_picker_loading);
    assert!(app.session_picker_entries.is_none());
    let esc = Event::Key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    let out = app.handle_input(&esc);
    assert!(
        matches!(
            out,
            crate::app::app_view::InputOutcome::Action(Action::SessionPickerClosed)
        ),
        "Esc on the loading picker must close it, got {out:?}"
    );
    let _ = dispatch(Action::SessionPickerClosed, &mut app);
    assert!(!app.session_picker_loading, "picker fully dismissed");
}
/// The welcome picker fuzzy-filters by the typed query: Enter picks the first
/// visible row, and a query that matches no title picks nothing.
#[test]
fn welcome_picker_enter_picks_only_entries_matching_the_query() {
    use crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};
    let enter = Event::Key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    let mut entry = make_picker_entry("sess-w1", "/r");
    entry.summary = "Quarterly roadmap notes".into();

    let mut app = test_app();
    app.session_picker_entries = Some(vec![entry.clone()]);
    app.session_picker_state.set_query("road");
    app.session_picker_state.selected = 0;
    let out = app.handle_input(&enter);
    assert!(
        matches!(
            out,
            crate::app::app_view::InputOutcome::Action(Action::PickSession(0))
        ),
        "a title matching the query must be pickable, got {out:?}"
    );

    let mut app = test_app();
    app.session_picker_entries = Some(vec![entry]);
    app.session_picker_state.set_query("zzz");
    app.session_picker_state.selected = 0;
    let out = app.handle_input(&enter);
    assert!(
        !matches!(
            out,
            crate::app::app_view::InputOutcome::Action(Action::PickSession(_))
        ),
        "entries not matching the query must not be pickable, got {out:?}"
    );
}
/// Plain picker fetches never bump the list seq, so two rapid picker opens
/// keep last-write-wins behavior — BOTH responses land in arrival order
/// instead of the superseded one being dropped as stale.
#[test]
fn build_mode_rapid_plain_fetches_keep_last_write_wins() {
    let mut app = test_app();
    assert!(!app.chat_mode);
    let first = dispatch(Action::FetchSessionList, &mut app);
    let second = dispatch(Action::FetchSessionList, &mut app);
    for effects in [&first, &second] {
        assert!(
            matches!(&effects[..], [Effect::FetchSessionList { seq: 0 }]),
            "a plain fetch carries the current seq, got {effects:?}"
        );
    }
    assert_eq!(
        app.session_picker_list_seq, 0,
        "Build mode never bumps the list seq"
    );
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("build-first", "/r")],
            seq: 0,
        }),
        &mut app,
    );
    assert_eq!(
        app.session_picker_entries
            .as_ref()
            .map(|e| e[0].id.as_str()),
        Some("build-first"),
        "superseded plain response must land (pre-existing behavior)"
    );
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("build-second", "/r")],
            seq: 0,
        }),
        &mut app,
    );
    assert_eq!(
        app.session_picker_entries
            .as_ref()
            .map(|e| e[0].id.as_str()),
        Some("build-second"),
        "later plain response wins (pre-existing behavior)"
    );
}
