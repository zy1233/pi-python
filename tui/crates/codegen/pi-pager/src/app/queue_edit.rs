//! Queued-prompt editing (`PromptMode::EditingQueued`) state machine.
//!
//! Extracted from `agent_view.rs` as a sibling `impl AgentView` block (same
//! pattern as pi-shell's `compaction.rs`): entry from the queue pane,
//! editing-mode key intercepts, the dirty-edit focus lock, and the
//! exit/cleanup paths.
//!
//! Stash invariant: `stashed_prompt` is set exactly once on entry
//! (`enter_queue_edit`) and `take()`n exactly once on exit —
//! `exit_editing_mode` is the sole restore owner: every exit (including
//! lost-row cancel) restores the draft exactly once.
//!
//! A dirty pane switch is blocked and never arms the undrawn `EditConfirm`
//! modal, which would otherwise capture all input invisibly.

use crossterm::event::{KeyCode, KeyEvent};

use crate::key;
use crate::views::modal::{ActiveModal, EditConfirmResult, ModalConfirmation};

use super::actions::Action;
use super::agent_view::{AgentPane, AgentView, PromptInputMode};
use super::app_view::InputOutcome;

/// State of the prompt widget's editing context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PromptMode {
    /// Normal mode: typing a new prompt to send.
    Normal,
    /// Editing a queued prompt.
    EditingQueued {
        /// The `QueuedPrompt.id` (monotonic counter) of the row being edited;
        /// also the stable selection id in [`AgentView::queue`].
        id: u64,
        /// Snapshot of the original text (for dirty detection).
        original: String,
        /// Kind snapshot taken when the edit started.
        kind: crate::app::agent::QueueEntryKind,
    },
}

impl AgentView {
    /// Editing-mode key intercepts for the prompt pane.
    ///
    /// Bare Enter saves, Esc (or Ctrl-C on empty) cancels. Shift/Alt+Enter
    /// inserts a newline (same as the normal composer) and must not save.
    /// Apple Terminal Cmd/Shift/Opt+Enter is rescued inside `is_mod_enter`
    /// via CoreGraphics — not a universal Cmd+Enter binding.
    ///
    /// Returns `None` when not editing or unhandled — must fall through to the widget.
    pub(super) fn handle_editing_queued_key(&mut self, key: &KeyEvent) -> Option<InputOutcome> {
        if let PromptMode::EditingQueued { id, .. } = &self.prompt_mode {
            let id = *id;
            let ctrl_c_empty = key!('c', CONTROL).matches(key) && self.prompt.text().is_empty();

            // Before bare-Enter save: Shift/Alt flags, or Apple Terminal bare
            // Enter with Cmd/Shift/Opt held (CoreGraphics rescue in is_mod_enter).
            if crate::input::is_mod_enter(key) {
                self.prompt.textarea.insert_str("\n");
                return Some(InputOutcome::Changed);
            }
            if key!(Enter).matches(key) && !self.prompt.text().trim().is_empty() {
                return Some(self.save_edited_queued_row(id, true));
            }
            if key.code == KeyCode::Esc || ctrl_c_empty {
                self.exit_editing_mode();
                return Some(InputOutcome::Action(Action::DrainQueue));
            }
        }
        None
    }

    /// Dirty-edit focus lock checked by `set_active_pane`.
    ///
    /// `None` = not editing / no lock — the caller proceeds with the normal
    /// pane switch. `Some(switched)` = the lock handled the switch: `false`
    /// when blocked behind the confirm modal, `true` after a clean exit.
    pub(super) fn editing_lock_on_pane_switch(&mut self, target: AgentPane) -> Option<bool> {
        if let PromptMode::EditingQueued { ref original, .. } = self.prompt_mode
            && target != AgentPane::Prompt
        {
            let dirty = self.prompt.text() != original;
            if dirty {
                // Block the switch; never arm `EditConfirm` here (it has no draw
                // arm, so it would capture all input invisibly). Resolve with Enter/Esc.
                // Clear the overlay-focus flip toggle callers set before switching.
                match target {
                    AgentPane::Queue => self.queue.overlay.focused = false,
                    AgentPane::Todo => self.todo.overlay.focused = false,
                    _ => {}
                }
                self.show_toast("Editing a queued prompt: press Enter to save, Esc to discard");
                return Some(false); // blocked, no modal armed
            }
            // Clean edit — silently exit editing mode.
            self.exit_editing_mode();
            self.active_pane = target;
            return Some(true);
        }
        None
    }

