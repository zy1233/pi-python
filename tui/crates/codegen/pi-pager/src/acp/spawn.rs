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

/// When the agent has gone away on its own, how long the bridge lets the reader finish with what
/// the agent wrote before it went (the end of its output is what ends the reader).
const EXIT_OUTPUT_DRAIN: Duration = Duration::from_secs(2);

/// Then how long that last output gets to be handled by the connection (an answer written just
/// before the exit is still an answer) before the bridge ends the requests that wait for more.
const EXIT_OUTPUT_SETTLE: Duration = Duration::from_millis(100);

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
                // The bridge ends with an error only when the agent went away on its own, and
                // by now the screen it went away on is gone: say it where the user reads it.
                eprintln!("Error: {error}");
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

/// `initialize` got no answer: say why, as far as the bridge knows.
///
/// A bridge that could not start the agent, or whose agent went away, ends with the reason (see
/// [`spawn_python_stdio_bridge`]); only whoever joins the thread reads it, and what `initialize`
/// itself saw is a closed channel. Cancelling first also stops an agent that is up but did not
/// answer. For the way out of a failed start: the join gives up on a stuck bridge after the same
/// budget as the exit does.
pub(crate) async fn explain_failed_start(
    error: anyhow::Error,
    cancel: CancellationToken,
    bridge: thread::JoinHandle<Result<()>>,
) -> anyhow::Error {
    cancel.cancel();
    let timeout = AGENT_EXIT_GRACE + BRIDGE_JOIN_SLACK;
    match tokio::task::spawn_blocking(move || join_bridge_thread(bridge, timeout)).await {
        Ok(JoinOutcome::Failed(reason)) => {
            tracing::debug!(%error, "initialize failed; the bridge says why");
            anyhow::anyhow!(reason)
        }
        _ => error.context(format!(
            "the agent did not answer `initialize` (what it wrote to stderr is in {})",
            agent_stderr_log_path(&grok_home()).display()
        )),
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

/// What the line that opens a run in the agent's stderr log begins with.
const AGENT_STARTED_MARKER: &str = "--- agent started ";

/// How much of the agent's stderr an error message carries: the last lines, and no more than this
/// many bytes of them.
const STDERR_TAIL_LINES: usize = 12;
const STDERR_TAIL_BYTES: usize = 4 * 1024;

/// The last lines the agent wrote to stderr in its latest run (what follows the newest
/// `--- agent started` line in the log), for an error that has to say why the agent is gone: a
/// Python that cannot find `pi_agent_cli` says so there, and nowhere else. `None` when it wrote
/// nothing, or the log cannot be read.
fn agent_stderr_tail(path: &std::path::Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    let text = String::from_utf8_lossy(&bytes);
    let run: &str = match text.rfind(AGENT_STARTED_MARKER) {
        Some(at) => text[at..]
            .split_once('\n')
            .map_or("", |(_marker, rest)| rest),
        None => &text,
    };
    let lines: Vec<&str> = run.lines().filter(|line| !line.trim().is_empty()).collect();
    let mut tail = lines[lines.len().saturating_sub(STDERR_TAIL_LINES)..].join("\n");
    if tail.is_empty() {
        return None;
    }
    if tail.len() > STDERR_TAIL_BYTES {
        let mut cut = tail.len() - STDERR_TAIL_BYTES;
        while !tail.is_char_boundary(cut) {
            cut += 1;
        }
        tail = format!("…{}", &tail[cut..]);
    }
    Some(tail)
}

/// The error for an agent that exited without being asked to: how it ended, and what it said.
fn agent_exit_message(
    program: &std::ffi::OsStr,
    status: &std::io::Result<std::process::ExitStatus>,
    log: &std::path::Path,
) -> String {
    use std::fmt::Write as _;

    let how = match status {
        Ok(status) => status.to_string(),
        Err(error) => format!("waiting for it failed: {error}"),
    };
    let mut message = format!(
        "the agent ({}) exited on its own ({how}).",
        program.to_string_lossy()
    );
    match agent_stderr_tail(log) {
        Some(tail) => {
            let _ = write!(
                message,
                "\nLast of what it wrote to stderr (all of it is in {}):",
                log.display()
            );
            for line in tail.lines() {
                let _ = write!(message, "\n  {line}");
            }
        }
        None => {
            let _ = write!(
                message,
                "\nIt wrote nothing to stderr that could be read ({}).",
                log.display()
            );
        }
    }
    message
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
        "{AGENT_STARTED_MARKER}{} (zypi pid {}) ---",
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
                let mut reader_task = tokio::task::spawn_local(async move {
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

                // Two ways to get past this point: the pager asks for the stop (cancel), or
                // the agent goes away on its own (a crash, a `kill`, a Python that cannot import
                // `pi_agent_cli`). The second used to go unnoticed — nothing waited on the child
                // — so the pager sat on requests that could not be answered, and the event
                // loop's "agent disconnected" arm (it quits on `connection.cancel`) never fired.
                let exited_on_its_own = tokio::select! {
                    biased;
                    () = cancel.cancelled() => None,
                    status = child.wait() => Some(status),
                };
                if let Some(status) = exited_on_its_own {
                    // Let what the agent wrote last be read, and handled, before the requests
                    // still waiting are ended: dropping this thread's tasks is what answers them
                    // with an error (their senders are in those tasks).
                    let _ = tokio::time::timeout(EXIT_OUTPUT_DRAIN, &mut reader_task).await;
                    tokio::time::sleep(EXIT_OUTPUT_SETTLE).await;
                    let message =
                        agent_exit_message(&program, &status, &agent_stderr_log_path(&home));
                    tracing::warn!(error = %message, "agent process exited on its own");
                    cancel.cancel();
                    writer_task.abort();
                    reader_task.abort();
                    return Err(anyhow::anyhow!(message));
                }

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

#[cfg(test)]
mod agent_exit_tests {
    use super::*;

    /// A log as the spawn path leaves it: one marker line per run, then what that run wrote.
    fn log_with(runs: &[&str]) -> (tempfile::TempDir, std::path::PathBuf) {
        let home = tempfile::tempdir().unwrap();
        let path = agent_stderr_log_path(home.path());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut text = String::new();
        for (n, output) in runs.iter().enumerate() {
            text.push_str(&format!("{AGENT_STARTED_MARKER}run {n} (zypi pid 1) ---\n"));
            text.push_str(output);
        }
        std::fs::write(&path, text).unwrap();
        (home, path)
    }

    #[test]
    fn the_tail_is_what_the_latest_run_wrote() {
        let (_home, path) = log_with(&["Traceback: the old run\n", "ModuleNotFoundError: x\n"]);
        assert_eq!(
            agent_stderr_tail(&path).as_deref(),
            Some("ModuleNotFoundError: x")
        );
    }

    #[test]
    fn the_tail_keeps_the_last_lines_and_skips_blank_ones() {
        let output: String = (1..=30).map(|n| format!("line {n}\n\n")).collect();
        let (_home, path) = log_with(&[&output]);
        let tail = agent_stderr_tail(&path).unwrap();
        let lines: Vec<&str> = tail.lines().collect();
        assert_eq!(lines.len(), STDERR_TAIL_LINES, "{tail}");
        assert_eq!(lines.first(), Some(&"line 19"), "{tail}");
        assert_eq!(lines.last(), Some(&"line 30"), "{tail}");
    }

    #[test]
    fn a_long_tail_is_cut_at_the_front_on_a_character_boundary() {
        // Three-byte characters: 6000 bytes, so the cut at 6000 - 4096 = 1904 falls inside one.
        let output = format!("{}\n", "€".repeat(2000));
        assert_eq!((2000 * '€'.len_utf8()) - STDERR_TAIL_BYTES, 1904);
        assert_ne!(1904 % '€'.len_utf8(), 0);
        let (_home, path) = log_with(&[&output]);
        let tail = agent_stderr_tail(&path).unwrap();
        assert!(tail.starts_with('…'), "{tail:.20}");
        assert!(
            tail.len() <= STDERR_TAIL_BYTES + '…'.len_utf8(),
            "{}",
            tail.len()
        );
        assert!(tail.trim_start_matches('…').chars().all(|c| c == '€'));
    }

    #[test]
    fn there_is_no_tail_without_output() {
        let (_home, path) = log_with(&["", "  \n\n"]);
        assert_eq!(
            agent_stderr_tail(&path),
            None,
            "marker and blank lines only"
        );
        let (_home, path) = log_with(&[]);
        assert_eq!(agent_stderr_tail(&path), None, "empty file");
        let missing = path.with_file_name("not-there.log");
        assert_eq!(agent_stderr_tail(&missing), None, "no file");
    }

    #[test]
    fn a_log_without_a_marker_is_read_whole() {
        let home = tempfile::tempdir().unwrap();
        let path = home.path().join("plain.log");
        std::fs::write(&path, "first\nsecond\n").unwrap();
        assert_eq!(agent_stderr_tail(&path).as_deref(), Some("first\nsecond"));
    }

    #[test]
    fn the_message_says_which_agent_how_it_ended_and_what_it_wrote() {
        let (_home, path) =
            log_with(&["Traceback (most recent call last):\nModuleNotFoundError: pi_agent_cli\n"]);
        let status = Err(std::io::Error::other("no such child"));
        let message = agent_exit_message(std::ffi::OsStr::new("python3"), &status, &path);
        assert!(
            message.starts_with(
                "the agent (python3) exited on its own (waiting for it failed: no such child)."
            ),
            "{message}"
        );
        assert!(
            message.contains(
                "\n  Traceback (most recent call last):\n  ModuleNotFoundError: pi_agent_cli"
            ),
            "{message}"
        );
        assert!(message.contains(&path.display().to_string()), "{message}");
    }

    #[test]
    fn the_message_points_at_the_log_when_there_is_nothing_in_it() {
        let (_home, path) = log_with(&[""]);
        let status = Err(std::io::Error::other("gone"));
        let message = agent_exit_message(std::ffi::OsStr::new("agent"), &status, &path);
        assert!(message.contains("wrote nothing to stderr"), "{message}");
        assert!(message.contains(&path.display().to_string()), "{message}");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn the_message_carries_the_exit_status() {
        let mut child = tokio::process::Command::new("sh")
            .args(["-c", "exit 3"])
            .spawn()
            .expect("spawn sh");
        let status = child.wait().await;
        let (_home, path) = log_with(&["boom\n"]);
        let message = agent_exit_message(std::ffi::OsStr::new("sh"), &status, &path);
        assert!(message.contains("(exit status: 3)"), "{message}");
        assert!(message.contains("\n  boom"), "{message}");
    }
}
