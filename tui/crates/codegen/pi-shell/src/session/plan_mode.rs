//! Plan mode state machine and prompt text generation.
//!
//! This module contains the [`PlanModeTracker`] struct that manages
//! the full plan mode lifecycle for a session. It is designed to be
//! testable in isolation — no references to `SessionActor`, conversation
//! history, or async I/O. Pure state machine logic.
//!
//! The `SessionActor` owns one `PlanModeTracker` (behind a `Mutex`) and
//! calls its methods at the appropriate points (`handle_session_mode`,
//! `handle_prompt`, `handle_completion`, `run_compact`).
/// Tracks plan mode lifecycle on the SessionActor.
///
/// Lives alongside `session_yolo_mode` and `active_agent_type` —
/// it is session-scoped mutable state, not part of AgentDefinition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum PlanModeState {
    /// Normal operating mode. No plan mode constraints.
    Inactive,
    /// Client toggled plan mode ON, but no prompt has been sent yet.
    /// The model does not know about plan mode yet. No tool call has
    /// been made, no system-reminder injected.
    ///
    /// Transitions:
    ///   -> Active  (first user prompt triggers injection)
    ///   -> Inactive (client toggles off before any prompt)
    Pending,
    /// Plan mode is active. The model has received plan mode instructions
    /// (either via system-reminder injection or via EnterPlanMode tool result).
    /// Write tools are blocked except for the plan file.
    ///
    /// Transitions:
    ///   -> Inactive    (ExitPlanMode approved, or user toggles off when idle)
    ///   -> ExitPending (user toggles off while a turn is in-flight)
    Active,
    /// Client toggled plan mode OFF while Active and a model turn is
    /// in-flight. We need to wait for the current turn to finish (or
    /// cancel it), then cleanly exit.
    ///
    /// Transitions:
    ///   -> Inactive (after turn completes, exit attachment injected)
    ExitPending,
}
/// Serializable snapshot of plan mode lifecycle state.
///
/// Persisted to `plan_mode.json` in the session directory and restored on
/// session reload/resume so plan mode survives process restarts.
/// The `plan_file_path` is NOT persisted — it is recomputed from session metadata.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PlanModeSnapshot {
    pub state: PlanModeState,
    pub was_previously_active: bool,
    pub reminder_count: u32,
    pub pending_exit_reminder: bool,
    /// Client was shown `exit_plan_mode` approval but has not answered yet.
    /// Survives process restart so the pager can restore approval chrome
    /// without treating every Active+plan.md session as pending.
    #[serde(default)]
    pub awaiting_plan_approval: bool,
}
#[cfg(test)]
mod tests {
    
    
}
