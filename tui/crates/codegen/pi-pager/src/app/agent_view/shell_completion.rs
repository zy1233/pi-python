//! Bash-mode shell completion: the always-on Tab surface (deterministic
//! fetch arming, terminal Tab semantics execution) and the dropdown accept
//! path shared by Tab/Enter/mouse.

#[cfg(test)]
use super::test_fixtures;
use super::{AgentView, PromptInputMode};
use crate::views::suggestion_controller::TabAction;

impl AgentView {
    /// Accept the selected completion-dropdown item into the prompt (the
    /// what-to-write policy lives in `CompletionSplice`). Returns whether
    /// the key was consumed; `false` only for the empty-items race (callers
    /// keep their close-and-fall-through arm).
    pub(in crate::app) fn accept_completion_dropdown_item(&mut self) -> bool {
        let had_items = !self.prompt.suggestions.dropdown.items.is_empty();
        // The SELECTED splice would clip an atomic element (paste chip):
        // committing would consume the candidates and then be declined by
        // the write path — honest no-op instead (nothing safe to write;
        // the dropdown stays up so another selection can still accept).
        if self.prompt.completion_accept_would_clip_element() {
            return true;
        }
        let Some(splice) = self.prompt.completion_dropdown_accept() else {
            // Stale-generation refusal closed the dropdown; swallow the key
            // (the refreshed fetch is in flight) instead of falling through
            // to focus-cycling or send.
            return had_items;
        };
        if self.prompt.apply_completion_splice(splice) {
            self.prompt_input_mode = PromptInputMode::Bash;
        }
        true
    }

    /// View-side executor for a [`TabAction`] (the policy lives in the
    /// controller's `tab_decision`).
    pub(super) fn execute_tab_action(&mut self, action: TabAction) {
        match action {
            TabAction::InstaAccept => {
                // A splice clipping an atomic element (paste chip) would be
                // declined AFTER the accept consumed the sole candidate —
                // every Tab would then refetch the same set. Show it instead.
                if self.prompt.completion_accept_would_clip_element() {
                    self.prompt.completion_dropdown_open_if_available();
                } else {
                    self.accept_completion_dropdown_item();
                }
            }
            TabAction::Fill(range, fill) => {
                if self.prompt.apply_completion_fill(range, &fill) {
                } else {
                    // Declined (range clips an atomic element): show the
                    // candidates instead of respinning fill+refetch every Tab.
                    self.prompt.completion_dropdown_open_if_available();
                }
            }
            TabAction::Open => {
                self.prompt.completion_dropdown_open_if_available();
            }
            TabAction::Nothing => {}
        }
    }
}

#[cfg(test)]
mod shell_suggestion_key_tests {
    use super::*;
    use crate::app::actions::Action;
    use crate::app::app_view::InputOutcome;
    use crate::views::suggestion_controller::{CompletionItemParsed, SuggestionSource};
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    /// Wire-shaped token item: `insert_text` is the compat whole line,
    /// `token_text` the span replacement (what a new shell sends).
    fn token_item(line: &str, token: &str, range: std::ops::Range<usize>) -> CompletionItemParsed {
        CompletionItemParsed {
            display: token.to_owned(),
            description: String::new(),
            insert_text: line.to_owned(),
            source: SuggestionSource::PathExecutable,
            priority: 0,
            replace_range: Some(range),
            token_text: Some(token.to_owned()),
            truncated: false,
        }
    }

    fn item(insert: &str, range: Option<std::ops::Range<usize>>) -> CompletionItemParsed {
        CompletionItemParsed {
            display: insert.to_owned(),
            description: String::new(),
            insert_text: insert.to_owned(),
            source: SuggestionSource::PathExecutable,
            priority: 0,
            replace_range: range,
            token_text: None,
            truncated: false,
        }
    }

