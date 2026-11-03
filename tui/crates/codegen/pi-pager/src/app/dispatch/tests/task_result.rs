//! Tests for async task-result application arms.

use super::super::task_result::{
    X11_PRIMARY_PASTE_HINT, maybe_show_x11_primary_paste_hint, show_clipboard_failure,
    wrap_host_image_request_eligible,
};
use super::*;

#[test]
fn stale_auth_copy_timeout_does_not_clear_newer_feedback() {
    let mut app = test_app();
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: Some("https://grok.com/auth".to_owned()),
        mode: AuthMode::Command,
    };

    let first_effects = crate::app::dispatch::router::dispatch_copy_auth_url(&mut app, |_| {
        crate::clipboard::ClipboardDelivery::Failed
    });
    let [
        Effect::ScheduleClearAuthCopyFeedback {
            generation: first_generation,
        },
    ] = first_effects.as_slice()
    else {
        panic!("first copy must schedule feedback clear");
    };

    let second_effects = crate::app::dispatch::router::dispatch_copy_auth_url(&mut app, |_| {
        crate::clipboard::ClipboardDelivery::Confirmed
    });
    let [
        Effect::ScheduleClearAuthCopyFeedback {
            generation: second_generation,
        },
    ] = second_effects.as_slice()
    else {
        panic!("second copy must schedule feedback clear");
    };
    assert_ne!(first_generation, second_generation);
    assert_eq!(
        app.auth_clipboard_delivery,
        Some(crate::clipboard::ClipboardDelivery::Confirmed)
    );

    dispatch_task_result(
        TaskResult::AuthCopyFeedbackTimeout {
            generation: *first_generation,
        },
        &mut app,
    );
    assert_eq!(
        app.auth_clipboard_delivery,
        Some(crate::clipboard::ClipboardDelivery::Confirmed),
        "the first copy's stale timeout must preserve the second feedback"
    );

    dispatch_task_result(
        TaskResult::AuthCopyFeedbackTimeout {
            generation: *second_generation,
        },
        &mut app,
    );
    assert_eq!(app.auth_clipboard_delivery, None);
}

#[test]
fn x11_primary_hint_requires_canonical_full_miss_outcome() {
    use crate::app::actions::{ClipboardPasteCompletion, ClipboardPasteTarget};
    assert_eq!(
        X11_PRIMARY_PASTE_HINT,
        "Try Shift+Insert to paste selected text"
    );
    let target = ClipboardPasteTarget::AgentPrompt {
        agent_id: AgentId(0),
        images_dir: None,
    };

    for completion in [
        ClipboardPasteCompletion::Handled,
        ClipboardPasteCompletion::Dropped,
        ClipboardPasteCompletion::Failed(
            crate::app::actions::ClipboardPasteFailure::AttachmentRead,
        ),
    ] {
        let mut app = test_app_with_agent();
        maybe_show_x11_primary_paste_hint(true, completion, &target, &mut app);
        assert!(
            app.agents[&AgentId(0)].toast.is_none(),
            "{completion:?} must not show full-miss guidance"
        );
    }

    let mut app = test_app_with_agent();
    maybe_show_x11_primary_paste_hint(false, ClipboardPasteCompletion::FullMiss, &target, &mut app);
    assert!(
        app.agents[&AgentId(0)].toast.is_none(),
        "non-X11 FullMiss must not show X11 guidance"
    );
}

#[test]
fn wrap_host_image_request_eligible_covers_full_miss_and_attachment_error_only() {
    use crate::app::actions::{ClipboardPasteCompletion, ClipboardPasteFailure};

    // A clean empty miss and a remote read *error* both fall through to the wrap
    // host-image request — the headless-SSH `grok wrap` image-paste fix.
    assert!(wrap_host_image_request_eligible(
        ClipboardPasteCompletion::FullMiss
    ));
    assert!(wrap_host_image_request_eligible(
        ClipboardPasteCompletion::Failed(ClipboardPasteFailure::AttachmentRead)
    ));

    // Every other outcome is a real dead end: it must keep toasting and never
    // solicit the host image.
    for completion in [
        ClipboardPasteCompletion::Handled,
        ClipboardPasteCompletion::Dropped,
        ClipboardPasteCompletion::Failed(ClipboardPasteFailure::TextRead),
        ClipboardPasteCompletion::Failed(ClipboardPasteFailure::TargetInsertion),
        ClipboardPasteCompletion::Failed(ClipboardPasteFailure::AlreadyReported),
    ] {
        assert!(
            !wrap_host_image_request_eligible(completion),
            "{completion:?} must not route to the wrap host-image request"
        );
    }
}

