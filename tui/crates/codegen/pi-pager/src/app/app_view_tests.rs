use super::*;
use crate::acp::model_state::ModelState;
use crate::acp::tracker::AcpUpdateTracker;
use crate::app::agent::{AgentSession, AgentState};
use crate::app::agent_view::AgentView;
use crate::app::bundle::BundleState;
use crate::scrollback::state::ScrollbackState;
use crossterm::event::{
    Event, KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
#[test]
fn welcome_show_toast_scrubs_control_chars() {
    let mut app = test_app();
    assert!(matches!(app.active_view, ActiveView::Welcome));
    app.show_toast("a\nb\rc\thttps://example.com");
    let toast = app
        .welcome_toast
        .as_ref()
        .map(|(m, _)| m.as_str())
        .unwrap_or("");
    assert!(
        !toast.chars().any(|c| c.is_control()),
        "control chars must be scrubbed at write: {toast:?}"
    );
    assert!(toast.contains("https://example.com"), "{toast:?}");
}
#[test]
fn parse_esc_ttl_bounds() {
    let default = PendingAction::ESC_DOUBLE_PRESS_TTL;
    assert_eq!(parse_esc_ttl(None), default);
    assert_eq!(parse_esc_ttl(Some("garbage".into())), default);
    assert_eq!(parse_esc_ttl(Some("".into())), default);
    assert_eq!(parse_esc_ttl(Some("0".into())), default);
    assert_eq!(parse_esc_ttl(Some("-5".into())), default);
    assert_eq!(
        parse_esc_ttl(Some(" 1200 ".into())),
        Duration::from_millis(1200)
    );
    assert_eq!(
        parse_esc_ttl(Some(ESC_DOUBLE_PRESS_TEST_MS.to_string())),
        Duration::from_millis(ESC_DOUBLE_PRESS_TEST_MS)
    );
    assert_eq!(
        parse_esc_ttl(Some(u64::MAX.to_string())),
        Duration::from_millis(ESC_DOUBLE_PRESS_TEST_MS)
    );
}
/// `AppView::draw` is the ONLY drain point for the process-wide deferred
/// release flag; if the wrapper loses its `run_deferred_release()` call,
/// every draw/tick-path cliff (video scroll-off, takeover drain,
/// frame-set replacement) silently stops purging. Drives the real
/// `draw()` against a channel-backed terminal (no tty; same recipe as
/// pager-render's `draw_frame` tests). Serialized: process-wide flag.
#[test]
#[serial_test::serial(MEMORY_RELEASE_DEFER)]
fn app_draw_drains_deferred_release_after_flush() {
    use crate::memory_release::test_support;
    use ratatui::{TerminalOptions, Viewport};
    test_support::install_counting_hook();
    crate::memory_release::run_deferred_release();
    let (frame_tx, _frame_rx) = std::sync::mpsc::channel::<crate::render::draw::WriterPayload>();
    let writer =
        crate::render::draw::TermWriter::new(frame_tx, crate::render::draw::WriterSync::new())
            .expect("single test writer");
    let backend = ratatui::backend::CrosstermBackend::new(writer);
    let mut terminal = pi_ratatui_inline::Terminal::with_options(
        backend,
        TerminalOptions {
            viewport: Viewport::Fixed(ratatui::layout::Rect::new(0, 0, 80, 24)),
        },
    )
    .expect("channel-backed terminal requires no tty");
    let mut app = test_app();
    crate::memory_release::request_release_after_draw("unit-test-defer");
    let before = test_support::calls();
    app.draw(&mut terminal);
    assert_eq!(
        test_support::calls(),
        before + 1,
        "AppView::draw must drain the deferred release post-flush"
    );
    let before = test_support::calls();
    app.draw(&mut terminal);
    assert_eq!(
        test_support::calls(),
        before,
        "a draw without a pending request must not purge"
    );
}
pub(crate) fn test_app() -> AppView {
    let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
    AppView {
        pending_startup: None,
        active_view: ActiveView::Welcome,
        auth_return_view: None,
        agents: indexmap::IndexMap::new(),
        next_agent_id: 0,
        models: ModelState::default(),
        registry: ActionRegistry::defaults(),
        settings_registry: std::sync::Arc::new(crate::settings::SettingsRegistry::defaults()),
        current_ui: pi_shell::agent::config::UiConfig::default(),
        status_line: Default::default(),
        cwd: std::path::PathBuf::from("/tmp"),
        cwd_has_git_ancestor: false,
        acp_tx: tx,
        scratch: crate::scrollback::render::ScratchBuffer::new(),
        cursor: CursorState::new(),
        pending_action: None,
        exit_session_pending: None,
        scroll_state: MouseScrollState::default(),
        scroll_config: ScrollConfig::default(),
        appearance: AppearanceConfig::default(),
        notification_service: NotificationService::new(Default::default()),
        pending_notification_escapes: None,
        deferred_notification: None,
        tracing_rx: None,
        active_announcements: vec![],
        hidden_announcement_ids: Default::default(),
        announcements_last_gen: 0,
        announcement: None,
        tips: Vec::new(),
        tip: None,
        cli_model_override: None,
        cli_effort_token: None,
        default_yolo: false,
        permission_mode_from_soft_default: true,
        auto_mode_gate: true,
        yolo_policy_block: None,
        yolo_launch_block_notice: None,
        screen_mode_switch_hint: None,
        require_plan_approval: false,
        plan_mode: false,
        subagents: false,
        ask_user: false,
        chat_mode: false,
        #[cfg(feature = "local-workspace")]
        welcome_workspace_mode: crate::views::welcome::WelcomeWorkspaceMode::Sandbox,
        #[cfg(feature = "local-workspace")]
        local_workspace_startup_locked: false,
        #[cfg(feature = "local-workspace")]
        welcome_session_local_workspace: None,
        #[cfg(feature = "local-workspace")]
        welcome_local_workspace_ack_pending: false,
        #[cfg(feature = "local-workspace")]
        welcome_history_load_as_build: false,
        mouse_captured: true,
        new_worktree_dialog: None,
        contextual_hints: Default::default(),
        remote_contextual_hints: None,
        tip_seen_counts: Default::default(),
        last_known_terminal_rows: 0,
        small_screen_tip_evaluated: false,
        ssh_wrap_tip_evaluated: false,
        clipboard_focus_tip: Default::default(),
        new_session_worktree_mode: WorktreeMode::Never,
        fork_worktree_mode: WorktreeMode::Ask,
        restore_code: None,
        suppress_code_restore_once: None,
        resume_local_miss: None,
        agent_override: None,
        bootstrap_acp_commands: Vec::new(),
        auth_methods: Vec::new(),
        auth_state: AuthState::Done,
        trust_state: TrustState::Done,
        consent_state: crate::app::consent::ConsentState::Done,
        account_email: None,
        welcome_consent_link_rects: Vec::new(),
        welcome_consent_hover_link: None,
        consent_answered: None,
        login_label: None,
        login_method_id: None,
        auth_start_mode: AuthMode::Pending,
        auth_code_input: LineEditor::default(),
        next_auth_request_seq: 1,
        auth_url_poll_handle: None,
        deferred_startup: Default::default(),
        auth_use_oauth: false,
        auth_clipboard_delivery: None,
        auth_clipboard_feedback_generation: 0,
        team_id: None,
        team_name: None,
        is_zdr: false,
        team_role: None,
        coding_data_retention_opt_out: true,
        privacy_notice_rollout: false,
        privacy_banner_reshow_days: None,
        privacy_banner_acked: None,
        privacy_banner_opt_in_inflight: false,
        coding_data_write_seq: 0,
        show_tips: None,
        auto_update: None,
        ask_user_question_timeout_enabled: None,
        zdr_access_enabled: false,
        usage_billing_redirect_url: None,
        access_gate_shown_logged: false,
        announcement_cta_impressions_logged: Default::default(),
        gate: None,
        subscription_tier: None,
        paywall_check_started: None,
        last_subscription_check_at: None,
        subscription_watch_interval_secs: None,
        pending_gate_verification: None,
        gate_verify_gen: 0,
        bundle_state: BundleState::default(),
        scroll_debug_hud: crate::views::scroll_debug_hud::ScrollDebugHud::new(),
        fps_hud: crate::views::fps_hud::FpsHud::new(),
        welcome_prompt: crate::views::prompt_widget::PromptWidget::new(),
        slash_mru: std::rc::Rc::new(std::cell::RefCell::new(
            crate::slash::mru::SlashMru::new_in_memory(),
        )),
        command_tags: std::rc::Rc::new(std::cell::RefCell::new(std::collections::HashMap::new())),
        welcome_prompt_focused: false,
        welcome_tip_typing_dismissed: false,
        welcome_menu_index: None,
        welcome_menu_rects: Vec::new(),
        welcome_import_banner_rect: None,
        last_mouse_pos: None,
        last_scroll_pos: None,
        last_cache_evict_at: None,
        welcome_prompt_rect: None,
        welcome_auth_url_rect: None,
        welcome_on_auth_url: false,
        welcome_announcement: WelcomeAnnouncementState::default(),
        welcome_auth_fallback_rect: None,
        welcome_refresh_rect: None,
        welcome_gate_url_rect: None,
        welcome_upgrade_cta_rect: None,
        welcome_privacy_banner_opt_in_rect: None,
        welcome_privacy_banner_opt_out_rect: None,
        welcome_privacy_banner_terms_rect: None,
        welcome_privacy_banner_policy_rect: None,
        #[cfg(feature = "local-workspace")]
        welcome_workspace_mode_rects: Default::default(),
        #[cfg(feature = "local-workspace")]
        welcome_on_workspace_mode: false,
        welcome_toast: None,
        welcome_on_privacy_banner: false,
        welcome_on_upgrade_cta: false,
        auth_show_raw_url: false,
        native_select_hold: false,
        session_picker_entries: None,
        session_picker_loading: false,
        session_picker_state: crate::views::picker::PickerState::with_mode(
            crate::views::picker::PickerMode::FullScreen,
        ),
        session_picker_source_filter: crate::views::session_picker::SourceFilter::default(),
        session_picker_relaxed_notified_for: None,
        session_picker_content_results: None,
        session_picker_content_loading: false,
        session_picker_deep_search_seq: 0,
        session_picker_list_seq: 0,
        foreign_session_compat: Default::default(),
        foreign_session_scan_seq: 0,
        foreign_scan_coordinator: Default::default(),
        session_picker_lanes: Default::default(),
        session_picker_detail_generation: 0,
        session_picker_entries_query: None,
        session_picker_pending_delete: None,
        welcome_tick: 0,
        welcome_shimmer_frame: 0,
        startup_warnings: Vec::new(),
        is_api_key_auth: false,
        pending_update_version: None,
        foreign_resume_launch_generation: 0,
        foreign_resume_launch: None,
        quit_for_update: false,
        relaunch: None,
        has_claude_import: false,
        import_claude_modal: None,
        welcome_doc_viewer: None,
        screen_mode: ScreenMode::Inline,
        pending_screen_mode_switch: None,
        pending_effects: Vec::new(),
        pending_editor: None,
        pending_pager_path: None,
        pending_pager_ansi: false,
        minimal_state: crate::minimal_api::MinimalState::default(),
        show_resolved_model: true,
        sharing_enabled: false,
        usage_visible: true,
        has_external_auth_provider: false,
        tier_restricted_commands: Vec::new(),
        credit_balance: None,
        auto_topup: None,
        billing_poll_wanted: false,
        session_picker_grouped: false,
        scheduler_background_loops_seed: true,
        cancel_rewind_enabled: true,
        session_recap_available: false,
        shell_feedback_trace_offer: false,
        feedback_trace_choice_latched: false,
        feedback_trace_upload_pending: None,
        tutorial: None,
        keyboard_normalizer: KeyboardNormalizer::from_terminal_context(),
        voice_mode_enabled: false,
        voice_ui_active: false,
        voice_config: pi_voice::VoiceConfig::default(),
        voice_auth: None,
        voice_cmd_tx: None,
        voice_state: VoiceState::Idle,
    }
}
pub(crate) fn test_app_with_agent() -> AppView {
    let mut app = test_app();
    let id = super::super::agent::AgentId(0);
    let mut agent = AgentView::new(
        AgentSession {
            id,
            acp_tx: app.acp_tx.clone(),
            session_id: Some("test-session".into()),
            models: ModelState::default(),
            state: AgentState::Idle,
            tracker: AcpUpdateTracker::new(),
            cwd: std::path::PathBuf::from("/tmp"),
            is_worktree: false,
            forked_from: None,
            pending_prompts: std::collections::VecDeque::new(),
            next_queue_id: 0,
            yolo_mode: false,
            auto_mode: false,
            prompt_history: Vec::new(),
            prompt_history_loading: false,
            loading_replay: false,
            restore_degree: None,
            rate_limited: false,
            model_incompatible: false,
            credit_limit_blocked: false,
            free_usage_blocked: false,
            available_commands: Vec::new(),
            available_commands_generation: 0,
            available_tools: None,
            model_switch_pending: false,
            user_model_preference: None,
            deferred_model_switch: None,
            in_flight_prompt: None,
            compact_held_prompt: None,
            current_prompt_id: None,
            created_via_new: false,
        },
        ScrollbackState::new(),
    );
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    app.agents.insert(id, agent);
    super::super::dispatch::switch_to_agent(
        &mut app,
        id,
        super::super::dispatch::SwitchCause::Load,
    );
    app
}
/// With the image-input tip OFF, the poll short-circuits at the window gate
/// before touching the pasteboard — the per-tip gate fails closed.
#[test]
fn clipboard_poll_no_op_when_flag_off() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (80, 30);
    app.notification_service.focus_tracker.on_focus_gained();
    app.contextual_hints.image_input = false;
    assert!(!app.poll_clipboard_focus_tip(), "tip-off poll is a no-op");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}
