//! Tests for session status, sharing, privacy, and coding-data-sharing dispatchers.

use super::*;

// ── coding_data_sharing dispatch tests ───
//
// The dispatcher uses **optimistic + rollback**, matching the
// `set_yolo_mode` pattern minus its toasts — the surfaces that change this
// setting show the result themselves. These tests pin the contract:
//   - Guards (ZDR, non-admin team) toast and short-circuit; they are the
//     only paths that still speak up, because nothing else on screen would.
//   - Idle unchanged opt-in skips the ACP write but still acks (rollout on).
//   - Optimistic mutation flips `app.coding_data_retention_opt_out`
//     BEFORE the Effect is emitted.
//   - `Effect::SetCodingDataSharing` carries
//     `rollback_to_opted_in = previous_value`.
//   - `TaskResult::CodingDataSharingFailed` reverts the optimistic
//     mutation; `TaskResult::CodingDataSharingUpdated` re-anchors
//     to the server-confirmed value.

/// Idle unchanged opt-in skips ACP and still acks. Already-out is covered
/// by `settings_opt_out_while_already_out_acks_without_write`.
#[test]
fn set_coding_data_sharing_unchanged_opt_in_skips_acp_and_acks() {
    let mut app = test_app_with_agent();
    app.privacy_notice_rollout = true;
    app.coding_data_retention_opt_out = false;
    let effects = dispatch(Action::SetCodingDataSharing { opted_in: true }, &mut app);
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPrivacyBannerAcked { .. })),
        "idle unchanged opt-in must still ack: {effects:?}"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::SetCodingDataSharing { .. })),
        "idle unchanged opt-in must NOT write ACP: {effects:?}"
    );
    assert!(app.agents[&AgentId(0)].toast.is_none());
    assert!(!app.coding_data_retention_opt_out);
    assert!(app.privacy_banner_acked.is_some());
    assert!(!app.privacy_banner_opt_in_inflight);
    assert_eq!(app.coding_data_write_seq, 0);
}

/// ZDR teams are blocked from toggling. The blocked path
/// toasts (not scrollback) and short-circuits with no Effect.
#[test]
fn set_coding_data_sharing_blocked_by_zdr() {
    let mut app = test_app_with_agent();
    app.is_zdr = true;
    app.coding_data_retention_opt_out = false;
    let effects = dispatch(Action::SetCodingDataSharing { opted_in: false }, &mut app);
    assert!(effects.is_empty(), "ZDR block must NOT emit Effect");
    assert!(
        app.privacy_banner_acked.is_none(),
        "ZDR block must not ack the banner"
    );
    assert!(!app.privacy_banner_opt_in_inflight);
    let toast = read_toast(&app);
    assert!(
        toast.contains("Zero Data Retention"),
        "ZDR toast must surface the policy: {toast}",
    );
    assert!(
        toast.contains('\u{2717}'),
        "blocked toast uses ✗ glyph: {toast}"
    );
    // State unchanged — the user was blocked, the optimistic
    // mutation never happened.
    assert!(
        !app.coding_data_retention_opt_out,
        "ZDR block must not mutate state",
    );
}

/// ZDR block fires even when the toggle would be a no-op
/// (defense-in-depth: don't quietly accept a same-value toggle
/// from a user the policy says shouldn't be touching this).
#[test]
fn set_coding_data_sharing_blocked_by_zdr_even_if_idempotent() {
    let mut app = test_app_with_agent();
    app.is_zdr = true;
    app.coding_data_retention_opt_out = false;
    let effects = dispatch(Action::SetCodingDataSharing { opted_in: true }, &mut app);
    assert!(effects.is_empty());
    assert!(app.privacy_banner_acked.is_none());
    assert!(!app.privacy_banner_opt_in_inflight);
    assert!(read_toast(&app).contains("Zero Data Retention"));
}

/// Non-admin team members are blocked from toggling (matches
/// desktop). The blocked path toasts and short-circuits.
#[test]
fn set_coding_data_sharing_blocked_non_admin() {
    let mut app = test_app_with_agent();
    app.team_name = Some("Acme".into());
    app.team_role = Some("Member".into());
    app.coding_data_retention_opt_out = false;
    let effects = dispatch(Action::SetCodingDataSharing { opted_in: false }, &mut app);
    assert!(effects.is_empty());
    assert!(app.privacy_banner_acked.is_none());
    assert!(!app.privacy_banner_opt_in_inflight);
    let toast = read_toast(&app);
    assert!(
        toast.contains("team admin"),
        "non-admin toast must mention team admin: {toast}",
    );
}