    /// Wire-shaped FILE token item (what the file provider sends).
    fn file_item(line: &str, token: &str, range: std::ops::Range<usize>) -> CompletionItemParsed {
        CompletionItemParsed {
            display: token.to_owned(),
            description: String::new(),
            insert_text: line.to_owned(),
            source: SuggestionSource::FilePath,
            priority: 0,
            replace_range: Some(range),
            token_text: Some(token.to_owned()),
            truncated: false,
        }
    }

    /// Whole-line history item (insert_text doubles as the span replacement).
    fn history_item(line: &str, range: std::ops::Range<usize>) -> CompletionItemParsed {
        CompletionItemParsed {
            display: line.to_owned(),
            description: String::new(),
            insert_text: line.to_owned(),
            source: SuggestionSource::History,
            priority: 10,
            replace_range: Some(range),
            token_text: None,
            truncated: false,
        }
    }

    /// Bash-mode agent with the env-gated as-you-type pipeline ON and
    /// `text` typed (the dropdown's request-text anchor pinned to it — the
    /// state right after a suggest response landed for the draft).
    fn bash_agent(text: &str) -> AgentView {
        let mut agent = bash_agent_always_on(text);
        agent.prompt.suggestions.enabled = true;
        agent
    }

    /// Same, with the pipeline OFF (`GROK_SUGGESTIONS` unset) — the
    /// always-on Tab surface under test.
    fn bash_agent_always_on(text: &str) -> AgentView {
        let mut agent = super::test_fixtures::make_agent();
        agent.prompt_input_mode = PromptInputMode::Bash;
        agent.prompt.suggestions.enabled = false;
        agent.prompt.textarea.insert_str(text);
        agent.prompt.suggestions.dropdown.request_text = text.to_owned();
        agent.prompt.suggestions.dropdown.request_cursor = text.len();
        agent
    }

