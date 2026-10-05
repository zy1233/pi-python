//! Re-exports of the crate-internal event types that live in
//! `pi-session-events`. The orphan-rule items (`From<&permission::Decision>`
//! and the doom-loop categorizer) stay here since they need shell-local
//! types.

pub(crate) use pi_session_events::types::GoalClassifierVerdictTelemetry;

// ── Laziness detector (Layer 3) discriminator vocabulary ─────────────
//
// Single source of truth for the `category` field on
// `Event::LazinessClassifierFired` / `LazinessNudgeFired` and the
// `reason` field on `Event::LazinessClassifierAborted`. The producer
// wraps the category strings in `LazinessCategory::as_const_str()`
// (acp_session.rs) for compile-time closure over the set, and the
// abort reasons are emitted only via the `LAZINESS_ABORT_*` consts
// below — no string literals at any producer site.
//
// The category strings are also the lowercase serde representation of
// the classifier's JSON output, so producer (classifier prompt) and
// consumer (Rust enum + telemetry) share one vocabulary.

/// Stalled — the model emitted prose narration claiming progress
/// without any real tool calls.
pub(crate) const LAZINESS_STALLED_NARRATION: &str = "stalled_narration";

/// Stalled — the model asked the user for permission to continue a
/// task that is already in flight.
pub(crate) const LAZINESS_STALLED_PERMISSION_ASKING: &str = "stalled_permission_asking";

/// Stalled — the model has no todo list but a multi-step task is
/// clearly in flight (no active plan tool calls despite a complex
/// pending task).
pub(crate) const LAZINESS_STALLED_NO_TODOS_BUT_TASK_IN_FLIGHT: &str =
    "stalled_no_todos_but_task_in_flight";

/// Stalled — the agent declared completion/success but the transcript
/// shows substantive claims unbacked by tool-call evidence (e.g. claims
/// running `make test` but no `make` tool_call appears; claims
/// "overnight 8+ hour run" but elapsed time is minutes; claims N review
/// rounds but only M happened).
pub(crate) const LAZINESS_STALLED_FALSE_COMPLETION: &str = "stalled_false_completion";

/// Not stalled — the model has genuinely completed its task.
pub(crate) const LAZINESS_NOT_STALLED_COMPLETE: &str = "not_stalled_complete";

/// Not stalled — the model is correctly waiting on a backgrounded task
/// it cannot drive forward.
pub(crate) const LAZINESS_NOT_STALLED_WAITING_BG: &str = "not_stalled_waiting_on_background";

/// Not stalled — the model is correctly waiting on user input for a
/// genuine ambiguity.
pub(crate) const LAZINESS_NOT_STALLED_WAITING_USER: &str = "not_stalled_waiting_on_user";

/// Aborted because a fresh user prompt arrived before classification
/// completed.
pub(crate) const LAZINESS_ABORT_USER_INPUT: &str = "user_input";

/// Aborted because the user switched models mid-classification.
pub(crate) const LAZINESS_ABORT_MODEL_SWITCH: &str = "model_switch";

/// Aborted because the classifier exceeded its wall-clock budget.
pub(crate) const LAZINESS_ABORT_TIMEOUT: &str = "timeout";

/// Aborted because the classifier response failed to parse after the
/// tolerant parser exhausted all three passes.
pub(crate) const LAZINESS_ABORT_CLASSIFIER_ERROR: &str = "classifier_error";

// Compile-time guard against an accidentally-empty const breaking the
// dashboards' group-by. `const _: () = assert!(…)` fires at build
// time, not at first test run.
#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !LAZINESS_STALLED_NARRATION.is_empty()
        && !LAZINESS_STALLED_PERMISSION_ASKING.is_empty()
        && !LAZINESS_STALLED_NO_TODOS_BUT_TASK_IN_FLIGHT.is_empty()
        && !LAZINESS_STALLED_FALSE_COMPLETION.is_empty()
        && !LAZINESS_NOT_STALLED_COMPLETE.is_empty()
        && !LAZINESS_NOT_STALLED_WAITING_BG.is_empty()
        && !LAZINESS_NOT_STALLED_WAITING_USER.is_empty()
        && !LAZINESS_ABORT_USER_INPUT.is_empty()
        && !LAZINESS_ABORT_MODEL_SWITCH.is_empty()
        && !LAZINESS_ABORT_TIMEOUT.is_empty()
        && !LAZINESS_ABORT_CLASSIFIER_ERROR.is_empty(),
    "Laziness discriminator consts must be non-empty",
);

