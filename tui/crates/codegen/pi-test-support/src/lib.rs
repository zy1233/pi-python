//! Shared test utilities for the TUI crates: env guard, hermetic sandbox, subprocess owner and
//! resource sampling.
//!
//! Provides:
//! - [`EnvGuard`] — Set or unset one environment variable for a `#[serial]` test, then restore it
//! - [`env::env_parse`] — Read a number from an environment variable, with a default
//! - [`TestSandbox`] — Own isolated paths, hermetic child env, optional git setup, diagnostics
//! - [`TestProcess`] — Own detached child lifecycle, process-tree teardown, bounded output tails
//! - [`ResourceSnapshot`] — RSS/threads/fds sampling for soak tests
//! - [`scaled`] — Multiply a harness timeout by `GROK_TEST_TIMEOUT_SCALE`
//!
//! The mock inference server, the SSE builders, the ACP stdio clients, the headless runner and the
//! counting server are gone (plan phase D.5): the Rust agent runtime that they tested is gone.
/// Multiply a harness timeout by `GROK_TEST_TIMEOUT_SCALE` (positive integer,
/// default 1). CI lanes on shared runner pools raise it so pool load slows
/// tests instead of failing them (see the Grok Build merge CI workflow).
pub fn scaled(base: std::time::Duration) -> std::time::Duration {
    let scale = std::env::var("GROK_TEST_TIMEOUT_SCALE")
        .ok()
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|&v| v > 0)
        .unwrap_or(1);
    base * scale
}
pub mod env;
pub mod process;
pub mod resources;
pub mod sandbox;
pub use env::EnvGuard;
#[cfg(unix)]
pub use process::process_has_exited_without_reap;
pub use process::{
    TestOutput, TestOutputSnapshot, TestProcess, TestProcessConfig, TestProcessState,
    TestProcessStderr, TestProcessStdout, TestProcessTermination, TestProcessTree, TestStdin,
};
pub use resources::{ResourceGrowth, ResourceSnapshot, RssMeasurement, RssOutcome, RssSampler};
pub use sandbox::{TestSandbox, TestSandboxBuilder};
