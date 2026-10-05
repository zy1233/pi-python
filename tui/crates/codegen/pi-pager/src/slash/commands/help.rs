//! `/help` -- open the command palette (the command + shortcut browser).
//!
//! In minimal mode there's no always-visible footer of hints, so `/help` is the
//! discoverable entry point (advertised in the status line). It opens the same
//! command palette as Ctrl+P, hosted inline by the overlay app-modal host.

use crate::app::actions::Action;
use crate::slash::command::{CommandExecCtx, CommandResult, SlashCommand};

/// Open the command palette.
pub struct HelpCommand;

impl SlashCommand for HelpCommand {
    fn name(&self) -> &str {
        "help"
    }

    fn description(&self) -> &str {
        "Browse commands and keyboard shortcuts"
    }

    fn run(&self, _ctx: &mut CommandExecCtx, _args: &str) -> CommandResult {
        CommandResult::Action(Action::OpenCommandPalette)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::acp::model_state::ModelState;
    use crate::settings::PagerLocalSnapshot;

    #[test]
    fn dispatches_open_command_palette() {
        let models = ModelState::default();
        let mut ctx = CommandExecCtx {
            models: &models,
            screen_mode: crate::app::ScreenMode::Minimal,
            pager_state: PagerLocalSnapshot::default(),
        };
        assert!(matches!(
            HelpCommand.run(&mut ctx, ""),
            CommandResult::Action(Action::OpenCommandPalette)
        ));
    }
}