/// Closed set of categories the Layer-3 classifier can return.
///
/// Mirrors the JSON schema in the classifier prompt; `serde` uses the
/// `LAZINESS_*` strings above as the wire format (snake_case). The
/// `as_const_str()` mapping is exhaustive over the variants — the
/// `laziness_category_round_trip` test asserts the variant ↔ const
/// pairing is one-to-one. Mirrors the `TodoGateReason` pattern.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum LazinessCategory {
    StalledNarration,
    StalledPermissionAsking,
    StalledNoTodosButTaskInFlight,
    StalledFalseCompletion,
    NotStalledComplete,
    NotStalledWaitingOnBackground,
    NotStalledWaitingOnUser,
}

impl LazinessCategory {
    /// Every variant of this enum. Used by the producer-consistency
    /// tests to enumerate the closed set rather than a hand-coded
    /// array that would silently drift if a new variant were added.
    /// `as_const_str` and `is_stalled` are compiler-enforced
    /// exhaustive matches; the `_assert_exhaustive` helper below is
    /// the cheap drift guard that forces this list to stay in sync
    /// (adding any variant without listing it here is a compile error).
    #[cfg_attr(
        not(test),
        expect(
            dead_code,
            reason = "exhaustiveness guard for tests; remove expect if used in prod"
        )
    )]
    pub(crate) const fn all() -> &'static [Self] {
        // The exhaustive match in this helper wires the array length
        // to the variant count: adding a variant breaks `match` AND
        // the array's expected count.
        const fn _assert_exhaustive(c: LazinessCategory) {
            match c {
                LazinessCategory::StalledNarration => (),
                LazinessCategory::StalledPermissionAsking => (),
                LazinessCategory::StalledNoTodosButTaskInFlight => (),
                LazinessCategory::StalledFalseCompletion => (),
                LazinessCategory::NotStalledComplete => (),
                LazinessCategory::NotStalledWaitingOnBackground => (),
                LazinessCategory::NotStalledWaitingOnUser => (),
            }
        }
        &[
            Self::StalledNarration,
            Self::StalledPermissionAsking,
            Self::StalledNoTodosButTaskInFlight,
            Self::StalledFalseCompletion,
            Self::NotStalledComplete,
            Self::NotStalledWaitingOnBackground,
            Self::NotStalledWaitingOnUser,
        ]
    }
}

// ── TodoGate discriminator vocabulary ─────────────────────────────────
//
// Source of truth for the `reason` field on `Event::TodoGateFired`.
// Producer wraps these via `TodoGateReason::as_str()` (acp_session.rs).

/// The TodoGate fired because a content-only turn ended with one or more
/// pending or unbacked in-progress todos.
pub(crate) const TODO_GATE_IN_FLIGHT: &str = "in_flight";

// Compile-time non-empty check — empty would silently break dashboards' group-by.
#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !TODO_GATE_IN_FLIGHT.is_empty(),
    "TodoGate discriminator consts must be non-empty",
);

// ── GoalClassifier discriminator vocabulary ───────────────────────────
//
// Single source of truth for the `reason` field on
// `Event::GoalClassifierFailOpen` / `Event::GoalClassifierFailClosed`.
// The producer wraps the reason strings via the `as_const_str()`
// methods on `GoalClassifierFailOpenReason` / `GoalClassifierFailClosedReason`
// so the variant set is compiler-enforced; the consts here are the wire
// vocabulary the dashboards group by.

