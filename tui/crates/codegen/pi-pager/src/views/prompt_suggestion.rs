//! Next-prompt suggestion controller (tab autocomplete ghost text).
//!
//! After a turn completes, the pager asks the shell (`suggestPrompt`)
//! to predict the user's likely next prompt. The prediction renders as dim
//! ghost text in the (empty) prompt input:
//!
//! - **Tab** or **Right arrow** accepts it (the ghost only shows with the
//!   cursor at end-of-text, where Right is otherwise a no-op — the fish/zsh
//!   autosuggestion convention).
//! - Typing a matching prefix *shrinks* the ghost; typing it out fully
//!   consumes it; any divergent text hides it (it comes back if the user
//!   clears the input, matching common agent-CLI autosuggest behavior).
//! - **Esc** on an empty prompt dismisses it for the rest of the turn.
//!
//! Visibility is *derived* from the current prompt text each frame
//! ([`PromptSuggestionController::ghost_for`]) rather than mutated on each
//! keystroke — there is no per-keystroke state machine to drift. Stale
//! responses are discarded via a generation counter, mirroring
//! `SuggestionController` (shell command suggestions).

/// Env override for the whole feature: `GROK_PROMPT_SUGGESTIONS=0/1`.
/// When unset, the persisted `prompt_suggestions` setting applies.
pub const PROMPT_SUGGESTIONS_ENV: &str = "GROK_PROMPT_SUGGESTIONS";

/// Controller for the predicted-next-prompt ghost text.
#[derive(Debug, Default)]
pub struct PromptSuggestionController {
    /// Full suggestion text from the model. Empty = no suggestion.
    full_text: String,
    /// Request generation counter; responses carrying a stale generation are
    /// discarded (a newer turn ended, or the suggestion was invalidated).
    generation: u64,
    /// Set when the user dismissed the current suggestion (Esc). Cleared by
    /// the next loaded suggestion.
    dismissed: bool,
    /// Set once the `shown` telemetry impression for the current suggestion
    /// has been logged. Visibility is derived per frame ([`Self::ghost_for`]),
    /// so a suggestion can become visible *after* load (divergent draft
    /// cleared, gate re-opened) — this latch makes the impression fire
    /// exactly once per installed suggestion, at first actual visibility.
    /// Re-armed by [`Self::on_loaded`]; deliberately **not** re-armed by
    /// [`Self::dismiss`]/[`Self::clear`] (the suggestion is gone).
    shown_logged: bool,
    /// Whether the feature is enabled. Resolved via
    /// `GROK_PROMPT_SUGGESTIONS` env var, falling back to the persisted
    /// `prompt_suggestions` setting.
    pub enabled: bool,
}

impl PromptSuggestionController {
    pub fn new() -> Self {
        Self {
            full_text: String::new(),
            generation: 0,
            dismissed: false,
            shown_logged: false,
            enabled: resolve_enabled(),
        }
    }

    /// The ghost text to render for the current prompt text, if any.
    ///
    /// Derived: the suggestion is visible iff the current text is a proper
    /// prefix of it (including the empty prompt). Typing matching characters
    /// shrinks the ghost; typing it out fully (or diverging) hides it;
    /// clearing the input brings the full suggestion back.
    pub fn ghost_for(&self, text: &str) -> Option<&str> {
        if !self.enabled || self.dismissed || self.full_text.is_empty() {
            return None;
        }
        let rest = self.full_text.strip_prefix(text)?;
        if rest.is_empty() { None } else { Some(rest) }
    }

    /// Accept the suggestion against the current prompt text. Returns the
    /// remainder to insert and clears the suggestion.
    pub fn accept(&mut self, text: &str) -> Option<String> {
        let rest = self.ghost_for(text)?.to_owned();
        self.clear();
        Some(rest)
    }

    /// Dismiss the current suggestion (Esc) until a new one loads.
    pub fn dismiss(&mut self) {
        self.dismissed = true;
    }

    /// Drop the suggestion and invalidate any in-flight fetch (turn started,
    /// prompt sent, session switched...).
    pub fn clear(&mut self) {
        self.full_text.clear();
        self.generation = self.generation.wrapping_add(1);
    }

    /// Whether a (non-dismissed) suggestion is loaded, regardless of the
    /// current prompt text.
    pub fn has_suggestion(&self) -> bool {
        self.enabled && !self.dismissed && !self.full_text.is_empty()
    }

    /// Latch the `shown` impression for the current suggestion: returns
    /// `true` exactly once per installed suggestion (the caller logs the
    /// telemetry event on `true`). Callers check actual visibility first;
    /// this only guards against double-logging when visibility — which is
    /// re-derived per frame — recurs or is re-checked on a later path.
    pub fn mark_shown_logged(&mut self) -> bool {
        !std::mem::replace(&mut self.shown_logged, true)
    }

    /// Whether the `shown` impression for the current suggestion has been
    /// logged already (read-only companion to [`Self::mark_shown_logged`]).
    #[cfg(test)]
    pub(crate) fn shown_logged(&self) -> bool {
        self.shown_logged
    }

    #[cfg(test)]
    pub(crate) fn set_suggestion_for_test(&mut self, text: &str) {
        self.enabled = true;
        self.dismissed = false;
        self.shown_logged = false;
        self.full_text = text.to_owned();
    }
}

/// Resolve the enabled state: env override wins, then the persisted
/// `prompt_suggestions` setting (default on). The env var is read once per
/// process; the setting is a thread-local cache, so this is cheap enough for
/// per-frame calls.
pub fn resolve_enabled() -> bool {
    static ENV_OVERRIDE: std::sync::OnceLock<Option<bool>> = std::sync::OnceLock::new();
    ENV_OVERRIDE
        .get_or_init(|| pi_config::env_bool(PROMPT_SUGGESTIONS_ENV))
        .unwrap_or_else(crate::appearance::cache::load_prompt_suggestions)
}

/// Content-free size metadata for acceptance-rate telemetry: `(chars, words)`
/// of the full suggestion text. Never log the text itself.
pub fn suggestion_size(text: &str) -> (usize, usize) {
    (text.chars().count(), text.split_whitespace().count())
}

#[cfg(test)]
mod tests {

}