#[test]
fn x11_primary_hint_routes_to_originating_agent() {
    let mut app = test_app_with_agent();
    let origin = AgentId(0);
    let active = AgentId(1);
    let session = make_test_agent_session(&app, active, "active-session");
    app.agents
        .insert(active, AgentView::new(session, ScrollbackState::new()));
    app.active_view = ActiveView::Agent(active);
    let target = crate::app::actions::ClipboardPasteTarget::AgentPrompt {
        agent_id: origin,
        images_dir: None,
    };

    maybe_show_x11_primary_paste_hint(
        true,
        crate::app::actions::ClipboardPasteCompletion::FullMiss,
        &target,
        &mut app,
    );

    assert_eq!(
        app.agents[&origin]
            .toast
            .as_ref()
            .map(|(msg, _)| msg.as_str()),
        Some(X11_PRIMARY_PASTE_HINT),
    );
    assert!(
        app.agents[&active].toast.is_none(),
        "an unrelated active agent must not receive X11 paste guidance"
    );
}

#[test]
fn clipboard_failure_routes_to_originating_agent_without_duplicate() {
    let mut app = test_app_with_agent();
    let origin = AgentId(0);
    let active = AgentId(1);
    let session = make_test_agent_session(&app, active, "active-session");
    app.agents
        .insert(active, AgentView::new(session, ScrollbackState::new()));
    app.active_view = ActiveView::Agent(active);
    let target = crate::app::actions::ClipboardPasteTarget::AgentPrompt {
        agent_id: origin,
        images_dir: None,
    };

    show_clipboard_failure(
        &target,
        crate::app::actions::ClipboardPasteFailure::TextRead,
        &mut app,
    );
    assert_eq!(
        app.agents[&origin]
            .toast
            .as_ref()
            .map(|(text, _)| text.as_str()),
        Some("Couldn't read clipboard text")
    );
    assert!(app.agents[&active].toast.is_none());

    app.agents[&origin].toast = Some(("Couldn't save pasted image".to_owned(), 90));
    show_clipboard_failure(
        &target,
        crate::app::actions::ClipboardPasteFailure::AlreadyReported,
        &mut app,
    );
    assert_eq!(
        app.agents[&origin]
            .toast
            .as_ref()
            .map(|(text, _)| text.as_str()),
        Some("Couldn't save pasted image")
    );
}

#[test]
fn cancel_complete_does_nothing() {
    let mut app = test_app_with_agent();
    let effects = dispatch(Action::TaskComplete(TaskResult::CancelComplete), &mut app);
    assert!(effects.is_empty());
}

#[test]
fn switch_model_complete_success_updates_model_and_pushes_message() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("grok-4.5"));

    // Set up available models so the display name can be resolved.
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .models
        .available
        .insert(
            model_id.clone(),
            acp::ModelInfo::new(model_id.clone(), "Grok 4.5".to_string()),
        );
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .model_switch_pending = true;

    let initial_scrollback = app.agents[&id].scrollback.len();

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id: model_id.clone(),
            effort: None,
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );

    // Pending flag cleared.
    assert!(!app.agents[&id].session.model_switch_pending);
    // Current model updated.
    assert_eq!(
        app.agents[&id].session.models.current,
        Some(model_id.clone())
    );
    // Success message pushed to scrollback.
    assert_eq!(app.agents[&id].scrollback.len(), initial_scrollback + 1);
    // PersistPreferredModel effect emitted.
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::PersistPreferredModel { model_id: mid, .. } if *mid == model_id.clone()
    ));
}

#[test]
fn switch_model_complete_skips_message_and_persist_when_unchanged() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("grok-4.5"));

    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.models.available.insert(
        model_id.clone(),
        acp::ModelInfo::new(model_id.clone(), "Grok 4.5".to_string()),
    );
    agent.session.models.current = Some(model_id.clone());
    agent.session.models.reasoning_effort = None;
    agent.session.model_switch_pending = true;

    let before = app.agents[&id].scrollback.len();
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id: model_id.clone(),
            effort: None,
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );

    assert!(!app.agents[&id].session.model_switch_pending);
    assert_eq!(app.agents[&id].scrollback.len(), before, "no message added");
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPreferredModel { .. })),
        "no persist effect for no-op switch"
    );
}

