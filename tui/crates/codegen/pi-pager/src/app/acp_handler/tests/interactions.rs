#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn interaction_resolved_dismisses_matching_permission() {
        // A peer answered a shared permission → this pane retracts its copy.
        let mut app = make_app_with_agent("sess-1");
        let (msg, _rx) = make_permission_message("sess-1");
        handle(msg, &mut app);
        assert_eq!(app.agents[&AgentId(0)].permission_queue.len(), 1);

        let changed = handle_session_notification(
            &interaction_resolved_ext("sess-1", "call-perm-1"),
            &mut app,
        );
        assert!(changed, "dismissing a visible permission must redraw");
        assert!(
            app.agents[&AgentId(0)].permission_queue.is_empty(),
            "the resolved permission must be removed from the queue"
        );
    }

    #[test]
    fn interaction_resolved_dismisses_matching_question() {
        use crate::views::question_view::QuestionViewState;
        let mut app = make_app_with_agent("sess-1");
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            let stashed = agent.prompt.stash();
            agent.question_view = Some(QuestionViewState::new("call-q".into(), vec![], stashed));
        }

        let changed =
            handle_session_notification(&interaction_resolved_ext("sess-1", "call-q"), &mut app);
        assert!(changed, "dismissing a visible question must redraw");
        assert!(
            app.agents[&AgentId(0)].question_view.is_none(),
            "the resolved question must be cleared"
        );
    }

    #[test]
    fn interaction_resolved_is_noop_for_unknown_tool_call_id() {
        let mut app = make_app_with_agent("sess-1");
        let (msg, _rx) = make_permission_message("sess-1");
        handle(msg, &mut app);

        let changed = handle_session_notification(
            &interaction_resolved_ext("sess-1", "some-other-call"),
            &mut app,
        );
        assert!(!changed, "an unknown tool_call_id must be a silent no-op");
        assert_eq!(
            app.agents[&AgentId(0)].permission_queue.len(),
            1,
            "an unrelated pending modal must be left intact"
        );
    }

    #[test]
    fn permission_for_inactive_agent_queues_on_owning_agent() {
        // The headline behavior change in handle_permission_request:
        // permissions for an inactive owning agent now QUEUE (not cancel)
        // so the user sees them on switching back.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));

        let (msg, mut rx) = make_permission_message("sess-A");
        let affected = handle(msg, &mut app);

        let agent_a = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent_a.permission_queue.len(),
            1,
            "permission for inactive A must queue on A's permission_queue"
        );
        let agent_b = app.agents.get(&AgentId(1)).unwrap();
        assert_eq!(
            agent_b.permission_queue.len(),
            0,
            "active B's permission_queue must remain empty"
        );
        assert!(
            !affected,
            "permission queued on a non-active agent must not request a redraw"
        );
        // Permission is still pending; the response_tx must still be alive
        // (no auto-cancel was sent).
        assert!(
            rx.try_recv().is_err(),
            "permission must NOT have been answered yet (queued, not cancelled)"
        );
    }

    #[test]
    fn exec_vehicle_permission_enqueues_a_persisting_default_scope() {
        // Regression guard for the enqueue invariant: an exec-vehicle bash
        // prompt that offers the scoped "Always allow:" row must open on a
        // default scope that persists a grant — the full command, not a bare
        // `python3` prefix (which the ←/→ arrows could not repair).
        use std::sync::Arc;
        use pi_workspace::permission::bash_command_splitting::BashCommandHighlights;

        let mut app = make_app_with_agent("sess-1");
        let highlights = BashCommandHighlights {
            prefix: vec![],
            highlighted_words: vec![
                "python3".to_owned(),
                "-u".to_owned(),
                "foo.py".to_owned(),
                "arg".to_owned(),
            ],
            suffix: vec![],
        };
        let meta = serde_json::to_value(&highlights).unwrap().as_object().cloned();

        let (tx, _rx) = tokio::sync::oneshot::channel();
        let request = acp::RequestPermissionRequest::new(
            acp::SessionId::new("sess-1"),
            acp::ToolCallUpdate::new(
                acp::ToolCallId::new(Arc::from("call-perm-exec")),
                acp::ToolCallUpdateFields::default(),
            ),
            vec![
                acp::PermissionOption::new(
                    acp::PermissionOptionId::new(Arc::from("allow-once")),
                    "Allow once",
                    acp::PermissionOptionKind::AllowOnce,
                ),
                acp::PermissionOption::new(
                    acp::PermissionOptionId::new(Arc::from("allow-always-command")),
                    "Always allow",
                    acp::PermissionOptionKind::AllowAlways,
                ),
            ],
        )
        .meta(meta);
        let msg = AcpClientMessage::RequestPermission(pi_acp_lib::AcpArgs {
            request,
            response_tx: tx,
        });

        handle(msg, &mut app);

        let agent = app.agents.get(&AgentId(0)).unwrap();
        let perm = agent.permission_queue.front().expect("permission queued");
        assert_eq!(
            perm.bash_selection_count, 4,
            "exec vehicle must open on the full-command scope"
        );
        assert!(
            pi_workspace::permission::always_allow_scope_persists(
                perm.bash_highlights.as_ref().unwrap(),
                perm.bash_selection_count,
            ),
            "the enqueue default scope must persist a grant"
        );
    }

    #[test]
    fn permission_for_inactive_yolo_agent_auto_approves() {
        // YOLO mode is honored on the OWNING agent, not the active one,
        // so background turns aren't blocked waiting for a switch.
        let mut app = make_app_with_agent("sess-A");
        app.agents.get_mut(&AgentId(0)).unwrap().session.yolo_mode = true;
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));

        let (msg, rx) = make_permission_message("sess-A");
        let affected = handle(msg, &mut app);

        assert!(!affected, "YOLO auto-approve never needs a redraw");
        let agent_a = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent_a.permission_queue.len(),
            0,
            "YOLO must auto-approve in place of queueing"
        );
        let response = rx
            .blocking_recv()
            .expect("YOLO must have sent a response on response_tx");
        let resp = response.expect("YOLO response must be Ok");
        match resp.outcome {
            acp::RequestPermissionOutcome::Selected(acp::SelectedPermissionOutcome {
                option_id,
                ..
            }) => {
                assert_eq!(option_id.0.as_ref(), "allow-once");
            }
            other => panic!("expected Selected, got {other:?}"),
        }
    }

    #[test]
    fn permission_for_unknown_session_id_is_cancelled() {
        // No agent owns the session and the active agent already has a
        // session_id (so the race-window fallback does not fire). The
        // permission must be cancelled rather than queued anywhere.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        // make_app_with_agent already activated AgentId(0); no switch needed.

        let (msg, rx) = make_permission_message("sess-unknown");
        let affected = handle(msg, &mut app);

        assert!(!affected);
        for id in [AgentId(0), AgentId(1)] {
            assert_eq!(
                app.agents.get(&id).unwrap().permission_queue.len(),
                0,
                "no agent should have queued the unknown-session permission",
            );
        }
        let response = rx
            .blocking_recv()
            .expect("cancel_permission must have sent a response");
        let resp = response.expect("response must be Ok");
        assert!(
            matches!(resp.outcome, acp::RequestPermissionOutcome::Cancelled),
            "unknown session_id permissions must be cancelled, got {:?}",
            resp.outcome,
        );
    }

    // ── Plan approval persistence tests ─────────────────────────

    /// Delivers a status snapshot the way the agent does, and reports whether
    /// the client repainted.
    fn notify_status(app: &mut crate::app::app_view::AppView, cwd: &str) -> bool {
        let notif = SessionNotification {
            session_id: acp::SessionId::new("sess-1"),
            update: PiSessionUpdate::SessionStatus(Box::new(
                crate::app::status_line::test_context(cwd),
            )),
            meta: None,
        };
        let raw = serde_json::value::to_raw_value(&notif).unwrap();
        let ext = acp::ExtNotification::new("pi/session_notification", std::sync::Arc::from(raw));
        handle_session_notification(&ext, app)
    }

    /// Storing the snapshot paints nothing when no row is configured, and the
    /// agent pushes one at every turn end: reporting a change here would
    /// repaint the whole fleet once per turn for a row nobody draws.
    #[test]
    fn a_status_snapshot_does_not_repaint_a_client_with_no_status_line() {
        let mut app = make_app_with_agent("sess-1");
        assert!(
            !app.current_ui.status_line.reserves_a_row(),
            "disabled is the default"
        );

        assert!(!notify_status(&mut app, "/tmp"), "no row, no repaint");
        assert!(
            app.agents[&AgentId(0)].status_context.is_some(),
            "the payload is still stored for whenever a row is enabled"
        );
        assert!(app.status_line.display().is_none(), "and nothing is drawn");
    }

    /// The other half. An enabled row settles once it has drawn, and an idle
    /// session asks for no ticks, so the snapshot's own repaint is the only
    /// thing that moves the row until the next turn.
    #[test]
    fn a_status_snapshot_repaints_a_row_that_had_already_settled() {
        let mut app = make_app_with_agent("sess-1");
        app.current_ui.status_line =
            pi_status_line::test_support::StatusLineConfigFixture::from_kind(
                pi_status_line::StatusLineType::Builtin,
            )
            .with_items(vec![pi_status_line::StatusLineItem::Cwd])
            .into_config();

        assert!(
            notify_status(&mut app, "/tmp/first"),
            "the first snapshot draws"
        );
        assert!(app.status_line.is_settled(), "a drawn row settles");
        assert_eq!(
            app.status_line_tick_demand(),
            crate::app::app_view::TickDemand::None,
            "an idle settled row asks for no ticks, so only the snapshot can move it"
        );

        // Inside the refresh floor the snapshot defers rather than repaints, so
        // what it must leave behind is a row still asking to be recomputed.
        notify_status(&mut app, "/tmp/second");
        assert_ne!(
            app.status_line_tick_demand(),
            crate::app::app_view::TickDemand::None,
            "the snapshot left the row settled and idle, so it will never redraw"
        );

        app.update_status_line_at(
            std::time::Instant::now() + crate::app::status_line::MIN_REFRESH_INTERVAL_MS,
        );
        assert!(
            app.status_line.take_changed(),
            "the deferred recompute never happened"
        );
    }

