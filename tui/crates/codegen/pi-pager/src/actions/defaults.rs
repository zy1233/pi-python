//! Default action definitions for the MVP.
//!
//! All key bindings are defined here — not scattered across event handlers.

use crate::key;
use crate::terminal::terminal_context;

use super::{ActionDef, ActionId, Category, When};

/// True when `Ctrl+.` is not a reliable shortcuts-cheatsheet primary.
///
/// Callers pick a deliverable alternate primary (`Ctrl+X` on the agent
/// screen). Both keys stay registered either way;
/// this only chooses which the UI advertises.
///
/// Driven by [`crate::terminal::TerminalContext::ctrl_dot_unreliable`]
/// (any KKP skip — brand, tmux `extended-keys off`, screen, unknown host),
/// plus host-OS signals: native Windows on a non-branded console, or a
/// Linux binary inside Win32's console pipeline (WSL).
pub fn ctrl_dot_unreliable() -> bool {
    terminal_context().ctrl_dot_unreliable() || cfg!(target_os = "windows") || crate::host::is_wsl()
}

/// The agent-screen action that owns Ctrl+G, if any: the external-editor
/// shortcut in minimal mode only (fullscreen leaves Ctrl+G unbound).
fn mode_ctrl_g_action(screen_mode: crate::app::ScreenMode) -> Option<ActionDef> {
    screen_mode.is_minimal().then(|| ActionDef {
        id: ActionId::EditPromptExternal,
        label: "edit prompt",
        description: "Edit prompt in external editor",
        default_key: key!('g', CONTROL),
        alt_keys: vec![],
        category: Category::Input,
        context: When::AgentScreen,
        hint_priority: None,
        hint_key_display: None,
        requires_confirmation: false,
        long_help: Some(
            "Opens the current prompt draft in $VISUAL or $EDITOR, falling back to vi when neither is set.\nSaving and closing the editor returns the updated text to the composer; it does not send the prompt.\nAvailable in minimal mode for ordinary attachment-free drafts.",
        ),
    })
}