/// `TaskResult::CodingDataSharingUpdated` re-anchors state to the
/// server-confirmed value (defense-in-depth).
#[test]
fn coding_data_sharing_updated_re_anchors_state() {
    let mut app = test_app_with_agent();
    // Simulate post-optimistic state: opted-out.
    app.coding_data_retention_opt_out = true;
    let id = AgentId(0);
    // Server confirms opt-out (same as optimistic).
    let seq = app.coding_data_write_seq;
    let effects = dispatch(
        Action::TaskComplete(TaskResult::CodingDataSharingUpdated {
            agent_id: id,
            opted_in: false,
            seq,
        }),
        &mut app,
    );
    assert!(effects.is_empty(), "TaskResult arm must NOT emit Effect");
    // State re-anchored (was already true, stays true).
    assert!(app.coding_data_retention_opt_out);
    assert!(
        app.agents[&AgentId(0)].toast.is_none(),
        "server confirmation must not toast",
    );
}

/// `TaskResult::CodingDataSharingUpdated` corrects the in-memory
/// state if the server reshapes the boolean (e.g. policy
/// override). Pins the defense-in-depth re-anchor contract.
#[test]
fn coding_data_sharing_updated_corrects_state_if_server_disagrees() {
    let mut app = test_app_with_agent();
    // Optimistic mutation said "opt-out" — but the server
    // overrides to "opt-in" (e.g. policy that prevents opt-out).
    app.coding_data_retention_opt_out = true;
    let id = AgentId(0);
    let seq = app.coding_data_write_seq;
    let effects = dispatch(
        Action::TaskComplete(TaskResult::CodingDataSharingUpdated {
            agent_id: id,
            opted_in: true, // server says opted-in
            seq,
        }),
        &mut app,
    );
    assert!(effects.is_empty());
    // State corrected to match server.
    assert!(
        !app.coding_data_retention_opt_out,
        "server-confirmed opt-in must overwrite optimistic opt-out",
    );
}

/// Optimistic mutation refreshes any open settings modal.
/// Without this refresh, the modal indicator would stay at the
/// pre-toggle value until manual re-render.
#[test]
fn set_coding_data_sharing_refreshes_open_modal_snapshot() {
    let mut app = test_app_with_agent();
    app.coding_data_retention_opt_out = false;
    // Open a settings modal (capture initial snapshot).
    let _ = dispatch(Action::OpenSettings, &mut app);
    // Verify snapshot reads opted-in.
    let agent_id = AgentId(0);
    {
        let state = match &app.agents[&agent_id].active_modal {
            Some(crate::views::modal::ActiveModal::Settings { state }) => state,
            _ => panic!("expected Settings modal open after OpenSettings dispatch"),
        };
        assert!(
            !state.pager_snapshot.coding_data_sharing_opt_out,
            "initial snapshot must read opt_out=false (opted-in)",
        );
    }
    // Dispatch the toggle.
    let _ = dispatch(Action::SetCodingDataSharing { opted_in: false }, &mut app);
    // Snapshot now reflects the optimistic mutation.
    let state = match &app.agents[&agent_id].active_modal {
        Some(crate::views::modal::ActiveModal::Settings { state }) => state,
        _ => panic!("Settings modal must still be open after SetCodingDataSharing dispatch"),
    };
    assert!(
        state.pager_snapshot.coding_data_sharing_opt_out,
        "snapshot must refresh to reflect opt_out=true (opted-out) after dispatch",
    );
}

#[test]
fn set_coding_data_sharing_is_silent_in_both_directions() {
    for opted_in in [true, false] {
        let mut app = test_app_with_agent();
        app.coding_data_retention_opt_out = opted_in; // a real change either way
        let _ = dispatch(Action::SetCodingDataSharing { opted_in }, &mut app);
        assert!(
            app.agents[&AgentId(0)].toast.is_none(),
            "opted_in={opted_in} must not toast, got {:?}",
            app.agents[&AgentId(0)].toast,
        );
    }
}

