//! Agent spawning — creates the agent process and ACP channels.
//!
//! Simplified to only support GrokShell (in-process) mode.
//! Subprocess and remote modes can be added later if needed.

use std::io::IsTerminal;
use std::rc::Rc;
use std::thread;
use std::time::Duration;

use anyhow::Result;
use tokio_util::sync::CancellationToken;
use pi_telemetry::startup::{self, StartupPhase};

use pi_acp_lib::{
    AcpAgentChannel, AcpClientChannel, AcpClientTx, AcpGatewayReceiver, AcpGatewaySender,
    acp_channels,
};
use pi_shell::{
    agent::{MvpAgent, activity::SESSION_FLUSH_GRACE, config::Config as AgentConfig},
    auth::AuthManager,
    util::grok_home::grok_home,
};

/// Extra slack when joining the agent OS thread after cancel so the flush
/// can finish and the thread can unwind.
const AGENT_JOIN_SLACK: Duration = Duration::from_secs(2);

/// How long the join stays silent before telling an interactive user why exit
/// is taking a moment. Short joins (the common case) print nothing.
const JOIN_NOTICE_AFTER: Duration = Duration::from_millis(1500);

/// Stderr notice after a slow join. Covers the whole SessionEnd pipeline
/// (hooks, telemetry sync, upload drain, memory, optional dream) — not
/// hooks alone, so the copy is intentionally not "session hooks".
const JOIN_NOTICE: &str = "Finishing session…";

/// Result of spawning a child agent.
pub struct SpawnedAgent {
    /// Agent worker OS thread. Hand to [`AgentShutdownGuard`] so the worker is
    /// cancelled and joined — letting session actors finish SessionEnd teardown
    /// (hooks, telemetry, uploads, memory) — on every exit path.
    pub thread_handle: thread::JoinHandle<Result<()>>,
    pub channel: AcpClientChannel,
    pub cancel: CancellationToken,
    /// The agent's `AuthManager`, shared so pager-side consumers (e.g. the voice
    /// channel) resolve the same refreshing bearer as chat traffic.
    pub auth_manager: std::sync::Arc<AuthManager>,
}

/// The single teardown mechanism for an in-process agent: cancels the worker
/// and joins it on drop, so session actors always get
/// `SessionCommand::Shutdown` (SessionEnd hooks, telemetry drain, memory)
/// before the process exits — on normal return, `?` bail, or panic unwind alike.
///
/// Hold one from every site that calls [`spawn_grok_shell`] (headless, the TUI,
/// `models`, `worktree`, `share`). Scope-end drop is the default; the TUI is the
/// one caller that drops it explicitly, because the join has to happen before
/// background processes are reaped (see `app::run`).
pub struct AgentShutdownGuard {
    cancel: CancellationToken,
    thread: Option<thread::JoinHandle<Result<()>>>,
}

impl AgentShutdownGuard {
    /// Guard an in-process agent worker. A `None` thread makes the guard a
    /// no-op cancel (leader mode has no in-process worker to join).
    pub fn new(cancel: CancellationToken, thread: Option<thread::JoinHandle<Result<()>>>) -> Self {
        Self { cancel, thread }
    }
}

impl Drop for AgentShutdownGuard {
    fn drop(&mut self) {
        self.cancel.cancel();
        let Some(handle) = self.thread.take() else {
            return;
        };
        let timeout = SESSION_FLUSH_GRACE + AGENT_JOIN_SLACK;
        match join_agent_thread(handle, timeout) {
            JoinOutcome::Joined => {}
            JoinOutcome::Failed(error) => {
                tracing::warn!(%error, "agent worker exited with error after cancel");
            }
            JoinOutcome::Panicked(panic) => {
                tracing::warn!(%panic, "agent worker panicked after cancel");
            }
            JoinOutcome::TimedOut => {
                tracing::warn!(
                    timeout_ms = timeout.as_millis() as u64,
                    "agent worker did not exit within grace after cancel; \
                     SessionEnd teardown (hooks/telemetry/uploads) may be incomplete"
                );
            }
            JoinOutcome::HelperLost => {
                tracing::warn!("agent worker join helper disappeared; proceeding");
            }
        }
    }
}

