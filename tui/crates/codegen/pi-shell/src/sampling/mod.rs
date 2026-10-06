//! Thin shim over `pi-sampling-types` (conversation types, API-backend enum) plus the error
//! classification helpers the pager still renders.
//!
//! The Rust sampling client (`pi-sampler`) is gone: model requests are made by the ACP agent
//! (today the Python `pi_agent_cli`), never by this process.

pub(crate) mod conversation;
pub mod error;
pub mod types;

pub use self::conversation::*;
pub use pi_sampling_types::ApiBackend;
