//! The settings the agent behind this pager backs.
//!
//! The settings page came with the Rust runtime that the Python agent replaced. Most of its rows
//! steer the pager (theme, scrolling, folds) and work as before. Eight rows steered that runtime,
//! or a feature that left with it. Against the real `zypi` they change nothing, or they change
//! something other than what the row says (`scripts/tui_pty/settings_probe.py` measures them; the
//! plan, decision 14, has the table):
//!
//! - `remember_tool_approvals`: the Python agent offers "Allow once" and "Reject" only. No
//!   "always allow" answer exists, so there is nothing to remember.
//! - `default_selected_permission`: it chooses which of those answers a prompt selects first. Only
//!   `reject` has an effect. The other values name rows that do not exist.
//! - `default_model`: it saves `[models].default`, and the next start ignores that key. The agent
//!   reads its model from `agent.toml`.
//! - `toolset.ask_user_question.timeout_enabled`: the agent has no `ask_user_question` tool.
//! - `fork_secondary_model`: forking a session left with the Rust runtime.
//! - `prompt_suggestions`: nothing in the pager creates a suggestion.
//! - `hunk_tracker_mode`: only the Rust runtime read it.
//! - `auto_update`: no code starts a background update check.
//!
//! A row that is offered and does nothing is worse than one that is missing, so the page does not
//! show these eight. The code behind them stays, behind the constant below. A value that an earlier
//! version saved stays in `config.toml`, and `reject` for `default_selected_permission` still works
//! there. Turn the constant off when the agent backs the rows again, or take a key out of
//! [`UNBACKED`] when its setting works.

/// The settings page does not offer the rows named in [`UNBACKED`].
pub(crate) const HIDE_UNBACKED: bool = true;

/// The keys of the settings that the Python agent does not back (see the module comment).
pub(crate) const UNBACKED: &[&str] = &[
    "remember_tool_approvals",
    "default_selected_permission",
    "default_model",
    "toolset.ask_user_question.timeout_enabled",
    "fork_secondary_model",
    "prompt_suggestions",
    "hunk_tracker_mode",
    "auto_update",
];

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::SettingsRegistry;

    /// The shipped agent backs none of the eight. If this fails because a setting works again,
    /// take its key out of `UNBACKED` and give the setting its own test.
    #[test]
    fn the_shipped_pager_hides_the_unbacked_settings() {
        assert!(HIDE_UNBACKED);
        assert_eq!(UNBACKED.len(), 8);
    }

    /// A key that the registry does not know hides nothing, so a rename must not pass unnoticed.
    #[test]
    fn every_unbacked_key_is_a_registered_setting() {
        let registry = SettingsRegistry::defaults();
        for key in UNBACKED {
            assert!(registry.find(*key).is_some(), "{key} is not registered");
        }
    }

    #[test]
    fn no_key_is_listed_twice() {
        let mut keys = UNBACKED.to_vec();
        keys.sort_unstable();
        keys.dedup();
        assert_eq!(keys.len(), UNBACKED.len());
    }
}
