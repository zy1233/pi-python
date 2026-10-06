#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    /// Regression: tool-call titles containing `"enter_plan_mode"` must not
    /// flip plan mode (the substring matcher used to brick sessions on any
    /// tool mentioning the phrase, e.g. a Grep with that pattern).
    #[test]
    fn tool_call_with_enter_plan_mode_substring_does_not_activate_plan_mode() {
        let mut agent = make_agent(Some("s1"));
        assert!(!agent.plan_mode_active);

        let updates = [
            make_tool_call("enter_plan_mode"),
            make_tool_call_update("enter_plan_mode"),
            make_tool_call("Execute `rg enter_plan_mode`"),
            make_tool_call_update("Execute `rg enter_plan_mode`"),
            make_tool_call_update("Plan mode entered"),
            make_tool_call("mcp__foo__enter_plan_mode"),
        ];
        for update in &updates {
            let refresh_needed = detect_plan_mode_change(update, &mut agent);
            assert!(
                !refresh_needed,
                "tool-call title (not a CurrentModeUpdate) must not request refresh"
            );
            assert!(
                !agent.plan_mode_active,
                "tool-call title must not flip plan mode"
            );
        }
    }

    /// Symmetric: tool-call titles containing `"exit_plan_mode"` must not
    /// deactivate plan mode either. Exit is signaled by `CurrentModeUpdate`.
    #[test]
    fn tool_call_with_exit_plan_mode_substring_does_not_deactivate_plan_mode() {
        let mut agent = make_agent(Some("s1"));
        agent.plan_mode_active = true;

        let updates = [
            make_tool_call("exit_plan_mode"),
            make_tool_call_update("exit_plan_mode"),
            make_tool_call_update("Plan mode exited"),
            make_tool_call("Execute `rg exit_plan_mode`"),
        ];
        for update in &updates {
            let refresh_needed = detect_plan_mode_change(update, &mut agent);
            assert!(!refresh_needed);
            assert!(
                agent.plan_mode_active,
                "tool-call title must not flip plan mode"
            );
        }
    }

    #[test]
    fn current_mode_update_plan_activates_plan_mode() {
        let mut agent = make_agent(Some("s1"));
        assert!(!agent.plan_mode_active);

        let refresh_needed = detect_plan_mode_change(&make_current_mode_update("plan"), &mut agent);
        assert!(refresh_needed);
        assert!(agent.plan_mode_active);
        assert!(agent.plan_mode_pending.is_none());
    }

    #[test]
    fn current_mode_update_default_deactivates_plan_mode() {
        let mut agent = make_agent(Some("s1"));
        agent.plan_mode_active = true;
        agent.plan_mode_pending = Some(true);

        let refresh_needed =
            detect_plan_mode_change(&make_current_mode_update("default"), &mut agent);
        assert!(refresh_needed);
        assert!(!agent.plan_mode_active);
        assert!(agent.plan_mode_pending.is_none());
    }

    /// Unknown mode ids (e.g. a custom agent definition name like
    /// `"browser_use"`) parse to `SessionMode::Default` and deactivate
    /// plan mode.
    #[test]
    fn current_mode_update_unknown_id_treated_as_default() {
        let mut agent = make_agent(Some("s1"));
        agent.plan_mode_active = true;

        let refresh_needed =
            detect_plan_mode_change(&make_current_mode_update("browser_use"), &mut agent);
        assert!(refresh_needed);
        assert!(!agent.plan_mode_active);
    }

    /// Idempotent CurrentModeUpdate still signals refresh because
    /// `plan_mode_pending` was cleared (affects effective state).
    #[test]
    fn current_mode_update_signals_refresh_even_on_no_op_active_change() {
        let mut agent = make_agent(Some("s1"));
        agent.plan_mode_active = true;
        agent.plan_mode_pending = Some(true);

        let refresh_needed = detect_plan_mode_change(&make_current_mode_update("plan"), &mut agent);
        assert!(
            refresh_needed,
            "CurrentModeUpdate must always signal refresh — pending was cleared"
        );
        assert!(agent.plan_mode_active);
        assert!(agent.plan_mode_pending.is_none());
    }

