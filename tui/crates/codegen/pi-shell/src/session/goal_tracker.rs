//! Goal mode state machine.
//!
//! This module contains [`GoalTracker`], a pure state machine (no async I/O)
//! modeled after [`PlanModeTracker`](super::plan_mode::PlanModeTracker).
//! The `SessionActor` owns one `GoalTracker` behind a `Mutex` and calls
//! its methods at the appropriate orchestration points.
//!
//! Persisted `"infra_paused"` requires this shell version (one-way upgrade).
//! Unknown wire values (including unknown `*_paused` forms) deserialize to
//! [`GoalStatus::UserPaused`] so a corrupt or forward-version snapshot can
//! never resurrect as a self-driving goal.

use std::path::PathBuf;

// Phase / Status enums

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum GoalPhase {
    Idle,
    Planning,
    Executing,
}

/// Lifecycle status of a goal. The paused variants encode the
/// reason the goal was paused — `UserPaused` for Ctrl+C / `/goal pause`,
/// `BackOffPaused` when the classifier run cap is hit, `NoProgressPaused`
/// when the verifier flags the same gaps with no progress before the cap,
/// `InfraPaused` when a turn finishes with an infrastructure error,
/// and `Blocked` when the model determined the goal is not achievable
/// in the current environment. Use [`GoalStatus::is_paused`] to test
/// paused-ness uniformly across all six variants.
///
/// **Backwards-compat serde aliases:** older shells serialized this
/// enum with the default PascalCase form (`"Active"`, `"Paused"`,
/// `"BudgetLimited"`, `"Complete"`). The `#[serde(alias = ...)]`
/// attributes preserve in-flight goal snapshots written by older shells
/// — legacy `"Paused"` maps to `UserPaused` (matches the pager-side
/// fallback). New
/// snapshots emit snake_case per `rename_all`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalStatus {
    #[serde(alias = "Active")]
    Active,
    #[serde(alias = "Paused")]
    UserPaused,
    BackOffPaused,
    /// Verifier flagged the same gaps across consecutive attempts (no
    /// progress) and auto-paused before the run cap. Resumable, same paused
    /// family as `BackOffPaused`; split out so the UI distinguishes a stall
    /// from a cap pause.
    NoProgressPaused,
    /// Infrastructure turn failure (`PromptTurnResult::Err`). The
    /// human-readable reason is stashed in [`GoalOrchestration::pause_message`].
    InfraPaused,
    /// stashed in [`GoalOrchestration::pause_message`].
    Blocked,
    #[serde(alias = "BudgetLimited")]
    BudgetLimited,
    #[serde(alias = "Complete")]
    Complete,
}

impl<'de> serde::Deserialize<'de> for GoalStatus {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let s = String::deserialize(deserializer)?;
        Ok(Self::from_wire_str(&s))
    }
}

impl GoalStatus {
    /// Parse a persisted/wire status string. Unknown values map to
    /// `UserPaused`: a status this shell cannot interpret must restore as
    /// a resumable paused goal, never an Active self-driving one.
    pub(crate) fn from_wire_str(s: &str) -> Self {
        match s {
            "active" | "Active" => Self::Active,
            "user_paused" | "paused" | "Paused" => Self::UserPaused,
            // Historical status from shells that had doom-loop auto-pause.
            "doom_loop_paused" => Self::UserPaused,
            "back_off_paused" => Self::BackOffPaused,
            "no_progress_paused" => Self::NoProgressPaused,
            "infra_paused" => Self::InfraPaused,
            "blocked" => Self::Blocked,
            "budget_limited" | "BudgetLimited" => Self::BudgetLimited,
            "complete" | "Complete" => Self::Complete,
            _ => Self::UserPaused,
        }
    }
}

