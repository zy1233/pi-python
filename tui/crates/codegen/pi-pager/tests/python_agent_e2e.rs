//! The pager's client path against the real Python agent, over real pipes and without a PTY.
//!
//! `pi_pager::acp::connect` is what `zypi` calls at startup: it resolves `PI_AGENT_COMMAND`, spawns
//! the agent, bridges its stdio into channels and runs `initialize`. These tests use that connection
//! the way the event loop does (requests go through `acp_send`; notifications and permission
//! questions come out of `connection.rx`) against `python -m pi_agent_cli` with the mock LLM, so a
//! schema drift between the Rust crates (agent-client-protocol 0.10, schema 0.11) and the Python
//! SDK (0.12, schema v1.19) shows up here and not in a user's terminal. The Python side asserts the
//! same things from its own client in `packages/pi-agent-cli/tests/test_acp_stdio_*.py`.
//! Plan: docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md, item 0.3.
//!
//! The tests that need the agent need a Python with `pi_agent_cli` installed
//! (`python -m pip install -e . -e packages/pi-agent-harness -e packages/pi-agent-cli`); name its
//! interpreter in `PI_E2E_PYTHON`. Without it they print that they were skipped and pass, so a
//! plain `cargo test` keeps working; with `PI_E2E_REQUIRED` set (TUI CI does) a missing interpreter
//! fails them instead.
//!
//! One `PI_HOME` serves the whole binary (`grok_home()` reads the variable once), and the agent is
//! chosen through `PI_AGENT_COMMAND`, so the tests run one at a time.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use agent_client_protocol as acp;
use pi_acp_lib::{AcpAgentTx, AcpClientMessage, AcpClientRx, acp_send};
use pi_pager::acp::{ConnectFlags, connect};
use tokio_util::sync::CancellationToken;

const WIRE_TIMEOUT: Duration = Duration::from_secs(60);
const MOCK_REPLY: &str = "Hello from mock";

// ---- environment -------------------------------------------------------------------------------

struct Env {
    home: PathBuf,
    mark: String,
}

static ENV: OnceLock<Env> = OnceLock::new();

/// Set up the process environment once, before anything reads it: a throw-away `PI_HOME`, the mock
/// LLM, and a unique marker that every process the agent starts inherits, so that "nothing is left
/// behind" can be checked without knowing a single pid.
fn env() -> &'static Env {
    ENV.get_or_init(|| {
        let mark = format!(
            "{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos())
        );
        let home = std::env::temp_dir().join(format!("pi-python-agent-e2e-{mark}"));
        std::fs::create_dir_all(&home).expect("temporary PI_HOME");
        // A static is never dropped, so a `TempDir` would be left behind: remove it at exit.
        #[cfg(unix)]
        // SAFETY: `remove_home` only touches the file system.
        unsafe {
            libc::atexit(remove_home);
        }
        // SAFETY: runs inside the serial tests' first call, before any of them starts a thread of
        // its own that reads the environment.
        unsafe {
            std::env::set_var("PI_HOME", &home);
            std::env::set_var("PI_USE_MOCK", "1");
            std::env::set_var("PI_E2E_MARK", &mark);
            for stale in ["PI_PYTHON", "GROK_HOME", "GROK_SANDBOX"] {
                std::env::remove_var(stale);
            }
        }
        Env { home, mark }
    })
}

#[cfg(unix)]
extern "C" fn remove_home() {
    if let Some(env) = ENV.get() {
        let _ = std::fs::remove_dir_all(&env.home);
    }
}

/// The interpreter named by `PI_E2E_PYTHON`, or `None` (and a note) when the test should be skipped.
fn python() -> Option<String> {
    match std::env::var("PI_E2E_PYTHON") {
        Ok(python) if !python.trim().is_empty() => Some(python),
        _ => {
            assert!(
                std::env::var_os("PI_E2E_REQUIRED").is_none(),
                "PI_E2E_REQUIRED is set, but PI_E2E_PYTHON is not: name a Python that has \
                 pi_agent_cli installed"
            );
            eprintln!("skipped: set PI_E2E_PYTHON to a Python with pi_agent_cli installed");
            None
        }
    }
}