/// Build the default action definitions for a screen mode.
///
/// `mouse_reporting_toggle_enabled` gates the opt-in `ToggleMouseCapture`
/// shortcut (see below); pass `false` for the standard set.
pub(super) fn default_actions(
    screen_mode: crate::app::ScreenMode,
    mouse_reporting_toggle_enabled: bool,
) -> Vec<ActionDef> {
    let ctx = terminal_context();
    // xterm.js embeds: no KKP; host often steals Ctrl+I. Share one family flag for
    // quit / half-page so VS Code-family embeds match VS Code.
    let in_vscode_family = ctx.brand.is_vscode_family();
    let in_vscode = in_vscode_family;
    // ToggleQueue takes Ctrl+4 as its primary on local macOS VS Code-family hosts.
    let local_mac_vscode = in_vscode_family && !ctx.is_ssh && cfg!(target_os = "macos");
    let ctrl_dot_unreliable = ctrl_dot_unreliable();

    let mut actions = vec![
        // ── Navigation (scrollback) ─────────────────────────────────
        ActionDef {
            id: ActionId::SelectNext,
            label: "nav",
            description: "Select next entry",
            default_key: key!('j'),
            alt_keys: vec![key!(Down)],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: Some(0),
            hint_key_display: Some("j/k"),
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::SelectPrev,
            label: "nav",
            description: "Select previous entry",
            default_key: key!('k'),
            alt_keys: vec![key!(Up)],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::NextTurn,
            label: "turn",
            description: "Next turn",
            default_key: key!('L'),
            alt_keys: vec![key!(Right, SHIFT)],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: Some(1),
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::PrevTurn,
            label: "turn",
            description: "Previous turn",
            default_key: key!('H'),
            alt_keys: vec![key!(Left, SHIFT)],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::NextResponse,
            label: "response",
            description: "Next response",
            default_key: key!('J'),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::PrevResponse,
            label: "response",
            description: "Previous response",
            default_key: key!('K'),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::GotoTop,
            label: "top/btm",
            description: "Go to top",
            default_key: key!('g'),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: Some(4),
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::GotoBottom,
            label: "bottom",
            description: "Go to bottom",
            default_key: key!('G'),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::ScrollUp,
            label: "scroll up",
            description: "Scroll up one line",
            default_key: key!('k', CONTROL),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::ScrollDown,
            label: "scroll down",
            description: "Scroll down one line",
            default_key: key!('j', CONTROL),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::HalfPageUp,
            label: "half page up",
            description: "Scroll up half page",
            default_key: key!('u', CONTROL),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::HalfPageDown,
            label: "half page down",
            description: "Scroll down half page",
            default_key: if in_vscode {
                key!('D')
            } else {
                key!('d', CONTROL)
            },
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::PageUp,
            label: "page up",
            description: "Scroll up one page",
            default_key: key!(PageUp),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::PageDown,
            label: "page down",
            description: "Scroll down one page",
            default_key: key!(PageDown),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        // ── View (scrollback) ───────────────────────────────────────
        ActionDef {
            id: ActionId::Collapse,
            label: "fold",
            description: "Collapse selected entry",
            default_key: key!('h'),
            alt_keys: vec![key!(Left)],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::Expand,
            label: "fold",
            description: "Expand selected entry",
            default_key: key!('l'),
            alt_keys: vec![key!(Right)],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::ToggleFold,
            label: "fold",
            description: "Expand / collapse",
            default_key: key!('e'),
            alt_keys: vec![],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: Some(3),
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Folds or unfolds the selected scrollback entry to hide or show its full body.\nHandy for skimming long tool output or reasoning.\nRelated: E folds/unfolds every entry, Ctrl+E toggles all thinking blocks.",
            ),
        },
        ActionDef {
            id: ActionId::ToggleExpandAll,
            label: "all",
            description: "Expand all / collapse all",
            default_key: key!('E'),
            alt_keys: vec![],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Folds or unfolds every scrollback entry at once, unlike e which toggles only the selected row.\nCollapse a long transcript to scan headers, then expand it all back.\nThinking blocks have their own toggle, Ctrl+E.",
            ),
        },
        ActionDef {
            id: ActionId::ExpandAllThinking,
            label: "expand/collapse thinking",
            description: "Toggle all thinking blocks",
            default_key: key!('e', CONTROL),
            alt_keys: vec![],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: Some(3),
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Shows or hides the agent's reasoning (thinking) blocks across the whole transcript in one keypress.\nReveal how the agent reached an answer, or hide reasoning to focus on results.\nSeparate from E, which folds every entry regardless of type.",
            ),
        },
        ActionDef {
            id: ActionId::ToggleRaw,
            label: "raw",
            description: "Toggle raw markdown",
            default_key: key!('r'),
            alt_keys: vec![],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Switches the selected entry between rendered markdown and its raw source text.\nUse it to copy exact markdown, inspect a link target, or see formatting the renderer hides.\nPress again to return to the rendered view.",
            ),
        },
        // ── Block content ────────────────────────────────────────────
        ActionDef {
            id: ActionId::CopyBlockContent,
            label: "copy",
            description: "Copy content",
            default_key: key!('y'),
            alt_keys: vec![],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None, // shown dynamically when block supports copy
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Copies the selected block's body to the clipboard: message text, full tool output, or a code block's contents.\nOffered only on blocks that support copy.\nFor just the command or file path, use Y instead.",
            ),
        },
        ActionDef {
            id: ActionId::CopyBlockMeta,
            label: "copy cmd",
            description: "Copy command / path",
            default_key: key!('Y'),
            alt_keys: vec![],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Copies only the block's identifier: a tool call's command line or a file block's path, not the body.\nHandy to re-run a command or paste a path elsewhere.\nUse lowercase y to copy the full content instead.",
            ),
        },
        ActionDef {
            id: ActionId::OpenBlockViewer,
            label: "view",
            description: "Open in viewer",
            default_key: key!(Enter),
            alt_keys: vec![key!('f', CONTROL)],
            category: Category::ConversationAction,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Opens the selected block in a focused, scrollable full-screen viewer.\nBest for long tool output, large files, or code you want to read away from the surrounding transcript.\nEsc returns to the conversation.",
            ),
        },
        // ── Link navigation ─────────────────────────────────────────
        ActionDef {
            id: ActionId::OpenNextLink,
            label: "link",
            description: "Next link",
            default_key: key!('o'),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::OpenPrevLink,
            label: "link",
            description: "Previous link",
            default_key: key!('O'),
            alt_keys: vec![],
            category: Category::ConversationNav,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        // ── Scrollback (contextual — block-type-dependent) ────────────
        // ── Essentials ────────────────────────────────────────────────
        ActionDef {
            id: ActionId::SendPrompt,
            label: "send",
            description: "Send",
            default_key: key!(Enter),
            alt_keys: vec![],
            category: Category::GettingStarted,
            context: When::PromptFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::FocusPrompt,
            label: "prompt",
            description: "Focus prompt",
            default_key: key!(Tab),
            alt_keys: vec![key!('i'), key!(' ')],
            category: Category::GettingStarted,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            id: ActionId::FocusScrollback,
            label: "scrollback",
            description: "Focus scrollback",
            default_key: key!(Tab),
            alt_keys: vec![],
            category: Category::GettingStarted,
            context: When::PromptFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Moves focus from the prompt to the scrollback so you can navigate the transcript.\nTab works in both simple and vim scrollback modes.\nEsc is reserved for the cancel / clear / rewind policy, not focus.",
            ),
        },
        ActionDef {
            id: ActionId::CancelTurn,
            label: "cancel",
            description: "Cancel turn",
            default_key: key!('c', CONTROL),
            alt_keys: vec![],
            category: Category::GettingStarted,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Interrupts the agent's current turn and stops generation, keeping the session open.\nEsc cancels immediately while a turn is running in minimal mode or when vim scrollback mode is off (prompt or scrollback focused, even with a draft).\nCtrl+C cancels when the prompt is empty; with a non-empty draft it clears the prompt first and leaves the turn running.\nIt stops the turn, not the app; use the quit shortcut to exit.",
            ),
        },
        ActionDef {
            id: ActionId::CycleMode,
            label: "mode",
            description: "Cycle mode (Normal / Plan / Always-approve)",
            // All Shift+Tab encodings — see `input::key::shift_tab_keys()`.
            default_key: crate::input::key::shift_tab_keys()[0],
            alt_keys: crate::input::key::shift_tab_keys()[1..].to_vec(),
            category: Category::GettingStarted,
            context: When::PromptFocused,
            hint_priority: None,
            hint_key_display: Some("Shift+Tab"),
            requires_confirmation: false,
            long_help: Some(
                "Steps the session mode: Normal -> Plan -> Always-Approve -> Normal.\nPlan keeps the agent planning first and writes no files; Always-Approve runs every tool call without asking.\nCtrl+O toggles auto-approve directly.",
            ),
        },
        // ── Panes (agent-level — toggle side panes) ─────────────────
        ActionDef {
            id: ActionId::ToggleTodos,
            label: "todos",
            description: "Toggle todo pane",
            default_key: key!('t', CONTROL),
            alt_keys: vec![],
            category: Category::Panels,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Shows or hides the todo pane: the agent's live task checklist for the current work.\nWatch what it plans to do and what's left as the turn runs.\nA side pane; toggle it off to reclaim width.",
            ),
        },
        ActionDef {
            id: ActionId::ToggleQueue,
            label: "queue",
            description: "Toggle prompt queue",
            // Local macOS VS Code family only: ; / ' often never arrive (saw
            // Ctrl+4 in input-debug). SSH and non-Mac keep ; (+ ' alt). Win/Linux
            // VS maps Ctrl+4 to focusFourthEditorGroup.
            default_key: if local_mac_vscode {
                key!('4', CONTROL)
            } else {
                key!(';', CONTROL)
            },
            // Apostrophe alt for consoles that drop Ctrl on `;`. Local Mac VS
            // also keeps ; / ' as alts alongside primary Ctrl+4.
            alt_keys: if local_mac_vscode {
                vec![key!(';', CONTROL), key!('\'', CONTROL)]
            } else {
                vec![key!('\'', CONTROL)]
            },
            category: Category::Panels,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Shows or hides the prompt queue.\nThe queue lets you line up follow-up prompts while a turn is running; each is sent automatically when the agent finishes.\nLocal macOS VS Code family: Ctrl+4 primary (Ctrl+; / Ctrl+' alts). Otherwise Ctrl+; with Ctrl+' alt.",
            ),
        },
        ActionDef {
            id: ActionId::OpenSessions,
            label: "sessions",
            description: "Open sessions",
            default_key: key!(F(3)),
            alt_keys: vec![],
            category: Category::Panels,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Opens the session browser to resume or switch between past conversations.\nSelect one to reattach to its full history. `/resume` does the same.",
            ),
        },
        // ── Prompt ───────────────────────────────────────────────────
        ActionDef {
            id: ActionId::EnableVoiceMode,
            label: "voice mode",
            description: "Start voice dictation (Ctrl+Space / F8)",
            // No key binding (`KeyCode::Null`): dispatched directly by the voice
            // chord's hold-to-talk press in the event loop, not via the registry.
            default_key: key!(Null),
            alt_keys: vec![],
            category: Category::Input,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
        ActionDef {
            // Voice capture chord (same surface as `/voice`; Esc/Enter stop).
            // Bound to BOTH Ctrl+Space and F8 — Ctrl+Space decodes on every
            // terminal (without the Kitty protocol it collapses to NUL, reported
            // as `Char(' ')`+CONTROL), and F8 is a fallback for OSes/terminals
            // that intercept Ctrl+Space (e.g. macOS input-source switching; use
            // Fn+F8 on a laptop). The event loop maps a press to hold-to-talk or
            // tap-toggle per `[ui].voice_capture_mode` before normal routing.
            id: ActionId::VoiceToggle,
            label: "mic",
            description: "Voice dictation (Ctrl+Space / F8)",
            default_key: key!(' ', CONTROL),
            alt_keys: vec![key!(F(8))],
            category: Category::Input,
            // `Always` so the toggle key works on the agent screen
            // (resolved via the global fallthrough).
            context: When::Always,
            hint_priority: Some(11),
            hint_key_display: Some("Ctrl+Space / F8"),
            requires_confirmation: false,
            long_help: Some(
                "Microphone capture for dictation, bound to Ctrl+Space (or F8: handy where Ctrl+Space is taken, e.g. macOS input-source switching; use Fn+F8 on a laptop).\nBehavior follows the Voice capture setting: toggle (press to start, press again to stop) or hold-to-talk (hold to record, release to stop), where hold needs a Kitty-protocol terminal and falls back to toggle elsewhere. `/voice` toggles everywhere.\nSpeech is transcribed straight into the prompt.",
            ),
        },
        // Prompt history has no key chord (Ctrl+R is deliberately unbound):
        // `/history` opens the search panel; Up on an empty prompt browses.
        ActionDef {
            id: ActionId::ToggleMultiline,
            label: "multiline",
            description: "Toggle multiline",
            default_key: key!('m', CONTROL),
            alt_keys: vec![],
            category: Category::Input,
            context: When::PromptFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Toggles a persistent multi-line prompt so the editor stays expanded for composing longer messages.\nInsert newlines with Shift+Enter or Alt+Enter (or a trailing backslash); bare Enter still sends.\nCtrl+M toggles multiline in the prompt.",
            ),
        },
        ActionDef {
            id: ActionId::StashPrompt,
            label: "stash",
            description: "Stash / pop prompt draft",
            default_key: key!('s', CONTROL),
            // The escape hatch for terminals that swallow Ctrl+S as XOFF.
            alt_keys: vec![key!('s', ALT)],
            category: Category::Input,
            context: When::PromptFocused,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Stash your current prompt as a draft.\nCtrl+S sets the draft aside and clears the composer. Ctrl+S on an empty composer restores it. The draft also restores by itself after you send your next prompt. Use Alt+S if your terminal swallows Ctrl+S.\nOne draft at a time: a new stash replaces the old one.",
            ),
        },
        ActionDef {
            id: ActionId::BashMode,
            label: "shell",
            description: "Shell mode (type ! on empty prompt)",
            default_key: key!('!'),
            alt_keys: vec![],
            category: Category::Input,
            context: When::PromptFocused,
            hint_priority: None,
            hint_key_display: Some("!"),
            requires_confirmation: false,
            long_help: Some(
                "Runs a shell command without leaving the chat: type ! at the start of an empty prompt, then the command.\nThe command output is captured into the scrollback.\nDelete the leading ! to go back to a normal prompt.",
            ),
        },
        // ── Agent ────────────────────────────────────────────────────
        ActionDef {
            id: ActionId::ToggleYolo,
            label: "yolo",
            description: "Toggle always-approve",
            default_key: key!('o', CONTROL),
            alt_keys: vec![],
            category: Category::Session,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Turns auto-approve (YOLO) on or off for this session.\nWhile on, the agent runs every tool call (edits, shell, deletes) with no per-action confirmation.\nSame state as the Shift+Tab cycle's Always-Approve; use with care.",
            ),
        },
        ActionDef {
            id: ActionId::NewSession,
            label: "new",
            description: "New session",
            default_key: key!('n', CONTROL),
            alt_keys: vec![],
            category: Category::Session,
            context: When::Always,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: true,
            long_help: Some(
                "Starts a fresh session with empty scrollback and context.\nRequires confirmation: press it twice (the first press arms, the second starts)\nso you don't discard the current conversation by accident.",
            ),
        },
        ActionDef {
            id: ActionId::Quit,
            label: "quit",
            description: "Quit",
            default_key: if in_vscode {
                key!('d', CONTROL)
            } else {
                key!('q', CONTROL)
            },
            alt_keys: if in_vscode {
                vec![]
            } else {
                vec![key!('d', CONTROL)]
            },
            category: Category::GettingStarted,
            context: When::Always,
            hint_priority: Some(10),
            hint_key_display: None,
            requires_confirmation: true,
            long_help: Some(
                "Exits the app. Requires confirmation: press twice in quick succession;\na lone press is treated as a stray key and ignored.\nBound to Ctrl+Q, with Ctrl+D as an alias (Ctrl+D is primary in VS Code's terminal).",
            ),
        },
        ActionDef {
            id: ActionId::CommandPalette,
            label: "commands",
            description: "Command palette",
            default_key: key!('p', CONTROL),
            alt_keys: vec![key!('?')],
            category: Category::GettingStarted,
            context: When::AgentScreen,
            hint_priority: Some(5),
            hint_key_display: Some("?"),
            requires_confirmation: false,
            long_help: Some(
                "Fuzzy-search every action and slash command, then run it by name.\nUseful when you don't remember a key binding.\nAlso opens with ? while the scrollback is focused.",
            ),
        },
        ActionDef {
            id: ActionId::ShortcutsHelp,
            label: "shortcuts",
            description: "Keyboard shortcuts",
            default_key: if ctrl_dot_unreliable {
                key!('x', CONTROL)
            } else {
                key!('.', CONTROL)
            },
            alt_keys: vec![if ctrl_dot_unreliable {
                key!('.', CONTROL)
            } else {
                key!('x', CONTROL)
            }],
            category: Category::GettingStarted,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Opens this keyboard cheatsheet.\nBrowse with j/k, expand a row's inline help with e, or press Enter for a shortcut's full detail page.\nBound to both Ctrl+. and Ctrl+X; the bar advertises whichever your terminal sends reliably.",
            ),
        },
        ActionDef {
            id: ActionId::ModelPicker,
            label: "model",
            description: "Pick model",
            default_key: key!(F(4)),
            alt_keys: vec![key!('m', ALT)],
            category: Category::Session,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: Some(
                "Opens the model picker to switch the model for this session; the choice applies to later turns.\nBound to F4 (Alt+M is an alias).\nReach it from the scrollback or the command palette.",
            ),
        },
        ActionDef {
            id: ActionId::OpenSettings,
            label: "settings",
            description: "Open the settings modal",
            default_key: key!(F(2)),
            alt_keys: vec![key!(',', CONTROL), key!(',', SUPER)],
            category: Category::GettingStarted,
            context: When::AgentScreen,
            hint_priority: None,
            hint_key_display: None,
            requires_confirmation: false,
            long_help: None,
        },
    ];

    // Ctrl+G: external editor (minimal mode only).
    actions.extend(mode_ctrl_g_action(screen_mode));

    // Toggle terminal mouse reporting (mouse capture). Opt-in via
    // `[ui] mouse_reporting_toggle = true` in config.toml. Disabling capture
    // hands mouse selection back to the terminal for native click-drag
    // copy/paste; re-enabling restores in-app mouse support.
    //
    // Single binding: Ctrl+R on scrollback only (not prompt — Ctrl+R there
    // remains prompt history search). Plain Ctrl+letter passes through Apple
    // Terminal; avoids Ctrl+Shift+… chords that Terminal.app often swallows.
    // Under Panels (not Essentials) — advanced/opt-in only.
    if mouse_reporting_toggle_enabled {
        actions.push(ActionDef {
            id: ActionId::ToggleMouseCapture,
            label: "mouse reporting",
            description: "Toggle mouse reporting (native copy/paste)",
            default_key: key!('r', CONTROL),
            alt_keys: vec![],
            category: Category::Panels,
            context: When::ScrollbackFocused,
            hint_priority: None,
            hint_key_display: Some("Ctrl+r"),
            requires_confirmation: false,
            long_help: None,
        });
    }

    // Minimal has no interactive scrollback surface. Keep its
    // logical prompt, agent-screen, and legitimate global actions, but do not
    // register bindings whose target UI cannot exist in this process mode.
    if screen_mode.is_minimal() {
        actions.retain(|def| {
            !matches!(def.context, When::ScrollbackFocused)
                && !matches!(def.id, ActionId::FocusScrollback)
        });
    }

    actions
}
