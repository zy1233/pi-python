//! Shared subprocess helpers: a TTY-detached async runner with a wall-clock
//! timeout and concurrent pipe draining, plus the hermetic `git` binary path.

use std::process::Output;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;

use pi_tty_utils::ProcessGroup;
use tokio::io::AsyncRead;
use tokio::io::AsyncReadExt;
use tokio::process::Child;
use tokio::process::Command;
use tokio::sync::mpsc;
use tracing::debug;
use tracing::warn;

const MAX_CAPTURE_BYTES: usize = 1024 * 1024;

const READ_CHUNK_SIZE: usize = 8192;

/// Quiet lull ending the post-exit drain (backgrounded grandchildren may hold
/// the pipes open).
const POST_EXIT_QUIET: Duration = Duration::from_millis(250);

/// Hard cap on the whole post-exit drain (`POST_EXIT_QUIET` is per-chunk, so a
/// grandchild that keeps writing could otherwise reach the deadline).
const POST_EXIT_BUDGET: Duration = Duration::from_secs(2);

/// Grace between SIGTERM and SIGKILL when tearing down a timed-out process
/// group, so a signal-aware child can exit cleanly before it is force-killed.
const TERM_GRACE: Duration = Duration::from_millis(500);

/// Run a config-provided command string through the platform shell: `sh -c`
/// on unix, `cmd /C` on Windows. The escape hatch shared by the auth
/// providers and the identity command.
///
/// Windows has no `sh` on `PATH` in a default install, so hardcoding it made
/// every one of those call sites fail to spawn — and where Git Bash *is*
/// installed, `sh` eats the backslashes in a native path such as
/// `C:\corp\auth.exe`. `cmd /C` runs `.exe` / `.cmd` / `.bat` directly and
/// propagates the child's exit code, which the auth providers' "exit 0 =
/// success" contract depends on (PowerShell's `-Command` does not).
pub(crate) fn shell_c(script: &str) -> Command {
    let (shell, flag) = if cfg!(windows) {
        ("cmd", "/C")
    } else {
        ("sh", "-c")
    };
    let mut cmd = Command::new(shell);
    cmd.args([flag, script]);
    cmd
}

/// Whether the command text may appear in spawn-failure/timeout logs.
/// `Redacted` (the safe default) keeps it out for commands whose text may
/// embed secrets; `Shown` includes it for diagnostics.
#[derive(Clone, Copy)]
pub(crate) enum CommandLog<'a> {
    Shown(&'a str),
}

impl std::fmt::Display for CommandLog<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match *self {
            Self::Shown(cmd) => f.write_str(cmd),
        }
    }
}

pub(crate) struct RunOptions<'a> {
    pub label: &'a str,
    pub command_log: CommandLog<'a>,
}

/// Why [`run_detached_with_timeout`] produced no `Output`. A nonzero exit is
/// not one of these; it comes back as `Ok`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RunError {
    SpawnFailed,
    TimedOut,
    WaitFailed,
}