#[test]
fn switch_model_complete_persists_resolved_effort_from_catalog_meta() {
    use pi_shell::sampling::types::ReasoningEffort;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("byok-model-47"));

    let mut meta = serde_json::Map::new();
    meta.insert(
        "supportsReasoningEffort".into(),
        serde_json::Value::Bool(true),
    );
    meta.insert(
        "reasoningEffort".into(),
        serde_json::Value::String("xhigh".into()),
    );
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .models
        .available
        .insert(
            model_id.clone(),
            acp::ModelInfo::new(model_id.clone(), "BYOK Model 4.7".to_string())
                .meta(serde_json::Value::Object(meta).as_object().cloned()),
        );
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .model_switch_pending = true;

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id: model_id.clone(),
            effort: None, // user typed `/model Blackbox 4.7` with no effort
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );

    assert_eq!(
        app.agents[&id].session.models.reasoning_effort,
        Some(ReasoningEffort::Xhigh)
    );

    assert_eq!(effects.len(), 1);
    match &effects[0] {
        Effect::PersistPreferredModel {
            model_id: mid,
            reasoning_effort,
        } => {
            assert_eq!(*mid, model_id);
            assert_eq!(
                *reasoning_effort,
                Some(ReasoningEffort::Xhigh),
                "persisted effort must mirror the live UI effort so a \
                     fresh chat does not reset reasoning to a stale default",
            );
        }
        other => panic!("expected PersistPreferredModel, got {other:?}"),
    }
}

#[test]
fn switch_to_non_reasoning_model_clears_persisted_effort() {
    use pi_shell::sampling::types::ReasoningEffort;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("grok-4.5"));

    // Simulate prior reasoning effort from a previous model.
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .models
        .reasoning_effort = Some(ReasoningEffort::High);

    // The new model does NOT have supportsReasoningEffort in its meta.
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .models
        .available
        .insert(
            model_id.clone(),
            acp::ModelInfo::new(model_id.clone(), "Grok Build".to_string()),
        );
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .model_switch_pending = true;

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id: model_id.clone(),
            effort: None,
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );

    assert_eq!(
        app.agents[&id].session.models.reasoning_effort, None,
        "reasoning_effort must be cleared when switching to a non-reasoning model",
    );

    assert_eq!(effects.len(), 1);
    match &effects[0] {
        Effect::PersistPreferredModel {
            reasoning_effort, ..
        } => {
            assert_eq!(
                *reasoning_effort, None,
                "persisted effort must be None so config.toml clears the stale value",
            );
        }
        other => panic!("expected PersistPreferredModel, got {other:?}"),
    }
}

#[test]
fn switch_model_complete_failure_pushes_error_and_clears_pending() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("bad-model"));

    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .model_switch_pending = true;
    let old_current = app.agents[&id].session.models.current.clone();
    let initial_scrollback = app.agents[&id].scrollback.len();

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id,
            effort: None,
            result: Err(SwitchModelError::Other("model not found".into())),
            prev_model_id: None,
        }),
        &mut app,
    );

    assert!(effects.is_empty());
    // Pending flag cleared.
    assert!(!app.agents[&id].session.model_switch_pending);
    // Current model unchanged.
    assert_eq!(app.agents[&id].session.models.current, old_current);
    // Error message pushed to scrollback.
    assert_eq!(app.agents[&id].scrollback.len(), initial_scrollback + 1);
}

#[test]
fn switch_model_incompatible_agent_shows_question_modal() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("cursor-model"));

    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .model_switch_pending = true;
    let initial_scrollback = app.agents[&id].scrollback.len();

    let err = pi_shell::agent::config::ModelSwitchIncompatibleAgentError {
        code: "MODEL_SWITCH_INCOMPATIBLE_AGENT".into(),
        active_agent_type: "grok-build".into(),
        required_agent_type: "cursor".into(),
        model_id: "cursor-model".into(),
        suggestion: "start_new_session".into(),
    };
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id,
            effort: None,
            result: Err(SwitchModelError::IncompatibleAgent { error: err }),
            prev_model_id: None,
        }),
        &mut app,
    );

    // No effects emitted (modal is synchronous state).
    assert!(effects.is_empty());
    // Pending flag cleared.
    assert!(!app.agents[&id].session.model_switch_pending);
    // Question modal is open.
    assert!(app.agents[&id].question_view.is_some());
    let qv = app.agents[&id].question_view.as_ref().unwrap();
    assert!(matches!(
        qv.local_kind,
        Some(crate::views::question_view::LocalQuestionKind::AgentTypeMismatch { .. })
    ));
    // No error message pushed to scrollback.
    assert_eq!(app.agents[&id].scrollback.len(), initial_scrollback);
}