/// Direct unit test of the `scrub_error_for_toast` helper —
/// pins the threshold and the fallback string against drift.
#[test]
fn scrub_error_for_toast_unit() {
    // Empty + short messages pass through.
    assert_eq!(scrub_error_for_toast(""), "");
    assert_eq!(scrub_error_for_toast("ok"), "ok");
    assert_eq!(scrub_error_for_toast("network timeout"), "network timeout");
    // At-threshold (120 chars) still passes through.
    let len_120 = "x".repeat(120);
    assert_eq!(scrub_error_for_toast(&len_120), len_120);
    // Over-threshold (121 chars) triggers scrub.
    let len_121 = "x".repeat(121);
    assert_eq!(
        scrub_error_for_toast(&len_121),
        "server error (see logs for details)"
    );
    // Control chars trigger scrub even at short lengths.
    assert_eq!(
        scrub_error_for_toast("hi\nthere"),
        "server error (see logs for details)"
    );
    assert_eq!(
        scrub_error_for_toast("hi\rthere"),
        "server error (see logs for details)"
    );
    // Format-category (Cf) chars also trigger scrub — bidi
    // overrides, zero-width joiner / space, BOM. Prevents
    // Trojan-Source-style visual spoofing
    // where a toast READS as one thing but bytes encode
    // another via embedded RIGHT-TO-LEFT-OVERRIDE.
    assert_eq!(
        scrub_error_for_toast("opt\u{202E}-out"),
        "server error (see logs for details)",
        "RIGHT-TO-LEFT OVERRIDE (U+202E) must be scrubbed",
    );
    assert_eq!(
        scrub_error_for_toast("opt\u{200B}out"),
        "server error (see logs for details)",
        "ZERO WIDTH SPACE (U+200B) must be scrubbed",
    );
    assert_eq!(
        scrub_error_for_toast("\u{FEFF}leading BOM"),
        "server error (see logs for details)",
        "BOM (U+FEFF) must be scrubbed",
    );
    assert_eq!(
        scrub_error_for_toast("zwj\u{200D}joiner"),
        "server error (see logs for details)",
        "ZERO WIDTH JOINER (U+200D) must be scrubbed",
    );
}

/// Synthetic AgentId(0) when no agents (welcome banner Accept path).
#[test]
fn set_coding_data_sharing_no_agents_still_emits_effect() {
    let mut app = test_app_with_agent();
    app.agents.clear();
    app.active_view = ActiveView::Welcome;
    app.coding_data_retention_opt_out = true;
    let effects = dispatch(Action::SetCodingDataSharing { opted_in: true }, &mut app);
    assert_eq!(effects.len(), 1, "no-agent path must still emit Effect");
    assert!(
        matches!(
            &effects[0],
            Effect::SetCodingDataSharing { opted_in: true, .. }
        ),
        "changed opt-in must be the ACP write, not an early ack: {effects:?}"
    );
    assert!(
        !app.coding_data_retention_opt_out,
        "optimistic opt-in must apply without agents",
    );
    assert!(app.privacy_banner_opt_in_inflight);
    assert!(app.privacy_banner_acked.is_none());
}

fn privacy_banner_ready_app() -> AppView {
    let mut app = test_app_with_agent();
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.privacy_notice_rollout = true;
    app.privacy_banner_acked = None;
    app.privacy_banner_reshow_days = None;
    app.privacy_banner_opt_in_inflight = false;
    app.is_zdr = false;
    app.team_name = None;
    app.coding_data_retention_opt_out = true;
    app
}

#[test]
fn privacy_banner_should_show_respects_gates() {
    let mut app = privacy_banner_ready_app();
    assert!(app.privacy_banner_should_show());

    app.coding_data_retention_opt_out = false;
    assert!(!app.privacy_banner_should_show(), "already opted in");
    app.coding_data_retention_opt_out = true;

    app.is_zdr = true;
    assert!(!app.privacy_banner_should_show(), "enterprise ZDR");
    app.is_zdr = false;

    app.privacy_banner_acked = Some("2099-01-01T00:00:00Z".into());
    assert!(
        !app.privacy_banner_should_show(),
        "recently acked, no reshow"
    );

    app.privacy_banner_reshow_days = Some(30);
    app.privacy_banner_acked = Some("2020-01-01T00:00:00Z".into());
    assert!(
        app.privacy_banner_should_show(),
        "acked long ago + reshow_days"
    );

    app.privacy_notice_rollout = false;
    assert!(!app.privacy_banner_should_show(), "rollout off");
}

