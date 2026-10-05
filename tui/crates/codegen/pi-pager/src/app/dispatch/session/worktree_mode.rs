//! Worktree-mode persistence helpers shared by the new-session question and
//! the worktree-session dispatchers.
use crate::app::actions::Effect;
/// If `persist_mode` is `Some`, write `mode` into `*field` and append
/// a [`Effect::PersistWorktreeMode`] to `effects` with the given
/// `config_key`.
pub(in crate::app::dispatch) fn apply_persist_worktree_mode(
    field: &mut crate::app::app_view::WorktreeMode,
    effects: &mut Vec<Effect>,
    persist_mode: Option<crate::app::app_view::WorktreeMode>,
    config_key: &'static str,
) {
    if let Some(mode) = persist_mode {
        *field = mode;
        effects.push(Effect::PersistWorktreeMode { mode, config_key });
    }
}
/// Build the two persistence options for the new-session worktree question
/// modal ("Always worktree" / "Never worktree").
pub(super) fn worktree_persist_options()
-> [pi_tools::implementations::grok_build::ask_user_question::QuestionOption; 2] {
    use pi_tools::implementations::grok_build::ask_user_question::QuestionOption;
    [
        QuestionOption {
            label: "Always worktree".into(),
            description: "Use worktree and stop asking (reset in config.toml)".into(),
            preview: None,
            id: None,
        },
        QuestionOption {
            label: "Never worktree".into(),
            description: "Skip worktree and stop asking (reset in config.toml)".into(),
            preview: None,
            id: None,
        },
    ]
}
