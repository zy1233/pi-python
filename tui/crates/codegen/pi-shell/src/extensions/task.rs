use serde::Deserialize;
use serde::Serialize;
use pi_tools::types::KillOutcome;
use pi_tools::implementations::grok_build::task::types::SubagentCancelOutcome;

/// Client-facing kill reason on `x.ai/task/kill`. Older clients omit it.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum TaskKillSource {
    #[default]
    ClientUi,
    Teardown,
}

/// Wire DTO for the `x.ai/task/kill` ext response payload (nested under
/// `result` in the `ExtMethodResult` envelope).
///
/// `pub` (with both serde directions) so ACP clients deserialize the typed
/// outcome instead of probing raw JSON.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KillTaskResponse {
    pub task_id: String,
    pub outcome: KillOutcome,
}

/// Wire mirror of the coordinator's [`SubagentCancelOutcome`], `kind`-tagged so
/// a client can branch and read the already-finished `status`. Sent alongside
/// the legacy `cancelled` bool: a new pager prefers this, an old one ignores it.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SubagentCancelOutcomeDto {
    /// A live subagent was cancelled — a real `SubagentFinished` is coming.
    Cancelled,
    /// Already finished — no finish coming; `status` is the real terminal status.
    AlreadyFinished { status: String },
    /// The id is unknown (never existed / evicted) — no finish coming.
    NotFound,
    /// Unknown future `kind` (`#[serde(other)]`): lets an old client still parse
    /// and fall back to the legacy bool. Never produced by `From`.
    #[serde(other)]
    Unknown,
}

impl From<SubagentCancelOutcome> for SubagentCancelOutcomeDto {
    fn from(outcome: SubagentCancelOutcome) -> Self {
        match outcome {
            SubagentCancelOutcome::Cancelled => Self::Cancelled,
            SubagentCancelOutcome::AlreadyFinished { status } => Self::AlreadyFinished { status },
            SubagentCancelOutcome::NotFound => Self::NotFound,
        }
    }
}

/// Wire DTO for the `x.ai/subagent/cancel` response payload (under `result` in
/// the `ExtMethodResult` envelope). `pub` + both serde dirs so clients read it typed.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelSubagentResponse {
    pub subagent_id: String,
    /// Legacy wire-compat flag for older pagers; new clients prefer `outcome`.
    pub cancelled: bool,
    /// Typed outcome; `None` only from an older shell. This shell always sets it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<SubagentCancelOutcomeDto>,
}

// ── Subagent list_running DTOs ────────────────────────────────────────────

// ── Subagent get DTOs ────────────────────────────────────────────────────

// ── Scheduler DTOs ────────────────────────────────────────────────────