/// Run `cmd` detached from the TTY with a wall-clock `timeout`, killing the
/// whole process group on timeout and capping capture per stream. A nonzero
/// exit comes back as `Ok`; [`RunError`] covers the no-output cases.
pub(crate) async fn run_detached_with_timeout(
    mut cmd: Command,
    timeout: Duration,
    opts: RunOptions<'_>,
) -> Result<Output, RunError> {
    let RunOptions { label, command_log } = opts;

    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        // Pipe stderr; inherit would corrupt the TUI alternate screen.
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    pi_tools::util::detach_command(&mut cmd);
    cmd.envs(pi_tools::util::pager_env());

    #[allow(clippy::disallowed_methods)] // a process group is built for it below
    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(e) => {
            warn!(error = %e, label, cmd = %command_log, "failed to start the command");
            return Err(RunError::SpawnFailed);
        }
    };

    // Enroll the child's process group so a timeout kill reaches grandchildren;
    // on failure only the direct child is killed.
    let group = ProcessGroup::new()
        .and_then(|mut group| group.attach(&child).map(|()| group))
        .inspect_err(|e| {
            debug!(error = %e, label, "process-group enrollment failed; a timeout kill covers the direct child only");
        })
        .ok();

    let stdout_rx = child.stdout.take().map(spawn_pipe_reader);
    let stderr_rx = child.stderr.take().map(spawn_pipe_reader);

    let status = match tokio::time::timeout(timeout, child.wait()).await {
        Ok(Ok(status)) => status,
        Ok(Err(e)) => {
            warn!(error = %e, label, "error while waiting for the command");
            terminate_child(&mut child, group.as_ref()).await;
            return Err(RunError::WaitFailed);
        }
        Err(_elapsed) => {
            warn!(
                label,
                cmd = %command_log,
                timeout_secs = timeout.as_secs(),
                "command timed out; terminating"
            );
            terminate_child(&mut child, group.as_ref()).await;
            return Err(RunError::TimedOut);
        }
    };

    // Both streams drain concurrently under one budget measured fresh from the
    // child's exit, so a near-deadline exit still captures its buffered output
    // in full.
    let drain_deadline = Instant::now() + POST_EXIT_BUDGET;
    let (stdout, stderr) = tokio::join!(
        drain_reader(stdout_rx, drain_deadline),
        drain_reader(stderr_rx, drain_deadline),
    );
    if !status.success() {
        // Debug, not warn: some callers run secret-bearing commands (auth
        // tokens, resolved identities), so stderr stays out of routine logs;
        // callers that need louder reporting inspect the returned stderr.
        debug!(status = %status, label, "command exited with a nonzero status");
    }
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

/// Tear down a timed-out or errored run: SIGTERM the process group, wait
/// [`TERM_GRACE`], then SIGKILL survivors. Always reaps the direct child too,
/// so an absent group signal can't leave it unwaited.
async fn terminate_child(child: &mut Child, group: Option<&ProcessGroup>) {
    if let Some(group) = group {
        let _ = group.terminate();
        let _ = tokio::time::timeout(TERM_GRACE, child.wait()).await;
        let _ = group.kill();
    }
    let _ = child.start_kill();
    // Bounded reap: a wedged (uninterruptible) child must not park the runner;
    // `kill_on_drop` reaps it later if this wait gives up.
    let _ = tokio::time::timeout(TERM_GRACE, child.wait()).await;
}

/// Stream a child pipe over an unbounded channel, capped at
/// [`MAX_CAPTURE_BYTES`]; a closed receiver (the drain gave up) stops the
/// reader so it never parks in `read()` forever.
fn spawn_pipe_reader<R>(mut pipe: R) -> mpsc::UnboundedReceiver<Vec<u8>>
where
    R: AsyncRead + Unpin + Send + 'static,
{
    let (tx, rx) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        let mut sent = 0usize;
        let mut chunk = [0u8; READ_CHUNK_SIZE];
        loop {
            let n = tokio::select! {
                biased;
                () = tx.closed() => break,
                result = pipe.read(&mut chunk) => match result {
                    Ok(0) | Err(_) => break,
                    Ok(n) => n,
                },
            };
            let keep = n.min(MAX_CAPTURE_BYTES.saturating_sub(sent));
            if keep > 0 {
                if tx.send(chunk[..keep].to_vec()).is_err() {
                    break;
                }
                sent += keep;
            }
            // Past the cap: keep reading to drain the pipe (never block the child),
            // discarding the excess.
        }
    });
    rx
}

async fn drain_reader(
    rx: Option<mpsc::UnboundedReceiver<Vec<u8>>>,
    drain_deadline: Instant,
) -> Vec<u8> {
    let Some(mut rx) = rx else {
        return Vec::new();
    };
    let mut buf = Vec::new();
    loop {
        let Some(remaining) = drain_deadline.checked_duration_since(Instant::now()) else {
            break;
        };
        let wait = POST_EXIT_QUIET.min(remaining);
        match tokio::time::timeout(wait, rx.recv()).await {
            Ok(Some(chunk)) => buf.extend_from_slice(&chunk),
            // A closed channel is EOF; an elapsed wait is a quiet lull (a
            // grandchild may still hold the pipe).
            Ok(None) | Err(_) => break,
        }
    }
    buf
}

#[cfg(test)]
mod tests {}
