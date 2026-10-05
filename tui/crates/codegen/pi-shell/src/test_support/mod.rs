
/// Permission bits (`mode & 0o777`) of `path`, for owner-only assertions.
#[cfg(unix)]
pub(crate) fn unix_mode(path: &std::path::Path) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).unwrap().permissions().mode() & 0o777
}

/// Keep this crate's unit-test binary from writing synthetic events into
/// the real unified log; pre-main so the redirect beats the lazily-opened
/// writer. Integration binaries under `tests/` isolate via `TestSandbox`
/// homes instead.
#[ctor::ctor]
fn redirect_unified_log_for_tests() {
    pi_telemetry::unified_log::redirect_to_temp_for_tests();
}

