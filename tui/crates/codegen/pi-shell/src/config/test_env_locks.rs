//! Locks of the tests that set the `GROK_*` variables the config resolvers read.
//!
//! `std::env` is process-wide. The tests in `config/tests.rs` take one of these locks around each
//! variable they set. The `#[serial]` tests in `agent/config_tests.rs` use a different lock (the
//! `serial_test` key), so the two groups did not keep each other out. A `serial` test that removed
//! `GROK_SUBAGENTS` could run while a config test had set it to `1`, and the config test failed
//! (1 run in 10 with shuffled test order and 32 test threads). A `serial` test that needs these
//! variables clear now holds [`RuntimeEnvLocks`] for the whole test.

use std::sync::{Mutex, MutexGuard};

/// `GROK_SUBAGENTS` and the other `[subagents]` variables.
pub(crate) static SUBAGENTS_ENV_LOCK: Mutex<()> = Mutex::new(());
/// `GROK_RESPECT_GITIGNORE` and `GROK_DISABLE_ZDR_INCOMPATIBLE_TOOLS`.
pub(crate) static TOOLS_ENV_LOCK: Mutex<()> = Mutex::new(());
/// The `GROK_*_MODEL` overrides.
pub(crate) static MODEL_OVERRIDES_ENV_LOCK: Mutex<()> = Mutex::new(());

/// Take `lock`. A test that panicked while it held the lock must not fail the next test, so a
/// poisoned lock is still a lock.
pub(crate) fn acquire(lock: &'static Mutex<()>) -> MutexGuard<'static, ()> {
    lock.lock().unwrap_or_else(|e| e.into_inner())
}

/// The three locks above, held together until this value is dropped.
///
/// The locks are always taken in the order of the fields. A test of `config/tests.rs` holds one
/// of them at most and never waits for a second one, so no cycle is possible.
#[must_use = "the locks are released when this value is dropped"]
pub(crate) struct RuntimeEnvLocks {
    _subagents: MutexGuard<'static, ()>,
    _tools: MutexGuard<'static, ()>,
    _models: MutexGuard<'static, ()>,
}

impl RuntimeEnvLocks {
    pub(crate) fn acquire() -> Self {
        Self {
            _subagents: acquire(&SUBAGENTS_ENV_LOCK),
            _tools: acquire(&TOOLS_ENV_LOCK),
            _models: acquire(&MODEL_OVERRIDES_ENV_LOCK),
        }
    }
}
