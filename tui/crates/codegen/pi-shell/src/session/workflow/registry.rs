pub(crate) const MAX_WORKFLOW_SOURCE_BYTES: u64 = 1024 * 1024;

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
    

}