    /// THE acceptance regression: accepting a $PATH item after `ls | gr`
    /// edits the token in place — never replaces the whole line with `grep`.
    #[test]
    fn dropdown_tab_accept_replaces_token_in_place() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.suggestions.dropdown.open = true;
        agent.prompt.suggestions.dropdown.items = vec![token_item("ls | grep", "grep", 5..7)];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), "ls | grep");
        assert_eq!(agent.prompt.cursor(), "ls | grep".len());
        assert_eq!(agent.prompt_input_mode, PromptInputMode::Bash);
        assert!(!agent.prompt.completion_dropdown_open());
    }

    /// Enter accepts the same way (both arms share the accept helper).
    #[test]
    fn dropdown_enter_accept_replaces_token_in_place() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.suggestions.dropdown.open = true;
        agent.prompt.suggestions.dropdown.items = vec![token_item("ls | grep", "grep", 5..7)];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Enter));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), "ls | grep");
        assert_eq!(agent.prompt_input_mode, PromptInputMode::Bash);
    }

    /// The accept works identically with the as-you-type pipeline OFF —
    /// in-place acceptance is not env-gated.
    #[test]
    fn dropdown_accept_works_without_env_flag() {
        let mut agent = bash_agent_always_on("ls | gr");
        agent.prompt.suggestions.dropdown.open = true;
        agent.prompt.suggestions.dropdown.items = vec![token_item("ls | grep", "grep", 5..7)];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), "ls | grep");
    }

    /// A ranged item whose range no longer fits the draft is a NO-OP accept
    /// — the draft survives untouched, the dropdown closes, the key is
    /// consumed (never a whole-line clobber, never a send).
    #[test]
    fn dropdown_accept_stale_range_is_a_draft_preserving_noop() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.set_text("totally different");
        agent.prompt.suggestions.dropdown.open = true;
        agent.prompt.suggestions.dropdown.items = vec![token_item("ls | grep", "grep", 5..7)];
        // Pass the generation gate (`set_text` bumped it) so this pins the
        // range-validation no-op, not the staleness gate. The "ls | gr"
        // anchor from `bash_agent` survives the swap (close() keeps it).
        agent.prompt.suggestions.dropdown.generation = agent.prompt.suggestions.generation();

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), "totally different");
        assert!(!agent.prompt.completion_dropdown_open());
    }

    /// Items populated for a superseded generation refuse the accept
    /// wholesale: dropdown closes, draft untouched, Enter does not fall
    /// through to send.
    #[test]
    fn dropdown_accept_stale_generation_is_a_noop() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.suggestions.dropdown.open = true;
        agent.prompt.suggestions.dropdown.items = vec![token_item("ls | grep", "grep", 5..7)];
        // A newer edit bumped the controller past the items' generation.
        agent.prompt.suggestions.dropdown.generation = 3;

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Enter));
        assert!(
            matches!(outcome, InputOutcome::Changed),
            "stale accept must consume the key, got {outcome:?}"
        );
        assert_eq!(agent.prompt.text(), "ls | gr");
        assert!(!agent.prompt.completion_dropdown_open());
    }

    /// Rangeless items (older shells) keep the whole-line behavior.
    #[test]
    fn dropdown_accept_without_range_sets_whole_line() {
        let mut agent = bash_agent("git st");
        agent.prompt.suggestions.dropdown.open = true;
        agent.prompt.suggestions.dropdown.items = vec![item("git status --porcelain", None)];

        let _ = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert_eq!(agent.prompt.text(), "git status --porcelain");
        assert_eq!(agent.prompt.cursor(), agent.prompt.text().len());
    }

    /// Tab opens the dropdown whenever items exist — a ghost is NOT required
    /// (pure path/file completions never carry one). Two candidates with no
    /// shared prefix beyond the typed token = the plain-open path (a single
    /// candidate insta-accepts instead — see the terminal-Tab tests below).
    #[test]
    fn tab_opens_dropdown_without_ghost() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.suggestions.dropdown.items =
            vec![item("grep", Some(5..7)), item("grip", Some(5..7))];
        assert!(!agent.prompt.has_ghost_text());
        assert!(!agent.prompt.completion_dropdown_open());

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(
            agent.prompt.text(),
            "ls | gr",
            "no fill without a longer LCP"
        );
    }

    // -- always-on Tab fetch (no GROK_SUGGESTIONS) --------------------------

    /// An empty bash draft has no token to complete: Tab keeps its
    /// focus-cycling fallthrough.
    #[test]
    fn tab_on_empty_bash_draft_falls_through_to_focus_scrollback() {
        let mut agent = bash_agent_always_on("");
        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(
            outcome,
            InputOutcome::Action(Action::FocusScrollback)
        ));
        assert!(agent.pending_effects.is_empty());
    }

    // -- terminal-like Tab (single-candidate accept / common-prefix fill) --

    /// Exactly one token candidate: Tab accepts it immediately — no
    /// dropdown flash — and the accept re-fetch keeps the pipeline alive.
    #[test]
    fn tab_single_token_candidate_accepts_without_dropdown_flash() {
        let mut agent = bash_agent("cat no");
        agent.prompt.suggestions.dropdown.items = vec![file_item("cat notes.md", "notes.md", 4..6)];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), "cat notes.md");
        assert_eq!(agent.prompt.cursor(), "cat notes.md".len());
        assert!(!agent.prompt.completion_dropdown_open());
    }

    /// A single HISTORY item keeps the plain dropdown-open behavior:
    /// terminal Tab semantics apply to token completions only.
    #[test]
    fn tab_single_history_item_opens_dropdown() {
        let mut agent = bash_agent("git st");
        agent.prompt.suggestions.dropdown.items =
            vec![history_item("git status --porcelain", 0..6)];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(agent.prompt.text(), "git st");
    }

    /// THE legacy-shell compatibility case: a rangeless `path` row (old
    /// shells send `insertText: "grep"`, no range) must never insta-accept
    /// — its whole-line fallback would replace `ls | gr` with `grep`. Tab
    /// plain-opens instead, sole match or not.
    #[test]
    fn tab_sole_rangeless_path_row_opens_dropdown_never_accepts() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.suggestions.dropdown.items = vec![item("grep", None)];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), "ls | gr", "draft must survive");
        assert!(agent.prompt.completion_dropdown_open());
    }

    /// Any rangeless row in a MIXED set (legacy PATH row next to a ranged
    /// file row) forces plain-open too — no insta-accept, no fill.
    #[test]
    fn tab_mixed_rangeless_and_ranged_rows_open_dropdown() {
        let mut agent = bash_agent("ls | gr");
        agent.prompt.suggestions.dropdown.items = vec![
            item("grep", None),
            file_item("ls | grokfile", "grokfile", 5..7),
        ];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(agent.prompt.text(), "ls | gr", "no accept, no fill");
    }

    /// A MIXED set (any non-token item alongside file/path rows) disables
    /// terminal-Tab semantics wholesale: no insta-accept, no fill — Tab
    /// plain-opens so the user sees every candidate, history included.
    #[test]
    fn tab_mixed_file_and_history_items_opens_dropdown() {
        let mut agent = bash_agent("cat no");
        agent.prompt.suggestions.dropdown.items = vec![
            history_item("cat notes.md --verbose", 0..6),
            file_item("cat notes.md", "notes.md", 4..6),
        ];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(agent.prompt.text(), "cat no", "no accept, no fill");
    }

    /// Whole-line history sets never prefix-fill (half a history line is
    /// not a command) — Tab plain-opens.
    #[test]
    fn tab_whole_line_history_items_open_dropdown_not_fill() {
        let mut agent = bash_agent("git st");
        agent.prompt.suggestions.dropdown.items = vec![
            history_item("git status --porcelain-A", 0..6),
            history_item("git status --porcelain-B", 0..6),
        ];

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(agent.prompt.text(), "git st");
    }

    /// Bash-mode agent whose draft is a paste CHIP (atomic element), with
    /// the dropdown anchor pinned to it — the state a landing would leave
    /// when the shell's token range points into the chip's raw text.
    fn chip_agent(items: Vec<CompletionItemParsed>) -> (AgentView, String) {
        let mut agent = super::test_fixtures::make_agent();
        agent.prompt_input_mode = PromptInputMode::Bash;
        agent.prompt.suggestions.enabled = false;
        agent
            .prompt
            .handle_paste("line one\nline two\nline three\nline four");
        let text = agent.prompt.text().to_owned();
        agent.prompt.suggestions.dropdown.request_text = text.clone();
        agent.prompt.suggestions.dropdown.request_cursor = agent.prompt.cursor();
        agent.prompt.suggestions.dropdown.items = items;
        (agent, text)
    }

    /// BugBot: a Fill whose range clips a paste chip used to no-op the
    /// write and STILL kick a refetch — every Tab spun fill+refetch with no
    /// draft change. The declined fill now degrades to opening the
    /// dropdown: candidates visible, nothing fetched, chip intact, and the
    /// second Tab rides the normal open-dropdown handling.
    #[test]
    fn tab_fill_clipping_paste_chip_opens_dropdown_without_refetch() {
        // Two candidates whose shared range (chip bytes 0..2, "li") fills
        // to "lima_" — a valid Fill decision over an unwritable span.
        let (mut agent, text) = chip_agent(vec![
            file_item("lima_one.txt", "lima_one.txt", 0..2),
            file_item("lima_two.txt", "lima_two.txt", 0..2),
        ]);
        let gen_before = agent.prompt.suggestions.generation();

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), text, "chip must survive the fill");
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(
            agent.prompt.suggestions.generation(),
            gen_before,
            "a declined fill must not invalidate anything"
        );

        // Second Tab goes through the open dropdown (accept path), never
        // the fetch arm — no spin.
        let _ = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert_eq!(agent.prompt.text(), text);
    }

    /// Same hole on the insta-accept arm: committing would consume the
    /// sole candidate and THEN decline the splice, leaving every Tab to
    /// refetch the same set. The probe degrades to showing the candidate.
    #[test]
    fn tab_insta_accept_clipping_paste_chip_opens_dropdown_without_refetch() {
        let (mut agent, text) = chip_agent(vec![file_item("lima_one.txt", "lima_one.txt", 0..2)]);

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), text, "chip must survive");
        assert!(agent.prompt.completion_dropdown_open());
        assert_eq!(
            agent.prompt.suggestions.dropdown.items.len(),
            1,
            "the candidate must not be consumed"
        );
    }

    /// BugBot sibling hole: the OPEN-dropdown accept (Tab/Enter/mouse all
    /// share the helper) used to consume the candidates and close before
    /// the write path declined the chip-clipping splice — leaving nothing.
    /// The probe now makes it an honest no-op: nothing consumed, dropdown
    /// up, chip/draft/generation untouched, no kick — and Enter must not
    /// fall through to send.
    #[test]
    fn dropdown_accept_clipping_paste_chip_keeps_candidates() {
        let (mut agent, text) = chip_agent(vec![
            file_item("lima_one.txt", "lima_one.txt", 0..2),
            file_item("lima_two.txt", "lima_two.txt", 0..2),
        ]);
        agent.prompt.suggestions.dropdown.open = true;
        let gen_before = agent.prompt.suggestions.generation();

        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Enter));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert_eq!(agent.prompt.text(), text, "chip must survive");
        assert!(
            agent.prompt.completion_dropdown_open(),
            "candidates stay up"
        );
        assert_eq!(
            agent.prompt.suggestions.dropdown.items.len(),
            2,
            "nothing consumed"
        );
        assert_eq!(agent.prompt.suggestions.generation(), gen_before);

        // Tab rides the same helper.
        let _ = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert_eq!(agent.prompt.suggestions.dropdown.items.len(), 2);
        assert_eq!(agent.prompt.text(), text);
    }

    /// The probe peeks the SELECTED item: with a chip-clipping row next to
    /// a plain-text row, acceptance follows the selection — no-op on the
    /// clipping one, normal accept after Down moves to the safe one.
    #[test]
    fn dropdown_accept_respects_selection_over_mixed_clip_ranges() {
        let (mut agent, _) = chip_agent(vec![]);
        agent.prompt.textarea.insert_str(" li");
        let text = agent.prompt.text().to_owned();
        agent.prompt.suggestions.dropdown.request_text = text.clone();
        agent.prompt.suggestions.dropdown.request_cursor = agent.prompt.cursor();
        let tok = text.len() - 2;
        agent.prompt.suggestions.dropdown.items = vec![
            file_item("lima_one.txt", "lima_one.txt", 0..2),
            file_item("lima_two.txt", "lima_two.txt", tok..text.len()),
        ];
        agent.prompt.suggestions.dropdown.open = true;

        // Selected = the chip-clipping row: honest no-op.
        let _ = agent.handle_prompt_key_for_test(&key(KeyCode::Enter));
        assert_eq!(agent.prompt.suggestions.dropdown.items.len(), 2);
        assert_eq!(agent.prompt.text(), text);

        // Down selects the plain-text row: accepts normally.
        let _ = agent.handle_prompt_key_for_test(&key(KeyCode::Down));
        let outcome = agent.handle_prompt_key_for_test(&key(KeyCode::Tab));
        assert!(matches!(outcome, InputOutcome::Changed));
        assert!(
            agent.prompt.text().ends_with(" lima_two.txt"),
            "safe selection must splice: {}",
            agent.prompt.text()
        );
        assert!(!agent.prompt.completion_dropdown_open());
    }

    // -- Bash-mode gating of the as-you-type pipeline ------------------------
}