#[test]
fn incompatible_agent_rollback_restores_previous_model() {
    // When SetDefaultModel optimistically updates models.current and the
    // shell rejects with IncompatibleAgent, the handler must roll back
    // models.current to the prev_model_id.
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let prev_model = acp::ModelId::new(std::sync::Arc::from("original-model"));
    let new_model = acp::ModelId::new(std::sync::Arc::from("cursor-model"));

    // Set up catalog with both models.
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.models.available.insert(
        prev_model.clone(),
        acp::ModelInfo::new(prev_model.clone(), "Original".to_string()),
    );
    agent.session.models.available.insert(
        new_model.clone(),
        acp::ModelInfo::new(new_model.clone(), "Cursor".to_string()),
    );
    // Simulate the optimistic update that set_default_model_inner does.
    agent.session.models.set_current(new_model.clone(), None);
    agent.session.model_switch_pending = true;

    assert_eq!(agent.session.models.current, Some(new_model.clone()));

    let err = pi_shell::agent::config::ModelSwitchIncompatibleAgentError {
        code: "MODEL_SWITCH_INCOMPATIBLE_AGENT".into(),
        active_agent_type: "grok-build".into(),
        required_agent_type: "cursor".into(),
        model_id: "cursor-model".into(),
        suggestion: "start_new_session".into(),
    };
    dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id: new_model,
            effort: None,
            result: Err(SwitchModelError::IncompatibleAgent { error: err }),
            prev_model_id: Some(prev_model.clone()),
        }),
        &mut app,
    );

    // models.current must be rolled back to the previous model.
    assert_eq!(
        app.agents[&id].session.models.current,
        Some(prev_model),
        "models.current must be rolled back on IncompatibleAgent",
    );
}

#[test]
fn incompatible_agent_closes_active_modal() {
    use crate::views::modal::ActiveModal;

    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("cursor-model"));

    // Open any modal to verify it gets closed.
    let agent = app.agents.get_mut(&id).unwrap();
    agent.active_modal = Some(ActiveModal::CommandPalette {
        entries: vec![],
        state: crate::views::picker::PickerState::input_active(),
        window: crate::views::modal_window::ModalWindowState::new(),
    });
    agent.session.model_switch_pending = true;

    let err = pi_shell::agent::config::ModelSwitchIncompatibleAgentError {
        code: "MODEL_SWITCH_INCOMPATIBLE_AGENT".into(),
        active_agent_type: "grok-build".into(),
        required_agent_type: "cursor".into(),
        model_id: "cursor-model".into(),
        suggestion: "start_new_session".into(),
    };
    dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id,
            effort: None,
            result: Err(SwitchModelError::IncompatibleAgent { error: err }),
            prev_model_id: None,
        }),
        &mut app,
    );

    // Active modal must be closed.
    assert!(
        app.agents[&id].active_modal.is_none(),
        "active modal must be closed when IncompatibleAgent fires",
    );
    // Question modal must be open.
    assert!(
        app.agents[&id].question_view.is_some(),
        "question modal must be open",
    );
}

#[test]
fn same_agent_type_switch_no_modal() {
    // Switching between two models with the same (or no) agent type
    // should succeed normally — no modal, no IncompatibleAgent error.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_a = acp::ModelId::new(std::sync::Arc::from("grok-build-a"));
    let model_b = acp::ModelId::new(std::sync::Arc::from("grok-build-b"));

    // Add both models to the catalog (no agentType → both use grok-build).
    let agent = app.agents.get_mut(&id).unwrap();
    agent.session.models.available.insert(
        model_a.clone(),
        acp::ModelInfo::new(model_a.clone(), "Grok Build A".to_string()),
    );
    agent.session.models.set_current(model_a, None);
    agent.session.models.available.insert(
        model_b.clone(),
        acp::ModelInfo::new(model_b.clone(), "Grok Build B".to_string()),
    );
    agent.session.model_switch_pending = true;

    // Shell returns Ok (same agent type, no mismatch).
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id: model_b.clone(),
            effort: None,
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );

    // Model should be switched, no modal.
    assert_eq!(app.agents[&id].session.models.current, Some(model_b));
    assert!(app.agents[&id].question_view.is_none());
    // Should emit PersistPreferredModel.
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPreferredModel { .. }))
    );
}

