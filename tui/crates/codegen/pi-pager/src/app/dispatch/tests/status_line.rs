//! Status row dispatch: what it draws, when it recomputes, and which results
//! it still accepts.

use super::*;

use std::time::{Duration, Instant};

use crate::app::app_view::TickDemand;
use crate::app::status_line::{
    ABANDON_AFTER, EVENT_DEBOUNCE, MIN_REFRESH_INTERVAL_MS, RunId, RunOutcome, test_context,
};
use crate::views::status_line::{StatusLineDisplay, StatusLineFrame, StatusSegment};
use pi_status_line::test_support::StatusLineConfigFixture;
use pi_status_line::{StatusLineConfig, StatusLineItem, StatusLineType};

/// A `command` is set whatever the mode, so a test can switch mode without
/// losing the script.
fn command_row(kind: StatusLineType) -> StatusLineConfigFixture {
    StatusLineConfigFixture::from_kind(kind).with_command("true")
}

fn status_line_app(kind: StatusLineType) -> AppView {
    let mut app = test_app_with_agent();
    app.current_ui.status_line = command_row(kind).into_config();
    app.agents.get_mut(&AgentId(0)).unwrap().status_context = Some(test_context("/tmp"));
    app
}

fn refresh_timer_app() -> AppView {
    let mut app = status_line_app(StatusLineType::Command);
    app.current_ui.status_line = command_row(StatusLineType::Command)
        .with_refresh_interval(Some(300))
        .into_config();
    app
}

fn settled_refresh_timer_app(now: Instant) -> AppView {
    let mut app = refresh_timer_app();
    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "the first run starts as ever");
    app.pending_effects.clear();
    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output("row".to_string()));
    app
}

/// A row that painted one result and has a second run outstanding. Moves the
/// caller's clock to the instant that second run began, so a deadline the
/// caller measures runs from the run it is about.
fn app_with_a_second_run(now: &mut Instant) -> AppView {
    let mut app = status_line_app(StatusLineType::Command);
    app.update_status_line_at(*now);
    app.on_status_line_command_finished_at(*now, RunId(0), RunOutcome::Output("drawn".to_string()));
    *now += EVENT_DEBOUNCE;
    app.update_status_line_at(*now);
    assert!(
        app.status_line.is_settled() && app.status_line.command_in_flight(*now),
        "the callers below prove nothing without both"
    );
    app
}

fn queued_a_run(app: &AppView) -> bool {
    matches!(
        app.pending_effects.as_slice(),
        [Effect::RunStatusLineCommand(_)]
    )
}

#[test]
fn refresh_timer_reruns_an_idle_settled_row() {
    let mut now = Instant::now();
    let mut app = settled_refresh_timer_app(now);
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "between timer fires an idle settled row still parks the loop"
    );

    now += Duration::from_secs(300);
    app.note_status_line_refresh_due_at(now);
    assert!(
        queued_a_run(&app),
        "the timer is the one thing that re-runs an idle settled row"
    );
}

#[test]
fn refresh_nudge_without_a_timer_config_runs_nothing() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);

    app.update_status_line_at(now);
    app.pending_effects.clear();
    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output("row".to_string()));

    now += Duration::from_secs(300);
    app.note_status_line_refresh_due_at(now);
    assert!(!queued_a_run(&app), "no refresh_interval, no timer runs");
    assert!(
        !app.status_line.refresh_due(),
        "and no due refresh left demanding ticks"
    );
}