    /// Resolve an `EditConfirm` modal keypress (the modal has already been
    /// `take()`n out of `active_modal` by `handle_modal_key`).
    pub(super) fn handle_edit_confirm_choice(
        &mut self,
        confirm: ModalConfirmation<EditConfirmResult>,
        pending_target: AgentPane,
        ch: char,
    ) -> InputOutcome {
        if let Some(result) = confirm.resolve(ch) {
            let was_drain_blocked = self.drain_blocked();
            match result {
                EditConfirmResult::Cancel => {
                    // Dismiss dialog, stay in editing mode.
                    // (active_modal already taken)
                }
                EditConfirmResult::Save => {
                    // Empty edit: keep the original row text — a
                    // queued prompt must never be blanked by Save.
                    if self.prompt.text().trim().is_empty() {
                        self.exit_editing_mode();
                        self.set_active_pane(pending_target, true);
                        if was_drain_blocked {
                            return InputOutcome::Action(Action::DrainQueue);
                        }
                        return InputOutcome::Changed;
                    }
                    let outcome = match self.prompt_mode.clone() {
                        PromptMode::EditingQueued { id, .. } => {
                            // Drain only when "save & send" was the
                            // advertised label (see helper doc).
                            self.save_edited_queued_row(id, was_drain_blocked)
                        }
                        // Unreachable in practice: the modal only
                        // opens from `EditingQueued`.
                        _ => {
                            self.exit_editing_mode();
                            InputOutcome::Changed
                        }
                    };
                    self.set_active_pane(pending_target, true);
                    return outcome;
                }
                EditConfirmResult::Discard => {
                    // Discard changes (revert to original), exit editing.
                    self.exit_editing_mode();
                    self.set_active_pane(pending_target, true);
                    if was_drain_blocked {
                        return InputOutcome::Action(Action::DrainQueue);
                    }
                    return InputOutcome::Changed;
                }
                EditConfirmResult::Delete => {
                    // Delete the prompt entirely from the queue.
                    if let PromptMode::EditingQueued { id, .. } = self.prompt_mode {
                        self.session.pending_prompts.retain(|p| p.id != id);
                    }
                    self.exit_editing_mode();
                    self.set_active_pane(pending_target, true);
                    // If drain was blocked and we deleted the front,
                    // the next prompt (if any) should now drain.
                    if was_drain_blocked {
                        return InputOutcome::Action(Action::DrainQueue);
                    }
                    return InputOutcome::Changed;
                }
            }
        } else {
            // Key didn't match any option — restore modal, keep blocking.
            self.active_modal = Some(ActiveModal::EditConfirm {
                modal: confirm,
                pending_target,
            });
        }
        InputOutcome::Changed
    }

    /// Enter editing mode for the queue row selected via
    /// `QueueEvent::EditSelected` (called from `handle_queue_key`).
    pub(super) fn enter_queue_edit(&mut self, id: u64) {
        use crate::app::agent::QueueEntryKind;

        let entry_data = self
            .session
            .pending_prompts
            .iter()
            .find(|p| p.id == id)
            .map(|p| {
                (
                    p.text.clone(),
                    p.kind,
                    p.images.clone(),
                    p.chip_elements.clone(),
                )
            });
        if let Some((text, kind, images, chip_elements)) = entry_data {
            self.stashed_prompt = if self.prompt.text().is_empty() {
                None
            } else {
                Some(self.prompt.stash())
            };
            // Load queued text and enter editing mode.
            // Set prompt_input_mode based on entry kind so the prompt
            // renders with the correct visual (yellow `!` prefix
            // for bash entries, normal for prompts/commands).
            self.prompt
                .restore(crate::views::prompt_widget::StashedPrompt::from_submission(
                    text.clone(),
                    images,
                    chip_elements,
                ));
            self.prompt_mode = PromptMode::EditingQueued {
                id,
                original: text,
                kind,
            };
            self.prompt_input_mode = if kind == QueueEntryKind::BashCommand {
                PromptInputMode::Bash
            } else {
                PromptInputMode::Normal
            };
            self.set_active_pane(AgentPane::Prompt, false);
        } else {
            // The row left the queue between selection and keypress, so there is nothing to edit.
            self.show_toast("Queued prompt is no longer in the queue");
        }
    }