/// Aggregate verdict produced by the goal-verification stage.
/// `Achieved` indicates the adversarial skeptic panel judged the goal
/// complete; `NotAchieved` means another worker round is warranted.
/// Serialized in snake_case to match `GoalStatus` / `GoalPhase`. The
/// enum name retains the `Classifier` prefix for wire stability across
/// the verification-stage rewire.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalClassifierVerdict {
    Achieved,
    NotAchieved,
}

// History

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalEvent {
    GoalCreated,
    PlanningStarted,
    PlanningCompleted,
    PlanningFailed,
    WorkerStarted,
    WorkerCompleted,
    WorkerFailed,
    ContextRotated,
    GoalPaused,
    GoalResumed,
    GoalCompleted,
    GoalCleared,
    BudgetExceeded,
    /// The model tried to stop early (a "giving up"-style bail) while the
    /// goal still had open work and the harness re-nudged it. `detail`
    /// carries the matched stop-pattern label.
    PrematureStopDetected,
    /// Forward-compat sink: a history event written by a newer shell that
    /// this binary doesn't know. Lets an older binary deserialize a newer
    /// snapshot's history instead of failing the whole field.
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GoalHistoryEntry {
    pub timestamp: String,
    pub event: GoalEvent,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub round: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tokens_used: Option<i64>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub unmet: Vec<String>,
}

// GoalOrchestration (full persisted state)

/// Generate a short opaque identifier used to scope the per-goal
/// scratch root (`<temp_dir>/grok-goal-<id>`) and the verifier
/// verdict/details files inside it.
///
/// The id is a 12-char prefix of a UUIDv4 simple form — ~48 bits of
/// entropy, enough to avoid collision between concurrent goals on the
/// same machine while staying short enough that the orchestrator model
/// can copy it verbatim into spawned verifier prompts without
/// truncation or typo risk (see the past-issue memory note on
/// "UUID-in-prompt copy-fidelity failure mode").
pub(crate) fn generate_verifier_id() -> String {
    let mut s = uuid::Uuid::new_v4().simple().to_string();
    s.truncate(12);
    s
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GoalOrchestration {
    pub goal_id: String,
    pub objective: String,
    pub status: GoalStatus,
    pub phase: GoalPhase,
    pub token_budget: Option<i64>,
    pub elapsed_ms: u64,
    pub created_at: String,
    pub current_subagent_id: Option<String>,
    pub current_subagent_role: Option<String>,
    #[serde(default)]
    pub total_worker_rounds: u32,
    #[serde(default)]
    pub total_verify_rounds: u32,
    #[serde(skip)]
    pub budget_limit_reported: bool,
    /// Session-wide total tokens recorded at goal creation. Seeds the
    /// spend accumulator (`last_session_tokens_seen`) so pre-goal usage
    /// is excluded from the goal's token count.
    #[serde(default)]
    pub token_baseline: i64,
    /// Monotonic high-water mark ratcheted by `SessionActor::goal_tokens`
    /// so wire values never decrease across compactions.
    #[serde(default)]
    pub tokens_used_high_water: i64,
    /// Cumulative parent-session tokens spent on this goal: the sum of
    /// positive per-call deltas of the session token total. Unlike a
    /// `current - baseline` difference, this can never shrink or freeze
    /// when auto-compaction reduces the context-size total. Best-effort
    /// sampling: growth fully consumed by a compaction between two
    /// `goal_tokens` calls is unobserved, and spend accrued since the
    /// last persisted snapshot is lost on crash (bounded by the snapshot
    /// cadence).
    #[serde(default)]
    pub parent_tokens_spent: i64,
    /// Session token total at the previous `SessionActor::goal_tokens`
    /// call — the anchor for the next positive delta. `None` on legacy
    /// snapshots; seeded from `token_baseline` on first use.
    #[serde(default)]
    pub last_session_tokens_seen: Option<i64>,
    pub history: Vec<GoalHistoryEntry>,

    /// Human-readable explanation set when the goal transitions to a
    /// paused state with a meaningful reason. `Blocked` and `InfraPaused`
    /// populate it (via [`GoalTracker::pause_with_message`]),
    /// but the field is orthogonal to status so future reasons can
    /// reuse it. Set only by [`GoalTracker::pause_with_message`];
    /// cleared by every transition out of a paused state —
    /// [`GoalTracker::resume`], [`GoalTracker::complete`], and
    /// [`GoalTracker::budget_limit`] all reset it to `None`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pause_message: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evaluator_blocker_key: Option<String>,
    #[serde(default)]
    pub evaluator_blocked_streak: u32,

    /// Short opaque identifier used to scope per-goal artifact paths
    /// owned by the harness. Today's consumers:
    ///
    /// * `goal_classifier.rs` — classifier details / changes diff /
    ///   per-skeptic verdict files (see the
    ///   `GOAL_CLASSIFIER_DETAILS_PATH_TEMPLATE`,
    ///   `GOAL_CLASSIFIER_CHANGES_PATH_TEMPLATE`,
    ///   `GOAL_VERIFIER_VERDICT_PATH_TEMPLATE`,
    ///   `GOAL_VERIFIER_DETAILS_PATH_TEMPLATE` consts).
    ///
    /// Generated by [`generate_verifier_id`] when the goal is created
    /// and persisted alongside the rest of the orchestration so the
    /// same id is reused across pause/resume cycles. The current
    /// model-facing template no longer references this id; the model
    /// is no longer instructed to read per-goal verdict files.
    ///
    /// Older persisted snapshots predate this field; the `serde(default)`
    /// attribute backfills a fresh id on load so verdict-file paths
    /// stay well-formed even after an upgrade.
    #[serde(default = "generate_verifier_id")]
    pub verifier_id: String,

    /// Number of times the goal-achievement classifier has been run
    /// for this goal. Reset only when the goal is recreated.
    #[serde(default)]
    pub classifier_runs_attempted: u32,
    /// Worker rounds since the last verification fired: `+1` per
    /// continuation build, reset to 0 when a classifier attempt is
    /// reserved. Drives the re-verify escalation.
    #[serde(default)]
    pub rounds_since_verify: u32,
    /// Hard cap on classifier runs for this goal. `None` means the
    /// cap has not been configured; `Some(0)` reserves the explicit
    /// "zero runs allowed" case. Mirrors the `token_budget:
    /// Option<i64>` precedent on this struct.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub classifier_max_runs: Option<u32>,
    /// Last aggregate verdict returned by the verification stage, if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_classifier_verdict: Option<GoalClassifierVerdict>,
    /// Path to the most recent verification-stage details artifact on disk.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_classifier_details_path: Option<String>,
    /// RFC3339 timestamp of the last classifier run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_classifier_at: Option<String>,
    /// Curated per-refuter gap summary (`build_gaps_summary`) from the
    /// most recent `NotAchieved` verdict. Inlined verbatim into every
    /// continuation directive until a later verdict overwrites it (an
    /// `Achieved` verdict clears it), so the freshest verifier feedback
    /// reaches the model each round rather than once.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_classifier_gaps: Option<String>,
    /// First verification round's full `FINAL_RESPONSE`, replayed as the
    /// breadth anchor on later rounds so a cold skeptic panel sees the whole
    /// deliverable, not just that round's fix note. Captured once (capped);
    /// never cleared on `Achieved` — it must outlive each round.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub first_final_response: Option<String>,
    /// Child session id of skeptic 0 (the persistent reject-gatekeeper)
    /// from the most recent N > 1 verification attempt. The next attempt
    /// resumes it (`resume_from`) so it delta-re-checks the prior gaps
    /// instead of re-analyzing cold. Cleared by [`GoalTracker::from_snapshot`]
    /// (the in-memory token records that anchor a resumed child's marginal
    /// accounting do not survive a restart), on goal completion, and never
    /// set for an N == 1 sole-judge panel.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub skeptic0_session_id: Option<String>,
    /// Resolved skeptic index → `{model, agent_type}` assignment, frozen at
    /// the first verification panel and reused on every resume so skeptic-0
    /// (and the cold panel) keep stable models across attempts. Index `i`
    /// holds `pool[i % pool.len()]`; the vector grows (clamped) but never
    /// rewrites a committed index. Empty ⇒ all skeptics inherit the current
    /// model. Persists across snapshot save/restore exactly like
    /// `skeptic0_session_id`, and is reset on the same terminal transitions.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub skeptic_model_assignment: Vec<crate::util::config::GoalRoleModel>,
    /// Normalized gap fingerprint of the previous `NotAchieved`
    /// rejection (see `goal_classifier::gap_fingerprint`). Compared
    /// against the next rejection's fingerprint to detect a stuck loop.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_gap_fingerprint: Option<String>,
    /// Count of consecutive rejections carrying the same gap
    /// fingerprint (1 on the first occurrence of a fingerprint). Drives
    /// the stall early-exit in [`GoalTracker::record_classifier_stall`].
    #[serde(default)]
    pub classifier_stall_count: u32,
    /// Count of consecutive `NotAchieved` verifications regardless of
    /// gap content (resets to 0 on an `Achieved` verdict). Drives the
    /// stall-triggered strategist: it fires when this reaches the
    /// configured `goal_strategist_every` (N) and again at each multiple
    /// (2N, 3N, …). Distinct from `classifier_stall_count`, which only
    /// counts *identical*-fingerprint repeats — this catches whack-a-mole
    /// where each round flags a different gap.
    #[serde(default)]
    pub consecutive_not_achieved: u32,
    /// The `consecutive_not_achieved` value at which the strategist last
    /// fired. The trigger fires when `consecutive_not_achieved >=
    /// last_strategist_fired_at + N`, which is SKIP-ROBUST: the synthetic
    /// concurrent-in-flight path can bump the streak past a multiple of N
    /// without landing exactly on it, and a strict `% N == 0` check would
    /// then miss the fire. Reset to 0 with `consecutive_not_achieved`.
    #[serde(default)]
    pub last_strategist_fired_at: u32,
    /// Added to the resolved classifier cap once the strategist has fired.
    #[serde(default)]
    pub strategist_cap_bonus: u32,

    /// Path to the most recent strategist strategy note on disk
    /// (`<session_dir>/goal/strategy.md`, via
    /// [`GoalTracker::strategy_path`]). `None` until the strategist runs.
    /// Surfaced in the continuation directive so the model re-reads it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_strategy_path: Option<String>,
    /// Short narrative recommendation read back from the strategist's
    /// note (capped). Inlined into the continuation directive until a
    /// later strategist run overwrites it; cleared on an `Achieved`
    /// verdict (same replay convention as `last_classifier_gaps`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_strategy_recommendation: Option<String>,

    /// `git rev-parse HEAD` captured at goal creation. Used by the
    /// classifier to diff the worktree against the goal's baseline.
    /// `None` for goals created before the baseline-capture wiring
    /// landed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub changes_baseline_commit: Option<String>,

    /// Path to the goal's plan markdown (`<session_dir>/goal/plan.md`,
    /// via [`GoalTracker::plan_path`]). `None` until a planner writes
    /// one. `is_some()` is the single source of truth for "this goal
    /// has a plan" — gates setup-time fire, the resume-retry path,
    /// and the load-time reconciler. Persisted across restart.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub plan_file: Option<PathBuf>,

    /// Path to the immutable snapshot of the planner's ORIGINAL plan
    /// (`<session_dir>/goal/plan.baseline.md`, via
    /// [`GoalTracker::plan_baseline_path`]). Captured once right after the
    /// planner first writes `plan_file`; never overwritten on later attempts
    /// or restarts. The verifier diffs the CURRENT plan against it
    /// (`capture_plan_changes`) so a skeptic sees every edit the agent made to
    /// `plan.md` during the run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub plan_baseline_file: Option<PathBuf>,

    /// True once the harness created and squat-verified the scratch root AND
    /// the implementer subdir, so prompts can honestly say the dir exists.
    /// `#[serde(skip)]`: recomputed by `from_snapshot` on every reload (the
    /// sole reload path), so a persisted value would be dead-on-read — same as
    /// the recomputed/transient `live_*` fields below.
    #[serde(skip)]
    pub scratch_dir_ready: bool,

    // Transient live-progress fields (not persisted)
    #[serde(skip)]
    pub live_subagent_tokens: u64,
    /// Per-model marginal-token breakdown (model_id, tokens), sorted by
    /// tokens descending. Transient mirror of the active goal's subagent
    /// token records; `#[serde(skip)]` so legacy snapshots deserialize and
    /// it is never persisted.
    #[serde(skip)]
    pub live_tokens_by_model: Vec<(String, u64)>,
    #[serde(skip)]
    pub live_context_window: u64,
    #[serde(skip)]
    pub live_context_pct: u8,
    #[serde(skip)]
    pub live_turn_count: u32,
    #[serde(skip)]
    pub live_tool_call_count: u32,

    /// True while the goal planner subagent is running. Latched by
    /// `emit_goal_planning` and reset after the planner finishes so the
    /// "planning…" badge survives the subagent-spawn / token-accounting
    /// `GoalUpdated`s that fire mid-run. Transient (never persisted).
    #[serde(skip)]
    pub planning_in_flight: bool,

    /// True while the verification skeptic panel is running. Latched around
    /// the verification stage (mirrors `planning_in_flight`) so the
    /// "Verifying…" badge survives the token-accounting / continuation
    /// `GoalUpdated`s that fire mid-verification. Transient (never persisted).
    #[serde(skip)]
    pub verifying_in_flight: bool,
}

// GoalTracker (pure state machine)

// Test helpers (shared across goal_tracker + goal_orchestrator tests)

#[cfg(test)]
pub(crate) fn make_base_orchestration() -> GoalOrchestration {
    GoalOrchestration {
        goal_id: "g-test".into(),
        objective: "test objective".into(),
        status: GoalStatus::Active,
        phase: GoalPhase::Idle,
        token_budget: None,
        elapsed_ms: 0,
        created_at: "2026-01-01T00:00:00Z".into(),
        current_subagent_id: None,
        current_subagent_role: None,
        total_worker_rounds: 0,
        total_verify_rounds: 0,
        budget_limit_reported: false,
        token_baseline: 0,
        tokens_used_high_water: 0,
        parent_tokens_spent: 0,
        last_session_tokens_seen: Some(0),
        history: Vec::new(),
        pause_message: None,
        evaluator_blocker_key: None,
        evaluator_blocked_streak: 0,
        verifier_id: generate_verifier_id(),
        classifier_runs_attempted: 0,
        rounds_since_verify: 0,
        classifier_max_runs: None,
        last_classifier_verdict: None,
        last_classifier_details_path: None,
        last_classifier_at: None,
        last_classifier_gaps: None,
        first_final_response: None,
        skeptic0_session_id: None,
        skeptic_model_assignment: Vec::new(),
        last_gap_fingerprint: None,
        classifier_stall_count: 0,
        consecutive_not_achieved: 0,
        last_strategist_fired_at: 0,
        strategist_cap_bonus: 0,
        last_strategy_path: None,
        last_strategy_recommendation: None,
        changes_baseline_commit: None,
        plan_file: None,
        plan_baseline_file: None,
        scratch_dir_ready: false,
        live_subagent_tokens: 0,
        live_tokens_by_model: Vec::new(),
        live_context_window: 0,
        live_context_pct: 0,
        live_turn_count: 0,
        live_tool_call_count: 0,
        planning_in_flight: false,
        verifying_in_flight: false,
    }
}

// Tests

#[cfg(test)]
#[path = "goal_tracker_tests.rs"]
mod tests;
