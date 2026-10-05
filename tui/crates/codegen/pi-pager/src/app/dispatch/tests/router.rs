//! Tests for the action router, model switching, slash commands, and other cross-cutting dispatch behavior.
use super::*;
#[test]
fn auth_copy_dispatch_preserves_all_delivery_states() {
    for delivery in [
        crate::clipboard::ClipboardDelivery::Confirmed,
        crate::clipboard::ClipboardDelivery::Unverified,
        crate::clipboard::ClipboardDelivery::Failed,
    ] {
        let mut app = test_app();
        app.auth_state = AuthState::Authenticating {
            request_seq: 1,
            handle: None,
            auth_url: Some("https://grok.com/auth".to_owned()),
            mode: AuthMode::Command,
        };
        let effects = crate::app::dispatch::router::dispatch_copy_auth_url(&mut app, |url| {
            assert_eq!(url, "https://grok.com/auth");
            delivery
        });
        assert_eq!(app.auth_clipboard_delivery, Some(delivery));
        assert_eq!(app.auth_clipboard_feedback_generation, 1);
        assert!(matches!(
            effects.as_slice(),
            [Effect::ScheduleClearAuthCopyFeedback { generation: 1 }]
        ));
    }
}
#[test]
fn external_prompt_editor_arms_typed_request_and_preserves_composer_modes() {
    use crate::app::agent_view::PromptInputMode;
    for mode in [PromptInputMode::Normal, PromptInputMode::Bash] {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.screen_mode = crate::app::ScreenMode::Minimal;
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .prompt
            .set_screen_mode(crate::app::ScreenMode::Minimal);
        agent.prompt_input_mode = mode;
        agent.prompt.set_text("draft with\nnewlines");
        let effects = dispatch(Action::EditPromptExternal, &mut app);
        assert!(effects.is_empty());
        let request = app.pending_editor.take().expect("editor request");
        let crate::app::external_editor::PendingEditorRequest::PromptDraft {
            agent_id,
            original_text,
        } = request;
        assert_eq!(agent_id, id);
        assert_eq!(original_text, "draft with\nnewlines");
        assert_eq!(app.agents[&id].prompt_input_mode, mode);
        assert_eq!(app.agents[&id].prompt.text(), "draft with\nnewlines");
    }
}
#[test]
fn external_prompt_editor_arms_in_fullscreen_and_refuses_owned_input() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().prompt.set_text("draft");
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(
        matches!(
            app.pending_editor.take(),
            Some(crate::app::external_editor::PendingEditorRequest::PromptDraft { .. })
        ),
        "full TUI arms the request without requiring prompt-pane focus"
    );
    app.screen_mode = crate::app::ScreenMode::Minimal;
    app.agents.get_mut(&id).unwrap().active_pane = ActivePane::Scrollback;
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(
        matches!(
            app.pending_editor,
            Some(crate::app::external_editor::PendingEditorRequest::PromptDraft { .. })
        ),
        "the composer stays the editing surface with scrollback focused"
    );
    app.pending_editor = None;
    app.agents
        .get_mut(&id)
        .unwrap()
        .permission_queue
        .push_back(crate::app::agent_view::test_fixtures::make_followup_permission_state());
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none(), "modal owner must refuse");
    assert_eq!(app.agents[&id].prompt.text(), "draft");
    app.agents.get_mut(&id).unwrap().permission_queue.clear();
    app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::EditingQueued {
        id: 1,
        original: "queued".to_owned(),
        kind: crate::app::agent::QueueEntryKind::Prompt,
    };
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none(), "queue edit must refuse");
    assert_eq!(app.agents[&id].prompt.text(), "draft");
    app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::Normal;
    app.agents.get_mut(&id).unwrap().prompt.set_text("/");
    let models = app.agents[&id].session.models.clone();
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .refresh_slash(&models);
    assert!(app.agents[&id].prompt.any_dropdown_open());
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none(), "dropdown owner must refuse");
}
#[test]
fn external_prompt_editor_refuses_elements_with_visible_message() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.screen_mode = crate::app::ScreenMode::Minimal;
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .set_screen_mode(crate::app::ScreenMode::Minimal);
    let agent = app.agents.get_mut(&id).unwrap();
    let pasted = "one\ntwo\nthree\nfour";
    let _ = agent.prompt.handle_paste(pasted);
    assert!(!agent.prompt.textarea.elements().is_empty());
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none());
    assert_eq!(app.agents[&id].prompt.text(), pasted);
    assert!(!app.agents[&id].prompt.textarea.elements().is_empty());
    assert!(
        app.agents[&id]
            .scrollback
            .iter_entries()
            .any(|(_, entry)| entry.block.searchable_text().as_deref()
                == Some(crate::app::external_editor::ATTACHMENT_MESSAGE))
    );
    let agent = app.agents.get_mut(&id).unwrap();
    agent.prompt.set_text("");
    agent.prompt.textarea.insert_element(
        "@src/main.rs",
        crate::views::prompt_widget::KIND_FILE_REF,
        None,
    );
    let file_ref_text = agent.prompt.text().to_owned();
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none());
    assert_eq!(app.agents[&id].prompt.text(), file_ref_text);
    assert!(!app.agents[&id].prompt.textarea.elements().is_empty());
    let agent = app.agents.get_mut(&id).unwrap();
    agent.prompt.set_text("");
    let image = crate::prompt_images::PastedImage {
        element_id: pi_ratatui_textarea::ElementId::from_raw(0),
        display_number: 0,
        mime_type: "image/png".to_owned(),
        dimensions: Some((8, 8)),
        byte_len: 1,
        encoded_bytes: Some(vec![0].into()),
        source_path: None,
        staged_temp_path: None,
        session_image_path: None,
        preview: crate::prompt_images::PromptImagePreview::default(),
    };
    agent.prompt.insert_image(image).unwrap();
    let image_text = agent.prompt.text().to_owned();
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none());
    assert_eq!(app.agents[&id].prompt.text(), image_text);
    assert_eq!(app.agents[&id].prompt.images.len(), 1);
}
#[test]
fn external_prompt_editor_refuses_voice_and_pending_paste_with_visible_messages() {
    use crate::app::agent_view::AgentDeferredSend;
    use crate::app::app_view::{VoiceState, VoiceTarget};
    for voice_state in [
        VoiceState::ColdStart {
            hold: false,
            target: VoiceTarget::Agent(AgentId(0)),
        },
        VoiceState::Recording {
            hold: false,
            target: VoiceTarget::Agent(AgentId(0)),
            interim: Some("partial".to_owned()),
        },
        VoiceState::Stopping {
            target: VoiceTarget::Agent(AgentId(0)),
            interim: Some("partial".to_owned()),
        },
    ] {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.screen_mode = crate::app::ScreenMode::Minimal;
        app.voice_state = voice_state;
        app.agents.get_mut(&id).unwrap().prompt.set_text("draft");
        let _ = dispatch(Action::EditPromptExternal, &mut app);
        assert!(app.pending_editor.is_none());
        assert_eq!(app.agents[&id].prompt.text(), "draft");
        assert!(
            app.agents[&id]
                .scrollback
                .iter_entries()
                .any(|(_, entry)| entry.block.searchable_text().as_deref()
                    == Some(crate::app::external_editor::VOICE_MESSAGE))
        );
    }
    for (probes, deferred_send) in [
        (1, None),
        (1, Some(AgentDeferredSend::SendPrompt)),
        (0, Some(AgentDeferredSend::SendPrompt)),
    ] {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.screen_mode = crate::app::ScreenMode::Minimal;
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.set_text("draft");
        agent.paste_probe_in_flight = probes;
        agent.deferred_send = deferred_send;
        let _ = dispatch(Action::EditPromptExternal, &mut app);
        assert!(app.pending_editor.is_none());
        assert_eq!(app.agents[&id].prompt.text(), "draft");
        assert_eq!(app.agents[&id].paste_probe_in_flight, probes);
        assert_eq!(app.agents[&id].deferred_send, deferred_send);
        assert!(
            app.agents[&id]
                .scrollback
                .iter_entries()
                .any(|(_, entry)| entry.block.searchable_text().as_deref()
                    == Some(crate::app::external_editor::PASTE_MESSAGE))
        );
    }
}
#[test]
fn deferred_paste_completion_after_refused_editor_does_not_implicitly_send_without_stash() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.screen_mode = crate::app::ScreenMode::Minimal;
    let agent = app.agents.get_mut(&id).unwrap();
    agent.prompt.set_text("draft");
    let draft_len = agent.prompt.text().len();
    agent.prompt.set_cursor(draft_len);
    agent.paste_probe_in_flight = 1;
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none());
    let effects = dispatch(
        Action::TaskComplete(TaskResult::ClipboardAttachmentProbed {
            ctx: crate::app::actions::ClipboardPasteContext {
                target: crate::app::actions::ClipboardPasteTarget::AgentPrompt {
                    agent_id: id,
                    images_dir: None,
                },
                source: crate::app::actions::ClipboardPasteSource::ClipboardKey {
                    text: crate::app::actions::ClipboardTextRead::Success(Some(
                        "pasted".to_owned(),
                    )),
                    tip_showing: false,
                },
            },
            image: crate::app::actions::ProbedAttachment::NoRaster,
            file_urls: None,
        }),
        &mut app,
    );
    assert!(effects.is_empty(), "no deferred submit was armed");
    assert_eq!(app.agents[&id].prompt.text(), "draftpasted");
    assert!(app.agents[&id].session.pending_prompts.is_empty());
}
#[test]
fn external_prompt_editor_result_replaces_or_clears_without_sending() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    app.agents.get_mut(&id).unwrap().prompt.set_text("original");
    crate::app::external_editor::apply_prompt_text(&mut app, id, "edited\n".to_owned());
    assert_eq!(app.agents[&id].prompt.text(), "edited\n");
    assert!(app.agents[&id].session.state.is_turn_running());
    assert!(app.agents[&id].session.pending_prompts.is_empty());
    crate::app::external_editor::apply_prompt_text(&mut app, id, String::new());
    assert_eq!(app.agents[&id].prompt.text(), "");
    assert!(app.agents[&id].session.state.is_turn_running());
}
#[test]
fn editor_failure_targets_original_agent_and_vanished_agent_is_safe() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().prompt.set_text("original");
    crate::app::external_editor::report_prompt_failure(&mut app, id, "editor failed");
    assert_eq!(app.agents[&id].prompt.text(), "original");
    assert!(
        app.agents[&id]
            .scrollback
            .iter_entries()
            .any(|(_, entry)| entry.block.searchable_text().as_deref() == Some("editor failed"))
    );
    app.agents.shift_remove(&id);
    crate::app::external_editor::apply_prompt_text(&mut app, id, "ignored".to_owned());
    crate::app::external_editor::report_prompt_failure(&mut app, id, "ignored");
    assert!(app.agents.is_empty());
}
#[test]
fn quit_returns_quit_effect() {
    let mut app = test_app();
    let effects = dispatch(Action::Quit, &mut app);
    assert!(matches!(effects.as_slice(), [Effect::Quit]));
}
#[test]
fn follow_up_chip_does_not_execute_slash_command() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    assert!(!app.agents[&id].session.is_yolo());
    let effects = dispatch(Action::SubmitFollowUp("/always-approve".into()), &mut app);
    assert!(
        !app.agents[&id].session.is_yolo(),
        "a /always-approve chip must NOT flip YOLO mode"
    );
    assert!(
        matches!(&effects[..], [Effect::SendPrompt { text, .. }] if text == "/always-approve"),
        "chip text must be submitted literally, got {effects:?}"
    );
}
#[test]
fn follow_up_chip_does_not_execute_exit_alias() {
    let mut app = test_app_with_agent();
    let effects = dispatch(Action::SubmitFollowUp("quit".into()), &mut app);
    assert!(
        matches!(&effects[..], [Effect::SendPrompt { text, .. }] if text == "quit"),
        "bare 'quit' chip must be a literal prompt, got {effects:?}"
    );
}
#[test]
fn mark_turn_finished_clears_start_and_stamps_active() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent.turn_started_at = Some(std::time::Instant::now());
    agent.last_active_at = None;
    agent.mark_turn_finished(crate::app::cancel_latency::TurnEnd::Completed);
    assert!(
        agent.turn_started_at.is_none(),
        "turn_started_at must be cleared"
    );
    assert!(
        agent.last_active_at.is_some(),
        "last_active_at must be stamped"
    );
}
fn critical_announcement(id: &str) -> pi_announcements::RemoteAnnouncement {
    pi_announcements::RemoteAnnouncement {
        id: Some(id.into()),
        title: Some(format!("{id} title")),
        message: Some(format!("{id} message")),
        severity: Some("critical".into()),
        ..Default::default()
    }
}
fn promo_announcement(id: &str) -> pi_announcements::RemoteAnnouncement {
    pi_announcements::RemoteAnnouncement {
        id: Some(id.into()),
        message: Some(format!("{id} message")),
        severity: Some("promo".into()),
        cta: Some(pi_announcements::AnnouncementCta {
            label: Some("Go".into()),
            url: Some(format!("https://example.com/{id}")),
            caption: None,
        }),
        ..Default::default()
    }
}
/// `AnnouncementCtaShown` latches once per (announcement, surface): first
/// frame with an armed CTA rect emits, later frames don't, and a NEW
/// announcement id re-emits on the same surfaces.
#[test]
fn cta_impressions_latch_once_per_surface_and_reemit_for_new_id() {
    use crate::app::app_view::ActiveView;
    use pi_telemetry::events::AnnouncementCtaSurface;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.active_view = ActiveView::Agent(id);
    app.active_announcements = vec![promo_announcement("promo-a")];
    let rect = Some(ratatui::layout::Rect::new(0, 0, 4, 1));
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.hit_announcement_cta.set(rect);
        agent.hit_upgrade_cta.set(rect);
    }
    app.log_announcement_cta_impressions();
    let logged = &app.announcement_cta_impressions_logged;
    assert_eq!(logged.len(), 2);
    assert!(logged.contains(&("promo-a".to_string(), AnnouncementCtaSurface::Banner)));
    assert!(logged.contains(&("promo-a".to_string(), AnnouncementCtaSurface::Header)));
    app.log_announcement_cta_impressions();
    assert_eq!(app.announcement_cta_impressions_logged.len(), 2);
    app.active_announcements = vec![promo_announcement("promo-b")];
    app.log_announcement_cta_impressions();
    let logged = &app.announcement_cta_impressions_logged;
    assert_eq!(logged.len(), 4);
    assert!(logged.contains(&("promo-b".to_string(), AnnouncementCtaSurface::Banner)));
    assert!(logged.contains(&("promo-b".to_string(), AnnouncementCtaSurface::Header)));
}
/// No impression without a painted button under a promo slot owner: a
/// critical preempting the slot, a hidden promo, and cleared (unpainted)
/// rects all emit nothing — the same gate the click dispatch resolves.
#[test]
fn cta_impressions_respect_slot_gate_and_paint() {
    use crate::app::app_view::ActiveView;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.active_view = ActiveView::Agent(id);
    let rect = Some(ratatui::layout::Rect::new(0, 0, 4, 1));
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.hit_announcement_cta.set(rect);
        agent.hit_upgrade_cta.set(rect);
    }
    app.active_announcements = vec![critical_announcement("crit"), promo_announcement("p")];
    app.log_announcement_cta_impressions();
    assert!(app.announcement_cta_impressions_logged.is_empty());
    app.active_announcements = vec![promo_announcement("p")];
    app.hidden_announcement_ids = ["p".to_string()].into_iter().collect();
    app.log_announcement_cta_impressions();
    assert!(app.announcement_cta_impressions_logged.is_empty());
    app.hidden_announcement_ids.clear();
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.hit_announcement_cta.clear();
        agent.hit_upgrade_cta.clear();
    }
    app.log_announcement_cta_impressions();
    assert!(app.announcement_cta_impressions_logged.is_empty());
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.hit_announcement_cta.clear();
        agent.hit_upgrade_cta.clear();
    }
    app.active_announcements = vec![promo_announcement("p2")];
    app.log_announcement_cta_impressions();
    assert!(app.announcement_cta_impressions_logged.is_empty());
}
/// Frame occluders (the goal-detail class) leave rects armed and block clicks
/// at dispatch time — impressions mirror the OSC 8 drop-whole rule: an
/// occluded CTA is not counted until an overlay-free frame shows it clean.
#[test]
fn cta_impressions_suppressed_while_rect_occluded() {
    use crate::app::app_view::ActiveView;
    use pi_telemetry::events::AnnouncementCtaSurface;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.active_view = ActiveView::Agent(id);
    app.active_announcements = vec![promo_announcement("p")];
    let rect = ratatui::layout::Rect::new(0, 0, 4, 1);
    let overlay = ratatui::layout::Rect::new(0, 0, 80, 1);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.hit_announcement_cta.set(Some(rect));
        agent.hit_upgrade_cta.set(Some(rect));
        agent.frame_occluder_rects.push(overlay);
    }
    app.log_announcement_cta_impressions();
    assert!(app.announcement_cta_impressions_logged.is_empty());
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.frame_occluder_rects.clear();
    }
    app.log_announcement_cta_impressions();
    let logged = &app.announcement_cta_impressions_logged;
    assert_eq!(logged.len(), 2);
    assert!(logged.contains(&("p".to_string(), AnnouncementCtaSurface::Banner)));
    assert!(logged.contains(&("p".to_string(), AnnouncementCtaSurface::Header)));
    app.log_announcement_cta_impressions();
    assert_eq!(app.announcement_cta_impressions_logged.len(), 2);
}
/// The welcome hero surface latches from its own armed rect (only the active
/// view's rects are consulted).
#[test]
fn cta_impressions_cover_welcome_surface() {
    use crate::app::app_view::ActiveView;
    use pi_telemetry::events::AnnouncementCtaSurface;
    let mut app = test_app_with_agent();
    app.active_announcements = vec![promo_announcement("p")];
    let rect = Some(ratatui::layout::Rect::new(0, 0, 4, 1));
    app.active_view = ActiveView::Welcome;
    app.welcome_upgrade_cta_rect = rect;
    app.log_announcement_cta_impressions();
    let logged = &app.announcement_cta_impressions_logged;
    assert!(logged.contains(&("p".to_string(), AnnouncementCtaSurface::Welcome)));
    app.active_view = ActiveView::Agent(AgentId(0));
    app.active_announcements = vec![promo_announcement("q")];
    app.log_announcement_cta_impressions();
    let logged = &app.announcement_cta_impressions_logged;
    assert!(!logged.contains(&("q".to_string(), AnnouncementCtaSurface::Welcome)));
    assert_eq!(logged.len(), 1);
}
#[test]
fn switch_model_dispatch_produces_effect_and_sets_pending() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("grok-4.5"));
    assert!(!app.agents[&id].session.model_switch_pending);
    let effects = dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SwitchModel { model_id: mid, .. } if mid == &model_id));
    assert!(app.agents[&id].session.model_switch_pending);
    assert!(app.agents[&id].session.state.is_idle());
}
#[test]
fn switch_model_allowed_when_agent_chat_kind() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().chat_kind = true;
    let model_id = acp::ModelId::new(std::sync::Arc::from("auto"));
    let effects = dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SwitchModel { model_id: mid, .. } if mid == &model_id));
    assert!(app.agents[&id].session.model_switch_pending);
}
#[test]
fn switch_model_allowed_when_app_chat_mode() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.chat_mode = true;
    let model_id = acp::ModelId::new(std::sync::Arc::from("auto"));
    let effects = dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SwitchModel { model_id: mid, .. } if mid == &model_id));
    assert!(app.agents[&id].session.model_switch_pending);
}
#[test]
fn agent_type_mismatch_cancel_is_noop() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("cursor-model"));
    let agent_count_before = app.agents.len();
    let effects = dispatch(
        Action::AgentTypeMismatchAnswered {
            start_new: false,
            model_id,
            effort: None,
        },
        &mut app,
    );
    assert!(effects.is_empty());
    assert_eq!(app.agents.len(), agent_count_before);
    assert!(matches!(app.active_view, ActiveView::Agent(a) if a == id));
}
#[test]
fn agent_type_mismatch_with_effort_stashes_deferred_switch() {
    use pi_shell::sampling::types::ReasoningEffort;
    let mut app = test_app_with_agent();
    let model_id = acp::ModelId::new(std::sync::Arc::from("cursor-reasoning"));
    let effort = Some(ReasoningEffort::High);
    let effects = dispatch(
        Action::AgentTypeMismatchAnswered {
            start_new: true,
            model_id: model_id.clone(),
            effort,
        },
        &mut app,
    );
    let create = effects
        .iter()
        .find(|e| matches!(e, Effect::CreateSession { .. }));
    assert!(create.is_some(), "expected CreateSession effect");
    match create.unwrap() {
        Effect::CreateSession { model_id: mid, .. } => {
            assert_eq!(mid.as_ref(), Some(&model_id));
        }
        _ => unreachable!(),
    }
    if let ActiveView::Agent(new_aid) = app.active_view {
        let agent = &app.agents[&new_aid];
        assert_eq!(
            agent.session.deferred_model_switch,
            Some(crate::app::agent::DeferredModelSwitch {
                model_id,
                effort,
                prev_model_id: None,
            }),
            "effort override must be stashed for the shell via deferred_model_switch",
        );
    } else {
        panic!("expected active view to be an Agent");
    }
}
#[test]
fn deferred_model_switch_still_works_for_cli_override() {
    let mut app = test_app();
    let cli_model = acp::ModelId::new(std::sync::Arc::from("cli-override"));
    app.cli_model_override = Some(cli_model.clone());
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    assert_eq!(
        app.agents[&id].session.deferred_model_switch,
        Some(crate::app::agent::DeferredModelSwitch {
            model_id: cli_model,
            effort: None,
            prev_model_id: None,
        }),
        "CLI -m override must still populate deferred_model_switch",
    );
}
#[test]
fn test_helper_agent_uses_generation_zero() {
    let app = test_app_with_agent();
    let id = AgentId(0);
    assert!(app.agents[&id].session.available_commands.is_empty());
    assert_eq!(app.agents[&id].session.available_commands_generation, 0);
    assert!(!app.agents[&id].session.model_switch_pending);
}
#[test]
fn slash_exit_dispatches_quit() {
    let mut app = test_app_with_agent();
    let effects = dispatch(Action::SendPrompt("/exit".into()), &mut app);
    assert!(
        effects.last().is_some_and(|e| matches!(e, Effect::Quit)),
        "expected Quit as last effect, got: {effects:?}"
    );
}
#[test]
fn slash_quit_alias_dispatches_quit() {
    let mut app = test_app_with_agent();
    let effects = dispatch(Action::SendPrompt("/quit".into()), &mut app);
    assert!(effects.last().is_some_and(|e| matches!(e, Effect::Quit)));
}
#[test]
fn slash_new_does_not_cancel_running_turn() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let effects = dispatch(Action::SendPrompt("/new".into()), &mut app);
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateSession { .. })),
        "expected CreateSession, got: {effects:?}",
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::CancelTurn { .. }))
    );
    assert!(
        app.agents[&id].session.state.is_turn_running(),
        "old agent's turn must remain running"
    );
}
#[test]
fn slash_new_uses_active_agent_cwd() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let agent_cwd = PathBuf::from("/custom/agent/cwd");
    app.agents.get_mut(&id).unwrap().session.cwd = agent_cwd.clone();
    let effects = dispatch(Action::SendPrompt("/new".into()), &mut app);
    let create = effects
        .iter()
        .find(|e| matches!(e, Effect::CreateSession { .. }));
    assert!(create.is_some(), "expected CreateSession effect");
    match create.unwrap() {
        Effect::CreateSession { cwd, .. } => assert_eq!(cwd, &agent_cwd),
        _ => unreachable!(),
    }
    let new_id = AgentId(1);
    assert!(!app.agents[&new_id].session.is_worktree);
}
#[test]
fn slash_model_invalid_arg_produces_scrollback_error() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let initial_scrollback = app.agents[&id].scrollback.len();
    let effects = dispatch(Action::SendPrompt("/model nonexistent".into()), &mut app);
    assert!(effects.is_empty(), "error should not produce effects");
    assert_eq!(app.agents[&id].scrollback.len(), initial_scrollback + 1);
    assert!(app.agents[&id].prompt.text().is_empty());
}
#[test]
fn slash_model_no_args_produces_scrollback_error() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let initial_scrollback = app.agents[&id].scrollback.len();
    let effects = dispatch(Action::SendPrompt("/model".into()), &mut app);
    assert!(effects.is_empty());
    assert_eq!(app.agents[&id].scrollback.len(), initial_scrollback + 1);
}
#[ignore = "pi-python: grok-specific feature not supported"]
#[test]
fn acp_bootstrap_command_appears_in_autocomplete() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![acp::AvailableCommand::new(
        "flush".to_string(),
        "Flush memory".to_string(),
    )];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    let models = app.agents[&id].session.models.clone();
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .textarea
        .insert_str("/flu");
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .refresh_slash(&models);
    let snap = app.agents[&id].prompt.slash_snapshot();
    assert!(snap.open, "dropdown should be open");
    assert!(
        snap.matches.iter().any(|r| r.display == "/flush"),
        "bootstrap ACP command should appear in matches, got: {:?}",
        snap.matches.iter().map(|r| &r.display).collect::<Vec<_>>()
    );
}
#[test]
fn acp_bootstrap_command_executes_as_passthrough() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![acp::AvailableCommand::new(
        "flush".to_string(),
        "Flush memory".to_string(),
    )];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.session_id = Some("sess-1".into());
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    let effects = dispatch(Action::SendPrompt("/flush".into()), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(
        matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "/flush"),
        "ACP command should passthrough, got: {effects:?}"
    );
}
#[ignore = "pi-python: grok-specific feature not supported"]
#[test]
fn acp_runtime_update_replaces_commands_in_autocomplete() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![acp::AvailableCommand::new(
        "old-cmd".to_string(),
        "Old command".to_string(),
    )];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    app.agents.get_mut(&id).unwrap().session.available_commands = vec![acp::AvailableCommand::new(
        "new-cmd".to_string(),
        "New command".to_string(),
    )];
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .available_commands_generation += 1;
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    let models = app.agents[&id].session.models.clone();
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .textarea
        .insert_str("/new");
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .refresh_slash(&models);
    let snap = app.agents[&id].prompt.slash_snapshot();
    assert!(
        snap.matches.iter().any(|r| r.display == "/new-cmd"),
        "new ACP command should appear"
    );
    assert!(
        !snap.matches.iter().any(|r| r.display == "/old-cmd"),
        "old ACP command should be replaced"
    );
}
#[ignore = "pi-python: grok-specific feature not supported"]
#[test]
fn acp_command_colliding_with_builtin_skipped_in_autocomplete() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![
        acp::AvailableCommand::new(
            "exit".to_string(),
            "ACP exit (should be skipped)".to_string(),
        ),
        acp::AvailableCommand::new("flush".to_string(), "Flush memory".to_string()),
    ];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    let registry = app.agents[&id].prompt.slash_controller.registry();
    let exit_cmd = registry.get("exit").unwrap();
    assert_eq!(exit_cmd.description(), "Quit the application");
    assert!(registry.get("flush").is_some());
}
#[ignore = "pi-python: grok-specific feature not supported"]
#[test]
fn acp_command_with_arg_hint_shows_placeholder() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![
        acp::AvailableCommand::new("search".to_string(), "Search codebase".to_string()).input(
            Some(acp::AvailableCommandInput::Unstructured(
                acp::UnstructuredCommandInput::new("<query>".to_string()),
            )),
        ),
    ];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    let registry = app.agents[&id].prompt.slash_controller.registry();
    let search_cmd = registry.get("search").unwrap();
    assert!(search_cmd.takes_args());
    assert!(!search_cmd.args_required());
    assert_eq!(search_cmd.arg_placeholder(), Some("<query>"));
}
#[test]
fn acp_command_with_args_passthrough_includes_args() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![
        acp::AvailableCommand::new("search".to_string(), "Search codebase".to_string()).input(
            Some(acp::AvailableCommandInput::Unstructured(
                acp::UnstructuredCommandInput::new("<query>".to_string()),
            )),
        ),
    ];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.session_id = Some("sess-1".into());
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.sync_acp_commands(
            &agent.session.available_commands,
            agent.session.available_tools.as_ref(),
            &agent.session.models,
        );
    }
    let effects = dispatch(Action::SendPrompt("/search find bugs".into()), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(
        matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "/search find bugs"),
        "ACP passthrough should preserve args, got: {effects:?}"
    );
}
#[test]
fn generation_lifecycle_bootstrap_through_runtime_update() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![acp::AvailableCommand::new(
        "initial".to_string(),
        "Initial command".to_string(),
    )];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    assert_eq!(app.agents[&id].session.available_commands_generation, 1);
    assert_eq!(app.agents[&id].session.available_commands.len(), 1);
    assert_eq!(app.agents[&id].acp_synced_generation, 0);
    app.agents.get_mut(&id).unwrap().session.available_commands = vec![acp::AvailableCommand::new(
        "updated".to_string(),
        "Updated command".to_string(),
    )];
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .available_commands_generation += 1;
    assert_eq!(app.agents[&id].session.available_commands_generation, 2);
    assert_eq!(
        app.agents[&id].session.available_commands[0].name,
        "updated"
    );
}
#[test]
fn tick_propagates_available_commands_to_bootstrap() {
    let mut app = test_app();
    app.bootstrap_acp_commands = vec![acp::AvailableCommand::new(
        "compact".to_string(),
        "Builtin only".to_string(),
    )];
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    app.active_view = crate::app::app_view::ActiveView::Agent(id);
    let skill_meta = serde_json::json!({
        "scope": "user",
        "path": "/home/user/.grok/skills/pick-best/SKILL.md",
    });
    app.agents.get_mut(&id).unwrap().session.available_commands = vec![
        acp::AvailableCommand::new("compact".to_string(), "Builtin".to_string()),
        acp::AvailableCommand::new("pick-best".to_string(), "Parallel tournament".to_string())
            .meta(skill_meta.as_object().cloned()),
    ];
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .available_commands_generation += 1;
    app.tick();
    assert_eq!(
        app.bootstrap_acp_commands.len(),
        2,
        "bootstrap should now include the skill"
    );
    assert!(
        app.bootstrap_acp_commands
            .iter()
            .any(|c| c.name == "pick-best"),
        "pick-best should be in bootstrap_acp_commands, got: {:?}",
        app.bootstrap_acp_commands
            .iter()
            .map(|c| &c.name)
            .collect::<Vec<_>>()
    );
    dispatch(Action::NewSession, &mut app);
    let new_id = AgentId(1);
    assert_eq!(app.agents[&new_id].session.available_commands.len(), 2);
    assert!(
        app.agents[&new_id]
            .session
            .available_commands
            .iter()
            .any(|c| c.name == "pick-best")
    );
}
#[test]
fn all_constructor_paths_initialize_slash_fields() {
    let mut app = test_app();
    dispatch(Action::NewSession, &mut app);
    {
        let s = &app.agents[&AgentId(0)].session;
        assert_eq!(s.available_commands_generation, 1);
        assert!(!s.model_switch_pending);
    }
    dispatch(Action::LoadSession("sess-1".into(), None, false), &mut app);
    {
        let s = &app.agents[&AgentId(1)].session;
        assert_eq!(s.available_commands_generation, 1);
        assert!(!s.model_switch_pending);
    }
    app.cwd = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    app.cwd_has_git_ancestor = true;
    dispatch(
        Action::NewWorktreeSession {
            load_session_id: None,
            label: None,
            git_ref: None,
        },
        &mut app,
    );
    {
        let s = &app.agents[&AgentId(2)].session;
        assert_eq!(s.available_commands_generation, 1);
        assert!(!s.model_switch_pending);
    }
    let test_app = test_app_with_agent();
    {
        let s = &test_app.agents[&AgentId(0)].session;
        assert_eq!(s.available_commands_generation, 0);
        assert!(!s.model_switch_pending);
    }
}
#[test]
fn deferred_switch_overwritten_by_second_switch() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_a = acp::ModelId::new(std::sync::Arc::from("model-a"));
    let model_b = acp::ModelId::new(std::sync::Arc::from("model-b"));
    app.agents.get_mut(&id).unwrap().session.session_id = None;
    dispatch(
        Action::SwitchModel {
            model_id: model_a.clone(),
            effort: None,
        },
        &mut app,
    );
    dispatch(
        Action::SwitchModel {
            model_id: model_b.clone(),
            effort: None,
        },
        &mut app,
    );
    assert_eq!(
        app.agents[&id].session.deferred_model_switch,
        Some(crate::app::agent::DeferredModelSwitch {
            model_id: model_b.clone(),
            effort: None,
            prev_model_id: Some(model_a),
        })
    );
}
#[test]
fn pick_over_cli_seed_keeps_display_as_rollback_target() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let displayed = acp::ModelId::new(std::sync::Arc::from("displayed-model"));
    let cli_model = acp::ModelId::new(std::sync::Arc::from("cli-model"));
    let picked = acp::ModelId::new(std::sync::Arc::from("picked-model"));
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.session_id = None;
    agent.session.models.current = Some(displayed.clone());
    agent.session.deferred_model_switch = Some(crate::app::agent::DeferredModelSwitch {
        model_id: cli_model,
        effort: None,
        prev_model_id: None,
    });
    dispatch(
        Action::SwitchModel {
            model_id: picked.clone(),
            effort: None,
        },
        &mut app,
    );
    assert_eq!(
        app.agents[&id].session.deferred_model_switch,
        Some(crate::app::agent::DeferredModelSwitch {
            model_id: picked,
            effort: None,
            prev_model_id: Some(displayed),
        })
    );
}
#[test]
fn deferred_switch_updates_display_and_persists() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("model-b"));
    app.agents.get_mut(&id).unwrap().session.session_id = None;
    let effects = dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    let agent = &app.agents[&id];
    assert_eq!(
        agent.session.models.current,
        Some(model_id.clone()),
        "pre-session pick must update the displayed model immediately"
    );
    assert_eq!(
        agent.session.deferred_model_switch,
        Some(crate::app::agent::DeferredModelSwitch {
            model_id: model_id.clone(),
            effort: None,
            prev_model_id: None,
        }),
        "switch must still round-trip once the session exists"
    );
    assert!(
        !agent.session.model_switch_pending,
        "nothing is in flight yet — the queue must not be blocked"
    );
    assert!(
        matches!(
            &effects[..],
            [Effect::PersistPreferredModel { model_id: m, .. }] if m == &model_id
        ),
        "expected a single PersistPreferredModel effect, got {effects:?}"
    );
    let effects = dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    assert!(
        effects.is_empty(),
        "unchanged pre-session pick must not re-persist, got {effects:?}"
    );
}
/// Process-wide `--chat` + non-conversation resume of a non-disk id still
/// loads (gateway conversation) with agent chat_kind from sticky mode.
#[test]
fn chat_mode_resume_without_local_disk_loads_as_chat() {
    let mut app = test_app();
    app.chat_mode = true;
    let effects = dispatch(
        Action::LoadSession("remote-conv-only".into(), None, false),
        &mut app,
    );
    assert!(matches!(
        &effects[..],
        [Effect::LoadSession {
            session_id,
            chat_kind: false,
            ..
        }] if session_id == "remote-conv-only"
    ));
    let agent = app.agents.values().next().expect("agent");
    assert!(
        agent.chat_kind,
        "sticky --chat must set agent chat_kind even without entry bit"
    );
    assert!(
        agent.conversation_entry,
        "sticky --chat gateway resume (no local disk) opens as chat"
    );
    assert!(
        agent.app_chat_mode,
        "app.chat_mode must propagate to AgentView::app_chat_mode"
    );
}
/// Process-wide `--chat` refuses local Build disk rows (no LoadSession).
#[test]
fn chat_mode_refuses_local_build_disk_load() {
    let cwd = PathBuf::from(format!(
        "/tmp/chat-mode-build-refuse-{}",
        std::process::id()
    ));
    let session_id = format!("build-disk-{}", std::process::id());
    let sess_dir = plant_local_build_session(&cwd, &session_id);
    let mut app = test_app();
    app.cwd = cwd;
    app.chat_mode = true;
    let effects = dispatch(Action::LoadSession(session_id, None, false), &mut app);
    let _ = std::fs::remove_dir_all(&sess_dir);
    assert!(
        effects.is_empty(),
        "local Build under --chat must refuse, got {effects:?}"
    );
    assert!(
        app.agents.is_empty(),
        "refuse must not allocate an agent slot"
    );
}
/// Conversation entry under `--chat` still loads even if a local path exists.
#[test]
fn chat_mode_allows_conversation_entry_even_if_local_path() {
    let cwd = PathBuf::from(format!("/tmp/chat-mode-conv-ok-{}", std::process::id()));
    let session_id = format!("conv-also-local-{}", std::process::id());
    let sess_dir = plant_local_build_session(&cwd, &session_id);
    let mut app = test_app();
    app.cwd = cwd;
    app.chat_mode = true;
    let effects = dispatch(Action::LoadSession(session_id, None, true), &mut app);
    let _ = std::fs::remove_dir_all(&sess_dir);
    assert!(matches!(
        &effects[..],
        [Effect::LoadSession {
            chat_kind: true,
            ..
        }]
    ));
    let agent = app.agents.values().next().expect("agent");
    assert!(
        agent.conversation_entry,
        "conversation-entry bit must stamp conversation_entry even if a local path exists"
    );
}
#[test]
fn entry_title_uses_display_name_when_set() {
    use crate::views::session_title::entry_title;
    let mut app = test_app_with_agent();
    if let Some(a) = app.agents.get_mut(&AgentId(0)) {
        a.display_name = Some("custom title".into());
    }
    let title = entry_title(&app.agents[&AgentId(0)]);
    assert_eq!(title, "custom title");
}
/// Cross-setting smoke test.
/// Verifies that the dispatcher routes each Action to the
/// correct setter (catches a copy-paste registration bug
/// where two setters were swapped). The original 5-setting
/// matrix shrank to 2 after the user-feedback drop of three of the settings
/// (`session_picker_grouped`, `load_envrc` and a process-model toggle).
#[test]
fn pr13_each_setter_writes_to_its_own_mirror() {
    let mut app = test_app_with_agent();
    assert_eq!(app.show_tips, None);
    assert_eq!(app.auto_update, None);
    let _ = dispatch(Action::SetShowTips(false), &mut app);
    assert_eq!(app.show_tips, Some(false));
    assert_eq!(app.auto_update, None);
    let _ = dispatch(Action::SetAutoUpdate(false), &mut app);
    assert_eq!(app.auto_update, Some(false));
    assert_eq!(app.show_tips, Some(false));
}
/// Three-way alignment pin: the PAGER registry default must agree
/// with `PagerLocalSnapshot::default()` (covered by
/// `defaults_match_pager_state` in `registry::tests`) AND with
/// `AgentView::new`'s runtime initializer. This is the third leg
/// of the triangle that was previously missing — the
/// registry test alone can't see `AgentView::new`'s constant.
#[test]
fn pager_registry_default_matches_agent_view_new_initializer() {
    use crate::settings::{SettingKind, SettingOwner, SettingsRegistry};
    let app = test_app_with_agent();
    let agent = app
        .agents
        .get(&AgentId(0))
        .expect("test_app_with_agent must create AgentId(0)");
    let reg = SettingsRegistry::defaults();
    for meta in reg.all() {
        if meta.owner != SettingOwner::Pager {
            continue;
        }
        match (meta.key, &meta.kind) {
            ("multiline_mode", SettingKind::Bool { default }) => {
                assert_eq!(
                    *default, agent.multiline_mode,
                    "registry default for `multiline_mode` ({default}) drifts from \
                         AgentView::new's initializer ({}). Update one to match the \
                         other — the registry is the contract surface.",
                    agent.multiline_mode,
                );
            }
            ("plan_mode", SettingKind::Enum { default, .. }) => {
                let effective = agent.plan_mode_pending.unwrap_or(agent.plan_mode_active);
                let expected = if effective { "on" } else { "off" };
                assert_eq!(
                    *default, expected,
                    "registry default for `plan_mode` (`{default}`) drifts from \
                         AgentView::new's initializer (effective={effective} → \
                         expected `{expected}`). Update one to match the other — \
                         the registry is the contract surface.",
                );
            }
            ("respect_manual_folds", SettingKind::Bool { default }) => {
                let live = agent
                    .scrollback
                    .appearance()
                    .scrollback
                    .scroll
                    .respect_manual_folds;
                assert_eq!(
                    *default, live,
                    "registry default for `respect_manual_folds` ({default}) drifts \
                         from the agent's default appearance config ({live}). Update one \
                         to match the other — ScrollConfig::default() is the source of \
                         truth.",
                );
            }
            _ => {
                panic!(
                    "PAGER setting `{}` has no arm in \
                     pager_registry_default_matches_agent_view_new_initializer — \
                     add an arm that pins the registry default against the runtime \
                     initializer in `AgentView::new` (or, for future fields, the \
                     equivalent runtime construction site).",
                    meta.key,
                )
            }
        }
    }
}
/// If the user picks the regular "Yes, proceed" option (NOT
/// enable-always-approve), the dispatcher must behave exactly as
/// before — no PersistPermissionMode effect, no YOLO flip. Pins
/// that the new code path is gated strictly on the id check.
#[test]
fn regular_allow_once_does_not_trigger_always_approve_persist() {
    use std::sync::Arc;
    let mut app = test_app_with_agent();
    let _response_rx = enqueue_permission_with_enable_always_approve(&mut app);
    let effects = dispatch(
        Action::PermissionSelect(acp::PermissionOptionId::new(Arc::from("opt-allow-once"))),
        &mut app,
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPermissionMode { .. })),
        "picking the regular AllowOnce option must NOT emit PersistPermissionMode — \
             the always-approve mode is opt-in via the dedicated option only",
    );
    assert!(
        !app.agents[&AgentId(0)].session.is_yolo(),
        "session.yolo_mode must remain OFF when the regular AllowOnce option is picked",
    );
    assert!(
        !app.default_yolo,
        "app.default_yolo must remain OFF when the regular AllowOnce option is picked",
    );
}
/// Launch-time blocked `--yolo` in the TUI: the one-shot notice is
/// surfaced (toast + durable system line) on the first agent view and
/// consumed so later switches stay quiet.
#[test]
fn switch_to_agent_surfaces_launch_block_notice_once() {
    let mut app = test_app();
    app.yolo_launch_block_notice = Some(POLICY_WARNING);
    let id = AgentId(0);
    let session = make_test_agent_session(&app, id, "test-session");
    app.agents
        .insert(id, AgentView::new(session, ScrollbackState::new()));
    switch_to_agent(&mut app, id, SwitchCause::New);
    let agent = &app.agents[&id];
    assert_eq!(
        agent.toast.as_ref().map(|(s, _)| s.as_str()),
        Some(POLICY_WARNING),
    );
    let system_texts: Vec<&str> = agent
        .scrollback
        .iter_entries()
        .filter_map(|(_, e)| match &e.block {
            crate::scrollback::block::RenderBlock::System(s) => Some(s.text.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(
        system_texts,
        vec![POLICY_WARNING],
        "warning must land in the transcript exactly once",
    );
    assert_eq!(
        app.yolo_launch_block_notice, None,
        "one-shot must be consumed"
    );
    let id2 = AgentId(1);
    let session2 = make_test_agent_session(&app, id2, "test-session-2");
    app.agents
        .insert(id2, AgentView::new(session2, ScrollbackState::new()));
    switch_to_agent(&mut app, id2, SwitchCause::New);
    assert!(app.agents[&id2].toast.is_none());
    assert_eq!(app.agents[&id2].scrollback.iter_entries().count(), 0);
}
/// Switching to a non-auto/non-yolo agent re-anchors a stale global
/// `"auto"` mirror (left by a different agent) to `"ask"`, so the cycle's
/// `sync_active_auto_flag` derive can't copy that Auto onto the now-active
/// agent.
#[test]
fn switch_to_agent_reanchors_stale_global_auto() {
    let mut app = test_app_with_agent();
    let id2 = AgentId(1);
    let session2 = make_test_agent_session(&app, id2, "test-session-2");
    app.agents
        .insert(id2, AgentView::new(session2, ScrollbackState::new()));
    app.next_agent_id = 2;
    app.current_ui.permission_mode = Some("auto".into());
    switch_to_agent(&mut app, id2, SwitchCause::Picker);
    assert_eq!(
        app.current_ui.permission_mode.as_deref(),
        Some("ask"),
        "switching to a non-auto agent must clear the stale global auto"
    );
    assert!(!app.agents[&id2].session.is_auto());
}
/// Expanding a session card only toggles the row open/closed; it never
/// fetches transcript detail.
#[test]
fn expand_session_card_toggles_modal_row() {
    use crate::views::modal::ActiveModal;
    let mut app = test_app_with_agent();
    open_session_picker_with(&mut app, vec![make_picker_entry("local-exp-1", "/r")]);
    let expand = |app: &mut AppView| {
        dispatch(
            Action::ExpandSessionCard {
                source: "local".into(),
                session_id: "local-exp-1".into(),
            },
            app,
        )
    };
    let is_expanded = |app: &AppView| {
        let agent = get_active_agent(app).expect("active agent");
        let Some(ActiveModal::SessionPicker { state, .. }) = agent.active_modal.as_ref() else {
            panic!("expected SessionPicker modal");
        };
        state.expanded.contains(&0)
    };

    let effects = expand(&mut app);
    assert!(effects.is_empty(), "no detail fetch, got {effects:?}");
    assert!(is_expanded(&app), "first expand opens the row");

    let effects = expand(&mut app);
    assert!(effects.is_empty(), "no detail fetch, got {effects:?}");
    assert!(!is_expanded(&app), "second expand collapses the row");
}

/// Welcome-screen variant of the card toggle.
#[test]
fn expand_session_card_toggles_welcome_row() {
    let mut app = test_app();
    app.session_picker_entries = Some(vec![make_picker_entry("local-exp-w1", "/r")]);
    let expand = |app: &mut AppView| {
        dispatch(
            Action::ExpandSessionCard {
                source: "local".into(),
                session_id: "local-exp-w1".into(),
            },
            app,
        )
    };

    let effects = expand(&mut app);
    assert!(effects.is_empty(), "no detail fetch, got {effects:?}");
    assert!(app.session_picker_state.expanded.contains(&0));

    let effects = expand(&mut app);
    assert!(effects.is_empty(), "no detail fetch, got {effects:?}");
    assert!(!app.session_picker_state.expanded.contains(&0));
}
