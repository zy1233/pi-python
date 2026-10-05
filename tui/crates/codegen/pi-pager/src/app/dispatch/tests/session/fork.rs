//! Tests for session forking.

use super::*;

// ── Worktree session tests ───────────────────────────────────────

#[test]
fn open_new_worktree_dialog_sets_dialog_state() {
    let mut app = test_app();
    assert!(app.new_worktree_dialog.is_none());
    let effects = dispatch(Action::OpenNewWorktreeDialog, &mut app);
    assert!(effects.is_empty());
    assert!(app.new_worktree_dialog.is_some());
}

#[test]
fn slash_new_uses_worktree_cwd() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let worktree_path = PathBuf::from("/worktree/path");
    app.agents.get_mut(&id).unwrap().session.cwd = worktree_path.clone();
    app.agents.get_mut(&id).unwrap().session.is_worktree = true;

    let effects = dispatch(Action::SendPrompt("/new".into()), &mut app);
    let create = effects
        .iter()
        .find(|e| matches!(e, Effect::CreateSession { .. }));
    assert!(create.is_some(), "expected CreateSession effect");
    match create.unwrap() {
        Effect::CreateSession { cwd, .. } => assert_eq!(cwd, &worktree_path),
        _ => unreachable!(),
    }
    // The new agent (id=1) should inherit is_worktree from the source agent.
    let new_id = AgentId(1);
    assert!(app.agents[&new_id].session.is_worktree);
}

#[test]
fn auth_complete_dispatches_deferred_worktree() {
    let mut app = test_app();
    app.cwd_has_git_ancestor = true;
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Pending,
    };
    app.deferred_startup.worktree = true;

    let effects = dispatch(
        Action::TaskComplete(TaskResult::AuthComplete {
            request_seq: 1,
            meta: None,
        }),
        &mut app,
    );

    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateWorktreeSession { .. }))
    );
    assert!(!app.deferred_startup.worktree);
}

#[test]
fn auth_complete_resume_plus_worktree_creates_worktree_with_session() {
    let mut app = test_app();
    app.cwd_has_git_ancestor = true;
    app.auth_state = AuthState::Authenticating {
        request_seq: 1,
        handle: None,
        auth_url: None,
        mode: AuthMode::Pending,
    };
    app.deferred_startup.session =
        Some(crate::app::session_startup::DeferredSessionStartup::Load {
            session_id: "test-session".into(),
            session_cwd: None,
            chat_kind: false,
        });
    app.deferred_startup.worktree = true;

    let effects = dispatch(
        Action::TaskComplete(TaskResult::AuthComplete {
            request_seq: 1,
            meta: None,
        }),
        &mut app,
    );

    // --resume + --worktree: CreateWorktreeSession with the session ID.
    assert!(effects.iter().any(|e| matches!(
        e,
        Effect::CreateWorktreeSession {
            load_session_id: Some(_),
            ..
        }
    )));
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::LoadSession { .. }))
    );
}




#[test]
fn build_child_fork_marker_worktree_format() {
    let banner = build_child_fork_marker("child-sid", "parent-sid", true, Some("/resume"));
    assert!(
        banner.contains("Session child-sid"),
        "must contain child session id: {banner}"
    );
    assert!(
        banner.contains("forked from parent-sid"),
        "must contain full parent session id: {banner}"
    );
    assert!(
        banner.contains("use /resume to switch between sessions"),
        "must advertise /resume: {banner}"
    );
    assert!(
        !banner.contains("share cwd"),
        "worktree case must NOT have shared-cwd line: {banner}"
    );
}

#[test]
fn build_child_fork_marker_no_worktree_format() {
    let banner = build_child_fork_marker("child-sid", "parent-sid", false, Some("/resume"));
    let lines: Vec<&str> = banner.split('\n').collect();
    assert_eq!(lines.len(), 2, "expected 2-line banner, got: {banner}");
    assert!(
        lines[0].contains("Session child-sid"),
        "first line must contain child session id: {banner}"
    );
    assert!(
        lines[0].contains("forked from parent-sid"),
        "first line must contain full parent session id: {banner}"
    );
    assert!(
        lines[0].contains("/resume"),
        "first line must advertise /resume: {banner}"
    );
    assert!(
        lines[1].contains("both agents share cwd"),
        "second line must surface shared-cwd caveat: {banner}"
    );
}


#[test]
fn dispatch_fork_answered_re_dispatches_to_dispatch_fork_resolved() {
    let mut app = fork_test_app();
    let effects = dispatch(
        Action::ForkAnswered {
            worktree: false,
            directive: Some("answered directive".into()),
            persist_mode: None,
        },
        &mut app,
    );
    assert!(matches!(effects.as_slice(), [Effect::ForkSession { .. }]));
    let new_agent = app.agents.get(&AgentId(1)).expect("fork created");
    assert_eq!(
        new_agent.pending_first_prompt.as_deref(),
        Some("answered directive")
    );
}

