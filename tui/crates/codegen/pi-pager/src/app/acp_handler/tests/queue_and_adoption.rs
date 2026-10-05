#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    /// Regression (live-delta path): the auto-wake turn streams its reply as a
    /// synthetic-promptId `session/update`. On a driver (not a viewer) the
    /// content must render, but the turn must NOT be claimed — no role flip, no
    /// `current_prompt_id`, no `TurnRunning` — otherwise it strands the
    /// turn-status and poisons the slot so later real turns' PromptResponses get
    /// discarded. (Pairs with the `handle_queue_changed` skip above: together
    /// they keep synthetic auto-wake turns out of the running slot on BOTH the
    /// queue-broadcast and the streaming-delta paths.)
    #[test]
    fn synthetic_auto_wake_delta_renders_without_claiming_turn() {
        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);

        let _ = handle(
            make_agent_chunk_message_with_prompt(
                "sess-1",
                "background task finished on its own",
                "task-completed-bg1",
                false,
            ),
            &mut app,
        );

        let agent = app.agents.get(&id).unwrap();
        assert_eq!(
            agent_message_text(agent),
            "background task finished on its own",
            "auto-wake content must still render"
        );
        assert!(matches!(agent.session.state, AgentState::Idle));
        assert!(agent.session.current_prompt_id.is_none());
        assert!(!agent.attached_as_viewer);
    }

    /// Multi-client mid-turn load (leader mode): a client that opens a session
    /// while ANOTHER client is driving an in-flight turn subscribes AFTER the
    /// turn-start `queue/changed`, so it never adopts via `handle_queue_changed`
    /// and its `current_prompt_id` stays `None`. The server conveys the running
    /// prompt id in the `SessionLoaded` response meta; the loader must adopt it
    /// (set `current_prompt_id` + enter `TurnRunning`) WITHOUT re-rendering the
    /// user block (replay already rendered it), so subsequent live
    /// `session/update` deltas for that prompt pass the gate and render live.
    #[test]
    fn session_loaded_with_running_prompt_id_adopts_and_passes_live_gate() {
        use crate::app::dispatch::dispatch;
        use crate::app::actions::{Action, TaskResult};

        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        // A viewer that loaded the session mid-turn has no local prompt id.
        assert!(app.agents[&id].session.current_prompt_id.is_none());
        let scroll_before = app.agents[&id].scrollback.len();

        dispatch(
            Action::TaskComplete(TaskResult::SessionLoaded {
                agent_id: id,
                session_id: acp::SessionId::new("sess-1"),
                models: None,
                code_restored: false,
                restore_summary: None,
                restore_degree: None,
                running_prompt_id: Some("p-run".to_string()),
                scheduler_background_loops: None,
            }),
            &mut app,
        );

        // Adopted the in-flight prompt id + entered the turn-running state.
        assert_eq!(
            app.agents[&id].session.current_prompt_id.as_deref(),
            Some("p-run"),
            "loader must adopt the server-conveyed running prompt id"
        );
        assert!(
            app.agents[&id].session.state.is_turn_running(),
            "loading mid-turn must enter TurnRunning so the spinner shows"
        );
        // No user-prompt block pushed: the in-flight turn's user prompt arrived
        // via the `session/load` replay; adoption must not duplicate it.
        let user_blocks = app.agents[&id]
            .scrollback
            .entries_in_range(0..app.agents[&id].scrollback.len())
            .iter()
            .filter(|e| matches!(&e.block, RenderBlock::UserPrompt(_)))
            .count();
        assert_eq!(
            user_blocks, 0,
            "adoption-on-load must NOT render a duplicate user-prompt block"
        );
        assert_eq!(
            app.agents[&id].scrollback.len(),
            scroll_before,
            "adoption-on-load must not grow the scrollback"
        );

        // A live (non-replay) chunk stamped with the adopted prompt id now
        // passes the gate and renders (previously dropped → the viewer froze).
        let (tx, _rx) = tokio::sync::oneshot::channel();
        let request = acp::SessionNotification::new(
            acp::SessionId::new("sess-1"),
            acp::SessionUpdate::AgentMessageChunk(acp::ContentChunk::new(acp::ContentBlock::Text(
                acp::TextContent::new("live delta from driver"),
            ))),
        )
        .meta(
            serde_json::json!({ "promptId": "p-run" })
                .as_object()
                .cloned(),
        );
        handle(
            AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
                request,
                response_tx: tx,
            }),
            &mut app,
        );

        assert_eq!(
            agent_message_text(app.agents.get(&id).unwrap()),
            "live delta from driver",
            "live delta for the adopted in-flight prompt must render, not be dropped by the gate"
        );
    }

    /// The adoption predicate: synthetic non-scheduler turns (auto-wake /
    /// subagent-completion / notification-drain / goal turns) emit no
    /// `prompt_complete`, so a re-attach must NOT adopt them (adoption would
    /// strand the viewer in `TurnRunning`). Scheduler-fired (`/loop`) turns are
    /// synthetic but client-driven with a real `prompt_complete`, and plain user
    /// turns are always adoptable.
    #[test]
    fn should_adopt_running_prompt_skips_synthetic_non_scheduler() {
        assert!(should_adopt_running_prompt("p-user"));
        assert!(should_adopt_running_prompt(
            "scheduler-fired-019e51a3-abcd-1234"
        ));
        assert!(!should_adopt_running_prompt("task-completed-abc-123"));
        assert!(!should_adopt_running_prompt("subagent-completed-xyz-789"));
        assert!(!should_adopt_running_prompt(
            "notifications-019e0000-0000-7000-8000-0000000000aa"
        ));
        assert!(!should_adopt_running_prompt("goal-summary-019e2d3e"));
        assert!(!should_adopt_running_prompt(
            "goal-classifier-nudge-019e2d3e"
        ));
    }

    /// A `SessionLoaded` conveying a SYNTHETIC non-scheduler `running_prompt_id`
    /// (auto-wake / subagent-completion) must NOT be adopted: those actor-run
    /// turns emit no `prompt_complete`, so adopting one would strand the viewer
    /// in `TurnRunning` forever. The agent stays `Idle`, and the preserve arg is
    /// gated on the same predicate so the running turn's buffered follow-up chips
    /// are NOT preserved (adoption — their only flusher — is skipped, so
    /// preserving would orphan them). The ADOPTABLE-id preserve contrast is
    /// covered by `reload_preserves_running_turn_follow_ups_and_renders_on_adoption`.
    #[test]
    fn session_loaded_with_synthetic_running_prompt_id_stays_idle() {
        use crate::app::dispatch::dispatch;
        use crate::app::actions::{Action, TaskResult};

        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);

        // Seed the running turn's buffered follow-up chips keyed by the synthetic
        // prompt id, as if they had arrived on the ext channel during replay.
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.follow_up_pending.insert(
                "task-completed-abc-123".to_string(),
                crate::app::agent_view::FollowUps {
                    response_id: "resp-syn".into(),
                    suggestions: vec!["go".into()],
                },
            );
            agent
                .follow_up_pending_order
                .push_back("task-completed-abc-123".to_string());
        }

        dispatch(
            Action::TaskComplete(TaskResult::SessionLoaded {
                agent_id: id,
                session_id: acp::SessionId::new("sess-1"),
                models: None,
                code_restored: false,
                restore_summary: None,
                restore_degree: None,
                running_prompt_id: Some("task-completed-abc-123".to_string()),
                scheduler_background_loops: None,
            }),
            &mut app,
        );

        assert!(
            app.agents[&id].session.current_prompt_id.is_none(),
            "synthetic non-scheduler running prompt must not be adopted on load"
        );
        assert!(
            app.agents[&id].session.state.is_idle(),
            "adopting a synthetic actor-run turn would strand the viewer in TurnRunning"
        );
        assert!(
            app.agents[&id].follow_up_pending.is_empty(),
            "a synthetic id's buffered follow-ups must be dropped, not preserved"
        );
    }

    /// A `SessionLoaded` conveying a SCHEDULER-FIRED (`/loop`) `running_prompt_id`
    /// IS adopted: cron turns are synthetic but client-driven and DO emit a
    /// `prompt_complete`, so the viewer can safely enter `TurnRunning` (the
    /// dashboard shows a running `/loop` session as Working).
    #[test]
    fn session_loaded_with_scheduler_fired_running_prompt_id_adopts() {
        use crate::app::dispatch::dispatch;
        use crate::app::actions::{Action, TaskResult};

        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        let pid = "scheduler-fired-019e51a3-abcd-1234";

        dispatch(
            Action::TaskComplete(TaskResult::SessionLoaded {
                agent_id: id,
                session_id: acp::SessionId::new("sess-1"),
                models: None,
                code_restored: false,
                restore_summary: None,
                restore_degree: None,
                running_prompt_id: Some(pid.to_string()),
                scheduler_background_loops: None,
            }),
            &mut app,
        );

        assert_eq!(
            app.agents[&id].session.current_prompt_id.as_deref(),
            Some(pid),
            "scheduler-fired turns must still be adopted on load"
        );
        assert!(
            app.agents[&id].session.state.is_turn_running(),
            "an adopted /loop turn must enter TurnRunning so the dashboard shows Working"
        );
    }

    /// The session-load running-turn adopt is a production turn-start
    /// clear site — adopting an in-flight turn on load drops prior chips.
    #[test]
    fn session_loaded_adopting_running_turn_clears_follow_up_chips() {
        use crate::app::dispatch::dispatch;
        use crate::app::actions::{Action, TaskResult};

        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        app.agents
            .get_mut(&id)
            .unwrap()
            .apply_follow_ups("resp-1".into(), vec!["a".into()]);
        dispatch(
            Action::TaskComplete(TaskResult::SessionLoaded {
                agent_id: id,
                session_id: acp::SessionId::new("sess-1"),
                models: None,
                code_restored: false,
                restore_summary: None,
                restore_degree: None,
                running_prompt_id: Some("p-run".to_string()),
                scheduler_background_loops: None,
            }),
            &mut app,
        );
        assert!(
            app.agents[&id].follow_ups.is_none(),
            "adopting a running turn on session-load must clear prior chips"
        );
    }

    /// Idle reload (no running prompt) must ALSO drop prior chips and fully
    /// reset the seen map/generation — follow-ups are streaming-only and never
    /// persist across a reload, so stale state must not survive.
    #[test]
    fn session_loaded_idle_clears_follow_up_chips_and_seen() {
        use crate::app::dispatch::dispatch;
        use crate::app::actions::{Action, TaskResult};

        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.apply_follow_ups("resp-1".into(), vec!["a".into()]);
            agent.follow_up_chips = vec![ratatui::layout::Rect::new(0, 0, 5, 1)];
        }
        dispatch(
            Action::TaskComplete(TaskResult::SessionLoaded {
                agent_id: id,
                session_id: acp::SessionId::new("sess-1"),
                models: None,
                code_restored: false,
                restore_summary: None,
                restore_degree: None,
                running_prompt_id: None,
                scheduler_background_loops: None,
            }),
            &mut app,
        );
        let agent = &app.agents[&id];
        assert!(agent.follow_ups.is_none(), "idle reload must clear chips");
        assert!(
            agent.follow_up_chips.is_empty(),
            "idle reload must clear hit rects"
        );
        assert!(
            agent.follow_up_seen.is_empty(),
            "idle reload must reset the seen map"
        );
        assert_eq!(
            agent.follow_up_next_gen, 0,
            "idle reload must reset the generation"
        );
    }

    /// Multi-client load-window gap (leader mode): a viewer
    /// (`attached_as_viewer`) that subscribed mid-turn receives the driver's
    /// live (non-replay) deltas WHILE its `session/load` replay is still in
    /// flight — so `current_prompt_id == None` and `loading_replay == true`.
    /// Before the fix the gate dropped every such delta (the viewer froze at
    /// its load snapshot). Now the viewer ADOPTS the incoming prompt id and the
    /// delta renders into scrollback, recovering progress streamed during the
    /// load window.
    #[test]
    fn viewer_adopts_live_delta_during_load_window_instead_of_dropping() {
        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        // Model a viewer mid-load: opened via `session/load`, replay in flight,
        // no turn of its own.
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.attached_as_viewer = true;
            agent.session.loading_replay = true;
            agent.session.current_prompt_id = None;
        }

        // A live (non-replay) chunk from the driver's user-initiated turn,
        // stamped with a normal (non-synthetic) promptId the viewer has never
        // seen.
        assert!(
            !is_server_initiated_prompt("p-driver"),
            "test fixture must use a non-synthetic prompt id"
        );
        let (tx, _rx) = tokio::sync::oneshot::channel();
        let request = acp::SessionNotification::new(
            acp::SessionId::new("sess-1"),
            acp::SessionUpdate::AgentMessageChunk(acp::ContentChunk::new(acp::ContentBlock::Text(
                acp::TextContent::new("in-window driver delta"),
            ))),
        )
        .meta(
            serde_json::json!({ "promptId": "p-driver" })
                .as_object()
                .cloned(),
        );
        let affected = handle(
            AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
                request,
                response_tx: tx,
            }),
            &mut app,
        );

        // Adopted the driver's prompt id (so subsequent deltas keep matching).
        assert_eq!(
            app.agents[&id].session.current_prompt_id.as_deref(),
            Some("p-driver"),
            "viewer must adopt the driver's prompt id rather than drop the delta"
        );
        // The body rendered into scrollback (not dropped).
        assert_eq!(
            agent_message_text(app.agents.get(&id).unwrap()),
            "in-window driver delta",
            "viewer must render the live delta received during its load window"
        );
        // Redraw is suppressed while `loading_replay` is true (the content is in
        // scrollback and the post-`SessionLoaded` redraw paints it).
        assert!(
            !affected,
            "redraw is suppressed during loading_replay even though the delta applied"
        );
    }

    /// Driver-rewind safety: the viewer relaxation must NOT regress the
    /// locally-created driver's stale-chunk drop. A non-viewer
    /// (`attached_as_viewer == false`) whose own turn just finished/cancelled
    /// (`current_prompt_id == None`) must still DROP a late delta carrying the
    /// aborted turn's promptId — otherwise rewound content would resurface.
    #[test]
    fn driver_post_rewind_still_drops_stale_delta() {
        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        // A locally-created driver (NOT a viewer) that just finished its turn:
        // finish_turn cleared current_prompt_id back to None. The aborted turn
        // was driven by THIS client, so its prompt id is self-originated — that
        // is what makes the gate treat the late chunk as ours (drop) rather
        // than as another client's turn (adopt).
        {
            let agent = app.agents.get_mut(&id).unwrap();
            assert!(
                !agent.attached_as_viewer,
                "a locally-created driver is never a viewer"
            );
            agent.note_self_originated_prompt("p-aborted");
            agent.session.loading_replay = false;
            agent.session.current_prompt_id = None;
        }

        // A late/stale chunk from the just-aborted turn arrives.
        let (tx, _rx) = tokio::sync::oneshot::channel();
        let request = acp::SessionNotification::new(
            acp::SessionId::new("sess-1"),
            acp::SessionUpdate::AgentMessageChunk(acp::ContentChunk::new(acp::ContentBlock::Text(
                acp::TextContent::new("stale rewound body"),
            ))),
        )
        .meta(
            serde_json::json!({ "promptId": "p-aborted" })
                .as_object()
                .cloned(),
        );
        handle(
            AcpClientMessage::SessionNotification(pi_acp_lib::AcpArgs {
                request,
                response_tx: tx,
            }),
            &mut app,
        );

        // Dropped: no adoption, nothing rendered.
        assert!(
            app.agents[&id].session.current_prompt_id.is_none(),
            "driver post-rewind must NOT adopt a stale prompt id"
        );
        assert_eq!(
            agent_message_text(app.agents.get(&id).unwrap()),
            "",
            "driver post-rewind must drop the stale delta (no scrollback render)"
        );
    }

    /// Multi-client mirroring after this pane has driven a turn (the sticky-flag
    /// bug). A pane that already sent a prompt has `attached_as_viewer == false`
    /// and a self-originated prompt id. When ANOTHER pane then drives a normal
    /// turn, the live deltas carry a foreign (non-server-initiated) prompt id
    /// this pane never originated. With the old one-way latch the gate dropped
    /// them and the pane rendered nothing; now the gate re-derives viewer status
    /// from prompt-id ownership, so the foreign turn is adopted + rendered and
    /// the flag flips back to true.
    #[test]
    fn pane_that_drove_a_turn_still_mirrors_another_clients_turn() {
        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        // This pane drove its own turn "p-mine" earlier, then went idle.
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.note_self_originated_prompt("p-mine");
            agent.attached_as_viewer = false;
            agent.session.loading_replay = false;
            agent.session.current_prompt_id = None;
        }

        // Another pane now drives a normal turn; its live delta arrives here
        // with a foreign, non-synthetic prompt id.
        assert!(
            !is_server_initiated_prompt("p-other"),
            "test fixture must use a non-synthetic prompt id"
        );
        let _ = handle(
            make_agent_chunk_message_with_prompt("sess-1", "other pane output", "p-other", false),
            &mut app,
        );

        let agent = app.agents.get(&id).unwrap();
        assert_eq!(
            agent.session.current_prompt_id.as_deref(),
            Some("p-other"),
            "the foreign turn must be adopted so subsequent deltas keep matching"
        );
        assert_eq!(
            agent_message_text(agent),
            "other pane output",
            "a pane that already drove a turn must still render another client's turn"
        );
        assert!(
            agent.attached_as_viewer,
            "viewing another client's turn must re-arm the viewer flag (no one-way latch)"
        );
    }

    /// A stash matching the finishing response's pid is discarded, not re-adopted.
    #[test]
    fn own_pid_stash_is_not_readopted_by_its_response() {
        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.session.current_prompt_id = Some("p1".to_string());
            agent.session.state = AgentState::TurnRunning;
        }
        app.pending_running_adoptions.insert(
            id,
            crate::app::acp_handler::PendingRunningAdoption {
                prompt_id: "p1".to_string(),
                text: Some("first".to_string()),
                combined_texts: None,
                kind: "prompt".to_string(),
            },
        );

        prompt_response(&mut app, "p1");
        let agent = app.agents.get(&id).unwrap();
        assert!(
            agent.session.state.is_idle(),
            "must not re-enter TurnRunning"
        );
        assert!(agent.session.current_prompt_id.is_none());
        assert!(!app.pending_running_adoptions.contains_key(&id));
    }

    /// `adopt_running_prompt` must not leave `start_turn`'s user-echo skip
    /// armed: the adopted turn pushed no local user block, so the armed skip
    /// would survive and silently eat the NEXT turn's `UserMessageChunk`
    /// broadcast (the driver's next message missing from the viewer).
    #[test]
    fn adopt_running_prompt_does_not_eat_next_turns_user_echo() {
        let mut app = make_app_with_agent("sess-adopt");
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.adopt_running_prompt("p-driver".into());
            // The adopted turn ends.
            agent.session.finish_turn(&mut agent.scrollback);
        }
        let len_before = app.agents[&id].scrollback.len();

        // The driver's NEXT turn begins with its user-message broadcast.
        let (tx, _rx) = tokio::sync::oneshot::channel();
        let request = acp::SessionNotification::new(
            acp::SessionId::new("sess-adopt"),
            acp::SessionUpdate::UserMessageChunk(acp::ContentChunk::new(acp::ContentBlock::Text(
                acp::TextContent::new("next question"),
            ))),
        )
        .meta(
            serde_json::json!({ "promptId": "p-next", "isReplay": false })
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
            app.agents[&id].scrollback.len(),
            len_before + 1,
            "the next turn's user message must render, not be echo-swallowed"
        );
    }

    /// The bind helper resets per-session cursor/highwater only when the
    /// bound id actually changes: `SessionLoaded` re-binds the SAME id after
    /// every load, so an unconditional reset would wipe the cursor the load's
    /// replay just established.
    #[test]
    fn bind_session_id_resets_cursor_only_on_id_change() {
        let mut agent = make_agent(Some("sess-a"));
        agent.last_seen_event_id = Some("sess-a-7".into());
        agent.last_applied_event_seq = Some(7);
        agent.last_applied_pi_event_seq = Some(8);

        let epoch = agent.session_binding_epoch;
        agent.bind_session_id(acp::SessionId::new("sess-a"));
        assert_eq!(agent.session_binding_epoch, epoch);
        assert_eq!(agent.last_seen_event_id.as_deref(), Some("sess-a-7"));
        assert_eq!(agent.last_applied_event_seq, Some(7));
        assert_eq!(agent.last_applied_pi_event_seq, Some(8));

        agent.bind_session_id(acp::SessionId::new("sess-b"));
        assert_eq!(agent.session_binding_epoch, epoch.wrapping_add(1));
        assert_eq!(
            agent.session.session_id.as_ref().map(|s| s.0.as_ref()),
            Some("sess-b")
        );
        assert!(
            agent.last_seen_event_id.is_none(),
            "another session's cursor must not survive a rebind"
        );
        assert!(agent.last_applied_event_seq.is_none());
        assert!(agent.last_applied_pi_event_seq.is_none());
    }

    #[test]
    fn session_binding_epoch_counts_bind_and_unbind_transitions() {
        let mut agent = make_agent(None);
        assert_eq!(agent.session_binding_epoch, 0);
        agent.bind_session_id(acp::SessionId::new("a"));
        assert_eq!(agent.session_binding_epoch, 1);
        agent.unbind_session_id();
        assert_eq!(agent.session_binding_epoch, 2);
        agent.bind_session_id(acp::SessionId::new("a"));
        assert_eq!(agent.session_binding_epoch, 3);
    }

    #[test]
    fn viewer_adopting_live_delta_enters_turn_running_and_timer_is_monotonic() {
        // A viewer (attached_as_viewer) watching the driver's turn starts Idle.
        // Adopting the first live delta must flip it to TurnRunning and stamp
        // `turn_started_at` so the turn-in-progress chrome (status line, elapsed
        // timer, cancel/interject footer hints — all gated on TurnRunning)
        // renders. A second chunk must NOT reset `turn_started_at`.
        let mut app = make_app_with_agent("sess-view");
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            agent.attached_as_viewer = true;
            assert!(matches!(agent.session.state, AgentState::Idle));
            assert!(agent.turn_started_at.is_none());
        }

        let _ = handle(
            make_agent_chunk_message_with_prompt(
                "sess-view",
                "driver chunk 1",
                "pid-driver",
                false,
            ),
            &mut app,
        );

        let first_started_at = {
            let agent = app.agents.get(&AgentId(0)).unwrap();
            assert!(
                matches!(agent.session.state, AgentState::TurnRunning),
                "viewer must enter TurnRunning when adopting a live driver delta"
            );
            assert_eq!(
                agent.session.current_prompt_id.as_deref(),
                Some("pid-driver")
            );
            agent
                .turn_started_at
                .expect("turn_started_at must be stamped on Idle->TurnRunning")
        };

        // Second chunk for the same turn must not reset the elapsed-timer anchor.
        let _ = handle(
            make_agent_chunk_message_with_prompt(
                "sess-view",
                "driver chunk 2",
                "pid-driver",
                false,
            ),
            &mut app,
        );
        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert!(matches!(agent.session.state, AgentState::TurnRunning));
        assert_eq!(
            agent.turn_started_at,
            Some(first_started_at),
            "turn_started_at must be monotonic across chunks (not reset per chunk)"
        );
    }

    #[test]
    fn viewer_replay_delta_does_not_enter_turn_running() {
        // A replayed historical-load delta (also attached_as_viewer) must NOT
        // flash TurnRunning — only LIVE deltas put a viewer into the running
        // state. The promptId is still adopted so later chunks match.
        let mut app = make_app_with_agent("sess-view");
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            agent.attached_as_viewer = true;
            agent.session.loading_replay = true;
        }

        let _ = handle(
            make_agent_chunk_message_with_prompt("sess-view", "old chunk", "pid-old", true),
            &mut app,
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert!(
            matches!(agent.session.state, AgentState::Idle),
            "a replay delta must not enter TurnRunning"
        );
        assert!(agent.turn_started_at.is_none());
    }

    #[test]
    fn viewer_mid_turn_reattach_shows_running_chrome_after_replay_window() {
        // Reproduces the MID-TURN reattach ordering that left a viewer stuck
        // Idle (no "Responding…"/cancel chrome) even though the driver's turn
        // was live:
        //   (1) the viewer subscribes on its load request, so it receives a
        //       LIVE delta DURING its replay window (loading_replay = true). It
        //       adopts the driver's promptId, but the TurnRunning transition is
        //       (correctly) suppressed while replaying.
        //   (2) SessionLoaded clears loading_replay — modeled here WITHOUT a
        //       runningPromptId conveyance (the worst case).
        //   (3) subsequent LIVE deltas carry the SAME (already-adopted) promptId,
        //       so they MATCH current_prompt_id and skip the adopt block.
        // Before the fix, step (3) never flipped the viewer to TurnRunning (the
        // TurnRunning entry lived inside the mismatch-only adopt block), so the
        // chrome never appeared. It must now.
        let mut app = make_app_with_agent("sess-view");
        {
            let agent = app.agents.get_mut(&AgentId(0)).unwrap();
            agent.attached_as_viewer = true;
            agent.session.loading_replay = true;
            assert!(matches!(agent.session.state, AgentState::Idle));
        }

        // (1) live delta during the replay window: adopts promptId, stays Idle.
        let _ = handle(
            make_agent_chunk_message_with_prompt("sess-view", "responding 1", "pid-driver", false),
            &mut app,
        );
        {
            let agent = app.agents.get(&AgentId(0)).unwrap();
            assert_eq!(
                agent.session.current_prompt_id.as_deref(),
                Some("pid-driver"),
                "the in-flight turn id is adopted even during the replay window"
            );
            assert!(
                matches!(agent.session.state, AgentState::Idle),
                "during loading_replay the viewer must not flash TurnRunning yet"
            );
        }

        // (2) SessionLoaded completes (no runningPromptId conveyed).
        app.agents
            .get_mut(&AgentId(0))
            .unwrap()
            .session
            .loading_replay = false;

        // (3) a post-load live delta MATCHING the already-adopted promptId — this
        //     skips the mismatch-only adopt block, so the fix must flip the
        //     viewer to TurnRunning here.
        let _ = handle(
            make_agent_chunk_message_with_prompt("sess-view", "responding 2", "pid-driver", false),
            &mut app,
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert!(
            matches!(agent.session.state, AgentState::TurnRunning),
            "viewer must show TurnRunning chrome for a matching post-load live \
             delta (mid-turn reattach)"
        );
        assert!(
            agent.turn_started_at.is_some(),
            "elapsed-timer anchor must be stamped so the counter renders"
        );
        assert_eq!(
            agent.session.tracker.activity(),
            Some(crate::acp::tracker::TurnActivity::Responding),
            "activity must be Responding so the status line reads 'Responding…'"
        );
    }

    #[test]
    fn viewer_does_not_enter_turn_running_for_server_initiated_turn() {
        // A server-initiated / auto-wake turn (synthetic prompt id, e.g. a
        // background subagent or task completion: `task-completed-…`) runs inside
        // the actor and emits NO `pi/session/prompt_complete`. If a viewer
        // entered TurnRunning for it, nothing would ever finish the turn and the
        // viewer would be stuck "Responding…" forever — exactly the bug where one
        // dashboard showed "Worked for" while the other was stuck responding.
        // The driver also declines to show chrome for these (its server-initiated
        // adopt path never calls start_turn), so the viewer must mirror that:
        // adopt the id (so content renders) but stay Idle (no running chrome).
        let mut app = make_app_with_agent("sess-view");
        app.agents.get_mut(&AgentId(0)).unwrap().attached_as_viewer = true;

        let _ = handle(
            make_agent_chunk_message_with_prompt(
                "sess-view",
                "auto-wake output",
                "task-completed-abc",
                false,
            ),
            &mut app,
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent.session.current_prompt_id.as_deref(),
            Some("task-completed-abc"),
            "the synthetic turn id is still adopted so its content renders"
        );
        assert!(
            matches!(agent.session.state, AgentState::Idle),
            "a viewer must NOT enter TurnRunning for a server-initiated turn (no \
             prompt_complete would ever finish it → permanent stuck spinner)"
        );
        assert!(agent.turn_started_at.is_none());
    }

    #[test]
    fn viewer_stop_hooks_after_marker_attach_to_it() {
        // Viewer order: the durable TurnCompleted pushes the marker first;
        // the batch arriving right after merges into it.
        let mut app = make_app_with_agent("sess-view-hooks");
        app.agents.get_mut(&AgentId(0)).unwrap().attached_as_viewer = true;
        let _ = handle(
            make_agent_chunk_message_with_prompt("sess-view-hooks", "chunk", "pid-v", false),
            &mut app,
        );
        let _ = handle_ext_notification(
            &pi_turn_completed_notif("sess-view-hooks", "pid-v", "end_turn", false),
            &mut app,
        );
        assert_eq!(
            last_marker_stop_hook_groups(&app.agents[&AgentId(0)].scrollback),
            Some(0),
            "marker starts without hooks"
        );

        let _ = handle_ext_notification(
            &pi_hook_execution_notif("sess-view-hooks", "stop", false),
            &mut app,
        );

        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            last_marker_stop_hook_groups(&agent.scrollback),
            Some(1),
            "the batch must merge into the existing marker"
        );
        assert_eq!(
            count_lifecycle_blocks(&agent.scrollback),
            0,
            "no standalone stop block on the live viewer path"
        );

        // A second, differently-named batch of the same turn (stop_failure +
        // stop on error turns) merges too…
        let _ = handle_ext_notification(
            &pi_hook_execution_notif("sess-view-hooks", "stop_failure", false),
            &mut app,
        );
        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            last_marker_stop_hook_groups(&agent.scrollback),
            Some(2),
            "a second batch with a new event name must also merge"
        );
        assert_eq!(count_lifecycle_blocks(&agent.scrollback), 0);

        // …but a same-name repeat (e.g. the session-end `stop` batch) does
        // not belong to this marker and stays standalone.
        let _ = handle_ext_notification(
            &pi_hook_execution_notif("sess-view-hooks", "stop", false),
            &mut app,
        );
        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(last_marker_stop_hook_groups(&agent.scrollback), Some(2));
        assert_eq!(
            count_lifecycle_blocks(&agent.scrollback),
            1,
            "a repeated same-name batch falls back to the standalone block"
        );
    }

    /// No regression: a running prompt whose terminal did NOT arrive in replay is
    /// still adopted on load (the set is precise — it blocks only ended turns).
    #[test]
    fn session_loaded_adopts_when_prompt_not_terminal_in_replay() {
        use crate::app::dispatch::dispatch;
        use crate::app::actions::{Action, TaskResult};

        let mut app = make_app_with_agent("sess-1");
        let id = AgentId(0);
        app.agents.get_mut(&id).unwrap().session.loading_replay = true;
        // A DIFFERENT turn's terminal arrives in replay.
        let _ = handle_ext_notification(
            &pi_turn_completed_notif("sess-1", "p-old", "end_turn", true),
            &mut app,
        );

        dispatch(
            Action::TaskComplete(TaskResult::SessionLoaded {
                agent_id: id,
                session_id: acp::SessionId::new("sess-1"),
                models: None,
                code_restored: false,
                restore_summary: None,
                restore_degree: None,
                running_prompt_id: Some("p-run".to_string()),
                scheduler_background_loops: None,
            }),
            &mut app,
        );

        let agent = &app.agents[&id];
        assert_eq!(
            agent.session.current_prompt_id.as_deref(),
            Some("p-run"),
            "a running prompt with no terminal-in-replay must still be adopted"
        );
        assert!(
            agent.session.state.is_turn_running(),
            "adopting an in-flight turn on load must enter TurnRunning"
        );
    }

    #[test]
    fn viewer_turn_anchor_backdates_from_turn_start_ms() {
        use std::time::Duration;
        // A turnStartMs ~10s in the past anchors ~10s ago.
        let ts = chrono::Utc::now().timestamp_millis() - 10_000;
        let elapsed = viewer_turn_anchor(Some(ts)).elapsed();
        assert!(
            elapsed >= Duration::from_secs(9) && elapsed <= Duration::from_secs(20),
            "anchor must be ~10s in the past, got {elapsed:?}"
        );
        // No turnStartMs (older shell) anchors at ~now.
        assert!(
            viewer_turn_anchor(None).elapsed() < Duration::from_secs(1),
            "no turnStartMs must anchor at ~now"
        );
        // A future turnStartMs (clock skew) clamps to now rather than panicking.
        let future = chrono::Utc::now().timestamp_millis() + 60_000;
        assert!(
            viewer_turn_anchor(Some(future)).elapsed() < Duration::from_secs(1),
            "a future turnStartMs must clamp to now"
        );
    }

