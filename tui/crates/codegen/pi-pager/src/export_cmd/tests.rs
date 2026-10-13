//! The export's reader of the replay runs against a channel that the test controls, and the clock
//! stands still until every task waits (`start_paused`), so the waits of the reader are exact.

use std::future::Future;
use std::time::Duration;

use agent_client_protocol as acp;
use pi_acp_lib::{AcpAgentMessage, AcpArgs, AcpClientMessage, AcpResult, acp_send};
use tokio::sync::{mpsc, oneshot};
use tokio::time::Instant;

use super::render_markdown;
use super::replay::{Waits, collect_replay};

const WAITS: Waits = Waits {
    silence: Duration::from_secs(10),
    settle: Duration::from_millis(500),
};

type Tx = mpsc::UnboundedSender<AcpClientMessage>;

fn session() -> acp::SessionId {
    acp::SessionId::new("sess-1")
}

fn replay_flag() -> Option<acp::Meta> {
    serde_json::json!({ "isReplay": true }).as_object().cloned()
}

fn chunk(text: &str, meta: Option<acp::Meta>) -> acp::ContentChunk {
    acp::ContentChunk::new(acp::ContentBlock::Text(acp::TextContent::new(
        text.to_string(),
    )))
    .meta(meta)
}

/// An update of the history, flagged the way the Python agent flags it (on the update).
fn user(text: &str) -> acp::SessionUpdate {
    acp::SessionUpdate::UserMessageChunk(chunk(text, replay_flag()))
}

fn agent(text: &str) -> acp::SessionUpdate {
    acp::SessionUpdate::AgentMessageChunk(chunk(text, replay_flag()))
}

/// What the agent says after the load that is not the history: no flag.
fn chatter(text: &str) -> acp::SessionUpdate {
    acp::SessionUpdate::AgentMessageChunk(chunk(text, None))
}

fn tool_call(id: &str, meta: Option<acp::Meta>) -> acp::SessionUpdate {
    acp::SessionUpdate::ToolCall(
        acp::ToolCall::new(acp::ToolCallId::new(id), "Execute `ls`")
            .kind(acp::ToolKind::Execute)
            .status(acp::ToolCallStatus::Completed)
            .raw_input(Some(serde_json::json!({ "command": "ls" })))
            .meta(meta),
    )
}

fn say_about(
    tx: &Tx,
    session: &acp::SessionId,
    update: acp::SessionUpdate,
    meta: Option<acp::Meta>,
) {
    let (response_tx, _response_rx) = oneshot::channel();
    let mut request = acp::SessionNotification::new(session.clone(), update);
    request.meta = meta;
    let message = AcpClientMessage::from(AcpArgs {
        request,
        response_tx,
    });
    // A closed channel is the test's own business.
    let _ = tx.send(message);
}

fn say(tx: &Tx, update: acp::SessionUpdate) {
    say_about(tx, &session(), update, None);
}

fn texts(updates: &[acp::SessionUpdate]) -> Vec<String> {
    updates
        .iter()
        .map(|update| match update {
            acp::SessionUpdate::UserMessageChunk(chunk)
            | acp::SessionUpdate::AgentMessageChunk(chunk)
            | acp::SessionUpdate::AgentThoughtChunk(chunk) => match &chunk.content {
                acp::ContentBlock::Text(part) => part.text.clone(),
                other => format!("{other:?}"),
            },
            other => format!("{other:?}"),
        })
        .collect()
}

/// A `session/load` that the agent answers at once.
fn answered() -> impl Future<Output = AcpResult<acp::LoadSessionResponse>> {
    std::future::ready(Ok(acp::LoadSessionResponse::new()))
}

fn refused(
    code: acp::ErrorCode,
    message: &str,
) -> impl Future<Output = AcpResult<acp::LoadSessionResponse>> {
    std::future::ready(Err(acp::Error::new(code.into(), message)))
}

// ---- what is kept ------------------------------------------------------------------------------

