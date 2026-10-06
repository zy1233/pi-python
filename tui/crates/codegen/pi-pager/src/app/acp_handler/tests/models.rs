#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    /// A follower client (no in-flight switch of its own) receives the
    /// leader's `ModelChanged` broadcast and silently mirrors the new model
    /// into its local state — no scrollback entry, no toast, just enough
    /// state for the status bar / `/model` dropdown to render correctly.
    #[test]
    fn model_changed_updates_state_silently_on_follower() {
        let mut app = make_app_with_agent("sess-1");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        seed_models(agent, "grok-3", &["grok-3", "grok-4"]);
        let scrollback_before = agent.scrollback.len();
        // Follower: no local switch in flight.
        assert!(!agent.session.model_switch_pending);

        let notif = model_changed_ext("sess-1", "grok-4", None);
        let changed = handle_ext_notification(&notif, &mut app);
        assert!(
            changed,
            "follower's state changed → handler must request a redraw"
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent
                .session
                .models
                .current
                .as_ref()
                .map(|id| id.0.as_ref()),
            Some("grok-4"),
            "follower must mirror the remote switch into its local model state",
        );
        assert_eq!(
            agent.scrollback.len(),
            scrollback_before,
            "follower must NOT push a 'Switched to' scrollback entry — that is \
             the invoking client's job (SwitchModelComplete owns the system message)"
        );
        assert!(
            !agent.session.model_switch_pending,
            "follower's pending flag must stay false (no local switch was issued)"
        );
    }

    /// A live remote `ModelChanged` (leader-mode fan-out from another client)
    /// must apply even when this client already has a local
    /// `user_model_preference` — otherwise the status bar desyncs from the
    /// gateway session. Preference is updated to track the new live model.
    /// (History-replay silent-revert is suppressed on the shell side via
    /// `ReconnectState::user_selected_model`, not by permanently blocking
    /// remote ModelChanged here.)
    #[test]
    fn model_changed_applies_and_updates_user_model_preference() {
        let mut app = make_app_with_agent("sess-1");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        seed_models(agent, "heavy", &["auto", "heavy"]);
        agent.session.user_model_preference =
            Some(acp::ModelId::new(std::sync::Arc::from("heavy")));
        assert!(!agent.session.model_switch_pending);

        let notif = model_changed_ext("sess-1", "auto", None);
        let changed = handle_ext_notification(&notif, &mut app);
        assert!(
            changed,
            "remote live ModelChanged must apply despite prior local preference"
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent
                .session
                .models
                .current
                .as_ref()
                .map(|id| id.0.as_ref()),
            Some("auto"),
            "selector must mirror the remote switch"
        );
        assert_eq!(
            agent
                .session
                .user_model_preference
                .as_ref()
                .map(|id| id.0.as_ref()),
            Some("auto"),
            "preference must track the applied remote switch"
        );
    }

    /// The invoking client is also a subscriber to its own session and so
    /// receives the broadcast it triggered. Its in-flight
    /// `SetSessionModelResponse` is the authority for its local state +
    /// the single "Switched to X" scrollback entry, so the broadcast handler
    /// must be a no-op here — gated on `model_switch_pending == true`.
    ///
    /// Concretely we verify the broadcast does NOT touch
    /// `models.current` (preserving the pre-response snapshot) — that
    /// snapshot is what `SwitchModelComplete`'s `unchanged` check compares
    /// against to decide whether to render the "Switched to X" message. If
    /// the broadcast optimistically updated state here, the response
    /// handler would see `prev == new`, mark it unchanged, and suppress the
    /// user-facing message entirely.
    #[test]
    fn model_changed_skipped_when_local_switch_in_flight() {
        let mut app = make_app_with_agent("sess-1");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        seed_models(agent, "grok-3", &["grok-3", "grok-4"]);
        // Invoker: a local switch is in flight (set by Action::SwitchModel /
        // set_default_model before the SetSessionModelRequest is sent).
        agent.session.model_switch_pending = true;
        let scrollback_before = agent.scrollback.len();

        let notif = model_changed_ext("sess-1", "grok-4", None);
        let changed = handle_ext_notification(&notif, &mut app);
        assert!(
            !changed,
            "broadcast must be a no-op while local switch is pending"
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent
                .session
                .models
                .current
                .as_ref()
                .map(|id| id.0.as_ref()),
            Some("grok-3"),
            "models.current must stay at the pre-response snapshot — \
             SwitchModelComplete owns the final apply + system message"
        );
        assert_eq!(
            agent.scrollback.len(),
            scrollback_before,
            "broadcast must not push any scrollback entry on the invoker"
        );
        assert!(
            agent.session.model_switch_pending,
            "pending flag must remain set until SwitchModelComplete arrives"
        );
    }

    /// A `ModelChanged` broadcast carrying a model id the local catalog
    /// doesn't know about must be dropped — applying it would render an
    /// unresolvable id in the status bar and desync the `/model` dropdown.
    /// This can happen when leader and a follower client briefly disagree
    /// on the model catalog (etag drift, custom-model config skew).
    #[test]
    fn model_changed_dropped_when_model_unknown_to_catalog() {
        let mut app = make_app_with_agent("sess-1");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        seed_models(agent, "grok-3", &["grok-3", "grok-4"]);

        let notif = model_changed_ext("sess-1", "grok-99-unknown", None);
        let changed = handle_ext_notification(&notif, &mut app);
        assert!(
            !changed,
            "unknown model must NOT trigger a redraw — no state changed"
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent
                .session
                .models
                .current
                .as_ref()
                .map(|id| id.0.as_ref()),
            Some("grok-3"),
            "models.current must stay on the previously-known model"
        );
    }

    /// `reasoning_effort` round-trips through the broadcast: the follower
    /// applies it alongside the model id so the prompt header / status bar
    /// show the right effort without waiting for a subsequent
 /// `legacy ext RPC`.
    #[test]
    fn model_changed_applies_reasoning_effort_on_follower() {
        use pi_shell::sampling::types::ReasoningEffort;
        let mut app = make_app_with_agent("sess-1");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        seed_models(agent, "grok-3", &["grok-3", "grok-4"]);

        let notif = model_changed_ext("sess-1", "grok-4", Some("high"));
        assert!(handle_ext_notification(&notif, &mut app));

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent.session.models.reasoning_effort,
            Some(ReasoningEffort::High),
            "follower must mirror the broadcast's reasoning_effort"
        );
    }

    /// `ModelChanged` for a session this client doesn't own / hasn't loaded
    /// must be dropped — `find_session_match` returns `None`. The bug-flavored
    /// version of this would be: leader-mode A switches model on session X
    /// (which this client never opened) and we accidentally apply the change
    /// to the active agent.
    #[test]
    fn model_changed_dropped_for_unknown_session_id() {
        let mut app = make_app_with_agent("sess-1");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        seed_models(agent, "grok-3", &["grok-3", "grok-4"]);

        let notif = model_changed_ext("sess-OTHER", "grok-4", None);
        let changed = handle_ext_notification(&notif, &mut app);
        assert!(!changed);

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent
                .session
                .models
                .current
                .as_ref()
                .map(|id| id.0.as_ref()),
            Some("grok-3"),
            "unrelated-session broadcast must not touch this agent's model"
        );
    }

