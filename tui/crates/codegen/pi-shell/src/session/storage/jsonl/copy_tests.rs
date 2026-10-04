use crate::session::info::Info;
use crate::session::storage::{
    JsonlStorageAdapter, StorageAdapter,
};
use agent_client_protocol as acp;
use tempfile::TempDir;

#[tokio::test]
async fn copy_session_data_source_not_found() {
    let temp_dir = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());

    let source_info = Info {
        id: acp::SessionId::new("nonexistent"),
        cwd: "/nonexistent".to_string(),
    };

    let target_info = Info {
        id: acp::SessionId::new("fork-nonexistent-abcd1234"),
        cwd: "/target".to_string(),
    };

    let result = adapter
        .copy_session_data(&source_info, &target_info, Default::default())
        .await;
    assert!(result.is_err());
}

/// Boundary matrix for the capped line reader: exactly-cap content is kept,
/// cap-plus-one is discarded without consuming an index (so the two copy
/// passes stay aligned), a drain spanning several read chunks terminates, and
/// an unterminated within-cap tail is kept.
#[test]
fn capped_line_reader_discards_overlong_lines_without_shifting_indexes() {
    fn collect(input: &[u8], cap: usize) -> Vec<(usize, Vec<u8>)> {
        let mut seen = Vec::new();
        super::for_each_jsonl_line_capped(std::io::Cursor::new(input), cap, |index, line| {
            seen.push((index, line.to_vec()));
            Ok(std::ops::ControlFlow::Continue(()))
        })
        .unwrap();
        seen
    }

    // Exactly cap content bytes: kept.
    assert_eq!(collect(b"abcd\n", 4), vec![(0, b"abcd".to_vec())]);
    // One over cap: discarded; the next line takes the next index, not a
    // shifted one.
    assert_eq!(
        collect(b"aa\nxxxxx\nbb\n", 4),
        vec![(0, b"aa".to_vec()), (1, b"bb".to_vec())]
    );
    // Overlong spanning several drain chunks still finds the line end.
    assert_eq!(
        collect(b"xxxxxxxxxxxxxxxxxxxxx\ncc\n", 4),
        vec![(0, b"cc".to_vec())]
    );
    // Overlong unterminated at EOF: drain hits EOF and stops cleanly.
    assert_eq!(collect(b"aa\nxxxxxxxx", 4), vec![(0, b"aa".to_vec())]);
    // Unterminated within-cap tail is kept, matching the uncapped reader.
    assert_eq!(
        collect(b"aa\nbb", 4),
        vec![(0, b"aa".to_vec()), (1, b"bb".to_vec())]
    );
}
