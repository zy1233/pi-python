//! Turn status line — single-row widget showing current turn activity.
//!
//! Layout: `⠧ Run command 0.2s              1m20s ⇣12k [stop]`
//!
//! - Spinner (left, slowed to ~7.5fps)
//! - Activity label (colored per activity type, truncates if needed)
//! - Phase timer `Xs` (gray, never truncates)
//! - Fill space
//! - Turn timer `Xm Ys` and optional token count `⇣Nk` (right-aligned, gray)
//! - Cancel button `[stop]` (right-aligned, red on hover)
//!
//! Hidden when idle (0 height). Appears between scrollback and prompt.

use std::time::{Duration, Instant};

use pi_workspace::permission::mcp_pretty_name_if_qualified;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use crate::acp::tracker::TurnActivity;
use crate::app::agent::{AgentCommand, AgentState};
use crate::render::line_utils::truncate_str;
use crate::theme::Theme;

/// Show each spinner frame for this many animation ticks.
/// At ~30fps, 4 ticks = ~133ms per frame = ~7.5 spinner fps.
pub(crate) const SPINNER_DIVISOR: u64 = 4;

/// Pulse speed for every "waiting on you" diamond — the drain-blocked
/// status, the pending-user-input status, and the plan-approval status
/// all share this cadence. `pulse_brightness` returns `sin²(tick*speed)`,
/// which has period π, so at ~30fps this is ~1.3s per cycle
/// (`π / (0.08 * 30) ≈ 1.31`).
///
/// Always route diamond rendering through [`pending_diamond_color`] so
/// the three call sites can never silently drift apart.
pub(crate) const USER_WAITING_PULSE_SPEED: f32 = 0.08;

/// Compute the pulsing diamond color for any "waiting on you" cue.
///
/// Blends `accent` toward `theme.bg_base` using a `sin²` pulse driven by
/// [`USER_WAITING_PULSE_SPEED`]. Brightness ranges from 0.3 (dim) to 1.0
/// (full accent) so the diamond stays visible at the trough.
///
/// Pass `theme.accent_user` for user-input waits (permission prompts,
/// `ask_user_question`, the drain-blocked idle status) and
/// `theme.accent_plan` for plan-approval waits.
pub(crate) fn pending_diamond_color(theme: &Theme, accent: Color, tick: u64) -> Color {
    let brightness = crate::theme::pulse_brightness(tick, USER_WAITING_PULSE_SPEED);
    crate::render::color::blend_color(theme.bg_base, accent, 0.3 + brightness * 0.7)
        .unwrap_or(accent)
}

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

/// Output from rendering the turn status line.
#[derive(Debug, Default)]
pub struct TurnStatusOutput {
    /// Hit area for the cancel button, if rendered.
    /// `None` when the button is not shown (idle, drain-blocked).
    pub cancel_button: Option<Rect>,
}

/// Hover state for the turn-status row's mouse affordances (`[stop]`).
/// `Some(_)` renders them; `None` marks a keyboard-only host (minimal mode —
/// no mouse capture) and suppresses all.
#[derive(Debug, Clone, Copy, Default)]
pub struct MouseButtons {
    /// Whether the mouse is over the `[stop]` cancel button.
    pub cancel_hovered: bool,
}

/// Inputs to [`render_turn_status`] — one frame's worth of turn state.
#[derive(Debug)]
pub struct TurnStatusArgs<'a> {
    pub state: &'a AgentState,
    pub activity: &'a Option<TurnActivity>,
    pub turn_elapsed: Option<Duration>,
    pub activity_started_at: Option<Instant>,
    pub tick: u64,
    pub drain_blocked: bool,
    /// Mouse affordances + hover state; `None` for keyboard-only hosts.
    pub buttons: Option<MouseButtons>,
    /// Context-window tokens used, shown as `⇣Nk`.
    pub total_tokens: Option<u64>,
    pub is_bash_turn: bool,
    pub is_pending_user_input: bool,
    /// Transparent right-side background so the row blends with the
    /// terminal's own background (minimal mode).
    pub flat_background: bool,
}

