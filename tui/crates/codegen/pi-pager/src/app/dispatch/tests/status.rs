//! Tests for session status and privacy-banner dispatchers.

use super::*;

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

fn privacy_banner_ready_app() -> AppView {
    let mut app = test_app_with_agent();
    app.active_view = ActiveView::Welcome;
    app.auth_state = AuthState::Done;
    app.trust_state = TrustState::Done;
    app.privacy_notice_rollout = true;
    app.privacy_banner_acked = None;
    app.privacy_banner_reshow_days = None;
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

        // Reset → SetTheme("zypinight") (the registered default).
        assert_eq!(effects.len(), 1);
        match &effects[0] {
            Effect::PersistSetting { key, value, .. } => {
                assert_eq!(*key, "theme");
                assert_eq!(value, &SettingValue::Enum("zypinight"));
            }
            other => panic!("expected PersistSetting, got {other:?}"),
        }
        assert_eq!(app.current_ui.theme.as_deref(), Some("zypinight"));
    });
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
