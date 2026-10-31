# pi-test-support

Shared test infrastructure of the `zypi` workspace. It began as the test kit of the
Rust agent runtime: mock inference server, SSE wire-format generators, ACP stdio
clients, headless runner, scripted and counting servers. That runtime is gone, and
plan phase D.5 removed these parts with it. What is left: the `TestSandbox`
filesystem and environment owner, the `TestProcess` subprocess owner, resource
sampling, and two env helpers. PR3 owns test subprocesses only; production spawning
and startup behavior are unchanged.

**Users.** `EnvGuard` (`pi-shell` unit tests); `TestProcess`, `TestProcessConfig`,
`TestSandbox` and `scaled` (one `pi-pager` test, `memory_trace_signal_topology_tests.rs`);
`env::env_parse` and `resources::{ResourceGrowth, ResourceSnapshot}` (the `pi-tools` soak
test). Nothing else outside this crate uses it.

> **Freshness rule:** update this README in the same PR that changes `src/` —
> reviewers should treat a `src/` diff without a README diff as incomplete.

This file is the API reference for the shared test-support surface.

## Module map

| Module | What it provides |
|--------|------------------|
| `sandbox` | `TestSandbox` — one owner for a temp root, isolated `HOME`/`USERPROFILE`, explicit `GROK_HOME`, workspace, and `TMPDIR`/`TMP`/`TEMP`. Child commands use `env_clear()` plus a minimal platform allowlist, loopback `NO_PROXY`, interactive-git suppression, telemetry/feedback/trace/instrumentation/updater kill switches, and no ambient proxy variables. Unix preserves the host `SHELL` when set and falls back to `/bin/sh`; explicit overrides still win. `TestSandbox::builder().mock_url(url)` wires grok API/models/auxiliary endpoints plus a fake CI key; `.git()` initializes and commits the owned workspace. Bazel test targets that execute Git directly provide `@git_hermetic` runfiles and `GIT_BIN_PATH`; at construction, `TestSandbox` resolves that path against the parent cwd while it is still the Bazel execroot, stores absolute `GIT_BIN_PATH`/`GIT_EXEC_PATH`, and prepends the binary parent to its baseline `PATH`. `TestSandbox::git_command()` applies that cleared environment plus detached, non-interactive Git settings. Without `GIT_BIN_PATH`, ordinary baseline `PATH` is preserved and no special binary/exec vars are added. `set_env`/`extend_env` and `remove_env` are the narrow post-baseline override seam. `diagnostic_summary()` redacts credential-key segments/suffixes and all malformed/non-loopback endpoints; loopback URLs are parsed and stripped of userinfo/query/fragment. |
| `process` | `TestProcess` — canonical Tokio child owner stacked over `TestSandbox`: clears/reapplies the sandbox env, applies `pager_env`, enforces null/piped stdin policy, TTY-detaches, owns the pre-PR3 `pi_tty_utils::ProcessGroup`, and captures bounded stdout/stderr tails. Unix detachment establishes the child session/process group before exec; Windows preserves `CREATE_NO_WINDOW` and uses the existing best-effort post-spawn Job attachment without claiming atomic descendant containment. Private Unix `waitid(WNOWAIT)` observes exit so descendants are cleaned before PID/PGID reuse. `wait_with_deadline` is non-destructive; Unix `close` sends SIGTERM then escalates, while Windows uses immediate Job hard-kill policy; Drop synchronously kills and performs a bounded best-effort reap. PID becomes unavailable after reap; status/reason, truncation counters, read/lifecycle errors, and secret-sanitized tails remain cached. `TestProcessTree` is the process-tree adapter for dependencies that retain their concrete child. All lifecycle policy is test-only; production utility behavior and APIs are unchanged. |
| `resources` | `ResourceSnapshot` (RSS, threads and open files, sampled together; `None` marks a metric the platform cannot report) and `ResourceGrowth` (saturating growth over a baseline), plus `RssSampler` / `RssMeasurement` / `RssOutcome`: a background poll of peak RSS judged against a budget. They read through `pi_tty_utils`, so the soak tests and production measure the same way. |
| `env` | `EnvGuard` — RAII guard for one environment variable in `#[serial]` tests: `set` / `unset` snapshot the prior value and restore it on drop, even after a panic. `env_parse(key, default)` — parse a number from an environment variable, with a warning and the default when it does not parse. |

## Consumer matrix

| Consumer | Uses | Notes |
|----------|------|-------|
| `pi-shell` unit tests | `EnvGuard` | Tests that change the environment run under `#[serial_test::serial]`. |
| `pi-pager` `memory_trace_signal_topology_tests.rs` | `TestProcess`, `TestProcessConfig`, `TestSandbox`, `scaled` | Starts the test binary again as a child in a sandbox, so that a `SIGCHLD` test runs in its own process. |
| `pi-tools` `tests/test_subagent_soak.rs` | `env::env_parse`, `ResourceGrowth`, `ResourceSnapshot` | The soak thresholds come from environment variables. |

## Sandbox contract

- Keep the `TestSandbox` alive at least as long as every child using its paths.
- Use the builder only for construction-time endpoint/git choices. Use
  `set_env`/`extend_env` for test-specific flags and terminal brands; the last
  explicit override wins. Use `remove_env` to test absence.
- Do not add process-global env mutation. Keep filesystem/environment ownership
  in `TestSandbox`; process groups, jobs, output tails, and kill-tree ownership
  stay in the separate `TestProcess`/`TestProcessTree` harness.
- Diagnostics may name sandbox paths and sanitized HTTP(S)/WS(S) loopback URLs.
  URL parsing fails closed: userinfo/query/fragment are stripped, while malformed
  or non-loopback values are redacted. Credential-like key segments/suffixes are
  always redacted.