/// `Action::ForkAnswered { worktree: true, .. }` produces the
/// `CreateWorktreeSession` effect (the "Yes" submit path).
#[test]
fn dispatch_fork_answered_worktree_true_emits_create_worktree_session() {
    let mut app = fork_test_app();
    let effects = dispatch(
        Action::ForkAnswered {
            worktree: true,
            directive: None,
            persist_mode: None,
        },
        &mut app,
    );
    assert!(matches!(
        effects.as_slice(),
        [Effect::CreateWorktreeSession { .. }]
    ));
}

/// `Action::ForkAnswered { worktree: false, .. }` produces the
/// `ForkSession` effect (the "No" submit path).
#[test]
fn dispatch_fork_answered_worktree_false_emits_fork_session() {
    let mut app = fork_test_app();
    let effects = dispatch(
        Action::ForkAnswered {
            worktree: false,
            directive: None,
            persist_mode: None,
        },
        &mut app,
    );
    assert!(matches!(effects.as_slice(), [Effect::ForkSession { .. }]));
}

#[test]
fn dispatch_fork_answered_with_persist_always_updates_mode_and_emits_effect() {
    let mut app = fork_test_app();
    app.fork_worktree_mode = crate::app::app_view::WorktreeMode::Ask;
    let effects = dispatch(
        Action::ForkAnswered {
            worktree: true,
            directive: None,
            persist_mode: Some(crate::app::app_view::WorktreeMode::Always),
        },
        &mut app,
    );
    assert_eq!(
        app.fork_worktree_mode,
        crate::app::app_view::WorktreeMode::Always
    );
    assert!(
        effects.iter().any(|e| matches!(
            e,
            Effect::PersistWorktreeMode {
                config_key: "fork_worktree_mode",
                ..
            }
        )),
        "expected PersistWorktreeMode with config_key fork_worktree_mode"
    );
}

#[test]
fn dispatch_new_session_answered_no_worktree_does_not_cancel_old_turn() {
    let mut app = new_session_test_app();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let effects = dispatch(
        Action::NewSessionAnswered {
            worktree: false,
            persist_mode: None,
        },
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateSession { .. })),
        "expected CreateSession, got {effects:?}"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::CancelTurn { .. }))
    );
    assert!(
        app.agents[&id].session.state.is_turn_running(),
        "old agent's turn must remain running"
    );
}

#[test]
fn dispatch_new_session_answered_worktree_does_not_cancel_old_turn() {
    let mut app = new_session_test_app();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    let effects = dispatch(
        Action::NewSessionAnswered {
            worktree: true,
            persist_mode: None,
        },
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateWorktreeSession { .. })),
        "expected CreateWorktreeSession, got {effects:?}"
    );
    // Old agent's turn must still be running.
    assert!(
        app.agents[&id].session.state.is_turn_running(),
        "old agent's turn must remain running"
    );
}

#[test]
fn dispatch_new_session_worktree_mode_always_skips_modal_and_creates_worktree() {
    let mut app = new_session_test_app();
    app.new_session_worktree_mode = crate::app::app_view::WorktreeMode::Always;
    let effects = dispatch(Action::NewSession, &mut app);
    assert!(
        app.agents[&AgentId(0)].question_view.is_none(),
        "modal must not open when new_session_worktree_mode is Always"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateWorktreeSession { .. })),
        "expected CreateWorktreeSession, got {effects:?}"
    );
}

#[test]
fn dispatch_new_session_worktree_mode_never_skips_modal_and_creates_in_cwd() {
    let mut app = new_session_test_app();
    app.new_session_worktree_mode = crate::app::app_view::WorktreeMode::Never;
    let effects = dispatch(Action::NewSession, &mut app);
    assert!(
        app.agents[&AgentId(0)].question_view.is_none(),
        "modal must not open when new_session_worktree_mode is Never"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateSession { .. })),
        "expected CreateSession, got {effects:?}"
    );
}

#[test]
fn fork_session_failed_pushes_turn_failed_block() {
    let mut app = fork_test_app();
    insert_placeholder_agent(&mut app, AgentId(1));
    let effects = dispatch(
        Action::TaskComplete(TaskResult::ForkSessionFailed {
            agent_id: AgentId(1),
            error: "shell oops".into(),
        }),
        &mut app,
    );
    assert!(effects.is_empty());
    // The placeholder agent stays in app.agents (no rollback).
    assert!(app.agents.contains_key(&AgentId(1)));
}

