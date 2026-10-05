//! System-sleep refresh-straddle mitigation for [`AuthManager`].
//!
//! A refresh that straddles a suspend can lose its rotated successor token,
//! leaving a revoked refresh token on disk and forcing re-login. Two layers
//! guard against that straddle:
//!
//! 1. The gate `refresh_chain` consults *defers* a not-yet-started refresh; an
//!    in-flight one is never aborted (dropping it could discard a rotated-token
//!    response — the very revocation we guard against). See
//!    [`AuthManager::refresh_chain`].
//! 2. When sleep becomes imminent and a refresh *is* already in flight,
//!    [`AuthManager::set_system_sleep_imminent`] briefly **holds the OS sleep
//!    acknowledgment** (macOS delays `IOAllowPowerChange`; Linux holds its
//!    `delay` inhibitor — both via the blocking power-listener callback) until
//!    the refresh drains, so the in-flight exchange finishes *before* the
//!    machine suspends.
//!
//! Split out of `manager.rs` so the manager stays scannable: this is a
//! self-contained unit (the [`SleepGate`] type, the [`InFlightGuard`], and a
//! small `impl AuthManager` block driving them from OS power events).

use std::sync::atomic::Ordering;
use std::time::Duration as StdDuration;

use parking_lot::RwLock;

use super::AuthManager;
use crate::util::dual_clock::DualClock;

/// Max lifetime of the "system sleep imminent" gate. A wake event normally
/// clears it; this is the safety bound so a *missed* wake event can never
/// permanently block token refresh. Generous vs. the OS pre-sleep window
/// (macOS ~30 s, Linux ~5 s) — it only needs to outlast the sleep transition.
pub(super) const SLEEP_GATE_MAX: StdDuration = StdDuration::from_secs(120);

/// Max time a token refresh may stay deferred for **dark wake** before one is
/// forced through, mirroring [`SLEEP_GATE_MAX`]. A normal dark wake lasts
/// seconds and recurs interspersed with full wakes, so this rarely fires; it
/// rescues a machine that reports a *continuous* dark wake — e.g. an
/// interactive Mac with no display, whose system video capability is never set
/// — which would otherwise defer every refresh forever and reach the same
/// logged-out state this guard prevents. Bounded on two clocks (see
/// [`DualClock`]) so it also survives the machine sleeping between dark wakes.
///
/// The straddle risk of one forced refresh is far smaller than a guaranteed
/// logout: requests only force through while the machine is busy enough to
/// issue them (so it is unlikely to re-sleep mid-exchange), and the idle
/// proactive loop reaches this at most once per [`BACKOFF_INTERVAL`].
///
/// [`BACKOFF_INTERVAL`]: super::BACKOFF_INTERVAL
pub(super) const DARK_WAKE_DEFER_MAX: StdDuration = StdDuration::from_secs(120);

/// A gate `refresh_chain` consults to avoid *starting* an IdP refresh just
/// before sleep. Only *defers* a not-yet-started refresh; an in-flight one is
/// left to finish (see [`AuthManager::refresh_chain`]).
///
/// The raise timestamp is a [`DualClock`] so the [`SLEEP_GATE_MAX`] backstop
/// survives the sleep itself: a gate raised just before a long sleep would
/// never auto-expire on the monotonic clock alone — the exact bug that let
/// an expired token reach the server and 401 — so the gate expires once
/// *either* clock passes the bound.
#[derive(Default)]
pub(super) struct SleepGate {
    pub(super) raised_at: RwLock<Option<DualClock>>,
}

impl SleepGate {

    /// A stale gate (a missed/late wake event) is lazily lowered here so it can
    /// never permanently block refresh; this read can therefore have a side
    /// effect. The gate expires once *either* clock passes [`SLEEP_GATE_MAX`]
    /// (see [`DualClock`]): without the wall-clock arm, a gate raised before a
    /// long sleep would never auto-expire, because the monotonic clock pauses
    /// while the machine is asleep.
    pub(super) fn is_gated(&self) -> bool {
        // Copy out so the read guard drops before the write lock below
        // (parking_lot is not reentrant).
        let raised_at = *self.raised_at.read();
        let Some(raise) = raised_at else {
            return false;
        };
        let (mono, wall) = raise.elapsed();
        if mono < SLEEP_GATE_MAX && wall < SLEEP_GATE_MAX {
            return true;
        }
        // Stale gate (missed/late wake). `sleep_straddle` = the monotonic clock
        // is still under the bound but real (wall-clock) time is not: the
        // machine slept through the gate without delivering a wake event. This
        // is precisely the case the wall-clock arm exists to catch, so surface
        // it explicitly rather than folding it into the generic expiry.
        let sleep_straddle = mono < SLEEP_GATE_MAX;
        {
            // Re-check under the write lock: a `WillSleep` can raise a *fresh*
            // gate between the read above and here, and clearing that one
            // would start a refresh into the very suspend it announces.
            let mut guard = self.raised_at.write();
            match *guard {
                Some(current) if current.mono == raise.mono => *guard = None,
                Some(_fresh_raise) => return true,
                None => return false,
            }
        }
        pi_telemetry::unified_log::info(
            "auth.sleep.gate_cleared",
            None,
            Some(serde_json::json!({
                "reason": "auto_expiry",
                "sleep_straddle": sleep_straddle,
                "mono_elapsed_ms": mono.as_millis() as u64,
                "wall_elapsed_ms": wall.as_millis() as u64,
            })),
        );
        false
    }
}

/// RAII counter for in-flight IdP refreshes. Increments on construction and
/// decrements on drop so the count stays balanced even if the refresh future is
/// cancelled or panics. When the count returns to zero it wakes any
/// sleep-imminent waiter parked in
/// [`AuthManager::hold_sleep_ack_until_refresh_drains`].
pub(super) struct InFlightGuard<'a>(&'a AuthManager);

