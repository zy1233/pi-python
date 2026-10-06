//! Diagnostics view tests.

use super::*;

#[test]
fn warning_category_ids_are_stable() {
    let ids = [
        WarningCategory::Clipboard,
        WarningCategory::DcsPassthrough,
        WarningCategory::ControlMode,
        WarningCategory::ByobuScreen,
        WarningCategory::UnsupportedTerminal,
        WarningCategory::TmuxExtendedKeysOff,
        WarningCategory::WaylandNoDataControl,
        WarningCategory::WezTermKittyKeyboardOff,
        WarningCategory::LimitedColorSupport,
        WarningCategory::SshWithoutWrap,
        WarningCategory::NotificationProtocolFallback,
        WarningCategory::FocusTrackingUnavailable,
        WarningCategory::SandboxProfileConflict,
    ]
    .map(|category| id_for(category).expect("diagnostic category must have an ID"))
    .map(|id| id.to_string());

    assert_eq!(
        ids,
        [
            "terminal.tmux-clipboard",
            "terminal.dcs-passthrough",
            "terminal.control-mode",
            "terminal.byobu-screen",
            "terminal.unsupported-emulator",
            "terminal.tmux-extended-keys",
            "terminal.wayland-data-control",
            "terminal.wezterm-kitty",
            "terminal.limited-color",
            "terminal.ssh-wrap",
            "notifications.protocol-fallback",
            "notifications.focus-tracking-unavailable",
            "sandbox.profile-conflict",
        ]
    );
}

/// `RGB` in the resolved feature list is the only signal that 24-bit color
/// survives tmux. Empty output means the answer is unknown rather than
/// negative: tmux before 3.2 renders the unknown format as an empty string.
#[test]
fn client_features_decide_color_passthrough() {
    let cases = [
        (
            TmuxProbeResult::Available(
                "bpaste,ccolour,clipboard,cstyle,focus,RGB,title".to_owned(),
            ),
            TmuxColorPassthrough::Forwarded,
        ),
        (
            TmuxProbeResult::Available("RGB".to_owned()),
            TmuxColorPassthrough::Forwarded,
        ),
        (
            TmuxProbeResult::Available("bpaste,ccolour,clipboard,cstyle,focus,title".to_owned()),
            TmuxColorPassthrough::Reduced,
        ),
        (
            TmuxProbeResult::Available(String::new()),
            TmuxColorPassthrough::Unknown,
        ),
        (
            TmuxProbeResult::Available("   ".to_owned()),
            TmuxColorPassthrough::Unknown,
        ),
        (TmuxProbeResult::Unsupported, TmuxColorPassthrough::Unknown),
        (TmuxProbeResult::Unavailable, TmuxColorPassthrough::Unknown),
        (
            TmuxProbeResult::Error("tmux unreachable".to_owned()),
            TmuxColorPassthrough::Unknown,
        ),
    ];

    let actual = cases
        .iter()
        .map(|(result, _)| tmux_color_passthrough(result))
        .collect::<Vec<_>>();

    assert_eq!(
        actual,
        cases
            .iter()
            .map(|(_, expected)| *expected)
            .collect::<Vec<_>>()
    );
}