/// `[Opt in]` success: ACP confirmation acks the banner.
#[test]
fn privacy_banner_opt_in_success_acks() {
    let mut app = privacy_banner_ready_app();
    let effects = dispatch(Action::PrivacyBannerOptIn, &mut app);
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::SetCodingDataSharing { opted_in: true, .. }
    ));
    assert!(app.privacy_banner_opt_in_inflight);
    assert!(!app.coding_data_retention_opt_out);
    assert!(app.privacy_banner_acked.is_none());

    let seq = app.coding_data_write_seq;
    let ack_effects = dispatch(
        Action::TaskComplete(TaskResult::CodingDataSharingUpdated {
            agent_id: AgentId(0),
            opted_in: true,
            seq,
        }),
        &mut app,
    );
    assert!(!app.privacy_banner_opt_in_inflight);
    assert!(app.privacy_banner_acked.is_some());
    assert!(!app.privacy_banner_should_show());
    assert!(
        ack_effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPrivacyBannerAcked { .. })),
        "success must persist ack: {ack_effects:?}"
    );
}

/// Already-out `[Opt out]` acks now and must not force an ACP write.
#[test]
fn privacy_banner_opt_out_acks_now_without_write() {
    use crate::views::modal::ActiveModal;
    let mut app = privacy_banner_ready_app();

    let effects = dispatch(Action::PrivacyBannerOptOut, &mut app);

    assert!(
        app.privacy_banner_acked.is_some(),
        "the ack lands on click, not on an ACP reply"
    );
    assert!(
        !app.privacy_banner_should_show(),
        "the banner is gone the moment it is dismissed"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPrivacyBannerAcked { .. })),
        "ack must persist: {effects:?}"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::SetCodingDataSharing { .. })),
        "already-out must not force an ACP write: {effects:?}"
    );
    assert_eq!(app.coding_data_write_seq, 0, "already-out is not a write");
    assert!(
        !app.privacy_banner_opt_in_inflight,
        "opt-out must not arm the opt-in inflight guard"
    );
    assert!(
        app.coding_data_retention_opt_out,
        "declining leaves the user opted out"
    );
    assert!(
        app.agents
            .values()
            .all(|a| !matches!(a.active_modal, Some(ActiveModal::Settings { .. }))),
        "[Opt out] answers the question; it must not detour into settings"
    );
}

/// A double-click (or a stale frame's hit rect) must not send a second
/// decline.
#[test]
fn privacy_banner_opt_out_is_idempotent() {
    let mut app = privacy_banner_ready_app();
    let _ = dispatch(Action::PrivacyBannerOptOut, &mut app);
    let again = dispatch(Action::PrivacyBannerOptOut, &mut app);
    assert!(
        again.is_empty(),
        "second dismissal must be inert: {again:?}"
    );
}

/// Settings Opt out while already out (banner eligible): acks, no ACP write.
#[test]
fn settings_opt_out_while_already_out_acks_without_write() {
    let mut app = privacy_banner_ready_app();
    assert!(app.privacy_banner_should_show());
    assert!(app.coding_data_retention_opt_out);

    let effects = dispatch(Action::SetCodingDataSharing { opted_in: false }, &mut app);

    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PersistPrivacyBannerAcked { .. })),
        "already-out Settings Opt out must ack: {effects:?}"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::SetCodingDataSharing { .. })),
        "already-out must not write ACP: {effects:?}"
    );
    assert!(app.privacy_banner_acked.is_some());
    assert!(!app.privacy_banner_should_show());
    assert!(app.coding_data_retention_opt_out);
    assert!(!app.privacy_banner_opt_in_inflight);
    assert_eq!(app.coding_data_write_seq, 0);
}