#[test]
fn switch_model_pending_lifecycle() {
    // Full lifecycle: false -> dispatch SwitchModel -> true -> SwitchModelComplete -> false
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("grok-4.5"));

    // Initially false.
    assert!(!app.agents[&id].session.model_switch_pending);

    // Action sets pending.
    dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    assert!(app.agents[&id].session.model_switch_pending);

    // TaskResult clears pending.
    dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id,
            effort: None,
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );
    assert!(!app.agents[&id].session.model_switch_pending);
}

#[test]
fn no_deferred_switch_means_no_extra_effect() {
    // When there is no deferred model switch, SessionCreated should
    // not produce a SwitchModel effect.
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Remove session_id to simulate pre-session state.
    app.agents.get_mut(&id).unwrap().session.session_id = None;
    assert!(app.agents[&id].session.deferred_model_switch.is_none());

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SessionCreated {
            agent_id: id,
            session_id: "new-session".into(),
            models: None,
            scheduler_background_loops: None,
        }),
        &mut app,
    );

    // No SwitchModel effect.
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::SwitchModel { .. }))
    );
    assert!(!app.agents[&id].session.model_switch_pending);
}

// -- Session deletion from the /resume picker -----------------------

/// `apply_setting_rollback` on a known key reverts the in-memory
/// cache without emitting any new effects.
#[test]
fn rollback_known_key_reverts_cache_and_no_effect() {
    use crate::settings::SettingValue;
    let mut app = test_app_with_agent();
    // Toggle to true first.
    let _ = dispatch(Action::SetCompactMode(true), &mut app);
    assert!(app.current_ui.compact_mode);
    // Now simulate persist failure that rolls back to false.
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SettingPersistFailed {
            key: "compact_mode",
            rollback_value: SettingValue::Bool(false),
            error: "test-error".into(),
        }),
        &mut app,
    );
    assert!(
        effects.is_empty(),
        "rollback path must NOT emit any new Effects (would loop)",
    );
    // Cache is reverted.
    assert!(!app.current_ui.compact_mode);
}

/// `apply_setting_rollback` on an unknown key surfaces a tracing
/// error and a secondary toast, but does not panic.
#[test]
fn rollback_unknown_key_does_not_panic() {
    use crate::settings::SettingValue;
    let mut app = test_app_with_agent();
    let effects = dispatch(
        Action::TaskComplete(TaskResult::SettingPersistFailed {
            key: "nonexistent_setting",
            rollback_value: SettingValue::Bool(true),
            error: "test-error".into(),
        }),
        &mut app,
    );
    assert!(effects.is_empty());
    // No assertion on cache state (no arm to rollback through);
    // the toast surfaces the inconsistency to the user.
}

#[test]
fn persist_failed_toast_contains_key_and_error() {
    use crate::settings::SettingValue;
    let mut app = test_app_with_agent();
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SettingPersistFailed {
            key: "compact_mode",
            rollback_value: SettingValue::Bool(false),
            error: "permission denied".into(),
        }),
        &mut app,
    );
    let toast = read_toast(&app);
    assert!(toast.contains("compact_mode"));
    assert!(toast.contains("permission denied"));
    assert!(toast.contains('\u{2717}'));
}

/// Rollback path must revert BOTH `app.current_ui` AND the
/// thread-local cache — failing only on one is the bug class
/// the inner/outer split exists to prevent.
#[test]
fn rollback_reverts_thread_local_cache_too() {
    use crate::settings::SettingValue;
    std::thread::spawn(|| {
        let mut app = test_app_with_agent();
        // Optimistic set: cache → true.
        let _ = dispatch(Action::SetCompactMode(true), &mut app);
        assert!(crate::appearance::cache::load());

        // Synthesize failure: rollback to false.
        let _ = dispatch(
            Action::TaskComplete(TaskResult::SettingPersistFailed {
                key: "compact_mode",
                rollback_value: SettingValue::Bool(false),
                error: "test-error".into(),
            }),
            &mut app,
        );
        assert!(
            !app.current_ui.compact_mode,
            "rollback must update app.current_ui"
        );
        assert!(
            !crate::appearance::cache::load(),
            "rollback must also revert the cache — otherwise the renderer \
                 still shows the optimistic value while current_ui shows the rolled-back one"
        );
    })
    .join()
    .unwrap();
}

