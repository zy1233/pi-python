//! Shared test utilities for the pager crate.
//!
//! Compiled only in `#[cfg(test)]` builds. Import via `crate::test_util`.
use std::path::{Path, PathBuf};
/// Minimal `AgentView` for unit tests outside the dispatch/handler modules
/// (which keep their own richer factories).
pub fn make_agent_view(session_id: Option<&str>, cwd: &str) -> crate::app::agent_view::AgentView {
    use crate::app::agent::{AgentId, AgentSession, AgentState};
    let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
    let session = AgentSession {
        id: AgentId(0),
        acp_tx: tx,
        session_id: session_id.map(agent_client_protocol::SessionId::new),
        models: crate::acp::model_state::ModelState::default(),
        state: AgentState::Idle,
        tracker: crate::acp::tracker::AcpUpdateTracker::new(),
        cwd: std::path::PathBuf::from(cwd),
        is_worktree: false,
        forked_from: None,
        pending_prompts: std::collections::VecDeque::new(),
        next_queue_id: 0,
        yolo_mode: false,
        auto_mode: false,
        prompt_history: Vec::new(),
        loading_replay: false,
        restore_degree: None,
        rate_limited: false,
        model_incompatible: false,
        credit_limit_blocked: false,
        free_usage_blocked: false,
        available_commands: Vec::new(),
        available_commands_generation: 0,
        available_tools: None,
        model_switch_pending: false,
        user_model_preference: None,
        deferred_model_switch: None,
        in_flight_prompt: None,
        compact_held_prompt: None,
        current_prompt_id: None,
        created_via_new: false,
    };
    crate::app::agent_view::AgentView::new(
        session,
        crate::scrollback::state::ScrollbackState::new(),
    )
}
pub fn make_worktree_record(
    id: &str,
    path: &std::path::Path,
    label: &str,
) -> pi_fast_worktree::WorktreeRecord {
    use pi_fast_worktree::{WorktreeKind, WorktreeRecord, WorktreeStatus};
    WorktreeRecord {
        id: id.to_owned(),
        path: path.to_path_buf(),
        source_repo: "/repo".into(),
        repo_name: "repo".into(),
        kind: WorktreeKind::Session,
        creation_mode: "linked".into(),
        git_ref: None,
        head_commit: None,
        session_id: None,
        creator_pid: None,
        created_at: 0,
        last_accessed_at: None,
        status: WorktreeStatus::Alive,
        metadata: Some(serde_json::json!({ "label": label })),
    }
}
/// Every row containing `row_marker` starts its PATH cell at the header's
/// PATH column, measured in display width so CJK regressions fail.
pub fn assert_path_column_aligned(text: &str, row_marker: &str) {
    use unicode_width::UnicodeWidthStr;
    let lines: Vec<&str> = text.lines().collect();
    let header = lines
        .iter()
        .find(|l| l.ends_with("PATH"))
        .unwrap_or_else(|| panic!("no PATH header in: {text}"));
    let path_col = header.width() - "PATH".width();
    let mut rows = 0;
    for line in lines.iter().filter(|l| l.contains(row_marker)) {
        let (_, path) = line
            .rsplit_once(' ')
            .expect("rows end in a space-free test path (PATH is the last cell)");
        assert_eq!(
            line.width() - path.width(),
            path_col,
            "path column must stay width-aligned: {line:?}"
        );
        rows += 1;
    }
    assert!(rows > 0, "no table rows matched {row_marker:?} in: {text}");
}
/// RAII guard for temporarily overriding an environment variable.
///
/// Captures the original value on construction and restores it on drop.
/// Used by theme and persist tests to redirect `HOME`/`USERPROFILE` to
/// temp directories without affecting the real user config.
pub struct EnvVarGuard {
    key: &'static str,
    original: Option<std::ffi::OsString>,
}
impl EnvVarGuard {
    /// Override `key` to `value` (paths, URLs, flags — anything OsStr-able),
    /// returning a guard that restores the original on drop.
    pub fn set(key: &'static str, value: impl AsRef<std::ffi::OsStr>) -> Self {
        let original = std::env::var_os(key);
        unsafe {
            std::env::set_var(key, value);
        }
        Self { key, original }
    }
}
impl Drop for EnvVarGuard {
    fn drop(&mut self) {
        unsafe {
            if let Some(value) = &self.original {
                std::env::set_var(self.key, value);
            } else {
                std::env::remove_var(self.key);
            }
        }
    }
}

