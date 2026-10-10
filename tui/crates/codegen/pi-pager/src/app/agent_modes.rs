//! The permission modes the agent behind this pager honours.
//!
//! The pager came with several ways to choose how tool calls are approved, all of them implemented
//! by the Rust runtime it used to embed: Normal (ask), Plan, Auto, Always-Approve, the settings
//! page's "Default" (a second spelling of ask) and the six `--permission-mode` values. The agent
//! now behind it is the Python one (`pi_agent_cli`), and it does two things: it asks, or it approves
//! everything (`pi/yolo_mode_changed` carrying `ask` or `always-approve`). What the other modes do
//! against the real `zypi` is in the plan (section 10.11, appendix A, 1.P3) and in
//! `scripts/tui_pty/mode_probe.py`:
//!
//! - **Plan** restricts nothing. `session/set_mode` has no handler on the Python side, so the call
//!   fails and is only logged, while the UI goes on saying `plan`.
//! - **Auto** never asks, because Python treats `auto` like `always-approve`, while the screen
//!   promises a classifier that left with the Rust runtime.
//! - **Default** is dropped by Python (`default` is not one of the three values it recognises in
//!   `pi/yolo_mode_changed`): after Always-Approve it shows Default and the agent still never asks.
//!
//! A mode that is offered and does not work is worse than one that is missing, so these three are
//! hidden: not on the Shift+Tab ring, not in the settings pickers, not taken by `--permission-mode`,
//! and a mode saved by an earlier version is shown as Ask. The code behind them is kept, behind the
//! constants below; flip one when the agent honours the mode (1.P3 (b)) and the mode comes back
//! with its tests.

/// The agent honours Plan: it handles `session/set_mode` and plan mode restricts its tools.
pub(crate) const PLAN: bool = false;

/// The agent honours Auto: it classifies tool calls instead of asking about all or none.
pub(crate) const AUTO: bool = false;

/// The agent tells "Default" from Ask. Until it does, Default is a second name for Ask that Python
/// drops on the floor.
pub(crate) const DEFAULT: bool = false;

/// The values `--permission-mode` takes: the ones the agent can act on.
///
/// `default` means no flag and `bypassPermissions` means `--always-approve`. `acceptEdits` and
/// `dontAsk` have no counterpart anywhere in the pager or the agent, so they are never accepted.
pub(crate) fn permission_mode_flag_values() -> Vec<&'static str> {
    let mut values = vec!["default", "bypassPermissions"];
    if PLAN {
        values.push("plan");
    }
    if AUTO {
        values.push("auto");
    }
    values
}

/// Whether this launch starts in Auto: what the config and the flags ask for
/// ([`pi_shell::util::config::effective_auto_for_launch_interactive`], whose soft default is Auto
/// when nothing selects a mode), unless the agent does not honour Auto.
pub(crate) fn launch_auto(
    yolo_flag: bool,
    permission_mode_flag: Option<&str>,
    remote_permission_mode: Option<&str>,
) -> bool {
    AUTO && pi_shell::util::config::effective_auto_for_launch_interactive(
        yolo_flag,
        permission_mode_flag,
        remote_permission_mode,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The shipped agent honours two modes. If this fails because a constant was flipped on
    /// purpose, the mode's own tests, `--permission-mode` and the plan's appendix A change with it.
    #[test]
    fn the_python_agent_honours_neither_plan_auto_nor_default() {
        assert!(!PLAN);
        assert!(!AUTO);
        assert!(!DEFAULT);
        assert_eq!(
            permission_mode_flag_values(),
            vec!["default", "bypassPermissions"]
        );
    }

    #[test]
    fn launch_never_starts_in_auto_while_the_agent_does_not_honour_it() {
        // The interactive soft default is Auto; with nothing asked for it must still be Ask.
        assert!(!launch_auto(false, None, None));
        assert!(!launch_auto(false, Some("auto"), None));
        assert!(!launch_auto(false, None, Some("auto")));
    }
}
