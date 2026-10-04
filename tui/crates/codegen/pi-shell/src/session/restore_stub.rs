use std::time::Duration;

use anyhow::{Result, bail};
use pi_file_utils::storage_client::StorageClient;

use crate::agent::session_registry_client::{ SessionRegistryClient};

const UNAVAILABLE: &str = "Remote session restore is not available in this build";

#[derive(Debug)]
pub struct RestoreResult {
    pub session_id: String,
    pub local_session_id: String,
    pub codebase: CodebaseRestoreResult,
    pub memory: MemoryRestoreResult,
    pub session_state: SessionStateRestoreResult,
}

pub type ProgressCallback = Box<dyn Fn(&RestoreProgressEvent) + Send + Sync>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PhaseStep {
    Start,
    End,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RestoreProgressEvent {
    pub phase: RestorePhase,
    pub step: PhaseStep,
    pub incomplete: bool,
    pub message: String,
    pub detail: Option<String>,
    pub elapsed: Duration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RestorePhase {
    SessionRecord,
    Download,
    Codebase,
    Memory,
    SessionState,
    Finalize,
}

#[derive(Debug)]
pub struct CodebaseRestoreResult {
    pub strategy: RestoreStrategy,
    pub commits_applied: usize,
    pub staged_applied: bool,
    pub unstaged_applied: bool,
    pub untracked_copied: usize,
    pub binary_copied: usize,
    pub warnings: Vec<String>,
    pub stash_created: bool,
}

#[derive(Debug)]
pub enum RestoreStrategy {
    DirectCheckout,
    PatchReplay,
    Skipped(String),
}

#[derive(Debug)]
pub struct MemoryRestoreResult {
    pub sessions_copied: usize,
}

#[derive(Debug, Default)]
pub struct SessionStateRestoreResult {
    pub files_copied: u32,
    pub summary_restored: bool,
    pub updates_restored: bool,
}

pub struct RestoreSessionOpts {
    pub turn_override: Option<i32>,
    pub progress: Option<ProgressCallback>,
    pub restore_code: bool,
}

pub async fn restore_session_with_storage(
    _client: &SessionRegistryClient,
    _storage_client: &StorageClient,
    _session_id: &str,
    _target_cwd: &str,
    _opts: RestoreSessionOpts,
) -> Result<RestoreResult> {
    bail!(UNAVAILABLE)
}