/// Fail-open — legacy wire string; runner does not emit.
pub(crate) const GOAL_CLASSIFIER_FAIL_OPEN_TIMEOUT: &str = "timeout";

/// Fail-open — the subagent spawn returned an error (channel closed,
/// coordinator rejected, etc.). INFRA-class.
pub(crate) const GOAL_CLASSIFIER_FAIL_OPEN_SAMPLER_ERROR: &str = "sampler_error";

/// Fail-open — the user aborted the run mid-classification.
pub(crate) const GOAL_CLASSIFIER_FAIL_OPEN_ABORTED: &str = "aborted";

/// Fail-open — the harness could not pre-create the details-file parent
/// directory or otherwise prepare the on-disk state.
pub(crate) const GOAL_CLASSIFIER_FAIL_OPEN_FILE_WRITE_FAILED: &str = "file_write_failed";

/// Fail-open — the goal was no longer Active when the runner resolved
/// its inputs (status changed during the spawn).
pub(crate) const GOAL_CLASSIFIER_FAIL_OPEN_GOAL_NOT_ACTIVE: &str = "goal_not_active_at_resolve";

/// Fail-closed — a second `update_goal(completed: true)` arrived
/// while a verification stage was already in flight for this goal.
/// Caller must not double-spawn.
pub(crate) const GOAL_CLASSIFIER_FAIL_CLOSED_CONCURRENT: &str = "concurrent_in_flight";

/// Fail-closed — the deferred-completion queue overflowed and the
/// oldest entry was dropped to make room for a newer one. PARSE-class
/// analogue of `ConcurrentInFlight` for the runaway-`completed: true`
/// path.
pub(crate) const GOAL_CLASSIFIER_FAIL_CLOSED_PENDING_QUEUE_FULL: &str = "pending_queue_full";

// Compile-time non-empty guard (mirrors the laziness const block).
#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !GOAL_CLASSIFIER_FAIL_OPEN_TIMEOUT.is_empty()
        && !GOAL_CLASSIFIER_FAIL_OPEN_SAMPLER_ERROR.is_empty()
        && !GOAL_CLASSIFIER_FAIL_OPEN_ABORTED.is_empty()
        && !GOAL_CLASSIFIER_FAIL_OPEN_FILE_WRITE_FAILED.is_empty()
        && !GOAL_CLASSIFIER_FAIL_OPEN_GOAL_NOT_ACTIVE.is_empty()
        && !GOAL_CLASSIFIER_FAIL_CLOSED_CONCURRENT.is_empty()
        && !GOAL_CLASSIFIER_FAIL_CLOSED_PENDING_QUEUE_FULL.is_empty(),
    "GoalClassifier discriminator consts must be non-empty",
);

// ── GoalPlanner discriminator vocabulary ──────────────────────────────
//
// The planner is fail-CLOSED by design (the opposite of the classifier).
// Every reason here represents a path that pauses the goal — there is no
// fail-open analogue.

/// Planner subagent coordinator channel was unreachable.
pub(crate) const GOAL_PLANNER_FAIL_CLOSED_TRANSPORT: &str = "transport";

/// Planner subagent reported a runtime failure.
pub(crate) const GOAL_PLANNER_FAIL_CLOSED_RUNTIME: &str = "runtime";

/// User aborted the planner mid-run.
pub(crate) const GOAL_PLANNER_FAIL_CLOSED_ABORTED: &str = "aborted";

/// Planner did not write `plan.md` (file missing or empty).
pub(crate) const GOAL_PLANNER_FAIL_CLOSED_MISSING_PLAN: &str = "missing_plan_file";

/// Harness could not pre-create the plan-file parent directory.
pub(crate) const GOAL_PLANNER_FAIL_CLOSED_FILE_WRITE_FAILED: &str = "file_write_failed";

