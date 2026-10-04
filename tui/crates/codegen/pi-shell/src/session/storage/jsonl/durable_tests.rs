use super::*;
use crate::session::info::Info;

fn info() -> Info {
    Info {
        id: acp::SessionId::new("durable-jsonl"),
        cwd: "/test".into(),
    }
}

#[test]
fn directory_barrier_failure_is_retried_even_after_file_exists() {
    let mut attempts = 0;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("updates.jsonl");
    let mut flaky_parent = || {
        attempts += 1;
        if attempts == 1 {
            Err(io::Error::other("directory barrier failed"))
        } else {
            Ok(())
        }
    };
    assert!(
        JsonlStorageAdapter::append_jsonl_line_sync_with(
            &path,
            b"{\"record\":1}\n".to_vec(),
            AppendDurability::Durable,
            std::fs::File::sync_all,
            &mut flaky_parent,
        )
        .is_err()
    );
    JsonlStorageAdapter::append_jsonl_line_sync_with(
        &path,
        b"{\"record\":1}\n".to_vec(),
        AppendDurability::Durable,
        std::fs::File::sync_all,
        &mut flaky_parent,
    )
    .unwrap();
    assert_eq!(attempts, 2);
}

#[test]
fn file_barrier_error_propagates() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("updates.jsonl");
    let error = JsonlStorageAdapter::append_jsonl_line_sync_with(
        &path,
        b"{\"record\":1}\n".to_vec(),
        AppendDurability::Durable,
        |_| Err(io::Error::other("file barrier failed")),
        || Ok(()),
    )
    .unwrap_err();
    assert_eq!(error.to_string(), "file barrier failed");
}

#[test]
fn cwd_switch_retry_after_post_append_barrier_failure_is_already_present() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("chat_history.jsonl");
    let item = ConversationItem::working_directory_switch("moved", 3);
    let mut line = serde_json::to_vec(&item).unwrap();
    line.push(b'\n');

    assert!(matches!(
        JsonlStorageAdapter::append_cwd_switch_line_sync_with(
            &path,
            line.clone(),
            3,
            |_| Err(io::Error::other("file barrier failed")),
            || Ok(()),
        ),
        Err(crate::session::storage::AppendCwdSwitchError::Committed {
            acknowledgement: pi_chat_state::StrictAppendAck::Appended,
            ..
        })
    ));
    assert!(matches!(
        JsonlStorageAdapter::append_cwd_switch_line_sync_with(
            &path,
            line,
            3,
            |_| Ok(()),
            || Ok(()),
        )
        .unwrap(),
        pi_chat_state::StrictAppendAck::AlreadyPresent(item)
            if item.text_content() == "moved"
    ));
    assert_eq!(std::fs::read_to_string(path).unwrap().lines().count(), 1);
}

#[cfg(target_os = "macos")]
#[test]
fn darwin_fullfsync_seam_reports_invalid_descriptor() {
    assert!(super::super::fullfsync_raw(-1).is_err());
}