/// Render the turn status line into the given area.
///
/// The caller is responsible for only allocating a 1-row area when
/// `should_show()` returns true (and 0 rows when false).
pub fn render_turn_status(
    buf: &mut Buffer,
    area: Rect,
    args: TurnStatusArgs<'_>,
) -> TurnStatusOutput {
    let TurnStatusArgs {
        state,
        activity,
        turn_elapsed,
        activity_started_at,
        tick,
        drain_blocked,
        buttons,
        total_tokens,
        is_bash_turn,
        is_pending_user_input,
        flat_background,
    } = args;
    // Resolve the mouse affordances: a keyboard-only host (`None`) suppresses
    // the button and reports no hover.
    let show_buttons = buttons.is_some();
    let cancel_hovered = buttons.is_some_and(|b| b.cancel_hovered);
    if area.height == 0 || area.width < 10 {
        return TurnStatusOutput::default();
    }

    let theme = Theme::current();

    // Special case: drain is blocked (user editing front prompt, agent idle).
    // No cancel button in this state.
    if drain_blocked && state.is_idle() {
        // Pulsing diamond in accent_user, blending toward bg.
        let diamond_color = pending_diamond_color(&theme, theme.accent_user, tick);
        let spans = vec![
            Span::styled(
                format!("{} ", crate::glyphs::diamond_filled()),
                Style::default().fg(diamond_color),
            ),
            Span::styled(
                "agent idle ~ waiting on your edit",
                Style::default().fg(theme.gray),
            ),
        ];
        buf.set_line(area.x, area.y, &Line::from(spans), area.width);
        return TurnStatusOutput::default();
    }

    // Nothing to show while idle.
    if state.is_idle() {
        return TurnStatusOutput::default();
    }

    // Shown while running AND while cancelling (the click routes to the
    // cancel-retry path); hidden when idle or on a keyboard-only host.
    let show_cancel = show_buttons
        && matches!(
            state,
            AgentState::TurnRunning
                | AgentState::CommandRunning { .. }
                | AgentState::TurnCancelling
                | AgentState::CommandCancelling { .. }
        );

    // ── Compute activity style and label ──
    let (activity_style, label, is_tool) = compute_activity(&theme, state, activity, is_bash_turn);

    // Early return for idle (shouldn't happen if should_show is respected, but be safe).
    if matches!(state, AgentState::Idle) {
        return TurnStatusOutput::default();
    }

    // ── Build right-aligned content first (to know how much space is left) ──
    // Format: `1m20s` or `1m20s ⇣12k` (with tokens).
    let turn_timer_str = match (turn_elapsed, total_tokens) {
        (Some(d), Some(tokens)) if tokens > 0 => {
            format!(
                "{} {}{}",
                format_turn_timer(d),
                crate::glyphs::token_arrow(),
                format_tokens_short(tokens)
            )
        }
        (Some(d), _) => format_turn_timer(d),
        _ => String::new(),
    };
    let turn_timer_width = turn_timer_str.width();

    // Cancel button: always `[stop]`. Every arm is a `&'static str` so the
    // per-frame status line never allocates. Hover state is conveyed by
    // color (red on hover, see `cancel_style`), not by swapping the label.
    let cancel_str: &str = if show_cancel { " [stop]" } else { "" };
    let cancel_width = cancel_str.width();

    let right_width = turn_timer_width + cancel_width;

    // ── Build components ──
    // While a tool is blocked on a permission prompt or `ask_user_question`,
    // swap the running braille spinner for a pulsing `◆`. Same animation
    // shape the drain-blocked and plan-approval indicators already use,
    // so every "your turn" status reads with one consistent visual cue.
    let spinner_str = if is_pending_user_input {
        format!("{} ", crate::glyphs::diamond_filled())
    } else {
        let frames = crate::glyphs::braille_spinner_frames();
        let frame_idx = (tick / SPINNER_DIVISOR) as usize % frames.len();
        format!("{} ", frames[frame_idx])
    };
    let spinner_width = spinner_str.width();

    // "Ask" tools (AskUserQuestion): suppress the phase timer so the user
    // doesn't feel time-pressured while answering questions.
    let is_asking = is_tool
        && matches!(
            activity,
            Some(TurnActivity::ToolRunning { title, .. })
                if title.starts_with("Ask: ") || title.starts_with("Ask ")
        );

    // Phase timer (gray, same as turn timer) — hidden for ask tools
    let phase_timer_str = if is_asking {
        String::new()
    } else {
        activity_started_at
            .map(|t| format!(" {}", format_turn_timer(t.elapsed())))
            .unwrap_or_default()
    };
    let phase_timer_width = phase_timer_str.width();

    // Timer style (gray for both phase and turn timers).
    //
    // Right-side elements (turn timer, cancel button) must set
    // fg, bg, AND remove_modifier explicitly. fill_background() paints
    // bg_base on every cell before widgets render, but set_line() for the
    // left content may overwrite fg/modifiers on cells in the right zone.
    // A Style with bg:None (the default) cannot restore bg after a reset,
    // and a Style without remove_modifier cannot clear leaked modifiers.
    // Right-side cells normally paint `bg_base`; in a flat-background host
    // (minimal mode) use the terminal's own background so the row stays
    // transparent like the rest of the live region.
    let timer_bg = if flat_background {
        Color::Reset
    } else {
        theme.bg_base
    };
    let timer_style = Style::default()
        .fg(theme.gray)
        .bg(timer_bg)
        .remove_modifier(Modifier::all());

    // Available width for activity label (only the label truncates)
    // Layout: spinner + label + phase_timer + gap(1) + turn_timer + cancel
    let min_gap = 1;
    let available_for_label = (area.width as usize)
        .saturating_sub(spinner_width)
        .saturating_sub(phase_timer_width)
        .saturating_sub(min_gap)
        .saturating_sub(right_width);

    // ── Render left side: spinner + label (truncated) + phase_timer ──
    let mut left_spans: Vec<Span<'static>> = Vec::with_capacity(5);

    // Spinner color: usually inherits the activity color (green for tools,
    // secondary for thinking/responding, yellow for retries). While the
    // tool is parked on the user we render `◆` with a smooth pulse from
    // dim→bright in `accent_user`, matching the drain-blocked and
    // plan-approval indicators so every "your turn" status has the same
    // visual cadence.
    let spinner_style = if is_pending_user_input {
        let diamond_color = pending_diamond_color(&theme, theme.accent_user, tick);
        Style::default().fg(diamond_color)
    } else {
        activity_style
    };
    left_spans.push(Span::styled(spinner_str, spinner_style));

    // Activity label (potentially truncated)
    if is_tool {
        if let Some(TurnActivity::ToolRunning { title, description }) = activity {
            if is_asking {
                // Ask tools: render as a unified gray label (like Thinking/Responding),
                // not as a command invocation — yellow is reserved for shell commands.
                let detail = title
                    .strip_prefix("Ask: ")
                    .or_else(|| title.strip_prefix("Ask "))
                    .unwrap_or(title.as_str());
                let msg = format!("Waiting on answers for {detail}");
                let display = truncate_str(&msg, available_for_label);
                left_spans.push(Span::styled(display, activity_style));
            } else if let Some(desc) = description
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                // Bash (and similar) tools carry a human description — prefer
                // that over the raw command for the status line so a sleep /
                // long-running exec reads as `{description}…` rather than
                // `Run sleep 5 && …`.
                let msg = crate::acp::tracker::format_waiting_for_subject(desc);
                let display = truncate_str(&msg, available_for_label);
                left_spans.push(Span::styled(display, activity_style));
            } else if let Some(query) = title.strip_prefix("Web search: ") {
                // Web search: "Search " (muted) + query (yellow)
                let prefix = "Search ";
                let prefix_width = prefix.width();
                let query = query.trim_matches('"');
                let max_query = available_for_label.saturating_sub(prefix_width).max(5);
                let display = truncate_str(query, max_query);
                left_spans.push(Span::styled(prefix, Style::default().fg(theme.gray)));
                left_spans.push(Span::styled(display, Style::default().fg(theme.command)));
            } else if let Some(url) = title.strip_prefix("Fetch: ") {
                // Fetch tools: "Fetch " (muted) + URL (yellow)
                let prefix = "Fetch ";
                let prefix_width = prefix.width();
                let max_url = available_for_label.saturating_sub(prefix_width).max(5);
                let display = truncate_str(url, max_url);
                left_spans.push(Span::styled(prefix, Style::default().fg(theme.gray)));
                left_spans.push(Span::styled(display, Style::default().fg(theme.command)));
            } else {
                // Normal tool: "Run " (muted) + command (syntax-highlighted).
                // For qualified MCP tool names the activity title is the
                // raw `server__action` string from ACP; prettify it to
                // `(Server) Action` so the spinner doesn't show the ugly
                // delimiter form. Non-MCP titles (bash commands etc.) are
                // returned untouched by `mcp_pretty_name_if_qualified`.
                let prefix = "Run ";
                let pretty = mcp_pretty_name_if_qualified(title.as_str());
                let detail = pretty.as_str();
                let prefix_width = prefix.width();
                let max_cmd = available_for_label.saturating_sub(prefix_width).max(5);
                let first_line = detail.lines().next().unwrap_or(detail);
                let display = truncate_str(first_line, max_cmd);
                left_spans.push(Span::styled(prefix, Style::default().fg(theme.gray)));
                left_spans.extend(crate::views::bash_highlight::highlight_bash_command(
                    &display,
                ));
            }
        }
    } else {
        let display = truncate_str(&label, available_for_label);
        left_spans.push(Span::styled(display, activity_style));
    }

    // Phase timer (gray, never truncates)
    if !phase_timer_str.is_empty() {
        left_spans.push(Span::styled(phase_timer_str, timer_style));
    }

    // Render left side
    let left_line = Line::from(left_spans);
    buf.set_line(area.x, area.y, &left_line, area.width);

    // ── Render right side: turn_timer + cancel ──
    let right_start_x = area.x + area.width.saturating_sub(right_width as u16);

    // Helper: build a fully-specified right-side style (fg + bg + clear mods).
    let right_style = |fg| {
        Style::default()
            .fg(fg)
            .bg(timer_bg)
            .remove_modifier(Modifier::all())
    };

    // Turn timer (gray)
    let mut x = right_start_x;
    if !turn_timer_str.is_empty() {
        let span = Span::styled(turn_timer_str.clone(), timer_style);
        buf.set_span(x, area.y, &span, turn_timer_width as u16);
        x += turn_timer_width as u16;
    }

    // Cancel button — accent_error (red) on hover, gray at rest
    let cancel_button_rect = if show_cancel && !cancel_str.is_empty() {
        let cancel_x = x;
        let cancel_style = if cancel_hovered {
            right_style(theme.accent_error)
        } else {
            right_style(theme.gray)
        };
        let span = Span::styled(cancel_str, cancel_style);
        buf.set_span(x, area.y, &span, cancel_width as u16);
        Some(Rect::new(cancel_x, area.y, cancel_width as u16, 1))
    } else {
        None
    };

    TurnStatusOutput {
        cancel_button: cancel_button_rect,
    }
}