/// The in-window gate decides whether an already-running iteration may touch
/// the pasteboard at all. It opens only when contextual hints are on, the
/// probe is supported (macOS), the fire cooldown is clear, the terminal is
/// focused, and the active agent is eligible; flipping any one closes it so
/// the poll reads the clipboard zero times. (Probe support is macOS-only, so
/// the in-window result tracks the platform.)
#[test]
fn clipboard_poll_window_gate() {
    let mut app = test_app_with_agent();
    app.contextual_hints.image_input = true;
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (80, 30);
    app.notification_service.focus_tracker.on_focus_gained();
    let now = std::time::Instant::now();
    let supported = crate::clipboard::clipboard_image_probe_supported();
    assert_eq!(
        app.clipboard_tip_in_poll_window(now),
        supported,
        "in window"
    );
    app.contextual_hints.image_input = false;
    assert!(!app.clipboard_tip_in_poll_window(now), "tip off");
    app.contextual_hints.image_input = true;
    app.notification_service.focus_tracker.on_focus_lost();
    assert!(!app.clipboard_tip_in_poll_window(now), "unfocused");
    app.notification_service.focus_tracker.on_focus_gained();
    let img = crate::prompt_images::from_clipboard_data(&crate::clipboard::ImageData {
        data: vec![1, 2, 3],
        mime_type: "image/png".into(),
    });
    app.agents.get_mut(&id).unwrap().prompt.images.push(img);
    assert!(!app.clipboard_tip_in_poll_window(now), "image attached");
    app.agents.get_mut(&id).unwrap().prompt.images.clear();
    let fired = crate::tips::clipboard_focus::CheckOutcome {
        change_count: Some(1),
        has_image: true,
    };
    app.clipboard_focus_tip.note_fired(&fired, now);
    assert!(!app.clipboard_tip_in_poll_window(now), "in cooldown");
}
/// A positive, deduped, un-cooled-down outcome on a drawable agent shows the
/// tip and commits the cooldown + changeCount dedup (same content won't
/// re-fire). Drives `apply_clipboard_probe` with a synthetic outcome so it
/// is independent of the real pasteboard.
#[test]
fn clipboard_probe_shows_and_commits_on_positive_outcome() {
    use crate::tips::clipboard_focus::CheckOutcome;
    let mut app = test_app_with_agent();
    app.contextual_hints.image_input = true;
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (80, 30);
    let now = std::time::Instant::now();
    let outcome = CheckOutcome {
        change_count: Some(7),
        has_image: true,
    };
    assert!(app.apply_clipboard_probe(outcome, now));
    assert!(app.agents[&id].ephemeral_tip.is_active());
    assert!(
        !app.clipboard_focus_tip.should_fire(&outcome, now),
        "fired content must commit the changeCount dedup"
    );
}
/// A refused show (here: the renderability gate on a short terminal) must
/// burn nothing — the same outcome stays fireable.
#[test]
fn clipboard_probe_refused_show_burns_nothing() {
    use crate::tips::clipboard_focus::CheckOutcome;
    let mut app = test_app_with_agent();
    app.contextual_hints.image_input = true;
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (80, 10);
    let now = std::time::Instant::now();
    let outcome = CheckOutcome {
        change_count: Some(7),
        has_image: true,
    };
    assert!(!app.apply_clipboard_probe(outcome, now));
    assert!(!app.agents[&id].ephemeral_tip.is_active());
    assert!(
        app.clipboard_focus_tip.should_fire(&outcome, now),
        "refused show must leave cooldown and dedup uncommitted"
    );
}
fn key_event(code: KeyCode, mods: KeyModifiers) -> Event {
    Event::Key(KeyEvent::new(code, mods))
}
/// Build a registry pinned to the non-VSCode bindings so tests are
/// deterministic regardless of the host terminal.
fn pin_non_vscode_registry(app: &mut AppView) {
    let mut actions = crate::actions::default_actions(ScreenMode::Fullscreen, false);
    for def in actions.iter_mut() {
        if def.id == ActionId::Quit {
            def.default_key = key!('q', CONTROL);
            def.alt_keys = vec![key!('d', CONTROL)];
        }
        if def.id == ActionId::HalfPageDown {
            def.default_key = key!('d', CONTROL);
        }
    }
    app.registry = ActionRegistry::new(actions);
}
fn ctrl_d() -> Event {
    key_event(KeyCode::Char('d'), KeyModifiers::CONTROL)
}
fn ctrl_q() -> Event {
    key_event(KeyCode::Char('q'), KeyModifiers::CONTROL)
}
fn ctrl_c() -> Event {
    key_event(KeyCode::Char('c'), KeyModifiers::CONTROL)
}
fn left_mouse(kind: MouseEventKind, column: u16, row: u16) -> Event {
    Event::Mouse(MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    })
}
#[test]
fn needs_animation_ignores_tracing_rx_outside_dev_builds() {
    let mut app = test_app_with_agent();
    let (_tx, rx) = tokio::sync::mpsc::channel::<String>(4);
    app.tracing_rx = Some(rx);
    assert!(
        !app.needs_animation(),
        "release builds must not request animation ticks just because \
         tracing_rx exists (always true after startup)"
    );
}
#[test]
fn needs_animation_gates_prompt_history_tick_delivery() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(
        !app.needs_animation(),
        "an idle agent with no history overlay must not request animation ticks"
    );
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.prompt_history = vec!["first prompt".into(), "second prompt".into()];
        let history = agent.combined_prompt_history();
        assert!(agent.prompt.history_search.activate(&history, ""));
    }
    assert!(
        app.needs_animation(),
        "an open prompt history overlay must request animation ticks"
    );
    let mut delivered = false;
    for _ in 0..1000 {
        if app.tick() && app.agents[&id].prompt.history_search.result_count() == 2 {
            delivered = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    assert!(
        delivered,
        "tick() must poll the history daemon and deliver results"
    );
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .history_search
        .deactivate();
    assert!(
        !app.needs_animation(),
        "closing the history overlay stops the animation ticks"
    );
}
#[test]
fn needs_animation_gates_scrollback_search_tick_delivery() {
    use crate::scrollback::ScrollbackSearchState;
    use crate::scrollback::block::RenderBlock;
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .scrollback
            .push_block(RenderBlock::user_prompt("foo bar"));
        agent
            .scrollback
            .push_block(RenderBlock::user_prompt("baz foo"));
        agent.scrollback.prepare_layout(80, 24);
    }
    assert!(
        !app.needs_animation(),
        "an idle agent with no search open must not request animation ticks"
    );
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.scrollback_search = Some(ScrollbackSearchState::open());
        let search = agent.scrollback_search.as_mut().unwrap();
        search.update_query("foo", &agent.scrollback);
        assert_eq!(
            search.current_index(),
            None,
            "matches are not computed synchronously on the input thread"
        );
    }
    assert!(
        app.needs_animation(),
        "an open scrollback search must request animation ticks"
    );
    let mut delivered = false;
    for _ in 0..1000 {
        app.tick();
        if app.agents[&id]
            .scrollback_search
            .as_ref()
            .unwrap()
            .current_index()
            == Some(0)
        {
            delivered = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    assert!(delivered, "tick() must poll the daemon and deliver results");
    assert_eq!(
        app.agents[&id]
            .scrollback_search
            .as_ref()
            .unwrap()
            .match_count(),
        2
    );
    app.agents.get_mut(&id).unwrap().scrollback_search = None;
    assert!(
        !app.needs_animation(),
        "closing the search stops the animation ticks"
    );
}
#[test]
fn tick_demand_fast_while_wake_turn_streams() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert_eq!(app.tick_demand(), TickDemand::None, "idle agent parks");
    app.agents
        .get_mut(&id)
        .unwrap()
        .note_streaming_wake_turn("p-wake");
    assert_eq!(
        app.tick_demand(),
        TickDemand::Fast,
        "wake chrome spinner must tick while the pane stays Idle"
    );
}
/// The welcome screen shimmer only advances ~12fps, so a resting welcome
/// screen must demand Slow ticks — not a 30fps loop; the deep-search
/// spinner upgrades it to Fast while loading.
#[test]
fn tick_demand_welcome_is_slow_unless_loading() {
    let mut app = test_app();
    assert_eq!(app.active_view, ActiveView::Welcome);
    assert_eq!(app.tick_demand(), TickDemand::Slow);
    assert!(app.needs_animation(), "slow still counts as animating");
    app.session_picker_content_loading = true;
    assert_eq!(app.tick_demand(), TickDemand::Fast);
}
/// An open modal session picker that is still fetching keeps fast ticks
/// alive on an otherwise-idle agent (its loading spinner must animate) —
/// including after the fast foreign scan lands rows the default Grok
/// filter hides; once the native list settles the demand parks again.
#[test]
fn tick_demand_fast_while_modal_session_picker_loads() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert_eq!(app.tick_demand(), TickDemand::None, "idle agent parks");
    app.agents.get_mut(&id).unwrap().active_modal =
        Some(crate::views::modal::ActiveModal::SessionPicker {
            state: crate::views::picker::PickerState::default(),
            entries: None,
            loading: true,
            lanes: Default::default(),
            previous_palette: None,
            window: crate::views::modal_window::ModalWindowState::new(),
            content_results: None,
            content_loading: false,
            deep_search_seq: 0,
            entries_query: None,
            source_filter: crate::views::session_picker::SourceFilter::default(),
            pending_delete: None,
        });
    assert_eq!(
        app.tick_demand(),
        TickDemand::Fast,
        "loading modal picker must keep the spinner animating"
    );
    let foreign_entry = SessionPickerEntry {
        id: "claude-1".into(),
        summary: "claude".into(),
        updated_at: chrono::Utc::now(),
        created_at: chrono::Utc::now(),
        cwd: String::new(),
        hostname: None,
        source: "claude".into(),
        model_id: None,
        num_messages: 0,
        last_active_at: None,
        branch: None,
        repo_name: "r".into(),
        worktree_label: None,
        last_turn_summary: None,
        last_recap: None,
        card_detail: None,
    };
    if let Some(crate::views::modal::ActiveModal::SessionPicker { entries, .. }) =
        app.agents.get_mut(&id).unwrap().active_modal.as_mut()
    {
        *entries = Some(vec![foreign_entry]);
    }
    assert_eq!(
        app.tick_demand(),
        TickDemand::Fast,
        "foreign rows hidden by the Grok filter must not end the loading spinner"
    );
    if let Some(crate::views::modal::ActiveModal::SessionPicker { loading, .. }) =
        app.agents.get_mut(&id).unwrap().active_modal.as_mut()
    {
        *loading = false;
    }
    assert_eq!(
        app.tick_demand(),
        TickDemand::None,
        "settled picker must not keep demanding ticks"
    );
}
/// An idle agent view demands no ticks at all; the macOS Cmd link-hover
/// poll (when it is the only pending work) demands Slow, never Fast.
#[test]
#[cfg(target_os = "macos")]
fn tick_demand_link_poll_is_slow_only() {
    use crate::render::osc8::{LinkOverlay, OverlayLink};
    use std::sync::Arc;
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert_eq!(app.tick_demand(), TickDemand::None, "idle agent parks");
    {
        let agent = app.agents.get_mut(&id).unwrap();
        let mut overlay = LinkOverlay::new();
        overlay.push(OverlayLink {
            screen_row: 2,
            col_start: 0,
            col_end: 10,
            target: crate::render::osc8::LinkTarget::Url(Arc::from("https://example.com")),
            presentation: crate::render::osc8::LinkPresentation::Opaque,
            id: Some(1),
        });
        agent.visible_link_map.rebuild(1, &overlay, vec![]);
        agent.hovered_entry = Some(0);
        agent.last_mouse_moved_at = Some(std::time::Instant::now());
    }
    if !crate::app::agent_view::has_native_link_hover() {
        assert_eq!(
            app.tick_demand(),
            TickDemand::Slow,
            "link poll alone must not spin the fast loop"
        );
    }
}
#[test]
fn needs_animation_gates_mode_switch_banner_countdown() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(!app.needs_animation(), "idle agent must not request ticks");
    app.agents
        .get_mut(&id)
        .unwrap()
        .show_mode_switch_banner("Plan");
    assert!(
        app.needs_animation(),
        "mode_switch_banner must request ticks (tick_mode_banner countdown)"
    );
    let mut cleared = false;
    for _ in 0..512 {
        app.tick();
        if app.agents[&id].mode_switch_banner.is_none() {
            cleared = true;
            break;
        }
    }
    assert!(
        cleared,
        "tick() must decrement mode_switch_banner until it expires"
    );
    assert!(
        !app.needs_animation(),
        "expired mode banner must stop requesting ticks"
    );
}
/// Draw-entry resync: an `expires_at` crossing between pushes must close
/// the `/announcements` gate on the next frame; a later live list re-opens
/// it through the same divergence check.
#[test]
fn slash_gate_resyncs_when_critical_expires_between_pushes() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents
        .get_mut(&id)
        .unwrap()
        .set_has_session_announcements(true);
    app.active_announcements = vec![pi_announcements::RemoteAnnouncement {
        id: Some("expired".into()),
        message: Some("gone".into()),
        severity: Some("critical".into()),
        expires_at: Some("2000-01-01T00:00:00Z".into()),
        ..Default::default()
    }];
    app.resync_announcement_slash_gate_on_divergence();
    assert!(
        !app.agents[&id]
            .prompt
            .slash_controller
            .has_session_announcements(),
        "expired-only list must close the gate on the next frame"
    );
    app.active_announcements = vec![pi_announcements::RemoteAnnouncement {
        id: Some("live".into()),
        message: Some("new outage".into()),
        severity: Some("critical".into()),
        ..Default::default()
    }];
    app.resync_announcement_slash_gate_on_divergence();
    assert!(
        app.agents[&id]
            .prompt
            .slash_controller
            .has_session_announcements(),
        "a live critical must re-open the gate"
    );
}
/// The word-select tip's long TTL is bounded by prompt divergence: ANY
/// prompt change since the tip was shown (typed here; the snapshot guard
/// covers paste/drop identically) refuses the chord immediately and
/// retires the tip on the next tick, so Ctrl+Y goes back to yank.
#[test]
fn word_select_tip_retires_on_prompt_divergence_and_accepts_before() {
    use std::collections::HashMap;
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.last_terminal_size = (80, 30);
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        let _ = agent.ephemeral_tip.show(
            crate::tips::word_select::word_select_tip(),
            &mut HashMap::new(),
        );
        agent.word_select_tip_prompt_snapshot = Some(agent.prompt.text().to_string());
    }
    let out = app.handle_input(&key_event(KeyCode::Char('y'), KeyModifiers::CONTROL));
    assert!(
        matches!(out, InputOutcome::Action(Action::AcceptWordSelectTip)),
        "Ctrl+Y with the tip up must route to accept, got {out:?}"
    );
    let _ = app.handle_input(&key_event(KeyCode::Char('a'), KeyModifiers::NONE));
    let out = app.handle_input(&key_event(KeyCode::Char('y'), KeyModifiers::CONTROL));
    assert!(
        !matches!(out, InputOutcome::Action(Action::AcceptWordSelectTip)),
        "Ctrl+Y after a prompt edit must not accept, got {out:?}"
    );
    app.tick();
    assert!(
        !app.agents[&id].ephemeral_tip.is_active(),
        "prompt divergence must retire the word-select tip on tick"
    );
    assert!(
        app.agents[&id].word_select_tip_prompt_snapshot.is_none(),
        "snapshot must drop with the tip"
    );
}
#[test]
fn needs_animation_gates_image_viewer_loading() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(!app.needs_animation());
    let viewer = crate::prompt_images::ImageViewerState::open_from_path_deferred(
        std::path::Path::new("/nonexistent/image_gate_test.png"),
    );
    assert!(viewer.loading, "deferred open must be in loading state");
    app.agents.get_mut(&id).unwrap().image_viewer = Some(viewer);
    assert!(
        app.needs_animation(),
        "image_viewer.loading must request ticks (poll/spawn load path)"
    );
    let mut terminal = false;
    for _ in 0..200 {
        app.tick();
        let agent = &app.agents[&id];
        if agent.image_viewer.is_none()
            || agent.toast.is_some()
            || agent.image_load_rx.is_some()
            || agent.image_viewer.as_ref().is_some_and(|v| !v.loading)
        {
            terminal = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(2));
    }
    assert!(
        terminal,
        "tick() must progress image load (spawn rx, fail toast, or clear loading)"
    );
    app.agents.get_mut(&id).unwrap().image_viewer = None;
    app.agents.get_mut(&id).unwrap().image_load_rx = None;
    app.agents.get_mut(&id).unwrap().toast = None;
    assert!(!app.needs_animation());
}
#[test]
fn needs_animation_gates_loading_replay() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(!app.needs_animation());
    app.agents.get_mut(&id).unwrap().session.loading_replay = true;
    assert!(
        app.needs_animation(),
        "loading_replay (attach/resume) must keep ticks alive"
    );
    let _ = app.tick();
    app.agents.get_mut(&id).unwrap().session.loading_replay = false;
    assert!(!app.needs_animation());
}
#[test]
fn active_scroll_stream_arms_scroll_clock_not_animation_ticks() {
    use crate::input::mouse::{ScrollConfig, ScrollDirection};
    let mut app = test_app_with_agent();
    assert!(!app.needs_animation());
    let _ = app
        .scroll_state
        .on_scroll_event(ScrollDirection::Up, ScrollConfig::default());
    assert!(
        app.scroll_state.has_active_stream(),
        "fixture: scroll event must arm an active stream"
    );
    assert!(
        !app.needs_animation(),
        "scroll streams must not demand animation ticks (scroll clock owns pacing)"
    );
    assert!(
        app.scroll_state
            .scroll_clock_deadline(std::time::Instant::now())
            .is_some(),
        "active stream must expose a scroll-clock deadline to the event loop"
    );
    let mut finalized = false;
    for _ in 0..200 {
        let _ = app.tick_scroll();
        if !app.scroll_state.has_active_stream() {
            finalized = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    assert!(
        finalized,
        "tick_scroll() must finalize the scroll stream without a metronome"
    );
    assert!(
        app.scroll_state
            .scroll_clock_deadline(std::time::Instant::now())
            .is_none(),
        "finalized stream must disarm the scroll clock (no idle wakeups)"
    );
    assert!(!app.needs_animation());
}
#[test]
fn handle_input_scroll_suppressed_events_do_not_report_changed() {
    let mut app = test_app_with_agent();
    let wheel = Event::Mouse(MouseEvent {
        kind: MouseEventKind::ScrollUp,
        column: 5,
        row: 5,
        modifiers: KeyModifiers::NONE,
    });
    std::thread::sleep(std::time::Duration::from_millis(20));
    const EVENTS: u32 = 30;
    let start = std::time::Instant::now();
    let mut changed = 0u32;
    for _ in 0..EVENTS {
        if matches!(app.handle_input(&wheel), InputOutcome::Changed) {
            changed += 1;
        }
        assert!(
            app.scroll_state.has_active_stream(),
            "wheel burst must keep the stream active"
        );
    }
    let elapsed_ms = start.elapsed().as_millis() as u32;
    assert!(
        changed >= 1,
        "a flushing wheel event must still report Changed"
    );
    let max_changed = elapsed_ms / 16 + 2;
    assert!(
        changed <= max_changed,
        "cadence-suppressed wheel events must not report Changed: got \
         {changed} Changed outcomes from {EVENTS} events in {elapsed_ms}ms \
         (bound {max_changed})"
    );
    assert!(
        app.scroll_state
            .scroll_clock_deadline(std::time::Instant::now())
            .is_some(),
        "armed stream must schedule a scroll-clock deadline"
    );
}
#[test]
fn tick_drains_tracing_rx_and_does_not_metronome_on_channel() {
    let mut app = test_app_with_agent();
    let (tx, rx) = tokio::sync::mpsc::channel::<String>(8);
    for i in 0..5 {
        tx.try_send(format!("trace line {i}"))
            .expect("queue tracer line");
    }
    app.tracing_rx = Some(rx);
    assert!(
        !app.needs_animation(),
        "non-dev: queued tracer lines must not request animation ticks"
    );
    let _ = app.tick();
    assert!(
        matches!(
            app.tracing_rx.as_mut().unwrap().try_recv(),
            Err(tokio::sync::mpsc::error::TryRecvError::Empty)
        ),
        "tick() must drain the tracer channel (bounded; cannot grow unbounded)"
    );
    assert!(
        !app.needs_animation(),
        "non-dev: a present-but-drained tracer channel must not request ticks"
    );
    drop(tx);
}
#[test]
fn needs_animation_gates_btw_loading_spinner() {
    use crate::views::btw_overlay::BtwOverlayState;
    use crate::views::turn_status::SPINNER_DIVISOR;
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(!app.needs_animation());
    app.agents.get_mut(&id).unwrap().btw_state = Some(BtwOverlayState::Loading {
        question: "what is X?".into(),
    });
    assert!(app.needs_animation());
    let saw_redraw = (0..SPINNER_DIVISOR).any(|_| app.tick());
    assert!(
        saw_redraw,
        "Loading must redraw at spinner cadence while idle"
    );
    app.agents.get_mut(&id).unwrap().btw_state =
        Some(BtwOverlayState::done("what is X?".into(), "X is …".into()));
    assert!(!app.needs_animation());
    app.agents.get_mut(&id).unwrap().btw_state = Some(BtwOverlayState::Error {
        question: "what is X?".into(),
        error: "boom".into(),
    });
    assert!(!app.needs_animation());
}
#[test]
fn needs_animation_gates_pending_acp_command_sync() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(
        !app.needs_animation(),
        "an idle, fully-synced agent must not request ticks"
    );
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .available_commands_generation += 1;
    assert!(
        app.agents[&id].acp_synced_generation
            != app.agents[&id].session.available_commands_generation,
        "fixture: a commands update must leave the catalog sync pending"
    );
    assert!(
        app.needs_animation(),
        "a pending ACP command-catalog sync must request animation ticks"
    );
    let _ = app.tick();
    assert_eq!(
        app.agents[&id].acp_synced_generation,
        app.agents[&id].session.available_commands_generation,
        "tick() must reconcile the slash-command catalog generation"
    );
    assert!(
        !app.needs_animation(),
        "a reconciled command catalog must stop requesting ticks"
    );
}
#[test]
fn needs_animation_gates_pending_turn_end_reconcile() {
    use super::super::dispatch::{TURN_END_RECONCILE_GRACE, reconcile_overdue_turn_ends};
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(app.agents[&id].session.state.is_idle());
    assert!(
        !app.needs_animation(),
        "an idle agent must not request ticks"
    );
    app.agents.get_mut(&id).unwrap().pending_turn_end_reconcile =
        Some(super::super::agent_view::PendingTurnEnd {
            prompt_id: "pid-stuck".into(),
            stop_reason: Some("end_turn".into()),
            agent_result: None,
            cancellation_category: None,
            received_at: std::time::Instant::now()
                - (TURN_END_RECONCILE_GRACE + std::time::Duration::from_secs(1)),
        });
    assert!(
        app.needs_animation(),
        "an armed turn-end reconcile must request ticks even for a background agent"
    );
    let _ = reconcile_overdue_turn_ends(&mut app);
    assert!(
        app.agents[&id].pending_turn_end_reconcile.is_none(),
        "reconcile must clear the overdue marker"
    );
    assert!(
        !app.needs_animation(),
        "a cleared reconcile marker must stop requesting ticks"
    );
}
#[test]
fn needs_animation_gates_pending_cancel_resend() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    assert!(!app.needs_animation());
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.running_wake_turn = Some(super::super::agent_view::RunningWakeTurn {
            prompt_id: "task-completed-bg1".into(),
            cancel_sent: true,
        });
        agent.pending_cancel_resend = Some(super::super::agent_view::PendingCancelResend {
            prompt_id: Some("task-completed-bg1".into()),
            sent_at: std::time::Instant::now(),
            attempts: 1,
            confirmed: false,
            trigger: crate::app::actions::CancelTrigger::Mouse,
        });
    }
    assert!(
        app.needs_animation(),
        "an armed cancel resend on a wake-cancelling pane must request ticks"
    );
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.running_wake_turn = None;
        agent.session.state = super::super::agent::AgentState::TurnCancelling;
    }
    assert!(
        app.needs_animation(),
        "an armed cancel resend on a cancelling pane must request ticks"
    );
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = super::super::agent::AgentState::Idle;
    }
    assert!(
        app.needs_animation(),
        "a stale resend record must keep ticking until reconcile drops it"
    );
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.pending_cancel_resend = None;
    }
    assert!(!app.needs_animation());
}
#[test]
fn gboom_backgrounded_game_drops_held_movement() {
    use crate::gboom::GboomState;
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let mut game = GboomState::new();
    game.handle_key(&KeyEvent::new(KeyCode::Char('w'), KeyModifiers::NONE));
    game.handle_key(&KeyEvent::new(KeyCode::Char('w'), KeyModifiers::NONE));
    assert!(
        game.any_movement_held(),
        "press should latch a movement hold"
    );
    app.agents.get_mut(&id).unwrap().gboom = Some(game);
    app.active_view = ActiveView::Agent(id);
    app.gboom_release_backgrounded_games();
    assert!(
        app.agents[&id].gboom.as_ref().unwrap().any_movement_held(),
        "the active game must keep its holds"
    );
    app.active_view = ActiveView::Welcome;
    app.gboom_release_backgrounded_games();
    assert!(
        !app.agents[&id].gboom.as_ref().unwrap().any_movement_held(),
        "a backgrounded game must drop its holds"
    );
}
/// `Event::Resize` must close the tip show gate of every agent view until
/// the next draw re-measures: a trigger firing between the event and the
/// (debounced) resize draw would otherwise act on the pre-resize measurement
/// and burn a seen count on a tip the new layout can never paint. The event
/// must NOT write the full terminal size into `last_terminal_size` — views
/// can paint into chrome-shrunk rects, so the event height proves nothing
/// about the banner row.
#[test]
fn resize_event_closes_tip_show_gate_until_redraw() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().note_terminal_size((80, 30));
    let _ = app.handle_input(&Event::Resize(120, 50));
    let agent = app.agents.get_mut(&id).unwrap();
    assert_eq!(
        agent.last_terminal_size,
        (80, 30),
        "event must not overwrite the draw-measured rect size"
    );
    let mut counts = std::collections::HashMap::new();
    let tip = || {
        crate::tips::EphemeralTip::new("t", ratatui::text::Line::from("hint"))
            .with_session_seen_cap("t_seen", 2)
    };
    assert!(!agent.show_ephemeral_tip(tip(), &mut counts));
    assert!(counts.is_empty(), "stale-size show must not burn a count");
    agent.note_terminal_size((120, 50));
    assert!(agent.show_ephemeral_tip(tip(), &mut counts));
    assert_eq!(counts.get("t_seen"), Some(&1));
}
#[test]
fn apply_auth_meta_enables_billing_surface_for_personal_users() {
    let mut app = test_app();
    app.usage_visible = false;
    let meta = pi_shell::auth::AuthMeta::default();
    app.apply_auth_meta(&meta);
    assert!(app.usage_visible);
}
#[test]
fn apply_auth_meta_clears_api_key_flag_and_restores_billing_on_personal_login() {
    let mut app = test_app();
    app.is_api_key_auth = true;
    app.usage_visible = false;
    app.apply_auth_meta(&pi_shell::auth::AuthMeta::default());
    assert!(!app.is_api_key_auth);
    assert!(app.usage_visible);
}
#[test]
fn is_restricted_tier_classification() {
    assert!(is_restricted_tier(None));
    assert!(is_restricted_tier(Some("")));
    assert!(is_restricted_tier(Some("Free")));
    assert!(is_restricted_tier(Some("X Basic")));
    assert!(is_restricted_tier(Some("x_basic")));
    assert!(!is_restricted_tier(Some("SuperGrok")));
    assert!(!is_restricted_tier(Some("SuperGrok Heavy")));
    assert!(!is_restricted_tier(Some("X Premium")));
    assert!(!is_restricted_tier(Some("X Premium+")));
    assert!(!is_restricted_tier(Some("SomeFutureTier")));
}
#[test]
fn voice_included_in_tier_restricted_commands() {
    assert!(TIER_RESTRICTED_COMMANDS.contains(&"voice"));
}
#[test]
fn is_voice_tier_restricted_tracks_tier() {
    let mut app = test_app();
    app.apply_auth_meta(&pi_shell::auth::AuthMeta::default());
    assert!(app.is_voice_tier_restricted());
    let mut app = test_app();
    let meta = pi_shell::auth::AuthMeta {
        subscription_tier: Some("SuperGrok".into()),
        ..Default::default()
    };
    app.apply_auth_meta(&meta);
    assert!(!app.is_voice_tier_restricted());
}
#[test]
fn apply_auth_meta_clears_gate_on_subscription() {
    let mut app = test_app();
    app.gate = Some(pi_shell::auth::GateInfo {
        message: "Subscribe to use Grok Build".into(),
        url: Some("https://grok.com/supergrok?referrer=grok-build".into()),
        label: None,
    });
    assert!(app.is_access_blocked());
    let meta = pi_shell::auth::AuthMeta::default();
    app.apply_auth_meta(&meta);
    assert!(app.gate.is_none());
    assert!(app.has_access());
}
#[test]
fn apply_auth_meta_gate_unchanged_when_still_gated() {
    let mut app = test_app();
    let gate = pi_shell::auth::GateInfo {
        message: "Subscribe".into(),
        url: None,
        label: None,
    };
    app.gate = Some(gate.clone());
    let meta = pi_shell::auth::AuthMeta {
        gate: Some(gate),
        ..Default::default()
    };
    app.apply_auth_meta(&meta);
    assert!(app.gate.is_some());
    assert!(app.is_access_blocked());
}
#[test]
fn welcome_ctrl_q_requires_confirmation() {
    let mut app = test_app();
    let outcome = app.handle_input(&key_event(KeyCode::Char('q'), KeyModifiers::CONTROL));
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app
        .pending_action
        .as_ref()
        .expect("expected pending action");
    assert!(matches!(pending.action, Action::Quit));
    assert_eq!(
        pending.shortcut,
        KeyShortcut::from(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::CONTROL))
    );
}
#[test]
fn welcome_ctrl_u_update_keeps_priority_over_foreign_resume() {
    let mut app = test_app();
    app.foreign_session_compat = pi_foreign_sessions::EnabledForeignSessionSources {
        cursor: true,
        ..Default::default()
    };
    let crate::app::actions::Effect::CanonicalizeForeignResumeCwd {
        requested_cwd,
        launch_token,
    } = app.begin_foreign_resume_detection().unwrap()
    else {
        panic!("expected canonicalization effect");
    };
    let canonical_cwd = dunce::canonicalize(&requested_cwd).unwrap();
    assert!(app.accept_foreign_resume_canonical_cwd(
        launch_token,
        &requested_cwd,
        Some(canonical_cwd.clone()),
    ));
    app.apply_foreign_resume_detection(
        launch_token,
        &canonical_cwd,
        Some(pi_foreign_sessions::RecentForeignSession {
            tool: pi_foreign_sessions::ForeignSessionTool::Cursor,
            native_id: "cursor-session".into(),
            age: std::time::Duration::from_secs(30),
        }),
    );
    let key = key_event(KeyCode::Char('u'), KeyModifiers::CONTROL);
    assert!(matches!(
        app.handle_input(&key),
        InputOutcome::Action(Action::ResumeForeignSession)
    ));
    app.pending_update_version = Some("9.9.9".into());
    assert!(matches!(
        app.handle_input(&key),
        InputOutcome::Action(Action::QuitForUpdate)
    ));
}
#[test]
fn minimal_ctrl_g_edits_prompt_while_full_tui_leaves_it_unbound() {
    let event = key_event(KeyCode::Char('g'), KeyModifiers::CONTROL);
    let mut minimal = test_app_with_agent();
    minimal.screen_mode = ScreenMode::Minimal;
    minimal.registry = ActionRegistry::defaults_for(ScreenMode::Minimal);
    let id = super::super::agent::AgentId(0);
    minimal
        .agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .set_screen_mode(ScreenMode::Minimal);
    minimal
        .agents
        .get_mut(&id)
        .unwrap()
        .set_input_mode(crate::views::agent::InputMode::Vim);
    assert_eq!(
        minimal.agents[&id].active_pane,
        crate::views::agent::ActivePane::Scrollback,
        "Vim startup leaves the legacy pane field on Scrollback"
    );
    let out = minimal.handle_input(&event);
    assert!(matches!(
        out,
        InputOutcome::Action(Action::EditPromptExternal)
    ));
    minimal.pending_editor = Some(
        crate::app::external_editor::PendingEditorRequest::PromptDraft {
            agent_id: id,
            original_text: "already pending".to_owned(),
        },
    );
    assert!(matches!(
        minimal.handle_input(&event),
        InputOutcome::Unchanged
    ));
    let mut owned = test_app_with_agent();
    owned.screen_mode = ScreenMode::Minimal;
    owned.registry = ActionRegistry::defaults_for(ScreenMode::Minimal);
    owned
        .agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .suggestions
        .dropdown
        .open = true;
    assert!(matches!(owned.handle_input(&event), InputOutcome::Changed));
    assert!(owned.pending_editor.is_none());
    let mut full = test_app_with_agent();
    full.screen_mode = ScreenMode::Fullscreen;
    let out = full.handle_input(&event);
    assert!(
        !matches!(out, InputOutcome::Action(Action::EditPromptExternal)),
        "Ctrl+G is only the external-editor chord in minimal mode, got {out:?}"
    );
    assert!(full.pending_editor.is_none());
}
#[test]
fn minimal_ctrl_t_toggles_todo_panel() {
    let mut app = test_app_with_agent();
    app.screen_mode = ScreenMode::Minimal;
    assert!(!app.minimal_state.show_todos);
    let out = app.handle_input(&key_event(KeyCode::Char('t'), KeyModifiers::CONTROL));
    assert!(matches!(out, InputOutcome::Changed));
    assert!(
        app.minimal_state.show_todos,
        "Ctrl+T pins the panel visible"
    );
    let _ = app.handle_input(&key_event(KeyCode::Char('t'), KeyModifiers::CONTROL));
    assert!(
        !app.minimal_state.show_todos,
        "Ctrl+T again unpins the panel"
    );
}
#[test]
fn non_minimal_ctrl_t_leaves_todo_panel_flag_untouched() {
    let mut app = test_app_with_agent();
    app.screen_mode = ScreenMode::Inline;
    assert!(!app.minimal_state.show_todos);
    let _ = app.handle_input(&key_event(KeyCode::Char('t'), KeyModifiers::CONTROL));
    assert!(
        !app.minimal_state.show_todos,
        "the minimal todo-panel flag must never flip outside minimal mode"
    );
}
/// In minimal mode Ctrl+O routes to `Action::OpenTranscriptPager`.
#[test]
fn minimal_ctrl_o_opens_transcript_pager() {
    let mut app = test_app_with_agent();
    app.screen_mode = ScreenMode::Minimal;
    app.registry = ActionRegistry::non_vscode_for_mode_for_test(ScreenMode::Minimal);
    let out = app.handle_input(&key_event(KeyCode::Char('o'), KeyModifiers::CONTROL));
    assert!(
        matches!(out, InputOutcome::Action(Action::OpenTranscriptPager)),
        "expected OpenTranscriptPager, got {out:?}"
    );
}
/// Minimal maps the full-TUI queue chord to `/queue` because the pane is absent.
#[test]
fn minimal_toggle_queue_chord_shows_queue_block() {
    let mut app = test_app_with_agent();
    app.screen_mode = ScreenMode::Minimal;
    app.registry = ActionRegistry::non_vscode_for_mode_for_test(ScreenMode::Minimal);
    let out = app.handle_input(&key_event(KeyCode::Char(';'), KeyModifiers::CONTROL));
    assert!(
        matches!(out, InputOutcome::Action(Action::ShowQueue)),
        "expected ShowQueue, got {out:?}"
    );
    app.screen_mode = ScreenMode::Fullscreen;
    let out = app.handle_input(&key_event(KeyCode::Char(';'), KeyModifiers::CONTROL));
    assert!(
        !matches!(out, InputOutcome::Action(Action::ShowQueue)),
        "full TUI must keep the queue-pane toggle, got {out:?}"
    );
}
fn welcome_session_entry(id: &str) -> SessionPickerEntry {
    SessionPickerEntry {
        id: id.into(),
        summary: id.into(),
        updated_at: chrono::Utc::now(),
        created_at: chrono::Utc::now(),
        cwd: "/tmp/repo".into(),
        hostname: None,
        source: "local".into(),
        model_id: None,
        num_messages: 0,
        last_active_at: None,
        branch: None,
        repo_name: "tmp-repo".into(),
        worktree_label: None,
        last_turn_summary: None,
        last_recap: None,
        card_detail: None,
    }
}
fn open_welcome_session_picker(app: &mut AppView) {
    crate::appearance::cache::set_vim_mode(false);
    app.session_picker_entries = Some(vec![welcome_session_entry("session-0")]);
    app.session_picker_state.search_active = true;
}
#[test]
fn welcome_session_picker_ctrl_w_resumes_in_worktree_while_search_is_focused() {
    let mut app = test_app();
    open_welcome_session_picker(&mut app);
    app.session_picker_state.set_query("session");
    let outcome = app.handle_input(&key_event(KeyCode::Char('w'), KeyModifiers::CONTROL));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::PickSessionInWorktree(0))
    ));
    assert_eq!(app.session_picker_state.query(), "session");
}
#[test]
fn welcome_session_picker_ctrl_d_keeps_global_quit_precedence() {
    let mut app = test_app();
    open_welcome_session_picker(&mut app);
    app.session_picker_state.set_query("session");
    let outcome = app.handle_input(&ctrl_d());
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(matches!(
        app.pending_action.as_ref().map(|pending| &pending.action),
        Some(Action::Quit)
    ));
    assert_eq!(app.session_picker_state.query(), "session");
}
#[test]
fn welcome_session_picker_cursor_motion_does_not_trigger_deep_search() {
    let mut app = test_app();
    open_welcome_session_picker(&mut app);
    app.session_picker_state.set_query("session");
    let outcome = app.handle_input(&key_event(KeyCode::Left, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.session_picker_state.query(), "session");
}
#[test]
fn welcome_session_picker_ctrl_u_kills_to_cursor_and_triggers_deep_search() {
    let mut app = test_app();
    open_welcome_session_picker(&mut app);
    app.session_picker_state.set_query("session");
    let _ = app.handle_input(&key_event(KeyCode::Left, KeyModifiers::NONE));
    let outcome = app.handle_input(&key_event(KeyCode::Char('u'), KeyModifiers::CONTROL));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::TriggerDeepSearch)
    ));
    assert_eq!(app.session_picker_state.query(), "n");
    assert_eq!(app.session_picker_state.query_cursor(), 0);
}
#[test]
fn welcome_ctrl_w_opens_new_worktree_dialog() {
    let mut app = test_app();
    app.cwd_has_git_ancestor = true;
    let outcome = app.handle_input(&key_event(KeyCode::Char('w'), KeyModifiers::CONTROL));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::OpenNewWorktreeDialog)
    ));
}
#[test]
fn welcome_ctrl_w_noop_outside_git_repo() {
    let mut app = test_app();
    app.cwd_has_git_ancestor = false;
    let outcome = app.handle_input(&key_event(KeyCode::Char('w'), KeyModifiers::CONTROL));
    assert!(matches!(outcome, InputOutcome::Unchanged));
}
#[test]
fn welcome_trust_decline_keys_quit() {
    for code in [KeyCode::Char('n'), KeyCode::Char('N'), KeyCode::Esc] {
        let mut app = test_app();
        app.trust_state = TrustState::Pending {
            workspace: std::path::PathBuf::from("/tmp/x"),
        };
        let outcome = app.handle_input(&key_event(code, KeyModifiers::NONE));
        assert!(
            matches!(outcome, InputOutcome::Action(Action::Quit)),
            "{code:?} on the trust prompt must quit, got {outcome:?}"
        );
    }
    let mut app = test_app();
    app.trust_state = TrustState::Pending {
        workspace: std::path::PathBuf::from("/tmp/x"),
    };
    let outcome = app.handle_input(&key_event(KeyCode::Char('y'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::TrustFolder)));
}
/// A notice already on screen, with both menu rows and both of its links painted.
fn consent_pending_app() -> AppView {
    use crate::app::consent::{ConsentLegibility, ConsentNotice, ConsentSegment};
    use ratatui::layout::Rect;
    let mut app = test_app();
    app.trust_state = TrustState::Pending {
        workspace: std::path::PathBuf::from("/tmp/x"),
    };
    app.consent_state = crate::app::consent::ConsentState::Pending {
        notice: ConsentNotice {
            id: "notice".to_string(),
            version: 1,
            title: "Title".to_string(),
            segments: vec![
                ConsentSegment::Link {
                    index: 0,
                    label: "Terms".to_string(),
                },
                ConsentSegment::Link {
                    index: 1,
                    label: "AUP".to_string(),
                },
            ],
            links: vec![
                "https://example.com/legal/tos".to_string(),
                "https://example.com/legal/aup".to_string(),
            ],
            accept_label: "Accept".to_string(),
        },
        legibility: ConsentLegibility::Painted,
        painted_at: Some(std::time::Instant::now()),
    };
    app.welcome_menu_rects = vec![Rect::new(10, 20, 30, 1), Rect::new(10, 21, 30, 1)];
    app.welcome_consent_link_rects =
        vec![(0, Rect::new(5, 12, 5, 1)), (1, Rect::new(20, 12, 6, 1))];
    app
}
/// Accept is `a` alone. `y` belongs to the trust question one screen later, Enter may be buffered,
/// and the rest have no meaning here.
#[test]
fn welcome_consent_answers_only_to_its_own_keys() {
    for code in [
        KeyCode::Char('y'),
        KeyCode::Char('n'),
        KeyCode::Esc,
        KeyCode::Enter,
        KeyCode::Char(' '),
        KeyCode::Tab,
    ] {
        let mut app = consent_pending_app();
        let outcome = app.handle_input(&key_event(code, KeyModifiers::NONE));
        assert!(
            matches!(outcome, InputOutcome::Unchanged),
            "{code:?} must not answer the notice, got {outcome:?}",
        );
    }
    let mut app = consent_pending_app();
    assert!(matches!(
        app.handle_input(&key_event(KeyCode::Char('a'), KeyModifiers::NONE)),
        InputOutcome::Action(Action::AcceptConsent)
    ));
    let mut app = consent_pending_app();
    assert!(matches!(app.handle_input(&ctrl_c()), InputOutcome::Changed));
    assert!(
        app.pending_action.is_some(),
        "the first Ctrl+C must arm the confirmation"
    );
    let mut app = consent_pending_app();
    assert!(
        matches!(
            app.handle_input(&key_event(KeyCode::Char('q'), KeyModifiers::NONE)),
            InputOutcome::Action(Action::Quit)
        ),
        "the screen offers Quit, so the key has to work",
    );
}
/// Every event from before the notice painted was aimed at the screen it replaced, and acting on
/// one would quit and take the composer's text with it. Ctrl+C is the exception, because nothing
/// else on this screen handles it.
#[test]
fn welcome_consent_ignores_everything_from_before_the_paint() {
    use crate::app::consent::ConsentState;
    let unpainted = || {
        let mut app = consent_pending_app();
        if let ConsentState::Pending { painted_at, .. } = &mut app.consent_state {
            *painted_at = None;
        }
        app
    };
    let mut app = consent_pending_app();
    let painted = match &app.consent_state {
        ConsentState::Pending { painted_at, .. } => painted_at.expect("painted"),
        ConsentState::Done => unreachable!(),
    };
    let outcome = app.handle_input_at_with_paste_provenance(
        &key_event(KeyCode::Char('a'), KeyModifiers::NONE),
        painted - std::time::Duration::from_millis(1),
        crate::app::app_view::PasteProvenance::Terminal,
    );
    assert!(
        matches!(outcome, InputOutcome::Unchanged),
        "a key that predates the notice was aimed at the composer, got {outcome:?}",
    );
    for ev in [
        left_mouse(MouseEventKind::Down(MouseButton::Left), 12, 20),
        key_event(KeyCode::Char('q'), KeyModifiers::NONE),
    ] {
        assert!(matches!(
            unpainted().handle_input(&ev),
            InputOutcome::Unchanged
        ));
    }
    let mut app = unpainted();
    assert!(matches!(app.handle_input(&ctrl_c()), InputOutcome::Changed));
    assert!(
        app.pending_action.is_some(),
        "a notice that never painted must still be escapable",
    );
}
#[test]
fn welcome_consent_answers_and_links_are_reachable_by_key_and_click() {
    let click = |col, row| left_mouse(MouseEventKind::Down(MouseButton::Left), col, row);
    let mut app = consent_pending_app();
    assert!(matches!(
        app.handle_input(&click(12, 20)),
        InputOutcome::Action(Action::AcceptConsent)
    ));
    let mut app = consent_pending_app();
    assert!(matches!(
        app.handle_input(&click(12, 21)),
        InputOutcome::Action(Action::Quit)
    ));
    let mut app = consent_pending_app();
    assert!(matches!(
        app.handle_input(&click(21, 12)),
        InputOutcome::Action(Action::OpenConsentLink(1))
    ));
    let mut app = consent_pending_app();
    assert!(matches!(
        app.handle_input(&key_event(KeyCode::Char('2'), KeyModifiers::NONE)),
        InputOutcome::Action(Action::OpenConsentLink(1))
    ));
    for code in [KeyCode::Char('0'), KeyCode::Char('3')] {
        let mut app = consent_pending_app();
        assert!(
            matches!(
                app.handle_input(&key_event(code, KeyModifiers::NONE)),
                InputOutcome::Unchanged
            ),
            "{code:?} addresses no link",
        );
    }
}
/// What the renderer reports is the only thing standing between a click and an acceptance, so the
/// three answers it can give have to land in the state exactly.
#[test]
fn consent_paint_records_what_the_renderer_reported() {
    use crate::app::consent::{ConsentLegibility, ConsentNotice, ConsentState};
    let pending = || ConsentState::Pending {
        notice: ConsentNotice {
            id: "notice".to_string(),
            version: 1,
            title: "Title".to_string(),
            segments: Vec::new(),
            links: Vec::new(),
            accept_label: "Accept".to_string(),
        },
        legibility: ConsentLegibility::Illegible,
        painted_at: None,
    };
    let mut state = pending();
    record_consent_paint(&mut state, Some(ConsentLegibility::Illegible));
    let ConsentState::Pending {
        painted_at,
        legibility,
        ..
    } = &state
    else {
        panic!("expected pending");
    };
    assert!(painted_at.is_some(), "an illegible paint is still a paint");
    assert_eq!(*legibility, ConsentLegibility::Illegible);
    let mut state = pending();
    record_consent_paint(&mut state, Some(ConsentLegibility::Painted));
    record_consent_paint(&mut state, None);
    let ConsentState::Pending {
        painted_at,
        legibility,
        ..
    } = &state
    else {
        panic!("expected pending");
    };
    assert_eq!(
        *legibility,
        ConsentLegibility::Illegible,
        "a frame that did not paint the notice cannot leave it acceptable",
    );
    assert!(painted_at.is_some(), "the first paint still happened");
}
/// An unreadable notice still has to take `q`, so the paint stamp cannot wait for legibility.
#[test]
fn welcome_consent_quit_works_while_the_body_is_unreadable() {
    use crate::app::consent::{ConsentLegibility, ConsentState};
    let mut app = consent_pending_app();
    if let ConsentState::Pending { legibility, .. } = &mut app.consent_state {
        *legibility = ConsentLegibility::Illegible;
    }
    app.welcome_menu_rects.truncate(1);
    assert!(matches!(
        app.handle_input(&key_event(KeyCode::Char('q'), KeyModifiers::NONE)),
        InputOutcome::Action(Action::Quit)
    ));
    let mut app = consent_pending_app();
    if let ConsentState::Pending { legibility, .. } = &mut app.consent_state {
        *legibility = ConsentLegibility::Illegible;
    }
    for ev in [
        key_event(KeyCode::Char('1'), KeyModifiers::NONE),
        left_mouse(MouseEventKind::Down(MouseButton::Left), 6, 12),
    ] {
        assert!(matches!(app.handle_input(&ev), InputOutcome::Unchanged));
    }
    let mut app = consent_pending_app();
    if let ConsentState::Pending { legibility, .. } = &mut app.consent_state {
        *legibility = ConsentLegibility::Illegible;
    }
    assert!(matches!(
        app.handle_input(&left_mouse(MouseEventKind::Down(MouseButton::Left), 12, 20)),
        InputOutcome::Action(Action::Quit)
    ));
}
#[test]
fn welcome_consent_hover_tracks_the_menu_row_and_the_link() {
    let mut app = consent_pending_app();
    app.welcome_menu_rects.truncate(1);
    let moved = |col, row| left_mouse(MouseEventKind::Moved, col, row);
    assert!(matches!(
        app.handle_input(&moved(12, 20)),
        InputOutcome::Changed
    ));
    assert_eq!(app.welcome_menu_index, Some(0));
    assert!(matches!(
        app.handle_input(&moved(30, 20)),
        InputOutcome::Unchanged
    ));
    assert!(matches!(
        app.handle_input(&moved(6, 12)),
        InputOutcome::Changed
    ));
    assert_eq!(app.welcome_consent_hover_link, Some(0));
    assert_eq!(app.welcome_menu_index, None);
    assert!(matches!(
        app.handle_input(&moved(21, 12)),
        InputOutcome::Changed
    ));
    assert_eq!(app.welcome_consent_hover_link, Some(1));
    assert!(matches!(
        app.handle_input(&moved(0, 0)),
        InputOutcome::Changed
    ));
    assert_eq!(app.welcome_consent_hover_link, None);
    assert!(matches!(
        app.handle_input(&moved(1, 0)),
        InputOutcome::Unchanged
    ));
}
#[test]
fn welcome_ctrl_c_requires_confirmation() {
    let mut app = test_app();
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app
        .pending_action
        .as_ref()
        .expect("expected pending action");
    assert!(matches!(pending.action, Action::Quit));
    assert_eq!(
        pending.shortcut,
        KeyShortcut::from(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL))
    );
}
#[test]
fn welcome_ctrl_c_double_press_quits() {
    let mut app = test_app();
    let _ = app.handle_input(&ctrl_c());
    assert!(app.pending_action.is_some());
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn welcome_ctrl_d_requires_confirmation() {
    let mut app = test_app();
    let outcome = app.handle_input(&ctrl_d());
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app
        .pending_action
        .as_ref()
        .expect("expected pending action");
    assert!(matches!(pending.action, Action::Quit));
    assert_eq!(
        pending.shortcut,
        KeyShortcut::from(KeyEvent::new(KeyCode::Char('d'), KeyModifiers::CONTROL))
    );
}
#[test]
fn menu_action_indices_without_import() {
    assert!(matches!(
        dispatch_menu_action(0, false),
        InputOutcome::Action(Action::OpenNewWorktreeDialog)
    ));
    assert!(matches!(
        dispatch_menu_action(1, false),
        InputOutcome::Action(Action::FetchSessionList)
    ));
    assert!(matches!(
        dispatch_menu_action(2, false),
        InputOutcome::Action(Action::Quit)
    ));
    // Past the last row there is nothing to do.
    assert!(matches!(
        dispatch_menu_action(3, false),
        InputOutcome::Unchanged
    ));
}
#[test]
fn menu_action_indices_with_import() {
    assert!(matches!(
        dispatch_menu_action(0, true),
        InputOutcome::Action(Action::ImportClaudeSettings)
    ));
    assert!(matches!(
        dispatch_menu_action(1, true),
        InputOutcome::Action(Action::OpenNewWorktreeDialog)
    ));
    assert!(matches!(
        dispatch_menu_action(2, true),
        InputOutcome::Action(Action::FetchSessionList)
    ));
    assert!(matches!(
        dispatch_menu_action(3, true),
        InputOutcome::Action(Action::Quit)
    ));
    assert!(matches!(
        dispatch_menu_action(4, true),
        InputOutcome::Unchanged
    ));
}
#[test]
fn welcome_pending_ctrl_c_quits_instantly() {
    let mut app = test_app();
    app.auth_state = AuthState::Pending { error: None };
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn welcome_authenticating_ctrl_c_quits_instantly() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Command,
    };
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn page_keys_from_prompt_page_conversation_without_mutating_prompt() {
    let mut app = test_app_with_agent();
    let ActiveView::Agent(id) = app.active_view else {
        panic!("test app must start on an agent");
    };
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.set_active_pane(crate::app::agent_view::AgentPane::Prompt, true);
        agent.prompt.set_text("draft text");
        agent.prompt.textarea.set_selection(1, 5);
    }
    let prompt_before = {
        let agent = &app.agents[&id];
        (
            agent.prompt.text().to_owned(),
            agent.prompt.cursor(),
            agent.prompt.textarea.selection_range(),
        )
    };
    assert!(
        prompt_before.2.is_some(),
        "precondition: prompt selection is active"
    );
    for (code, page_up) in [(KeyCode::PageUp, true), (KeyCode::PageDown, false)] {
        let outcome = app.handle_input(&key_event(code, KeyModifiers::NONE));
        assert!(
            matches!(
                (&outcome, page_up),
                (InputOutcome::Action(Action::PageUp), true)
                    | (InputOutcome::Action(Action::PageDown), false)
            ),
            "{code:?} must page the conversation, got {outcome:?}",
        );
        let agent = &app.agents[&id];
        assert_eq!(agent.active_pane, crate::app::agent_view::AgentPane::Prompt);
        assert_eq!(agent.prompt.text(), prompt_before.0);
        assert_eq!(agent.prompt.cursor(), prompt_before.1);
        assert_eq!(agent.prompt.textarea.selection_range(), prompt_before.2);
    }
}
#[test]
fn prompt_paging_scope_matches_agent_surface() {
    fn focused_app(screen_mode: ScreenMode) -> (AppView, super::super::agent::AgentId) {
        let mut app = test_app_with_agent();
        app.screen_mode = screen_mode;
        let ActiveView::Agent(id) = app.active_view else {
            panic!("test app must start on an agent");
        };
        app.agents
            .get_mut(&id)
            .unwrap()
            .set_active_pane(crate::app::agent_view::AgentPane::Prompt, true);
        (app, id)
    }
    for (label, screen_mode, paging_enabled) in [
        ("inline agent", ScreenMode::Inline, true),
        ("fullscreen agent", ScreenMode::Fullscreen, true),
        ("minimal agent", ScreenMode::Minimal, false),
    ] {
        let (mut app, _id) = focused_app(screen_mode);
        let outcome = app.handle_input(&key_event(KeyCode::PageUp, KeyModifiers::NONE));
        assert_eq!(
            matches!(
                &outcome,
                InputOutcome::Action(Action::PageUp | Action::PageDown)
            ),
            paging_enabled,
            "{label} prompt paging scope mismatch: {outcome:?}",
        );
    }
}
#[test]
fn ctrl_d_from_scrollback_is_half_page_down_not_quit() {
    let mut app = test_app_with_agent();
    pin_non_vscode_registry(&mut app);
    let outcome = app.handle_input(&ctrl_d());
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::HalfPageDown)
    ));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_d_double_press_quits_from_prompt() {
    let mut app = test_app_with_agent();
    pin_non_vscode_registry(&mut app);
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().active_pane = crate::views::agent::ActivePane::Prompt;
    let outcome = app.handle_input(&ctrl_d());
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "first Ctrl+D should set pending quit, got: {outcome:?}",
    );
    assert!(app.pending_action.is_some());
    assert_eq!(app.pending_action.as_ref().unwrap().label, Some("quit"));
    let outcome = app.handle_input(&ctrl_d());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_d_in_vscode_quits_from_scrollback() {
    let mut app = test_app_with_agent();
    let mut actions = crate::actions::default_actions(ScreenMode::Fullscreen, false);
    for def in actions.iter_mut() {
        if def.id == ActionId::Quit {
            def.default_key = key!('d', CONTROL);
            def.alt_keys = vec![];
        }
        if def.id == ActionId::HalfPageDown {
            def.default_key = key!('D');
        }
    }
    app.registry = ActionRegistry::new(actions);
    let outcome = app.handle_input(&ctrl_d());
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "first Ctrl+D should set pending quit, got: {outcome:?}",
    );
    assert!(app.pending_action.is_some());
    assert_eq!(app.pending_action.as_ref().unwrap().label, Some("quit"));
    let outcome = app.handle_input(&ctrl_d());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_q_sets_pending_action() {
    let mut app = test_app_with_agent();
    let outcome = app.handle_input(&ctrl_q());
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(app.pending_action.is_some());
    assert_eq!(app.pending_action.as_ref().unwrap().label, Some("quit"));
}
#[test]
fn ctrl_q_double_press_quits() {
    let mut app = test_app_with_agent();
    let _ = app.handle_input(&ctrl_q());
    assert!(app.pending_action.is_some());
    let outcome = app.handle_input(&ctrl_q());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn different_key_clears_pending() {
    crate::appearance::cache::set_simple_mode(false);
    let mut app = test_app_with_agent();
    if let ActiveView::Agent(id) = app.active_view
        && let Some(agent) = app.agents.get_mut(&id)
    {
        agent.vim_mode = true;
    }
    let _ = app.handle_input(&ctrl_q());
    assert!(app.pending_action.is_some());
    let outcome = app.handle_input(&key_event(KeyCode::Char('j'), KeyModifiers::NONE));
    assert!(app.pending_action.is_none());
    assert!(matches!(outcome, InputOutcome::Action(Action::SelectNext)));
}
#[test]
fn ctrl_q_then_ctrl_d_does_not_confirm() {
    let mut app = test_app_with_agent();
    pin_non_vscode_registry(&mut app);
    let _ = app.handle_input(&ctrl_q());
    assert!(app.pending_action.is_some());
    let outcome = app.handle_input(&ctrl_d());
    assert!(app.pending_action.is_none());
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::HalfPageDown)
    ));
}
fn ctrl_n() -> Event {
    key_event(KeyCode::Char('n'), KeyModifiers::CONTROL)
}
#[test]
fn ctrl_n_sets_pending_new_session() {
    let mut app = test_app_with_agent();
    let outcome = app.handle_input(&ctrl_n());
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app.pending_action.as_ref().expect("pending action");
    assert_eq!(pending.label, Some("new"));
}
#[test]
fn second_ctrl_n_opens_new_session_mode_question_when_mode_is_ask() {
    let mut app = test_app_with_agent();
    app.new_session_worktree_mode = WorktreeMode::Ask;
    let _ = app.handle_input(&ctrl_n());
    let outcome = app.handle_input(&ctrl_n());
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::ChooseNewSessionMode)
    ));
    assert!(app.pending_action.is_none());
}
#[test]
fn second_ctrl_n_respects_never_worktree_mode() {
    let mut app = test_app_with_agent();
    app.new_session_worktree_mode = WorktreeMode::Never;
    let _ = app.handle_input(&ctrl_n());
    let outcome = app.handle_input(&ctrl_n());
    assert!(matches!(outcome, InputOutcome::Action(Action::NewSession)));
    assert!(app.pending_action.is_none());
}
#[test]
fn second_ctrl_n_respects_always_worktree_mode() {
    let mut app = test_app_with_agent();
    app.new_session_worktree_mode = WorktreeMode::Always;
    let _ = app.handle_input(&ctrl_n());
    let outcome = app.handle_input(&ctrl_n());
    assert!(matches!(outcome, InputOutcome::Action(Action::NewSession)));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_c_running_cancels_turn() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Action(Action::CancelTurn)));
}
#[test]
fn ctrl_c_cancelling_escalates_to_quit_pending() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnCancelling;
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(app.pending_action.is_some());
    assert_eq!(app.pending_action.as_ref().unwrap().label, Some("quit"));
}
fn assert_pending_quit(app: &AppView) {
    let pending = app
        .pending_action
        .as_ref()
        .expect("expected pending action");
    assert_eq!(pending.label, Some("quit"));
    assert!(matches!(pending.action, Action::Quit));
}
#[test]
fn ctrl_c_idle_empty_prompt_sets_pending_quit() {
    crate::appearance::cache::set_simple_mode(true);
    let mut app = test_app_with_agent();
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_pending_quit(&app);
}
#[test]
fn ctrl_c_idle_empty_prompt_focused_sets_pending_quit() {
    crate::appearance::cache::set_simple_mode(true);
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().active_pane = crate::views::agent::ActivePane::Prompt;
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_pending_quit(&app);
}
#[test]
fn ctrl_c_double_press_idle_quits() {
    crate::appearance::cache::set_simple_mode(true);
    let mut app = test_app_with_agent();
    let _ = app.handle_input(&ctrl_c());
    assert!(app.pending_action.is_some());
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_c_consumed_by_cancel_does_not_set_pending() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Action(Action::CancelTurn)));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_c_consumed_by_text_clear_does_not_set_pending() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt.textarea.set_text("some text");
    let outcome = app.handle_input(&ctrl_c());
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_c_then_other_key_resets_pending() {
    crate::appearance::cache::set_simple_mode(true);
    let mut app = test_app_with_agent();
    let _ = app.handle_input(&ctrl_c());
    assert!(app.pending_action.is_some());
    let _ = app.handle_input(&key_event(KeyCode::Char('j'), KeyModifiers::NONE));
    assert!(app.pending_action.is_none());
}
#[test]
fn ctrl_c_idle_prompt_with_text_clears_text() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt.textarea.set_text("draft prompt");
    assert!(agent.session.state.is_idle());
    let outcome = app.handle_input(&ctrl_c());
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Ctrl+C with text in idle prompt must Change (clear text), got: {outcome:?}",
    );
    assert!(
        app.agents[&id].prompt.textarea.text().is_empty(),
        "Ctrl+C must clear prompt text when agent is idle; got: {:?}",
        app.agents[&id].prompt.textarea.text(),
    );
}
#[test]
fn ctrl_c_running_prompt_with_text_clears_text_and_preserves_turn() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt.textarea.set_text("draft prompt");
    let outcome = app.handle_input(&ctrl_c());
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Ctrl+C with text in a running prompt must clear the text, got: {outcome:?}",
    );
    assert!(
        app.agents[&id].prompt.textarea.text().is_empty(),
        "Ctrl+C must clear prompt text first; got: {:?}",
        app.agents[&id].prompt.textarea.text(),
    );
    assert!(
        app.agents[&id].session.state.is_turn_running(),
        "First Ctrl+C must NOT cancel the turn while a draft was present",
    );
    let outcome = app.handle_input(&ctrl_c());
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Second Ctrl+C on empty running prompt must CancelTurn, got: {outcome:?}",
    );
}
#[test]
fn esc_from_prompt_pane_running_turn_cancels_in_non_vim_mode() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.vim_mode = false;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "1× Esc while running must cancel in non-vim mode, got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
    assert_eq!(
        app.agents[&id].cancel_trigger_hint,
        Some(crate::app::actions::CancelTrigger::Esc)
    );
}
#[test]
fn esc_cancels_running_wake_turn_while_pane_is_idle() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.running_wake_turn = Some(crate::app::agent_view::RunningWakeTurn {
        prompt_id: "task-completed-bg1".into(),
        cancel_sent: false,
    });
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.vim_mode = false;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Esc during a wake turn must cancel, got {outcome:?}"
    );
    assert!(
        app.pending_action.is_none(),
        "must not arm idle clear/rewind"
    );
    assert_eq!(
        app.agents[&id].cancel_trigger_hint,
        Some(crate::app::actions::CancelTrigger::Esc)
    );
}
#[test]
fn esc_from_prompt_pane_running_turn_with_draft_cancels_preserving_draft() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.vim_mode = false;
    agent.prompt.textarea.set_text("draft while streaming");
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "mid-turn Esc with draft must cancel in non-vim mode, got {outcome:?}"
    );
    assert!(app.pending_action.is_none(), "must not arm idle clear");
    assert_eq!(
        app.agents[&id].prompt.textarea.text(),
        "draft while streaming",
        "Esc cancel must preserve the draft (not clear it like Ctrl+C)"
    );
    assert_eq!(
        app.agents[&id].cancel_trigger_hint,
        Some(crate::app::actions::CancelTrigger::Esc)
    );
}
#[test]
fn esc_from_scrollback_pane_running_turn_cancels_in_non_vim_mode() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    agent.vim_mode = false;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "1× Esc from scrollback while running must cancel in non-vim mode, got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
    assert_eq!(
        app.agents[&id].cancel_trigger_hint,
        Some(crate::app::actions::CancelTrigger::Esc)
    );
}
#[test]
fn esc_from_prompt_pane_running_turn_vim_mode_is_swallowed() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.vim_mode = true;
    agent.prompt.textarea.set_text("draft while streaming");
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "1× Esc while running must swallow in vim mode, got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
    assert!(app.agents[&id].cancel_trigger_hint.is_none());
    assert_eq!(
        app.agents[&id].prompt.textarea.text(),
        "draft while streaming",
        "vim mid-turn Esc must not clear the draft or arm idle clear"
    );
    assert!(app.agents[&id].session.state.is_turn_running());
}
#[test]
fn esc_from_scrollback_pane_running_turn_vim_mode_is_swallowed() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    agent.vim_mode = true;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "1× Esc from scrollback while running must swallow in vim mode, got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
    assert!(app.agents[&id].cancel_trigger_hint.is_none());
    assert!(app.agents[&id].session.state.is_turn_running());
}
#[test]
fn esc_cancels_turn_gate_truth_table() {
    assert!(crate::app::esc_cancels_turn(true, true));
    assert!(crate::app::esc_cancels_turn(true, false));
    assert!(crate::app::esc_cancels_turn(false, false));
    assert!(!crate::app::esc_cancels_turn(false, true));
}
#[test]
fn esc_running_turn_minimal_screen_mode_cancels_even_with_vim_on() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.vim_mode = true;
    agent
        .prompt
        .set_screen_mode(crate::app::ScreenMode::Minimal);
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "minimal mode must Esc-cancel even with vim scrollback nav on, got {outcome:?}"
    );
    assert_eq!(
        app.agents[&id].cancel_trigger_hint,
        Some(crate::app::actions::CancelTrigger::Esc)
    );
}
#[test]
fn esc_owned_before_agent_covers_app_level_owners() {
    let mut app = test_app_with_agent();
    assert!(!app.esc_owned_before_agent());
    let target = VoiceTarget::Agent(super::super::agent::AgentId(0));
    app.voice_state = VoiceState::Recording {
        hold: false,
        target,
        interim: None,
    };
    assert!(app.esc_owned_before_agent(), "listening owns Esc");
    app.voice_state = VoiceState::ColdStart {
        hold: false,
        target,
    };
    assert!(app.esc_owned_before_agent(), "pending cold-start owns Esc");
    app.voice_state = VoiceState::Idle;
    assert!(!app.esc_owned_before_agent());
    app.import_claude_modal = Some(
        crate::views::import_claude_modal::ImportClaudeModalState::new(
            pi_shell::claude_import::ImportPlan::default(),
            std::path::PathBuf::from("/tmp"),
        ),
    );
    assert!(app.esc_owned_before_agent(), "import-claude modal owns Esc");
    app.import_claude_modal = None;
    assert!(!app.esc_owned_before_agent());
}
#[test]
fn esc_while_cancelling_retries_cancel() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnCancelling;
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    agent.vim_mode = true;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Esc while cancelling must retry CancelTurn, got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
    assert_eq!(
        app.agents[&id].cancel_trigger_hint,
        Some(crate::app::actions::CancelTrigger::Esc)
    );
}
#[test]
fn esc_cancel_grace_holds_rewind_arm_then_expires() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.vim_mode = false;
    agent
        .scrollback
        .push_block(crate::scrollback::block::RenderBlock::user_prompt(
            "earlier",
        ));
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::CancelTurn)));
    assert!(app.agents[&id].rewind_suppress_deadline.is_some());
    app.agents.get_mut(&id).unwrap().session.state = AgentState::Idle;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Esc within the post-cancel grace must swallow, got {outcome:?}"
    );
    assert!(
        app.pending_action.is_none(),
        "post-cancel Esc must not arm the rewind picker"
    );
    app.agents.get_mut(&id).unwrap().rewind_suppress_deadline = Some(std::time::Instant::now());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        matches!(
            app.pending_action.as_ref().map(|p| &p.action),
            Some(Action::RewindShowPicker)
        ),
        "expired grace must restore the idle rewind arm"
    );
    assert!(
        app.agents[&id].rewind_suppress_deadline.is_none(),
        "the expired deadline must be cleared on the consult"
    );
}
#[test]
fn idle_non_empty_double_esc_clears_prompt() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt.textarea.set_text("draft to clear");
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app.pending_action.as_ref().expect("arm clear");
    assert_eq!(pending.label, Some("clear"));
    assert!(matches!(pending.action, Action::ClearPrompt));
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::ClearPrompt)));
    assert!(app.pending_action.is_none());
    let effects = crate::app::dispatch::dispatch(Action::ClearPrompt, &mut app);
    assert!(effects.is_empty());
    assert!(app.agents[&id].prompt.textarea.text().is_empty());
    assert!(
        app.agents[&id].session.prompt_history.is_empty(),
        "the cleared draft goes to the stash, never to the history"
    );
    assert_eq!(
        app.agents[&id]
            .prompt_stash
            .as_ref()
            .map(|entry| entry.prompt.text.as_str()),
        Some("draft to clear")
    );
}
#[test]
fn idle_empty_with_messages_double_esc_opens_rewind_silent() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent
        .scrollback
        .push_block(crate::scrollback::block::RenderBlock::user_prompt(
            "earlier",
        ));
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app.pending_action.as_ref().expect("arm rewind");
    assert!(
        pending.label.is_none(),
        "first Esc for rewind must be silent"
    );
    assert!(matches!(pending.action, Action::RewindShowPicker));
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::RewindShowPicker)
    ));
    assert!(app.pending_action.is_none());
}
#[test]
fn idle_empty_no_messages_esc_is_swallowed() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    assert!(agent.scrollback.is_empty());
    assert!(agent.prompt.textarea.text().is_empty());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "idle empty with no messages must swallow Esc (not FocusScrollback), got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
    assert_eq!(
        app.agents[&id].active_pane,
        crate::views::agent::ActivePane::Prompt
    );
}
#[test]
fn mouse_send_retires_armed_clear_so_next_esc_swallows() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        agent.prompt.textarea.set_text("draft to clear");
        agent.vim_mode = true;
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app.pending_action.as_ref().expect("arm clear");
    assert!(matches!(pending.action, Action::ClearPrompt));
    let _ = crate::app::dispatch::dispatch(Action::SendPrompt("draft to clear".into()), &mut app);
    assert!(
        app.pending_action.is_none(),
        "submit must retire the stale ClearPrompt arm",
    );
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Esc after a mouse-send must swallow mid-turn, got {outcome:?}",
    );
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::ClearPrompt)),
        "the retired ClearPrompt arm must not fire",
    );
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Esc must not cancel mid-turn",
    );
    assert!(app.agents[&id].cancel_trigger_hint.is_none());
    assert!(app.pending_action.is_none());
}
/// Arm an idle-Esc `ClearPrompt`, submit via `text`-carrying `action` (a
/// turn-start path with no intervening key), assert the arm was retired, then
/// with the turn running assert the next Esc swallows (never the stale clear).
fn assert_submit_path_retires_clear_arm(action: Action) {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        agent.prompt.textarea.set_text("draft to clear");
        agent.vim_mode = true;
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(matches!(
        app.pending_action.as_ref().expect("arm clear").action,
        Action::ClearPrompt
    ));
    let _ = crate::app::dispatch::dispatch(action, &mut app);
    assert!(
        app.pending_action.is_none(),
        "every submit path (inner funnel) must retire the stale ClearPrompt arm",
    );
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Esc after a non-keyed submit must swallow mid-turn, got {outcome:?}",
    );
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Esc must not cancel mid-turn",
    );
    assert!(app.agents[&id].cancel_trigger_hint.is_none());
    assert!(app.pending_action.is_none());
}
#[test]
fn submit_follow_up_retires_armed_clear_so_next_esc_swallows() {
    assert_submit_path_retires_clear_arm(Action::SubmitFollowUp("follow up".into()));
}
#[test]
fn slash_preserving_send_retires_armed_clear_so_next_esc_swallows() {
    assert_submit_path_retires_clear_arm(Action::SendSlashCommandPreservingDraft(
        "/compact".into(),
    ));
}
#[test]
fn stale_idle_clear_arm_never_fires_on_busy_agent() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        agent.prompt.textarea.set_text("draft to clear");
        agent.vim_mode = true;
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(matches!(
        app.pending_action.as_ref().expect("arm clear").action,
        Action::ClearPrompt
    ));
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Esc on a busy agent must swallow, not fire the stale clear arm, got {outcome:?}",
    );
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::ClearPrompt)),
        "the stale ClearPrompt arm must not fire on a running turn",
    );
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Esc must not cancel mid-turn",
    );
    assert!(app.agents[&id].cancel_trigger_hint.is_none());
    assert!(
        app.pending_action.is_none(),
        "the stale arm must be dropped"
    );
}
#[test]
fn stale_idle_rewind_arm_never_fires_on_busy_agent() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        agent.vim_mode = true;
        agent
            .scrollback
            .push_block(crate::scrollback::block::RenderBlock::user_prompt(
                "earlier",
            ));
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(matches!(
        app.pending_action.as_ref().expect("arm rewind").action,
        Action::RewindShowPicker
    ));
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Esc on a busy agent must swallow, not fire the stale rewind arm, got {outcome:?}",
    );
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "Esc must not cancel mid-turn",
    );
    assert!(
        app.pending_action.is_none(),
        "the stale arm must be dropped"
    );
}
#[test]
fn stale_idle_clear_arm_never_fires_on_wake_turn() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        agent.prompt.textarea.set_text("draft to clear");
        agent.vim_mode = true;
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(matches!(
        app.pending_action.as_ref().expect("arm clear").action,
        Action::ClearPrompt
    ));
    app.agents
        .get_mut(&id)
        .unwrap()
        .note_streaming_wake_turn("p-wake");
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "Esc on a wake turn must swallow, not fire the stale clear arm, got {outcome:?}",
    );
    assert!(app.agents[&id].cancel_trigger_hint.is_none());
    assert!(
        app.pending_action.is_none(),
        "the stale arm must be dropped"
    );
}
#[test]
fn esc_consumed_by_policy_disarms_esc_d_combo() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        assert!(agent.scrollback.is_empty());
        assert!(agent.prompt.textarea.text().is_empty());
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        app.agents[&id].esc_pressed_at.is_none(),
        "idle-empty swallow Esc must disarm the Esc→d combo",
    );
    let mut app = test_app_with_agent();
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        agent.vim_mode = true;
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        app.agents[&id].esc_pressed_at.is_none(),
        "mid-turn swallow Esc must disarm the Esc→d combo",
    );
}
#[test]
fn idle_non_empty_esc_ttl_expiry_re_arms_without_clearing() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt.textarea.set_text("still here");
    let _ = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    if let Some(p) = app.pending_action.as_mut() {
        p.expires_at = std::time::Instant::now() - std::time::Duration::from_millis(1);
    }
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(
        app.agents[&id].prompt.textarea.text(),
        "still here",
        "expired first Esc must not clear"
    );
    let pending = app.pending_action.as_ref().expect("re-arm clear");
    assert_eq!(pending.label, Some("clear"));
}
#[test]
fn idle_images_only_double_esc_arms_clear() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent
        .prompt
        .images
        .push(crate::prompt_images::from_clipboard_data(
            &crate::clipboard::ImageData {
                data: vec![1, 2, 3],
                mime_type: "image/png".into(),
            },
        ));
    assert!(agent.prompt.textarea.text().is_empty());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    let pending = app.pending_action.as_ref().expect("arm clear for images");
    assert!(matches!(pending.action, Action::ClearPrompt));
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::ClearPrompt)));
    assert!(app.pending_action.is_none());
    let effects = crate::app::dispatch::dispatch(Action::ClearPrompt, &mut app);
    assert!(effects.is_empty());
    assert!(
        app.agents[&id].prompt.images.is_empty(),
        "second Esc must clear the image chips"
    );
    assert!(
        app.agents[&id].session.prompt_history.is_empty(),
        "an images-only (empty-text) clear records nothing in prompt history"
    );
}
/// Scrollback-pane double-Esc, idle + empty prompt + messages: first Esc
/// arms `RewindShowPicker` silently, second within the TTL opens the
/// picker. Driven per scrollback nav mode because the routing differs —
/// vim resolves through `lookup_with_mode(vim=true)`, non-vim adds the
/// bare-letter forward-to-prompt fallback — and neither may consume Esc.
fn assert_scrollback_double_esc_opens_rewind(vim: bool) {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.vim_mode = vim;
    agent.set_input_mode(if vim {
        crate::views::agent::InputMode::Vim
    } else {
        crate::views::agent::InputMode::Simple
    });
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    agent
        .scrollback
        .push_block(crate::scrollback::block::RenderBlock::user_prompt(
            "earlier",
        ));
    assert!(agent.prompt.textarea.text().is_empty());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "vim={vim}: first scrollback Esc must arm silently, got {outcome:?}"
    );
    let pending = app
        .pending_action
        .as_ref()
        .expect("scrollback-pane idle Esc must arm rewind");
    assert!(
        pending.label.is_none(),
        "vim={vim}: first Esc for rewind must be silent"
    );
    assert!(matches!(pending.action, Action::RewindShowPicker));
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Action(Action::RewindShowPicker)),
        "vim={vim}: second Esc from scrollback must open the rewind picker, got {outcome:?}"
    );
    assert!(app.pending_action.is_none());
}
/// Non-vim (simple) scrollback nav: double-Esc from scrollback opens rewind.
#[test]
fn idle_scrollback_pane_double_esc_opens_rewind() {
    assert_scrollback_double_esc_opens_rewind(false);
}
/// Vim scrollback nav consumes no plain Esc, so the same flow must work.
#[test]
fn idle_scrollback_pane_double_esc_opens_rewind_vim_mode() {
    assert_scrollback_double_esc_opens_rewind(true);
}
/// From the SCROLLBACK pane an idle Esc with a draft in the (unfocused)
/// composer arms NOTHING and leaves the draft intact: clear is skipped by
/// the prompt-pane gate, and rewind is skipped by the global
/// empty-composer gate even with turns present — never clear or
/// rewind-stash a draft the reader has scrolled past. The Esc is
/// swallowed (no pending, no global quit/back-out).
#[test]
fn idle_scrollback_pane_esc_with_draft_and_messages_swallows() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    agent
        .scrollback
        .push_block(crate::scrollback::block::RenderBlock::user_prompt(
            "earlier",
        ));
    agent
        .prompt
        .textarea
        .set_text("draft while reading scrollback");
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        app.pending_action.is_none(),
        "scrollback-pane Esc with a draft must arm neither clear nor rewind"
    );
    assert_eq!(
        app.agents[&id].prompt.textarea.text(),
        "draft while reading scrollback",
        "scrollback-pane Esc must leave the composer draft intact"
    );
}
/// A pending needs-input overlay blocks the scrollback rewind arm: the
/// overlay intercepts exempt the scrollback pane, so its Esc reaches the
/// policy — which must swallow rather than arm a picker that would
/// key-starve the pending overlay. The overlay must survive the Esc.
#[test]
fn idle_scrollback_pane_esc_with_pending_input_overlay_does_not_arm_rewind() {
    type OverlayInstaller = (&'static str, fn(&mut AgentView));
    let installers: [OverlayInstaller; 1] = [
        ("question_view", |a| {
            let stashed = a.prompt.stash();
            a.question_view = Some(crate::views::question_view::QuestionViewState::new(
                "call-q".into(),
                vec![],
                stashed,
            ));
        }),
    ];
    for (name, install) in installers {
        let mut app = test_app_with_agent();
        let id = super::super::agent::AgentId(0);
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Scrollback;
        agent
            .scrollback
            .push_block(crate::scrollback::block::RenderBlock::user_prompt(
                "earlier",
            ));
        assert!(agent.prompt.textarea.text().is_empty());
        install(agent);
        assert!(
            !agent.no_input_overlay_pending(),
            "{name}: fixture must have a pending needs-input overlay"
        );
        let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
        assert!(
            matches!(outcome, InputOutcome::Changed),
            "{name}: scrollback Esc under a pending overlay must swallow, got {outcome:?}"
        );
        assert!(
            app.pending_action.is_none(),
            "{name}: must not arm rewind under a pending needs-input overlay"
        );
        assert!(
            !app.agents[&id].no_input_overlay_pending(),
            "{name}: the pending overlay must survive the swallowed Esc"
        );
    }
}
/// A latent Bash/Remember composer mode blocks the scrollback rewind arm: a rewind restore must not drop conversation text into a still-armed
/// `!` composer. The Esc must swallow WITHOUT exiting the mode: mode exit stays a prompt-pane (step 0e) affordance.
#[test]
fn idle_scrollback_pane_esc_in_bash_mode_does_not_arm_rewind() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    agent.prompt_input_mode = crate::app::agent_view::PromptInputMode::Bash;
    agent
        .scrollback
        .push_block(crate::scrollback::block::RenderBlock::user_prompt(
            "earlier",
        ));
    assert!(agent.prompt.textarea.text().is_empty());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "scrollback Esc with a latent bash composer must swallow, got {outcome:?}"
    );
    assert!(
        app.pending_action.is_none(),
        "must not arm rewind while the composer is in bash mode"
    );
    assert_eq!(
        app.agents[&id].prompt_input_mode,
        crate::app::agent_view::PromptInputMode::Bash,
        "scrollback Esc must not exit the composer mode either"
    );
}
/// An active prompt history search blocks the scrollback rewind arm — the
/// step 0b intercept is prompt-pane-only, so a scrollback Esc reaches the
/// policy while the search overlay is open and must swallow rather than
/// stack a rewind arm under it. The search must survive the Esc.
#[test]
fn idle_scrollback_pane_esc_with_history_search_does_not_arm_rewind() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent
        .scrollback
        .push_block(crate::scrollback::block::RenderBlock::user_prompt(
            "earlier",
        ));
    agent.session.prompt_history = vec!["earlier".into()];
    assert!(agent.prompt.textarea.text().is_empty());
    let history = agent.combined_prompt_history();
    let current_text = agent.prompt.text().to_string();
    assert!(
        agent
            .prompt
            .history_search
            .activate(&history, &current_text)
    );
    agent.active_pane = crate::views::agent::ActivePane::Scrollback;
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "scrollback Esc with an open history search must swallow, got {outcome:?}"
    );
    assert!(
        app.pending_action.is_none(),
        "must not arm rewind while history search is open"
    );
    assert!(
        app.agents[&id].prompt.history_search.is_active(),
        "scrollback Esc must not dismiss the search either"
    );
}
#[test]
fn running_slash_dropdown_esc_dismisses_not_cancel() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt.set_text("/he");
    agent.prompt.refresh_slash(&agent.session.models);
    assert!(
        agent.prompt.slash_open(),
        "precondition: slash dropdown open"
    );
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(app.pending_action.is_none());
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "slash Esc must steal, not cancel"
    );
    assert!(app.agents[&id].session.state.is_turn_running());
    assert!(!app.agents[&id].prompt.slash_open());
}
#[test]
fn running_bash_mode_empty_esc_exits_mode_not_cancel() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.state = AgentState::TurnRunning;
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    agent.prompt_input_mode = crate::app::agent_view::PromptInputMode::Bash;
    assert!(agent.prompt.textarea.text().is_empty());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::CancelTurn)),
        "empty bash Esc exits mode, does not cancel while running"
    );
    assert_eq!(
        app.agents[&id].prompt_input_mode,
        crate::app::agent_view::PromptInputMode::Normal
    );
    assert!(app.agents[&id].session.state.is_turn_running());
}
#[test]
fn tab_from_prompt_follows_screen_mode_registry() {
    let id = super::super::agent::AgentId(0);
    for mode in [ScreenMode::Fullscreen, ScreenMode::Inline] {
        let mut app = test_app_with_agent();
        app.screen_mode = mode;
        app.registry = ActionRegistry::defaults_for(mode);
        app.agents.get_mut(&id).unwrap().active_pane = crate::views::agent::ActivePane::Prompt;
        let outcome = app.handle_input(&key_event(KeyCode::Tab, KeyModifiers::NONE));
        assert!(matches!(
            outcome,
            InputOutcome::Action(Action::FocusScrollback)
        ));
    }
    let mut minimal = test_app_with_agent();
    minimal.screen_mode = ScreenMode::Minimal;
    minimal.registry = ActionRegistry::defaults_for(ScreenMode::Minimal);
    minimal.agents.get_mut(&id).unwrap().active_pane = crate::views::agent::ActivePane::Prompt;
    let outcome = minimal.handle_input(&key_event(KeyCode::Tab, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Unchanged));
    assert_eq!(
        minimal.agents[&id].active_pane,
        crate::views::agent::ActivePane::Prompt
    );
}
#[test]
fn prompt_focused_printable_chars_still_go_to_textarea() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    let _ = app.handle_input(&key_event(KeyCode::Char('a'), KeyModifiers::NONE));
    let agent = app.agents.get(&id).unwrap();
    assert_eq!(agent.prompt.textarea.text(), "a");
}
#[test]
fn prompt_focused_question_mark_with_shift_still_goes_to_textarea() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_pane = crate::views::agent::ActivePane::Prompt;
    let outcome = app.handle_input(&key_event(KeyCode::Char('?'), KeyModifiers::SHIFT));
    assert!(
        !matches!(outcome, InputOutcome::Changed if app.agents.get(&id).unwrap().active_modal.is_some()),
        "?+SHIFT must not open the command palette when typing in the prompt; got {outcome:?}",
    );
    let agent = app.agents.get(&id).unwrap();
    assert!(
        agent.active_modal.is_none(),
        "?+SHIFT must not open any modal in the prompt",
    );
    assert!(
        agent.prompt.textarea.text().contains('?'),
        "?+SHIFT must reach the textarea as `?`; got {:?}",
        agent.prompt.textarea.text(),
    );
}
#[test]
fn prompt_focused_bare_text_chars_promote_no_action() {
    for ch in ['p', 'b', '/', '?', '1', '5', 'm', 'o', 'c', 'h'] {
        let mut app = test_app_with_agent();
        let id = super::super::agent::AgentId(0);
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        let _ = app.handle_input(&key_event(KeyCode::Char(ch), KeyModifiers::NONE));
        let agent = app.agents.get(&id).unwrap();
        assert!(
            agent.active_modal.is_none(),
            "bare `{ch}` must not open any modal",
        );
        assert!(
            agent.prompt.textarea.text().contains(ch),
            "bare `{ch}` must reach the textarea; got {:?}",
            agent.prompt.textarea.text(),
        );
        let mut app = test_app_with_agent();
        let agent = app.agents.get_mut(&id).unwrap();
        agent.active_pane = crate::views::agent::ActivePane::Prompt;
        let _ = app.handle_input(&key_event(KeyCode::Char(ch), KeyModifiers::SHIFT));
        let agent = app.agents.get(&id).unwrap();
        assert!(
            agent.active_modal.is_none(),
            "shift+`{ch}` must not open any modal",
        );
    }
}
#[test]
fn welcome_pending_l_triggers_login() {
    let mut app = test_app();
    app.auth_state = AuthState::Pending { error: None };
    app.welcome_prompt_focused = false;
    let outcome = app.handle_input(&key_event(KeyCode::Char('l'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::Login)));
}
#[test]
fn welcome_pending_enter_triggers_login() {
    let mut app = test_app();
    app.auth_state = AuthState::Pending { error: None };
    app.welcome_prompt_focused = false;
    let outcome = app.handle_input(&key_event(KeyCode::Enter, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::Login)));
}
#[test]
fn welcome_pending_n_is_unchanged() {
    let mut app = test_app();
    app.auth_state = AuthState::Pending { error: None };
    app.welcome_prompt_focused = false;
    let outcome = app.handle_input(&key_event(KeyCode::Char('n'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Unchanged));
}
#[test]
fn welcome_done_n_starts_session() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    let outcome = app.handle_input(&key_event(KeyCode::Char('n'), KeyModifiers::NONE));
    assert!(matches!(
        outcome,
        InputOutcome::ActionThenForward(Action::NewSession)
    ));
}
#[test]
fn welcome_done_ctrl_w_opens_new_worktree_dialog() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.cwd_has_git_ancestor = true;
    let outcome = app.handle_input(&key_event(KeyCode::Char('w'), KeyModifiers::CONTROL));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::OpenNewWorktreeDialog)
    ));
}
#[test]
fn welcome_ctrl_v_creates_normal_session() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.welcome_prompt_focused = true;
    let outcome = app.handle_input(&key_event(KeyCode::Char('v'), KeyModifiers::CONTROL));
    assert!(matches!(
        outcome,
        InputOutcome::ActionThenForward(Action::NewSession)
    ));
}
#[test]
fn welcome_cmd_v_creates_normal_session() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.welcome_prompt_focused = true;
    let outcome = app.handle_input(&key_event(KeyCode::Char('v'), KeyModifiers::SUPER));
    assert!(matches!(
        outcome,
        InputOutcome::ActionThenForward(Action::NewSession)
    ));
}
#[test]
fn worktree_dialog_enter_creates_worktree_session() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.new_worktree_dialog = Some(NewWorktreeDialogState::new());
    let outcome = app.handle_input(&key_event(KeyCode::Enter, KeyModifiers::NONE));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::NewWorktreeSession {
            load_session_id: None,
            label: None,
            git_ref: None,
        })
    ));
    assert!(app.new_worktree_dialog.is_none());
}
#[test]
fn worktree_dialog_modified_enter_is_ignored() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.new_worktree_dialog = Some(NewWorktreeDialogState::new());
    let outcome = app.handle_input(&key_event(KeyCode::Enter, KeyModifiers::CONTROL));
    assert!(matches!(outcome, InputOutcome::Unchanged));
    assert!(app.new_worktree_dialog.is_some());
    let outcome = app.handle_input(&key_event(KeyCode::Char('w'), KeyModifiers::SHIFT));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label(), "W");
}
#[test]
fn worktree_dialog_enter_threads_label() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.new_worktree_dialog = Some(NewWorktreeDialogState::new());
    for c in "wolves".chars() {
        app.handle_input(&key_event(KeyCode::Char(c), KeyModifiers::NONE));
    }
    let outcome = app.handle_input(&key_event(KeyCode::Enter, KeyModifiers::NONE));
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::NewWorktreeSession {
            load_session_id: None,
            label: Some(ref l),
            git_ref: None,
        }) if l == "wolves"
    ));
    assert!(app.new_worktree_dialog.is_none());
}
#[test]
fn worktree_dialog_esc_closes() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.new_worktree_dialog = Some(NewWorktreeDialogState::new());
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(app.new_worktree_dialog.is_none());
}
#[test]
fn worktree_dialog_typing_updates_label() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    app.new_worktree_dialog = Some(NewWorktreeDialogState::new());
    let outcome = app.handle_input(&key_event(KeyCode::Char('h'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label(), "h");
    let outcome = app.handle_input(&key_event(KeyCode::Char('i'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label(), "hi");
}
#[test]
fn worktree_dialog_backspace_removes_char() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    let mut dialog = NewWorktreeDialogState::new();
    dialog.set_label("test");
    app.new_worktree_dialog = Some(dialog);
    let outcome = app.handle_input(&key_event(KeyCode::Backspace, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label(), "tes");
}
#[test]
fn worktree_dialog_enforces_byte_cap_for_typing_and_middle_paste() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    let mut dialog = NewWorktreeDialogState::new();
    dialog.set_label("a".repeat(98));
    let _ = dialog.set_cursor_byte(1);
    app.new_worktree_dialog = Some(dialog);
    let outcome = app.handle_input(&Event::Paste("éx".to_owned()));
    assert!(matches!(outcome, InputOutcome::Changed));
    let dialog = app.new_worktree_dialog.as_ref().unwrap();
    assert_eq!(dialog.label().len(), 100);
    assert_eq!(&dialog.label()[1.."aé".len()], "é");
    let outcome = app.handle_input(&key_event(KeyCode::Char('中'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label().len(), 100);
    let mut dialog = NewWorktreeDialogState::new();
    dialog.set_label("a".repeat(99));
    app.new_worktree_dialog = Some(dialog);
    let _ = app.handle_input(&key_event(KeyCode::Char('é'), KeyModifiers::NONE));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label().len(), 99);
}
#[test]
fn worktree_dialog_paste_is_scoped_away_from_welcome_prompt() {
    let mut app = test_app();
    app.auth_state = AuthState::Done;
    let mut dialog = NewWorktreeDialogState::new();
    dialog.set_label("ab");
    let _ = dialog.set_cursor_byte(1);
    app.new_worktree_dialog = Some(dialog);
    let outcome = app.handle_input(&Event::Paste("中".to_owned()));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.new_worktree_dialog.as_ref().unwrap().label(), "a中b");
    assert!(app.welcome_prompt.text().is_empty());
}
#[test]
fn authenticating_loopback_esc_quits() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
}
#[test]
fn authenticating_command_esc_quits() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Command,
    };
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Action(Action::Quit)));
}
/// Regression (user report): 'q' must type into the auth-code input,
/// not quit.
#[test]
fn authenticating_loopback_q_types_into_code_input() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    let outcome = app.handle_input(&key_event(KeyCode::Char('q'), KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "typing 'q' must edit the auth code input, got {outcome:?}"
    );
    assert_eq!(app.auth_code_input.text(), "q");
}
/// Users reflex-type the displayed device code; bare 'q' must not abort.
#[test]
fn authenticating_device_and_command_q_does_not_quit() {
    for mode in [AuthMode::Device, AuthMode::Command] {
        let mut app = test_app();
        app.auth_state = AuthState::Authenticating {
            request_seq: 1,
            handle: None,
            auth_url: None,
            mode,
        };
        let outcome = app.handle_input(&key_event(KeyCode::Char('q'), KeyModifiers::NONE));
        assert!(
            matches!(outcome, InputOutcome::Unchanged),
            "bare 'q' must not quit during {mode:?} auth, got {outcome:?}"
        );
    }
}
/// Advertised cancel keys must survive the bare-'q' removal.
#[test]
fn authenticating_advertised_cancel_keys_still_quit() {
    for mode in [AuthMode::Loopback, AuthMode::Device, AuthMode::Command] {
        for (code, mods) in [
            (KeyCode::Char('q'), KeyModifiers::CONTROL),
            (KeyCode::Char('c'), KeyModifiers::CONTROL),
            (KeyCode::Esc, KeyModifiers::NONE),
        ] {
            let mut app = test_app();
            app.auth_state = AuthState::Authenticating {
                request_seq: 1,
                handle: None,
                auth_url: None,
                mode,
            };
            let outcome = app.handle_input(&key_event(code, mods));
            assert!(
                matches!(outcome, InputOutcome::Action(Action::Quit)),
                "{code:?}+{mods:?} must still quit during {mode:?} auth, got {outcome:?}"
            );
        }
    }
}
#[test]
fn authenticating_loopback_char_mutates_input() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    let outcome = app.handle_input(&key_event(KeyCode::Char('a'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.auth_code_input.text(), "a");
}
#[test]
fn authenticating_loopback_readline_control_chords_are_ignored() {
    for code in [KeyCode::Char('u'), KeyCode::Char('d')] {
        let mut app = test_app();
        app.auth_state = AuthState::Authenticating {
            request_seq: 1,
            handle: None,
            auth_url: None,
            mode: AuthMode::Loopback,
        };
        app.auth_code_input.set_text("token");
        let outcome = app.handle_input(&key_event(code, KeyModifiers::CONTROL));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(app.auth_code_input.text(), "token");
    }
}
#[cfg(target_os = "windows")]
#[test]
fn authenticating_loopback_altgr_char_mutates_input() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    let outcome = app.handle_input(&key_event(
        KeyCode::Char('@'),
        KeyModifiers::CONTROL | KeyModifiers::ALT,
    ));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.auth_code_input.text(), "@");
}
#[test]
fn authenticating_loopback_backspace_removes_char() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    app.auth_code_input.set_text("ab");
    let outcome = app.handle_input(&key_event(KeyCode::Backspace, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.auth_code_input.text(), "a");
}
#[test]
fn authenticating_loopback_paste_appends_text() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    app.auth_code_input.set_text("tok");
    let outcome = app.handle_input(&Event::Paste("en_value".to_string()));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.auth_code_input.text(), "token_value");
}
#[test]
fn authenticating_loopback_cursor_edit_and_paste_stay_scoped() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    app.auth_code_input.set_text("ab");
    let _ = app.handle_input(&key_event(KeyCode::Left, KeyModifiers::NONE));
    let _ = app.handle_input(&Event::Paste("中\r\n".to_owned()));
    assert_eq!(app.auth_code_input.text(), "a中b");
    assert!(app.welcome_prompt.text().is_empty());
    let _ = app.handle_input(&key_event(KeyCode::Delete, KeyModifiers::NONE));
    assert_eq!(app.auth_code_input.text(), "a中");
}
#[test]
fn authenticating_loopback_uses_canonical_super_v_paste() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    crate::clipboard::set_clipboard_probe_hook(crate::clipboard::ClipboardProbeHook::no_raster(
        Some("secret\r\n"),
    ));
    let outcome = app.handle_input(&key_event(KeyCode::Char('v'), KeyModifiers::SUPER));
    crate::clipboard::clear_clipboard_probe_hook();
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(app.auth_code_input.text(), "secret");
    assert!(app.welcome_prompt.text().is_empty());
}
#[test]
fn authenticating_loopback_enter_empty_is_noop() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    app.auth_code_input.set_text("   ");
    let outcome = app.handle_input(&key_event(KeyCode::Enter, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Unchanged));
}
#[test]
fn authenticating_loopback_enter_with_content_submits() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Loopback,
    };
    app.auth_code_input.set_text(" token123 ");
    let outcome = app.handle_input(&key_event(KeyCode::Enter, KeyModifiers::NONE));
    match outcome {
        InputOutcome::Action(Action::SubmitAuthCode(code)) => {
            assert_eq!(code, "token123");
        }
        other => panic!("expected SubmitAuthCode, got {:?}", other),
    }
}
/// A bare `Moved` after a press means the release was lost: the press
/// must end, never promote into a selection.
#[test]
fn moved_after_press_ends_gesture_instead_of_promoting() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent
        .scrollback
        .push_block(crate::scrollback::RenderBlock::agent_message(
            "hello world this should wrap across lines",
        ));
    agent.scrollback.prepare_layout(40, 10);
    let mut buf = ratatui::buffer::Buffer::empty(ratatui::layout::Rect::new(0, 0, 40, 20));
    let _ = agent.draw(
        ratatui::layout::Rect::new(0, 0, 40, 20),
        &mut buf,
        &ActionRegistry::defaults(),
        &mut crate::scrollback::render::ScratchBuffer::new(),
        None,
        false,
        crate::app::agent_view::BannerSlotParams::none(),
        &BundleState::default(),
        &mut Vec::new(),
        crate::app::agent_view::AppRenderParams::default(),
    );
    let hit = agent
        .last_scrollback_selection_model
        .ranges
        .first()
        .and_then(|range| range.lines.first())
        .cloned()
        .expect("expected selectable markdown line");
    let down_col = hit.screen_x + hit.selectable_cols.start;
    let row = hit.screen_y.min(9);
    let move_col = down_col + 1;
    let down = left_mouse(MouseEventKind::Down(MouseButton::Left), down_col, row);
    let moved = left_mouse(MouseEventKind::Moved, move_col, row);
    assert!(matches!(app.handle_input(&down), InputOutcome::Changed));
    let agent = app.agents.get(&id).unwrap();
    assert!(agent.pending_text_drag.is_some());
    assert!(agent.drag_selection.is_none());
    assert!(matches!(app.handle_input(&moved), InputOutcome::Changed));
    let agent = app.agents.get(&id).unwrap();
    assert!(!agent.left_mouse_down, "lost release ended the press");
    assert!(agent.pending_text_drag.is_none());
    assert!(agent.drag_selection.is_none(), "hover must not select");
}
#[test]
fn moved_without_button_does_not_promote_pending_scrollback_drag() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent
        .scrollback
        .push_block(crate::scrollback::RenderBlock::agent_message(
            "hello world this should wrap across lines",
        ));
    agent.scrollback.prepare_layout(40, 10);
    let mut buf = ratatui::buffer::Buffer::empty(ratatui::layout::Rect::new(0, 0, 40, 20));
    let _ = agent.draw(
        ratatui::layout::Rect::new(0, 0, 40, 20),
        &mut buf,
        &ActionRegistry::defaults(),
        &mut crate::scrollback::render::ScratchBuffer::new(),
        None,
        false,
        crate::app::agent_view::BannerSlotParams::none(),
        &BundleState::default(),
        &mut Vec::new(),
        crate::app::agent_view::AppRenderParams::default(),
    );
    let hit = agent
        .last_scrollback_selection_model
        .ranges
        .first()
        .and_then(|range| range.lines.first())
        .cloned()
        .expect("expected selectable markdown line");
    let down_col = hit.screen_x + hit.selectable_cols.start;
    let row = hit.screen_y.min(9);
    let move_col = down_col + 1;
    let down = left_mouse(MouseEventKind::Down(MouseButton::Left), down_col, row);
    let up = left_mouse(MouseEventKind::Up(MouseButton::Left), down_col, row);
    let moved = left_mouse(MouseEventKind::Moved, move_col, row);
    assert!(matches!(app.handle_input(&down), InputOutcome::Changed));
    assert!(matches!(app.handle_input(&up), InputOutcome::Changed));
    let agent = app.agents.get(&id).unwrap();
    assert!(!agent.left_mouse_down);
    assert!(agent.pending_text_drag.is_none());
    assert!(agent.drag_selection.is_none());
    let outcome = app.handle_input(&moved);
    assert!(matches!(
        outcome,
        InputOutcome::Unchanged | InputOutcome::Changed
    ));
    let agent = app.agents.get(&id).unwrap();
    assert!(agent.drag_selection.is_none());
}
#[test]
fn scrollback_click_still_selects_entry_on_mouse_up() {
    let mut app = test_app_with_agent();
    let id = super::super::agent::AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent
        .scrollback
        .push_block(crate::scrollback::RenderBlock::agent_message("hello world"));
    agent.scrollback.prepare_layout(40, 10);
    let mut buf = ratatui::buffer::Buffer::empty(ratatui::layout::Rect::new(0, 0, 40, 20));
    let _ = agent.draw(
        ratatui::layout::Rect::new(0, 0, 40, 20),
        &mut buf,
        &ActionRegistry::defaults(),
        &mut crate::scrollback::render::ScratchBuffer::new(),
        None,
        false,
        crate::app::agent_view::BannerSlotParams::none(),
        &BundleState::default(),
        &mut Vec::new(),
        crate::app::agent_view::AppRenderParams::default(),
    );
    let hit = agent
        .last_scrollback_selection_model
        .ranges
        .first()
        .and_then(|range| range.lines.first())
        .cloned()
        .expect("expected selectable markdown line");
    let click_col = hit.screen_x + hit.selectable_cols.start;
    let click_row = hit.screen_y;
    let down = left_mouse(
        MouseEventKind::Down(MouseButton::Left),
        click_col,
        click_row,
    );
    let up = left_mouse(MouseEventKind::Up(MouseButton::Left), click_col, click_row);
    assert!(matches!(app.handle_input(&down), InputOutcome::Changed));
    assert!(matches!(app.handle_input(&up), InputOutcome::Changed));
    let selected_after = app.agents.get(&id).unwrap().scrollback.selected();
    assert_eq!(selected_after, Some(0));
}
fn make_test_warning() -> crate::startup::StartupWarning {
    crate::startup::StartupWarning {
        severity: crate::startup::WarningSeverity::Warning,
        message: "test warning".to_string(),
        action: Some("run /terminal-setup".to_string()),
    }
}
#[test]
fn welcome_d_starts_session_when_no_warnings() {
    let mut app = test_app();
    app.welcome_prompt_focused = true;
    app.startup_warnings = vec![];
    let outcome = app.handle_input(&key_event(KeyCode::Char('d'), KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::ActionThenForward(Action::NewSession)),
        "Expected NewSession when no warnings, got {outcome:?}"
    );
}
#[test]
fn welcome_other_char_starts_session_even_with_warnings() {
    let mut app = test_app();
    app.welcome_prompt_focused = true;
    app.startup_warnings = vec![make_test_warning()];
    let outcome = app.handle_input(&key_event(KeyCode::Char('a'), KeyModifiers::NONE));
    assert!(
        matches!(outcome, InputOutcome::ActionThenForward(Action::NewSession)),
        "Expected NewSession for 'a' even with warnings, got {outcome:?}"
    );
}
#[test]
fn merge_escapes_both_some_concatenates() {
    let result = AppView::merge_escapes(
        Some("notif".into()),
        Some(crate::terminal::overlay::PostFlush::plain("render".into())),
    );
    assert_eq!(
        result.as_ref().map(|post| post.as_str()),
        Some("notifrender")
    );
}
#[test]
fn merge_escapes_only_notif() {
    let result = AppView::merge_escapes(Some("notif".into()), None);
    assert_eq!(result.as_ref().map(|post| post.as_str()), Some("notif"));
}
#[test]
fn merge_escapes_only_render() {
    let result = AppView::merge_escapes(
        None,
        Some(crate::terminal::overlay::PostFlush::plain("render".into())),
    );
    assert_eq!(result.as_ref().map(|post| post.as_str()), Some("render"));
}
#[test]
fn merge_escapes_both_none() {
    let result = AppView::merge_escapes(None, None);
    assert!(result.is_none());
}
#[test]
fn worktree_mode_round_trip_ask() {
    let mode = WorktreeMode::from_config_str("ask");
    assert_eq!(mode, WorktreeMode::Ask);
    assert_eq!(mode.as_config_str(), "ask");
}
#[test]
fn worktree_mode_round_trip_always() {
    let mode = WorktreeMode::from_config_str("always");
    assert_eq!(mode, WorktreeMode::Always);
    assert_eq!(mode.as_config_str(), "always");
}
#[test]
fn worktree_mode_round_trip_never() {
    let mode = WorktreeMode::from_config_str("never");
    assert_eq!(mode, WorktreeMode::Never);
    assert_eq!(mode.as_config_str(), "never");
}
#[test]
fn worktree_mode_unrecognised_falls_back_to_never() {
    assert_eq!(WorktreeMode::from_config_str("alway"), WorktreeMode::Never);
    assert_eq!(WorktreeMode::from_config_str(""), WorktreeMode::Never);
    assert_eq!(WorktreeMode::from_config_str("ALWAYS"), WorktreeMode::Never);
}
/// Helper: parse a TOML string and return the document.
fn parse_toml(s: &str) -> toml_edit::DocumentMut {
    s.parse::<toml_edit::DocumentMut>().expect("valid TOML")
}
#[test]
fn resolve_from_hints_no_keys_returns_defaults() {
    let doc = parse_toml("");
    let (new_s, fork) = WorktreeMode::resolve_from_hints(doc.get("hints"));
    assert_eq!(new_s, WorktreeMode::Never);
    assert_eq!(fork, WorktreeMode::Ask);
}
#[test]
fn resolve_from_hints_legacy_key_sets_both() {
    let doc = parse_toml("[hints]\nworktree_mode = \"always\"\n");
    let (new_s, fork) = WorktreeMode::resolve_from_hints(doc.get("hints"));
    assert_eq!(new_s, WorktreeMode::Always);
    assert_eq!(fork, WorktreeMode::Always);
}
#[test]
fn resolve_from_hints_per_command_keys_override_legacy() {
    let doc = parse_toml(
        "[hints]\n\
         worktree_mode = \"always\"\n\
         new_session_worktree_mode = \"never\"\n\
         fork_worktree_mode = \"ask\"\n",
    );
    let (new_s, fork) = WorktreeMode::resolve_from_hints(doc.get("hints"));
    assert_eq!(new_s, WorktreeMode::Never);
    assert_eq!(fork, WorktreeMode::Ask);
}
#[test]
fn resolve_from_hints_only_per_command_keys() {
    let doc = parse_toml(
        "[hints]\n\
         new_session_worktree_mode = \"ask\"\n\
         fork_worktree_mode = \"never\"\n",
    );
    let (new_s, fork) = WorktreeMode::resolve_from_hints(doc.get("hints"));
    assert_eq!(new_s, WorktreeMode::Ask);
    assert_eq!(fork, WorktreeMode::Never);
}
#[test]
fn resolve_from_hints_one_per_command_key_other_falls_back_to_legacy() {
    let doc = parse_toml(
        "[hints]\n\
         worktree_mode = \"always\"\n\
         fork_worktree_mode = \"never\"\n",
    );
    let (new_s, fork) = WorktreeMode::resolve_from_hints(doc.get("hints"));
    assert_eq!(new_s, WorktreeMode::Always);
    assert_eq!(fork, WorktreeMode::Never);
}
#[test]
fn resolve_from_hints_one_per_command_key_other_falls_back_to_default() {
    let doc = parse_toml("[hints]\nnew_session_worktree_mode = \"always\"\n");
    let (new_s, fork) = WorktreeMode::resolve_from_hints(doc.get("hints"));
    assert_eq!(new_s, WorktreeMode::Always);
    assert_eq!(fork, WorktreeMode::Ask);
}
fn scroll_event(kind: MouseEventKind, column: u16, row: u16) -> Event {
    Event::Mouse(MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    })
}
#[test]
fn scroll_event_stashes_origin_for_residual_flush() {
    let mut app = test_app();
    assert!(app.last_scroll_pos.is_none());
    let _ = app.handle_input(&scroll_event(MouseEventKind::ScrollDown, 42, 17));
    assert_eq!(app.last_scroll_pos, Some((42, 17)));
    let _ = app.handle_input(&scroll_event(MouseEventKind::ScrollUp, 7, 3));
    assert_eq!(app.last_scroll_pos, Some((7, 3)));
}
#[test]
fn scroll_event_does_not_stash_when_blocking_modal_open() {
    let mut app = test_app();
    app.new_worktree_dialog = Some(NewWorktreeDialogState::new());
    assert!(app.is_scroll_blocking_modal_open());
    let _ = app.handle_input(&scroll_event(MouseEventKind::ScrollDown, 42, 17));
    assert!(
        app.last_scroll_pos.is_none(),
        "scroll events must be ignored while a scroll-blocking modal is open",
    );
}
#[test]
fn welcome_privacy_banner_hover_triggers_redraw() {
    let mut app = test_app();
    app.active_view = ActiveView::Welcome;
    app.welcome_privacy_banner_opt_in_rect = Some(ratatui::layout::Rect::new(50, 10, 8, 1));
    app.welcome_privacy_banner_opt_out_rect = Some(ratatui::layout::Rect::new(25, 10, 24, 1));
    app.welcome_privacy_banner_terms_rect = Some(ratatui::layout::Rect::new(7, 11, 5, 1));
    app.welcome_privacy_banner_policy_rect = Some(ratatui::layout::Rect::new(17, 11, 14, 1));
    let over = left_mouse(MouseEventKind::Moved, 52, 10);
    assert!(matches!(app.handle_input(&over), InputOutcome::Changed));
    assert!(app.welcome_on_privacy_banner);
    let cross = left_mouse(MouseEventKind::Moved, 30, 10);
    assert!(matches!(app.handle_input(&cross), InputOutcome::Changed));
    assert!(app.welcome_on_privacy_banner);
    let over_legal = left_mouse(MouseEventKind::Moved, 10, 11);
    assert!(matches!(
        app.handle_input(&over_legal),
        InputOutcome::Changed
    ));
    assert!(app.welcome_on_privacy_banner);
    let leave = left_mouse(MouseEventKind::Moved, 5, 5);
    assert!(matches!(app.handle_input(&leave), InputOutcome::Changed));
    assert!(!app.welcome_on_privacy_banner);
    assert!(matches!(app.handle_input(&leave), InputOutcome::Unchanged));
}
#[test]
fn welcome_doc_viewer_is_scroll_blocking_and_wheel_scrolls_content() {
    let mut app = test_app();
    app.active_view = ActiveView::Welcome;
    app.welcome_doc_viewer = Some(crate::views::modal::ActiveModal::DocViewer {
        title: "Guide".into(),
        content: "line\n".repeat(80),
        scroll: 0,
        window: crate::views::modal_window::ModalWindowState::new(),
        cached_lines: None,
        previous_palette: None,
        standalone: true,
    });
    assert!(
        app.is_scroll_blocking_modal_open(),
        "welcome doc-viewer overlay must block background scroll",
    );
    let outcome = app.handle_input(&scroll_event(MouseEventKind::ScrollDown, 40, 12));
    assert!(
        matches!(outcome, InputOutcome::Changed),
        "wheel must be handled by the doc viewer",
    );
    assert!(
        app.last_scroll_pos.is_none(),
        "wheel must not reach the background scroll path while the doc viewer is open",
    );
    let scroll = match app.welcome_doc_viewer.as_ref() {
        Some(crate::views::modal::ActiveModal::DocViewer { scroll, .. }) => *scroll,
        _ => panic!("expected DocViewer"),
    };
    assert!(scroll > 0, "wheel must advance doc scroll, got {scroll}");
}
#[test]
fn tutorial_is_scroll_blocking_and_wheel_scrolls_topic() {
    let mut app = test_app();
    app.active_view = ActiveView::Welcome;
    let mut tut = crate::views::tutorial::TutorialState::new();
    let _ = crate::views::tutorial::handle_tutorial_input(
        &Event::Key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
        &mut tut,
    );
    app.tutorial = Some(tut);
    assert!(
        app.is_scroll_blocking_modal_open(),
        "tutorial overlay must block background scroll",
    );
    let outcome = app.handle_input(&scroll_event(MouseEventKind::ScrollDown, 40, 12));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        app.last_scroll_pos.is_none(),
        "wheel must not reach the background scroll path while the tutorial is open",
    );
    let tut = app.tutorial.as_ref().expect("tutorial stays open");
    assert!(
        tut.scroll > 0,
        "wheel must advance topic scroll, got {}",
        tut.scroll
    );
}
#[test]
fn tutorial_esc_on_list_closes_overlay() {
    let mut app = test_app();
    app.active_view = ActiveView::Welcome;
    app.tutorial = Some(crate::views::tutorial::TutorialState::new());
    let outcome = app.handle_input(&Event::Key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        app.tutorial.is_none(),
        "Esc on the list closes the tutorial"
    );
}
/// Esc with a voice cold-start still queued (pipeline spawning, mic not yet
/// open) must cancel it so the event loop doesn't open the mic after the user
/// backed out — even though `voice_listening` is still false.
#[test]
fn esc_cancels_pending_voice_cold_start() {
    let mut app = test_app_with_agent();
    pin_non_vscode_registry(&mut app);
    app.voice_state = VoiceState::ColdStart {
        hold: false,
        target: VoiceTarget::Agent(super::super::agent::AgentId(0)),
    };
    let outcome = app.handle_input(&key_event(KeyCode::Esc, KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(
        !app.voice_state.pending_cold_start(),
        "Esc must cancel the queued cold-start"
    );
    assert!(
        app.voice_recording_target().is_none(),
        "target dropped on cancel"
    );
}
/// The dictation overlay must only render on the surface that owns the bound
/// target. After an explicit stop the interim is kept (`Stopping`) for a
/// trailing final, so navigating away must not flash it on the wrong box.
#[test]
fn voice_overlay_bound_to_target_surface() {
    let id = super::super::agent::AgentId(0);
    let mut app = test_app();
    app.voice_state = VoiceState::Stopping {
        target: VoiceTarget::Agent(id),
        interim: Some("partial".into()),
    };
    app.active_view = ActiveView::Agent(id);
    assert!(
        app.voice_target_on_active_surface(),
        "overlay shows on the agent that owns the dictation"
    );
    app.active_view = ActiveView::Welcome;
    assert!(
        !app.voice_target_on_active_surface(),
        "overlay hidden once the user navigates off the target surface"
    );
}
/// Dictation into the active agent's prompt must stay on-surface and the
/// bind-enforcer must not auto-stop it.
#[test]
fn voice_target_on_active_agent_stays_bound() {
    let id = super::super::agent::AgentId(0);
    let mut app = test_app();
    app.voice_state = VoiceState::Recording {
        hold: false,
        target: VoiceTarget::Agent(id),
        interim: None,
    };
    app.active_view = ActiveView::Agent(id);
    assert!(app.voice_target_on_active_surface());
    app.enforce_voice_session_bound();
    assert!(
        app.voice_listening(),
        "dictation on the active agent must not auto-stop the mic"
    );
}
#[test]
fn minimal_double_ctrl_c_arms_then_quits() {
    let prev = crate::app::minimal_mode_active();
    crate::app::set_minimal_mode_active_for_test(true);
    let mut app = test_app_with_agent();
    if let ActiveView::Agent(id) = app.active_view {
        app.agents.get_mut(&id).unwrap().active_pane = crate::views::agent::ActivePane::Prompt;
    }
    let o1 = app.handle_input(&key_event(KeyCode::Char('c'), KeyModifiers::CONTROL));
    let armed = app.pending_action.is_some();
    let o2 = app.handle_input(&key_event(KeyCode::Char('c'), KeyModifiers::CONTROL));
    crate::app::set_minimal_mode_active_for_test(prev);
    assert!(armed, "first Ctrl+C should arm quit (o1={o1:?})");
    assert!(
        matches!(o2, InputOutcome::Action(crate::app::actions::Action::Quit)),
        "second Ctrl+C should quit (o2={o2:?})"
    );
}
/// Chat mode hides the welcome picker's source filter, so `f` must not
/// cycle it; Build mode keeps the cycle.
#[test]
fn welcome_picker_f_cycle_disabled_under_chat_mode() {
    let conversation_entry = SessionPickerEntry {
        id: "conv-welcome-f".into(),
        summary: "chat".into(),
        updated_at: chrono::Utc::now(),
        created_at: chrono::Utc::now(),
        cwd: String::new(),
        hostname: None,
        source: "conversation".into(),
        model_id: None,
        num_messages: 0,
        last_active_at: None,
        branch: None,
        repo_name: "r".into(),
        worktree_label: None,
        last_turn_summary: None,
        last_recap: None,
        card_detail: None,
    };
    let f_key = Event::Key(KeyEvent::new(KeyCode::Char('f'), KeyModifiers::NONE));
    crate::appearance::cache::set_vim_mode(false);
    let mut app = test_app();
    app.session_picker_entries = Some(vec![conversation_entry]);
    app.chat_mode = true;
    let _ = app.handle_input(&f_key);
    assert_eq!(
        app.session_picker_source_filter,
        crate::views::session_picker::SourceFilter::Grok,
        "f must not cycle the hidden source filter under chat mode"
    );
    assert_eq!(
        app.session_picker_state.query(),
        "f",
        "under chat mode `f` keeps its normal typing/search meaning"
    );
    app.session_picker_state.reset();
    app.chat_mode = false;
    let outcome = app.handle_input(&f_key);
    assert!(matches!(
        outcome,
        InputOutcome::Action(Action::CycleSessionSourceFilter)
    ));
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_ctrl_e_cycles_workspace_mode() {
    use crate::views::welcome::WelcomeWorkspaceMode;
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    assert_eq!(app.welcome_workspace_mode, WelcomeWorkspaceMode::Sandbox);
    let key = Event::Key(KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL));
    let outcome = app.handle_input(&key);
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(
        app.welcome_workspace_mode,
        WelcomeWorkspaceMode::LocalWorkspace
    );
    let _ = app.handle_input(&key);
    assert_eq!(app.welcome_workspace_mode, WelcomeWorkspaceMode::Sandbox);
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_ack_cancel_clears_history_bypass() {
    use crate::views::welcome::WelcomeWorkspaceMode;
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.welcome_local_workspace_ack_pending = true;
    app.welcome_workspace_mode = WelcomeWorkspaceMode::LocalWorkspace;
    app.welcome_history_load_as_build = true;
    app.deferred_startup.worktree = true;
    app.deferred_startup.history_load_as_build = true;
    let outcome = app.handle_input(&key_event(KeyCode::Char('n'), KeyModifiers::NONE));
    assert!(matches!(outcome, InputOutcome::Changed));
    assert!(!app.welcome_local_workspace_ack_pending);
    assert_eq!(app.welcome_workspace_mode, WelcomeWorkspaceMode::Sandbox);
    assert!(
        !app.welcome_history_load_as_build,
        "ACK cancel must drop history bypass"
    );
    assert!(!app.deferred_startup.history_load_as_build);
    assert!(!app.deferred_startup.worktree);
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_workspace_click_selects_mode() {
    use crate::views::welcome::{WelcomeWorkspaceMode, WorkspaceModeHitRects};
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.welcome_workspace_mode_rects = WorkspaceModeHitRects {
        options: [
            Some(ratatui::layout::Rect::new(10, 5, 9, 1)),
            Some(ratatui::layout::Rect::new(20, 5, 17, 1)),
        ],
        row: Some(ratatui::layout::Rect::new(0, 5, 80, 1)),
    };
    let click = Event::Mouse(crossterm::event::MouseEvent {
        kind: crossterm::event::MouseEventKind::Down(crossterm::event::MouseButton::Left),
        column: 25,
        row: 5,
        modifiers: KeyModifiers::NONE,
    });
    let outcome = app.handle_input(&click);
    assert!(matches!(outcome, InputOutcome::Changed));
    assert_eq!(
        app.welcome_workspace_mode,
        WelcomeWorkspaceMode::LocalWorkspace
    );
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_workspace_locked_ignores_cycle_and_click() {
    use crate::views::welcome::{WelcomeWorkspaceMode, WorkspaceModeHitRects};
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.local_workspace_startup_locked = true;
    app.welcome_workspace_mode = WelcomeWorkspaceMode::LocalWorkspace;
    app.welcome_workspace_mode_rects = WorkspaceModeHitRects {
        options: [
            Some(ratatui::layout::Rect::new(10, 5, 9, 1)),
            Some(ratatui::layout::Rect::new(20, 5, 17, 1)),
        ],
        row: Some(ratatui::layout::Rect::new(0, 5, 80, 1)),
    };
    let key = Event::Key(KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL));
    assert!(matches!(
        app.handle_input(&key),
        InputOutcome::Unchanged | InputOutcome::Changed
    ));
    assert_eq!(
        app.welcome_workspace_mode,
        WelcomeWorkspaceMode::LocalWorkspace
    );
    let click = Event::Mouse(crossterm::event::MouseEvent {
        kind: crossterm::event::MouseEventKind::Down(crossterm::event::MouseButton::Left),
        column: 12,
        row: 5,
        modifiers: KeyModifiers::NONE,
    });
    let _ = app.handle_input(&click);
    assert_eq!(
        app.welcome_workspace_mode,
        WelcomeWorkspaceMode::LocalWorkspace,
        "locked picker must not change selection"
    );
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_ctrl_e_ignored_while_history_picker_open() {
    use crate::views::welcome::WelcomeWorkspaceMode;
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.session_picker_entries = Some(vec![]);
    app.session_picker_state.set_query("keep-me");
    let before = app.welcome_workspace_mode;
    let key = Event::Key(KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL));
    let outcome = app.handle_input(&key);
    assert_eq!(app.welcome_workspace_mode, before);
    assert!(
        !matches!(outcome, InputOutcome::Action(Action::ForceDeepSearch)),
        "history open: Ctrl+E must not cycle or soft-refresh: {outcome:?}"
    );
    assert_eq!(app.session_picker_state.query(), "keep-me");
    let _ = WelcomeWorkspaceMode::Sandbox;
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_ctrl_e_ignored_while_authenticating() {
    use crate::views::welcome::WelcomeWorkspaceMode;
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Command,
    };
    app.trust_state = TrustState::Done;
    assert_eq!(app.welcome_workspace_mode, WelcomeWorkspaceMode::Sandbox);
    let key = Event::Key(KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL));
    let _ = app.handle_input(&key);
    assert_eq!(
        app.welcome_workspace_mode,
        WelcomeWorkspaceMode::Sandbox,
        "Ctrl+E must not cycle mode before auth is Done"
    );
}
#[cfg(feature = "local-workspace")]
#[test]
fn welcome_ctrl_e_ignored_when_zdr_blocked() {
    use crate::views::welcome::WelcomeWorkspaceMode;
    let mut app = test_app();
    app.chat_mode = true;
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.is_zdr = true;
    app.zdr_access_enabled = false;
    assert_eq!(app.welcome_workspace_mode, WelcomeWorkspaceMode::Sandbox);
    let key = Event::Key(KeyEvent::new(KeyCode::Char('e'), KeyModifiers::CONTROL));
    let _ = app.handle_input(&key);
    assert_eq!(
        app.welcome_workspace_mode,
        WelcomeWorkspaceMode::Sandbox,
        "Ctrl+E must not cycle mode on ZDR-blocked welcome"
    );
}