impl<'a> InFlightGuard<'a> {
    pub(super) fn new(mgr: &'a AuthManager) -> Self {
        mgr.begin_refresh_in_flight();
        Self(mgr)
    }
}

impl Drop for InFlightGuard<'_> {
    fn drop(&mut self) {
        self.0.end_refresh_in_flight();
    }
}

impl AuthManager {

    /// Mark an IdP refresh as starting. Paired with [`Self::end_refresh_in_flight`]
    /// via [`InFlightGuard`]; see [`Self::hold_sleep_ack_until_refresh_drains`].
    fn begin_refresh_in_flight(&self) {
        self.refresh_in_flight.fetch_add(1, Ordering::SeqCst);
    }

    /// Mark an IdP refresh as finished. When the count returns to zero, wake any
    /// sleep-ack waiter under the same lock it parks on, so a held OS sleep ack
    /// is released the moment the exchange finishes rather than after the full
    /// timeout. `fetch_sub` returns the *previous* value, so `== 1` is the
    /// drop-to-zero edge. Notifying with no waiter parked is cheap and harmless.
    fn end_refresh_in_flight(&self) {
        if self.refresh_in_flight.fetch_sub(1, Ordering::SeqCst) == 1 {
            let _drain = self.refresh_drain_lock.lock();
            self.refresh_drain_cv.notify_all();
        }
    }

    pub(crate) fn is_sleep_gated(&self) -> bool {
        self.sleep_gate.is_gated()
    }

    /// Whether the system is currently in a **dark wake** (see
    /// [`pi_system_power::PowerState`] for the canonical explanation of what a
    /// dark wake is and why an IdP refresh must avoid one). `refresh_chain`
    /// gates on [`Self::should_defer_for_dark_wake`], which wraps this with a
    /// deferral bound.
    ///
    /// Scoped to processes that actively listen for power events (local /
    /// interactive): if the OS power listener was never started
    /// (headless / server), we skip the query — both because dark wake is
    /// not a concern there and because a screenless Mac can read as a permanent
    /// dark wake (no video capability), which would otherwise wedge refresh.
    ///
    /// `GROK_AUTH_FORCE_DARK_WAKE=1|0` forces the answer for testing (unset
    /// = ask the OS), read **before** the `power_listener_started` check so
    /// a headless run — which never starts the listener — can drive the
    /// dark-wake paths against a real binary. Pair with
    /// `GROK_AUTH_EARLY_INVALIDATION_SECS` for a seconds-long repro.
    pub(crate) fn is_dark_wake(&self) -> bool {
        #[cfg(test)]
        if let Some(forced) = *self.dark_wake_override.lock() {
            return forced;
        }
        match std::env::var("GROK_AUTH_FORCE_DARK_WAKE").ok().as_deref() {
            Some("1") => return true,
            Some("0") => return false,
            _ => {}
        }
        if !self.power_listener_started.load(Ordering::Acquire) {
            return false;
        }
        matches!(
            pi_system_power::current_power_state(),
            pi_system_power::PowerState::DarkWake
        )
    }

    /// Ends the current dark-wake deferral run so the next one starts with a
    /// fresh [`DARK_WAKE_DEFER_MAX`] budget.
    pub(super) fn end_dark_wake_defer_run(&self) {
        *self.dark_wake_defer_since.write() = None;
    }

    /// Whether `refresh_chain` should defer this refresh because the system is
    /// in a dark wake — bounded so deferral can never be indefinite.
    ///
    /// Tracks when the current unbroken run of dark-wake deferrals began (on two
    /// clocks; see [`DualClock`]). While inside the [`DARK_WAKE_DEFER_MAX`]
    /// budget it returns `true` (defer). Once either clock passes the bound it
    /// forces one refresh through (`false`) and resets the clock, so a machine
    /// stuck reporting a continuous dark wake refreshes periodically instead of
    /// deferring forever and logging the user out. A full wake clears the run
    /// (here, or eagerly in [`Self::set_system_sleep_imminent`]).
    pub(crate) fn should_defer_for_dark_wake(&self) -> bool {
        // Sample the power state before taking the lock (it's an FFI read with
        // no ordering relationship to the budget), then one write guard for the
        // whole decision: a read-then-write would let two concurrent callers
        // both start a run and restart the budget indefinitely.
        let dark = self.is_dark_wake();
        let mut run = self.dark_wake_defer_since.write();
        if !dark {
            // Full wake (or no signal): end any deferral run in progress.
            *run = None;
            return false;
        }
        let Some(raise) = *run else {
            // First deferral of this dark-wake run: start the budget clock.
            *run = Some(DualClock::now());
            return true;
        };
        let (mono, wall) = raise.elapsed();
        if mono < DARK_WAKE_DEFER_MAX && wall < DARK_WAKE_DEFER_MAX {
            return true;
        }
        // Budget exhausted: force this refresh through and reset the clock so a
        // still-continuous dark wake defers afresh (up to DARK_WAKE_DEFER_MAX)
        // before the next forced refresh, rather than abandoning deferral
        // entirely.
        *run = None;
        drop(run);
        pi_telemetry::unified_log::warn(
            "auth.dark_wake.defer_budget_exhausted",
            None,
            Some(serde_json::json!({
                "mono_elapsed_ms": mono.as_millis() as u64,
                "wall_elapsed_ms": wall.as_millis() as u64,
            })),
        );
        false
    }

    /// Force the [`AuthManager::is_dark_wake`] result in tests.
    #[cfg(test)]
    pub(crate) fn set_dark_wake_for_test(&self, dark: bool) {
        *self.dark_wake_override.lock() = Some(dark);
    }

}