#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !GOAL_PLANNER_FAIL_CLOSED_TRANSPORT.is_empty()
        && !GOAL_PLANNER_FAIL_CLOSED_RUNTIME.is_empty()
        && !GOAL_PLANNER_FAIL_CLOSED_ABORTED.is_empty()
        && !GOAL_PLANNER_FAIL_CLOSED_MISSING_PLAN.is_empty()
        && !GOAL_PLANNER_FAIL_CLOSED_FILE_WRITE_FAILED.is_empty(),
    "GoalPlanner discriminator consts must be non-empty",
);

// ── GoalStrategist discriminator vocabulary ───────────────────────────
//
// The strategist is fail-OPEN by design (the opposite of the planner).
// Every reason here represents a path that is logged and then ignored —
// the goal keeps running. The strategist is a best-effort advisory
// enhancement, never a gate.

/// Strategist subagent coordinator channel was unreachable.
pub(crate) const GOAL_STRATEGIST_FAILED_TRANSPORT: &str = "transport";

/// Strategist subagent reported a runtime failure.
pub(crate) const GOAL_STRATEGIST_FAILED_RUNTIME: &str = "runtime";

/// User aborted the strategist mid-run.
pub(crate) const GOAL_STRATEGIST_FAILED_ABORTED: &str = "aborted";

/// Strategist did not write the strategy note (file missing or empty).
pub(crate) const GOAL_STRATEGIST_FAILED_MISSING_STRATEGY: &str = "missing_strategy_file";

#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !GOAL_STRATEGIST_FAILED_TRANSPORT.is_empty()
        && !GOAL_STRATEGIST_FAILED_RUNTIME.is_empty()
        && !GOAL_STRATEGIST_FAILED_ABORTED.is_empty()
        && !GOAL_STRATEGIST_FAILED_MISSING_STRATEGY.is_empty(),
    "GoalStrategist discriminator consts must be non-empty",
);

/// The plan.md restore-guard could not put the contract back. A
/// write to plan.md failed.
pub(crate) const GOAL_STRATEGIST_RESTORE_WRITE_FAILED: &str = "write_failed";

/// Removing a strategist-created plan.md failed.
pub(crate) const GOAL_STRATEGIST_RESTORE_REMOVE_FAILED: &str = "remove_failed";

/// plan.md is (or became) a symlink — refused to restore through it
/// (treated as tampering).
pub(crate) const GOAL_STRATEGIST_RESTORE_SYMLINK_TAMPER: &str = "symlink_tamper";

#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !GOAL_STRATEGIST_RESTORE_WRITE_FAILED.is_empty()
        && !GOAL_STRATEGIST_RESTORE_REMOVE_FAILED.is_empty()
        && !GOAL_STRATEGIST_RESTORE_SYMLINK_TAMPER.is_empty(),
    "GoalStrategist restore discriminator consts must be non-empty",
);

// ── GoalSummarizer discriminator vocabulary ───────────────────────────
//
// The summarizer is fail-OPEN by design: it runs ONCE after the goal is
// already verified-achieved, so every reason here is logged and ignored —
// goal completion is never blocked, paused, or un-achieved.

/// Summarizer subagent coordinator channel was unreachable.
pub(crate) const GOAL_SUMMARIZER_FAIL_OPEN_TRANSPORT: &str = "transport";

/// Summarizer subagent reported a runtime failure.
pub(crate) const GOAL_SUMMARIZER_FAIL_OPEN_RUNTIME: &str = "runtime";

/// User aborted the summarizer mid-run.
pub(crate) const GOAL_SUMMARIZER_FAIL_OPEN_ABORTED: &str = "aborted";

/// Summarizer returned an empty (whitespace-only) summary — nothing to
/// surface, so the closing message is skipped.
pub(crate) const GOAL_SUMMARIZER_FAIL_OPEN_EMPTY_SUMMARY: &str = "empty_summary";

#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !GOAL_SUMMARIZER_FAIL_OPEN_TRANSPORT.is_empty()
        && !GOAL_SUMMARIZER_FAIL_OPEN_RUNTIME.is_empty()
        && !GOAL_SUMMARIZER_FAIL_OPEN_ABORTED.is_empty()
        && !GOAL_SUMMARIZER_FAIL_OPEN_EMPTY_SUMMARY.is_empty(),
    "GoalSummarizer discriminator consts must be non-empty",
);

