#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn follow_ups_replayed_meta_suppresses_chips() {
        let mut app = make_app_with_agent("sess-1");
        let params = serde_json::json!({
            "response_id": "resp-1",
            "suggestions": [{ "label": "x" }],
            "_meta": { "replayed": true },
        });
        let notif = acp::ExtNotification::new(
            "pi/follow_ups",
            serde_json::value::to_raw_value(&params).unwrap().into(),
        );
        let affected = handle_ext_notification(&notif, &mut app);
        assert!(!affected, "a replayed chunk must not render chips");
        assert!(app.agents[&AgentId(0)].follow_ups.is_none());
    }

    #[test]
    fn follow_ups_malformed_params_are_ignored() {
        let mut app = make_app_with_agent("sess-1");
        let bad = [
            serde_json::Value::String("not an object".into()),
            serde_json::json!([1, 2, 3]),
            serde_json::json!({ "suggestions": 7 }),
            serde_json::json!({}),
        ];
        for params in bad {
            let notif = acp::ExtNotification::new(
                "pi/follow_ups",
                serde_json::value::to_raw_value(&params).unwrap().into(),
            );
            let affected = handle_ext_notification(&notif, &mut app);
            assert!(!affected, "malformed params must be ignored: {params}");
        }
        assert!(app.agents[&AgentId(0)].follow_ups.is_none());
    }

    #[test]
    fn follow_ups_empty_response_id_is_ignored() {
        let mut app = make_app_with_agent("sess-1");
        let affected = handle_ext_notification(&follow_ups_ext("", &["x"]), &mut app);
        assert!(
            !affected,
            "without a response_id there is no newest-wins key"
        );
        assert!(app.agents[&AgentId(0)].follow_ups.is_none());
    }

    #[test]
    fn follow_ups_blank_labels_yield_no_chips() {
        let mut app = make_app_with_agent("sess-1");
        let affected = handle_ext_notification(&follow_ups_ext("resp-1", &["   ", ""]), &mut app);
        assert!(!affected);
        assert!(app.agents[&AgentId(0)].follow_ups.is_none());
    }

    #[test]
    fn follow_ups_per_element_malformed_is_ignored() {
        let mut app = make_app_with_agent("sess-1");
        for bad in [
            serde_json::json!({ "response_id": "r", "suggestions": [{ "label": 7 }] }),
            serde_json::json!({ "response_id": "r", "suggestions": [null] }),
        ] {
            let notif = acp::ExtNotification::new(
                "pi/follow_ups",
                serde_json::value::to_raw_value(&bad).unwrap().into(),
            );
            assert!(
                !handle_ext_notification(&notif, &mut app),
                "a malformed suggestion element drops the notification: {bad}"
            );
        }
        assert!(app.agents[&AgentId(0)].follow_ups.is_none());
    }

    #[test]
    fn follow_ups_empty_array_renders_no_chips() {
        let mut app = make_app_with_agent("sess-1");
        let affected = handle_ext_notification(&follow_ups_ext("resp-1", &[]), &mut app);
        assert!(!affected);
        assert!(app.agents[&AgentId(0)].follow_ups.is_none());
    }

