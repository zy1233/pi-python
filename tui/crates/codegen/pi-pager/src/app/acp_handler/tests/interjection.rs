#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

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

