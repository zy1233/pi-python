//! Folder-trust grant, re-published from `pi-workspace`.
//!
//! This module used to hold the consume side of the folder-trust gate: a cache with one verdict
//! per workspace, and a `project_scope_allowed` check that the loaders of repo-local MCP servers,
//! plugins and permission policy called before they read what a cloned repository ships. Nothing
//! calls the gate any more, because those call sites went with the Rust agent runtime (plan
//! phase D.8). The decision side (scan, precedence, prompt, trust store) lives in `pi-workspace`;
//! the pager uses it for its own "do you trust this folder?" dialog.

// `grant_folder_trust` is the only item that the pager still takes from here (for `--trust`).
pub use pi_workspace::folder_trust::grant_folder_trust;
