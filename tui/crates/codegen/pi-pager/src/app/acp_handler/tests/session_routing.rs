#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn acp_chunk_for_inactive_agent_lands_in_its_scrollback() {
        // Regression: switching away from a streaming agent must not
        // discard chunks bound for that agent. Before this fix, only
        // `TaskResult::PromptResponse` survived, so the user saw a bare
        // "Worked for X.Xs" with no body text.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));

        let affected = handle(make_agent_chunk_message("sess-A", "hello from A"), &mut app);

        let agent_a = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent_message_text(agent_a),
            "hello from A",
            "chunk for inactive agent A must land in A's scrollback"
        );
        assert!(
            !affected,
            "chunk routed to a non-active agent must not request a redraw"
        );
        let agent_b = app.agents.get(&AgentId(1)).unwrap();
        assert!(
            agent_b.scrollback.is_empty(),
            "active agent B's scrollback must remain untouched"
        );
    }

    #[test]
    fn acp_chunk_for_active_agent_returns_affected_true() {
        // Baseline: chunk for the visible agent triggers a redraw.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));

        let affected = handle(make_agent_chunk_message("sess-B", "hello from B"), &mut app);

        assert!(affected, "chunk for active agent must request a redraw");
        let agent_b = app.agents.get(&AgentId(1)).unwrap();
        assert_eq!(agent_message_text(agent_b), "hello from B");
    }

    #[test]
    fn acp_chunk_with_unknown_session_id_is_dropped_and_no_redraw() {
        // No agent owns the session_id and the active agent already has a
        // session_id assigned (so the race-window fallback does not fire).
        // The notification must be dropped silently.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        // make_app_with_agent already activated AgentId(0); no switch needed.

        let affected = handle(
            make_agent_chunk_message("sess-unknown", "stray text"),
            &mut app,
        );

        assert!(!affected, "unknown session_id must not request a redraw");
        assert!(
            app.agents.get(&AgentId(0)).unwrap().scrollback.is_empty(),
            "agent A must not have absorbed a notification for sess-unknown"
        );
        assert!(
            app.agents.get(&AgentId(1)).unwrap().scrollback.is_empty(),
            "agent B must not have absorbed a notification for sess-unknown"
        );
    }

    #[test]
    fn session_id_none_race_window_routes_to_active_agent() {
        // Pin the existing race-window semantics: notifications that arrive
        // before `TaskResult::SessionCreated` (active agent has no session_id
        // yet) must still land on the active agent.

        // Case 1: active agent A has session_id == None; everyone else has
        // a real id. Stray notification routes to A.
        {
            let mut app = make_app_with_agent("sess-A");
            app.agents.get_mut(&AgentId(0)).unwrap().session.session_id = None;
            insert_agent(&mut app, AgentId(1), Some("sess-B"));
            // make_app_with_agent already activated AgentId(0); no switch needed.

            let _ = handle(
                make_agent_chunk_message("not-yet-assigned", "racing chunk"),
                &mut app,
            );

            assert_eq!(
                agent_message_text(app.agents.get(&AgentId(0)).unwrap()),
                "racing chunk",
                "race-window fallback should land on active agent A"
            );
            assert!(
                app.agents.get(&AgentId(1)).unwrap().scrollback.is_empty(),
                "non-active agent B must not absorb the race chunk"
            );
        }

        // Case 2: both A and B have session_id == None; the active one wins.
        {
            let mut app = make_app_with_agent("sess-A");
            app.agents.get_mut(&AgentId(0)).unwrap().session.session_id = None;
            insert_agent(&mut app, AgentId(1), None);
            switch_active_to(&mut app, AgentId(1));

            let _ = handle(
                make_agent_chunk_message("not-yet-assigned", "racing chunk"),
                &mut app,
            );

            assert!(
                app.agents.get(&AgentId(0)).unwrap().scrollback.is_empty(),
                "non-active agent A must not absorb the race chunk"
            );
            assert_eq!(
                agent_message_text(app.agents.get(&AgentId(1)).unwrap()),
                "racing chunk",
                "race-window fallback must prefer the active agent (B)"
            );
        }
    }

    #[test]
    fn plan_update_for_inactive_agent_lands_in_its_todo() {
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));
        // Sanity: A's todo starts empty.
        assert_eq!(
            app.agents.get(&AgentId(0)).unwrap().todo.counts().total(),
            0,
        );

        let _ = handle(make_plan_message("sess-A", &["task1", "task2"]), &mut app);

        let agent_a = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent_a.todo.counts().total(),
            2,
            "Plan update must mutate A's todo even when B is active"
        );
        let agent_b = app.agents.get(&AgentId(1)).unwrap();
        assert_eq!(
            agent_b.todo.counts().total(),
            0,
            "active agent B's todo must not absorb A's plan"
        );
    }

    #[test]
    fn commands_update_for_inactive_agent_bumps_its_generation() {
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));
        let initial_gen_a = app
            .agents
            .get(&AgentId(0))
            .unwrap()
            .session
            .available_commands_generation;

        let _ = handle(
            make_commands_update_message("sess-A", &["compact", "fork"]),
            &mut app,
        );

        let agent_a = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent_a.session.available_commands.len(),
            2,
            "AvailableCommandsUpdate must replace A's commands list"
        );
        assert_eq!(
            agent_a.session.available_commands_generation,
            initial_gen_a + 1,
            "AvailableCommandsUpdate must bump A's generation counter"
        );
    }

    #[test]
    fn acp_chunks_for_two_agents_dont_cross_contaminate() {
        // Send chunks to both A and B in sequence; each landing in its own
        // scrollback proves the demux works in both directions regardless
        // of which agent is currently active.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        switch_active_to(&mut app, AgentId(1));

        let _ = handle(make_agent_chunk_message("sess-A", "A only"), &mut app);
        let _ = handle(make_agent_chunk_message("sess-B", "B only"), &mut app);

        assert_eq!(
            agent_message_text(app.agents.get(&AgentId(0)).unwrap()),
            "A only",
        );
        assert_eq!(
            agent_message_text(app.agents.get(&AgentId(1)).unwrap()),
            "B only",
        );
    }

