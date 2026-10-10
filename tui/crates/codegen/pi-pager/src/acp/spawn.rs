//! Agent spawning — creates a Python stdio ACP bridge process and channels.
//!
//! Interactive TUI spawns `python -m pi_agent_cli` (or `[agent].command`) and
//! bridges JSON-RPC over stdio into an [`AcpClientChannel`].

use std::io::IsTerminal;
use std::thread;
use std::time::Duration;

use anyhow::Result;
use pi_telemetry::startup::{self, StartupPhase};
use tokio_util::sync::CancellationToken;

use pi_acp_lib::{
    AcpAgentChannel, AcpClientChannel, AcpGatewayReceiver, AcpGatewaySender, acp_channels,
};
use pi_shell::{
    agent::config::Config as AgentConfig, auth::AuthManager, util::grok_home::grok_home,
};

/// Upper bound for the agent process to be stopped and reaped after cancel:
/// [`AGENT_EOF_GRACE`] for a voluntary exit, then the kill and its reap.
/// Kept at the historical value so the pager's overall exit budget
/// (`app::exit_timeout`) is unchanged.
const AGENT_EXIT_GRACE: Duration = Duration::from_secs(10);

/// How long the agent gets to exit by itself once its stdin is closed.
///
/// EOF is the stop request: the Python agent aborts the turns in flight, which
/// reaps the process groups of its running tools, and exits 0 (measured; the
/// stdio contract tests pin it). SIGKILL gives it no such chance, so the tools
/// of a killed agent outlive it as orphans — the kill is the last resort after
/// this grace, not the first move. Well below [`AGENT_EXIT_GRACE`].
const AGENT_EOF_GRACE: Duration = Duration::from_secs(3);

const _: () = assert!(AGENT_EOF_GRACE.as_millis() < AGENT_EXIT_GRACE.as_millis());

/// Extra slack when joining the bridge thread after the agent process has been
/// reaped, so the thread can unwind.
const BRIDGE_JOIN_SLACK: Duration = Duration::from_secs(2);

/// How long the join stays silent before telling an interactive user why exit
/// is taking a moment. Short joins (the common case) print nothing.
const JOIN_NOTICE_AFTER: Duration = Duration::from_millis(1500);

/// Stderr notice after a slow join: the agent process is not gone yet.
const JOIN_NOTICE: &str = "Stopping agent…";

/// A spawned ACP agent process, seen from the pager.
pub struct AgentProcess {
    /// OS thread running the stdio bridge, which owns the child process: on
    /// cancel it kills and reaps the child, then returns. Hand it to
    /// [`AgentProcessGuard`] so every exit path stops the agent.
    pub bridge_thread: thread::JoinHandle<Result<()>>,
    pub channel: AcpClientChannel,
    pub cancel: CancellationToken,
    /// An `AuthManager` for pager-side consumers (e.g. the voice channel) that
    /// resolve a refreshing bearer.
    pub auth_manager: std::sync::Arc<AuthManager>,
}

/// The single teardown mechanism for the agent process: cancels the bridge and
/// joins its thread on drop, so the child is killed and reaped before the pager
/// exits — on normal return, `?` bail, or panic unwind alike.
///
/// Only `app::run` owns one. It drops it explicitly, after the terminal is
/// restored and before the telemetry drain and the process-scope sweep, so the
/// child is gone first.
pub struct AgentProcessGuard {
    cancel: CancellationToken,
    thread: Option<thread::JoinHandle<Result<()>>>,
}

impl AgentProcessGuard {
    /// Guard the bridge thread of a spawned agent process. A `None` thread
    /// makes the guard cancel-only (nothing to join).
    pub fn new(cancel: CancellationToken, thread: Option<thread::JoinHandle<Result<()>>>) -> Self {
        Self { cancel, thread }
    }
}

