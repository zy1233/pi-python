use std::path::{Path, PathBuf};

pub(crate) const MAX_WORKFLOW_SOURCE_BYTES: u64 = 1024 * 1024;

pub(crate) fn project_root(session_cwd: &Path) -> PathBuf {
    pi_workspace::session::git::find_git_root_from_path(session_cwd)
        .unwrap_or_else(|_| session_cwd.to_path_buf())
}

#[cfg(windows)]
fn atomic_rename_noreplace_windows(source: &Path, target: &Path) -> io::Result<()> {
    if target.exists() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "destination already exists",
        ));
    }
    std::fs::rename(source, target)
}

#[cfg(test)]
mod tests {
    

    fn script(name: &str) -> String {
        format!("let meta = #{{ name: \"{name}\", description: \"d\" }};\ncomplete(\"ok\");")
    }

}
