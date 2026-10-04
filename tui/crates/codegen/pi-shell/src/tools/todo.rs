//! Todo types — re-exported from `pi-tools` with ACP conversion helpers.
//!
//! Types are canonical in `pi-tools`. This module adds ACP ↔ TodoItem
//! conversions since `pi-tools` is protocol-agnostic.

pub use pi_tools::implementations::grok_build::todo::TodoId;
pub use pi_tools::implementations::grok_build::todo::TodoItem;
pub use pi_tools::implementations::grok_build::todo::TodoPriority;
pub(crate) use pi_tools::implementations::grok_build::todo::TodoState;
pub use pi_tools::implementations::grok_build::todo::TodoStatus;

use agent_client_protocol as acp;

/// Convert an ACP `PlanEntry` to a `TodoItem`.
///
/// Handles the cancelled state: ACP has no `Cancelled` status, so cancelled
/// items are stored as `Completed` with `{"cancelled": true}` in meta.
pub fn todo_item_from_plan_entry(entry: acp::PlanEntry) -> TodoItem {
    let status = match entry.status {
        acp::PlanEntryStatus::Pending => TodoStatus::Pending,
        acp::PlanEntryStatus::InProgress => TodoStatus::InProgress,
        acp::PlanEntryStatus::Completed => {
            // Check if this is actually a cancelled item
            if entry
                .meta
                .as_ref()
                .and_then(|m| m.get("cancelled"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                TodoStatus::Cancelled
            } else {
                TodoStatus::Completed
            }
        }
        // TODO(acp-0.10): `PlanEntryStatus` is #[non_exhaustive].
        _ => TodoStatus::Pending,
    };
    TodoItem {
        content: entry.content,
        priority: match entry.priority {
            acp::PlanEntryPriority::High => TodoPriority::High,
            acp::PlanEntryPriority::Medium => TodoPriority::Medium,
            acp::PlanEntryPriority::Low => TodoPriority::Low,
            // TODO(acp-0.10): `PlanEntryPriority` is #[non_exhaustive].
            _ => TodoPriority::Medium,
        },
        status,
        meta: entry.meta.map(serde_json::Value::Object),
    }
}

