//! `resolve_startup` against a scripted `session/list` agent. The real agent is in
//! `tests/python_agent_e2e.rs`.

use super::*;
use pi_acp_lib::AcpAgentMessage;
use std::sync::{Arc, Mutex};

const A: &str = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B: &str = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const C: &str = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const UNKNOWN: &str = "99999999-9999-4999-8999-999999999999";

/// The `(cwd, cursor)` of every `session/list` request, in order.
type Seen = Arc<Mutex<Vec<(Option<String>, Option<String>)>>>;

/// A scripted agent: request number `n` is answered with `script(n)`.
fn agent(
    script: impl Fn(usize) -> Result<serde_json::Value, acp::Error> + Send + 'static,
) -> (AcpAgentTx, Seen) {
    let seen: Seen = Default::default();
    let seen_by_agent = seen.clone();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Some(message) = rx.recv().await {
            let AcpAgentMessage::ListSessions(args) = message else {
                panic!("a request other than session/list: {message:?}");
            };
            let n = {
                let mut seen = seen_by_agent.lock().unwrap();
                seen.push((
                    args.request
                        .cwd
                        .as_ref()
                        .map(|cwd| cwd.to_string_lossy().into_owned()),
                    args.request.cursor.clone(),
                ));
                seen.len() - 1
            };
            let answer = script(n).map(|page| serde_json::from_value(page).expect("a page"));
            let _ = args.response_tx.send(answer);
        }
    });
    (tx, seen)
}

fn row(id: &str, cwd: &str, title: &str, updated: &str) -> serde_json::Value {
    serde_json::json!({
        "sessionId": id, "cwd": cwd, "title": title, "updatedAt": updated,
    })
}

fn page(rows: Vec<serde_json::Value>, next: Option<&str>) -> serde_json::Value {
    match next {
        Some(cursor) => serde_json::json!({ "sessions": rows, "nextCursor": cursor }),
        None => serde_json::json!({ "sessions": rows }),
    }
}

fn boom() -> acp::Error {
    acp::Error::new(acp::ErrorCode::InternalError.into(), "boom")
}

fn listed(id: &str, updated: Option<&str>) -> ListedSession {
    ListedSession {
        id: id.to_owned(),
        cwd: PathBuf::from("/work"),
        title: None,
        updated_at: updated.map(|stamp| stamp.parse().unwrap()),
    }
}

const WORK: &str = "/work";

async fn resolve(
    tx: &AcpAgentTx,
    intent: SessionStartupIntent,
) -> anyhow::Result<MaterializedStartup> {
    resolve_startup(tx, intent, Path::new(WORK)).await
}

fn resume_id(id: &str) -> SessionStartupIntent {
    SessionStartupIntent::Resume {
        session_id: Some(id.to_owned()),
        most_recent_for_cwd: false,
    }
}

const CONTINUE: SessionStartupIntent = SessionStartupIntent::Resume {
    session_id: None,
    most_recent_for_cwd: true,
};

fn unwrap_resume(started: MaterializedStartup) -> (String, Option<PathBuf>, Option<String>) {
    match started {
        MaterializedStartup::Resume {
            session_id,
            original_cwd,
            title,
            deferred_local_miss,
            suppress_code_restore,
        } => {
            assert!(!deferred_local_miss && !suppress_code_restore);
            (session_id, original_cwd, title)
        }
        other => panic!("expected Resume, got {other:?}"),
    }
}

// ---- the pure parts ----------------------------------------------------------------------------

#[test]
fn most_recent_is_the_latest_activity_not_the_first_row() {
    let rows = [
        listed("created-last", Some("2026-10-01T10:00:00Z")),
        listed("used-last", Some("2026-10-02T09:00:00Z")),
        listed("created-first", Some("2026-10-01T11:00:00Z")),
    ];
    assert_eq!(most_recent(&rows).unwrap().id, "used-last");
}

#[test]
fn most_recent_keeps_the_earlier_row_on_equal_times() {
    let rows = [
        listed("first", Some("2026-10-01T10:00:00Z")),
        listed("second", Some("2026-10-01T10:00:00Z")),
    ];
    assert_eq!(most_recent(&rows).unwrap().id, "first");
}

#[test]
fn most_recent_ranks_rows_without_a_time_last() {
    let rows = [
        listed("no-time", None),
        listed("timed", Some("2000-01-01T00:00:00Z")),
    ];
    assert_eq!(most_recent(&rows).unwrap().id, "timed");
    let none = [listed("x", None), listed("y", None)];
    assert_eq!(most_recent(&none).unwrap().id, "x");
    assert!(most_recent(&[]).is_none());
}