impl Drop for AgentProcessGuard {
    fn drop(&mut self) {
        self.cancel.cancel();
        let Some(handle) = self.thread.take() else {
            return;
        };
        let timeout = AGENT_EXIT_GRACE + BRIDGE_JOIN_SLACK;
        match join_bridge_thread(handle, timeout) {
            JoinOutcome::Joined => {}
            JoinOutcome::Failed(error) => {
                tracing::warn!(%error, "agent bridge exited with error after cancel");
            }
            JoinOutcome::Panicked(panic) => {
                tracing::warn!(%panic, "agent bridge panicked after cancel");
            }
            JoinOutcome::TimedOut => {
                tracing::warn!(
                    timeout_ms = timeout.as_millis() as u64,
                    "agent bridge did not exit within the budget after cancel; \
                     the agent process may still be running"
                );
            }
            JoinOutcome::HelperLost => {
                tracing::warn!("agent bridge join helper disappeared; proceeding");
            }
        }
    }
}

/// Why the join ended, so each case is explicit at the call site (and callers
/// can tell a reaped agent from an abandoned one).
#[derive(Debug, PartialEq, Eq)]
enum JoinOutcome {
    /// Bridge returned cleanly: the agent process was killed and reaped.
    Joined,
    /// Bridge returned an error; the agent process may still be running.
    Failed(String),
    /// Bridge panicked, with the payload rendered as text.
    Panicked(String),
    /// Bridge was still running when the budget elapsed.
    TimedOut,
    /// The join helper vanished without reporting (helper thread itself died).
    HelperLost,
}

/// Wait up to `timeout` for a cancelled bridge thread to exit.
///
/// The blocking `join` runs on a helper thread so this stays callable from
/// `Drop` — which cannot await — while every caller sits on the async runtime.
/// On timeout that helper is abandoned rather than joined; this is safe **only
/// because every caller is on its way out of the process**, so the OS reaps the
/// thread at exit. Do not reuse this outside teardown.
fn join_bridge_thread(handle: thread::JoinHandle<Result<()>>, timeout: Duration) -> JoinOutcome {
    use std::sync::mpsc::RecvTimeoutError;

    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let _ = tx.send(handle.join());
    });

    // Two-phase wait: silent for a short join (overwhelmingly the common case),
    // then a one-line notice so a slow stop does not look like a frozen exit.
    // Only for a terminal — piped/JSON consumers stay clean.
    let quiet = timeout.min(JOIN_NOTICE_AFTER);
    match rx.recv_timeout(quiet) {
        Ok(result) => return classify_join(result),
        Err(RecvTimeoutError::Timeout) => {
            if std::io::stderr().is_terminal() {
                eprintln!("{JOIN_NOTICE}");
            }
        }
        Err(RecvTimeoutError::Disconnected) => return JoinOutcome::HelperLost,
    }
    match rx.recv_timeout(timeout.saturating_sub(quiet)) {
        Ok(result) => classify_join(result),
        Err(RecvTimeoutError::Timeout) => JoinOutcome::TimedOut,
        Err(RecvTimeoutError::Disconnected) => JoinOutcome::HelperLost,
    }
}

fn classify_join(result: thread::Result<Result<()>>) -> JoinOutcome {
    match result {
        Ok(Ok(())) => JoinOutcome::Joined,
        Ok(Err(e)) => JoinOutcome::Failed(e.to_string()),
        Err(payload) => JoinOutcome::Panicked(panic_message(payload)),
    }
}

/// Render a panic payload as text — `panic!` payloads are `&str` or `String`,
/// so the log shows the message instead of an opaque `Any`.
fn panic_message(payload: Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = payload.downcast_ref::<&'static str>() {
        (*s).to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "non-string panic payload".to_string()
    }
}

/// How the agent process ended after the stop request.
#[derive(Debug, PartialEq, Eq)]
enum StopOutcome {
    /// It exited by itself within the grace period.
    Exited,
    /// It ignored the stop request and was killed.
    Killed,
}

/// Wait up to `grace` for `child` to exit by itself — its stdin is already
/// closed, which is the stop request — then kill it if it has not, and reap it.
async fn stop_child(child: &mut tokio::process::Child, grace: Duration) -> StopOutcome {
    match tokio::time::timeout(grace, child.wait()).await {
        Ok(_) => StopOutcome::Exited,
        Err(_) => {
            let _ = child.start_kill();
            let _ = child.wait().await;
            StopOutcome::Killed
        }
    }
}