/// Why the join ended, so each case is explicit at the call site (and callers
/// can tell a completed flush from an abandoned one).
#[derive(Debug, PartialEq, Eq)]
enum JoinOutcome {
    /// Worker returned cleanly: session actors flushed within the grace.
    Joined,
    /// Worker returned an error; the flush may be incomplete.
    Failed(String),
    /// Worker panicked, with the payload rendered as text.
    Panicked(String),
    /// Worker was still running when the budget elapsed.
    TimedOut,
    /// The join helper vanished without reporting (helper thread itself died).
    HelperLost,
}

/// Wait up to `timeout` for a cancelled agent worker to exit.
///
/// The blocking `join` runs on a helper thread so this stays callable from
/// `Drop` — which cannot await — while every caller sits on the async runtime.
/// On timeout that helper is abandoned rather than joined; this is safe **only
/// because every caller is on its way out of the process**, so the OS reaps the
/// thread at exit. Do not reuse this outside teardown.
fn join_agent_thread(handle: thread::JoinHandle<Result<()>>, timeout: Duration) -> JoinOutcome {
    use std::sync::mpsc::RecvTimeoutError;

    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let _ = tx.send(handle.join());
    });

    // Two-phase wait: silent for a short join (overwhelmingly the common case),
    // then a one-line notice so a slow SessionEnd pipeline does not look like a
    // frozen exit. Only for a terminal — piped/JSON consumers stay clean.
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

/// Spawn the standard-ACP Python agent (`python -m pi_agent_cli` by default)
/// and bridge its stdio JSON-RPC into an [`AcpClientChannel`].
///
/// In-process grok-shell is no longer the interactive runtime. `AuthManager`
/// is still constructed for pager fields that expect it, but pi login is
/// not performed — LLM credentials live in the Python process environment.
pub async fn spawn_grok_shell(
    agent_config: AgentConfig,
    cancel: &CancellationToken,
    _memory_config: Option<pi_shell::config::MemoryConfig>,
) -> Result<SpawnedAgent> {
    let auth_manager = std::sync::Arc::new(AuthManager::new(
        &grok_home(),
        agent_config.grok_com_config.clone(),
    ));

    let agent_cancel = cancel.child_token();
    let (acp_client, acp_agent) = acp_channels();

    startup::enter(StartupPhase::WorkerSpawn);
    let handle = spawn_python_stdio_bridge(acp_agent, agent_cancel.clone()).await?;

    Ok(SpawnedAgent {
        thread_handle: handle,
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
        let (prog, args) = parse_agent_command(
            r#""C:\Program Files\Python312\python.exe" -m pi_agent_cli"#,
        )
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
                    .stderr(std::process::Stdio::inherit())
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
                    loop {
                        tokio::select! {
                            biased;
                            _ = cancel_r.cancelled() => break,
                            line = lines.next_line() => {
                                match line {
                                    Ok(Some(json_line)) => {
                                        if incoming_write.write_all(json_line.as_bytes()).await.is_err()
                                            || incoming_write.write_all(b"\n").await.is_err()
                                        {
                                            break;
                                        }
                                    }
                                    Ok(None) | Err(_) => break,
                                }
                            }
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
                let _ = child.start_kill();
                reader_task.abort();
                writer_task.abort();
                let _ = child.wait().await;
                Ok(())
            })
        })?)
}

