#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn voice_settings_update_omitted_leaves_gate_unchanged() {
        // Unrelated settings push must not flip the gate (default-on stays on;
        // kill-switch stays off until an explicit true/false).
        let mut app = make_app_with_agent("sess-1");
        app.apply_voice_mode_enabled(true);
        let omit = acp::ExtNotification::new(
            "pi/settings/update",
            std::sync::Arc::from(
                serde_json::value::to_raw_value(&serde_json::json!({ "sharing_enabled": true }))
                    .unwrap(),
            ),
        );
        let _ = handle_ext_notification(&omit, &mut app);
        assert!(app.voice_mode_enabled);

        app.apply_voice_mode_enabled(false);
        let _ = handle_ext_notification(&omit, &mut app);
        assert!(!app.voice_mode_enabled);
    }

    /// Temporary client kill switch: remote `sharing_enabled: true` must not
    /// re-enable share UI. Agents stay off and `/share` stays menu-hidden
    /// (typed `/share` still dispatches for the disable message).
    #[ignore = "pi-python: grok-specific feature not supported"]
    #[test]
    fn settings_update_sharing_enabled_true_stays_forced_off() {
        let mut app = make_app_with_agent("sess-share-kill");
        app.sharing_enabled = true;
        for agent in app.agents.values_mut() {
            agent.set_sharing_enabled(true);
        }

        let notif = acp::ExtNotification::new(
            "pi/settings/update",
            serde_json::value::to_raw_value(&serde_json::json!({
                "sharing_enabled": true,
            }))
            .unwrap()
            .into(),
        );
        let _ = handle_ext_notification(&notif, &mut app);

        assert!(
            !app.sharing_enabled,
            "remote true must not lift the temporary kill switch"
        );
        for agent in app.agents.values() {
            assert!(!agent.sharing_enabled);
            let reg = agent.prompt.slash_controller.registry();
            assert!(
                reg.get("share").is_none(),
                "/share stays out of the completion menu"
            );
            assert!(
                reg.get_for_dispatch("share").is_some(),
                "typed /share still resolves so the disable path can run"
            );
        }
    }

    /// User-owned mode must not re-arm default_yolo or rewrite UI from remote.
    #[test]
    fn permission_mode_user_claim_blocks_default_yolo_rearm() {
        let mut app = make_app_with_agent("sess-user-claim");
        app.auto_mode_gate = true;
        app.permission_mode_from_soft_default = false;
        app.current_ui.permission_mode = Some("ask".into());
        app.default_yolo = false;

        let apply_yolo = acp::ExtNotification::new(
            "pi/settings/update",
            serde_json::value::to_raw_value(&serde_json::json!({
                "permission_mode": "always-approve",
            }))
            .unwrap()
            .into(),
        );
        let _ = handle_ext_notification(&apply_yolo, &mut app);
        assert!(
            !app.default_yolo,
            "user-claimed mode must not re-arm default_yolo from remote always-approve"
        );
        assert_eq!(
            app.current_ui.permission_mode.as_deref(),
            Some("ask"),
            "user-claimed UI must not be rewritten by remote soft-default"
        );
        assert!(
            !app.permission_mode_from_soft_default,
            "user claim origin stays false"
        );
    }

    #[test]
    fn permission_mode_omitted_does_not_clear_soft_default() {
        let mut app = make_app_with_agent("sess-omit-pm");
        app.permission_mode_from_soft_default = true;
        app.current_ui.permission_mode = Some("auto".into());
        app.default_yolo = false;
        app.auto_mode_gate = true;

        let unrelated = acp::ExtNotification::new(
            "pi/settings/update",
            serde_json::value::to_raw_value(&serde_json::json!({
                "show_resolved_model": true,
            }))
            .unwrap()
            .into(),
        );
        let _ = handle_ext_notification(&unrelated, &mut app);
        assert_eq!(
            app.current_ui.permission_mode.as_deref(),
            Some("auto"),
            "omitted permission_mode must not clear soft-applied UI mode"
        );
        assert!(
            app.permission_mode_from_soft_default,
            "origin must stay SoftDefault when field is omitted"
        );
        assert!(
            !app.default_yolo,
            "omitted permission_mode must not recompute default_yolo"
        );
    }

