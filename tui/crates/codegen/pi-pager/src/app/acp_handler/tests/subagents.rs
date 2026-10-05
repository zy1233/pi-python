#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    #[test]
    fn a_replay_tagged_spawn_outside_a_session_load_is_dropped() {
        with_replay_disk_home(|_| {
            let child_sid = "child-unexpected-replay";
            let mut app = make_app_with_agent("sess-parent");
            assert!(!app.agents[&AgentId(0)].session.loading_replay);
            write_child_updates_jsonl(
                replay_disk_test_home(),
                child_sid,
                &(child_tool_line(child_sid) + "\n"),
            );
            let spawned = subagent_ext_replay(
                "sess-parent",
                serde_json::json!({
                    "sessionUpdate": "subagent_spawned",
                    "subagent_id": child_sid,
                    "parent_session_id": "sess-parent",
                    "child_session_id": child_sid,
                    "subagent_type": "explore",
                    "description": "scan src/",
                }),
                "sess-parent-1",
            );
            handle_ext_notification(&spawned, &mut app);
            let agent = app.agents.get(&AgentId(0)).unwrap();
            assert!(
                agent.subagent_sessions.is_empty(),
                "unexpected replay spawn must not register"
            );
            assert!(agent.subagent_views.is_empty());
        });
    }

    #[test]
    fn a_stray_replay_is_accepted_briefly_after_a_load_then_a_live_update_stops_it() {
        let replay = || crate::acp::meta::NotificationMeta {
            is_replay: true,
            ..crate::acp::meta::NotificationMeta::default()
        };
        let mut app = make_app_with_agent("sess-late");
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        assert!(!agent.session.loading_replay);
        agent.arm_late_replay_grace();

        assert!(
            !drop_unexpected_replay(agent, &replay(), "sess-late", "test"),
            "a stray replay must still apply during the post-load grace"
        );

        let live = crate::acp::meta::NotificationMeta::default();
        assert!(!drop_unexpected_replay(agent, &live, "sess-late", "test"));
        assert!(
            drop_unexpected_replay(agent, &replay(), "sess-late", "test"),
            "a live update ends the grace so later replays are dropped"
        );
    }

    /// Each scenario owns one parent plus a child driven through the real
    /// notification handler, and the child's on-disk transcript under the
    /// shared replay test home.
    mod eviction_and_rebuild {
        use super::*;
        use crate::app::subagent::ChildTranscript;

        struct Scenario {
            app: AppView,
            child_sid: &'static str,
        }

        impl Drop for Scenario {
            fn drop(&mut self) {
                crate::app::subagent::set_replay_grok_home_for_tests(None);
            }
        }

        impl Scenario {
            /// Live-spawn `child_sid`, first writing `updates` to the child's
            /// `updates.jsonl` (`None` = nothing persisted).
            fn spawn(child_sid: &'static str, updates: Option<String>) -> Self {
                let home = replay_disk_test_home();
                crate::app::subagent::set_replay_grok_home_for_tests(Some(home.to_path_buf()));
                let mut app = make_app_with_agent("sess-parent");
                spawn_subagent_with_optional_updates(&mut app, child_sid, updates.as_deref());
                Scenario { app, child_sid }
            }

            fn spawn_sibling(&mut self, child_sid: &str, updates: Option<String>) {
                spawn_subagent_with_optional_updates(&mut self.app, child_sid, updates.as_deref());
            }

            fn agent(&self) -> &AgentView {
                self.app.agents.get(&AgentId(0)).unwrap()
            }

            fn agent_mut(&mut self) -> &mut AgentView {
                self.app.agents.get_mut(&AgentId(0)).unwrap()
            }

            fn transcript(&self) -> ChildTranscript {
                self.agent().subagent_sessions[self.child_sid].transcript
            }

            fn set_transcript(&mut self, state: ChildTranscript) {
                let sid = self.child_sid;
                self.agent_mut()
                    .subagent_sessions
                    .get_mut(sid)
                    .unwrap()
                    .transcript = state;
            }

            fn set_background(&mut self) {
                let sid = self.child_sid;
                self.agent_mut()
                    .subagent_sessions
                    .get_mut(sid)
                    .unwrap()
                    .is_background = true;
            }

            /// Deliver the child's `SubagentFinished` through the real handler.
            fn finish(&mut self) {
                let _ = handle(
                    make_ext_session_notification_with_method(
                        "sess-parent",
                        "pi/session/update",
                        test_subagent_finished(self.child_sid),
                    ),
                    &mut self.app,
                );
            }

            fn open(&mut self) {
                self.open_child(self.child_sid);
            }

            fn open_child(&mut self, child_sid: &str) {
                let sid = child_sid.to_string();
                self.agent_mut().open_subagent_fullscreen(sid);
            }

            fn close(&mut self) {
                self.agent_mut().close_subagent_fullscreen();
            }

            fn push_child_block(&mut self, block: RenderBlock) {
                let sid = self.child_sid;
                self.agent_mut()
                    .subagent_views
                    .get_mut(sid)
                    .unwrap()
                    .scrollback
                    .push_block(block);
            }

            fn tool_calls(&self) -> usize {
                self.tool_calls_for(self.child_sid)
            }

            fn tool_calls_for(&self, child_sid: &str) -> usize {
                child_scrollback_tool_call_count(self.agent(), child_sid)
            }

            fn session_events(&self) -> usize {
                child_scrollback_session_event_count(self.agent(), self.child_sid)
            }

            fn prompts_matching(&self, prompt: &str) -> usize {
                child_scrollback_matching_prompt_count(self.agent(), self.child_sid, prompt)
            }

            fn has_system_block(&self) -> bool {
                let child = self.agent().subagent_views.get(self.child_sid).unwrap();
                (0..child.scrollback.len()).any(|i| {
                    matches!(
                        child.scrollback.entry(i).map(|e| &e.block),
                        Some(RenderBlock::System(_))
                    )
                })
            }

            fn compaction_markers(&self) -> usize {
                let child = self.agent().subagent_views.get(self.child_sid).unwrap();
                (0..child.scrollback.len())
                    .filter(|i| {
                        matches!(
                            child.scrollback.entry(*i).map(|e| &e.block),
                            Some(RenderBlock::SessionEvent(b)) if matches!(
                                b.event,
                                SessionEvent::CompactionStarted { .. }
                                    | SessionEvent::CompactionCompleted { .. }
                            )
                        )
                    })
                    .count()
            }

            fn updates_path(&self) -> std::path::PathBuf {
                replay_disk_test_home()
                    .join("sessions")
                    .join(urlencoding::encode("/tmp").as_ref())
                    .join(self.child_sid)
                    .join("updates.jsonl")
            }

            /// Replace `updates.jsonl` with a directory so a rebuild read
            /// fails (`Err`, not the missing-file `Empty`).
            fn break_transcript(&self) {
                let path = self.updates_path();
                std::fs::remove_file(&path).unwrap();
                std::fs::create_dir(&path).unwrap();
            }

            fn replace_transcript(&self, content: &str) {
                let path = self.updates_path();
                if path.is_dir() {
                    std::fs::remove_dir(&path).unwrap();
                }
                std::fs::write(&path, content).unwrap();
            }

            /// Remove the child session dir so a rebuild resolves `Empty`.
            fn remove_session_dir(&self) {
                std::fs::remove_dir_all(self.updates_path().parent().unwrap()).unwrap();
            }
        }

        fn child_compaction_started_line(child_sid: &str) -> String {
            format!(
                r#"{{"method":"_x.ai/session/update","params":{{"sessionId":"{child_sid}","update":{{"sessionUpdate":"auto_compact_started","tokens_used":9000,"context_window":10000,"percentage":90,"reason":"threshold"}}}}}}"#
            )
        }

        fn child_compaction_completed_line(child_sid: &str) -> String {
            format!(
                r#"{{"method":"_x.ai/session/update","params":{{"sessionId":"{child_sid}","update":{{"sessionUpdate":"auto_compact_completed","tokens_after":100,"elapsed_ms":5}}}}}}"#
            )
        }

    }

    #[test]
    fn a_spawn_for_an_unknown_session_is_ignored() {
        let mut app = make_app_with_agent("sess-A");
        let affected = handle(
            make_ext_session_notification_with_method(
                "sess-unknown",
                "pi/session/update",
                test_subagent_spawned("sess-unknown", "child-unknown"),
            ),
            &mut app,
        );

        assert!(!affected, "unknown session_id must not request a redraw");
        let agent = app.agents.get(&AgentId(0)).unwrap();
        assert!(
            agent.subagent_sessions.is_empty(),
            "SubagentSpawned for unknown session must not register subagent_sessions"
        );
        assert!(
            agent.scrollback.is_empty(),
            "SubagentSpawned for unknown session must not push scrollback"
        );
    }

    #[test]
    fn a_malformed_session_notification_is_ignored() {
        let mut app = make_app_with_agent("sess-A");
        let (tx, _rx) = tokio::sync::oneshot::channel();
        // Valid JSON but not a SessionNotification: parse must fail quietly.
        let raw =
            serde_json::value::to_raw_value(&serde_json::json!({"unexpected": true})).unwrap();
        let request = acp::ExtNotification::new("pi/session/update", raw.into());
        let msg = AcpClientMessage::ExtNotification(pi_acp_lib::AcpArgs {
            request,
            response_tx: tx,
        });

        let affected = handle(msg, &mut app);

        assert!(
            !affected,
            "malformed pi/session/update params must not redraw"
        );
        assert!(
            app.agents.get(&AgentId(0)).unwrap().scrollback.is_empty(),
            "malformed notification must not mutate scrollback"
        );
    }

    #[test]
    fn a_notification_reaches_its_target_agent_even_when_another_is_active() {
        // AutoCompactCompleted on the pi ext path resets the context bar
        // numerator via refresh_context_used. That side effect must run on
        // the matched agent regardless of which view is currently active.
        let mut app = make_app_with_agent("sess-A");
        insert_agent(&mut app, AgentId(1), Some("sess-B"));
        // Seed A with a stale context-used reading so we can prove the
        // notification reset it.
        {
            let agent_a = app.agents.get_mut(&AgentId(0)).unwrap();
            agent_a.apply_context_used(90_000, 131_072);
        }
        switch_active_to(&mut app, AgentId(1));

        let affected = handle(
            make_ext_session_notification(
                "sess-A",
                PiSessionUpdate::AutoCompactCompleted {
                    tokens_before: Some(90_000),
                    tokens_after: 25_000,
                    elapsed_ms: Some(300),
                    summary_preview: None,
                },
            ),
            &mut app,
        );

        let agent_a = app.agents.get(&AgentId(0)).unwrap();
        assert_eq!(
            agent_a.context_state.as_ref().map(|c| c.used),
            Some(25_000),
            "AutoCompactCompleted must reset A's context_used even when B is active"
        );
        assert!(
            !affected,
            "ext notification routed to a non-active agent must not request a redraw"
        );
    }

