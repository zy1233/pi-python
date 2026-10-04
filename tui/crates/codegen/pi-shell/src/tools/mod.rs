//! Tool-related configuration and shared value types for pi-shell.
//!
//! Tool *execution* is not owned by this crate: the Rust-side agent runtime was
//! removed, so only the config/value types the TUI and config loader still
//! read live here. Types (ToolOutput, ToolInput, etc.) come from `pi-tools`.

pub mod config;
pub mod todo;


// Re-export key types from pi-tools for convenience
pub use self::todo::{TodoId, TodoItem, TodoPriority, TodoStatus};
