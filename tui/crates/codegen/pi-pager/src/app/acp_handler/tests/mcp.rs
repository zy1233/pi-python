#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn mcp_initialized_unknown_session_is_dropped() {
        // An mcp_initialized for a session that matches no agent must not
        // clear anyone's indicator (no misrouting to the active agent).
        let mut app = make_app_with_agent("sess-A");
        app.agents.get_mut(&AgentId(0)).unwrap().mcp_init_progress =
            Some(crate::app::agent_view::McpInitProgress {
                total: 1,
                connected: 0,
                started_at: Instant::now(),
            });

        let notif = make_mcp_initialized_notif_for("sess-unknown");
        let changed = handle_ext_notification(&notif, &mut app);
        assert!(!changed);
        assert!(
            app.agents[&AgentId(0)].mcp_init_progress.is_some(),
            "unknown-session mcp_initialized must not clear the active agent",
        );
    }

    #[test]
    fn mcp_lifecycle_notif_for_subagent_session_is_dropped() {
        // A subagent runs its own MCP init, emitting init_progress /
        // mcp_initialized under the *child* session id. Those must NOT write to
        // or clear the parent agent's mcp_init_progress — it's a per-root-agent
        // indicator with no subagent slot, so a subagent's init must not
        // clobber the parent's spinner.
        let mut app = make_app_with_agent("sess-A");
        app.agents.get_mut(&AgentId(0)).unwrap().mcp_init_progress =
            Some(crate::app::agent_view::McpInitProgress {
                total: 2,
                connected: 1,
                started_at: Instant::now(),
            });
        // Register a subagent child view keyed by the child session id.
        app.agents
            .get_mut(&AgentId(0))
            .unwrap()
            .subagent_views
            .insert(
                "child-sess".to_string(),
                Box::new(make_agent(Some("child-sess"))),
            );

        // init_progress for the child session must leave the parent untouched.
        let changed = handle_ext_notification(
            &make_mcp_init_progress_notif_for(5, 0, "child-sess"),
            &mut app,
        );
        assert!(
            !changed,
            "subagent init_progress must not redraw the parent"
        );
        let p = app.agents[&AgentId(0)].mcp_init_progress.as_ref().unwrap();
        assert_eq!(
            (p.total, p.connected),
            (2, 1),
            "parent spinner must be untouched by a subagent's init",
        );

        // mcp_initialized for the child session must not clear the parent.
        let changed =
            handle_ext_notification(&make_mcp_initialized_notif_for("child-sess"), &mut app);
        assert!(!changed);
        assert!(
            app.agents[&AgentId(0)].mcp_init_progress.is_some(),
            "subagent mcp_initialized must not clear the parent's spinner",
        );
    }

    /// Pin that the pager deserializes against the *shell's*
    /// `McpServerStatus` enum, so a future new variant doesn't need a
    /// pager change to be recognized. Round-trip through
    /// `serde_json::to_string` of the shell type itself.
    #[test]
    fn server_status_round_trips_shell_canonical_type() {
        use pi_shell::extensions::mcp::{
            McpServerSource, McpServerStatus, McpServerStatusPayload, McpServerStatusReason,
        };
        let payload = McpServerStatusPayload {
            session_id: "s".into(),
            name: "alpha".into(),
            source: McpServerSource::Local,
            status: McpServerStatus::NeedsAuth,
            reason: McpServerStatusReason::AuthExpired,
            detail: Some("token expired".into()),
            tools: None,
        };
        let json = serde_json::to_string(&payload).unwrap();
        let roundtripped: McpServerStatusPayload = serde_json::from_str(&json).unwrap();
        assert_eq!(payload, roundtripped);
        // `needsAuth` must be on the wire as the lowercase form, not
        // mixed-case — verifies the rename_all = lowercase contract.
        assert!(
            json.contains("\"needsauth\""),
            "wire form must be lowercase 'needsauth'; got {json}"
        );
    }