#[test]
fn a_row_is_trimmed_and_its_time_parsed() {
    let info: acp::SessionInfo = serde_json::from_value(serde_json::json!({
        "sessionId": A, "cwd": "/work", "title": "  fix it  ",
        "updatedAt": "2026-10-16T09:41:22.123Z",
    }))
    .unwrap();
    let row = ListedSession::from(&info);
    assert_eq!(row.id, A);
    assert_eq!(row.title.as_deref(), Some("fix it"));
    assert_eq!(
        row.updated_at,
        Some("2026-10-16T09:41:22.123Z".parse().unwrap())
    );
    let bare: acp::SessionInfo = serde_json::from_value(serde_json::json!({
        "sessionId": B, "cwd": "/work", "title": "   ", "updatedAt": "not a time",
    }))
    .unwrap();
    let row = ListedSession::from(&bare);
    assert_eq!(row.title, None, "a blank title is no title");
    assert_eq!(
        row.updated_at, None,
        "a time that does not parse is no time"
    );
}

#[test]
fn the_session_directory_is_passed_on_only_when_it_is_another_one() {
    let here = Path::new("/work");
    assert_eq!(original_cwd(Path::new("/work"), here), None);
    assert_eq!(original_cwd(Path::new("/work/"), here), None);
    assert_eq!(original_cwd(Path::new(""), here), None);
    assert_eq!(
        original_cwd(Path::new("/other"), here),
        Some(PathBuf::from("/other"))
    );
}

#[test]
fn uuids_compare_as_uuids() {
    assert!(same_id(&A.to_uppercase(), A));
    assert!(same_id(&A.replace('-', ""), A));
    assert!(!same_id(A, B));
    assert!(same_id("legacy-id", "legacy-id"));
    assert!(!same_id("legacy-id", "Legacy-Id"));
}

// ---- --continue --------------------------------------------------------------------------------

#[tokio::test]
async fn continue_reads_every_page_and_takes_the_latest_activity() {
    let (tx, seen) = agent(|n| {
        Ok(match n {
            0 => page(
                vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
                Some("c1"),
            ),
            1 => page(
                vec![row(B, WORK, "two", "2026-10-03T12:00:00Z")],
                Some("c2"),
            ),
            2 => page(vec![row(C, WORK, "three", "2026-10-02T11:00:00Z")], None),
            _ => panic!("a fourth page was asked for"),
        })
    });
    let (id, original_cwd, title) = unwrap_resume(resolve(&tx, CONTINUE).await.unwrap());
    assert_eq!(id, B, "the session used last, not the one created last");
    assert_eq!(original_cwd, None, "it is in this directory");
    assert_eq!(title.as_deref(), Some("two"));
    let seen = seen.lock().unwrap().clone();
    let cursors: Vec<Option<&str>> = seen.iter().map(|(_, cursor)| cursor.as_deref()).collect();
    assert_eq!(cursors, [None, Some("c1"), Some("c2")]);
    assert!(
        seen.iter().all(|(cwd, _)| cwd.as_deref() == Some(WORK)),
        "--continue asks for this directory only: {seen:?}"
    );
}

#[tokio::test]
async fn continue_with_no_session_in_the_directory_says_so() {
    let (tx, _) = agent(|_| Ok(page(vec![], None)));
    let error = resolve(&tx, CONTINUE).await.unwrap_err().to_string();
    assert!(
        error.contains("No session found for current directory"),
        "{error}"
    );
}

#[tokio::test]
async fn resume_without_an_argument_is_continue() {
    let (tx, seen) = agent(|_| {
        Ok(page(
            vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
            None,
        ))
    });
    // The intent `--resume` with no value has: the same as `--continue`.
    let (id, ..) = unwrap_resume(resolve(&tx, CONTINUE).await.unwrap());
    assert_eq!(id, A);
    assert_eq!(seen.lock().unwrap().len(), 1);
}

// ---- --resume <id> -----------------------------------------------------------------------------

