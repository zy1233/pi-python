//! Tests for feedback / remember / btw / recap dispatchers.

use super::*;
use crate::app::dispatch::{recap_unavailable_toast, scrollback_has_user_messages};

#[test]
fn recap_unavailable_toast_empty_vs_with_messages() {
    assert_eq!(recap_unavailable_toast(false), "No messages yet");
    assert_eq!(recap_unavailable_toast(true), "Couldn't generate recap");
}

#[test]
fn manual_recap_with_no_messages_toasts_empty_state_and_skips_request() {
    let mut app = test_app_with_agent();
    app.session_recap_available = true;
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.set_text("/recap");
        assert!(!scrollback_has_user_messages(&agent.scrollback));
    }

    let effects = dispatch(Action::SendRecap { auto: false }, &mut app);

    assert!(
        effects.is_empty(),
        "empty session must not fire pi/recap: {effects:?}"
    );
    let agent = app.agents.get(&id).unwrap();
    assert!(agent.pending_recap_entry.is_none(), "no loading spinner");
    assert_eq!(
        agent.toast.as_ref().map(|(s, _)| s.as_str()),
        Some("No messages yet"),
        "empty session should say No messages yet, not Couldn't generate recap"
    );
    assert_eq!(agent.prompt.text(), "", "slash command text is cleared");
}

#[test]
fn manual_recap_with_messages_requests_and_shows_spinner() {
    let mut app = test_app_with_agent();
    app.session_recap_available = true;
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .scrollback
            .push_block(RenderBlock::user_prompt("hello"));
        assert!(scrollback_has_user_messages(&agent.scrollback));
    }

    let effects = dispatch(Action::SendRecap { auto: false }, &mut app);

    assert!(
        matches!(effects.as_slice(), [Effect::SendRecap { auto: false, .. }]),
        "expected SendRecap effect, got {effects:?}"
    );
    let agent = app.agents.get(&id).unwrap();
    assert!(
        agent.pending_recap_entry.is_some(),
        "manual recap shows a loading spinner when there is something to summarize"
    );
    assert!(agent.toast.is_none());
}

/// Regression: during session/load, scrollback is batched so
/// `turn_count()` stays 0 until `end_batch`, but UserPrompt entries may already
/// be present. Manual `/recap` must still request a recap.
#[test]
fn manual_recap_during_batch_load_with_prompts_still_requests() {
    let mut app = test_app_with_agent();
    app.session_recap_available = true;
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.scrollback.begin_batch();
        agent
            .scrollback
            .push_block(RenderBlock::user_prompt("hello from resume"));
        // Batched push defers rebuild_turns — turn index is stale, entries aren't.
        assert_eq!(agent.scrollback.turn_count(), 0);
        assert!(scrollback_has_user_messages(&agent.scrollback));
    }

    let effects = dispatch(Action::SendRecap { auto: false }, &mut app);

    assert!(
        matches!(effects.as_slice(), [Effect::SendRecap { auto: false, .. }]),
        "batched resume with user prompts must still fire pi/recap: {effects:?}"
    );
    let agent = app.agents.get(&id).unwrap();
    assert!(agent.pending_recap_entry.is_some());
    assert!(agent.toast.is_none());
    // Clean up batch for the test fixture (not required for the assertion).
    app.agents.get_mut(&id).unwrap().scrollback.end_batch();
}

/// While session replay is still streaming, don't claim "No messages yet" even
/// if scrollback looks empty — history may arrive on the next notification.
#[test]
fn manual_recap_while_loading_replay_still_requests() {
    let mut app = test_app_with_agent();
    app.session_recap_available = true;
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.loading_replay = true;
        assert!(!scrollback_has_user_messages(&agent.scrollback));
    }

    let effects = dispatch(Action::SendRecap { auto: false }, &mut app);

    assert!(
        matches!(effects.as_slice(), [Effect::SendRecap { auto: false, .. }]),
        "loading_replay must not short-circuit to No messages yet: {effects:?}"
    );
    let agent = app.agents.get(&id).unwrap();
    assert!(agent.pending_recap_entry.is_some());
    assert!(agent.toast.is_none());
}

#[test]
fn recap_request_transport_failure_with_no_turns_uses_empty_toast() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let session_id = app.agents[&id].session.session_id.clone().unwrap();
    {
        let agent = app.agents.get_mut(&id).unwrap();
        let spinner = agent
            .scrollback
            .push(crate::scrollback::entry::ScrollbackEntry::running(
                RenderBlock::session_event(SessionEvent::Recap {
                    summary: String::new(),
                    auto: false,
                }),
            ));
        agent.pending_recap_entry = Some(spinner);
        assert!(!scrollback_has_user_messages(&agent.scrollback));
    }

    dispatch(
        Action::TaskComplete(TaskResult::RecapRequested {
            session_id,
            auto: false,
            error: Some("transport down".into()),
        }),
        &mut app,
    );

    let agent = app.agents.get(&id).unwrap();
    assert!(agent.pending_recap_entry.is_none());
    assert_eq!(
        agent.toast.as_ref().map(|(s, _)| s.as_str()),
        Some("No messages yet")
    );
}

