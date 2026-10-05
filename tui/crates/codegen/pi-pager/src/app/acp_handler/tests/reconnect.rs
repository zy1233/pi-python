#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn handle_updates_total_tokens_used_for_active_agent() {
        let mut app = make_app_with_agent("sess-1");
        assert!(app.agents.get(&AgentId(0)).unwrap().context_state.is_none());

        let _ = handle(make_token_notification_message("sess-1", 12_345), &mut app);

        assert_eq!(
            app.agents
                .get(&AgentId(0))
                .unwrap()
                .context_state
                .as_ref()
                .map(|c| c.used),
            Some(12_345),
        );
    }

    #[test]
    fn handle_routes_tokens_to_root_when_session_id_not_yet_set() {
        // Regression: a notification racing ahead of TaskResult::SessionCreated
        // (session_id still None) must update the active agent, not be dropped
        // into the empty subagent_views path.
        let mut app = make_app_with_agent("sess-1");
        app.agents.get_mut(&AgentId(0)).unwrap().session.session_id = None;

        let _ = handle(make_token_notification_message("sess-1", 12_345), &mut app);

        assert_eq!(
            app.agents
                .get(&AgentId(0))
                .unwrap()
                .context_state
                .as_ref()
                .map(|c| c.used),
            Some(12_345),
        );
    }

    #[test]
    fn duplicate_event_id_is_dropped_and_highwater_advances() {
        // Each session/update carries a monotonic eventId; live + replay of the
        // same event share it. A client that receives an event twice must render
        // it once (this is what eliminates the driver-side duplication when a
        // second client opens the same session). Per-session events arrive in
        // increasing order, so the pager keeps a highwater and drops anything
        // `<=` it. Updates without an eventId still apply (back-compat).
        let mut app = make_app_with_agent("sess-dedup");
        let id = AgentId(0);

        // First event applies (active agent → affected==true) and sets highwater.
        let a1 = handle(
            make_agent_chunk_with_event("sess-dedup", "hello", "p1", Some("sess-dedup-5")),
            &mut app,
        );
        assert!(a1, "first event must apply");
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(5));

        // Exact duplicate eventId → dropped (not affected), highwater unchanged.
        let a2 = handle(
            make_agent_chunk_with_event("sess-dedup", "hello", "p1", Some("sess-dedup-5")),
            &mut app,
        );
        assert!(!a2, "a duplicate eventId must be dropped");
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(5));

        // Stale lower eventId → dropped.
        let a3 = handle(
            make_agent_chunk_with_event("sess-dedup", "hello", "p1", Some("sess-dedup-3")),
            &mut app,
        );
        assert!(!a3, "a lower (already-passed) eventId must be dropped");
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(5));

        // New higher eventId → applies, highwater advances.
        let a4 = handle(
            make_agent_chunk_with_event("sess-dedup", "world", "p1", Some("sess-dedup-9")),
            &mut app,
        );
        assert!(a4, "a new (higher) eventId must apply");
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(9));

        // No eventId (older shell) → always applies; highwater untouched.
        let a5 = handle(
            make_agent_chunk_with_event("sess-dedup", "again", "p1", None),
            &mut app,
        );
        assert!(
            a5,
            "an update without an eventId must still apply (back-compat)"
        );
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(9));
    }

    /// Regression: the per-process `eventId` counter resets each resume,
    /// so replayed history isn't monotonic — replay must bypass the dedup highwater.
    #[test]
    fn replayed_history_with_event_id_resets_does_not_break_resume() {
        let mut app = make_app_with_agent("sess-resume");
        let id = AgentId(0);
        // Replay arrives inside a `session/load` window.
        app.agents.get_mut(&id).unwrap().session.loading_replay = true;

        // eventIds climb (5, 9) then reset below the peak (2, 4): resumed twice.
        for (text, eid) in [
            ("r1-a", "sess-resume-5"),
            ("r1-b", "sess-resume-9"),
            ("r2-a", "sess-resume-2"),
            ("r2-b", "sess-resume-4"),
        ] {
            handle(
                make_agent_chunk_meta("sess-resume", text, "p1", Some(eid), true),
                &mut app,
            );
        }
        assert_eq!(
            app.agents[&id].last_applied_event_seq, None,
            "replay must not seed the dedup highwater"
        );
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-resume-9"),
            "reconnect cursor is forward-only: lower post-reset ids must not regress the highwater"
        );
        // SessionLoaded completes the window.
        app.agents.get_mut(&id).unwrap().session.loading_replay = false;

        assert!(
            handle(
                make_agent_chunk_with_event("sess-resume", "new turn", "p2", Some("sess-resume-1")),
                &mut app,
            ),
            "a live update after resume must render even with a reset-low eventId"
        );
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(1));

        assert!(
            !handle(
                make_agent_chunk_with_event("sess-resume", "dup", "p2", Some("sess-resume-1")),
                &mut app,
            ),
            "a duplicate live eventId is still deduped"
        );
    }

    /// An applied Plan update advances the reconnect cursor like any other
    /// applied arm — leaving it behind would make the tail re-send it.
    #[test]
    fn applied_plan_update_advances_reconnect_cursor() {
        let mut app = make_app_with_agent("sess-plan");
        let id = AgentId(0);

        let _ = handle(plan_update_msg("sess-plan", &[], None, false), &mut app);

        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            None,
            "no eventId on the update — cursor untouched"
        );

        let _ = handle(
            plan_update_msg("sess-plan", &[], Some("sess-plan-6"), false),
            &mut app,
        );
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-plan-6")
        );
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(6));
    }

    /// pi updates dedup on their OWN per-session `eventId` highwater: a
    /// re-delivered live copy (cursor-tail overlap when stamp order and file
    /// order diverge, leader fan-out) is dropped instead of re-applied — the
    /// pi arms have no other dedup. Replay stays exempt.
    #[test]
    fn pi_session_update_dedup_drops_already_applied_event() {
        let mut app = make_app_with_agent("sess-xdup");
        let id = AgentId(0);

        assert!(handle_ext_notification(
            &pi_model_switch_notif("sess-xdup", "sess-xdup-10"),
            &mut app
        ));
        assert_eq!(app.agents[&id].scrollback.len(), 1);
        assert_eq!(app.agents[&id].last_applied_pi_event_seq, Some(10));

        // Exact re-delivery: dropped, nothing re-applied, cursor unchanged.
        assert!(!handle_ext_notification(
            &pi_model_switch_notif("sess-xdup", "sess-xdup-10"),
            &mut app
        ));
        assert_eq!(
            app.agents[&id].scrollback.len(),
            1,
            "a duplicate pi event must not push a second block"
        );
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-xdup-10")
        );

        // A newer event still applies.
        assert!(handle_ext_notification(
            &pi_model_switch_notif("sess-xdup", "sess-xdup-11"),
            &mut app
        ));
        assert_eq!(app.agents[&id].scrollback.len(), 2);
        assert_eq!(app.agents[&id].last_applied_pi_event_seq, Some(11));

        // Lower-stale re-delivery (an already-applied lower id re-sent by
        // the cursor tail, e.g. goal mode) is dropped too — `<=`, not just
        // equality.
        assert!(!handle_ext_notification(
            &pi_model_switch_notif("sess-xdup", "sess-xdup-9"),
            &mut app
        ));
        assert_eq!(
            app.agents[&id].scrollback.len(),
            2,
            "a stale lower-id pi event must not push a block"
        );
        assert_eq!(app.agents[&id].last_applied_pi_event_seq, Some(11));
    }

    /// An unhandled pi kind (the default `_` arm) leaves no trace, so it must
    /// NOT advance the reconnect cursor or the dedup highwater — a cursor
    /// reconnect must still re-deliver it. An applied kind advances both.
    #[test]
    fn unhandled_pi_update_does_not_advance_cursor_or_highwater() {
        let mut app = make_app_with_agent("sess-ig");
        let id = AgentId(0);

        assert!(!handle_ext_notification(
            &pi_unhandled_notif("sess-ig", "sess-ig-7"),
            &mut app
        ));
        assert_eq!(
            app.agents[&id].last_seen_event_id, None,
            "an unhandled pi update must not advance the reconnect cursor"
        );
        assert_eq!(
            app.agents[&id].last_applied_pi_event_seq, None,
            "an unhandled pi update must not advance the dedup highwater"
        );

        // An applied kind (ModelAutoSwitched) advances both.
        assert!(handle_ext_notification(
            &pi_model_switch_notif("sess-ig", "sess-ig-8"),
            &mut app
        ));
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-ig-8")
        );
        assert_eq!(app.agents[&id].last_applied_pi_event_seq, Some(8));
    }

    /// Split-highwater regression: a fresh direct-emitted pi id must NOT
    /// make a queued lower-id ACP chunk look stale. pi lines bypass the
    /// agent's FIFO pipeline, so this ordering happens routinely (goal mode,
    /// subagent progress while the parent streams) — a shared highwater
    /// would silently drop the late chunk (live-text loss).
    #[test]
    fn direct_pi_event_does_not_shoot_down_delayed_acp_chunk() {
        let mut app = make_app_with_agent("sess-split");
        let id = AgentId(0);

        // Direct pi emission stamped N+1 arrives first.
        assert!(handle_ext_notification(
            &pi_model_switch_notif("sess-split", "sess-split-21"),
            &mut app
        ));
        assert_eq!(app.agents[&id].last_applied_pi_event_seq, Some(21));

        // The delayed ACP chunk stamped N arrives after — it must render.
        let len_before = app.agents[&id].scrollback.len();
        assert!(
            handle(
                make_agent_chunk_with_event(
                    "sess-split",
                    "delayed text",
                    "p1",
                    Some("sess-split-20")
                ),
                &mut app,
            ),
            "the lower-id ACP chunk must apply"
        );
        assert_eq!(
            app.agents[&id].scrollback.len(),
            len_before + 1,
            "the chunk's text must render — a shared highwater would have dropped it"
        );
        assert_eq!(
            app.agents[&id].last_applied_event_seq,
            Some(20),
            "the ACP highwater is seeded by the ACP stream only"
        );
        assert_eq!(
            app.agents[&id].last_applied_pi_event_seq,
            Some(21),
            "…and the ACP apply must not clobber the pi highwater either"
        );
    }

    /// The bg-task stdout arm advances the reconnect cursor like the other
    /// applied arms — a lagging cursor re-delivers the chunk, and after a
    /// full-replay swap the highwater (deliberately unseeded by replay)
    /// cannot absorb it.
    #[test]
    fn applied_bg_stdout_update_advances_reconnect_cursor() {
        let mut app = make_app_with_agent("sess-bg");
        let id = AgentId(0);
        app.agents
            .get_mut(&id)
            .unwrap()
            .session
            .bg_tool_call_to_task
            .insert("call-bg".into(), "task-1".into());

        let (tx, _rx) = tokio::sync::oneshot::channel();
        let request = acp::SessionNotification::new(
            acp::SessionId::new("sess-bg"),
            acp::SessionUpdate::ToolCallUpdate(acp::ToolCallUpdate::new(
                acp::ToolCallId::new("call-bg"),
                acp::ToolCallUpdateFields::new().raw_output(Some(serde_json::json!({
                    "type": "Bash",
                    "output_for_prompt": "hi",
                }))),
            )),
        )
        .meta(
            serde_json::json!({ "eventId": "sess-bg-6" })
                .as_object()
                .cloned(),
        );
        let _ = handle(
            AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
                request,
                response_tx: tx,
            }),
            &mut app,
        );

        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-bg-6"),
            "the bg-stdout arm must advance the cursor"
        );
    }

    /// Symptom-2 guard: a replay update with no `session/load` in flight
    /// (leader broadcast fallthrough, or a replay landing after its reload
    /// already timed out) must be dropped, never appended.
    #[test]
    fn unexpected_replay_update_is_dropped() {
        let mut app = make_app_with_agent("sess-rc");
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent
                .scrollback
                .push_block(RenderBlock::system("live content"));
        }

        assert!(
            !handle(
                replay_chunk("sess-rc", "stale history", "sess-rc-9"),
                &mut app
            ),
            "unexpected replay must not redraw"
        );

        let agent = app.agents.get_mut(&id).unwrap();
        assert_eq!(
            agent.scrollback.len(),
            1,
            "unexpected replay must not append to the transcript"
        );
        assert!(
            agent.last_seen_event_id.is_none(),
            "a dropped replay must not advance the reconnect cursor"
        );
    }

    /// The reconnect cursor only follows APPLIED updates: a deduped duplicate
    /// or a stale-turn drop must not advance it.
    #[test]
    fn dropped_updates_do_not_advance_reconnect_cursor() {
        let mut app = make_app_with_agent("sess-cur");
        let id = AgentId(0);

        assert!(handle(
            make_agent_chunk_with_event("sess-cur", "a", "p1", Some("sess-cur-5")),
            &mut app,
        ));
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-cur-5")
        );

        // Duplicate (deduped) — cursor unchanged.
        assert!(!handle(
            make_agent_chunk_with_event("sess-cur", "a", "p1", Some("sess-cur-5")),
            &mut app,
        ));
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-cur-5")
        );

        // Stale-turn drop: a non-viewer's self-originated, non-current prompt
        // id is dropped by the promptId gate — cursor unchanged.
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.note_self_originated_prompt("p-stale");
            agent.session.current_prompt_id = Some("p1".into());
        }
        let _ = handle(
            make_agent_chunk_with_event("sess-cur", "stale", "p-stale", Some("sess-cur-9")),
            &mut app,
        );
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-cur-5"),
            "a promptId-gated drop must not advance the cursor"
        );
    }

    #[test]
    fn deduped_stale_event_does_not_regress_context_used() {
        // Regression: the context bar must not drop when a stale, already-passed
        // replay delta arrives after a fresher live one. In leader / reconnect /
        // replay-live-overlap, a historical delta (LOWER eventId, LOWER
        // totalTokens) is deduped for rendering — but `refresh_context_used`
        // must respect the dedup too, otherwise the bar regresses below the real
        // usage (the reported "resume shows lower context" bug).
        let mut app = make_app_with_agent("sess-ctx");
        let id = AgentId(0);

        // Fresh live delta: high eventId, high token count.
        let _ = handle(
            make_token_notification_with_event("sess-ctx", 500_000, "sess-ctx-20"),
            &mut app,
        );
        assert_eq!(
            app.agents[&id].context_state.as_ref().map(|c| c.used),
            Some(500_000),
        );
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(20));

        // Stale historical replay delta: lower eventId (deduped), lower tokens.
        let _ = handle(
            make_token_notification_with_event("sess-ctx", 120_000, "sess-ctx-7"),
            &mut app,
        );
        assert_eq!(
            app.agents[&id].context_state.as_ref().map(|c| c.used),
            Some(500_000),
            "a deduped stale delta must not regress context_used to its lower value"
        );
        // Highwater unchanged by the deduped event.
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(20));
    }

    /// Apply-only cursor rule (pi path): a `ModelChanged` the catalog can't
    /// resolve is ignored, so it must NOT advance the reconnect cursor or the
    /// dedup highwater — a later reconnect (catalog now has the model) must
    /// still replay it. An applied follower switch advances both. Mirrors the
    /// ACP path's `advance_reconnect_cursor`.
    #[test]
    fn ignored_model_changed_does_not_advance_cursor_applied_one_does() {
        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            seed_models(agent, "grok-3", &["grok-3", "grok-4"]);
        }

        // Unknown model → ignored → both markers untouched.
        assert!(!handle_ext_notification(
            &model_changed_ext_with_event("sess-1", "grok-99-unknown", "sess-1-7"),
            &mut app
        ));
        assert_eq!(
            app.agents[&id].last_seen_event_id, None,
            "an ignored ModelChanged must not advance the reconnect cursor"
        );
        assert_eq!(
            app.agents[&id].last_applied_pi_event_seq, None,
            "an ignored ModelChanged must not advance the dedup highwater"
        );

        // Known model → applied → both markers advance.
        assert!(handle_ext_notification(
            &model_changed_ext_with_event("sess-1", "grok-4", "sess-1-8"),
            &mut app
        ));
        assert_eq!(
            app.agents[&id].last_seen_event_id.as_deref(),
            Some("sess-1-8"),
            "an applied ModelChanged advances the reconnect cursor"
        );
        assert_eq!(app.agents[&id].last_applied_pi_event_seq, Some(8));
    }

    #[test]
    fn fresh_higher_event_still_updates_context_used() {
        // Counterpart: a genuinely newer delta (higher eventId) must still
        // advance the context bar — the dedup gate only blocks stale events.
        let mut app = make_app_with_agent("sess-ctx2");
        let id = AgentId(0);

        let _ = handle(
            make_token_notification_with_event("sess-ctx2", 100_000, "sess-ctx2-3"),
            &mut app,
        );
        assert_eq!(
            app.agents[&id].context_state.as_ref().map(|c| c.used),
            Some(100_000),
        );

        let _ = handle(
            make_token_notification_with_event("sess-ctx2", 250_000, "sess-ctx2-8"),
            &mut app,
        );
        assert_eq!(
            app.agents[&id].context_state.as_ref().map(|c| c.used),
            Some(250_000),
            "a newer (higher eventId) delta must update context_used"
        );
        assert_eq!(app.agents[&id].last_applied_event_seq, Some(8));
    }