fn quoted(path: impl AsRef<str>) -> String {
    format!("\"{}\"", path.as_ref())
}

/// `python -m pi_agent_cli` with the mock LLM (the same agent `test_acp_stdio_contract.py` drives).
fn use_cli_agent(python: &str) {
    env();
    // SAFETY: see `env`; the tests are serial.
    unsafe {
        std::env::set_var(
            "PI_AGENT_COMMAND",
            format!("{} -m pi_agent_cli", quoted(python)),
        );
    }
}

/// The scripted agent of the tool tests (`tests/_tool_agent.py`): the real stdio loop with an LLM
/// whose first turn calls one tool. `call` is `{"name": ..., "arguments": {...}}`; without it the
/// call is a `bash` command that records its pid in `pidfile` and then sleeps.
fn use_tool_agent(python: &str, permission: &str, call: Option<serde_json::Value>, pidfile: &Path) {
    let script = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../../packages/pi-agent-cli/tests/_tool_agent.py");
    assert!(script.is_file(), "missing {}", script.display());
    env();
    // SAFETY: see `env`; the tests are serial.
    unsafe {
        std::env::set_var(
            "PI_AGENT_COMMAND",
            format!("{} {}", quoted(python), quoted(script.to_string_lossy())),
        );
        std::env::set_var("PI_TEST_PERMISSION", permission);
        std::env::set_var("PI_TEST_TOOL_PIDFILE", pidfile);
        match call {
            Some(call) => std::env::set_var("PI_TEST_TOOL_CALL", call.to_string()),
            None => std::env::remove_var("PI_TEST_TOOL_CALL"),
        }
        std::env::remove_var("PI_TEST_STUCK_THREAD");
    }
}

/// A process that carries this run's marker in its environment: the agent or something it started.
#[derive(Debug, PartialEq, Eq)]
struct Marked {
    pid: u32,
    cmdline: String,
}

#[cfg(target_os = "linux")]
fn marked_processes() -> Vec<Marked> {
    let needle = format!("PI_E2E_MARK={}", env().mark);
    let mut found = Vec::new();
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return found;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<u32>().ok())
        else {
            continue;
        };
        let Ok(environ) = std::fs::read(entry.path().join("environ")) else {
            continue;
        };
        if environ
            .split(|b| *b == 0)
            .any(|var| var == needle.as_bytes())
        {
            let cmdline = std::fs::read(entry.path().join("cmdline")).unwrap_or_default();
            found.push(Marked {
                pid,
                cmdline: String::from_utf8_lossy(&cmdline).replace('\0', " "),
            });
        }
    }
    found
}

#[cfg(not(target_os = "linux"))]
fn marked_processes() -> Vec<Marked> {
    Vec::new()
}

// ---- the client side ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
enum Answer {
    AllowOnce,
    RejectOnce,
    Cancelled,
}

fn pick(options: &[acp::PermissionOption], answer: Answer) -> acp::RequestPermissionOutcome {
    let wanted = match answer {
        Answer::AllowOnce => acp::PermissionOptionKind::AllowOnce,
        Answer::RejectOnce => acp::PermissionOptionKind::RejectOnce,
        Answer::Cancelled => return acp::RequestPermissionOutcome::Cancelled,
    };
    options
        .iter()
        .find(|option| option.kind == wanted)
        .map(|option| {
            acp::RequestPermissionOutcome::Selected(acp::SelectedPermissionOutcome::new(
                option.option_id.clone(),
            ))
        })
        .unwrap_or(acp::RequestPermissionOutcome::Cancelled)
}

/// What the agent sent while the test ran.
#[derive(Default)]
struct Traffic {
    updates: Mutex<Vec<acp::SessionNotification>>,
    questions: Mutex<Vec<acp::RequestPermissionRequest>>,
    unexpected: Mutex<Vec<String>>,
}

