//! Input handling (keys, mouse).

pub mod key;
pub(crate) mod keyboard_normalizer;
pub(crate) mod line_editor;
#[cfg(target_os = "macos")]
pub(crate) mod macos_modifiers;
pub(crate) mod mouse;
pub(crate) mod scroll_log;
pub(crate) mod terminal_support;

pub use keyboard_normalizer::{KeyboardNormalizer, ModifierState};
pub use terminal_support::{is_apple_terminal_newline_modifier_held, is_mod_enter};
