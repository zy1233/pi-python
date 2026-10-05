//! Shim — see `pi_telemetry::instrumentation` for the implementation.
//!
//! Two pieces stay here:
//! - The [`instrumentation_timer!`] macro, because it's `#[macro_export]`-ed
//!   from this crate and call sites spell it `crate::instrumentation_timer!`
//!   (i.e. `pi_shell::instrumentation_timer!`). Keeping the macro here
//!   means downstream callers don't need to be edited.
//! - [`finalize_and_exit`], because shell needs to log a terminal exit event
//!   and shut down the shared OTel pipeline before the process exits. The
//!   telemetry crate exposes the shutdown helper, so this thin wrapper just
//!   plumbs it together with `process::exit`.

pub(crate) use pi_telemetry::instrumentation::{
    InstrumentationMode, InstrumentationTimer, TARGET, current_mode,
};

/// Time a block under the instrumentation target.
///
/// Macro stays in shell so `$crate` continues to resolve to `pi_shell`
/// for the 12+ existing call sites that spell it as
/// `crate::instrumentation_timer!(...)` or `pi_shell::instrumentation_timer!(...)`.
/// The macro body delegates to types and functions in
/// `pi_telemetry::instrumentation`.
#[macro_export]
macro_rules! instrumentation_timer {
    ($name:literal) => {{
        let mode = $crate::instrumentation::current_mode();
        match mode {
            $crate::instrumentation::InstrumentationMode::Chrome => {
                let span = tracing::info_span!(target: $crate::instrumentation::TARGET, $name);
                $crate::instrumentation::InstrumentationTimer::new_with_span(
                    $name,
                    mode,
                    Some(span.entered()),
                )
            }
            _ => $crate::instrumentation::InstrumentationTimer::new($name),
        }
    }};
}
