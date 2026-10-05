//! Git worktree operations: create, list, remove, apply.
//!
//! Core worktree lifecycle logic lives in [`pi_workspace::worktree`].
//! This module re-exports everything from there and adds session-aware
//! functions that depend on shell-specific infrastructure (persistence,
//! auth, registry client, storage client, session restore).
use crate::util::config::WorktreeType as ShellWorktreeType;
pub(crate) use pi_workspace::worktree::*;
impl From<ShellWorktreeType> for WorktreeType {
    fn from(t: ShellWorktreeType) -> Self {
        match t {
            ShellWorktreeType::Linked => WorktreeType::Linked,
            ShellWorktreeType::Standalone => WorktreeType::Standalone,
            ShellWorktreeType::Git => WorktreeType::Git,
        }
    }
}
impl From<WorktreeType> for ShellWorktreeType {
    fn from(t: WorktreeType) -> Self {
        match t {
            WorktreeType::Linked => ShellWorktreeType::Linked,
            WorktreeType::Standalone => ShellWorktreeType::Standalone,
            WorktreeType::Git => ShellWorktreeType::Git,
        }
    }
}
#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use serial_test::serial;
    #[test]
    fn resume_request_deserializes_with_defaults() {
        let json = r#"{"sessionId":"s1","sourceCwd":"/project"}"#;
        let req: ResumeSessionInWorktreeRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.session_id, "s1");
        assert_eq!(req.source_cwd, "/project");
        assert!(matches!(req.copy_mode, WorktreeCopyMode::Dirty));
        assert!(req.worktree_type.is_none());
        assert!(req.git_ref.is_none());
    }
    #[test]
    fn resume_request_deserializes_explicit_fields() {
        let json = r#"{
            "sessionId": "abc",
            "sourceCwd": "/work",
            "copyMode": "clean",
            "worktreeType": "standalone",
            "gitRef": "main"
        }"#;
        let req: ResumeSessionInWorktreeRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.session_id, "abc");
        assert!(matches!(req.copy_mode, WorktreeCopyMode::Clean));
        assert_eq!(req.worktree_type, Some(WorktreeType::Standalone));
        assert_eq!(req.git_ref.as_deref(), Some("main"));
    }
    #[test]
    fn resume_response_round_trips() {
        let resp = ResumeSessionInWorktreeResponse {
            session_id: "forked-id".into(),
            worktree_path: "/wt/root".into(),
            effective_cwd: "/wt/root/sub".into(),
            remote_restored: true,
            parent_session_id: "original-id".into(),
            chat_messages_copied: 42,
            updates_copied: 100,
            code_restored: true,
            restore_summary: Some(
                "checked out abc12345, staged: true, unstaged: false, untracked: 3".into(),
            ),
            restore_degree: Some(pi_workspace::session::git::RestoreDegree::Full),
        };
        let json = serde_json::to_string(&resp).unwrap();
        let deser: ResumeSessionInWorktreeResponse = serde_json::from_str(&json).unwrap();
        assert_eq!(deser.session_id, "forked-id");
        assert_eq!(deser.effective_cwd, "/wt/root/sub");
        assert!(deser.remote_restored);
        assert_eq!(deser.parent_session_id, "original-id");
        assert_eq!(deser.chat_messages_copied, 42);
        assert_eq!(deser.updates_copied, 100);
        assert!(deser.code_restored);
        assert_eq!(
            deser.restore_summary.as_deref(),
            Some("checked out abc12345, staged: true, unstaged: false, untracked: 3")
        );
        assert_eq!(
            deser.restore_degree,
            Some(pi_workspace::session::git::RestoreDegree::Full)
        );
    }
    #[test]
    fn resume_response_serializes_degree_when_set() {
        let resp = ResumeSessionInWorktreeResponse {
            session_id: "s".into(),
            worktree_path: "w".into(),
            effective_cwd: "e".into(),
            remote_restored: false,
            parent_session_id: "p".into(),
            chat_messages_copied: 0,
            updates_copied: 0,
            code_restored: true,
            restore_summary: Some("checked out abc".into()),
            restore_degree: Some(pi_workspace::session::git::RestoreDegree::HeadOnly),
        };
        let json = serde_json::to_string(&resp).unwrap();
        assert!(json.contains("\"restoreDegree\":\"head_only\""));
        assert!(json.contains("\"restoreSummary\":\"checked out abc\""));
        let deser: ResumeSessionInWorktreeResponse = serde_json::from_str(&json).unwrap();
        assert_eq!(
            deser.restore_degree,
            Some(pi_workspace::session::git::RestoreDegree::HeadOnly)
        );
        assert_eq!(deser.restore_summary.as_deref(), Some("checked out abc"));
    }
    /// Unknown degree strings must fail deserialisation rather than
    /// silently round-tripping as a typo.
    #[test]
    fn resume_response_rejects_unknown_degree_string() {
        let json = r#"{
            "sessionId": "s",
            "worktreePath": "w",
            "effectiveCwd": "e",
            "remoteRestored": false,
            "parentSessionId": "p",
            "chatMessagesCopied": 0,
            "updatesCopied": 0,
            "codeRestored": true,
            "restoreDegree": "full_"
        }"#;
        let r: Result<ResumeSessionInWorktreeResponse, _> = serde_json::from_str(json);
        assert!(r.is_err(), "typo \"full_\" must fail to deserialise");
    }
    #[test]
    fn resume_response_camel_case_keys() {
        let resp = ResumeSessionInWorktreeResponse {
            session_id: "s".into(),
            worktree_path: "w".into(),
            effective_cwd: "e".into(),
            remote_restored: false,
            parent_session_id: "p".into(),
            chat_messages_copied: 0,
            updates_copied: 0,
            code_restored: false,
            restore_summary: None,
            restore_degree: None,
        };
        let json = serde_json::to_string(&resp).unwrap();
        assert!(json.contains("sessionId"));
        assert!(json.contains("worktreePath"));
        assert!(json.contains("effectiveCwd"));
        assert!(json.contains("remoteRestored"));
        assert!(json.contains("parentSessionId"));
        assert!(json.contains("chatMessagesCopied"));
        assert!(json.contains("updatesCopied"));
        assert!(json.contains("codeRestored"));
        assert!(!json.contains("restoreSummary"));
        assert!(!json.contains("restoreDegree"));
        assert!(!json.contains("session_id"));
        assert!(!json.contains("worktree_path"));
    }
    #[test]
    fn test_dirty_summary_serialization() {
        let summary = DirtyStateSummary {
            staged_count: 3,
            modified_count: 5,
            deleted_count: 1,
            untracked_count: 12,
            has_partially_staged: true,
            skipped_dirs: vec!["node_modules".to_string(), "target".to_string()],
        };
        let json = serde_json::to_string(&summary).unwrap();
        assert!(json.contains("\"stagedCount\":3"));
        assert!(json.contains("\"modifiedCount\":5"));
        assert!(json.contains("\"hasPartiallyStaged\":true"));
        assert!(json.contains("\"skippedDirs\":["));
    }
    #[test]
    fn test_copy_mode_default_is_dirty() {
        let mode: WorktreeCopyMode = Default::default();
        assert_eq!(mode, WorktreeCopyMode::Dirty);
    }
    #[test]
    fn test_copy_mode_deserialization() {
        let clean: WorktreeCopyMode = serde_json::from_str("\"clean\"").unwrap();
        assert_eq!(clean, WorktreeCopyMode::Clean);
        let dirty: WorktreeCopyMode = serde_json::from_str("\"dirty\"").unwrap();
        assert_eq!(dirty, WorktreeCopyMode::Dirty);
    }
    #[test]
    fn test_created_status_without_copied_changes() {
        let status = WorktreeStatus::Created {
            session_id: "test-123".to_string(),
            worktree_path: "/path/to/worktree".to_string(),
            commit: "abc123".to_string(),
            source_git_root: None,
            copied_changes: None,
        };
        let json = serde_json::to_string(&status).unwrap();
        assert!(!json.contains("copiedChanges"));
        assert!(json.contains("\"status\":\"created\""));
        assert!(json.contains("\"sessionId\":\"test-123\""));
    }
    #[test]
    fn test_created_status_with_copied_changes() {
        let status = WorktreeStatus::Created {
            session_id: "test-123".to_string(),
            worktree_path: "/path/to/worktree".to_string(),
            commit: "abc123".to_string(),
            source_git_root: Some("/path/to/source".to_string()),
            copied_changes: Some(CopiedChangesSummary {
                staged_copied: 3,
                modified_copied: 5,
                untracked_copied: 12,
                deletions_applied: 1,
                warnings: vec![],
            }),
        };
        let json = serde_json::to_string(&status).unwrap();
        assert!(json.contains("copiedChanges"));
        assert!(json.contains("\"stagedCopied\":3"));
    }
    #[test]
    fn remove_request_legacy_worktree_path_only() {
        let json = r#"{"worktreePath": "/path/to/wt", "force": true}"#;
        let req: RemoveWorktreeRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.worktree_path.as_deref(), Some("/path/to/wt"));
        assert!(req.id_or_path.is_none());
        assert!(req.force);
        assert!(!req.dry_run);
    }
    #[test]
    fn remove_request_new_id_or_path_only() {
        let json = r#"{"idOrPath": "wt-abc123", "dryRun": true}"#;
        let req: RemoveWorktreeRequest = serde_json::from_str(json).unwrap();
        assert!(req.worktree_path.is_none());
        assert_eq!(req.id_or_path.as_deref(), Some("wt-abc123"));
        assert!(!req.force);
        assert!(req.dry_run);
    }
    #[test]
    fn remove_request_both_fields_deserializes_but_handler_rejects() {
        let json = r#"{"worktreePath": "/explicit", "idOrPath": "fallback"}"#;
        let req: RemoveWorktreeRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.worktree_path.as_deref(), Some("/explicit"));
        assert_eq!(req.id_or_path.as_deref(), Some("fallback"));
    }
    #[test]
    fn remove_response_omits_resolved_path_when_none() {
        let resp = RemoveWorktreeResponse {
            removed: true,
            resolved_path: None,
        };
        let json = serde_json::to_string(&resp).unwrap();
        assert!(!json.contains("resolvedPath"));
    }
    #[test]
    fn remove_response_includes_resolved_path_when_present() {
        let resp = RemoveWorktreeResponse {
            removed: true,
            resolved_path: Some("/resolved/path".into()),
        };
        let json = serde_json::to_string(&resp).unwrap();
        assert!(json.contains("\"resolvedPath\":\"/resolved/path\""));
    }
    #[test]
    #[serial]
    fn resolve_worktree_by_id_or_path_nonexistent_returns_none() {
        let result = resolve_worktree_by_id_or_path("/nonexistent/path/xyz123").unwrap();
        assert!(result.is_none());
    }
    #[test]
    #[serial]
    fn resolve_worktree_by_id_or_path_existing_dir_returns_path() {
        let tmp = tempfile::TempDir::new().unwrap();
        let path = tmp.path().to_str().unwrap();
        let result = resolve_worktree_by_id_or_path(path).unwrap();
        assert_eq!(result.unwrap(), tmp.path());
    }
    fn make_wt_record(path: &str, source_repo: &str) -> pi_fast_worktree::WorktreeRecord {
        pi_fast_worktree::WorktreeRecord {
            id: format!("wt-{}", path.replace('/', "-")),
            path: std::path::PathBuf::from(path),
            source_repo: std::path::PathBuf::from(source_repo),
            repo_name: "repo".into(),
            kind: pi_fast_worktree::WorktreeKind::Session,
            creation_mode: "linked".into(),
            git_ref: None,
            head_commit: None,
            session_id: None,
            creator_pid: None,
            created_at: 0,
            last_accessed_at: None,
            status: pi_fast_worktree::WorktreeStatus::Alive,
            metadata: None,
        }
    }
    #[test]
    fn build_candidate_list_cwd_equals_main_root() {
        let result = build_candidate_list("/repo/main", "/repo/main", &[], &[]);
        assert_eq!(result, vec!["/repo/main"]);
    }
    #[test]
    fn build_candidate_list_cwd_differs_from_main_root() {
        let result = build_candidate_list("/repo/wt-1", "/repo/main", &[], &[]);
        assert_eq!(result, vec!["/repo/wt-1", "/repo/main"]);
    }
    #[test]
    fn build_candidate_list_includes_db_records_sorted() {
        let records = vec![
            make_wt_record("/repo/wt-c", "/repo/main"),
            make_wt_record("/repo/wt-a", "/repo/main"),
            make_wt_record("/repo/wt-b", "/repo/main"),
        ];
        let result = build_candidate_list("/repo/main", "/repo/main", &records, &[]);
        assert_eq!(
            result,
            vec!["/repo/main", "/repo/wt-a", "/repo/wt-b", "/repo/wt-c"]
        );
    }
    #[test]
    fn build_candidate_list_dedupes_cwd_and_main_root() {
        let records = vec![
            make_wt_record("/repo/main", "/repo/main"),
            make_wt_record("/repo/wt-1", "/repo/main"),
            make_wt_record("/repo/wt-1", "/repo/main"),
        ];
        let result = build_candidate_list("/repo/wt-1", "/repo/main", &records, &[]);
        assert_eq!(result, vec!["/repo/wt-1", "/repo/main"]);
    }
    #[test]
    fn test_background_copy_guard_registers_and_unregisters() {
        let context = BackgroundCopyContext::new();
        let worktree_path = "/test/worktree/guard-test".to_string();
        let cancellation_token = tokio_util::sync::CancellationToken::new();
        {
            let _guard = BackgroundCopyGuard::new(
                context.clone(),
                worktree_path.clone(),
                cancellation_token.clone(),
            );
        }
        let was_cancelled = context.cancel(&worktree_path);
        assert!(!was_cancelled);
    }
    #[test]
    fn test_background_copy_context_cancel_via_context() {
        let context = BackgroundCopyContext::new();
        let worktree_path = "/test/worktree/cancel-test".to_string();
        let cancellation_token = tokio_util::sync::CancellationToken::new();
        let _guard = BackgroundCopyGuard::new(
            context.clone(),
            worktree_path.clone(),
            cancellation_token.clone(),
        );
        assert!(!cancellation_token.is_cancelled());
        let was_cancelled = context.cancel(&worktree_path);
        assert!(was_cancelled);
        assert!(cancellation_token.is_cancelled());
    }
}