/// `set_yolo_mode_inner` is the backstop: even a (stale) rollback
/// value of "always-approve" must not re-enable yolo under the pin.
#[test]
fn rollback_to_always_approve_blocked_by_policy_pin() {
    use crate::settings::SettingValue;
    let mut app = test_app_with_agent();
    app.yolo_policy_block = Some(POLICY_WARNING);

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SettingPersistFailed {
            key: "permission_mode",
            rollback_value: SettingValue::Enum("always-approve"),
            error: "permission denied".into(),
        }),
        &mut app,
    );

    assert!(effects.is_empty(), "rollback path never re-emits effects");
    assert!(
        !app.agents[&AgentId(0)].session.is_yolo(),
        "inner backstop must hold on the rollback path"
    );
    assert!(!app.default_yolo);
}

// -- Session list completion (`SessionListLoaded` / `SessionListFailed`) ----------

fn modal_picker(app: &AppView) -> (&Option<Vec<crate::app::app_view::SessionPickerEntry>>, bool) {
    use crate::views::modal::ActiveModal;
    let agent = get_active_agent(app).expect("active agent");
    let Some(ActiveModal::SessionPicker {
        entries, loading, ..
    }) = agent.active_modal.as_ref()
    else {
        panic!("expected SessionPicker modal");
    };
    (entries, *loading)
}

fn set_modal_picker_loading(app: &mut AppView, value: bool) {
    use crate::views::modal::ActiveModal;
    if let Some(ActiveModal::SessionPicker { loading, .. }) = get_active_agent_mut(app)
        .expect("active agent")
        .active_modal
        .as_mut()
    {
        *loading = value;
    }
}

/// A current-seq list replaces the modal's entries and clears its spinner;
/// the welcome picker is left alone while the modal owns the fetch.
#[test]
fn session_list_loaded_lands_on_modal_and_clears_loading() {
    let mut app = test_app_with_agent();
    open_session_picker_with(&mut app, vec![]);
    set_modal_picker_loading(&mut app, true);
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("a", "/r"), make_picker_entry("b", "/r")],
            seq: 0,
        }),
        &mut app,
    );
    let (entries, loading) = modal_picker(&app);
    let ids: Vec<&str> = entries
        .as_deref()
        .unwrap_or_default()
        .iter()
        .map(|e| e.id.as_str())
        .collect();
    assert_eq!(ids, ["a", "b"]);
    assert!(!loading, "landing the list clears the modal spinner");
    assert!(app.session_picker_entries.is_none());
}

/// Without a modal, the list lands on the welcome-screen picker.
#[test]
fn session_list_loaded_lands_on_welcome_picker_without_modal() {
    let mut app = test_app();
    app.session_picker_loading = true;
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("w1", "/r")],
            seq: 0,
        }),
        &mut app,
    );
    let ids: Vec<&str> = app
        .session_picker_entries
        .as_deref()
        .unwrap_or_default()
        .iter()
        .map(|e| e.id.as_str())
        .collect();
    assert_eq!(ids, ["w1"]);
    assert!(!app.session_picker_loading);
}

/// An empty list leaves the modal without entries and shows the generic toast.
#[test]
fn session_list_empty_shows_generic_toast() {
    let mut app = test_app_with_agent();
    open_session_picker_with(&mut app, vec![make_picker_entry("old", "/r")]);
    set_modal_picker_loading(&mut app, true);
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![],
            seq: 0,
        }),
        &mut app,
    );
    assert!(read_toast(&app).contains("No sessions found"));
    let (entries, loading) = modal_picker(&app);
    assert!(entries.is_none(), "an empty list clears the entries");
    assert!(!loading);
}

/// A failed fetch surfaces the error, clears the spinner and drops the entries.
#[test]
fn session_list_failure_toasts_and_clears_loading() {
    let mut app = test_app_with_agent();
    open_session_picker_with(&mut app, vec![]);
    set_modal_picker_loading(&mut app, true);
    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListFailed {
            error: "boom".into(),
            seq: 0,
        }),
        &mut app,
    );
    assert!(read_toast(&app).contains("boom"));
    let (entries, loading) = modal_picker(&app);
    assert!(entries.is_none());
    assert!(!loading);
}