/// A Settings pick before the notice is rolled out must not stamp an ack
/// that would hide the banner when the cohort turns on.
#[test]
fn settings_choice_does_not_ack_when_rollout_off() {
    for opted_in in [true, false] {
        let mut app = test_app_with_agent();
        app.privacy_notice_rollout = false;
        app.coding_data_retention_opt_out = true;
        app.auth_state = AuthState::Done;
        app.trust_state = TrustState::Done;
        let effects = dispatch(Action::SetCodingDataSharing { opted_in }, &mut app);
        assert!(
            !effects
                .iter()
                .any(|e| matches!(e, Effect::PersistPrivacyBannerAcked { .. })),
            "rollout-off must not persist ack (opted_in={opted_in}): {effects:?}"
        );
        assert!(
            app.privacy_banner_acked.is_none(),
            "rollout-off must not stamp ack (opted_in={opted_in})"
        );
        if opted_in {
            let seq = app.coding_data_write_seq;
            let ack_effects = dispatch(
                Action::TaskComplete(TaskResult::CodingDataSharingUpdated {
                    agent_id: AgentId(0),
                    opted_in: true,
                    seq,
                }),
                &mut app,
            );
            assert!(
                !ack_effects
                    .iter()
                    .any(|e| matches!(e, Effect::PersistPrivacyBannerAcked { .. })),
                "rollout-off opt-in success must not ack: {ack_effects:?}"
            );
            assert!(app.privacy_banner_acked.is_none());
        }
    }
}

#[test]
fn dispatch_rename_session_updates_display_name_locally() {
    let mut app = test_app_with_agent();
    let effects = dispatch_rename_session(&mut app, "renamed via slash".into());
    assert_eq!(effects.len(), 1);
    assert_eq!(
        app.agents[&AgentId(0)].display_name.as_deref(),
        Some("renamed via slash"),
        "/rename must also update local display_name cache"
    );
    match &effects[0] {
        Effect::RenameSession { kind, .. } => {
            assert_eq!(
                *kind,
                pi_shell::session::unified_list::SessionKind::Build,
                "build-lane /rename must send kind=build"
            );
        }
        other => panic!("expected RenameSession, got {other:?}"),
    }
}

#[test]
fn dispatch_rename_session_strips_controls_before_display_name_and_effect() {
    let mut app = test_app_with_agent();
    let effects =
        dispatch_rename_session(&mut app, "  Hello\u{1b}[31mWorld\u{07}\u{9b}C1  ".into());
    assert_eq!(
        app.agents[&AgentId(0)].display_name.as_deref(),
        Some("Hello[31mWorldC1"),
        "optimistic display_name must match the shell strip (no OSC/CSI/BEL/C1)"
    );
    match &effects[..] {
        [Effect::RenameSession { title, .. }] => {
            assert_eq!(title, "Hello[31mWorldC1");
        }
        other => panic!("expected one RenameSession, got {other:?}"),
    }

    let mut app = test_app_with_agent();
    let effects = dispatch_rename_session(&mut app, "\u{1b}\u{07}\n\t".into());
    assert!(
        effects.is_empty(),
        "control-only title must not emit RenameSession: {effects:?}"
    );
    assert!(
        app.agents[&AgentId(0)].display_name.is_none(),
        "control-only title must not paint a blank/dirty display_name"
    );
    assert!(
        last_system_text(&app, AgentId(0)).contains("title must not be blank"),
        "control-only title must surface the same failed-rename system block"
    );
}

#[test]
fn dispatch_rename_session_chat_kind_stamps_kind_chat() {
    let mut app = test_app_with_agent();
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.chat_kind = true;
    agent.conversation_entry = true;
    let effects = dispatch_rename_session(&mut app, "chat rename".into());
    match &effects[..] {
        [Effect::RenameSession { kind, title, .. }] => {
            assert_eq!(title, "chat rename");
            assert_eq!(
                *kind,
                pi_shell::session::unified_list::SessionKind::Chat,
                "chat-lane /rename must send kind=chat"
            );
        }
        other => panic!("expected one RenameSession, got {other:?}"),
    }
}

#[test]
fn dispatch_rename_session_sticky_chat_local_build_stays_build() {
    let mut app = test_app_with_agent();
    app.chat_mode = true;
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    // Sticky `--chat` UI bit, local-disk history-bypass (not a conversation).
    agent.chat_kind = true;
    agent.conversation_entry = false;
    let effects = dispatch_rename_session(&mut app, "local title".into());
    match &effects[..] {
        [Effect::RenameSession { kind, title, .. }] => {
            assert_eq!(title, "local title");
            assert_eq!(
                *kind,
                pi_shell::session::unified_list::SessionKind::Build,
                "history-bypass local build under sticky --chat must send kind=build"
            );
        }
        other => panic!("expected one RenameSession, got {other:?}"),
    }
}