#[tokio::test(start_paused = true)]
async fn the_replayed_updates_come_back_in_the_order_they_were_sent() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say(&tx, user("hi"));
    say(&tx, agent("hello"));
    say(&tx, tool_call("t1", replay_flag()));

    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(updates.len(), 3);
    assert_eq!(texts(&updates[..2]), ["hi", "hello"]);
    assert!(matches!(updates[2], acp::SessionUpdate::ToolCall(_)));
}

#[tokio::test(start_paused = true)]
async fn what_the_agent_says_besides_the_history_is_left_out() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say(&tx, user("hi"));
    // The explanation that comes before the question about the project is a message too.
    say(&tx, chatter("Do you trust this project?"));
    say(&tx, tool_call("t2", None));
    say(&tx, agent("hello"));

    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["hi", "hello"]);
}

#[tokio::test(start_paused = true)]
async fn the_flag_may_sit_on_the_notification_instead_of_the_update() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say_about(&tx, &session(), chatter("hi"), replay_flag());

    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["hi"]);
}

#[tokio::test(start_paused = true)]
async fn a_flag_that_says_false_does_not_count() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    let live = serde_json::json!({ "isReplay": false })
        .as_object()
        .cloned();
    say(
        &tx,
        acp::SessionUpdate::AgentMessageChunk(chunk("live", live)),
    );
    say(&tx, agent("kept"));

    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["kept"]);
}

#[tokio::test(start_paused = true)]
async fn updates_of_another_session_are_left_out() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say(&tx, user("mine"));
    say_about(&tx, &acp::SessionId::new("other"), user("not mine"), None);

    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["mine"]);
}

// ---- when the replay is complete ---------------------------------------------------------------

#[tokio::test(start_paused = true)]
async fn an_update_that_trails_the_answer_is_still_collected() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say(&tx, user("hi"));
    let sender = tx.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(200)).await;
        say(&sender, agent("late"));
        tokio::time::sleep(Duration::from_secs(5)).await;
        say(&sender, agent("too late"));
    });

    let started = Instant::now();
    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["hi", "late"]);
    // The quiet time starts again with the late update: 200 ms + 500 ms.
    assert_eq!(started.elapsed(), Duration::from_millis(700));
}

#[tokio::test(start_paused = true)]
async fn chatter_after_the_answer_does_not_hold_the_wait_open() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say(&tx, user("hi"));
    let sender = tx.clone();
    tokio::spawn(async move {
        for _ in 0..100 {
            tokio::time::sleep(Duration::from_millis(100)).await;
            say(&sender, chatter("still here"));
        }
    });

    let started = Instant::now();
    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["hi"]);
    assert_eq!(started.elapsed(), WAITS.settle);
}

#[tokio::test(start_paused = true)]
async fn a_closed_channel_after_the_answer_ends_the_wait_at_once() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    say(&tx, user("hi"));
    drop(tx);

    let started = Instant::now();
    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["hi"]);
    assert_eq!(started.elapsed(), Duration::ZERO);
}

#[tokio::test(start_paused = true)]
async fn a_long_replay_is_not_cut_off_by_the_silence_limit() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    let sender = tx.clone();
    tokio::spawn(async move {
        for n in 1..=3 {
            tokio::time::sleep(Duration::from_secs(6)).await;
            say(&sender, agent(&format!("part {n}")));
        }
    });
    // The agent answers at 20 s. The silence limit (10 s) is longer than any pause between two
    // messages, but shorter than the whole replay.
    let load = async {
        tokio::time::sleep(Duration::from_secs(20)).await;
        Ok(acp::LoadSessionResponse::new())
    };

    let updates = collect_replay(&mut rx, &session(), load, WAITS)
        .await
        .unwrap();

    assert_eq!(texts(&updates), ["part 1", "part 2", "part 3"]);
}

// ---- what the agent asks -----------------------------------------------------------------------