#[tokio::test]
async fn resume_by_id_asks_for_every_directory_and_stops_when_it_finds_the_id() {
    let (tx, seen) = agent(|n| {
        Ok(match n {
            0 => page(
                vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
                Some("c1"),
            ),
            1 => page(
                vec![row(B, "/elsewhere", "two", "2026-10-02T10:00:00Z")],
                Some("c2"),
            ),
            _ => panic!("the walk went on after the id was found"),
        })
    });
    let (id, original_cwd, title) = unwrap_resume(resolve(&tx, resume_id(B)).await.unwrap());
    assert_eq!(id, B);
    assert_eq!(
        original_cwd,
        Some(PathBuf::from("/elsewhere")),
        "the session is loaded in its own directory"
    );
    assert_eq!(title.as_deref(), Some("two"));
    let seen = seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 2);
    assert!(
        seen.iter().all(|(cwd, _)| cwd.is_none()),
        "an id is looked for in every directory: {seen:?}"
    );
}

#[tokio::test]
async fn resume_by_id_in_this_directory_passes_no_directory() {
    let (tx, _) = agent(|_| {
        Ok(page(
            vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
            None,
        ))
    });
    let (_, original_cwd, _) = unwrap_resume(resolve(&tx, resume_id(A)).await.unwrap());
    assert_eq!(original_cwd, None);
}

#[tokio::test]
async fn resume_by_id_ignores_letter_case_and_loads_with_the_agents_spelling() {
    let (tx, _) = agent(|_| {
        Ok(page(
            vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
            None,
        ))
    });
    let (id, ..) = unwrap_resume(resolve(&tx, resume_id(&A.to_uppercase())).await.unwrap());
    assert_eq!(id, A);
}

#[tokio::test]
async fn resume_by_unknown_id_names_the_id() {
    let (tx, seen) = agent(|n| {
        Ok(match n {
            0 => page(
                vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
                Some("c1"),
            ),
            _ => page(vec![row(B, WORK, "two", "2026-10-02T10:00:00Z")], None),
        })
    });
    let error = resolve(&tx, resume_id(UNKNOWN))
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("Session does not exist"), "{error}");
    assert!(error.contains(UNKNOWN), "{error}");
    assert!(
        !error.contains("cut short"),
        "the list was read to its end: {error}"
    );
    assert_eq!(seen.lock().unwrap().len(), 2, "every page was read");
}

#[tokio::test]
async fn a_cut_list_is_said_in_the_error() {
    // The agent keeps handing out the cursor it gave: the walk is cut after the second page.
    let (tx, _) = agent(|n| {
        Ok(page(
            vec![row(A, WORK, &format!("t{n}"), "2026-10-01T10:00:00Z")],
            Some("same"),
        ))
    });
    let error = resolve(&tx, resume_id(UNKNOWN))
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("cut short"), "{error}");
}

// ---- --resume <title> --------------------------------------------------------------------------

#[tokio::test]
async fn resume_by_title_matches_in_this_directory_whatever_the_letter_case() {
    let (tx, seen) = agent(|n| {
        Ok(match n {
            0 => page(
                vec![row(A, WORK, "Other work", "2026-10-01T10:00:00Z")],
                Some("c1"),
            ),
            _ => page(
                vec![row(B, WORK, "Fix Login Bug", "2026-10-02T10:00:00Z")],
                None,
            ),
        })
    });
    let (id, original_cwd, title) =
        unwrap_resume(resolve(&tx, resume_id("  fix login BUG ")).await.unwrap());
    assert_eq!(id, B);
    assert_eq!(original_cwd, None);
    assert_eq!(title.as_deref(), Some("Fix Login Bug"));
    let seen = seen.lock().unwrap().clone();
    assert!(
        seen.iter().all(|(cwd, _)| cwd.as_deref() == Some(WORK)),
        "titles are matched per directory: {seen:?}"
    );
}

#[tokio::test]
async fn resume_by_a_title_two_sessions_have_lists_them() {
    let (tx, _) = agent(|_| {
        Ok(page(
            vec![
                row(A, WORK, "hello", "2026-10-01T10:00:00Z"),
                row(B, WORK, "Hello", "2026-10-02T10:00:00Z"),
                row(C, WORK, "something else", "2026-10-03T10:00:00Z"),
            ],
            None,
        ))
    });
    let error = resolve(&tx, resume_id("hello"))
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("Multiple sessions match title"), "{error}");
    assert!(error.contains(A) && error.contains(B), "{error}");
    assert!(!error.contains(C), "{error}");
    assert!(error.contains("2026-10-02 10:00 UTC"), "{error}");
    assert!(error.contains("--resume <session-id>"), "{error}");
}

