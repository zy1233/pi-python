#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn parked_completions_push_chips_without_markers() {
        use crate::app::agent_view::test_fixtures::{count_turn_markers, simulate_task_output_wait};

        let mut app = make_app_with_agent("sess-park");
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            agent.session.state = AgentState::TurnRunning;
            agent.session.current_prompt_id = Some("p1".into());
            insert_running_task(agent, "t10", "sleep 10");
            insert_running_task(agent, "t15", "sleep 15");
            insert_running_task(agent, "t20", "sleep 20");
            simulate_task_output_wait(agent, "t20");
            assert!(agent.renders_parked());
        }

        handle_ext_notification(
            &make_task_completed_notif("sess-park", "t10", "sleep 10", Some(0)),
            &mut app,
        );
        // Duplicate completion for the same task: not a Running→Done edge.
        handle_ext_notification(
            &make_task_completed_notif("sess-park", "t10", "sleep 10", Some(0)),
            &mut app,
        );
        handle_ext_notification(
            &make_task_completed_notif("sess-park", "t15", "sleep 15", Some(0)),
            &mut app,
        );
        handle_ext_notification(
            &make_task_completed_notif("sess-park", "t20", "sleep 20", Some(0)),
            &mut app,
        );

        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        assert_eq!(
            count_turn_markers(agent),
            0,
            "completions during a park never write a marker"
        );
        assert!(
            work_status_lines(&agent.scrollback).is_empty(),
            "no work-only status lines in the transcript"
        );
    }

    #[test]
    fn consecutive_subagent_finishes_stay_markerless() {
        use crate::app::agent_view::test_fixtures::count_turn_markers;

        let mut app = make_app_with_agent("sess-park");
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            park_on_subagents(agent, &["child-1", "child-2", "child-3"]);
        }

        for child in ["child-1", "child-1", "child-2", "child-3"] {
            handle(
                make_ext_session_notification("sess-park", test_subagent_finished(child)),
                &mut app,
            );
        }
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        assert_eq!(
            count_turn_markers(agent),
            0,
            "subagent finishes never write a marker mid-park"
        );
    }

    #[test]
    fn repark_after_parent_output_stays_markerless() {
        use crate::acp::meta::NotificationMeta;
        use crate::app::agent_view::test_fixtures::{
            complete_task_output_wait_call, count_turn_markers, simulate_task_output_wait_call,
        };

        let mut app = make_app_with_agent("sess-park");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.session.current_prompt_id = Some("p1".into());
        insert_running_task(agent, "t10", "sleep 10");

        simulate_task_output_wait_call(agent, "wait-1", "t10", 30_000);
        assert!(agent.renders_parked());
        assert_eq!(count_turn_markers(agent), 0);

        complete_task_output_wait_call(agent, "wait-1");
        assert!(agent.session.tracker.handle_update(
            acp::SessionUpdate::AgentMessageChunk(acp::ContentChunk::new(
                acp::ContentBlock::Text(acp::TextContent::new("between-parks content")),
            )),
            &NotificationMeta::default(),
            &mut agent.scrollback,
        ));

        simulate_task_output_wait_call(agent, "wait-2", "t10", 30_000);
        assert!(agent.renders_parked(), "the re-park renders parked again");
        assert_eq!(count_turn_markers(agent), 0, "and still writes no marker");
    }

    #[test]
    fn interjection_notification_for_unknown_session_is_ignored() {
        let mut app = make_app_with_agent("sess-view");
        let affected = handle_ext_notification(&interjection_ext("sess-other", "stray"), &mut app);
        assert!(!affected, "an unmatched session must be a no-op");

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert!(
            last_interjection_text(&agent.scrollback).is_none(),
            "no interjection block must be pushed for an unknown session"
        );
    }

