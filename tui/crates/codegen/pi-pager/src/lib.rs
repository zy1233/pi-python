//! pi-pager — Grok Build TUI.
//!
//! A clean-room implementation built on the v3 pager rendering engine.
pub mod acp;
pub(crate) mod actions;
pub mod app;
pub mod brand;
pub mod client_identity;
pub mod completions_cmd;
mod config_toml_edit;
pub(crate) mod diagnostics;
pub mod disk_usage_cmd;
pub mod docs;
pub mod doctor_cmd;
pub mod export_cmd;
pub(crate) mod fs_size;
pub(crate) mod git_info;
pub(crate) mod hyperlink_route;
pub(crate) mod inline_media_ffmpeg;
pub mod input;
pub(crate) mod input_log;
pub mod memory_release;
pub mod memory_trace;
#[path = "minimal/api.rs"]
pub mod minimal_api;
#[path = "minimal/hook.rs"]
pub mod minimal_hook;
pub(crate) mod notifications;
#[allow(unused_imports, unused_macros)]
pub(crate) mod obf;
pub(crate) mod pty_wrap;
pub(crate) mod recent_dirs;
pub mod scrollback;
pub mod search;
pub mod settings;
pub(crate) mod slash;
pub(crate) mod startup;
pub(crate) mod tips;
pub(crate) mod tutorial_docs;
pub(crate) mod wrap_clipboard_image;
pub mod wrap_cmd;
pub(crate) mod wrap_filter;
pub(crate) mod wrap_restore;
pub use pi_pager_render::{
    appearance, clipboard, gboom, glyphs, host, link_opener, modal_window_state, prompt_images,
    render, syntax, terminal, theme, util,
};
#[cfg(test)]
pub(crate) mod test_util;
pub(crate) mod tracing;
pub(crate) mod unified_log;
pub mod views;
pub mod voice;