#[test]
fn owed_refresh_is_deferred_never_dropped_and_consumed_by_the_first_run() {
    let mut now = Instant::now();
    let mut app = settled_refresh_timer_app(now);
    app.note_status_line_refresh_due_at(now);
    assert!(!queued_a_run(&app), "inside the floor even the timer waits");
    assert!(app.status_line.refresh_due(), "deferred, not dropped");
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);
    assert!(
        queued_a_run(&app),
        "past the floor the owed refresh runs without waiting out the debounce"
    );
    assert!(!app.status_line.refresh_due(), "consumed by that run");

    let mut now = Instant::now();
    let mut app = refresh_timer_app();
    app.active_view = ActiveView::Welcome;
    now += Duration::from_secs(300);
    app.note_status_line_refresh_due_at(now);
    assert!(!queued_a_run(&app), "no agent, nothing to describe");
    assert!(app.status_line.refresh_due(), "kept through the invalidate");
    app.active_view = ActiveView::Agent(AgentId(0));
    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "the first run after an agent appears");
    assert!(
        !app.status_line.refresh_due(),
        "consumed by that run, which carries the refresh_interval trigger"
    );
}

#[test]
fn refresh_with_no_agent_behind_the_view_settles_empty_and_parks_the_loop() {
    let mut now = Instant::now();
    let mut app = refresh_timer_app();
    // The view still names the agent, but the agent is gone: nothing to describe.
    app.agents.shift_remove(&AgentId(0));

    now += Duration::from_secs(300);
    app.note_status_line_refresh_due_at(now);
    assert!(!queued_a_run(&app), "no agent, nothing to run");
    assert!(
        !app.status_line.refresh_due(),
        "settling empty takes the request with it: whatever starved this \
         update of a context starves the run the refresh waits for too"
    );
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "a refresh nothing can answer must not become a stranded wake"
    );
}

#[test]
fn row_that_cannot_resolve_paints_the_problem_verbatim() {
    let now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);
    app.current_ui.status_line =
        StatusLineConfigFixture::from_kind(StatusLineType::Command).into_config();

    app.update_status_line_at(now);

    // A warning segment, not dim text: the row is chrome, and this is the one
    // thing in it the user has to notice.
    let problem = StatusLineDisplay::Segments(vec![StatusSegment::warn(
        "[ui.status_line] type = \"command\" needs command = \"…\"",
    )]);
    assert_eq!(app.status_line.display().as_deref(), Some(&problem));
}

/// The model list of an agent that offers one model, which is the current one.
fn model_state(id: &str, name: &str) -> crate::acp::model_state::ModelState {
    let id = acp::ModelId::new(std::sync::Arc::from(id));
    let mut models = crate::acp::model_state::ModelState::default();
    models.available.insert(
        id.clone(),
        acp::ModelInfo::new(id.clone(), name.to_string()),
    );
    models.current = Some(id);
    models
}

/// The built-in row as painted: one text per segment.
fn row_texts(app: &AppView) -> Vec<String> {
    let display = app.status_line.display();
    let Some(StatusLineDisplay::Segments(segments)) = display.as_deref() else {
        return Vec::new();
    };
    segments
        .iter()
        .map(|segment| segment.text().to_owned())
        .collect()
}

/// A built-in row with these items, over an agent that has sent no status and
/// knows this directory and this model: the Python agent's case.
fn builtin_app_without_agent_status(items: Vec<StatusLineItem>) -> AppView {
    let mut app = status_line_app(StatusLineType::Builtin);
    app.current_ui.status_line = command_row(StatusLineType::Builtin)
        .with_items(items)
        .into_config();
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.status_context = None;
    agent.session.cwd = std::path::PathBuf::from("/tmp/project");
    agent.session.models = model_state("model-a", "Model A");
    app
}

#[test]
fn builtin_row_is_built_from_what_the_client_knows_when_the_agent_sends_no_status() {
    let now = Instant::now();
    let mut app = builtin_app_without_agent_status(vec![
        StatusLineItem::Cwd,
        StatusLineItem::Model,
        StatusLineItem::Context,
    ]);

    app.refresh_status_line_now_at(now);

    assert_eq!(
        row_texts(&app),
        ["project", "Model A"],
        "the directory and the model come from the client, and the context item has \
         no percentage to show: the Python agent does not know its window"
    );
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "a settled row must let the loop park"
    );
}