// ── GoalRoleModel discriminator vocabulary ────────────────────────────
//
// Source of truth for the `reason` field on `Event::GoalRoleModelFailOpen`.
// Per-role model selection is fail-OPEN by design: a bad/unauthorized
// model, an unusable toolset, or a harness whose flavor the subagent system
// can't represent degrades that role (or skeptic index) to the current model +
// session harness — the goal is never paused.
// The producer (the goal spawn wiring / `resolve_goal_role_override` +
// `spawn_with_fail_open_retry`) wraps the reason strings via
// `GoalRoleModelFailOpenReason::as_const_str`; the consts here are the
// wire vocabulary dashboards group by. The four `describe_subagent_type`
// outcomes (Unknown / NotAllowed / Disabled / Unavailable) get
// distinct wire strings because they distinguish config-bug from
// infra-flakiness on the dashboard.

/// Fail-open — the configured model id is not in the session's model
/// catalog (`find_model_by_id` miss).
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_MODEL_UNKNOWN: &str = "model_unknown";

/// Fail-open — the model is in the catalog but the session's
/// `allowed_models` does not permit it (`ModelInfo.user_selectable == false`).
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_MODEL_UNAUTHORIZED: &str = "model_unauthorized";

/// Fail-open — the configured `agent_type` did not resolve
/// (`describe_subagent_type` ⇒ `Unknown`): either a subagent type, or a `/goal`
/// harness name, that does not exist.
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_UNKNOWN: &str = "toolset_unknown";

/// Fail-open — the `agent_type` exists but is not on the parent's
/// allow-list (`describe_subagent_type` ⇒ `NotAllowed`).
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_NOT_ALLOWED: &str = "toolset_not_allowed";

/// Fail-open — the `agent_type` is allow-listed but disabled
/// (`describe_subagent_type` ⇒ `Disabled`).
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_DISABLED: &str = "toolset_disabled";

/// Fail-open — the coordinator could not describe the toolset (channel
/// closed / responder dropped / timeout; `describe_subagent_type` ⇒
/// `Unavailable`). Infra-flakiness, distinct from the config-bug cases.
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_UNAVAILABLE: &str = "toolset_unavailable";

/// Fail-open — the toolset built but lacks the capabilities the role
/// requires (e.g. a verifier whose toolset cannot read/grep code).
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_INCAPABLE: &str = "toolset_incapable";

/// Fail-open — spawning the role with the configured pair returned a
/// `SpawnError`; the spawn-and-retry-once wrapper retried on the current
/// model.
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_SPAWN_FAILED: &str = "spawn_failed";

/// Fail-open — the configured `agent_type` resolves as a STRICT harness whose
/// subagent flavor `resolve_subagent_toolset` can't represent (e.g. `codex`):
/// committing it would silently run grok-build flavor. Distinct from
/// `toolset_unknown` (a name that doesn't resolve at all).
pub(crate) const GOAL_ROLE_MODEL_FAIL_OPEN_HARNESS_FLAVOR_UNSUPPORTED: &str =
    "harness_flavor_unsupported";

#[allow(clippy::const_is_empty)]
const _: () = assert!(
    !GOAL_ROLE_MODEL_FAIL_OPEN_MODEL_UNKNOWN.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_MODEL_UNAUTHORIZED.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_UNKNOWN.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_NOT_ALLOWED.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_DISABLED.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_UNAVAILABLE.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_INCAPABLE.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_SPAWN_FAILED.is_empty()
        && !GOAL_ROLE_MODEL_FAIL_OPEN_HARNESS_FLAVOR_UNSUPPORTED.is_empty(),
    "GoalRoleModel discriminator consts must be non-empty",
);