#[test]
fn translate_local_submit_yes_returns_worktree_true_action() {
    use crate::views::question_view::{LocalQuestionKind, QuestionViewState};
    use pi_tools::implementations::grok_build::ask_user_question::{
        Question, QuestionOption,
    };
    let q = Question {
        question: "?".into(),
        options: (0..2)
            .map(|i| QuestionOption {
                label: format!("opt{i}"),
                description: String::new(),
                preview: None,
                id: None,
            })
            .collect(),
        multi_select: Some(false),
        id: None,
    };
    let mut state = QuestionViewState::new(
        "x".into(),
        vec![q],
        crate::views::prompt_widget::StashedPrompt::default(),
    )
    .with_local_kind(LocalQuestionKind::Fork {
        directive: Some("d".into()),
    });
    // Set selection to option 0 ("Yes" in production).
    state.selections[0] = crate::views::question_view::QuestionSelection::Single(Some(0));
    let kind = state.local_kind.take().unwrap();
    let outcome = crate::app::agent_view::translate_local_submit_for_test(&state, kind, false);
    match outcome {
        crate::app::app_view::InputOutcome::Action(Action::ForkAnswered {
            worktree,
            directive,
            persist_mode,
        }) => {
            assert!(worktree);
            assert_eq!(directive.as_deref(), Some("d"));
            assert!(persist_mode.is_none());
        }
        other => panic!("expected ForkAnswered, got {other:?}"),
    }
}

#[test]
fn translate_local_submit_no_returns_worktree_false_action() {
    use crate::views::question_view::{LocalQuestionKind, QuestionViewState};
    use pi_tools::implementations::grok_build::ask_user_question::{
        Question, QuestionOption,
    };
    let q = Question {
        question: "?".into(),
        options: (0..2)
            .map(|_| QuestionOption {
                label: "opt".into(),
                description: String::new(),
                preview: None,
                id: None,
            })
            .collect(),
        multi_select: Some(false),
        id: None,
    };
    let mut state = QuestionViewState::new(
        "x".into(),
        vec![q],
        crate::views::prompt_widget::StashedPrompt::default(),
    )
    .with_local_kind(LocalQuestionKind::Fork { directive: None });
    // Option 1 = "No" -> worktree=false.
    state.selections[0] = crate::views::question_view::QuestionSelection::Single(Some(1));
    let kind = state.local_kind.take().unwrap();
    let outcome = crate::app::agent_view::translate_local_submit_for_test(&state, kind, false);
    match outcome {
        crate::app::app_view::InputOutcome::Action(Action::ForkAnswered {
            worktree,
            directive,
            persist_mode,
        }) => {
            assert!(!worktree);
            assert!(directive.is_none());
            assert!(persist_mode.is_none());
        }
        other => panic!("expected ForkAnswered, got {other:?}"),
    }
}

#[test]
fn translate_local_submit_always_returns_persist_always_for_fork() {
    use crate::views::question_view::{LocalQuestionKind, QuestionViewState};
    use pi_tools::implementations::grok_build::ask_user_question::{
        Question, QuestionOption,
    };
    let q = Question {
        question: "?".into(),
        options: (0..4)
            .map(|i| QuestionOption {
                label: format!("opt{i}"),
                description: String::new(),
                preview: None,
                id: None,
            })
            .collect(),
        multi_select: Some(false),
        id: None,
    };
    let mut state = QuestionViewState::new(
        "x".into(),
        vec![q],
        crate::views::prompt_widget::StashedPrompt::default(),
    )
    .with_local_kind(LocalQuestionKind::Fork { directive: None });
    state.selections[0] = crate::views::question_view::QuestionSelection::Single(Some(2));
    let kind = state.local_kind.take().unwrap();
    let outcome = crate::app::agent_view::translate_local_submit_for_test(&state, kind, false);
    match outcome {
        crate::app::app_view::InputOutcome::Action(Action::ForkAnswered {
            worktree,
            persist_mode,
            ..
        }) => {
            assert!(worktree);
            assert_eq!(
                persist_mode,
                Some(crate::app::app_view::WorktreeMode::Always)
            );
        }
        other => panic!("expected ForkAnswered with persist Always, got {other:?}"),
    }
}

#[test]
fn translate_local_submit_never_returns_persist_never_for_fork() {
    use crate::views::question_view::{LocalQuestionKind, QuestionViewState};
    use pi_tools::implementations::grok_build::ask_user_question::{
        Question, QuestionOption,
    };
    let q = Question {
        question: "?".into(),
        options: (0..4)
            .map(|i| QuestionOption {
                label: format!("opt{i}"),
                description: String::new(),
                preview: None,
                id: None,
            })
            .collect(),
        multi_select: Some(false),
        id: None,
    };
    let mut state = QuestionViewState::new(
        "x".into(),
        vec![q],
        crate::views::prompt_widget::StashedPrompt::default(),
    )
    .with_local_kind(LocalQuestionKind::Fork { directive: None });
    state.selections[0] = crate::views::question_view::QuestionSelection::Single(Some(3));
    let kind = state.local_kind.take().unwrap();
    let outcome = crate::app::agent_view::translate_local_submit_for_test(&state, kind, false);
    match outcome {
        crate::app::app_view::InputOutcome::Action(Action::ForkAnswered {
            worktree,
            persist_mode,
            ..
        }) => {
            assert!(!worktree);
            assert_eq!(
                persist_mode,
                Some(crate::app::app_view::WorktreeMode::Never)
            );
        }
        other => panic!("expected ForkAnswered with persist Never, got {other:?}"),
    }
}