#[test]
fn builtin_row_has_no_context_or_cost_to_show_without_a_status_from_the_agent() {
    let now = Instant::now();
    let mut app =
        builtin_app_without_agent_status(vec![StatusLineItem::Context, StatusLineItem::Cost]);

    app.refresh_status_line_now_at(now);

    assert!(
        app.status_line.display().is_none(),
        "a number the client cannot know is not drawn as zero"
    );
}

#[test]
fn the_status_the_agent_sent_wins_over_what_the_client_knows() {
    let now = Instant::now();
    let mut app =
        builtin_app_without_agent_status(vec![StatusLineItem::Cwd, StatusLineItem::Model]);
    // The agent names a directory and no model.
    app.agents.get_mut(&AgentId(0)).unwrap().status_context =
        Some(test_context("/srv/theirs"));

    app.refresh_status_line_now_at(now);

    assert_eq!(
        row_texts(&app),
        ["theirs", "Model A"],
        "its directory stands, and the model it left out is the client's"
    );
}

#[test]
fn switching_the_model_rebuilds_a_row_that_had_already_settled() {
    let mut now = Instant::now();
    let mut app = builtin_app_without_agent_status(vec![StatusLineItem::Model]);
    app.refresh_status_line_now_at(now);
    assert_eq!(row_texts(&app), ["Model A"]);
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "a settled row must park the loop, or the assertion below proves nothing"
    );

    app.agents.get_mut(&AgentId(0)).unwrap().session.models = model_state("model-b", "Model B");
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::Slow,
        "the row asks for the tick that rebuilds it"
    );

    // Throttled like any other rebuild: the new model lands on the tick after
    // the floor passes.
    app.update_status_line_at(now);
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);
    assert_eq!(row_texts(&app), ["Model B"]);
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "the rebuilt row parks again rather than ticking on the same switch"
    );
}

#[test]
fn the_end_of_a_turn_rebuilds_a_row_that_had_already_settled() {
    let mut now = Instant::now();
    let mut app = builtin_app_without_agent_status(vec![
        StatusLineItem::Model,
        StatusLineItem::TurnTimer,
    ]);
    let began = Instant::now()
        .checked_sub(Duration::from_secs(12))
        .expect("the monotonic clock is older than twelve seconds");
    app.agents.get_mut(&AgentId(0)).unwrap().turn_started_at = Some(began);
    app.refresh_status_line_now_at(now);
    assert_eq!(
        row_texts(&app),
        ["Model A", "12s"],
        "the timer shows the turn that runs"
    );

    // The turn ends. Nothing else changes, and the timer needs no tick any more,
    // so only the flag that a turn runs can ask for the rebuild.
    app.agents.get_mut(&AgentId(0)).unwrap().turn_started_at = None;
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::Slow,
        "the row asks for the tick that rebuilds it"
    );
    app.update_status_line_at(now);
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);

    assert_eq!(
        row_texts(&app),
        ["Model A"],
        "a timer must not stay on the row after its turn"
    );
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "the rebuilt row parks again"
    );
}

#[test]
fn the_end_of_a_turn_runs_a_script_once_more_with_no_turn_in_its_payload() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.status_context = None;
    agent.turn_started_at = Some(Instant::now());
    app.update_status_line_at(now);
    assert!(
        context_of_first_run(&app).turn.is_some(),
        "the last run of the turn names its start"
    );
    app.pending_effects.clear();
    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output("busy".to_string()));

    // The turn ends. A script runs on every tick of a turn and on none after
    // it, so only the flag that a turn runs can ask for this run.
    app.agents.get_mut(&AgentId(0)).unwrap().turn_started_at = None;
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);

    assert!(
        context_of_first_run(&app).turn.is_none(),
        "the script is told that the turn is over"
    );
}

/// The context a script run starts with, for an agent that has sent no status.
fn context_of_first_run(app: &AppView) -> pi_status_line::StatusLineContext {
    let [Effect::RunStatusLineCommand(run)] = app.pending_effects.as_slice() else {
        panic!("the update starts a run");
    };
    run.context().clone()
}