    /// Save the edited composer text back to the queued row and exit edit
    /// mode. Single owner of the save invariants for the bare-Enter
    /// intercept and the modal Save arm.
    ///
    /// `drain`: whether the save requests a queue drain. Enter-save always
    /// drains (the user just released the front edit lock); modal Save drains
    /// only when the drain was blocked on this edit — a plain save of a
    /// non-front row must not start the head prompt's turn.
    ///
    /// Text that resolves to a pager builtin leaves through `Action::RunEditedQueuedCommand`
    /// instead, ignoring `drain`: dispatch runs the command and drains once it has settled the row.
    fn save_edited_queued_row(&mut self, id: u64, drain: bool) -> InputOutcome {
        // A pager builtin left in the row would reach the model verbatim as a literal `/…` string:
        // the agent's resolve() reserves pager-owned names without handling them.
        // Only a `Prompt` row in normal composer mode qualifies; bash and remember rows stay text.
        if matches!(
            self.prompt_mode,
            PromptMode::EditingQueued {
                kind: crate::app::agent::QueueEntryKind::Prompt,
                ..
            }
        ) && self.prompt_input_mode == PromptInputMode::Normal
            && crate::slash::is_complete_builtin_invocation(
                self.prompt.text(),
                self.prompt.slash_controller.registry(),
            )
        {
            let text = self.prompt.text().to_string();
            self.exit_editing_mode();
            return InputOutcome::Action(Action::RunEditedQueuedCommand {
                local_id: id,
                text,
            });
        }

        let edited = self.prompt.stash();
        let (new_text, mut images, chip_elements) = edited.into_submission();
        // In-place mutation of the queued row. Recompute token ranges for the
        // edited text — the stale ranges would point at the pre-edit byte offsets.
        let skill_token_ranges = self
            .prompt
            .slash_controller
            .recognized_token_ranges(&new_text, &self.session.models);
        if let Some(entry) = self.session.pending_prompts.iter_mut().find(|p| p.id == id) {
            let retained: std::collections::HashSet<u64> = images
                .iter()
                .map(|image| image.preview.identity())
                .collect();
            for old in entry.images.drain(..) {
                if !retained.contains(&old.preview.identity()) {
                    crate::prompt_images::cleanup_temp_file(&old);
                }
            }
            entry.text = new_text;
            entry.images = std::mem::take(&mut images);
            entry.chip_elements = chip_elements;
            entry.skill_token_ranges = skill_token_ranges;
            // Clear stale wire_blocks: edited text may no longer match the original skill
            // invocation. The prompt will be sent as plain text via the normal path.
            // Pager builtins never get here (`is_complete_builtin_invocation` routed them
            // to dispatch); ACP, skill, and unknown `/…` text is left for the agent's
            // resolve(), which does not know pager builtins.
            entry.wire_blocks = None;
            // display_as_skill rides wire_blocks (see its field doc) — clear both
            // together, or the drain keeps stale skill styling over the ranges.
            entry.display_as_skill = false;
        }
        crate::prompt_images::drain_and_cleanup(&mut images);
        self.exit_editing_mode();
        if drain {
            InputOutcome::Action(Action::DrainQueue)
        } else {
            InputOutcome::Changed
        }
    }

    /// Whether the next turn is held because the user is editing the front prompt:
    /// idle and the edited id is the `pending_prompts` front.
    pub(crate) fn drain_blocked(&self) -> bool {
        let PromptMode::EditingQueued { id, .. } = &self.prompt_mode else {
            return false;
        };
        if !self.session.state.is_idle() {
            return false;
        }
        self.session
            .pending_prompts
            .front()
            .is_some_and(|p| p.id == *id)
    }

