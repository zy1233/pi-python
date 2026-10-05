#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    // ── derive_child_cwd ─────────────────────────────────────────────

    #[test]
    fn derive_child_cwd_uses_child_cwd_from_info() {
        let parent_cwd = PathBuf::from("/parent/cwd");
        let mut info = make_subagent_info("child-1");
        info.child_cwd = Some("/child/worktree".into());
        info.worktree_path = Some("/child/worktree".into());

        let (cwd, is_wt) = derive_child_cwd(&parent_cwd, Some(&info));
        assert_eq!(cwd, PathBuf::from("/child/worktree"));
        assert!(is_wt);
    }

    #[test]
    fn derive_child_cwd_falls_back_to_parent_when_child_cwd_is_none() {
        let parent_cwd = PathBuf::from("/parent/cwd");
        let info = make_subagent_info("child-2");

        let (cwd, is_wt) = derive_child_cwd(&parent_cwd, Some(&info));
        assert_eq!(cwd, PathBuf::from("/parent/cwd"));
        assert!(!is_wt);
    }

    #[test]
    fn derive_child_cwd_worktree_independent_of_child_cwd() {
        let parent_cwd = PathBuf::from("/parent/cwd");
        let mut info = make_subagent_info("child-3");
        info.child_cwd = None;
        info.worktree_path = Some("/some/worktree".into());

        let (cwd, is_wt) = derive_child_cwd(&parent_cwd, Some(&info));
        assert_eq!(cwd, PathBuf::from("/parent/cwd"), "falls back to parent");
        assert!(
            is_wt,
            "worktree flag must be set even when child_cwd is None"
        );
    }

    #[test]
    fn derive_child_cwd_no_info_falls_back() {
        let parent_cwd = PathBuf::from("/parent/cwd");
        let (cwd, is_wt) = derive_child_cwd(&parent_cwd, None);
        assert_eq!(cwd, PathBuf::from("/parent/cwd"));
        assert!(!is_wt);
    }

