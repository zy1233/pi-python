//! Shared utilities used by both `pi-shell` and its downstream clients
//! (e.g. `pi-pager-render`). This crate sits upstream of `pi-shell`
//! so it must never depend on it.

pub mod clipboard;
pub mod placeholder_images;
pub mod session;
pub mod stderr;
pub mod ui_config;