#[test]
fn recap_request_transport_failure_with_turns_uses_generic_toast() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let session_id = app.agents[&id].session.session_id.clone().unwrap();
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .scrollback
            .push_block(RenderBlock::user_prompt("hello"));
        let spinner = agent
            .scrollback
            .push(crate::scrollback::entry::ScrollbackEntry::running(
                RenderBlock::session_event(SessionEvent::Recap {
                    summary: String::new(),
                    auto: false,
                }),
            ));
        agent.pending_recap_entry = Some(spinner);
        assert!(scrollback_has_user_messages(&agent.scrollback));
    }

    dispatch(
        Action::TaskComplete(TaskResult::RecapRequested {
            session_id,
            auto: false,
            error: Some("transport down".into()),
        }),
        &mut app,
    );

    let agent = app.agents.get(&id).unwrap();
    assert!(agent.pending_recap_entry.is_none());
    assert_eq!(
        agent.toast.as_ref().map(|(s, _)| s.as_str()),
        Some("Couldn't generate recap")
    );
}

/// "Yes, always upload" uploads now and persists `[telemetry] trace_upload`.
#[test]
fn feedback_trace_always_upload_persists_setting() {
    use crate::app::actions::FeedbackTraceChoice;

    let mut app = test_app_with_agent();
    let effects = dispatch(
        Action::SendFeedback {
            text: "clipboard is broken over ssh".into(),
            images: Default::default(),
            trace: Some(FeedbackTraceChoice::AlwaysUpload),
        },
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::UploadFeedbackTrace { .. })),
        "always includes this report's archive: {effects:?}"
    );
    assert!(
        effects.iter().any(|e| matches!(
            e,
            Effect::PersistSetting {
                key: "trace_upload",
                value: crate::settings::SettingValue::Bool(true),
                ..
            }
        )),
        "always must persist the consent: {effects:?}"
    );
}

/// "Opt out and don't ask again" sends the report alone, latches the offer off
/// for this session, and persists `[features] feedback_trace_card = false`.
#[test]
fn feedback_trace_never_ask_persists_suppression() {
    use crate::app::actions::FeedbackTraceChoice;

    let mut app = test_app_with_agent();
    app.shell_feedback_trace_offer = true;
    let effects = dispatch(
        Action::SendFeedback {
            text: "clipboard is broken over ssh".into(),
            images: Default::default(),
            trace: Some(FeedbackTraceChoice::NeverAsk),
        },
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::SendFeedback { .. })),
        "the report still sends: {effects:?}"
    );
    assert!(
        effects
            .iter()
            .all(|e| !matches!(e, Effect::UploadFeedbackTrace { .. })),
        "never-ask must not upload: {effects:?}"
    );
    assert!(
        effects.iter().any(|e| matches!(
            e,
            Effect::PersistSetting {
                key: "feedback_trace_card",
                value: crate::settings::SettingValue::Bool(false),
                ..
            }
        )),
        "never-ask must persist the suppression: {effects:?}"
    );
    assert!(!app.feedback_trace_offer(), "offer latches off");
    assert!(
        app.feedback_trace_choice_latched,
        "sticky across auth-meta refreshes"
    );
}