/// Out-of-order completions: only the response for the current seq lands;
/// stale successes and failures are both dropped silently.
#[test]
fn stale_session_list_responses_are_dropped() {
    let mut app = test_app_with_agent();
    open_session_picker_with(&mut app, vec![make_picker_entry("current", "/r")]);
    set_modal_picker_loading(&mut app, true);
    app.session_picker_list_seq = 3;

    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("stale", "/r")],
            seq: 2,
        }),
        &mut app,
    );
    let (entries, loading) = modal_picker(&app);
    assert_eq!(entries.as_deref().map(<[_]>::len), Some(1));
    assert_eq!(entries.as_deref().unwrap()[0].id, "current");
    assert!(loading, "a stale result must not clear the spinner");

    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListFailed {
            error: "boom".into(),
            seq: 2,
        }),
        &mut app,
    );
    assert!(
        app.agents[&AgentId(0)].toast.is_none(),
        "a stale failure must not toast"
    );

    let _ = dispatch(
        Action::TaskComplete(TaskResult::SessionListLoaded {
            sessions: vec![make_picker_entry("fresh", "/r")],
            seq: 3,
        }),
        &mut app,
    );
    let (entries, loading) = modal_picker(&app);
    assert_eq!(entries.as_deref().unwrap()[0].id, "fresh");
    assert!(!loading);
}

// -- Session deletion from the /resume picker -----------------------

#[test]
fn delete_session_complete_removes_only_matching_source_and_id() {
    use crate::views::modal::ActiveModal;
    let mut app = test_app_with_agent();
    let mut other_source_same_id = make_picker_entry("s1", "/r");
    other_source_same_id.source = "archive".into();
    open_session_picker_with(
        &mut app,
        vec![
            make_picker_entry("s0", "/r"),
            other_source_same_id.clone(),
            make_picker_entry("s1", "/r"),
            make_picker_entry("s2", "/r"),
        ],
    );
    app.session_picker_entries = Some(vec![other_source_same_id, make_picker_entry("s1", "/r")]);
    if let Some(ActiveModal::SessionPicker { pending_delete, .. }) = get_active_agent_mut(&mut app)
        .unwrap()
        .active_modal
        .as_mut()
    {
        *pending_delete = Some(crate::views::session_picker::PendingDelete {
            source: "local".into(),
            session_id: "s1".into(),
            cwd: "/r".into(),
        });
    }

    let _ = dispatch_task_result(
        TaskResult::DeleteSessionComplete {
            source: "local".into(),
            session_id: "s1".into(),
            after: crate::app::actions::AfterSessionDelete::Stay,
        },
        &mut app,
    );

    let agent = get_active_agent(&app).expect("active agent");
    let Some(ActiveModal::SessionPicker {
        entries: Some(list),
        pending_delete,
        ..
    }) = agent.active_modal.as_ref()
    else {
        panic!("expected SessionPicker modal");
    };
    let identities: Vec<_> = list
        .iter()
        .map(|entry| (entry.source.as_str(), entry.id.as_str()))
        .collect();
    assert_eq!(
        identities,
        vec![("local", "s0"), ("archive", "s1"), ("local", "s2")]
    );
    assert!(
        pending_delete.is_none(),
        "pending_delete must be cleared after deletion completes"
    );
    let welcome_identities: Vec<_> = app
        .session_picker_entries
        .as_ref()
        .unwrap()
        .iter()
        .map(|entry| (entry.source.as_str(), entry.id.as_str()))
        .collect();
    assert_eq!(welcome_identities, vec![("archive", "s1")]);
}

#[test]
fn delete_session_failed_keeps_all_entries() {
    use crate::views::modal::ActiveModal;
    let mut app = test_app_with_agent();
    open_session_picker_with(
        &mut app,
        vec![make_picker_entry("s0", "/r"), make_picker_entry("s1", "/r")],
    );

    let _ = dispatch_task_result(
        TaskResult::DeleteSessionFailed {
            source: "local".into(),
            session_id: "s1".into(),
            error: "boom".into(),
        },
        &mut app,
    );

    let agent = get_active_agent(&app).expect("active agent");
    let Some(ActiveModal::SessionPicker {
        entries: Some(list),
        ..
    }) = agent.active_modal.as_ref()
    else {
        panic!("expected SessionPicker modal");
    };
    assert_eq!(list.len(), 2, "a failed delete must not remove any entry");
}
