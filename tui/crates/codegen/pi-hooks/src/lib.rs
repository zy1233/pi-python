//! # pi-hooks
//!
//! Hook definitions: file-based discovery, parsing and trust.
//!
//! ## Overview
//!
//! Hooks are discovered from dedicated directories (`~/.grok/hooks/` and
//! `<git-worktree-root>/.grok/hooks/`) and defined in JSON files (compatible settings format).
//! This crate loads and checks the definitions. It does not run them: the dispatcher and the
//! command and HTTP runners are gone (plan D.8), because nothing in `zypi` called them. The
//! Python agent has no hook system that reads these definitions.
//!
//! ## What is here
//!
//! - `config`: the hook JSON format, `HookSpec`, environment expansion at load time
//! - `discovery`: `load_hooks`, `HookRegistry`, the hook directories and settings layers
//! - `event`: the event names and the payloads of the events
//! - `matcher`: the tool-name matcher of a hook
//! - `trust`: the legacy trust file of projects (read once, for a migration) and the list of
//!   disabled hooks
//!
//! ## Quick start
//!
//! ```rust,no_run
//! use std::path::Path;
//! use pi_hooks::discovery::load_hooks;
//! use pi_hooks::event::HookEventName;
//!
//! let (registry, errors) = load_hooks(
//!     Some(Path::new("/home/user/.grok/hooks")),
//!     Some(Path::new("/project/.grok/hooks")),
//! );
//!
//! for err in &errors {
//!     eprintln!("hook load warning: {err}");
//! }
//!
//! let pre_hooks = registry.hooks_for(HookEventName::PreToolUse);
//! println!("loaded {} pre_tool_use hooks", pre_hooks.len());
//! ```

pub mod config;
pub mod discovery;
mod env_expand;
pub mod error;
pub mod event;
pub mod matcher;
#[cfg(test)]
mod test_support;
pub mod trust;