#[tokio::test]
async fn resume_by_unknown_title_says_what_did_not_match() {
    let (tx, _) = agent(|_| {
        Ok(page(
            vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
            None,
        ))
    });
    let error = resolve(&tx, resume_id("no such title"))
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("Session does not exist"), "{error}");
    assert!(
        error.contains("no session id or title matched \"no such title\""),
        "{error}"
    );
    assert!(error.contains("--resume <session-id>"), "{error}");
}

#[tokio::test]
async fn a_blank_title_matches_nothing() {
    let (tx, _) = agent(|_| {
        Ok(page(
            vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
            None,
        ))
    });
    let error = resolve(&tx, resume_id("   "))
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("no session id or title matched"), "{error}");
}

// ---- errors ------------------------------------------------------------------------------------

#[tokio::test]
async fn a_page_that_fails_fails_the_lookup() {
    let (tx, _) = agent(|n| match n {
        0 => Ok(page(
            vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
            Some("c1"),
        )),
        _ => Err(boom()),
    });
    for intent in [CONTINUE, resume_id(UNKNOWN), resume_id("a title")] {
        let error = resolve(&tx, intent).await.unwrap_err().to_string();
        assert!(
            error.contains("could not list the sessions of the agent"),
            "{error}"
        );
        assert!(error.contains("boom"), "{error}");
    }
}

// ---- --session-id ------------------------------------------------------------------------------

fn new_with_id(id: &str) -> SessionStartupIntent {
    SessionStartupIntent::NewWithId {
        session_id: id.to_owned(),
    }
}

#[tokio::test]
async fn a_new_session_without_an_id_asks_nothing() {
    let (tx, seen) = agent(|_| panic!("no request is expected"));
    assert!(matches!(
        resolve(&tx, SessionStartupIntent::NewAuto).await.unwrap(),
        MaterializedStartup::NewAuto
    ));
    assert!(seen.lock().unwrap().is_empty());
}

#[tokio::test]
async fn a_session_id_that_is_not_a_uuid_is_refused_before_any_request() {
    let (tx, seen) = agent(|_| panic!("no request is expected"));
    let error = resolve(&tx, new_with_id("my-run-1"))
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("must be a valid UUID"), "{error}");
    assert!(seen.lock().unwrap().is_empty());
}

#[tokio::test]
async fn a_session_id_in_use_is_refused() {
    let (tx, seen) = agent(|n| {
        Ok(match n {
            0 => page(
                vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
                Some("c1"),
            ),
            _ => page(
                vec![row(B, "/elsewhere", "two", "2026-10-02T10:00:00Z")],
                None,
            ),
        })
    });
    let error = resolve(&tx, new_with_id(B)).await.unwrap_err().to_string();
    assert!(
        error.contains(&format!("Session ID {B} is already in use")),
        "{error}"
    );
    let seen = seen.lock().unwrap().clone();
    assert!(
        seen.iter().all(|(cwd, _)| cwd.is_none()),
        "the agent keeps one id space for every directory: {seen:?}"
    );
}

#[tokio::test]
async fn a_free_session_id_goes_through() {
    let (tx, seen) = agent(|n| {
        Ok(match n {
            0 => page(
                vec![row(A, WORK, "one", "2026-10-01T10:00:00Z")],
                Some("c1"),
            ),
            _ => page(vec![row(B, WORK, "two", "2026-10-02T10:00:00Z")], None),
        })
    });
    match resolve(&tx, new_with_id(C)).await.unwrap() {
        MaterializedStartup::NewWithId { session_id } => assert_eq!(session_id, C),
        other => panic!("expected NewWithId, got {other:?}"),
    }
    assert_eq!(
        seen.lock().unwrap().len(),
        2,
        "every page was read to prove the id is free"
    );
}

#[tokio::test]
async fn an_agent_that_cannot_list_does_not_block_session_id() {
    let (tx, _) = agent(|_| {
        Err(acp::Error::new(
            acp::ErrorCode::MethodNotFound.into(),
            "session/list is not offered",
        ))
    });
    assert!(matches!(
        resolve(&tx, new_with_id(C)).await.unwrap(),
        MaterializedStartup::NewWithId { .. }
    ));
}

#[tokio::test]
async fn a_failing_list_fails_session_id_unless_it_is_missing() {
    let (tx, _) = agent(|_| Err(boom()));
    let error = resolve(&tx, new_with_id(C)).await.unwrap_err().to_string();
    assert!(
        error.contains("could not list the sessions of the agent"),
        "{error}"
    );
}