/// `ConfirmResetSetting { choice: Reset }` on a SHARED Bool
/// target restores the Settings modal AND fires the typed
/// `Action::SetCompactMode(default)` via recursive dispatch —
/// the `Effect::PersistSetting` is the externally-observable
/// signal. Also asserts the ui_snapshot was
/// refreshed to the new (post-reset) value (symmetric with the
/// Cancel test's snapshot assertion).
#[test]
fn dispatch_confirm_reset_setting_reset_dispatches_typed_setter_for_shared_bool() {
    use crate::settings::SettingValue;
    use crate::views::modal::{ActiveModal, ResetSettingsResult};
    let mut app = test_app_with_agent();
    // Flip compact_mode to true so we can observe the reset back
    // to its default (false).
    let _ = dispatch(Action::SetCompactMode(true), &mut app);
    assert!(app.current_ui.compact_mode);

    setup_reset_confirm_open(&mut app, "compact_mode");

    let effects = dispatch(
        Action::ConfirmResetSetting {
            choice: ResetSettingsResult::Reset,
        },
        &mut app,
    );

    // Recursive dispatch into Action::SetCompactMode(false) emits
    // the persist effect.
    assert_eq!(effects.len(), 1);
    match &effects[0] {
        Effect::PersistSetting { key, value, .. } => {
            assert_eq!(*key, "compact_mode");
            assert_eq!(value, &SettingValue::Bool(false));
        }
        other => panic!("expected PersistSetting, got {other:?}"),
    }
    // In-memory state is reset to the default.
    assert!(!app.current_ui.compact_mode);
    // Modal is restored AND ui_snapshot reflects the new value
    // (symmetric with the Cancel test).
    let agent = app.agents.get(&AgentId(0)).expect("agent must exist");
    match &agent.active_modal {
        Some(ActiveModal::Settings { state }) => {
            assert!(
                !state.ui_snapshot.compact_mode,
                "ui_snapshot must reflect the post-reset value"
            );
        }
        _ => panic!("Reset branch must restore the Settings modal"),
    }
}

/// `ConfirmResetSetting { choice: Reset }` on a SHARED Enum
/// target (`theme`) dispatches `Action::SetTheme(default)` via
/// recursive dispatch — verifies the action_for_reset Enum arm.
#[test]
fn dispatch_confirm_reset_setting_reset_dispatches_typed_setter_for_shared_enum() {
    use crate::settings::SettingValue;
    use crate::views::modal::ResetSettingsResult;
    // SetTheme mutates the global theme cache — serialize with the
    // other theme tests via the theme test lock.
    with_theme_test_env(|| {
        let mut app = test_app_with_agent();
        // Flip theme to a non-default first.
        let _ = dispatch(Action::SetTheme("tokyonight".to_string()), &mut app);
        assert_eq!(app.current_ui.theme.as_deref(), Some("tokyonight"));

        setup_reset_confirm_open(&mut app, "theme");

        let effects = dispatch(
            Action::ConfirmResetSetting {
                choice: ResetSettingsResult::Reset,
            },
            &mut app,
        );

        // Reset → SetTheme("groknight") (the registered default).
        assert_eq!(effects.len(), 1);
        match &effects[0] {
            Effect::PersistSetting { key, value, .. } => {
                assert_eq!(*key, "theme");
                assert_eq!(value, &SettingValue::Enum("groknight"));
            }
            other => panic!("expected PersistSetting, got {other:?}"),
        }
        assert_eq!(app.current_ui.theme.as_deref(), Some("groknight"));
    });
}

fn seed_scrolled_up(app: &mut AppView) {
    let sb = &mut app.agents.get_mut(&AgentId(0)).unwrap().scrollback;
    for i in 0..40 {
        sb.push_block(RenderBlock::agent_message(format!("seed {i}")));
    }
    sb.prepare_layout(80, 8);
    sb.goto_top();
}