impl Traffic {
    /// The text of the agent's (or, with `user`, the user's) message chunks for one session.
    fn text(&self, session: &acp::SessionId, user: bool) -> String {
        let updates = self.updates.lock().unwrap();
        let mut text = String::new();
        for note in updates.iter().filter(|note| &note.session_id == session) {
            let chunk = match &note.update {
                acp::SessionUpdate::AgentMessageChunk(chunk) if !user => chunk,
                acp::SessionUpdate::UserMessageChunk(chunk) if user => chunk,
                _ => continue,
            };
            if let acp::ContentBlock::Text(part) = &chunk.content {
                text.push_str(&part.text);
            }
        }
        text
    }

    /// Wait for a condition on what has arrived. Notifications travel through the pager's channels
    /// while the response travels through its own, so a response can be seen before the last
    /// notification that preceded it on the wire; the event loop copes, and so does this.
    async fn until(&self, what: &str, done: impl Fn(&Traffic) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !done(self) {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }
}

/// A connected agent plus the loop that plays the client's side of the conversation.
struct Live {
    tx: AcpAgentTx,
    cancel: CancellationToken,
    bridge: Option<std::thread::JoinHandle<anyhow::Result<()>>>,
    traffic: Arc<Traffic>,
    answer: Arc<Mutex<Answer>>,
    advertised_auth_methods: usize,
    needs_login: bool,
}

impl Live {
    async fn start(answer: Answer) -> Live {
        let parent = CancellationToken::new();
        let connection =
            tokio::time::timeout(WIRE_TIMEOUT, connect(&parent, ConnectFlags::default()))
                .await
                .expect("connect did not return")
                .unwrap_or_else(|error| panic!("connect failed: {error:#}"));
        let mut connection = connection;
        let traffic = Arc::new(Traffic::default());
        let answer = Arc::new(Mutex::new(answer));
        tokio::spawn(pump(
            std::mem::replace(&mut connection.rx, tokio::sync::mpsc::unbounded_channel().1),
            answer.clone(),
            traffic.clone(),
        ));
        Live {
            tx: connection.tx.clone(),
            cancel: connection.cancel.clone(),
            bridge: connection.bridge_thread.take(),
            traffic,
            answer,
            advertised_auth_methods: connection.auth_methods.len(),
            needs_login: connection.needs_login,
        }
    }

    fn answer_with(&self, answer: Answer) {
        *self.answer.lock().unwrap() = answer;
    }

