//! End-to-end failure-scenario suite for the MCP status dispatcher +
//! bounded auto-restart pipeline.
//!
//! Every test drives the **real** [`run_dispatcher`] loop —
//! `collect_window` (50 ms coalesce) → `collect_close_candidates` +
//! `drop_dead_clients` → `flush_window` (status push + `shutting_down`
//! book-keeping) → `maybe_schedule_restart` → `auto_restart_stdio`
//! bounded `[1,4,16]s` backoff — against a single mock that wires the
//! same three observation points production uses:
//!
//! 1. `mcp_state.owned_clients`  — did the dead `Arc<McpClient>` get torn down?
//! 2. the shared [`SharedShutdownState`] — did the teardown classify as intentional?
//! 3. the mock's recorded `respawn_stdio` calls + wire pushes — did auto-restart
//!    do the right thing (fire / skip / exhaust / retry)?
//!
//! The mock shares the same `SharedShutdownState` the dispatcher
//! mutates, so the dispatcher ↔ restart-actions binding is genuinely
//! exercised rather than stubbed on both sides.
//!
//! All tests run under `start_paused = true, flavor = "current_thread"`:
//! time only advances via explicit `tokio::time::advance`, so the
//! 50 ms window and the 1/4/16 s backoff fire deterministically with no
//! wall-clock sleeps.



/// Yield enough times for the dispatcher task + any spawned
/// `auto_restart_stdio` task to make progress after a clock advance.
///
/// Why 8: after a `tokio::time::advance`, the work hops across several
/// independent `spawn_local` tasks, one task per `yield_now`. The
/// longest chain in these tests is:
///   1. dispatcher wakes from `collect_window`'s timer,
///   2. `drop_dead_clients` acquires the `McpState` lock,
///   3. `flush_window` emits,
///   4. `maybe_schedule_restart` `spawn_local`s `auto_restart_stdio`,
///   5. that task wakes from its backoff `select!`,
///   6. it runs the in-loop guard checks (one of which `.await`s
///      `is_stdio_server_configured`),
///   7. it `.await`s `respawn_stdio` (which now `.await`s the
///      `McpState` lock to re-insert), and
///   8. it pushes the status payload.
///
/// That's ~7 hand-offs; 8 yields is a small, fixed upper bound that
/// drains the whole chain deterministically under `start_paused` (no
/// wall-clock cost — `yield_now` doesn't advance the paused clock).
async fn settle() {
    for _ in 0..8 {
        tokio::task::yield_now().await;
    }
}