#[test]
fn a_script_is_given_the_facts_of_the_client_when_the_agent_sends_no_status() {
    let now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.status_context = None;
    agent.session.cwd = std::path::PathBuf::from("/tmp/project");
    agent.session.models = model_state("model-a", "Model A");

    app.update_status_line_at(now);

    let ctx = context_of_first_run(&app);
    assert_eq!(ctx.cwd, "/tmp/project");
    assert_eq!(ctx.workspace.current_dir, "/tmp/project");
    assert_eq!(ctx.session_id.as_deref(), Some("test-session"));
    assert_eq!(ctx.model.id.as_deref(), Some("model-a"));
    assert_eq!(ctx.model.display_name.as_deref(), Some("Model A"));
    assert_eq!(ctx.version, crate::client_identity::PAGER_CLIENT_VERSION);
    assert_eq!(
        ctx.schema_version,
        Some(pi_status_line::STATUS_LINE_SCHEMA_VERSION)
    );
    assert_eq!(ctx.trigger, Some(pi_status_line::StatusLineTrigger::State));
    assert!(ctx.cost.total_duration_ms < 60_000, "counted from the attach");
    assert!(ctx.turn.is_none(), "no turn is running");
    assert!(
        ctx.cost.total_cost_usd.is_none()
            && ctx.context_window.context_window_size.is_none()
            && ctx.context_window.context_tokens.is_none()
            && ctx.context_window.used_percentage.is_none(),
        "what the client cannot know is absent, not zero"
    );
}

#[test]
fn a_script_run_during_a_turn_is_told_when_the_turn_began() {
    let now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.status_context = None;
    agent.turn_started_at = Some(Instant::now());

    app.update_status_line_at(now);

    let ctx = context_of_first_run(&app);
    let began = ctx.turn.expect("a turn is running").started_at_ms;
    let wall_now = chrono::Utc::now().timestamp_millis();
    assert!(
        (wall_now - began).abs() < 5_000,
        "the turn began just now: {began} against {wall_now}"
    );
}

#[test]
fn renaming_the_session_rebuilds_a_row_that_had_already_settled() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Builtin);
    app.current_ui.status_line = command_row(StatusLineType::Builtin)
        .with_items(vec![StatusLineItem::SessionName])
        .into_config();
    app.agents.get_mut(&AgentId(0)).unwrap().display_name = Some("before".to_string());
    app.refresh_status_line_now_at(now);
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "a settled row must park the loop, or the assertion below proves nothing"
    );

    app.agents.get_mut(&AgentId(0)).unwrap().display_name = Some("after".to_string());
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::Slow,
        "the row asks for the tick that rebuilds it"
    );

    // Throttled like any other rebuild: the new name lands on the tick after
    // the floor passes.
    app.update_status_line_at(now);
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);
    let display = app.status_line.display();
    let Some(StatusLineDisplay::Segments(segments)) = display.as_deref() else {
        panic!("the rename left the row without its one segment");
    };
    assert_eq!(
        segments.iter().map(StatusSegment::text).collect::<Vec<_>>(),
        ["after"]
    );
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "the rebuilt row parks again rather than ticking on the same rename"
    );
}

#[test]
fn row_that_settled_on_nothing_gives_its_line_back() {
    let now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);

    app.update_status_line_at(now);
    assert_eq!(
        app.status_line_frame().height(),
        1,
        "the line is held until the script answers"
    );

    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output(String::new()));
    assert_eq!(
        app.status_line_frame().height(),
        0,
        "a script that printed nothing must not leave a blank line behind"
    );
}

#[test]
fn run_past_its_deadline_asks_for_the_tick_that_abandons_it() {
    let mut now = Instant::now();
    // A settled row raises no other demand, so the deadline is the only thing
    // left that can ask for the tick the watchdog runs on.
    let mut app = app_with_a_second_run(&mut now);
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::None,
        "inside its deadline a run answers through its own result, not a tick"
    );
    now += ABANDON_AFTER;
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::Slow,
        "past the deadline the tick is what runs the watchdog"
    );

    app.status_line.abandon_if_past_deadline(now);
    assert!(
        !app.status_line.command_in_flight(now),
        "the deadline releases the run so a later recompute can start one"
    );
}