/// Spawn the standard-ACP agent (`python -m pi_agent_cli` by default) as a child
/// process and bridge its stdio JSON-RPC into an [`AcpClientChannel`].
///
/// The agent runs out of process. `AuthManager` is still constructed for pager
/// fields that expect it (voice), but pi login is not performed — LLM
/// credentials live in the agent's own environment.
pub async fn spawn_agent_process(
    agent_config: AgentConfig,
    cancel: &CancellationToken,
) -> Result<AgentProcess> {
    let auth_manager = std::sync::Arc::new(AuthManager::new(
        &grok_home(),
        agent_config.grok_com_config.clone(),
    ));

    let agent_cancel = cancel.child_token();
    let (acp_client, acp_agent) = acp_channels();

    startup::enter(StartupPhase::WorkerSpawn);
    let bridge_thread = spawn_python_stdio_bridge(acp_agent, agent_cancel.clone()).await?;

    Ok(AgentProcess {
        bridge_thread,
        channel: acp_client,
        cancel: agent_cancel,
        auth_manager,
    })
}

/// Resolve a spawn program path for the current host (WSL translates `D:/…` → `/mnt/d/…`).
pub fn resolve_spawn_program(program: &std::ffi::OsStr) -> std::ffi::OsString {
    if std::path::Path::new(program).exists() {
        return program.to_os_string();
    }
    let raw = program.to_string_lossy();
    if pi_tty_utils::is_wsl() {
        if let Some(wsl) = windows_path_to_wsl(&raw) {
            if wsl.exists() {
                return wsl.into_os_string();
            }
        }
    }
    program.to_os_string()
}

/// `D:/foo/bar` or `D:\foo\bar` → `/mnt/d/foo/bar` (WSL interop).
fn windows_path_to_wsl(path: &str) -> Option<std::path::PathBuf> {
    let trimmed = path.trim();
    let bytes = trimmed.as_bytes();
    if bytes.len() < 3 || !bytes[0].is_ascii_alphabetic() || bytes[1] != b':' {
        return None;
    }
    if bytes[2] != b'/' && bytes[2] != b'\\' {
        return None;
    }
    let drive = bytes[0].to_ascii_lowercase() as char;
    let rest = trimmed[3..].replace('\\', "/");
    Some(std::path::PathBuf::from(format!("/mnt/{drive}/{rest}")))
}

/// Resolve the Python ACP agent command (`PI_AGENT_COMMAND` / `PI_PYTHON`).
pub fn pi_agent_command() -> (std::ffi::OsString, Vec<std::ffi::OsString>) {
    if let Ok(raw) = std::env::var("PI_AGENT_COMMAND")
        && let Some((prog, args)) = parse_agent_command(&raw)
    {
        return (resolve_spawn_program(&prog), args);
    }
    if let Some((prog, args)) = agent_command_from_config(&grok_home()) {
        return (resolve_spawn_program(&prog), args);
    }
    let python = std::env::var_os("PI_PYTHON").unwrap_or_else(|| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    });
    (
        resolve_spawn_program(&python),
        vec!["-m".into(), "pi_agent_cli".into()],
    )
}

fn agent_command_from_config(
    home: &std::path::Path,
) -> Option<(std::ffi::OsString, Vec<std::ffi::OsString>)> {
    for name in ["agent.toml", "config.toml"] {
        if let Some(cmd) = agent_command_from_toml_file(&home.join(name)) {
            return Some(cmd);
        }
    }
    None
}

fn agent_command_from_toml_file(
    path: &std::path::Path,
) -> Option<(std::ffi::OsString, Vec<std::ffi::OsString>)> {
    let content = std::fs::read_to_string(path).ok()?;
    let table: toml::Table = toml::from_str(&content).ok()?;
    let agent = table.get("agent")?.as_table()?;
    let cmd = agent.get("command")?.as_str()?.trim();
    parse_agent_command(cmd)
}

fn parse_agent_command(raw: &str) -> Option<(std::ffi::OsString, Vec<std::ffi::OsString>)> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Some(parts) = shlex::split(trimmed)
        && let Some((prog, args)) = parts.split_first()
    {
        return Some((
            std::ffi::OsString::from(prog),
            args.iter().map(std::ffi::OsString::from).collect(),
        ));
    }
    let mut parts = trimmed.split_whitespace();
    let prog = parts.next()?;
    Some((
        std::ffi::OsString::from(prog),
        parts.map(std::ffi::OsString::from).collect(),
    ))
}

#[cfg(test)]
mod pi_agent_command_tests {
    use super::*;

