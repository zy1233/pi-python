#[cfg(all(test, feature = "dhat-heap"))]
#[global_allocator]
static DHAT_ALLOC: dhat::Alloc = dhat::Alloc;
pub(crate) use pi_telemetry::unified_log;
pub mod agent;
pub mod auth;
pub(crate) use pi_bundle as bundle;
pub mod claude_import;
pub mod claude_import_state;
pub mod config;
pub use pi_shell_base::env;
pub mod extensions;
pub mod heap_profile;
pub use pi_http as http;
pub mod instrumentation;
pub mod managed_config;
pub use pi_models as models;
pub mod remote;
pub mod sampling;
pub mod session;
pub(crate) use pi_shell_terminal as terminal;
#[cfg(test)]
pub(crate) mod test_support;
pub mod tier;
pub mod tools;
pub mod upload;
pub mod util;
