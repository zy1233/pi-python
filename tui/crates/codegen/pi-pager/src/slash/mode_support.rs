//! Which render modes a slash command works in.

use crate::app::ScreenMode;

/// What to tell a user who typed a command the current mode cannot run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Remedy {
    SwitchMode {
        /// Sentence fragment, parenthesized in the refusal:
        /// `"minimal is single-session"`.
        why: &'static str,
    },
}

/// Which render modes a slash command functions in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModeSupport {
    Both,
    FullscreenOnly(Remedy),
}

impl ModeSupport {
    pub(crate) fn supports(self, mode: ScreenMode) -> bool {
        match self {
            Self::Both => true,
            Self::FullscreenOnly(_) => !mode.is_minimal(),
        }
    }

    pub(crate) fn refusal(self, token: &str, mode: ScreenMode) -> Option<String> {
        if self.supports(mode) {
            return None;
        }
        let (remedy, current, switch) = match self {
            Self::Both => return None,
            Self::FullscreenOnly(remedy) => (remedy, "minimal", "/fullscreen"),
        };
        Some(match remedy {
            Remedy::SwitchMode { why } => format!(
                "/{token} isn't available in {current} mode ({why}). \
                 Run {switch} to switch this session."
            ),
        })
    }
}

#[cfg(test)]
#[path = "mode_support_tests.rs"]
mod tests;