#[test]
fn result_that_arrives_after_the_deadline_still_paints() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);

    app.update_status_line_at(now);
    now += ABANDON_AFTER;
    app.status_line.abandon_if_past_deadline(now);
    assert!(!app.status_line.command_in_flight(now));

    let _ = app.status_line.finish_command_run(
        now,
        RunId(0),
        RunOutcome::Output("late but useful".to_string()),
    );
    assert!(
        app.status_line.display().is_some(),
        "an abandoned run keeps its id, so a late result still paints"
    );
}

#[test]
fn settled_row_recovers_an_abandoned_run_on_its_next_refresh() {
    let mut now = Instant::now();
    let mut app = app_with_a_second_run(&mut now);
    now += ABANDON_AFTER;
    // Recovery through a refresh rather than through the tick the row now asks
    // for, since a keystroke or a resize can arrive first.
    app.pending_effects.clear();
    app.refresh_status_line_now_at(now);
    assert!(
        queued_a_run(&app),
        "the refresh abandons the outstanding run and starts a fresh one"
    );
}

#[test]
fn force_raised_inside_the_floor_is_deferred_rather_than_dropped() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);

    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "nothing ran, so nothing below holds");
    app.pending_effects.clear();

    app.refresh_status_line_now_at(now);
    assert!(
        app.pending_effects.is_empty(),
        "the floor holds a force raised this close to the last run"
    );

    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output("stale".to_string()));
    assert!(
        app.status_line.force_pending(),
        "the result started before the force and cannot answer it"
    );
    assert_eq!(
        app.status_line_tick_demand_at(now),
        TickDemand::Slow,
        "a deferred rerun must keep asking for ticks, or the row stays stale"
    );
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "the force never reran past the floor");
}

#[test]
fn second_run_cannot_start_while_one_is_outstanding() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);

    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "nothing ran, so the slot stays free");
    app.pending_effects.clear();
    now += EVENT_DEBOUNCE;
    assert!(
        app.status_line.is_due(now),
        "the debounce is what would refuse, so this proves nothing about the slot"
    );
    app.update_status_line_at(now);
    assert!(
        app.pending_effects.is_empty(),
        "a script slower than the debounce ran twice over"
    );

    let _ = app.status_line.finish_command_run(
        now,
        RunId(0),
        RunOutcome::Output("the first run".to_string()),
    );
    assert!(
        app.status_line.display().is_some(),
        "the outstanding run kept its id, so its result still paints"
    );
}

#[test]
fn gap_between_runs_is_measured_from_the_end_of_the_last_one() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);

    app.update_status_line_at(now);
    app.pending_effects.clear();
    now += EVENT_DEBOUNCE * 3;
    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output("row".to_string()));

    app.update_status_line_at(now);
    assert!(
        app.pending_effects.is_empty(),
        "a script slower than the debounce re-ran with no gap at all"
    );
    now += EVENT_DEBOUNCE;
    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "the debounce passed and nothing reran");
}

#[test]
fn minimal_mode_runs_the_row_like_fullscreen() {
    let now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);
    app.screen_mode = crate::app::ScreenMode::Minimal;
    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "the script runs in minimal mode too");

    let mut app = status_line_app(StatusLineType::Builtin);
    app.current_ui.status_line = command_row(StatusLineType::Builtin)
        .with_items(vec![StatusLineItem::Cwd])
        .into_config();
    app.screen_mode = crate::app::ScreenMode::Minimal;
    app.update_status_line_at(now);
    assert!(
        matches!(app.status_line_frame(), StatusLineFrame::On { .. }),
        "a builtin row fills for the minimal live region to paint"
    );
}

#[test]
fn resize_arms_the_next_run_only_when_there_is_a_row() {
    let mut app = status_line_app(StatusLineType::Command);
    app.queue_status_line_resize();
    assert!(
        app.status_line.force_pending(),
        "the row has to re-run at the new width"
    );

    let mut off = status_line_app(StatusLineType::Disabled);
    off.queue_status_line_resize();
    assert!(!off.status_line.force_pending(), "no row, no resize");
}