/// Name of the environment variable that tells a test, in a child process that
/// [`in_own_process`] started, to run its body instead of starting a child.
const OWN_PROCESS_ENV: &str = "PI_PAGER_TEST_IN_OWN_PROCESS";

/// Run `body` in a child process that runs only this one test.
///
/// Use it for a test that changes process-wide state which other tests read
/// without a lock (the color cap, the native-palette lock, the minimal-mode
/// flag, the embedded-modal flag, the theme, ...). All tests of one test
/// binary are threads of one process and share these statics, so a change
/// inside the body can break any other test that runs at the same time. A
/// child process has its own statics, so the change cannot get out. A lock
/// does not help when the tests that read the state do not take it, and there
/// are too many of them to change.
///
/// The parent starts this test binary again with `--exact` and the name of the
/// test, and waits for it. libtest names the thread of a test after the test,
/// so the call needs no name. The parent fails, and prints what the child
/// wrote, when the child fails or when it did not run exactly one test, so a
/// child that ran nothing cannot make the test pass. When the thread has no
/// such name (the test did not start on a thread of its own), nothing runs at
/// the same time, and the body runs here. The code of the test that is before
/// the call runs in both processes; the code after it runs in both too, but
/// the parent does not run `body`.
pub fn in_own_process(body: impl FnOnce()) {
    if std::env::var_os(OWN_PROCESS_ENV).is_some() {
        body();
        return;
    }
    let thread = std::thread::current();
    let Some(name) = thread.name().filter(|name| name.contains("::")) else {
        body();
        return;
    };
    let exe = std::env::current_exe().expect("path of the test binary");
    let output = std::process::Command::new(exe)
        .args(["--exact", name, "--nocapture", "--test-threads", "1"])
        .env(OWN_PROCESS_ENV, name)
        .output()
        .expect("start the test binary again");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success() && stdout.contains("test result: ok. 1 passed;"),
        "`{name}` did not pass in its own process ({}; the child must run exactly one \
         test)\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}",
        output.status,
    );
}

