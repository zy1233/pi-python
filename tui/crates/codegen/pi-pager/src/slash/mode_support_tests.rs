use pretty_assertions::assert_eq;

use super::{ModeSupport, Remedy};
use crate::app::ScreenMode;

const FULLSCREEN_ONLY: ModeSupport = ModeSupport::FullscreenOnly(Remedy::SwitchMode {
    why: "minimal is single-session",
});

#[test]
fn inline_counts_as_fullscreen() {
    for mode in [ScreenMode::Fullscreen, ScreenMode::Inline] {
        assert!(ModeSupport::Both.supports(mode));
        assert!(FULLSCREEN_ONLY.supports(mode));
    }

    assert!(ModeSupport::Both.supports(ScreenMode::Minimal));
    assert!(!FULLSCREEN_ONLY.supports(ScreenMode::Minimal));
}

#[test]
fn supported_modes_have_no_refusal() {
    assert_eq!(
        ModeSupport::Both.refusal("theme", ScreenMode::Minimal),
        None
    );
    assert_eq!(
        FULLSCREEN_ONLY.refusal("theme", ScreenMode::Inline),
        None,
        "inline is not minimal, so a fullscreen-only command runs"
    );
}

/// Pinned on the composed sentence, not the variant, so a remedy that reads
/// wrong to a user lands in the diff rather than only in the code.
#[test]
fn mode_specific_builtin_refusals_are_pinned() {
    let commands = crate::slash::commands::builtin_commands();
    let mut actual: Vec<(&str, String)> = commands
        .iter()
        .filter_map(|command| {
            let refusal = [ScreenMode::Minimal, ScreenMode::Fullscreen]
                .into_iter()
                .find_map(|mode| command.mode_support().refusal(command.name(), mode))?;
            Some((command.name(), refusal))
        })
        .collect();
    actual.sort_unstable();

    assert_eq!(
        actual,
        vec![(
            "theme",
            "/theme isn't available in minimal mode \
             (minimal renders with your terminal's own palette). \
             Run /fullscreen to switch this session."
                .to_string()
        )]
    );
}
