//! Tests for session-related modals (extensions, /new worktree question)
//! and session close helpers.

use super::*;

// ── /new dispatcher tests ─────────────────────────────────────────────

#[test]
fn dispatch_new_session_opens_question_modal_in_git_repo() {
    let mut app = new_session_test_app();
    app.new_session_worktree_mode = crate::app::app_view::WorktreeMode::Ask;
    let effects = dispatch(Action::NewSession, &mut app);
    assert!(effects.is_empty(), "no effects until modal answered");
    // No new agent yet (creation is deferred until modal answered).
    assert_eq!(app.agents.len(), 1);
    let qv = app.agents[&AgentId(0)]
        .question_view
        .as_ref()
        .expect("modal must be open");
    match qv.local_kind.as_ref().expect("local_kind must be set") {
        crate::views::question_view::LocalQuestionKind::NewSession => {}
        other => panic!("expected NewSession, got {other:?}"),
    }
    assert_eq!(
        qv.questions[0].options.len(),
        4,
        "modal must offer exactly 4 options (Yes/No/Always/Never)"
    );
    let labels: Vec<&str> = qv.questions[0]
        .options
        .iter()
        .map(|o| o.label.as_str())
        .collect();
    assert_eq!(
        labels,
        vec!["Yes", "No", "Always worktree", "Never worktree"]
    );
}

#[test]
fn dispatch_new_session_skips_modal_in_non_git_repo() {
    // current_branch stays None (no git repo) → no modal, straight
    // to dispatch_new_session_inner.
    let mut app = test_app_with_agent();
    let effects = dispatch(Action::NewSession, &mut app);
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateSession { .. })),
        "non-git path must emit CreateSession, got {effects:?}"
    );
    assert!(
        app.agents.values().all(|a| a.question_view.is_none()),
        "non-git path must not open the modal"
    );
}

// ── Session close ─────────────────────────────