/// On-disk git checkout living under a tempdir. Dropping the fixture deletes it.
pub struct TempGitRepo {
    _dir: tempfile::TempDir,
    pub path: PathBuf,
}
impl TempGitRepo {
    pub fn init(branch: &str) -> Self {
        let dir = tempfile::tempdir().expect("temp git root");
        let path = dir.path().join("repo");
        init_git_repo_on_branch(&path, branch);
        Self { _dir: dir, path }
    }
    /// CoW-style standalone clone: `.git` is a directory plus `grok-worktree-source`.
    pub fn standalone_clone(&self, branch: &str) -> Self {
        let dir = tempfile::tempdir().expect("temp clone root");
        let path = dir.path().join("clone");
        copy_dir_all(&self.path, &path);
        checkout_named_branch(&path, branch);
        std::fs::write(
            path.join(".git").join("grok-worktree-source"),
            self.path.to_string_lossy().as_bytes(),
        )
        .unwrap();
        Self { _dir: dir, path }
    }
    /// Linked `git worktree` of `branch` as a sibling of this checkout.
    /// `.git` at the returned path is a file. Keep `self` alive while using it.
    pub fn add_linked_worktree(&self, name: &str, branch: &str) -> PathBuf {
        let repo = git2::Repository::open(&self.path).unwrap();
        let commit = repo.head().unwrap().peel_to_commit().unwrap();
        if repo.find_branch(branch, git2::BranchType::Local).is_err() {
            repo.branch(branch, &commit, false).unwrap();
        }
        let wt_path = self
            .path
            .parent()
            .expect("repo lives in a temp parent")
            .join(name);
        let reference = repo
            .find_branch(branch, git2::BranchType::Local)
            .unwrap()
            .into_reference();
        repo.worktree(
            name,
            &wt_path,
            Some(git2::WorktreeAddOptions::new().reference(Some(&reference))),
        )
        .unwrap();
        wt_path
    }
}
/// Match [`crate::git_info`] tilde-collapse of a filesystem path.
pub fn collapsed_path_display(path: &Path) -> String {
    let home = std::env::var("HOME")
        .ok()
        .map(|h| std::path::PathBuf::from(h.trim_end_matches(['/', '\\'])));
    match home {
        Some(h) => path
            .strip_prefix(&h)
            .map(|rest| format!("~/{}", rest.display()))
            .unwrap_or_else(|_| path.display().to_string()),
        None => path.display().to_string(),
    }
}
pub fn init_git_repo_on_branch(path: &Path, branch: &str) {
    std::fs::create_dir_all(path).unwrap();
    let repo = git2::Repository::init(path).unwrap();
    std::fs::write(path.join("README"), "test\n").unwrap();
    let sig = git2::Signature::now("test", "test@test.com").unwrap();
    let tree_id = {
        let mut index = repo.index().unwrap();
        index.add_path(Path::new("README")).unwrap();
        index.write().unwrap();
        index.write_tree().unwrap()
    };
    let tree = repo.find_tree(tree_id).unwrap();
    let oid = repo
        .commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
        .unwrap();
    let commit = repo.find_commit(oid).unwrap();
    let current = repo
        .head()
        .ok()
        .and_then(|h| h.shorthand().map(str::to_string));
    if current.as_deref() != Some(branch) {
        repo.branch(branch, &commit, true).unwrap();
        repo.set_head(&format!("refs/heads/{branch}")).unwrap();
        repo.checkout_head(Some(git2::build::CheckoutBuilder::new().force()))
            .unwrap();
    }
}
fn checkout_named_branch(repo_path: &Path, branch: &str) {
    let repo = git2::Repository::open(repo_path).unwrap();
    let commit = repo.head().unwrap().peel_to_commit().unwrap();
    if repo.find_branch(branch, git2::BranchType::Local).is_err() {
        repo.branch(branch, &commit, false).unwrap();
    }
    repo.set_head(&format!("refs/heads/{branch}")).unwrap();
    repo.checkout_head(Some(git2::build::CheckoutBuilder::new().force()))
        .unwrap();
}
fn copy_dir_all(src: &Path, dst: &Path) {
    std::fs::create_dir_all(dst).unwrap();
    for entry in std::fs::read_dir(src).unwrap() {
        let entry = entry.unwrap();
        let to = dst.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_dir_all(&entry.path(), &to);
        } else {
            std::fs::copy(entry.path(), to).unwrap();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};

    /// The body runs in the child only, so a change that it makes to a `static`
    /// does not reach the parent. The code after the call runs in both
    /// processes: the flag is set in the process that ran the body, and in no
    /// other.
    #[test]
    fn a_change_in_the_body_stays_in_the_child() {
        static FLAG: AtomicBool = AtomicBool::new(false);
        let is_child = std::env::var_os(OWN_PROCESS_ENV).is_some();
        in_own_process(|| FLAG.store(true, Ordering::SeqCst));
        assert_eq!(FLAG.load(Ordering::SeqCst), is_child);
    }

    /// A panic in the body fails the parent, and the message of the parent
    /// says so. The child fails too: its panic does not carry the expected
    /// message. That failure is what the parent reports.
    #[test]
    #[should_panic(expected = "did not pass in its own process")]
    fn a_failure_in_the_body_fails_the_parent() {
        in_own_process(|| panic!("the body of the test fails"));
    }
}
