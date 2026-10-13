//! Getting the replay of one session out of the agent.
//!
//! `session/load` makes the agent send the history of the session as `session/update`
//! notifications, and then answer the request. The agent says more than the history while that
//! happens (the list of its commands, a question about trusting the project). This module keeps
//! the history and refuses the rest, because nobody is here to answer it.

use std::future::Future;
use std::time::Duration;

use agent_client_protocol as acp;
use anyhow::{Context, Result, anyhow, bail};
use pi_acp_lib::{
    AcpArgs, AcpClientMessage, AcpClientRx, AcpRequest, AcpResult, acp_channel_failure, acp_send,
};
use tokio::time::{Instant, sleep_until};
use tokio_util::sync::CancellationToken;

use crate::acp::spawn::AgentProcessGuard;
use crate::acp::{ConnectFlags, connect};

/// How long the agent has to start and to finish its `initialize`.
const START_TIMEOUT: Duration = Duration::from_secs(60);

/// The two waits of [`collect_replay`].
#[derive(Clone, Copy, Debug)]
pub(super) struct Waits {
    /// How long the agent may say nothing before it has answered `session/load`.
    pub(super) silence: Duration,
    /// How long the replay may stay quiet, after the answer, before it counts as complete.
    pub(super) settle: Duration,
}

const WAITS: Waits = Waits {
    silence: Duration::from_secs(60),
    settle: Duration::from_millis(500),
};

/// The updates that the agent replays for `session_id`, in the order it sent them.
///
/// The agent runs for as long as this takes. It is stopped before the function returns, on
/// every path.
pub(super) async fn session_updates(session_id: &str) -> Result<Vec<acp::SessionUpdate>> {
    let cancel = CancellationToken::new();
    let mut connection = match tokio::time::timeout(
        START_TIMEOUT,
        connect(&cancel, ConnectFlags::default()),
    )
    .await
    {
        Ok(connection) => connection?,
        Err(_) => {
            cancel.cancel();
            bail!(
                "The agent did not start within {} s.",
                START_TIMEOUT.as_secs()
            );
        }
    };
    let _agent = AgentProcessGuard::new(cancel.clone(), connection.bridge_thread.take());

    let session = acp::SessionId::new(session_id);
    // The agent looks the session up by its id and uses the directory that the session has on
    // record; the one sent here is only a fallback.
    let cwd = std::env::current_dir().context("Failed to read the current directory")?;
    let load = acp_send(
        acp::LoadSessionRequest::new(session.clone(), cwd),
        &connection.tx,
    );
    collect_replay(&mut connection.rx, &session, load, WAITS).await
}

/// Read what the agent sends while it answers `load` (a `session/load` request), until the
/// replay is complete.
///
/// The result holds the updates of `session` that carry the replay flag, in the order they came.
///
/// The answer to `load` does not end the replay at once. The protocol library gives a response
/// to its caller as soon as it has read it, but it gives each notification to `rx` from a task of
/// its own, so the last updates can reach `rx` after the answer. A quiet time after the answer
/// ends the wait instead. Only replayed updates restart that time: what else the agent says
/// after the load does not belong to the history and does not delay the end.
pub(super) async fn collect_replay(
    rx: &mut AcpClientRx,
    session: &acp::SessionId,
    load: impl Future<Output = AcpResult<acp::LoadSessionResponse>>,
    waits: Waits,
) -> Result<Vec<acp::SessionUpdate>> {
    tokio::pin!(load);
    let mut updates = Vec::new();
    let mut answer: Option<AcpResult<acp::LoadSessionResponse>> = None;
    let mut closed = false;
    let mut deadline = Instant::now() + waits.silence;

    loop {
        if closed && answer.is_some() {
            break;
        }
        tokio::select! {
            biased;
            message = rx.recv(), if !closed => match message {
                Some(message) => {
                    let heard = handle(message, session, &mut updates);
                    if answer.is_none() {
                        // Any message shows that the agent is alive and at work.
                        deadline = Instant::now() + waits.silence;
                    } else if heard == Heard::Replay {
                        deadline = Instant::now() + waits.settle;
                    }
                }
                // Every sender is gone: nothing more can come, and `load` ends at once.
                None => closed = true,
            },
            result = &mut load, if answer.is_none() => {
                answer = Some(result);
                deadline = Instant::now() + waits.settle;
            }
            () = sleep_until(deadline) => break,
        }
    }

    match answer {
        Some(Ok(_)) => Ok(updates),
        Some(Err(error)) => Err(load_failure(session, &error)),
        None => bail!(
            "The agent did not answer while it loaded session '{}': it said nothing for {} s.",
            session.0,
            waits.silence.as_secs()
        ),
    }
}