/// Closed set of per-role model-selection fail-open reasons (the `reason`
/// wire strings above). Constructed by the goal spawn wiring; the wire
/// vocabulary is the source of truth dashboards group by.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GoalRoleModelFailOpenReason {
    ModelUnknown,
    ModelUnauthorized,
    ToolsetUnknown,
    ToolsetNotAllowed,
    ToolsetDisabled,
    ToolsetUnavailable,
    ToolsetIncapable,
    SpawnFailed,
    HarnessFlavorUnsupported,
}

impl GoalRoleModelFailOpenReason {
    /// Every variant of this enum, enumerated alongside a compiler-
    /// enforced exhaustive `match` so adding a variant is a compile error
    /// until it is listed here (and given an `as_const_str` arm). Mirrors
    /// [`LazinessCategory::all`]; drives the exhaustiveness test.
    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "exhaustiveness guard, exercised only by tests")
    )]
    pub(crate) const fn all() -> &'static [Self] {
        const fn _assert_exhaustive(r: GoalRoleModelFailOpenReason) {
            match r {
                GoalRoleModelFailOpenReason::ModelUnknown => (),
                GoalRoleModelFailOpenReason::ModelUnauthorized => (),
                GoalRoleModelFailOpenReason::ToolsetUnknown => (),
                GoalRoleModelFailOpenReason::ToolsetNotAllowed => (),
                GoalRoleModelFailOpenReason::ToolsetDisabled => (),
                GoalRoleModelFailOpenReason::ToolsetUnavailable => (),
                GoalRoleModelFailOpenReason::ToolsetIncapable => (),
                GoalRoleModelFailOpenReason::SpawnFailed => (),
                GoalRoleModelFailOpenReason::HarnessFlavorUnsupported => (),
            }
        }
        &[
            Self::ModelUnknown,
            Self::ModelUnauthorized,
            Self::ToolsetUnknown,
            Self::ToolsetNotAllowed,
            Self::ToolsetDisabled,
            Self::ToolsetUnavailable,
            Self::ToolsetIncapable,
            Self::SpawnFailed,
            Self::HarnessFlavorUnsupported,
        ]
    }
}