    #[test]
    fn agent_command_from_config_prefers_agent_toml() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(
            tmp.path().join("config.toml"),
            "[agent]\ncommand = \"python -m wrong\"\n",
        )
        .unwrap();
        std::fs::write(
            tmp.path().join("agent.toml"),
            "[agent]\ncommand = \"python -m pi_agent_cli\"\n",
        )
        .unwrap();
        let (prog, args) = agent_command_from_config(tmp.path()).unwrap();
        assert_eq!(prog, std::ffi::OsString::from("python"));
        assert_eq!(
            args,
            vec![
                std::ffi::OsString::from("-m"),
                std::ffi::OsString::from("pi_agent_cli"),
            ]
        );
    }

    #[test]
    fn agent_command_from_config_parses_agent_section() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(
            tmp.path().join("config.toml"),
            "[agent]\ncommand = \"python -m pi_agent_cli\"\n",
        )
        .unwrap();
        let (prog, args) = agent_command_from_config(tmp.path()).unwrap();
        assert_eq!(prog, std::ffi::OsString::from("python"));
        assert_eq!(
            args,
            vec![
                std::ffi::OsString::from("-m"),
                std::ffi::OsString::from("pi_agent_cli"),
            ]
        );
    }

    #[test]
    fn windows_path_to_wsl_converts_drive_paths() {
        assert_eq!(
            super::windows_path_to_wsl("D:/work/pi-python/.venv/Scripts/python.exe"),
            Some(std::path::PathBuf::from(
                "/mnt/d/work/pi-python/.venv/Scripts/python.exe"
            ))
        );
        assert_eq!(
            super::windows_path_to_wsl(r"D:\work\pi-python\.venv\Scripts\python.exe"),
            Some(std::path::PathBuf::from(
                "/mnt/d/work/pi-python/.venv/Scripts/python.exe"
            ))
        );
        assert!(super::windows_path_to_wsl("/usr/bin/python3").is_none());
    }

    #[test]
    fn parse_agent_command_supports_quoted_windows_paths() {
        let (prog, args) =
            parse_agent_command(r#""C:\Program Files\Python312\python.exe" -m pi_agent_cli"#)
                .expect("quoted command should parse");
        assert_eq!(
            prog,
            std::ffi::OsString::from(r"C:\Program Files\Python312\python.exe")
        );
        assert_eq!(
            args,
            vec![
                std::ffi::OsString::from("-m"),
                std::ffi::OsString::from("pi_agent_cli"),
            ]
        );
    }
}

/// Where the agent's stderr goes while the TUI runs, under the config home.
const AGENT_STDERR_LOG: &str = "agent.stderr.log";

/// A log longer than this at spawn time is moved to `agent.stderr.log.1` (replacing the one that
/// was there) and a new one is started, so the two files hold a few runs at most. The size is not
/// enforced while an agent runs: it is the agent's own output, which is small unless it is stuck
/// in a loop that prints.
const AGENT_STDERR_LOG_ROTATE_BYTES: u64 = 1024 * 1024;

fn agent_stderr_log_path(home: &std::path::Path) -> std::path::PathBuf {
    home.join("logs").join(AGENT_STDERR_LOG)
}

/// Open the agent's stderr log for appending, rotating it first if it has grown past
/// `rotate_over`, and write a line that says where this run's output starts: the file outlives
/// the run. Owner-only on Unix, since a traceback can quote prompts, paths and keys.
fn open_agent_stderr_log(
    path: &std::path::Path,
    rotate_over: u64,
) -> std::io::Result<std::fs::File> {
    use std::io::Write as _;

    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if std::fs::metadata(path).is_ok_and(|meta| meta.len() > rotate_over) {
        let mut older = path.as_os_str().to_owned();
        older.push(".1");
        // Renaming over an existing file fails on Windows. A rotation that cannot be done is not
        // worth losing the log for: keep appending to the big file.
        let _ = std::fs::remove_file(&older);
        let _ = std::fs::rename(path, &older);
    }
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    writeln!(
        file,
        "--- agent started {} (zypi pid {}) ---",
        chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false),
        std::process::id()
    )?;
    Ok(file)
}

