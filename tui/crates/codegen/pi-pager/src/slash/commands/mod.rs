//! Concrete slash command implementations.
//!
//! Each command lives in its own submodule. This module re-exports
//! command structs and provides `builtin_commands()` for registry
//! construction.
//!
//! The builtin set is the pi-python TUI's own: standard-ACP session commands
//! (`/new`, `/resume`, `/quit`, `/model`) plus local chrome (`/help`,
//! `/theme`, `/settings`, `/multiline`, `/home`). Everything else the agent
//! offers arrives over ACP (`available_commands_update`) and is registered
//! by [`CommandRegistry::set_acp_commands`](super::registry::CommandRegistry).
pub mod effort_levels;
pub mod exit;
pub mod help;
pub mod home;
pub mod model;
pub mod multiline;
pub mod new;
pub mod resume;
pub mod settings_cmd;
pub mod theme;
use super::command::SlashCommand;
use std::sync::Arc;
/// All pager-local builtin commands, in menu order: this vec breaks ties after MRU recency and tags, so moving an entry moves it in the menu.
///
/// This is the single source of truth for the builtin command set. The registry is constructed from this list.
pub fn builtin_commands() -> Vec<Arc<dyn SlashCommand>> {
    vec![
        Arc::new(settings_cmd::SettingsCommand),
        Arc::new(new::NewCommand),
        Arc::new(model::ModelCommand),
        Arc::new(resume::ResumeCommand),
        Arc::new(theme::ThemeCommand),
        Arc::new(multiline::MultilineCommand),
        Arc::new(home::HomeCommand),
        Arc::new(help::HelpCommand),
        Arc::new(exit::ExitCommand),
    ]
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::acp::model_state::ModelState;
    use crate::app::actions::Action;
    use crate::slash::command::{CommandExecCtx, CommandResult, SlashCommand};
    use crate::slash::registry::CommandRegistry;
    use agent_client_protocol as acp;
    /// Build a ModelState with two models for testing.
    fn sample_models() -> ModelState {
        let mut models = ModelState::default();
        let id_fast = acp::ModelId::new(Arc::from("grok-4.5"));
        models.available.insert(
            id_fast.clone(),
            acp::ModelInfo::new(id_fast.clone(), "Grok 4.5".to_string()),
        );
        let id_pro = acp::ModelId::new(Arc::from("grok-4.3"));
        models.available.insert(
            id_pro.clone(),
            acp::ModelInfo::new(id_pro.clone(), "Grok 4.3".to_string()),
        );
        models.current = Some(id_fast);
        models
    }
    static DEFAULT_BUNDLE_STATE: crate::app::bundle::BundleState =
        crate::app::bundle::BundleState {
            has_cache: false,
            version: String::new(),
            personas: Vec::new(),
            roles: Vec::new(),
            agents: Vec::new(),
            skills: Vec::new(),
            persona_details: Vec::new(),
            role_details: Vec::new(),
        };
    pub(crate) fn make_ctx(models: &ModelState) -> CommandExecCtx<'_> {
        CommandExecCtx {
            models,
            session_id: None,
            bundle_state: &DEFAULT_BUNDLE_STATE,
            screen_mode: crate::app::ScreenMode::Inline,
            billing_surface_visible: true,
            usage_command_visible: true,
            pager_state: crate::settings::PagerLocalSnapshot {
                multiline_mode: false,
                yolo_mode: false,
                ..crate::settings::PagerLocalSnapshot::default()
            },
        }
    }
    /// The builtin set is the whitelist: nothing else is registered, and
    /// every entry of the whitelist constant is present.
    #[test]
    fn builtin_set_is_exactly_the_standard_whitelist() {
        let builtins = builtin_commands();
        let mut names: Vec<&str> = builtins.iter().map(|c| c.name()).collect();
        names.sort_unstable();
        let mut expected: Vec<&str> = crate::slash::registry::PI_STANDARD_SLASH_NAMES.to_vec();
        expected.sort_unstable();
        assert_eq!(names, expected);
    }
    #[test]
    fn builtin_registry_lookup_by_canonical() {
        let reg = CommandRegistry::new(builtin_commands());
        for name in [
            "quit",
            "new",
            "resume",
            "help",
            "theme",
            "settings",
            "multiline",
            "model",
            "home",
        ] {
            assert!(reg.get(name).is_some(), "/{name} should be registered");
        }
    }
    #[test]
    fn removed_commands_are_not_registered() {
        let reg = CommandRegistry::new(builtin_commands());
        for name in [
            "dashboard",
            "compact",
            "fork",
            "mcps",
            "plugins",
            "hooks",
            "tasks",
            "queue",
            "rename",
            "release-notes",
            "workflow",
            "feedback",
        ] {
            assert!(reg.get(name).is_none(), "/{name} must not be registered");
        }
    }
    #[test]
    fn builtin_registry_lookup_by_alias() {
        let reg = CommandRegistry::new(builtin_commands());
        for (alias, canonical) in [
            ("exit", "quit"),
            ("clear", "new"),
            ("m", "model"),
            ("welcome", "home"),
            ("t", "theme"),
            ("ml", "multiline"),
            ("config", "settings"),
            ("prefs", "settings"),
            ("preferences", "settings"),
        ] {
            let cmd = reg
                .get(alias)
                .unwrap_or_else(|| panic!("/{alias} should resolve"));
            assert_eq!(cmd.name(), canonical, "/{alias}");
        }
    }
    #[test]
    fn aliases_resolve_to_same_command() {
        let reg = CommandRegistry::new(builtin_commands());
        let exit_cmd = reg.get("exit").unwrap();
        let quit_cmd = reg.get("quit").unwrap();
        assert_eq!(exit_cmd.name(), quit_cmd.name());
    }
    #[test]
    fn exit_returns_quit_action() {
        let models = ModelState::default();
        let mut ctx = make_ctx(&models);
        let cmd = exit::ExitCommand;
        let result = cmd.run(&mut ctx, "");
        assert!(matches!(result, CommandResult::Action(Action::Quit)));
    }
    #[test]
    fn new_returns_new_session_action() {
        let models = ModelState::default();
        let mut ctx = make_ctx(&models);
        let cmd = new::NewCommand;
        let result = cmd.run(&mut ctx, "");
        assert!(matches!(result, CommandResult::Action(Action::NewSession)));
    }
    #[test]
    fn home_returns_exit_session_action() {
        let models = ModelState::default();
        let mut ctx = make_ctx(&models);
        let cmd = home::HomeCommand;
        let result = cmd.run(&mut ctx, "");
        assert!(matches!(result, CommandResult::Action(Action::ExitSession)));
    }
    /// Bare `/model <name>` → `SetDefaultModel` (switch + persist).
    /// `/model <name> <effort>` → `SwitchModel` (session-scoped).
    #[test]
    fn model_resolves_by_display_name() {
        let models = sample_models();
        let mut ctx = make_ctx(&models);
        let cmd = model::ModelCommand;
        let result = cmd.run(&mut ctx, "Grok 4.5");
        match result {
            CommandResult::Action(Action::SetDefaultModel(id)) => {
                assert_eq!(id.0.as_ref(), "grok-4.5");
            }
            other => panic!("expected Action(SetDefaultModel), got {other:?}"),
        }
    }
    #[test]
    fn model_resolves_by_model_id() {
        let models = sample_models();
        let mut ctx = make_ctx(&models);
        let cmd = model::ModelCommand;
        let result = cmd.run(&mut ctx, "grok-4.3");
        match result {
            CommandResult::Action(Action::SetDefaultModel(id)) => {
                assert_eq!(id.0.as_ref(), "grok-4.3");
            }
            other => panic!("expected Action(SetDefaultModel), got {other:?}"),
        }
    }
    #[test]
    fn model_resolves_case_insensitively() {
        let models = sample_models();
        let mut ctx = make_ctx(&models);
        let cmd = model::ModelCommand;
        let result = cmd.run(&mut ctx, "grok 4.5");
        match result {
            CommandResult::Action(Action::SetDefaultModel(id)) => {
                assert_eq!(id.0.as_ref(), "grok-4.5");
            }
            other => panic!("expected Action(SetDefaultModel), got {other:?}"),
        }
    }
    #[test]
    fn model_invalid_arg_returns_error() {
        let models = sample_models();
        let mut ctx = make_ctx(&models);
        let cmd = model::ModelCommand;
        let result = cmd.run(&mut ctx, "nonexistent-model");
        match result {
            CommandResult::Error(msg) => {
                assert!(
                    msg.contains("nonexistent-model"),
                    "error should contain the arg"
                );
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }
    #[test]
    fn model_empty_arg_returns_error() {
        let models = sample_models();
        let mut ctx = make_ctx(&models);
        let cmd = model::ModelCommand;
        let result = cmd.run(&mut ctx, "");
        assert!(matches!(result, CommandResult::Error(_)));
    }
    #[test]
    fn model_whitespace_only_arg_returns_error() {
        let models = sample_models();
        let mut ctx = make_ctx(&models);
        let cmd = model::ModelCommand;
        let result = cmd.run(&mut ctx, "   ");
        assert!(matches!(result, CommandResult::Error(_)));
    }
    #[test]
    fn model_suggest_args_returns_available_models() {
        let models = sample_models();
        let ctx = crate::slash::command::AppCtx {
            models: &models,
            cwd: std::path::Path::new("."),
            has_session_announcements: false,
            billing_surface_visible: true,
            usage_command_visible: true,
            workflows_available: true,
            saved_workflows: &[],
            workflow_runs: &[],
            screen_mode: crate::app::ScreenMode::Fullscreen,
            current_title: None,
        };
        let cmd = model::ModelCommand;
        let items = cmd.suggest_args(&ctx, "").expect("should have suggestions");
        assert_eq!(items.len(), 2);
        assert!(
            items
                .iter()
                .any(|i| i.display.starts_with("Grok 4.5") && i.insert_text == "Grok 4.5")
        );
        assert!(
            items
                .iter()
                .any(|i| i.display == "Grok 4.3" && i.insert_text == "Grok 4.3")
        );
    }
    #[test]
    fn model_suggest_args_empty_models_returns_none() {
        let models = ModelState::default();
        let ctx = crate::slash::command::AppCtx {
            models: &models,
            cwd: std::path::Path::new("."),
            has_session_announcements: false,
            billing_surface_visible: true,
            usage_command_visible: true,
            workflows_available: true,
            saved_workflows: &[],
            workflow_runs: &[],
            screen_mode: crate::app::ScreenMode::Fullscreen,
            current_title: None,
        };
        let cmd = model::ModelCommand;
        assert!(cmd.suggest_args(&ctx, "").is_none());
    }
}
