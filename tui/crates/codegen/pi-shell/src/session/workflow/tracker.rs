use pi_workflow::PhaseMeta;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowRunStatus {
    Active,
    UserPaused,
    BackOffPaused,
    NoProgressPaused,
    InfraPaused,
    Blocked,
    BudgetLimited,
    Interrupted,
    Complete,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowHistoryEntry {
    pub event: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    pub at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct WorkflowTokenLease {
    pub lease_id: String,
    pub grant: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowAgentRow {
    pub agent_id: String,
    pub label: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub phase: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    pub state: String,
    #[serde(default)]
    pub tokens_used: u64,
    #[serde(default)]
    pub duration_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowRunState {
    pub run_id: String,
    #[serde(default)]
    pub revision: u64,
    pub name: String,
    pub objective: String,
    pub status: WorkflowRunStatus,
    #[serde(default)]
    pub phases: Vec<PhaseMeta>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_phase: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_budget: Option<u64>,
    #[serde(default)]
    pub agents_used: u64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub token_leases: Vec<WorkflowTokenLease>,
    #[serde(default)]
    pub agent_usage_incomplete: bool,
    #[serde(default)]
    pub elapsed_ms_floor: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pause_message: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub history: Vec<WorkflowHistoryEntry>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub journal_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result_summary: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub agents: Vec<WorkflowAgentRow>,
}

#[cfg(test)]
mod tests {}