/// What a message from the agent was, for the clock of [`collect_replay`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Heard {
    /// An update that belongs to the replay of the session.
    Replay,
    /// Anything else.
    Other,
}

/// Do what the export does with one message from the agent. It keeps the replayed updates and
/// answers the rest in the way that grants nothing.
fn handle(
    message: AcpClientMessage,
    session: &acp::SessionId,
    updates: &mut Vec<acp::SessionUpdate>,
) -> Heard {
    match message {
        AcpClientMessage::SessionNotification(args) => {
            let notification = args.request;
            let _ = args.response_tx.send(Ok(()));
            if notification.session_id == *session && marks_replay(&notification) {
                updates.push(notification.update);
                Heard::Replay
            } else {
                Heard::Other
            }
        }
        // The agent asks about trusting the project, or about a tool. "Cancelled" is the answer
        // of a client whose user is not there; it decides nothing and the agent stores nothing.
        AcpClientMessage::RequestPermission(args) => {
            let outcome = acp::RequestPermissionOutcome::Cancelled;
            let _ = args
                .response_tx
                .send(Ok(acp::RequestPermissionResponse::new(outcome)));
            Heard::Other
        }
        AcpClientMessage::ExtNotification(args) => {
            let _ = args.response_tx.send(Ok(()));
            Heard::Other
        }
        // This client offers no files, no terminals and no extension methods.
        AcpClientMessage::ReadTextFile(args) => refuse(args),
        AcpClientMessage::WriteTextFile(args) => refuse(args),
        AcpClientMessage::CreateTerminal(args) => refuse(args),
        AcpClientMessage::TerminalOutput(args) => refuse(args),
        AcpClientMessage::ReleaseTerminal(args) => refuse(args),
        AcpClientMessage::WaitForTerminalExit(args) => refuse(args),
        AcpClientMessage::KillTerminalCommand(args) => refuse(args),
        AcpClientMessage::ExtMethod(args) => refuse(args),
    }
}

/// Answer a request of the agent with "method not found".
fn refuse<T: AcpRequest>(args: AcpArgs<T>) -> Heard {
    let method = args.request.method_name();
    tracing::debug!(method, "export_cmd: refused a request of the agent");
    let error = acp::Error::new(
        acp::ErrorCode::MethodNotFound.into(),
        format!("{method} is not offered while a session is exported"),
    );
    let _ = args.response_tx.send(Err(error));
    Heard::Other
}

/// Whether the agent marked this notification as a part of the replay. The flag is `isReplay` in
/// the `_meta` of the notification, or in the `_meta` of the update that it carries; the Python
/// agent puts it on the update.
fn marks_replay(notification: &acp::SessionNotification) -> bool {
    fn flagged(meta: Option<&acp::Meta>) -> bool {
        meta.and_then(|meta| meta.get("isReplay"))
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
    }
    let update_meta = match &notification.update {
        acp::SessionUpdate::UserMessageChunk(chunk)
        | acp::SessionUpdate::AgentMessageChunk(chunk)
        | acp::SessionUpdate::AgentThoughtChunk(chunk) => chunk.meta.as_ref(),
        acp::SessionUpdate::ToolCall(call) => call.meta.as_ref(),
        acp::SessionUpdate::ToolCallUpdate(update) => update.meta.as_ref(),
        acp::SessionUpdate::Plan(plan) => plan.meta.as_ref(),
        _ => None,
    };
    flagged(notification.meta.as_ref()) || flagged(update_meta)
}

/// The message for a `session/load` that the agent refused or that never finished.
fn load_failure(session: &acp::SessionId, error: &acp::Error) -> anyhow::Error {
    let id = &session.0;
    if error.code == acp::ErrorCode::ResourceNotFound {
        anyhow!("Session '{id}' not found.")
    } else if acp_channel_failure(error).is_some() {
        anyhow!("The agent stopped before it had loaded session '{id}'.")
    } else {
        anyhow!("The agent could not load session '{id}': {error}")
    }
}
