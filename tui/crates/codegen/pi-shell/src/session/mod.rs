pub mod acp_types;
pub mod announcement_state;
pub mod commands;
pub mod handle;
pub mod pending_interaction;
pub use self::acp_types::*;
pub use self::handle::*;
pub use self::persistence::{
    LocalFeedbackEntry, UserFeedbackEntry, find_local_child_for_remote, resolve_local_session,
    resolve_local_session_any_cwd, session_exists_for_cwd,
};
pub use self::result::{Empty, ExtMethodResult};
pub use prod_mc_cli_chat_proxy_types::feedback_types::{
    ClientType, FeedbackImage, FeedbackTerminalInfo, MAX_FEEDBACK_IMAGE_BYTES,
    MAX_FEEDBACK_IMAGE_TOTAL_BYTES, MAX_FEEDBACK_IMAGES, RatingType, feedback_image_extension,
    validate_feedback_images,
};
/// Describes who originated a prompt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PromptOrigin {
    /// A normal user-initiated prompt.
    User,
    /// Auto-wake prompt injected when a background terminal task completed.
    TaskCompleted {
        /// The background task ID (without the `task-completed-` prefix).
        task_id: String,
    },
    /// Auto-wake prompt injected when a background subagent completed.
    SubagentCompleted {
        /// The subagent ID (without the `subagent-completed-` prefix).
        subagent_id: String,
    },
    WorkflowCompleted {
        completion_id: String,
    },
    /// Server-initiated prompt from the idle-gated notification drain
    /// (`maybe_drain_notifications`). Batches one or more monitor-event
    /// or bash-task-completed notifications into a single turn while the
    /// user is idle.
    NotificationDrain,
    /// Orchestrator-initiated summary turn. The goal orchestrator injects a
    /// system reminder into context and then triggers a model turn so the
    /// model can print a visible progress update.
    GoalSummary,
    /// Verification-stage nudge injected after the verification stage
    /// achieved — keep working" system-reminder body alongside the
    /// path to the persisted details file. The variant name retains
    /// the `Classifier` prefix for wire stability.
    GoalClassifierNudge,
    /// Scheduled task (`/loop`) prompt fired by the scheduler via the pager.
    SchedulerFired,
    /// Turn injected after a resumed plan-approval decision: the
    /// shell re-parked `exit_plan_mode` on resume, the user approved/revised,
    /// and the shell injects the follow-up turn. Synthetic so the user never
    /// typed it — kept out of prompt history — but it still runs a real turn.
    PlanResume,
}
impl PromptOrigin {
    /// Parse a prompt_id string into a `PromptOrigin`.
    pub fn from_prompt_id(prompt_id: &str) -> Self {
        if let Some(task_id) = prompt_id.strip_prefix("task-completed-") {
            Self::TaskCompleted {
                task_id: task_id.to_string(),
            }
        } else if let Some(subagent_id) = prompt_id.strip_prefix("subagent-completed-") {
            Self::SubagentCompleted {
                subagent_id: subagent_id.to_string(),
            }
        } else if let Some(completion_id) = prompt_id.strip_prefix("workflow-completed-") {
            Self::WorkflowCompleted {
                completion_id: completion_id.to_string(),
            }
        } else if prompt_id.starts_with("notifications-") {
            Self::NotificationDrain
        } else if prompt_id.starts_with("goal-summary-") {
            Self::GoalSummary
        } else if prompt_id.starts_with("goal-classifier-nudge-") {
            Self::GoalClassifierNudge
        } else if prompt_id.starts_with("scheduler-fired-") {
            Self::SchedulerFired
        } else if prompt_id.starts_with("plan-resume-") {
            Self::PlanResume
        } else {
            Self::User
        }
    }
    /// Returns `true` for auto-wake (synthetic) prompts.
    pub fn is_synthetic(&self) -> bool {
        !matches!(self, Self::User)
    }
    /// Whether a `UserMessageChunk` echo for this origin must stay out of
    /// client scrollback (live and on resume). Model-only / side-channel
    /// content — UI already surfaces it via task pane, monitor gutter, etc.
    ///
    /// Cron (`SchedulerFired`) and plan-resume follow-ups still render;
    /// real user turns always render.
    pub fn hide_user_echo_from_scrollback(&self) -> bool {
        match self {
            Self::User | Self::SchedulerFired | Self::PlanResume => false,
            Self::TaskCompleted { .. }
            | Self::SubagentCompleted { .. }
            | Self::WorkflowCompleted { .. }
            | Self::NotificationDrain
            | Self::GoalSummary
            | Self::GoalClassifierNudge => true,
        }
    }
}
#[cfg(test)]
mod tests {
    use super::{PromptOrigin};
    #[test]
    fn from_prompt_id_user() {
        assert_eq!(
            PromptOrigin::from_prompt_id("my-prompt"),
            PromptOrigin::User
        );
        assert!(!PromptOrigin::from_prompt_id("my-prompt").is_synthetic());
    }
    #[test]
    fn goal_classifier_nudge_origin_round_trips_through_from_prompt_id() {
        let prompt_id = format!("goal-classifier-nudge-{}", uuid::Uuid::now_v7());
        let origin = PromptOrigin::from_prompt_id(&prompt_id);
        assert!(matches!(origin, PromptOrigin::GoalClassifierNudge));
        assert!(origin.is_synthetic());
    }
    #[test]
    fn notification_drain_is_server_initiated() {
        let prompt_id = "notifications-019e0000-0000-7000-8000-0000000000aa";
        assert!(PromptOrigin::from_prompt_id(prompt_id).is_synthetic());
    }
    #[test]
    fn hide_user_echo_from_scrollback_by_origin() {
        assert!(!PromptOrigin::User.hide_user_echo_from_scrollback());
        assert!(
            !PromptOrigin::from_prompt_id("scheduler-fired-abc").hide_user_echo_from_scrollback()
        );
        assert!(!PromptOrigin::from_prompt_id("plan-resume-1").hide_user_echo_from_scrollback());
        assert!(PromptOrigin::from_prompt_id("task-completed-t1").hide_user_echo_from_scrollback());
        assert!(
            PromptOrigin::from_prompt_id("subagent-completed-s1").hide_user_echo_from_scrollback()
        );
        assert!(
            PromptOrigin::from_prompt_id("notifications-uuid").hide_user_echo_from_scrollback()
        );
        assert!(
            PromptOrigin::from_prompt_id("workflow-completed-wf-1-9")
                .hide_user_echo_from_scrollback()
        );
        assert!(PromptOrigin::from_prompt_id("goal-summary-1").hide_user_echo_from_scrollback());
        assert!(
            PromptOrigin::from_prompt_id("goal-classifier-nudge-1")
                .hide_user_echo_from_scrollback()
        );
    }
}
/// Share session request/response types
pub mod share {
}
pub(crate) mod events;
pub mod feedback;
pub mod goal_tracker;
pub mod helpers;
pub(crate) mod image_normalize;
pub use pi_shared::session::info;
pub(crate) mod mcp_dispatcher;
#[cfg(test)]
mod mcp_dispatcher_e2e_tests;
pub(crate) mod mcp_restart;
pub mod memory;
pub mod persistence;
pub mod plan_mode;
#[path = "restore_stub.rs"]
pub mod restore;
pub mod result;
pub mod signals;
pub mod storage;
pub(crate) mod telemetry;
pub mod unified_list;
pub(crate) mod wire_tags;
pub(crate) mod workflow;
pub mod worktree;
