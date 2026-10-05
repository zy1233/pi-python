#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

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

