//! The permission modes the agent behind this pager honours.
//!
//! The pager came with several ways to choose how tool calls are approved, all of them implemented
//! by the Rust runtime it used to embed: Normal (ask), Plan, Auto, Always-Approve, the settings
//! page's "Default" (a second spelling of ask) and the six `--permission-mode` values. The agent
//! now behind it is the Python one (`pi_agent_cli`). It asks (`ask`), approves everything
//! (`always-approve`), or runs Auto (`auto`: rules decide which calls are clearly in scope and the
//! rest asks; it never refuses by itself; design: `docs/specs/2026-11-02-auto-mode-design.md`).
//! The pager tells it with `pi/yolo_mode_changed` and, at the start of a session, with
//! `_meta.yoloMode` / `_meta.autoMode`. What the other modes do against the real `zypi` is in the
//! plan (section 10.11, appendix A, 1.P3) and in `scripts/tui_pty/mode_probe.py`:
//!
//! - **Plan** restricts nothing. `session/set_mode` has no handler on the Python side, so the call
//!   fails and is only logged, while the UI goes on saying `plan`.
//! - **Default** is dropped by Python (`default` is not one of the three values it recognises in
//!   `pi/yolo_mode_changed`): after Always-Approve it shows Default and the agent still never asks.
//!
//! A mode that is offered and does not work is worse than one that is missing, so these two are
//! hidden: not on the Shift+Tab ring, not in the settings pickers, not taken by `--permission-mode`,
//! and a mode saved by an earlier version is shown as Ask. The code behind them is kept, behind the
//! constants below; flip one when the agent honours the mode (1.P3 (b)) and the mode comes back
//! with its tests.
//!
//! Auto is offered, but a fresh launch does not start in it: it starts in Ask unless the user chose
//! Auto (`--permission-mode auto`, or `[ui] permission_mode = "auto"` saved by an earlier pick).
//! The interactive soft default of the vendored resolver is Auto; [`launch_auto`] does not use it.

/// The agent honours Plan: it handles `session/set_mode` and plan mode restricts its tools.
pub(crate) const PLAN: bool = false;

/// The agent honours Auto: it judges each tool call, runs the ones that are clearly in scope and
/// asks about the rest.
pub(crate) const AUTO: bool = true;

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

/// Whether this launch starts in Auto: only when the user chose it, by the flags or the config
/// ([`pi_shell::util::config::effective_auto_for_launch`]). Nothing chosen means Ask: the
/// interactive variant of that resolver soft-defaults to Auto, and a new mode is not the default
/// safety level (decision D5b of the Auto design). Never true when the agent does not honour Auto.
pub(crate) fn launch_auto(
    yolo_flag: bool,
    permission_mode_flag: Option<&str>,
    remote_permission_mode: Option<&str>,
) -> bool {
    AUTO && pi_shell::util::config::effective_auto_for_launch(
        yolo_flag,
        permission_mode_flag,
        remote_permission_mode,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The shipped agent honours Ask, Always-Approve and Auto. If this fails because a constant was
    /// flipped on purpose, the mode's own tests, `--permission-mode` and the plan's appendix A
    /// change with it.
    #[test]
    fn the_python_agent_honours_auto_and_neither_plan_nor_default() {
        assert!(!PLAN);
        assert!(AUTO);
        assert!(!DEFAULT);
        assert_eq!(
            permission_mode_flag_values(),
            vec!["default", "bypassPermissions", "auto"]
        );
    }

    /// D5b: a fresh `zypi` starts in Ask. The vendored interactive resolver would pick Auto when
    /// nothing selects a mode, so this test reads the real resolver. The first assertion is the
    /// control: it needs the Auto gate open, and an explicit `--permission-mode auto` must then
    /// launch Auto. Without it, a closed gate would make every `false` below pass for the wrong
    /// reason. The ones after it need a home with no `[ui] permission_mode` (the baseline run gives
    /// the tests one).
    #[test]
    fn a_launch_starts_in_ask_unless_auto_was_chosen() {
        assert!(
            launch_auto(false, Some("auto"), None),
            "the Auto gate is closed (GROK_AUTO_PERMISSION_MODE, [auto_mode] enabled): this test says nothing"
        );
        assert!(
            !launch_auto(false, None, None),
            "nothing chosen must launch in Ask, not in the interactive soft default"
        );
        assert!(!launch_auto(false, Some("default"), None));
        assert!(!launch_auto(false, Some("bypassPermissions"), None));
        // `--always-approve` alone, and with `--permission-mode auto`, are Always-Approve.
        assert!(!launch_auto(true, None, None));
        assert!(!launch_auto(true, Some("auto"), None));
    }
}