/// Bridge `GoalClassifierVerdict` (shell) → `GoalClassifierVerdictTelemetry`.
/// The exhaustive match catches drift if either side adds a variant.
impl From<crate::session::goal_tracker::GoalClassifierVerdict> for GoalClassifierVerdictTelemetry {
    fn from(verdict: crate::session::goal_tracker::GoalClassifierVerdict) -> Self {
        use crate::session::goal_tracker::GoalClassifierVerdict;
        match verdict {
            GoalClassifierVerdict::Achieved => Self::Achieved,
            GoalClassifierVerdict::NotAchieved => Self::NotAchieved,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Hand-curated expected pairings — kept here (not in `all()`) so
    /// the test can detect a desync between `as_const_str` and the
    /// `pub const` set. If a variant is added to the enum, `all()`
    /// grows AND `as_const_str` grows (both compiler-enforced); this
    /// table must also be extended, which the `assert_eq!(len)` below
    /// catches.
    const EXPECTED_CATEGORY_CONSTS: &[(LazinessCategory, &str)] = &[
        (
            LazinessCategory::StalledNarration,
            LAZINESS_STALLED_NARRATION,
        ),
        (
            LazinessCategory::StalledPermissionAsking,
            LAZINESS_STALLED_PERMISSION_ASKING,
        ),
        (
            LazinessCategory::StalledNoTodosButTaskInFlight,
            LAZINESS_STALLED_NO_TODOS_BUT_TASK_IN_FLIGHT,
        ),
        (
            LazinessCategory::StalledFalseCompletion,
            LAZINESS_STALLED_FALSE_COMPLETION,
        ),
        (
            LazinessCategory::NotStalledComplete,
            LAZINESS_NOT_STALLED_COMPLETE,
        ),
        (
            LazinessCategory::NotStalledWaitingOnBackground,
            LAZINESS_NOT_STALLED_WAITING_BG,
        ),
        (
            LazinessCategory::NotStalledWaitingOnUser,
            LAZINESS_NOT_STALLED_WAITING_USER,
        ),
    ];

    #[test]
    fn laziness_category_all_covers_every_variant() {
        // Closed-set guard: if a 7th variant were added,
        // `LazinessCategory::all()`'s array would grow (and the
        // exhaustive `_assert_exhaustive` match in the impl would
        // force the array to grow too). The test then forces the
        // expected-table to grow via the length equality, and the
        // per-variant assertions below verify nothing was missed.
        let all = LazinessCategory::all();
        assert_eq!(
            all.len(),
            EXPECTED_CATEGORY_CONSTS.len(),
            "LazinessCategory::all() and EXPECTED_CATEGORY_CONSTS drifted",
        );
        let all_set: std::collections::BTreeSet<&LazinessCategory> = all.iter().collect();
        let expected_set: std::collections::BTreeSet<&LazinessCategory> =
            EXPECTED_CATEGORY_CONSTS.iter().map(|(c, _)| c).collect();
        assert_eq!(
            all_set, expected_set,
            "every variant in `all()` must also appear in EXPECTED_CATEGORY_CONSTS",
        );
    }

    /// Hand-curated `(variant, literal_str, const)` triples. `literal_str`
    /// is a HARDCODED string independent of the `pub const`, so a typo in
    /// a const *value* (e.g. `"model_unknown"` → `"model_unkown"`) is
    /// caught — the test asserts `as_const_str() == literal_str` AND
    /// `as_const_str() == const`. Kept separate from `all()` (which is
    /// compiler-enforced exhaustive via its `_assert_exhaustive` match) so
    /// a new variant forces this table to grow too — caught by the length
    /// equality below.
    const EXPECTED_FAIL_OPEN_CONSTS: &[(GoalRoleModelFailOpenReason, &str, &str)] = &[
        (
            GoalRoleModelFailOpenReason::ModelUnknown,
            "model_unknown",
            GOAL_ROLE_MODEL_FAIL_OPEN_MODEL_UNKNOWN,
        ),
        (
            GoalRoleModelFailOpenReason::ModelUnauthorized,
            "model_unauthorized",
            GOAL_ROLE_MODEL_FAIL_OPEN_MODEL_UNAUTHORIZED,
        ),
        (
            GoalRoleModelFailOpenReason::ToolsetUnknown,
            "toolset_unknown",
            GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_UNKNOWN,
        ),
        (
            GoalRoleModelFailOpenReason::ToolsetNotAllowed,
            "toolset_not_allowed",
            GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_NOT_ALLOWED,
        ),
        (
            GoalRoleModelFailOpenReason::ToolsetDisabled,
            "toolset_disabled",
            GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_DISABLED,
        ),
        (
            GoalRoleModelFailOpenReason::ToolsetUnavailable,
            "toolset_unavailable",
            GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_UNAVAILABLE,
        ),
        (
            GoalRoleModelFailOpenReason::ToolsetIncapable,
            "toolset_incapable",
            GOAL_ROLE_MODEL_FAIL_OPEN_TOOLSET_INCAPABLE,
        ),
        (
            GoalRoleModelFailOpenReason::SpawnFailed,
            "spawn_failed",
            GOAL_ROLE_MODEL_FAIL_OPEN_SPAWN_FAILED,
        ),
        (
            GoalRoleModelFailOpenReason::HarnessFlavorUnsupported,
            "harness_flavor_unsupported",
            GOAL_ROLE_MODEL_FAIL_OPEN_HARNESS_FLAVOR_UNSUPPORTED,
        ),
    ];

    #[test]
    fn goal_role_model_fail_open_reason_all_covers_every_variant() {
        // Closed-set guard: `all()` grows (its `_assert_exhaustive` match
        // is compiler-enforced) when a variant is added, forcing the
        // expected-table to grow via this length equality.
        let all = GoalRoleModelFailOpenReason::all();
        assert_eq!(
            all.len(),
            EXPECTED_FAIL_OPEN_CONSTS.len(),
            "GoalRoleModelFailOpenReason::all() and EXPECTED_FAIL_OPEN_CONSTS drifted",
        );
    }
}