    /// Exit editing mode: restore stashed text, clear mode, focus queue pane.
    /// No-op unless `EditingQueued`.
    ///
    /// Always resets `prompt_input_mode` to `Normal` so it doesn't leak
    /// into subsequent normal prompt entry.
    pub(super) fn exit_editing_mode(&mut self) {
        // Idempotent: remove_local_queue_row's guard may have exited already;
        // a second take() of the spent stash would wipe the composer.
        if !matches!(self.prompt_mode, PromptMode::EditingQueued { .. }) {
            return;
        }
        let stash = self.stashed_prompt.take().unwrap_or_default();
        self.prompt.restore(stash);
        self.finish_editing_exit();
    }

    /// Shared tail of every edit exit — stash policy stays with the callers.
    fn finish_editing_exit(&mut self) {
        self.prompt_mode = PromptMode::Normal;
        self.prompt_input_mode = PromptInputMode::Normal;
        // Editing is over, so a pending EditConfirm is meaningless — left
        // behind it would eat all input without ever rendering. Other
        // modal variants are untouched.
        if matches!(self.active_modal, Some(ActiveModal::EditConfirm { .. })) {
            self.active_modal = None;
        }
        // Return focus to queue pane (if still visible).
        // Force=true: we just cleared editing mode, no lock to check.
        if self.queue.is_visible() {
            self.set_active_pane(AgentPane::Queue, true);
        } else {
            self.set_active_pane(AgentPane::Scrollback, true);
        }
    }
}

#[cfg(test)]
mod tests {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    use crate::app::actions::Action;
    use crate::app::agent::AgentState;
    use crate::app::agent_view::test_fixtures::{
        make_running_agent, running_agent_local_only, test_pasted_image,
    };
    use crate::app::agent_view::{AgentPane, AgentView, PromptMode};
    use crate::app::app_view::InputOutcome;
    use crate::views::modal::{ActiveModal, ModalConfirmation};

    fn edit_key() -> KeyEvent {
        // Default key binding for QueueEvent::EditSelected.
        KeyEvent::new(KeyCode::Char('e'), KeyModifiers::NONE)
    }