/// Turning trace upload on while opted out is the switch-back-on
/// affordance: it flips coding-data sharing through the standard write
/// path, and this report's upload waits for that write to be confirmed
/// (the storage proxy refuses uploads while the account is opted out).
#[test]
fn feedback_turn_on_while_opted_out_reenables_sharing_then_uploads() {
    use crate::app::actions::FeedbackTraceChoice;

    let mut app = test_app_with_agent();
    app.shell_feedback_trace_offer = true;
    app.coding_data_retention_opt_out = true;
    let effects = dispatch(
        Action::SendFeedback {
            text: "clipboard is broken over ssh".into(),
            images: Default::default(),
            trace: Some(FeedbackTraceChoice::AlwaysUpload),
        },
        &mut app,
    );
    assert!(
        !app.coding_data_retention_opt_out,
        "turn-on must flip sharing back on (optimistic)"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::SetCodingDataSharing { opted_in: true, .. })),
        "the server-side sharing write must be issued: {effects:?}"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::UploadFeedbackTrace { .. })),
        "the upload must wait for the opt-in to land: {effects:?}"
    );
    assert!(
        !effects.iter().any(|e| matches!(
            e,
            Effect::PersistSetting {
                key: "trace_upload",
                ..
            }
        )),
        "the consent must not persist before the opt-in lands: {effects:?}"
    );
    let seq = app
        .feedback_trace_upload_pending
        .as_ref()
        .expect("upload parked on the sharing write")
        .seq;

    // The opt-in confirmation releases the parked upload and the deferred
    // consent persist.
    let effects = dispatch(
        Action::TaskComplete(TaskResult::CodingDataSharingUpdated {
            agent_id: AgentId(0),
            opted_in: true,
            seq,
        }),
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::UploadFeedbackTrace { .. })),
        "confirmed opt-in must release the parked upload: {effects:?}"
    );
    assert!(
        effects.iter().any(|e| matches!(
            e,
            Effect::PersistSetting {
                key: "trace_upload",
                value: crate::settings::SettingValue::Bool(true),
                ..
            }
        )),
        "confirmed opt-in must persist the consent: {effects:?}"
    );
    assert!(
        app.feedback_trace_upload_pending.is_none(),
        "the parked upload is consumed"
    );
}

/// A write that confirms with `opted_in = false` (server kept sharing off)
/// must behave like the failure path: no upload, no persist, latch undone so
/// the card can re-offer.
#[test]
fn feedback_turn_on_unlatches_when_the_confirm_keeps_sharing_off() {
    use crate::app::actions::FeedbackTraceChoice;

    let mut app = test_app_with_agent();
    app.shell_feedback_trace_offer = true;
    app.coding_data_retention_opt_out = true;
    let _ = dispatch(
        Action::SendFeedback {
            text: "clipboard is broken over ssh".into(),
            images: Default::default(),
            trace: Some(FeedbackTraceChoice::AlwaysUpload),
        },
        &mut app,
    );
    let seq = app
        .feedback_trace_upload_pending
        .as_ref()
        .expect("upload parked on the sharing write")
        .seq;

    let effects = dispatch(
        Action::TaskComplete(TaskResult::CodingDataSharingUpdated {
            agent_id: AgentId(0),
            opted_in: false,
            seq,
        }),
        &mut app,
    );
    assert!(
        !effects.iter().any(|e| matches!(
            e,
            Effect::UploadFeedbackTrace { .. } | Effect::PersistSetting { .. }
        )),
        "a confirm that keeps sharing off must not upload or persist: {effects:?}"
    );
    assert!(
        app.feedback_trace_upload_pending.is_none(),
        "the parked upload is consumed"
    );
    assert!(
        app.feedback_trace_offer() && !app.feedback_trace_choice_latched,
        "the card must be able to re-offer"
    );
}

/// Inline `/feedback <text>` with no session has nowhere to send, so it says so instead of failing silently.
#[test]
fn send_feedback_without_a_session_says_so() {
    let id = AgentId(0);
    let mut app = test_app_with_agent();
    app.agents.get_mut(&id).unwrap().session.session_id = None;

    assert!(
        dispatch(
            Action::SendFeedback {
                text: "long report".into(),
                images: Default::default(),
                trace: Some(crate::app::actions::FeedbackTraceChoice::NoUpload),
            },
            &mut app
        )
        .is_empty()
    );

    assert!(last_system_text(&app, id).contains("No active session"));
}

fn test_pasted_png() -> crate::prompt_images::PastedImage {
    crate::prompt_images::from_clipboard_data(&crate::clipboard::ImageData {
        data: vec![
            0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0, 0, 0, 0, 0,
        ],
        mime_type: "image/png".to_string(),
    })
}

/// `dispatch_send_feedback` bailing before the send (no agent view) still
/// owns the attachments and must delete their staged temp files.
#[test]
fn send_feedback_without_agent_view_cleans_staged_temp_files() {
    let mut app = test_app_with_agent();
    app.active_view = crate::app::app_view::ActiveView::Welcome;

    let dir = tempfile::tempdir().unwrap();
    let staged = dir.path().join("staged.png");
    std::fs::write(&staged, b"staged").unwrap();
    let mut image = test_pasted_png();
    image.staged_temp_path = Some(staged.clone());

    let effects = dispatch(
        Action::SendFeedback {
            text: "it broke".into(),
            images: vec![image].into(),
            trace: None,
        },
        &mut app,
    );
    assert!(effects.is_empty(), "the send must bail: {effects:?}");
    assert!(
        !staged.exists(),
        "a bailed send must release its staged files"
    );
}

