//! Bounded stdio MCP auto-restart.
//!
//! When [`crate::session::mcp_dispatcher::run_dispatcher`] processes a
//! window containing a [`pi_mcp::servers::McpClientEventKind::TransportClosed`]
//! or [`pi_mcp::servers::McpClientEventKind::HandshakeFailed`] key for a
//! **stdio** MCP server, the dispatcher hands the key off to
//! [`maybe_schedule_restart`]. That function applies the guard rails listed
//! below and, if all pass, spawns a one-shot [`auto_restart_stdio`] task that
//! sleeps + respawns up to three times before parking the server as
//! `unavailable`.
//!
//! ## Backoff
//!
//! Three attempts at exactly:
//!
//! ```text
//! attempt 1 → +1s  (t=1s)
//! attempt 2 → +4s  (t=5s)
//! attempt 3 → +16s (t=21s)
//! ```
//!
//! Encoded as [`BACKOFF`]. The full window before exhaustion is 21 s.
//!
//! ## Guard rails (skip conditions)
//!
//! These are the guard rails for where auto-restart must NOT fire.
//! Same ground truth at both check sites, BUT the **check
//! order differs by design** between the two sites — see the comparison
//! table below.
//!
//! 1. **Non-restart event kind** — `maybe_schedule_restart` short-circuits
//!    for anything other than `TransportClosed` / `HandshakeFailed`. The
//!    auto-restart loop does not see other kinds (it's never invoked for
//!    them), so this gate appears only at schedule time.
//! 2. **HTTP / HttpAuth** — auto-restart is **stdio-only**. HTTP/OAuth
//!    transports go through `reset_transport` on the next tool call,
//!    which is the existing and correct recovery path. The single
//!    [`RestartActions::is_stdio_server_configured`] question returns
//!    `false` for any non-stdio configured entry, so the gate doubles as
//!    the HTTP filter (no separate `is_http` check is needed).
//! 3. **`kill_on_drop` from config diff** —
//!    [`pi_mcp::servers::start_mcp_server`] sets
//!    `kill_on_drop(true)` on the spawned `tokio::process::Command`
//!    in the `acp::McpServer::Stdio` arm. When
//!    `McpState::update_configs_diff` drops the `Arc<McpClient>` the
//!    child is SIGKILLed and the liveness watcher eventually emits
//!    `TransportClosed`. The dispatcher's
//!    [`crate::session::mcp_dispatcher::ShutdownState`] (set on
//!    `ConfigRemoved` events) is the explicit "this teardown was
//!    intentional" channel. We consult it via
//!    [`RestartActions::is_in_shutting_down`] at both check sites.
//! 4. **Disabled / not currently configured** — `update_configs_diff` or
//!    `ToggleMcpServer enabled=false` removes the stdio entry. We consult
//!    [`RestartActions::is_stdio_server_configured`] (which already
//!    folds the disabled-list check); on `false` mid-loop we emit one
//!    final [`crate::session::mcp_dispatcher::McpServerStatusReason::Disabled`]
//!    push and stop.
//! 5. **Already-Empty** — see the [`pi_mcp::servers::ClientStateKind::Empty`]
//!    doc: a previous handshake exhausted attempts. Recovery from
//!    `Empty` is via the explicit `Refresh` button, not auto-restart.
//!    Enforced upstream: the liveness watcher emits `TransportClosed`
//!    only from `Ready` / `Initializing`, never from `Empty`.
//!
//! ### Check-order difference
//!
//! | Site                       | First check                        | Then                              |
//! |----------------------------|------------------------------------|-----------------------------------|
//! | [`maybe_schedule_restart`] | `is_in_shutting_down` (cheap, sync)| `is_stdio_server_configured` (async, may hit disk) |
//! | [`auto_restart_stdio`] loop| `is_stdio_server_configured`       | `is_in_shutting_down`             |
//!
//! At schedule time we shed the cheap sync check first so we never pay
//! the async + disk hit for an event we'll skip anyway. Inside the loop
//! the priority inverts: the "user removed it" path needs an explicit
//! wire push (`Reason::Disabled`) before we exit, so we check it first;
//! `shutting_down` exit needs no push (the upstream `ConfigRemoved`
//! flush already emitted one).
//!
//! ## Telemetry
//!
//! Emitted via `tracing::info!` with the metric name in the `target:`
//! field (`metrics.mcp.auto_restart.<counter>`), one target per metric.
//!
//! | Metric                              | Labels                                                      |
//! |-------------------------------------|-------------------------------------------------------------|
//! | `mcp.auto_restart.attempted`        | `server`, `attempt`                                         |
//! | `mcp.auto_restart.succeeded`        | `server`, `attempt ∈ {1,2,3}`                               |
//! | `mcp.auto_restart.exhausted`        | `server`                                                    |
//! | `mcp.auto_restart.skipped`          | `server`, `reason ∈ {shutting_down, not_configured, disabled}` |
//!
//! `attempted` is counted once per actual `respawn_stdio` call (after
//! the in-loop guards pass and the backoff sleep elapses), not at task
//! entry — so it stays honest if the configured-set flips mid-sleep.

// ── telemetry helpers (tracing-as-metrics; see module doc § Telemetry) ──

// ── in-place HTTP recovery metrics (kept separate from auto_restart.* so
//    operators can distinguish stdio respawn from HTTP transport reset) ──

#[cfg(test)]
mod tests {
    
    
    
    

    /// `forward_status` and the dispatcher must agree on the wire
    /// method name. If someone renames
    /// `SERVER_STATUS_METHOD` only one path follows — this pinning
    /// test breaks loudly. We don't probe an actual ACP gateway —
    /// just assert the const referenced by `forward_status` is the
    /// same one re-exported by `mcp_dispatcher`.
    #[test]
    fn forward_status_uses_dispatcher_method() {
        assert_eq!(
            crate::session::mcp_dispatcher::SERVER_STATUS_METHOD,
            "x.ai/mcp/server_status",
            "wire method name pinned",
        );
        // The `forward_status` function uses
        // `mcp_dispatcher::SERVER_STATUS_METHOD` directly — same
        // const, no shadowing. If the import line at the top of
        // this file ever fans out a local copy, this test still
        // catches the wire name itself.
    }
}
