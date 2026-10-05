//! Bridges workspace file-change events to the codebase graph.
//!
//! [`ws_event_to_codebase_graph_event`] converts a workspace event into a graph
//! event for the [`IndexManagerHandle`](pi_codebase_graph::IndexManagerHandle);
//! [`refresh_codebase_graph_after_head_change`] handles git HEAD changes by
//! diffing `ORIG_HEAD..HEAD`.

use std::path::Path;

const GIT_DIFF_REBUILD_THRESHOLD: usize = 500;

fn parse_diff_name_status_line(
    line: &str,
    repo_root: &Path,
) -> Option<pi_codebase_graph::FileEvent> {
    let mut parts = line.splitn(3, '\t');
    let status = parts.next()?.trim();
    let path = parts.next()?;

    match status.chars().next()? {
        'A' => Some(pi_codebase_graph::FileEvent::created(repo_root.join(path))),
        'D' => Some(pi_codebase_graph::FileEvent::removed(repo_root.join(path))),
        'R' | 'C' => {
            let new_path = parts.next()?;
            Some(pi_codebase_graph::FileEvent::renamed(
                repo_root.join(path),
                repo_root.join(new_path),
            ))
        }
        _ => Some(pi_codebase_graph::FileEvent::modified(repo_root.join(path))),
    }
}

/// After a HEAD change, diff `ORIG_HEAD..HEAD` and send targeted
/// events to the codebase graph. Falls back to full rebuild if too
/// many files changed.
///
/// Emits [`WorkspaceEvent::CodebaseIndexUpdated`] on the provided
/// `events_tx` after the index has been updated (either via targeted
/// events or a full rebuild). Skips the event if the index actor
/// channel is closed (i.e. the actor has been dropped).
pub(crate) async fn refresh_codebase_graph_after_head_change(
    idx: &pi_codebase_graph::IndexManagerHandle,
    repo_root: &Path,
    events_tx: &tokio::sync::broadcast::Sender<pi_workspace_types::WorkspaceEvent>,
) {
    let mut diff_cmd = tokio::process::Command::new("git");
    diff_cmd
        .args(["diff", "--name-status", "ORIG_HEAD", "HEAD"])
        .current_dir(repo_root)
        .stdin(std::process::Stdio::null());
    pi_tools::util::detach_command(&mut diff_cmd);
    diff_cmd.envs(pi_tools::util::pager_env());
    let diff_output = diff_cmd.output().await;

    // `None` means the update failed entirely (channel closed) --
    // skip the event so subscribers are not misled.
    let files_updated: Option<u64>;

    match diff_output {
        Ok(output) if output.status.success() => {
            let changed: Vec<_> = String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter(|l| !l.is_empty())
                .filter_map(|l| parse_diff_name_status_line(l, repo_root))
                .collect();

            let count = changed.len();
            if count > GIT_DIFF_REBUILD_THRESHOLD {
                tracing::debug!(
                    "git_refresh: {count} changed files exceeds threshold, falling back to rebuild"
                );
                files_updated = match idx.rebuild() {
                    Ok(()) => Some(count as u64),
                    Err(e) => {
                        tracing::debug!("git_refresh: rebuild failed: {:?}", e);
                        None
                    }
                };
            } else if let Err(e) = idx.send_events(changed) {
                tracing::debug!("git_refresh: failed to send graph events: {:?}", e);
                files_updated = None;
            } else {
                tracing::debug!("git_refresh: sent {count} changed files to codebase graph");
                files_updated = Some(count as u64);
            }
        }
        _ => {
            tracing::debug!("git_refresh: git diff failed, falling back to rebuild");
            files_updated = match idx.rebuild() {
                Ok(()) => Some(0),
                Err(e) => {
                    tracing::debug!("git_refresh: rebuild fallback also failed: {:?}", e);
                    None
                }
            };
        }
    }

    if let Some(count) = files_updated {
        let _ = events_tx.send(pi_workspace_types::WorkspaceEvent::CodebaseIndexUpdated {
            files_indexed: count,
        });
    }
}

pub(crate) fn ws_event_to_codebase_graph_event(
    path: &std::path::Path,
    kind: pi_workspace_types::FsEventKind,
) -> pi_codebase_graph::FileEvent {
    use pi_codebase_graph::{FileEvent, FileEventKind};
    let graph_kind = match kind {
        pi_workspace_types::FsEventKind::Created => FileEventKind::Created,
        pi_workspace_types::FsEventKind::Modified => FileEventKind::Modified,
        pi_workspace_types::FsEventKind::Removed => FileEventKind::Removed,
        pi_workspace_types::FsEventKind::Renamed => FileEventKind::Renamed,
    };
    FileEvent::new(vec![path.to_path_buf()], graph_kind)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_diff_name_status_all_variants() {
        use pi_codebase_graph::FileEventKind;
        let root = Path::new("/repo");

        let ev = parse_diff_name_status_line("M\tsrc/main.rs", root).unwrap();
        assert_eq!(ev.kind, FileEventKind::Modified);

        let ev = parse_diff_name_status_line("A\tnew_file.rs", root).unwrap();
        assert_eq!(ev.kind, FileEventKind::Created);

        let ev = parse_diff_name_status_line("D\told_file.rs", root).unwrap();
        assert_eq!(ev.kind, FileEventKind::Removed);

        let ev = parse_diff_name_status_line("R100\told.rs\tnew.rs", root).unwrap();
        assert_eq!(ev.kind, FileEventKind::Renamed);

        assert!(parse_diff_name_status_line("", root).is_none());
    }
}