/// Compute activity style, label, and whether it's a tool.
fn compute_activity(
    theme: &Theme,
    state: &AgentState,
    activity: &Option<TurnActivity>,
    is_bash_turn: bool,
) -> (Style, String, bool) {
    match (state, activity) {
        (AgentState::TurnCancelling | AgentState::CommandCancelling { .. }, _) => (
            Style::default().fg(theme.accent_error),
            "Cancelling…".to_string(),
            false,
        ),
        (AgentState::TurnRunning, Some(TurnActivity::Thinking)) => (
            Style::default().fg(theme.text_secondary),
            "Thinking…".to_string(),
            false,
        ),
        (AgentState::TurnRunning, Some(TurnActivity::Responding)) => (
            Style::default().fg(theme.text_secondary),
            "Responding…".to_string(),
            false,
        ),
        (AgentState::TurnRunning, Some(TurnActivity::ToolRunning { title, description })) => {
            // "Ask" tools (AskUserQuestion) use gray spinner like Thinking —
            // green feels out of place when the user is answering questions.
            // Human descriptions (e.g. bash `description`) also use muted
            // secondary — they read as a wait subject (`Wait 5s…`), not a
            // green `Run <command>` invocation.
            let is_ask = title.starts_with("Ask: ") || title.starts_with("Ask ");
            let has_desc = description
                .as_deref()
                .map(str::trim)
                .is_some_and(|s| !s.is_empty());
            let style = if is_ask || has_desc {
                Style::default().fg(theme.text_secondary)
            } else {
                Style::default().fg(theme.accent_success)
            };
            (style, String::new(), true)
        }
        (AgentState::TurnRunning, Some(TurnActivity::AutoCompacting)) => (
            Style::default().fg(theme.text_secondary),
            "Compacting…".to_string(),
            false,
        ),
        (AgentState::TurnRunning, Some(TurnActivity::Retrying { attempt, .. })) => (
            Style::default().fg(theme.warning),
            format!("Retrying (attempt {attempt})…"),
            false,
        ),
        (AgentState::TurnRunning, Some(TurnActivity::WritingToolCall(writing))) => (
            Style::default().fg(theme.text_secondary),
            writing.label(),
            false,
        ),
        (AgentState::TurnRunning, Some(TurnActivity::Waiting(reason))) => (
            // Explicit wait reason (model / subagent / task output / tasks /
            // sleep): name what the agent is blocked on instead of a generic
            // "Waiting…". See `WaitingReason` and `AgentView::resolve_turn_activity`.
            Style::default().fg(theme.text_secondary),
            reason.label(),
            false,
        ),
        (AgentState::TurnRunning, None) if is_bash_turn => (
            // Bash turn: not inference, show generic "Running…".
            Style::default().fg(theme.text_secondary),
            "Running…".to_string(),
            false,
        ),
        (AgentState::TurnRunning, None) => (
            // Fallback: a running inference turn with no resolved activity. The
            // view resolves this gap into Waiting(Model) before render,
            // so this is now a rarely-hit safety net.
            Style::default().fg(theme.text_secondary),
            "Waiting…".to_string(),
            false,
        ),
        (
            AgentState::CommandRunning {
                command:
                    command @ (AgentCommand::CreateWorktree
                    | AgentCommand::RestoreWorktree
                    | AgentCommand::RestoreCode
                    | AgentCommand::ForkSession),
                ..
            },
            _,
        ) => (
            Style::default().fg(theme.gray),
            format!("{}…", command.display_name()),
            false,
        ),
        (AgentState::CommandRunning { command, .. }, _) => (
            Style::default().fg(theme.text_secondary),
            format!("{}…", command.display_name()),
            false,
        ),
        (AgentState::Idle, _) => (Style::default(), String::new(), false),
    }
}

