//! Prompt-queue pane: visibility toggles, key handling and row removal.

#[cfg(test)]
use super::test_fixtures;
use super::{AgentPane, AgentView, PromptMode, overlay_action_to_outcome};
use crate::app::app_view::InputOutcome;
use crossterm::event::KeyEvent;

impl AgentView {
    /// Remove a queued row: fix selection, drop the entry, hide the pane if
    /// the queue emptied. Returns the removed prompt, if any.
    pub(in crate::app) fn remove_local_queue_row(
        &mut self,
        id: u64,
    ) -> Option<crate::app::agent::QueuedPrompt> {
        let pos = self
            .session
            .pending_prompts
            .iter()
            .position(|p| p.id == id)?;
        // Deleting the row being edited discards the edit — and must exit
        // BEFORE the removal so a potential auto-hide pane switch can't hit
        // the editing lock (see queue_edit.rs ordering invariant).
        if matches!(
            self.prompt_mode,
            PromptMode::EditingQueued { id: editing_id, .. } if editing_id == id
        ) {
            self.exit_editing_mode();
        }
        self.queue.select_after_delete(id);
        let prompt = self.session.pending_prompts.remove(pos);
        if self.visible_queue_is_empty() {
            self.hide_queue_pane();
        }
        prompt
    }

    /// Highlights the bottom row and leaves the composer untouched.
    pub(super) fn try_focus_queue_from_prompt(&mut self) -> Option<InputOutcome> {
        self.sync_queue_pane();
        let id = *self.queue.entry_ids().last()?;

        if !self.set_active_pane(AgentPane::Queue, false) {
            return Some(InputOutcome::Changed);
        }

        self.queue.overlay.visible = true;
        self.queue.overlay.focused = true;
        self.queue.list_state.select_by_id(id);
        Some(InputOutcome::Changed)
    }

    /// Rebuild the queue pane from the local drip-feed queue.
    pub(crate) fn sync_queue_pane(&mut self) {
        self.queue.sync_from_local(&self.session.pending_prompts);
    }

    /// Shared tail of every turn-end marker push
    /// (`push_turn_terminal_marker`).
    pub(crate) fn push_end_marker_block(
        &mut self,
        event: crate::scrollback::blocks::SessionEvent,
    ) {
        let block = crate::scrollback::blocks::SessionEventBlock::new(event);
        self.scrollback
            .push_block(crate::scrollback::block::RenderBlock::SessionEvent(block));
    }

    /// Toggle queue pane visibility (Ctrl-; shortcut).
    pub(in crate::app) fn toggle_queue_pane(&mut self) {
        self.queue.overlay.toggle();
        self.queue.on_state_change();
        if self.queue.overlay.focused {
            self.set_active_pane(AgentPane::Queue, false);
        } else if self.active_pane == AgentPane::Queue {
            self.set_active_pane(AgentPane::Scrollback, false);
        }
    }