/// Spawn an in-process grok-shell agent (unused; kept for reference during the
/// fork). Interactive TUI now uses [`spawn_grok_shell`] → Python stdio.
#[allow(dead_code)]
async fn spawn_agent_thread_direct(
    spawn_agent: Box<dyn FnOnce(AcpClientTx) -> Result<Rc<MvpAgent>> + Send + 'static>,
    channel: AcpAgentChannel,
    cancel: CancellationToken,
    skills_paths: Vec<String>,
) -> Result<thread::JoinHandle<Result<()>>> {
    // Off the UI worker: failure must fail spawn, not start ACP.
    let rt = tokio::task::spawn_blocking(|| {
        let mut builder = tokio::runtime::Builder::new_current_thread();
        pi_tty_utils::runtime::build_with_blocking_pool(builder.enable_all())
    })
    .await
    .map_err(|e| anyhow::anyhow!("agent runtime worker join: {e}"))?
    .map_err(|e| {
        tracing::error!(error = %e, "failed to start agent runtime");
        anyhow::anyhow!("failed to start agent runtime: {e}")
    })?;
    Ok(thread::Builder::new()
        .name("acp-agent-worker".into())
        .spawn(move || -> Result<()> {
            let local = tokio::task::LocalSet::new();
            local.block_on(&rt, async move {
                let client_tx = channel.tx.clone();
                let agent_rc = spawn_agent(client_tx)?;

                // Direct dispatch: RPC requests go straight to the agent
                let gw_rx =
                    AcpGatewayReceiver::new(channel.rx, agent_rc.clone()).with_tracing(true);
                tokio::task::spawn_local(gw_rx.run());

                let _skills_watcher = {
                    let cwd = std::env::current_dir().unwrap_or_default();
                    let workspace_user_dir =
                        pi_agent::prompt::workspace_user::optional_workspace_user_dir();
                    pi_shell::config::watcher::SkillsFileWatcher::start(
                        Some(cwd.as_path()),
                        workspace_user_dir.as_deref(),
                        &skills_paths,
                    )
                    .map(|(mut watcher, mut skills_rx)| {
                        let agent = agent_rc.clone();
                        tokio::task::spawn_local(async move {
                            while let Some(change) = skills_rx.recv().await {
                                let created_discovery_dir = watcher.refresh_new_discovery_dirs();
                                match change {
                                    pi_shell::config::watcher::DiscoveryChange::Skills => {
                                        tracing::info!(
                                            "skill directory changed on disk; reloading skills for all sessions"
                                        );
                                        agent.reload_skills_all_sessions();
                                        if created_discovery_dir {
                                            agent.advertise_commands_all_sessions();
                                        }
                                    }
                                    pi_shell::config::watcher::DiscoveryChange::Workflows => {
                                        tracing::info!(
                                            "workflow directory changed on disk; re-advertising commands for all sessions"
                                        );
                                        agent.advertise_commands_all_sessions();
                                    }
                                }
                            }
                        })
                    })
                };
                tokio::task::yield_now().await;

                // Keep running until cancelled, then flush every live session
                // actor (SessionEnd hooks + memory save) before the LocalSet /
                // agent drop. Session actors live on dedicated OS threads and
                // only exit cleanly on SessionCommand::Shutdown; without this
                // flush, /exit and headless quit race process death and skip
                // SessionEnd. Mirrors leader auto-update / relaunch.
                cancel.cancelled().await;
                agent_rc.flush_all_sessions(SESSION_FLUSH_GRACE).await;
                pi_telemetry::session_ctx::drain_at_process_exit().await;
                anyhow::Result::Ok(())
            })
        })?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_reports_clean_worker_exit() {
        let handle = thread::spawn(|| Ok(()));
        assert_eq!(
            join_agent_thread(handle, Duration::from_secs(5)),
            JoinOutcome::Joined
        );
    }

    #[test]
    fn join_reports_worker_error() {
        let handle = thread::spawn(|| Err(anyhow::anyhow!("flush failed")));
        assert_eq!(
            join_agent_thread(handle, Duration::from_secs(5)),
            JoinOutcome::Failed("flush failed".to_string())
        );
    }

    /// The timeout branch the built-binary e2e cannot reach: a wedged worker
    /// (e.g. a hung SessionEnd hook) is abandoned once the budget elapses
    /// instead of holding the process open indefinitely.
    #[test]
    fn join_abandons_wedged_worker_at_budget() {
        let handle = thread::spawn(|| {
            thread::sleep(Duration::from_secs(30));
            Ok(())
        });
        let started = std::time::Instant::now();
        assert_eq!(
            join_agent_thread(handle, Duration::from_millis(50)),
            JoinOutcome::TimedOut
        );
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "join must return at its budget, not wait out the worker"
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
