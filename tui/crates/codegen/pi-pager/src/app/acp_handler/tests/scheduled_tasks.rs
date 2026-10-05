#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn created_upserts_existing_chip_preserving_identity_and_linkage() {
        let mut app = make_app_with_agent("sess-1");
        let original_created_at = Instant::now() - std::time::Duration::from_secs(60);
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            agent.session.scheduled_tasks.insert(
                "task-up".into(),
                crate::app::agent::ScheduledTaskInfo {
                    task_id: "task-up".into(),
                    prompt: "old prompt".into(),
                    human_schedule: "every 5 minutes".into(),
                    created_at: original_created_at,
                    next_fire_at: Some("2026-01-01T00:00:00Z".into()),
                    tag: "loop".into(),
                    last_subagent_id: Some("sub-abc".into()),
                },
            );
        }

        let notif = make_created_ext_notif(
            "sess-1",
            "task-up",
            "new prompt",
            "every 10 minutes",
            Some("2026-02-02T02:02:02Z"),
        );
        assert!(handle_scheduled_task_created(&notif, &mut app));

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(agent.session.scheduled_tasks.len(), 1, "no duplicate chip");
        let info = agent.session.scheduled_tasks.get("task-up").unwrap();
        assert_eq!(info.prompt, "new prompt");
        assert_eq!(info.human_schedule, "every 10 minutes");
        assert_eq!(info.next_fire_at.as_deref(), Some("2026-02-02T02:02:02Z"));
        assert_eq!(
            info.created_at, original_created_at,
            "chip identity (countdown anchor) preserved"
        );
        assert_eq!(
            info.last_subagent_id.as_deref(),
            Some("sub-abc"),
            "click-through linkage preserved across an update"
        );
    }

    #[test]
    fn created_updates_correct_agent_when_active_view_differs() {
        let mut app = make_app_two_agents();
        let notif = make_created_ext_notif(
            "sess-owner",
            "task-new",
            "check PR",
            "every 5m",
            Some("2026-06-01T12:00:00Z"),
        );
        let needs_redraw = handle_scheduled_task_created(&notif, &mut app);
        assert!(
            !needs_redraw,
            "non-active agent mutation should not trigger redraw"
        );

        let agent0 = app.agents.get(&AgentId(0)).unwrap();
        assert!(
            agent0.session.scheduled_tasks.contains_key("task-new"),
            "task must be created on the owning agent"
        );

        let agent1 = app.agents.get(&AgentId(1)).unwrap();
        assert!(
            agent1.session.scheduled_tasks.is_empty(),
            "non-owning agent must not receive the task"
        );
    }