    /// Routes through overlay structural keys, then queue actions, then navigation.
    pub(in crate::app) fn handle_queue_key(&mut self, key: &KeyEvent) -> InputOutcome {
        use crate::views::overlay::{handle_overlay_key, handle_overlay_nav_key};
        use crate::views::queue_pane::QueueEvent;

        // Structural keys through shared handler (Esc, Ctrl-F, etc.).
        let action = handle_overlay_key(&mut self.queue.overlay, key)
            .or_else(|| handle_overlay_nav_key(&mut self.queue.overlay, key));
        if let Some(action) = action {
            self.queue.on_state_change();
            // Overlay dismiss skips hide_queue_pane; reset edge when queue is empty.
            if !self.queue.overlay.visible && self.visible_queue_is_empty() {
                self.queue.reset_auto_show_edge();
            }
            if !self.queue.overlay.visible || !self.queue.overlay.focused {
                self.set_active_pane(AgentPane::Scrollback, false);
            }
            return overlay_action_to_outcome(action);
        }

        // Queue-specific actions (delete, edit, reorder). `x`/Delete = row delete.
        if let Some(event) = self.queue.handle_key(key) {
            match event {
                QueueEvent::DeleteSelected { id } => {
                    // No drain kick (cf. mouse [cancel]): queue focus is unreachable mid-edit.
                    self.remove_local_queue_row(id);
                }
                QueueEvent::EditSelected { id } => {
                    // Entry into editing mode lives in `queue_edit.rs`.
                    self.enter_queue_edit(id);
                }
                QueueEvent::SwapUp { id } => {
                    self.session.swap_prompt_up(id);
                }
                QueueEvent::SwapDown { id } => {
                    self.session.swap_prompt_down(id);
                }
            }
            return InputOutcome::Changed;
        }

        // Down off the last row returns to the composer, as the history panel does past its newest entry.
        // Up stays clamped, because overshooting there would open history and rewrite the composer.
        if crate::key!(Down).matches(key)
            && self.queue.selected_id() == self.queue.entry_ids().last().copied()
        {
            self.queue.overlay.focused = false;
            self.set_active_pane(AgentPane::Prompt, false);
            return InputOutcome::Changed;
        }

        // Navigation keys (j/k, y to copy, etc.).
        if self.queue.handle_navigation_key(key) {
            InputOutcome::Changed
        } else {
            InputOutcome::Unchanged
        }
    }

    /// True when the pane would show zero rows.
    pub(in crate::app) fn visible_queue_is_empty(&self) -> bool {
        self.session.pending_prompts.is_empty()
    }

    /// Hide the queue pane. Only steals focus when the queue pane was active —
    /// removing the last row from the composer path must not yank the user out
    /// of the composer into scrollback.
    pub(in crate::app) fn hide_queue_pane(&mut self) {
        self.queue.overlay.visible = false;
        self.queue.overlay.focused = false;
        // External hide skips sync auto-hide; reset so next enqueue can auto-show.
        self.queue.reset_auto_show_edge();
        if self.active_pane == AgentPane::Queue {
            self.set_active_pane(AgentPane::Scrollback, false);
        }
    }
}