#[test]
fn row_belonging_to_another_agent_is_not_painted_under_this_one() {
    let now = Instant::now();
    let mut app = status_line_app(StatusLineType::Builtin);
    app.current_ui.status_line = command_row(StatusLineType::Builtin)
        .with_items(vec![StatusLineItem::Cwd])
        .into_config();
    app.update_status_line_at(now);
    assert!(
        matches!(app.status_line_frame(), StatusLineFrame::On { .. }),
        "the row never filled"
    );

    app.active_view = ActiveView::Agent(AgentId(7));
    assert!(
        matches!(app.status_line_frame(), StatusLineFrame::Reserved { .. }),
        "another agent's row must not paint here, and the line stays held so \
         nothing jumps while this agent's row is built"
    );
}

#[test]
fn cycling_agents_cannot_re_run_a_script_faster_than_the_floor() {
    let mut now = Instant::now();
    let mut app = status_line_app(StatusLineType::Command);
    // A second agent the row can legitimately describe, so the switch reaches
    // the throttle rather than stopping at "no session to report on".
    let second = AgentId(1);
    let session = make_test_agent_session(&app, second, "second-session");
    let mut agent = AgentView::new(session, ScrollbackState::new());
    agent.status_context = Some(test_context("/tmp/second"));
    app.agents.insert(second, agent);

    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "nothing ran, so the switch is moot");
    app.pending_effects.clear();
    app.on_status_line_command_finished_at(now, RunId(0), RunOutcome::Output("row".to_string()));

    app.active_view = ActiveView::Agent(second);
    app.update_status_line_at(now);
    assert!(app.pending_effects.is_empty(), "re-ran inside the floor");
    assert!(app.status_line.force_pending(), "the switch was dropped");
    now += MIN_REFRESH_INTERVAL_MS;
    app.update_status_line_at(now);
    assert!(queued_a_run(&app), "the deferred run never happened");
}

#[test]
fn row_nobody_asked_for_arms_nothing_that_outlives_the_turn() {
    let now = Instant::now();
    let mut app = test_app_with_agent();
    app.current_ui.status_line = StatusLineConfig::default();

    app.refresh_status_line_for(AgentId(0));

    assert!(
        !app.status_line.force_pending(),
        "a disabled row that latches a force wakes the event loop for good"
    );
    assert_eq!(app.status_line_tick_demand_at(now), TickDemand::None);
}

#[test]
fn only_a_row_that_can_change_mid_turn_holds_the_loop_awake_for_one() {
    let mut now = Instant::now();
    for (kind, demand) in [
        (StatusLineType::Command, TickDemand::Slow),
        (StatusLineType::Builtin, TickDemand::None),
    ] {
        let mut app = status_line_app(kind);
        app.current_ui.status_line = command_row(kind)
            .with_items(vec![StatusLineItem::Cwd])
            .into_config();
        app.update_status_line_at(now);
        // Settle the run a `command` row just started, so the only thing left
        // that could ask for a tick is the turn.
        app.on_status_line_command_finished_at(
            now,
            RunId(0),
            RunOutcome::Output("row".to_string()),
        );
        app.agents.get_mut(&AgentId(0)).unwrap().turn_started_at = Some(std::time::Instant::now());

        // The row is built from whether a turn runs, so the start of a turn makes it
        // stale once. Let that one rebuild happen, and settle the run it starts, so
        // that what is left to ask for a tick is the turn itself and nothing else.
        now += MIN_REFRESH_INTERVAL_MS;
        app.update_status_line_at(now);
        if matches!(kind, StatusLineType::Command) {
            app.on_status_line_command_finished_at(
                now,
                RunId(1),
                RunOutcome::Output("row".to_string()),
            );
        }

        assert_eq!(app.status_line_tick_demand_at(now), demand, "{kind:?}");
    }
}