    fn delete_key() -> KeyEvent {
        // Default key binding for QueueEvent::DeleteSelected.
        KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE)
    }

    fn enter_key() -> KeyEvent {
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)
    }

    fn enter_edit_local_row() -> AgentView {
        let mut agent = make_running_agent();
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        assert!(matches!(
            agent.prompt_mode,
            PromptMode::EditingQueued { .. }
        ));
        agent
    }

    /// Shift/Alt+Enter → newline in edit mode (must not save).
    /// Cmd/SUPER is not a product-wide newline chord (Apple Terminal only via CG).
    /// `/btw why` fences the ordering: mod-Enter beats the hijack in `save_edited_queued_row`.
    #[test]
    fn edit_mod_enter_inserts_newline_without_exiting() {
        for mods in [KeyModifiers::SHIFT, KeyModifiers::ALT] {
            for text in ["line1", "/btw why"] {
                let mut agent = enter_edit_local_row();
                agent.prompt.set_text(text);
                let outcome =
                    agent.handle_prompt_key_for_test(&KeyEvent::new(KeyCode::Enter, mods));
                assert!(
                    matches!(outcome, InputOutcome::Changed),
                    "mod Enter ({mods:?}, {text:?}) must not save; got {outcome:?}"
                );
                assert!(
                    matches!(agent.prompt_mode, PromptMode::EditingQueued { .. }),
                    "must stay in edit mode for {mods:?}, {text:?}"
                );
                assert_eq!(
                    agent.prompt.text(),
                    format!("{text}\n"),
                    "mod Enter ({mods:?}) must insert a newline"
                );
                assert_eq!(
                    agent.session.pending_prompts[0].text, "local one",
                    "queue row must stay unchanged for {mods:?}, {text:?}"
                );
            }
        }
    }

    /// Bare Enter still saves (mod-enter path must not steal it).
    #[test]
    fn edit_bare_enter_still_saves() {
        let mut agent = enter_edit_local_row();
        agent.prompt.set_text("line1 EDITED");
        let outcome = agent.handle_prompt_key_for_test(&enter_key());
        assert!(
            matches!(outcome, InputOutcome::Action(Action::DrainQueue)),
            "bare Enter must save; got {outcome:?}"
        );
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));
        assert_eq!(agent.session.pending_prompts[0].text, "line1 EDITED");
    }

    fn attach_image_to_local_row(agent: &mut AgentView) {
        let mut image = test_pasted_image();
        image.display_number = 1;
        let row = agent.session.pending_prompts.front_mut().unwrap();
        row.text = "local one [Image #1] ".into();
        row.images = vec![image];
        row.chip_elements = vec![crate::app::agent::ChipElement {
            range: 10..20,
            kind: crate::views::prompt_widget::KIND_IMAGE,
            display: None,
        }];
        agent.queue.sync_from_local(&agent.session.pending_prompts);
    }

    /// Edit on a row no longer in the queue toasts instead of a silent drop.
    #[test]
    fn edit_on_vanished_row_toasts() {
        let mut agent = make_running_agent();
        agent.enter_queue_edit(999);

        assert!(
            matches!(agent.prompt_mode, PromptMode::Normal),
            "a vanished row must not enter edit mode"
        );
        assert_eq!(
            agent.toast.as_ref().map(|(message, _)| message.as_str()),
            Some("Queued prompt is no longer in the queue"),
        );
    }

    /// Idle + editing the queue front blocks drain UI.
    #[test]
    fn drain_blocked_true_editing_front_while_idle() {
        let mut agent = make_running_agent();
        agent.session.state = AgentState::Idle;
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        assert!(
            agent.drain_blocked(),
            "idle + editing the queue front must block drain"
        );
    }

    /// Editing a non-front row does not block drain.
    #[test]
    fn drain_blocked_false_editing_non_front() {
        let mut agent = make_running_agent();
        agent.session.state = AgentState::Idle;
        agent.session.enqueue_prompt("local two".to_string());
        agent.queue.sync_from_local(&agent.session.pending_prompts);
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[1]);
        let _ = agent.handle_queue_key(&edit_key());
        assert!(
            matches!(agent.prompt_mode, PromptMode::EditingQueued { .. }),
            "the non-front row must be in edit mode"
        );
        assert!(
            !agent.drain_blocked(),
            "editing a non-front row must not block drain"
        );
    }

    /// Running turn: editing the front is not "drain blocked" (nothing drains mid-turn).
    #[test]
    fn drain_blocked_false_editing_front_while_running() {
        let mut agent = make_running_agent();
        assert!(matches!(agent.session.state, AgentState::TurnRunning));
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        assert!(
            !agent.drain_blocked(),
            "while a turn is running, drain_blocked is false"
        );
    }

    /// Submitting an edit on a local-origin row continues to mutate the local
    /// `pending_prompts` in place (existing behavior preserved).
    #[test]
    fn submit_local_edit_mutates_local_pending_prompts() {
        let mut agent = make_running_agent();

        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());

        agent.prompt.set_text("local one EDITED");

        let outcome = agent.handle_prompt_key_for_test(&enter_key());
        match outcome {
            InputOutcome::Action(Action::DrainQueue) => {}
            other => panic!("expected DrainQueue, got {other:?}"),
        }
        assert_eq!(agent.session.pending_prompts.len(), 1);
        assert_eq!(agent.session.pending_prompts[0].text, "local one EDITED");
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));
    }

    /// Fail-closed: only a complete builtin invocation at position 0 is hijacked. Everything else
    /// saves as text exactly as before.
    #[test]
    fn incomplete_unknown_and_mid_text_slash_edits_still_save_as_text() {
        // `/btw` alone requires args; `/nope` is unknown (agent pass-through); a mid-text token
        // is not an invocation.
        for text in ["/btw", "/nope x", "great /compact go"] {
            let mut agent = enter_edit_local_row();
            agent.prompt.set_text(text);
            let outcome = agent.handle_prompt_key_for_test(&enter_key());
            assert!(
                matches!(outcome, InputOutcome::Action(Action::DrainQueue)),
                "{text:?} must save as text; got {outcome:?}"
            );
            assert_eq!(agent.session.pending_prompts[0].text, text);
        }
    }

    /// A bash row edited into `/btw …` stays a bash command: the composer is in bash mode, so the
    /// text is a shell string, not a pager command.
    #[test]
    fn edit_local_bash_row_into_builtin_saves_as_bash_text() {
        use crate::app::agent::QueueEntryKind;
        let mut agent = make_running_agent();
        agent.session.pending_prompts.clear();
        agent.session.enqueue_bash_command("ls".into());
        agent.queue.sync_from_local(&agent.session.pending_prompts);

        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(*ids.last().unwrap());
        let _ = agent.handle_queue_key(&edit_key());
        agent.prompt.set_text("/btw why");

        let outcome = agent.handle_prompt_key_for_test(&enter_key());
        assert!(
            matches!(outcome, InputOutcome::Action(Action::DrainQueue)),
            "bash rows are never hijacked, got {outcome:?}"
        );
        let row = &agent.session.pending_prompts[0];
        assert_eq!(row.text, "/btw why");
        assert_eq!(row.kind, QueueEntryKind::BashCommand, "kind must survive");
    }

    /// Ctrl+; (toggle_queue_pane) while dirty-editing must not brick: the switch
    /// is blocked, no modal armed, and the queue overlay is not left focused.
    #[test]
    fn toggle_queue_pane_while_dirty_editing_does_not_brick() {
        let mut agent = make_running_agent();

        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        agent.prompt.set_text("local one EDITED");
        assert!(matches!(
            agent.prompt_mode,
            PromptMode::EditingQueued { .. }
        ));

        agent.toggle_queue_pane();

        assert!(
            agent.active_modal.is_none(),
            "toggling the queue mid-edit must never arm an invisible EditConfirm"
        );
        assert!(
            matches!(agent.prompt_mode, PromptMode::EditingQueued { .. }),
            "the blocked switch keeps the user in the edit"
        );
        assert!(
            !agent.queue.overlay.focused,
            "a blocked switch must not leave the queue overlay focused while input is in the prompt"
        );
    }

    #[test]
    fn queue_edit_cancel_restores_draft_image_state() {
        let mut agent = make_running_agent();
        attach_image_to_local_row(&mut agent);
        agent.prompt.set_text("draft ");
        let draft_end = agent.prompt.text().len();
        agent.prompt.set_cursor(draft_end);
        agent.prompt.insert_image(test_pasted_image()).unwrap();

        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        assert_eq!(agent.prompt.text(), "local one [Image #1] ");
        assert_eq!(agent.prompt.images.len(), 1);
        agent.prompt.set_text("discard this edit");

        let outcome =
            agent.handle_prompt_key_for_test(&KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert!(matches!(outcome, InputOutcome::Action(Action::DrainQueue)));
        assert_eq!(agent.prompt.text(), "draft [Image #1] ");
        assert_eq!(
            agent
                .prompt
                .textarea()
                .elements()
                .iter()
                .filter(|element| element.kind == crate::views::prompt_widget::KIND_IMAGE)
                .count(),
            1
        );
        assert_eq!(
            agent.prompt.drain_images().len(),
            1,
            "restored draft image must remain sendable"
        );
        let row = agent.session.pending_prompts.front().unwrap();
        assert_eq!(row.text, "local one [Image #1] ");
        assert_eq!(row.images.len(), 1);
        assert_eq!(row.chip_elements.len(), 1);
    }

    /// Lone-local-row agent with "draft" stashed, edit mode entered on the
    /// row. Removing the row empties the queue → the pane auto-hide switch
    /// runs mid-flow.
    fn edit_lone_local_row() -> AgentView {
        let mut agent = running_agent_local_only();
        agent.prompt.set_text("draft");
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        // Fixture breakage must fail here, not in the flows under test.
        assert!(matches!(
            agent.prompt_mode,
            PromptMode::EditingQueued { .. }
        ));
        agent
    }

    /// Deleting the lone LOCAL row while it is being edited (dirty): the
    /// delete discards the edit — it must not leave `EditingQueued` pointing
    /// at a removed row or let the auto-hide arm the invisible modal.
    #[test]
    fn delete_edited_lone_local_row_discards_edit_without_orphaned_modal() {
        let mut agent = edit_lone_local_row();
        agent.prompt.set_text("local one EDITED");

        let _ = agent.handle_queue_key(&delete_key());

        assert!(agent.session.pending_prompts.is_empty());
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));
        assert!(
            agent.active_modal.is_none(),
            "orphaned EditConfirm would brick input"
        );
        assert_eq!(agent.prompt.text(), "draft");
    }

    /// `PromptMode` for an in-progress edit of the fixture's local row.
    fn editing_lone_local() -> PromptMode {
        PromptMode::EditingQueued {
            id: 0,
            original: "local one".into(),
            kind: crate::app::agent::QueueEntryKind::Prompt,
        }
    }

    /// `exit_editing_mode` clears a stray `EditConfirm` (unreachable in-tree
    /// after the reorder, so pin the backstop directly) and leaves every
    /// other modal variant alone.
    #[test]
    fn exit_editing_mode_clears_only_stray_edit_confirm() {
        let mut agent = make_running_agent();
        agent.prompt_mode = editing_lone_local();
        agent.active_modal = Some(ActiveModal::EditConfirm {
            modal: ModalConfirmation::edit_confirm(),
            pending_target: AgentPane::Scrollback,
        });
        agent.exit_editing_mode();
        assert!(agent.active_modal.is_none());
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));

        // Scoping: a non-EditConfirm modal survives the exit untouched.
        // Re-enter edit mode so the guarded body (not the idempotency
        // early-return) is what leaves the palette alone.
        agent.prompt_mode = editing_lone_local();
        agent.active_modal = Some(ActiveModal::CommandPalette {
            entries: crate::views::modal::default_palette_entries(
                agent.sharing_enabled,
                &agent.prompt.slash_controller,
            ),
            state: crate::views::picker::PickerState::input_active(),
            window: crate::views::modal_window::ModalWindowState::new(),
        });
        agent.exit_editing_mode();
        assert!(matches!(
            agent.active_modal,
            Some(ActiveModal::CommandPalette { .. })
        ));
    }

    /// `exit_editing_mode` outside `EditingQueued` is a no-op — the
    /// remove-then-exit caller shape must not wipe the composer by
    /// double-taking the spent stash.
    #[test]
    fn exit_editing_mode_is_noop_when_not_editing() {
        let mut agent = make_running_agent();
        agent.prompt.set_text("draft");
        agent.exit_editing_mode();
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));
        assert_eq!(agent.prompt.text(), "draft");
    }

    /// Modal Save with an empty composer keeps the original row text — a
    /// queued prompt must never be blanked by Save.
    #[test]
    fn edit_confirm_save_empty_preserves_original_text() {
        let mut agent = make_running_agent();

        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&edit_key());
        agent.prompt.set_text("");

        // Arm the modal directly (the pane switch no longer arms it) to test empty-Save.
        agent.active_modal = Some(ActiveModal::EditConfirm {
            modal: ModalConfirmation::edit_confirm(),
            pending_target: AgentPane::Scrollback,
        });
        let _ = agent.handle_modal_key(&KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE));

        assert_eq!(agent.session.pending_prompts[0].text, "local one");
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));
    }

    /// Modal Save on a NON-front local row while idle saves the text but must
    /// NOT dispatch `DrainQueue` — the modal advertised plain "save" (not
    /// "save & send"), and draining would start the head prompt's turn.
    #[test]
    fn edit_confirm_save_non_front_row_does_not_drain() {
        let mut agent = make_running_agent();
        agent.session.enqueue_prompt("local two".to_string());
        agent.queue.sync_from_local(&agent.session.pending_prompts);
        agent.session.state = AgentState::Idle;

        // Edit the non-front row; arm the modal directly (the pane switch no longer arms it).
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[1]);
        let _ = agent.handle_queue_key(&edit_key());
        agent.prompt.set_text("local two EDITED");
        agent.active_modal = Some(ActiveModal::EditConfirm {
            modal: ModalConfirmation::edit_confirm(),
            pending_target: AgentPane::Scrollback,
        });

        let outcome =
            agent.handle_modal_key(&KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE));
        assert!(
            !matches!(outcome, InputOutcome::Action(Action::DrainQueue)),
            "plain save must not drain the head prompt, got {outcome:?}"
        );
        assert_eq!(agent.session.pending_prompts.len(), 2);
        assert_eq!(agent.session.pending_prompts[1].text, "local two EDITED");
        assert!(matches!(agent.prompt_mode, PromptMode::Normal));
    }
}