#[cfg(test)]
mod queue_edit_routing_tests {
    use super::test_fixtures::{make_running_agent, running_agent_local_only};
    use super::*;
    use crate::app::app_view::InputOutcome;
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    fn delete_key() -> KeyEvent {
        KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE)
    }

    /// `visible_queue_is_empty` tracks the local queue.
    #[test]
    fn visible_queue_is_empty_tracks_the_local_queue() {
        let mut agent = make_running_agent();
        assert!(!agent.visible_queue_is_empty());

        agent.session.pending_prompts.clear();
        assert!(agent.visible_queue_is_empty());

        agent.session.enqueue_prompt("again".to_string());
        assert!(!agent.visible_queue_is_empty());
    }

    fn down_key() -> KeyEvent {
        KeyEvent::new(
            crossterm::event::KeyCode::Down,
            crossterm::event::KeyModifiers::NONE,
        )
    }

    /// Down off the last row hands focus back, so Up in and Down out is a round trip.
    #[test]
    fn down_off_the_last_row_returns_to_the_composer() {
        let mut agent = make_running_agent();
        agent.active_pane = AgentPane::Queue;
        agent.queue.overlay.focused = true;
        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(*ids.last().unwrap());

        agent.handle_queue_key(&down_key());

        assert_eq!(agent.active_pane, AgentPane::Prompt);
        assert!(!agent.queue.overlay.focused);
    }

    /// Above the last row Down steps the highlight and stays in the pane.
    #[test]
    fn down_above_the_last_row_stays_in_the_queue() {
        let mut agent = make_running_agent();
        agent.active_pane = AgentPane::Queue;
        agent.queue.overlay.focused = true;
        agent.session.pending_prompts.clear();
        agent.session.enqueue_prompt("queued first".to_string());
        agent.session.enqueue_prompt("queued last".to_string());
        agent.sync_queue_pane();
        let ids = agent.queue.entry_ids();
        assert_eq!(ids.len(), 2, "two rows so Down has somewhere to go");
        agent.queue.list_state.select_by_id(ids[0]);

        agent.handle_queue_key(&down_key());

        // Only the exit rule is asserted. Stepping is the list pane's own, and it moves by
        // index, which nothing resolves until a render, so it cannot move in a headless test.
        assert_eq!(agent.active_pane, AgentPane::Queue);
        assert!(agent.queue.overlay.focused);
    }

    /// Keyboard-deleting the last queued row hides the pane and hands focus back to scrollback.
    #[test]
    fn delete_last_row_hides_pane() {
        let mut agent = running_agent_local_only();
        let ids = agent.queue.entry_ids();
        assert_eq!(ids.len(), 1);
        agent.queue.list_state.select_by_id(ids[0]);
        let outcome = agent.handle_queue_key(&delete_key());
        assert!(matches!(outcome, InputOutcome::Changed));

        assert!(agent.session.pending_prompts.is_empty());
        assert!(!agent.queue.overlay.visible);
        assert!(!agent.queue.overlay.focused);
        assert_eq!(agent.active_pane, AgentPane::Scrollback);
    }

    /// Deleting a non-last row keeps the pane open, focused, and on the surviving row.
    #[test]
    fn delete_one_of_two_rows_keeps_pane_open() {
        let mut agent = running_agent_local_only();
        agent.session.enqueue_prompt("local two".to_string());
        agent.sync_queue_pane();
        let ids = agent.queue.entry_ids();
        assert_eq!(ids.len(), 2);
        agent.queue.list_state.select_by_id(ids[0]);

        let outcome = agent.handle_queue_key(&delete_key());
        assert!(matches!(outcome, InputOutcome::Changed));

        assert_eq!(agent.session.pending_prompts.len(), 1);
        assert_eq!(agent.session.pending_prompts[0].text, "local two");
        assert!(agent.queue.overlay.visible);
        assert!(agent.queue.overlay.focused);
        assert_eq!(agent.active_pane, AgentPane::Queue);
    }

    /// Shift+K / Shift+J reorder the selected row within the local queue.
    #[test]
    fn swap_up_and_down_reorder_local_rows() {
        let mut agent = running_agent_local_only();
        agent.session.enqueue_prompt("local two".to_string());
        agent.sync_queue_pane();
        let ids = agent.queue.entry_ids();
        assert_eq!(ids.len(), 2);

        agent.queue.list_state.select_by_id(ids[1]);
        let shift_k = KeyEvent::new(KeyCode::Char('K'), KeyModifiers::SHIFT);
        let outcome = agent.handle_queue_key(&shift_k);
        assert!(matches!(outcome, InputOutcome::Changed));
        let texts: Vec<&str> = agent
            .session
            .pending_prompts
            .iter()
            .map(|p| p.text.as_str())
            .collect();
        assert_eq!(texts, ["local two", "local one"]);

        let shift_j = KeyEvent::new(KeyCode::Char('J'), KeyModifiers::SHIFT);
        let outcome = agent.handle_queue_key(&shift_j);
        assert!(matches!(outcome, InputOutcome::Changed));
        let texts: Vec<&str> = agent
            .session
            .pending_prompts
            .iter()
            .map(|p| p.text.as_str())
            .collect();
        assert_eq!(texts, ["local one", "local two"]);
    }

    #[test]
    fn delete_last_then_requeue_auto_shows_pane() {
        let mut agent = running_agent_local_only();
        // Prime prev_len via sync (mirrors a rendered frame with one row).
        agent.queue.sync_from_local(&agent.session.pending_prompts);
        assert!(agent.queue.overlay.visible);

        let ids = agent.queue.entry_ids();
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&delete_key());
        assert!(!agent.queue.overlay.visible);

        agent.session.enqueue_prompt("replacement".into());
        agent.queue.sync_from_local(&agent.session.pending_prompts);
        assert!(
            agent.queue.overlay.visible,
            "new queued prompt must be visible after delete+requeue"
        );
        assert_eq!(agent.queue.entry_ids().len(), 1);
    }

    /// A running agent whose only queued row is a local bash command.
    fn running_agent_with_local_bash(command: &str) -> AgentView {
        let mut agent = running_agent_local_only();
        agent.session.pending_prompts.clear();
        agent.session.enqueue_bash_command(command.into());
        agent.queue.sync_from_local(&agent.session.pending_prompts);
        agent
    }

    /// Empty Enter mid-turn never touches the queue: a queued bash row stays queued.
    #[test]
    fn enter_empty_from_prompt_leaves_queued_bash_row_alone() {
        let mut agent = running_agent_with_local_bash("git status");
        agent.active_pane = AgentPane::Prompt;
        agent.queue.overlay.focused = false;
        agent.prompt.set_text("");

        let enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
        let outcome = agent.handle_prompt_key_for_test(&enter);
        assert!(
            matches!(outcome, InputOutcome::Changed),
            "empty Enter must be a no-op, got {outcome:?}"
        );
        assert_eq!(
            agent.session.pending_prompts.len(),
            1,
            "bash row must stay queued"
        );
    }

    /// Empty Enter mid-turn never sends a queued follow-up early.
    #[test]
    fn enter_empty_from_prompt_leaves_queued_prompt_alone() {
        let mut agent = running_agent_local_only();
        agent.active_pane = AgentPane::Prompt;
        agent.queue.overlay.focused = false;
        agent.prompt.set_text("");

        let enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
        let outcome = agent.handle_prompt_key_for_test(&enter);
        assert!(
            matches!(outcome, InputOutcome::Changed),
            "empty Enter must be a no-op, got {outcome:?}"
        );
        assert_eq!(agent.session.pending_prompts.len(), 1);
        assert_eq!(
            agent.active_pane,
            AgentPane::Prompt,
            "empty Enter must not steal focus"
        );
    }

    /// Multiline + non-empty composer: bare Enter inserts a newline
    /// (does not queue/send), even mid-turn with a queue present.
    #[test]
    fn multiline_enter_with_text_inserts_newline() {
        let mut agent = running_agent_local_only();
        agent.multiline_mode = true;
        agent.active_pane = AgentPane::Prompt;
        agent.queue.overlay.focused = false;
        agent.prompt.set_text("draft line");

        let enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
        let outcome = agent.handle_prompt_key_for_test(&enter);
        assert!(
            matches!(outcome, InputOutcome::Changed),
            "multiline Enter with text must insert newline, got {outcome:?}"
        );
        assert!(
            agent.prompt.text().contains('\n'),
            "expected newline insertion, got {:?}",
            agent.prompt.text()
        );
        assert_eq!(
            agent.session.pending_prompts.len(),
            1,
            "queued follow-up must remain"
        );
    }

    /// Backslash continuation mid-turn only inserts the newline.
    #[test]
    fn enter_backslash_continuation_inserts_newline_and_keeps_queue() {
        let mut agent = running_agent_local_only();
        agent.active_pane = AgentPane::Prompt;
        agent.queue.overlay.focused = false;
        // Trailing backslash with the cursor at end (insert_str advances it).
        agent.prompt.set_text("");
        agent.prompt.textarea.insert_str("wip\\");

        let enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
        let outcome = agent.handle_prompt_key_for_test(&enter);
        assert!(
            matches!(outcome, InputOutcome::Changed),
            "backslash continuation must insert a newline; got {outcome:?}"
        );
        assert_eq!(
            agent.prompt.text(),
            "wip\n",
            "the backslash must be replaced with a newline (continuation applied)"
        );
        assert_eq!(
            agent.session.pending_prompts.len(),
            1,
            "queued follow-up must remain"
        );
    }
}
