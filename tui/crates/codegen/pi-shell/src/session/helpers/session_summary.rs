//! Session title generation via LLM tool call.

/// Real-user turn counts at which the auto title is refreshed from the whole
/// conversation, then frozen. Turn 1's title comes from the fast first-prompt
/// path; refreshing at a couple of early turns lets the title catch up to the
/// real topic without churning enough to make sessions hard to recognize. A
/// manual `/rename` always wins and stops refreshes.
pub(crate) const TITLE_REFRESH_TURNS: [usize; 2] = [3, 6];

/// Durable title-refresh checkpoint watermark under `{session_dir}/`: the number
/// of [`TITLE_REFRESH_TURNS`] checkpoints already consumed. Written on every
/// completed attempt (success or failure) so the freeze survives resume,
/// restart, and compaction; only a committed value is persisted, so an aborted
/// refresh still retries. See [`load_title_refresh_watermark`]. Public so the
/// fork/copy path can carry it alongside the inherited title.
pub(crate) const TITLE_REFRESH_WATERMARK_FILE: &str = "title_refresh_idx";

/// Load the persisted checkpoint index, clamped to the number of checkpoints so
/// a stale larger value still means "frozen". `None` when the session has no
/// watermark yet (fresh, pre-feature, or feature-was-off) — the caller decides
/// the starting checkpoint for that case (see [`initial_title_refresh_idx`]).
pub(crate) fn load_title_refresh_watermark(session_dir: &std::path::Path) -> Option<usize> {
    std::fs::read_to_string(session_dir.join(TITLE_REFRESH_WATERMARK_FILE))
        .ok()
        .and_then(|s| s.trim().parse::<usize>().ok())
        .map(|idx| idx.min(TITLE_REFRESH_TURNS.len()))
}

/// Persist the checkpoint index after a completed attempt. Best-effort, and
/// written atomically (temp sibling + rename) so a crash mid-write can't leave a
/// partial/empty file that would load as `0` and reopen the refresh window.
pub(crate) fn save_title_refresh_watermark(session_dir: &std::path::Path, idx: usize) {
    if !session_dir.is_dir() {
        return;
    }
    let path = session_dir.join(TITLE_REFRESH_WATERMARK_FILE);
    if let Err(e) = crate::session::storage::write_bytes_atomic(&path, idx.to_string().as_bytes()) {
        tracing::warn!(error = %e, path = %path.display(), "failed to persist title refresh watermark");
    }
}

#[cfg(test)]
mod tests {

    /// The freeze watermark round-trips (durable across a shortened conversation,
    /// e.g. compaction), reads `None` when absent, and clamps a stale-large value.
    #[test]
    fn title_refresh_watermark_round_trips_and_clamps() {
        use super::{
            TITLE_REFRESH_TURNS, TITLE_REFRESH_WATERMARK_FILE, load_title_refresh_watermark,
            save_title_refresh_watermark,
        };
        let dir = tempfile::TempDir::new().unwrap();
        assert_eq!(
            load_title_refresh_watermark(dir.path()),
            None,
            "missing → None"
        );
        save_title_refresh_watermark(dir.path(), 1);
        assert_eq!(load_title_refresh_watermark(dir.path()), Some(1));
        std::fs::write(dir.path().join(TITLE_REFRESH_WATERMARK_FILE), "99").unwrap();
        assert_eq!(
            load_title_refresh_watermark(dir.path()),
            Some(TITLE_REFRESH_TURNS.len()),
            "stale-large watermark reads as frozen"
        );
    }
}
