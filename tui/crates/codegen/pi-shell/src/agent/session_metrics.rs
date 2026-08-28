//! Session lifecycle event structs.
//!
//! Re-exported from `pi-telemetry` after the telemetry crate split.
//! The structs themselves live in the telemetry crate; this module preserves
//! the existing import path so nothing else in shell needs to change.

pub(crate) use pi_telemetry::session_metrics::{
    DoomLoopRecovery, SessionStartKind, SessionStarted, TraceUploadAttempted, TraceUploadFailed,
    TraceUploadSkipped, TraceUploadSucceeded, Turn, TurnCompletedLifecycle,
};
