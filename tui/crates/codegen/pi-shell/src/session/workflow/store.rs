use std::io::{self, Read};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use pi_sampling_types::ReasoningEffort;

use super::tracker::WorkflowRunState;

pub(crate) const WORKFLOW_RUN_MANIFEST_VERSION: u8 = 4;
pub(crate) const MAX_RESTORED_WORKFLOW_RUNS: usize = 128;
pub(crate) const MAX_WORKFLOW_MANIFEST_BYTES: u64 = 512 * 1024;
pub(crate) const MAX_WORKFLOW_ARGS_BYTES: u64 = 1024 * 1024;
pub(crate) const MAX_WORKFLOW_EFFORT_BYTES: u64 = 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowRunManifest {
    pub version: u8,
    pub state: WorkflowRunState,
    pub script_revision: u32,
}

#[derive(Debug, Clone)]
pub struct RestoredWorkflowRun {
    pub manifest: WorkflowRunManifest,
    pub script: String,
    pub args: serde_json::Value,
    pub effort: Option<ReasoningEffort>,
}

pub(crate) fn validate_run_id(run_id: &str) -> io::Result<()> {
    if run_id.is_empty()
        || !run_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid workflow run id",
        ));
    }
    Ok(())
}

pub(crate) fn script_revision_path(run_dir: &Path, revision: u32) -> PathBuf {
    run_dir.join("scripts").join(format!("{revision:04}.rhai"))
}

pub(crate) fn read_bounded_nofollow(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "workflow artifact is not a regular file: {}",
                path.display()
            ),
        ));
    }
    if metadata.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "workflow artifact exceeds {limit} bytes: {}",
                path.display()
            ),
        ));
    }
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path)?;
    let opened = file.metadata()?;
    if !opened.is_file() || opened.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("workflow artifact changed during open: {}", path.display()),
        ));
    }
    let mut bytes = Vec::with_capacity(opened.len() as usize);
    file.take(limit.saturating_add(1)).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "workflow artifact exceeds {limit} bytes: {}",
                path.display()
            ),
        ));
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    
    

}