/// The agent's stderr in the TUI: its log file, or nothing when that cannot be opened (a log that
/// cannot be written must not keep the agent from starting). It cannot be the terminal: the
/// screen belongs to the TUI.
fn agent_stderr(home: &std::path::Path) -> std::process::Stdio {
    let path = agent_stderr_log_path(home);
    match open_agent_stderr_log(&path, AGENT_STDERR_LOG_ROTATE_BYTES) {
        Ok(file) => file.into(),
        Err(error) => {
            tracing::warn!(
                path = %path.display(),
                %error,
                "cannot open the agent's stderr log; its stderr is discarded"
            );
            std::process::Stdio::null()
        }
    }
}

/// Spawn the agent child and the thread that bridges its stdio to `channel`.
///
/// The returned thread owns the child: on `cancel` it closes the child's stdin
/// (EOF is the stop request), gives it [`AGENT_EOF_GRACE`] to exit by itself,
/// kills it if it does not, reaps it, and returns. The child's stderr is
/// `<home>/logs/agent.stderr.log` (see [`agent_stderr`]); before it was written there it went
/// to this process's stderr, which `app::run` has already pointed at `/dev/null`, so Python
/// tracebacks and warnings were thrown away.
async fn spawn_python_stdio_bridge(
    channel: AcpAgentChannel,
    cancel: CancellationToken,
) -> Result<thread::JoinHandle<Result<()>>> {
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, simplex};
    use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};

    const MAX_BUF: usize = 8 * 1024 * 1024;

    let (program, args) = pi_agent_command();
    let home = grok_home();
    let rt = tokio::task::spawn_blocking(|| {
        let mut builder = tokio::runtime::Builder::new_current_thread();
        pi_tty_utils::runtime::build_with_blocking_pool(builder.enable_all())
    })
    .await
    .map_err(|e| anyhow::anyhow!("agent runtime worker join: {e}"))?
    .map_err(|e| anyhow::anyhow!("failed to start agent runtime: {e}"))?;

    Ok(thread::Builder::new()
        .name("acp-python-bridge".into())
        .spawn(move || -> Result<()> {
            let local = tokio::task::LocalSet::new();
            local.block_on(&rt, async move {
                let mut child = tokio::process::Command::new(&program)
                    .args(&args)
                    .stdin(std::process::Stdio::piped())
                    .stdout(std::process::Stdio::piped())
                    .stderr(agent_stderr(&home))
                    .env("PI_HOME", &home)
                    .kill_on_drop(true)
                    .spawn()
                    .map_err(|e| {
                        anyhow::anyhow!(
                            "failed to spawn ACP agent {program:?} {args:?}: {e}\n\
                             Set PI_AGENT_COMMAND or PI_PYTHON, and install pi-agent-cli-lc."
                        )
                    })?;

                let mut child_stdin = child
                    .stdin
                    .take()
                    .ok_or_else(|| anyhow::anyhow!("agent stdin not piped"))?;
                let child_stdout = child
                    .stdout
                    .take()
                    .ok_or_else(|| anyhow::anyhow!("agent stdout not piped"))?;

                let (incoming_read, mut incoming_write) = simplex(MAX_BUF);
                let (outgoing_read, outgoing_write) = simplex(MAX_BUF);

                let cancel_r = cancel.clone();
                let reader_task = tokio::task::spawn_local(async move {
                    let mut lines = BufReader::new(child_stdout).lines();
                    // After cancel the lines are read and dropped until the agent
                    // closes its stdout: a stopping agent must not find the pipe
                    // closed (EPIPE noise on the restored terminal). The bridge
                    // aborts this task once the child is reaped.
                    while let Ok(Some(json_line)) = lines.next_line().await {
                        if cancel_r.is_cancelled() {
                            continue;
                        }
                        if incoming_write
                            .write_all(json_line.as_bytes())
                            .await
                            .is_err()
                            || incoming_write.write_all(b"\n").await.is_err()
                        {
                            break;
                        }
                    }
                });

                let cancel_w = cancel.clone();
                let writer_task = tokio::task::spawn_local(async move {
                    let mut reader = BufReader::new(outgoing_read);
                    let mut line = String::new();
                    loop {
                        line.clear();
                        tokio::select! {
                            biased;
                            _ = cancel_w.cancelled() => break,
                            result = reader.read_line(&mut line) => {
                                match result {
                                    Ok(0) => break,
                                    Ok(_) => {
                                        let pending = line.trim_end();
                                        if pending.is_empty() {
                                            continue;
                                        }
                                        if child_stdin.write_all(pending.as_bytes()).await.is_err()
                                            || child_stdin.write_all(b"\n").await.is_err()
                                        {
                                            break;
                                        }
                                    }
                                    Err(_) => break,
                                }
                            }
                        }
                    }
                });

                let gw_tx = AcpGatewaySender::new(channel.tx).with_tracing(true);
                let incoming = pi_acp_lib::LineBufferedRead::spawn_local(incoming_read.compat());
                let (conn, handle_io) = agent_client_protocol::ClientSideConnection::new(
                    gw_tx,
                    outgoing_write.compat_write(),
                    incoming,
                    |fut| {
                        tokio::task::spawn_local(fut);
                    },
                );
                let gw_rx = AcpGatewayReceiver::new(channel.rx, conn).with_tracing(true);
                tokio::task::spawn_local(handle_io);
                tokio::task::spawn_local(gw_rx.run());
                tokio::task::yield_now().await;

                cancel.cancelled().await;
                // Stop request: end the writer task, which drops the child's stdin
                // (EOF). The Python agent treats EOF as "the client is gone".
                writer_task.abort();
                let _ = writer_task.await;
                if stop_child(&mut child, AGENT_EOF_GRACE).await == StopOutcome::Killed {
                    tracing::warn!(
                        grace_ms = AGENT_EOF_GRACE.as_millis() as u64,
                        "agent did not exit after its stdin closed; killed it"
                    );
                }
                reader_task.abort();
                let _ = reader_task.await;
                Ok(())
            })
        })?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_reports_clean_bridge_exit() {
        let handle = thread::spawn(|| Ok(()));
        assert_eq!(
            join_bridge_thread(handle, Duration::from_secs(5)),
            JoinOutcome::Joined
        );
    }

    #[test]
    fn join_reports_bridge_error() {
        let handle = thread::spawn(|| Err(anyhow::anyhow!("reap failed")));
        assert_eq!(
            join_bridge_thread(handle, Duration::from_secs(5)),
            JoinOutcome::Failed("reap failed".to_string())
        );
    }

    /// The timeout branch the built-binary e2e cannot reach: a wedged bridge
    /// (e.g. a reap stuck on an unkillable child) is abandoned once the budget
    /// elapses instead of holding the process open indefinitely.
    #[test]
    fn join_abandons_wedged_bridge_at_budget() {
        let handle = thread::spawn(|| {
            thread::sleep(Duration::from_secs(30));
            Ok(())
        });
        let started = std::time::Instant::now();
        assert_eq!(
            join_bridge_thread(handle, Duration::from_millis(50)),
            JoinOutcome::TimedOut
        );
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "join must return at its budget, not wait out the bridge"
        );
    }

    #[cfg(unix)]
    fn spawn_sh(script: &str) -> tokio::process::Child {
        tokio::process::Command::new("sh")
            .arg("-c")
            .arg(script)
            .stdin(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .expect("spawn sh")
    }

    /// An agent that exits when its stdin closes — what the Python agent does —
    /// is reaped without a kill.
    #[cfg(unix)]
    #[tokio::test]
    async fn child_that_exits_on_eof_is_not_killed() {
        let mut child = spawn_sh("cat > /dev/null");
        drop(child.stdin.take());
        let started = std::time::Instant::now();
        assert_eq!(
            stop_child(&mut child, Duration::from_secs(30)).await,
            StopOutcome::Exited
        );
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "a voluntary exit must not wait out the grace"
        );
    }

    /// An agent that ignores the stop request is killed once the grace is over,
    /// instead of holding the exit open.
    #[cfg(unix)]
    #[tokio::test]
    async fn child_that_ignores_eof_is_killed_after_the_grace() {
        let mut child = spawn_sh("exec sleep 60");
        drop(child.stdin.take());
        let started = std::time::Instant::now();
        assert_eq!(
            stop_child(&mut child, Duration::from_millis(100)).await,
            StopOutcome::Killed
        );
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "the kill must follow the grace, not the child's own lifetime"
        );
        assert!(
            child.try_wait().expect("try_wait").is_some(),
            "the killed child must have been reaped"
        );
    }

    #[test]
    fn panic_payloads_render_as_text() {
        assert_eq!(
            classify_join(Err(Box::new("boom"))),
            JoinOutcome::Panicked("boom".to_string())
        );
        assert_eq!(
            classify_join(Err(Box::new("boom".to_string()))),
            JoinOutcome::Panicked("boom".to_string())
        );
        assert_eq!(
            classify_join(Err(Box::new(7u32))),
            JoinOutcome::Panicked("non-string panic payload".to_string())
        );
    }
}