/// Whether the turn status line should be visible.
///
/// Returns true when a turn is active (Running or Cancelling) or when the
/// drain is blocked (agent idle, waiting on user edit).
pub fn should_show(state: &AgentState, drain_blocked: bool) -> bool {
    !state.is_idle() || drain_blocked
}

/// Format a duration for the turn/phase timer.
///
/// Re-exports [`crate::util::format_duration`] under the old name for
/// backwards compatibility within this module.
pub use crate::util::format_duration as format_turn_timer;

/// Format a token count for compact display.
///
/// - Under 1000: `1`, `10`, `100` (raw number)
/// - 1k-100k: `1.23k`, `10.1k` (with decimal)
/// - 100k-1m: `100k`, `500k` (whole thousands)
/// - 1m+: `1.23m`, `10.1m` (with decimal)
fn format_tokens_short(tokens: u64) -> String {
    if tokens < 1000 {
        format!("{tokens}")
    } else if tokens < 100_000 {
        // 1k-99.9k: show one or two decimals for precision
        let k = tokens as f64 / 1000.0;
        if tokens < 10_000 {
            format!("{k:.2}k") // 1.23k
        } else {
            format!("{k:.1}k") // 10.1k
        }
    } else if tokens < 1_000_000 {
        // 100k-999k: whole thousands
        let k = tokens / 1000;
        format!("{k}k")
    } else {
        // 1m+: show with decimal
        let m = tokens as f64 / 1_000_000.0;
        if tokens < 10_000_000 {
            format!("{m:.2}m") // 1.23m
        } else {
            format!("{m:.1}m") // 10.1m
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[test]
    fn format_subsecond() {
        assert_eq!(format_turn_timer(Duration::from_millis(500)), "0.5s");
        assert_eq!(format_turn_timer(Duration::from_millis(120)), "0.1s");
    }

    #[test]
    fn format_under_10s_has_decimal() {
        assert_eq!(format_turn_timer(Duration::from_secs_f64(5.2)), "5.2s");
        assert_eq!(format_turn_timer(Duration::from_secs_f64(9.9)), "9.9s");
    }

    #[test]
    fn format_10s_plus_no_decimal() {
        assert_eq!(format_turn_timer(Duration::from_secs(10)), "10s");
        assert_eq!(format_turn_timer(Duration::from_secs(32)), "32s");
        assert_eq!(format_turn_timer(Duration::from_secs(59)), "59s");
    }

    #[test]
    fn format_minutes() {
        assert_eq!(format_turn_timer(Duration::from_secs(60)), "1m0s");
        assert_eq!(format_turn_timer(Duration::from_secs(80)), "1m20s");
        assert_eq!(format_turn_timer(Duration::from_secs(600)), "10m0s");
    }

    #[test]
    fn activity_label_defaults_to_waiting_and_follows_streaming_activity() {
        let theme = Theme::current();
        // Running turn, no streaming activity → generic "Waiting…".
        let (_, label, _) = compute_activity(&theme, &AgentState::TurnRunning, &None, false);
        assert_eq!(label, "Waiting…");
        let (_, label, _) = compute_activity(
            &theme,
            &AgentState::TurnRunning,
            &Some(TurnActivity::Responding),
            false,
        );
        assert_eq!(label, "Responding…");
    }

    #[test]
    fn waiting_reason_renders_specific_label() {
        use crate::acp::tracker::WaitingReason;
        let theme = Theme::current();
        let cases = [
            (WaitingReason::Model, "Waiting for response…"),
            (WaitingReason::task_output(), "Waiting on task output…"),
            (
                WaitingReason::TaskOutput {
                    task_ids: vec!["t1".into()],
                    subject: Some("compile release".into()),
                    waits: false,
                },
                "compile release…",
            ),
            (WaitingReason::TasksComplete, "Waiting on tasks…"),
            (WaitingReason::Sleep, "Sleeping…"),
        ];
        for (reason, expected) in cases {
            let (_, label, is_tool) = compute_activity(
                &theme,
                &AgentState::TurnRunning,
                &Some(TurnActivity::Waiting(reason.clone())),
                false,
            );
            assert_eq!(label, expected, "reason {reason:?}");
            assert!(!is_tool, "waiting is not a tool activity");
        }
    }

    #[test]
    fn bash_turn_still_renders_running_not_waiting() {
        let theme = Theme::current();
        // A bash (non-inference) turn with no activity keeps its own "Running…"
        // label — the view leaves it as `None` rather than Waiting(Model).
        let (_, label, _) = compute_activity(&theme, &AgentState::TurnRunning, &None, true);
        assert_eq!(label, "Running…");
    }

    #[test]
    fn format_hours() {
        assert_eq!(format_turn_timer(Duration::from_secs(3600)), "1h0m");
        assert_eq!(format_turn_timer(Duration::from_secs(3725)), "1h2m");
    }

    #[test]
    fn should_show_when_running() {
        assert!(should_show(&AgentState::TurnRunning, false));
        assert!(should_show(&AgentState::TurnCancelling, false));
        assert!(!should_show(&AgentState::Idle, false));
    }

    /// Cancelling keeps `[stop]` clickable (the retry affordance for a lost
    /// cancel); a revert to the running-only gate strands mouse users.
    #[test]
    fn cancelling_keeps_stop_button_clickable() {
        let mut buf = Buffer::empty(Rect::new(0, 0, 80, 1));
        let output = render_turn_status(
            &mut buf,
            Rect::new(0, 0, 80, 1),
            TurnStatusArgs {
                state: &AgentState::TurnCancelling,
                activity: &None,
                turn_elapsed: Some(Duration::from_secs(3)),
                activity_started_at: None,
                tick: 0,
                drain_blocked: false,
                buttons: Some(MouseButtons::default()),
                total_tokens: None,
                is_bash_turn: false,
                is_pending_user_input: false,
                flat_background: false,
            },
        );
        assert!(
            output.cancel_button.is_some(),
            "cancelling must keep a clickable [stop] (cancel-retry affordance)"
        );
        let text = buffer_text(&buf, Rect::new(0, 0, 80, 1));
        assert!(
            text.contains("Cancelling") && text.contains("[stop]"),
            "got: {text:?}"
        );
    }

    #[test]
    fn should_show_when_drain_blocked() {
        assert!(should_show(&AgentState::Idle, true));
    }

    /// Collect every rendered glyph in `area` into a single string.
    fn buffer_text(buf: &Buffer, area: Rect) -> String {
        (area.y..area.y + area.height)
            .map(|y| {
                (area.x..area.x + area.width)
                    .filter_map(|x| buf.cell((x, y)).map(|c| c.symbol().to_string()))
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Baseline render args: idle agent on a mouse host.
    fn idle_args<'a>() -> TurnStatusArgs<'a> {
        TurnStatusArgs {
            state: &AgentState::Idle,
            activity: &None,
            turn_elapsed: None,
            activity_started_at: None,
            tick: 0,
            drain_blocked: false,
            buttons: Some(MouseButtons::default()),
            total_tokens: None,
            is_bash_turn: false,
            is_pending_user_input: false,
            flat_background: false,
        }
    }

    /// Render `args` into a `width`×1 row.
    fn render_row(args: TurnStatusArgs<'_>, width: u16) -> (TurnStatusOutput, Buffer) {
        let area = Rect::new(0, 0, width, 1);
        let mut buf = Buffer::empty(area);
        let output = render_turn_status(&mut buf, area, args);
        (output, buf)
    }

    /// Render `args` into a `width`×1 row, returning the visible text.
    fn render_row_text(args: TurnStatusArgs<'_>, width: u16) -> String {
        let (_, buf) = render_row(args, width);
        buffer_text(&buf, buf.area)
    }

    #[test]
    fn idle_renders_nothing() {
        let text = render_row_text(idle_args(), 60);
        assert!(
            text.trim().is_empty(),
            "idle with nothing pending must render nothing, got: {text:?}"
        );
    }

    #[test]
    fn format_tokens_under_1k() {
        assert_eq!(format_tokens_short(0), "0");
        assert_eq!(format_tokens_short(1), "1");
        assert_eq!(format_tokens_short(10), "10");
        assert_eq!(format_tokens_short(100), "100");
        assert_eq!(format_tokens_short(999), "999");
    }

    #[test]
    fn format_tokens_1k_to_10k() {
        assert_eq!(format_tokens_short(1000), "1.00k");
        assert_eq!(format_tokens_short(1230), "1.23k");
        assert_eq!(format_tokens_short(1500), "1.50k");
        assert_eq!(format_tokens_short(9990), "9.99k");
        assert_eq!(format_tokens_short(9999), "10.00k"); // rounds up
    }

    #[test]
    fn format_tokens_10k_to_100k() {
        assert_eq!(format_tokens_short(10000), "10.0k");
        assert_eq!(format_tokens_short(10100), "10.1k");
        assert_eq!(format_tokens_short(12345), "12.3k");
        assert_eq!(format_tokens_short(99999), "100.0k"); // rounds up
    }

    #[test]
    fn format_tokens_100k_to_1m() {
        assert_eq!(format_tokens_short(100000), "100k");
        assert_eq!(format_tokens_short(128000), "128k");
        assert_eq!(format_tokens_short(500000), "500k");
        assert_eq!(format_tokens_short(999000), "999k");
    }

    #[test]
    fn format_tokens_millions() {
        assert_eq!(format_tokens_short(1_000_000), "1.00m");
        assert_eq!(format_tokens_short(1_230_000), "1.23m");
        assert_eq!(format_tokens_short(9_999_000), "10.00m"); // rounds
        assert_eq!(format_tokens_short(10_000_000), "10.0m");
        assert_eq!(format_tokens_short(10_100_000), "10.1m");
    }

    #[test]
    fn user_waiting_pulse_speed_is_stable() {
        // The drain-blocked, pending-user-input, and plan-approval cues
        // all read from this single constant via `pending_diamond_color`,
        // so this assertion guards against an accidental tweak that
        // would silently change the cadence of every "your turn" cue.
        assert_eq!(USER_WAITING_PULSE_SPEED, 0.08);
    }
}