    /// What `zypi` does on the way out: cancel, and wait for the bridge to stop the agent.
    async fn stop(mut self) {
        assert_eq!(
            *self.traffic.unexpected.lock().unwrap(),
            Vec::<String>::new(),
            "the agent asked the client for something the pager does not offer"
        );
        self.cancel.cancel();
        let bridge = self.bridge.take().expect("the bridge thread");
        let joined = tokio::time::timeout(
            Duration::from_secs(20),
            tokio::task::spawn_blocking(move || bridge.join()),
        )
        .await
        .expect("the bridge did not stop within 20 s")
        .expect("join task");
        match joined {
            Ok(Ok(())) => {}
            Ok(Err(error)) => panic!("the bridge ended with an error: {error:#}"),
            Err(_) => panic!("the bridge panicked"),
        }
        assert_eq!(
            marked_processes(),
            Vec::new(),
            "processes of the agent are still running after the pager stopped it"
        );
    }
}

/// The client's half of the connection: record every update, answer every permission question.
async fn pump(mut rx: AcpClientRx, answer: Arc<Mutex<Answer>>, traffic: Arc<Traffic>) {
    while let Some(message) = rx.recv().await {
        match message {
            AcpClientMessage::SessionNotification(args) => {
                traffic.updates.lock().unwrap().push(args.request.clone());
                let _ = args.response_tx.send(Ok(()));
            }
            AcpClientMessage::RequestPermission(args) => {
                traffic.questions.lock().unwrap().push(args.request.clone());
                let outcome = pick(&args.request.options, *answer.lock().unwrap());
                let _ = args
                    .response_tx
                    .send(Ok(acp::RequestPermissionResponse::new(outcome)));
            }
            // A notification asks for nothing, and a client may ignore the ones it does not know.
            AcpClientMessage::ExtNotification(args) => {
                let _ = args.response_tx.send(Ok(()));
            }
            // What the pager did not offer to serve (files, terminals): the agent must not ask.
            other => traffic
                .unexpected
                .lock()
                .unwrap()
                .push(format!("{other:?}")),
        }
    }
}

// ---- requests ----------------------------------------------------------------------------------

async fn wire<T>(
    what: &str,
    request: impl std::future::Future<Output = Result<T, acp::Error>>,
) -> T {
    tokio::time::timeout(WIRE_TIMEOUT, request)
        .await
        .unwrap_or_else(|_| panic!("{what} timed out"))
        .unwrap_or_else(|error| panic!("{what} failed: {error:?}"))
}

async fn new_session(tx: &AcpAgentTx, cwd: &Path) -> acp::NewSessionResponse {
    wire(
        "session/new",
        acp_send(acp::NewSessionRequest::new(cwd.to_path_buf()), tx),
    )
    .await
}

fn prompt_request(session: &acp::SessionId, text: &str) -> acp::PromptRequest {
    acp::PromptRequest::new(
        session.clone(),
        vec![acp::ContentBlock::Text(acp::TextContent::new(
            text.to_string(),
        ))],
    )
}

async fn prompt(tx: &AcpAgentTx, session: &acp::SessionId, text: &str) -> acp::PromptResponse {
    wire(
        "session/prompt",
        acp_send(prompt_request(session, text), tx),
    )
    .await
}

async fn list(
    tx: &AcpAgentTx,
    cwd: &Path,
    cursor: Option<String>,
) -> Result<acp::ListSessionsResponse, acp::Error> {
    tokio::time::timeout(
        WIRE_TIMEOUT,
        acp_send(
            acp::ListSessionsRequest::default()
                .cwd(cwd.to_path_buf())
                .cursor(cursor),
            tx,
        ),
    )
    .await
    .expect("session/list timed out")
}

// ---- tests -------------------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn connect_runs_the_handshake_and_stop_leaves_nothing_behind() {
    let Some(python) = python() else { return };
    use_cli_agent(&python);

