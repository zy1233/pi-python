//! Shared utilities used by both `pi-grok-shell` and its downstream clients
//! (e.g. `pi-grok-pager-render`). This crate sits upstream of `pi-grok-shell`
//! so it must never depend on it.

pub mod clipboard;
pub mod placeholder_images;
pub mod session;
pub mod stderr;
pub mod ui_config;
