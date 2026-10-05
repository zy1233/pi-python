/// A server-authoritative running prompt that drained into the running slot
/// while the previous turn was still finishing locally (FIFO handoff
/// race). Stashed on [`AppView::pending_running_adoptions`] and consumed by the
/// `PromptResponse` handler after `finish_turn` clears `current_prompt_id`.
#[derive(Debug, Clone)]
pub(crate) struct PendingRunningAdoption {
    /// The `prompt_id` the leader reported as `running_prompt_id`.
    pub prompt_id: String,
    /// The queued prompt's text (for the turn-start shim's user block), if the
    /// pager knew about the prompt. `None` for prompts queued by other clients.
    pub text: Option<String>,
    /// Combined-turn display segments (len ≥ 2); shim paints one bubble each.
    pub combined_texts: Option<Vec<String>>,
    /// The adopted entry's `kind` (`"prompt"`/`"bash"`/`"verification"`/…),
    /// which selects the turn-start shim's display block + focus flag.
    pub kind: String,
}