    let live = Live::start(Answer::Cancelled).await;
    assert_eq!(
        live.advertised_auth_methods, 0,
        "the Python agent has no login"
    );
    assert!(!live.needs_login);
    live.stop().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn a_prompt_streams_the_reply_and_ends_the_turn() {
    let Some(python) = python() else { return };
    use_cli_agent(&python);
    let cwd = tempfile::tempdir().unwrap();

    let live = Live::start(Answer::Cancelled).await;
    let session = new_session(&live.tx, cwd.path()).await;

    // The model catalogue the pager builds `/model` from: a `select` option of category `model`.
    let options = serde_json::to_value(&session.config_options).unwrap();
    let model = options
        .as_array()
        .and_then(|all| all.iter().find(|option| option["id"] == "model"))
        .unwrap_or_else(|| panic!("no `model` config option in {options}"));
    assert_eq!(model["category"], "model");
    assert_eq!(model["type"], "select");

    let reply = prompt(&live.tx, &session.session_id, "say hello").await;
    assert_eq!(reply.stop_reason, acp::StopReason::EndTurn);
    live.traffic
        .until("the reply text", |t| {
            t.text(&session.session_id, false) == MOCK_REPLY
        })
        .await;
    live.stop().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn a_session_is_listed_with_a_title_and_replayed_by_a_later_agent() {
    let Some(python) = python() else { return };
    use_cli_agent(&python);
    let cwd = tempfile::tempdir().unwrap();

    let first = Live::start(Answer::Cancelled).await;
    let created = new_session(&first.tx, cwd.path()).await.session_id;
    prompt(&first.tx, &created, "remember  this").await;
    first.stop().await;

    // A new agent process, as after a restart: it only has the session files to go on.
    let second = Live::start(Answer::Cancelled).await;
    let listed = list(&second.tx, cwd.path(), None)
        .await
        .expect("session/list");
    let info = listed
        .sessions
        .iter()
        .find(|info| info.session_id == created)
        .unwrap_or_else(|| panic!("{created:?} is not in {:?}", listed.sessions));
    assert_eq!(info.title.as_deref(), Some("remember this"));
    assert!(info.updated_at.is_some(), "no updatedAt: {info:?}");
    assert!(listed.next_cursor.is_none());

    wire(
        "session/load",
        acp_send(
            acp::LoadSessionRequest::new(created.clone(), cwd.path().to_path_buf()),
            &second.tx,
        ),
    )
    .await;
    second
        .traffic
        .until("the replayed history", |t| {
            t.text(&created, true) == "remember  this" && t.text(&created, false) == MOCK_REPLY
        })
        .await;
    let again = prompt(&second.tx, &created, "and again").await;
    assert_eq!(again.stop_reason, acp::StopReason::EndTurn);
    second.stop().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn session_list_pages_are_followed_by_cursor() {
    let Some(python) = python() else { return };
    use_cli_agent(&python);
    let cwd = tempfile::tempdir().unwrap();

    let live = Live::start(Answer::Cancelled).await;
    // One more than a page (`SESSION_LIST_PAGE_SIZE` is 50 in pi_agent_cli.session_list).
    let mut created = Vec::new();
    for _ in 0..51 {
        created.push(new_session(&live.tx, cwd.path()).await.session_id);
    }

    let first = list(&live.tx, cwd.path(), None).await.expect("first page");
    assert_eq!(first.sessions.len(), 50);
    assert_eq!(
        first.sessions[0].session_id,
        *created.last().unwrap(),
        "newest first"
    );
    let cursor = first
        .next_cursor
        .clone()
        .expect("a cursor after a full page");

    let second = list(&live.tx, cwd.path(), Some(cursor))
        .await
        .expect("second page");
    assert_eq!(second.sessions.len(), 1);
    assert!(second.next_cursor.is_none(), "the last page has no cursor");

    let listed: BTreeSet<String> = first
        .sessions
        .iter()
        .chain(&second.sessions)
        .map(|info| info.session_id.to_string())
        .collect();
    let expected: BTreeSet<String> = created.iter().map(ToString::to_string).collect();
    assert_eq!(listed, expected, "every session once, none twice");

    // A cursor the agent did not issue is the client's mistake, and says so.
    let error = list(&live.tx, cwd.path(), Some("not-a-cursor".to_owned()))
        .await
        .expect_err("a made-up cursor must be refused");
    assert_eq!(error.code, acp::ErrorCode::InvalidParams, "{error:?}");
    live.stop().await;
}

fn write_call(target: &Path) -> serde_json::Value {
    serde_json::json!({
        "id": "call_write",
        "name": "write",
        "arguments": { "path": target, "content": "the tool ran" },
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn a_permission_question_reaches_the_client_and_its_answer_decides() {
    let Some(python) = python() else { return };
    let cwd = tempfile::tempdir().unwrap();
    let target = cwd.path().join("written.txt");
    use_tool_agent(
        &python,
        "ask",
        Some(write_call(&target)),
        &cwd.path().join("tool.pid"),
    );

    // Allowed once: the tool runs, and the turn goes on to the model's answer.
    let live = Live::start(Answer::AllowOnce).await;
    let session = new_session(&live.tx, cwd.path()).await.session_id;
    let reply = prompt(&live.tx, &session, "write it").await;
    assert_eq!(reply.stop_reason, acp::StopReason::EndTurn);
    assert_eq!(std::fs::read_to_string(&target).unwrap(), "the tool ran");
    {
        let questions = live.traffic.questions.lock().unwrap();
        assert_eq!(questions.len(), 1, "one question for one call");
        let question = &questions[0];
        let kinds: Vec<_> = question.options.iter().map(|option| option.kind).collect();
        assert_eq!(
            kinds,
            [
                acp::PermissionOptionKind::AllowOnce,
                acp::PermissionOptionKind::RejectOnce
            ]
        );
        let call = serde_json::to_value(&question.tool_call).unwrap();
        assert_eq!(call["toolCallId"], "call_write", "{call}");
        assert_eq!(call["title"], "write", "{call}");
        assert_eq!(
            call["rawInput"]["path"],
            serde_json::json!(target),
            "{call}"
        );
    }

    // Refused: the tool does not run, and the turn still ends normally (the model is told).
    std::fs::remove_file(&target).unwrap();
    live.answer_with(Answer::RejectOnce);
    let other = new_session(&live.tx, cwd.path()).await.session_id;
    let reply = prompt(&live.tx, &other, "write it").await;
    assert_eq!(reply.stop_reason, acp::StopReason::EndTurn);
    assert!(!target.exists(), "a refused tool call must not run");
    assert_eq!(live.traffic.questions.lock().unwrap().len(), 2);
    live.stop().await;
}

#[cfg(target_os = "linux")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn cancel_ends_a_running_tool_as_cancelled_and_reaps_it() {
    let Some(python) = python() else { return };
    let cwd = tempfile::tempdir().unwrap();
    let pidfile = cwd.path().join("tool.pid");
    use_tool_agent(&python, "auto", None, &pidfile);

    let live = Live::start(Answer::Cancelled).await;
    let session = new_session(&live.tx, cwd.path()).await.session_id;
    let running = tokio::spawn({
        let tx = live.tx.clone();
        let session = session.clone();
        async move { acp_send(prompt_request(&session, "run it"), &tx).await }
    });

    // The tool is a `bash` that records its pid and then sleeps for five minutes.
    let tool_pid = wait_for_pid(&pidfile).await;
    let alive = |pid: u32| Path::new(&format!("/proc/{pid}")).exists();
    assert!(alive(tool_pid));

    wire(
        "session/cancel",
        acp_send(acp::CancelNotification::new(session.clone()), &live.tx),
    )
    .await;
    let reply = tokio::time::timeout(WIRE_TIMEOUT, running)
        .await
        .expect("the cancelled prompt did not return")
        .expect("prompt task")
        .expect("session/prompt");
    assert_eq!(reply.stop_reason, acp::StopReason::Cancelled);

    let deadline = Instant::now() + Duration::from_secs(5);
    while alive(tool_pid) {
        assert!(Instant::now() < deadline, "the tool outlived the cancel");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    live.stop().await;
}

/// `connect` against an agent command that cannot serve: the error it ends with, and how long that
/// took. Needs no Python.
async fn failed_connect(command: &str) -> (String, Duration) {
    env();
    // SAFETY: see `env`; the tests are serial.
    unsafe { std::env::set_var("PI_AGENT_COMMAND", command) };
    let started = Instant::now();
    let parent = CancellationToken::new();
    let outcome = tokio::time::timeout(
        Duration::from_secs(30),
        connect(&parent, ConnectFlags::default()),
    )
    .await
    .unwrap_or_else(|_| panic!("connect hung instead of failing, with `{command}`"));
    parent.cancel();
    match outcome {
        Ok(_) => panic!("connect succeeded with `{command}`"),
        Err(error) => (format!("{error:#}"), started.elapsed()),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn a_missing_agent_is_an_error_that_says_what_to_do() {
    let (error, took) =
        failed_connect("\"/nonexistent/pi-agent-for-the-e2e-test\" -m pi_agent_cli").await;
    // What the user is told is what the bridge knows, not the closed channel `initialize` saw.
    assert!(error.contains("failed to spawn ACP agent"), "{error}");
    assert!(error.contains("pi-agent-for-the-e2e-test"), "{error}");
    assert!(error.contains("PI_AGENT_COMMAND"), "{error}");
    assert!(!error.contains("piAcpChannelFailure"), "{error}");
    assert!(took < Duration::from_secs(10), "{took:?}");
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn an_agent_that_exits_at_once_is_an_error_with_what_it_wrote() {
    // Used to hang: nothing waited on the child, so `initialize` never got an answer or an error.
    let (error, took) = failed_connect(r#"sh -c 'echo "boom from the agent" >&2; exit 3'"#).await;
    assert!(error.contains("exited on its own"), "{error}");
    assert!(error.contains("exit status: 3"), "{error}");
    assert!(error.contains("boom from the agent"), "{error}");
    assert!(error.contains("agent.stderr.log"), "{error}");
    assert!(took < Duration::from_secs(10), "{took:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn a_python_without_the_agent_says_so() {
    // The commonest way to fail on a first run: a Python is found, `pi_agent_cli` is not
    // installed in it. Any interpreter will do for this; the module is made up.
    let Some(python) = python() else { return };
    let (error, took) = failed_connect(&format!(
        "{} -m pi_agent_cli_that_is_not_installed",
        quoted(&python)
    ))
    .await;
    assert!(
        error.contains("No module named pi_agent_cli_that_is_not_installed"),
        "{error}"
    );
    assert!(error.contains("exited on its own"), "{error}");
    assert!(took < Duration::from_secs(20), "{took:?}");
}

/// What the pager's event loop relies on when the agent dies in the middle of a turn: the stdio
/// bridge fires the connection's cancel token ("agent disconnected"), the requests still waiting
/// end with an error, and the bridge's own result says what happened. Used to leave the loop
/// waiting for ever.
#[cfg(target_os = "linux")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(pi_agent)]
async fn an_agent_that_dies_mid_turn_ends_the_requests_and_fires_cancel() {
    let Some(python) = python() else { return };
    let cwd = tempfile::tempdir().unwrap();
    let pidfile = cwd.path().join("tool.pid");
    use_tool_agent(&python, "auto", None, &pidfile);

    let mut live = Live::start(Answer::Cancelled).await;
    let session = new_session(&live.tx, cwd.path()).await.session_id;
    let running = tokio::spawn({
        let tx = live.tx.clone();
        async move { acp_send(prompt_request(&session, "run it"), &tx).await }
    });
    let tool_pid = wait_for_pid(&pidfile).await;

    // SIGKILL: the agent cannot clean up after itself, so its bash tool outlives it (that is
    // why the pager closes stdin first and kills last; see `AGENT_EOF_GRACE`).
    let agent = marked_processes()
        .into_iter()
        .find(|process| process.cmdline.contains("_tool_agent.py"))
        .expect("the scripted agent is running");
    kill(agent.pid);

    let error = tokio::time::timeout(Duration::from_secs(15), running)
        .await
        .expect("the prompt still waits for an agent that is gone")
        .expect("prompt task")
        .expect_err("a prompt cannot succeed without its agent");
    eprintln!("the pending prompt ended with: {error:?}");
    tokio::time::timeout(Duration::from_secs(5), live.cancel.cancelled())
        .await
        .expect("the bridge did not fire cancel when the agent died");

    let bridge = live.bridge.take().expect("the bridge thread");
    let ended = tokio::task::spawn_blocking(move || bridge.join())
        .await
        .expect("join task");
    let reason = match ended {
        Ok(Err(error)) => format!("{error:#}"),
        other => panic!("the bridge should end with the reason, got {other:?}"),
    };
    assert!(reason.contains("exited on its own"), "{reason}");
    assert!(reason.contains("signal: 9"), "{reason}");

    // The orphaned tool is this test's to clean up.
    kill(tool_pid);
    let deadline = Instant::now() + Duration::from_secs(5);
    while !marked_processes().is_empty() {
        assert!(
            Instant::now() < deadline,
            "left behind: {:?}",
            marked_processes()
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

#[cfg(target_os = "linux")]
fn kill(pid: u32) {
    let status = std::process::Command::new("kill")
        .args(["-9", &pid.to_string()])
        .status()
        .expect("run kill");
    assert!(status.success(), "kill -9 {pid}");
}

/// The pid the scripted agent's `bash` tool writes into `pidfile` once it runs.
#[cfg(target_os = "linux")]
async fn wait_for_pid(pidfile: &Path) -> u32 {
    let deadline = Instant::now() + WIRE_TIMEOUT;
    loop {
        if let Some(pid) = std::fs::read_to_string(pidfile)
            .ok()
            .and_then(|text| text.trim().parse::<u32>().ok())
        {
            return pid;
        }
        assert!(Instant::now() < deadline, "the tool never started");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