fn current_usage_nonce(app: &AppView) -> u64 {
    match app.agents[&AgentId(0)].active_modal.as_ref() {
        Some(crate::views::modal::ActiveModal::UsageInfo { state }) => state.fetch_nonce,
        _ => 0,
    }
}

fn complete_session_usage(app: &mut AppView) {
    let nonce = current_usage_nonce(app);
    dispatch(
        Action::TaskComplete(TaskResult::SessionUsageComplete {
            agent_id: AgentId(0),
            session_id: "test-session".to_string().into(),
            usage: Box::default(),
            nonce,
        }),
        app,
    );
}

fn context_info_response() -> pi_shell::session::SessionInfoResponse {
    use pi_shell::session::acp_types::{ContextInfo, SessionInfoData};

    pi_shell::session::SessionInfoResponse {
        session_id: "test-session".to_string(),
        cwd: "/tmp/test".to_string(),
        data: SessionInfoData {
            agent_name: None,
            model: Some("grok-build".to_string()),
            model_display_name: None,
            resolved_model_id: None,
            model_fingerprint: None,
            show_model_fingerprint: false,
            api_backend: None,
            conversation_id: None,
            turns: 0,
            turn_index: 0,
            context: ContextInfo::default(),
        },
    }
}

#[test]
fn stale_context_info_results_do_not_update_replaced_session() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let before = agent_scrollback_len(&app);
    app.agents
        .get_mut(&id)
        .unwrap()
        .bind_session_id("replacement".into());

    dispatch(
        Action::TaskComplete(TaskResult::ContextInfoComplete {
            agent_id: id,
            session_id: "test-session".into(),
            info: Box::new(context_info_response()),
            nonce: Default::default(),
        }),
        &mut app,
    );
    dispatch(
        Action::TaskComplete(TaskResult::ContextInfoFailed {
            agent_id: id,
            session_id: "test-session".into(),
            error: "request failed".to_string(),
            nonce: Default::default(),
        }),
        &mut app,
    );

    assert_eq!(agent_scrollback_len(&app), before);
}

#[test]
fn session_usage_keeps_scroll_when_page_flip_off() {
    let prev = crate::appearance::cache::load_page_flip_on_send();
    crate::appearance::cache::set_page_flip_on_send(false);
    let mut app = test_app_with_agent();
    app.screen_mode = crate::app::ScreenMode::Minimal;
    app.usage_visible = false;
    seed_scrolled_up(&mut app);
    complete_session_usage(&mut app);
    assert_eq!(app.agents[&AgentId(0)].scrollback.scroll_offset(), 0);
    crate::appearance::cache::set_page_flip_on_send(prev);
}

// ── Minimal update-notice tests ──────────────────────────────────────

#[test]
fn minimal_update_notice_commits_a_system_block() {
    let mut app = test_app_with_agent();
    let before = agent_scrollback_len(&app);
    commit_minimal_update_notice(&mut app, "9.9.9");
    assert_eq!(agent_scrollback_len(&app), before + 1);
    let text = last_system_text(&app, AgentId(0));
    assert!(text.contains("Update available: v9.9.9"), "got: {text:?}");
    assert!(text.contains("Restart to apply."), "got: {text:?}");
}

#[test]
fn minimal_update_notice_no_active_agent_is_noop() {
    let mut app = test_app();
    // Must not panic and must not require an agent.
    commit_minimal_update_notice(&mut app, "9.9.9");
}

// ── Tutorial dispatch tests ──────────────────────────────────────────

// ── Usage modal (full TUI) dispatch tests ────────────────────────────

#[test]
fn usage_results_without_open_modal_are_dropped_in_full_mode() {
    let mut app = test_app_with_agent();
    let before = agent_scrollback_len(&app);
    complete_session_usage(&mut app);
    dispatch(
        Action::TaskComplete(TaskResult::SessionInfoFailed {
            agent_id: AgentId(0),
            session_id: "test-session".into(),
            error: "boom".to_string(),
            nonce: Default::default(),
        }),
        &mut app,
    );
    dispatch(
        Action::TaskComplete(TaskResult::ContextInfoFailed {
            agent_id: AgentId(0),
            session_id: "test-session".into(),
            error: "boom".to_string(),
            nonce: Default::default(),
        }),
        &mut app,
    );
    assert_eq!(agent_scrollback_len(&app), before);
}