#[tokio::test(start_paused = true)]
async fn a_question_about_a_tool_or_the_project_is_answered_cancelled() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    let (response_tx, response_rx) = oneshot::channel();
    let question = acp::RequestPermissionRequest::new(
        session(),
        acp::ToolCallUpdate::new(
            acp::ToolCallId::new("trust"),
            acp::ToolCallUpdateFields::new(),
        ),
        Vec::new(),
    );
    tx.send(AcpClientMessage::from(AcpArgs {
        request: question,
        response_tx,
    }))
    .unwrap();
    say(&tx, user("hi"));

    let updates = collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    let answer = response_rx.await.unwrap().unwrap();
    assert!(
        matches!(answer.outcome, acp::RequestPermissionOutcome::Cancelled),
        "{answer:?}"
    );
    assert_eq!(texts(&updates), ["hi"]);
}

#[tokio::test(start_paused = true)]
async fn a_request_for_a_file_is_refused() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    let (response_tx, response_rx) = oneshot::channel();
    tx.send(AcpClientMessage::from(AcpArgs {
        request: acp::ReadTextFileRequest::new(session(), "/etc/hostname"),
        response_tx,
    }))
    .unwrap();

    collect_replay(&mut rx, &session(), answered(), WAITS)
        .await
        .unwrap();

    let error = response_rx.await.unwrap().unwrap_err();
    assert_eq!(error.code, acp::ErrorCode::MethodNotFound);
}

// ---- when it fails -----------------------------------------------------------------------------

#[tokio::test(start_paused = true)]
async fn an_unknown_session_is_named() {
    let (_tx, mut rx) = mpsc::unbounded_channel();
    let load = refused(acp::ErrorCode::ResourceNotFound, "Resource not found");

    let error = collect_replay(&mut rx, &session(), load, WAITS)
        .await
        .unwrap_err();

    assert_eq!(error.to_string(), "Session 'sess-1' not found.");
}

#[tokio::test(start_paused = true)]
async fn another_refusal_is_shown_with_the_reason_of_the_agent() {
    let (_tx, mut rx) = mpsc::unbounded_channel();
    let load = refused(acp::ErrorCode::InvalidParams, "Invalid params");

    let error = collect_replay(&mut rx, &session(), load, WAITS)
        .await
        .unwrap_err();

    assert_eq!(
        error.to_string(),
        "The agent could not load session 'sess-1': Invalid params"
    );
}

#[tokio::test(start_paused = true)]
async fn an_agent_that_goes_away_is_reported_as_stopped() {
    let (_tx, mut rx) = mpsc::unbounded_channel();
    // The request cannot even be sent: nobody holds the other end of the channel.
    let (agent_tx, agent_rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
    drop(agent_rx);
    let load = acp_send(acp::LoadSessionRequest::new(session(), "/work"), &agent_tx);

    let error = collect_replay(&mut rx, &session(), load, WAITS)
        .await
        .unwrap_err();

    assert_eq!(
        error.to_string(),
        "The agent stopped before it had loaded session 'sess-1'."
    );
}

#[tokio::test(start_paused = true)]
async fn an_agent_that_says_nothing_is_given_up_on() {
    let (_tx, mut rx) = mpsc::unbounded_channel();
    let load = std::future::pending::<AcpResult<acp::LoadSessionResponse>>();

    let started = Instant::now();
    let error = collect_replay(&mut rx, &session(), load, WAITS)
        .await
        .unwrap_err();

    assert_eq!(started.elapsed(), WAITS.silence);
    assert_eq!(
        error.to_string(),
        "The agent did not answer while it loaded session 'sess-1': it said nothing for 10 s."
    );
}

// ---- the text ----------------------------------------------------------------------------------

#[test]
fn a_conversation_is_written_as_the_export_command_writes_it() {
    let md = render_markdown(vec![user("hi"), agent("hello"), agent(" there")]);

    assert_eq!(md, "## User\n\nhi\n\n## Assistant\n\nhello there");
}

#[test]
fn tool_calls_are_listed_under_a_tools_heading() {
    let md = render_markdown(vec![user("list"), tool_call("t1", replay_flag())]);

    assert!(md.starts_with("## User\n\nlist\n\n## Tools\n\n- "), "{md}");
    assert!(md.contains("ls"), "{md}");
}

#[test]
fn no_updates_make_no_text() {
    assert_eq!(render_markdown(Vec::new()), "");
}