#[cfg(test)]
mod agent_stderr_tests {
    use super::*;

    fn log_in(home: &std::path::Path) -> std::path::PathBuf {
        agent_stderr_log_path(home)
    }

    #[test]
    fn the_log_lives_under_logs_in_the_config_home() {
        let home = std::path::Path::new("some-home");
        let path = agent_stderr_log_path(home);
        assert!(path.starts_with(home), "{path:?}");
        assert!(path.ends_with("logs/agent.stderr.log"), "{path:?}");
    }

    #[test]
    fn opening_creates_the_directory_and_marks_where_a_run_starts() {
        let home = tempfile::tempdir().unwrap();
        let path = log_in(home.path());
        let _file = open_agent_stderr_log(&path, u64::MAX).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.starts_with("--- agent started "), "{text:?}");
        assert!(
            text.contains(&format!("(zypi pid {})", std::process::id())),
            "{text:?}"
        );
        assert!(text.ends_with(" ---\n"), "{text:?}");
    }

    #[test]
    fn the_next_run_appends_to_what_the_last_one_wrote() {
        use std::io::Write as _;

        let home = tempfile::tempdir().unwrap();
        let path = log_in(home.path());
        let mut first = open_agent_stderr_log(&path, u64::MAX).unwrap();
        writeln!(first, "Traceback: the first run").unwrap();
        drop(first);
        drop(open_agent_stderr_log(&path, u64::MAX).unwrap());
        let text = std::fs::read_to_string(&path).unwrap();
        assert_eq!(text.matches("--- agent started ").count(), 2, "{text}");
        let before = text.find("Traceback: the first run").unwrap();
        let second_marker = text.rfind("--- agent started ").unwrap();
        assert!(before < second_marker, "{text}");
    }

    #[test]
    fn a_log_past_the_limit_is_moved_aside_and_the_old_one_replaced() {
        let home = tempfile::tempdir().unwrap();
        let path = log_in(home.path());
        let older = path.with_file_name("agent.stderr.log.1");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&older, "two runs ago\n").unwrap();
        std::fs::write(&path, "x".repeat(40)).unwrap();

        // At the limit: kept. One byte past it: rotated.
        drop(open_agent_stderr_log(&path, 40).unwrap());
        assert_eq!(std::fs::read_to_string(&older).unwrap(), "two runs ago\n");
        let before = std::fs::read_to_string(&path).unwrap();
        assert!(before.starts_with(&"x".repeat(40)), "{before}");

        // The file now holds those 40 bytes and a marker, so it is past the limit.
        drop(open_agent_stderr_log(&path, 40).unwrap());
        assert_eq!(std::fs::read_to_string(&older).unwrap(), before);
        let now = std::fs::read_to_string(&path).unwrap();
        assert!(now.starts_with("--- agent started "), "{now}");
        assert!(!now.contains("xxxx"), "{now}");
    }

    #[cfg(unix)]
    #[test]
    fn a_new_log_is_readable_by_its_owner_only() {
        use std::os::unix::fs::PermissionsExt as _;

        let home = tempfile::tempdir().unwrap();
        let path = log_in(home.path());
        drop(open_agent_stderr_log(&path, u64::MAX).unwrap());
        let mode = std::fs::metadata(&path).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600, "{mode:o}");
    }

    #[test]
    fn a_log_that_cannot_be_opened_is_an_error_not_a_panic() {
        let home = tempfile::tempdir().unwrap();
        // `logs` is a file, so the directory cannot be made.
        std::fs::write(home.path().join("logs"), "in the way").unwrap();
        assert!(open_agent_stderr_log(&log_in(home.path()), u64::MAX).is_err());
        // The spawn path turns that into "discard" instead of failing the agent's start.
        let _stdio = agent_stderr(home.path());
    }
}
