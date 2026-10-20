use anyhow::{Context, bail};
use std::env;
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::Command;

/// The files called `name` in the directories of `path`, in the order of `path`.
///
/// `path` has the form of the `PATH` variable. Every result is an absolute path: an entry of
/// `path` that is relative, or empty, counts from `cwd`, as it does for the shell and for
/// `Command::new`. A candidate has to be a file. On Unix it also needs an execute bit.
fn executables_on_path(name: &str, path: &OsStr, cwd: &Path) -> Vec<PathBuf> {
    let file_name = format!("{name}{}", env::consts::EXE_SUFFIX);
    env::split_paths(path)
        .map(|dir| cwd.join(dir).join(&file_name))
        .filter(|candidate| is_executable_file(candidate))
        .collect()
}

#[cfg(unix)]
fn is_executable_file(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn is_executable_file(path: &Path) -> bool {
    path.is_file()
}

fn check_protoc_good(protoc: &Path) -> anyhow::Result<()> {
    let output = Command::new(protoc)
        .arg("--version")
        .output()
        .context("Failed to execute protoc")?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!(
            "protoc --version failed, likely dotslash is missing; \
             try `cargo install dotslash`; stdout: {stdout:?}, stderr: {stderr:?}"
        );
    }
    Ok(())
}

fn is_github_actions() -> bool {
    env::var_os("GITHUB_ACTIONS").is_some()
}

/// Find `protoc` command.
///
/// Search order:
/// 1. `$PROTOC` environment variable (set by Bazel `build_script_env` or user override)
/// 2. `bin/protoc` walking up parent directories (dotslash wrapper for local dev)
/// 3. `protoc` on `$PATH` (system install or other tooling), as an absolute path
///
/// When `bin/protoc` exists but fails to execute (e.g. the dotslash wrapper running
/// in Bazel remote execution where `dotslash` is not installed), the error is not fatal —
/// we fall through to the PATH-based lookup instead.
///
/// Returns `Ok(None)` if not found and not in a strict environment (GitHub Actions).
pub fn find_protoc() -> anyhow::Result<Option<PathBuf>> {
    // 1. Check the PROTOC env var first. This is the standard override used by prost-build
    //    and is set by Bazel cargo_build_script build_script_env to point at a hermetic
    //    protoc binary instead of the dotslash wrapper.
    if let Ok(protoc_env) = env::var("PROTOC") {
        let protoc = PathBuf::from(&protoc_env);
        if protoc.try_exists()? {
            check_protoc_good(&protoc)?;
            return Ok(Some(protoc));
        }
    }

    // 2. Walk up directories looking for bin/protoc (dotslash wrapper).
    let cwd = env::current_dir()?;
    let mut dir = cwd.clone();
    let mut dir_rel = PathBuf::new();
    loop {
        // Return relative path to make build more deterministic.
        let protoc = dir_rel.join("bin/protoc");
        if protoc.try_exists()? {
            match check_protoc_good(&protoc) {
                Ok(()) => return Ok(Some(protoc)),
                Err(e) => {
                    // bin/protoc exists but can't execute — likely the dotslash wrapper
                    // in an environment without dotslash (e.g. Bazel remote execution).
                    // Fall through to PATH-based lookup below.
                    eprintln!(
                        "bin/protoc found at `{}` but failed to execute: {e:#}; \
                         trying protoc from PATH as fallback",
                        protoc.display()
                    );
                    break;
                }
            }
        }
        if !dir.pop() {
            break;
        }
        dir_rel.push("..");
    }

    // 3. Try protoc from PATH (system install or other tooling).
    //
    //    Return the absolute path of the file, not the bare name `protoc`. The caller prints
    //    the path as `cargo:rerun-if-changed`, and cargo treats a path that does not exist as
    //    changed in every run. The bare name is no file in the package directory, so each
    //    `cargo` run started the build script again, and that rebuilt the crates that use it.
    if let Some(path) = env::var_os("PATH") {
        for candidate in executables_on_path("protoc", &path, &cwd) {
            if check_protoc_good(&candidate).is_ok() {
                return Ok(Some(candidate));
            }
        }
    }

    // 4. Not found anywhere.
    if is_github_actions() {
        return Err(anyhow::anyhow!(
            "`protoc` not found (checked $PROTOC env, bin/protoc, and PATH)"
        ));
    }
    eprintln!("`protoc` not found; likely it is missing in docker image");
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;
    use std::fs;

    fn search_path(dirs: &[&Path]) -> OsString {
        env::join_paths(dirs).unwrap()
    }

    /// Make the file `dir/protoc` with the given Unix mode bits (and its directory).
    #[cfg(unix)]
    fn make_protoc(dir: &Path, mode: u32) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        fs::create_dir_all(dir).unwrap();
        let file = dir.join("protoc");
        fs::write(&file, "#!/bin/sh\n").unwrap();
        fs::set_permissions(&file, fs::Permissions::from_mode(mode)).unwrap();
        file
    }

    #[cfg(unix)]
    #[test]
    fn finds_an_executable_and_gives_its_absolute_path() {
        let tmp = tempfile::tempdir().unwrap();
        let bin = tmp.path().join("bin");
        let file = make_protoc(&bin, 0o755);

        let found = executables_on_path("protoc", &search_path(&[&bin]), tmp.path());

        assert_eq!(found, vec![file]);
        assert!(found[0].is_absolute());
    }

    #[cfg(unix)]
    #[test]
    fn a_relative_entry_counts_from_the_given_directory() {
        let tmp = tempfile::tempdir().unwrap();
        let file = make_protoc(&tmp.path().join("tools"), 0o755);

        let found = executables_on_path("protoc", OsStr::new("tools"), tmp.path());

        assert_eq!(found, vec![file]);
        assert!(found[0].is_absolute());
    }

    #[cfg(unix)]
    #[test]
    fn an_empty_entry_counts_as_the_given_directory() {
        let tmp = tempfile::tempdir().unwrap();
        let file = make_protoc(tmp.path(), 0o755);

        // The text "" is one empty entry. A shell searches the current directory for it.
        let found = executables_on_path("protoc", OsStr::new(""), tmp.path());

        assert_eq!(found, vec![file]);
    }

    #[cfg(unix)]
    #[test]
    fn keeps_the_order_of_the_entries() {
        let tmp = tempfile::tempdir().unwrap();
        let (a, b) = (tmp.path().join("a"), tmp.path().join("b"));
        let in_a = make_protoc(&a, 0o755);
        let in_b = make_protoc(&b, 0o755);

        let found = executables_on_path("protoc", &search_path(&[&b, &a]), tmp.path());

        assert_eq!(found, vec![in_b, in_a]);
    }

    #[cfg(unix)]
    #[test]
    fn skips_a_file_that_has_no_execute_bit() {
        let tmp = tempfile::tempdir().unwrap();
        let bin = tmp.path().join("bin");
        make_protoc(&bin, 0o644);

        assert!(executables_on_path("protoc", &search_path(&[&bin]), tmp.path()).is_empty());
    }

    #[test]
    fn skips_a_directory_that_has_the_name() {
        let tmp = tempfile::tempdir().unwrap();
        let bin = tmp.path().join("bin");
        fs::create_dir_all(bin.join(format!("protoc{}", env::consts::EXE_SUFFIX))).unwrap();

        assert!(executables_on_path("protoc", &search_path(&[&bin]), tmp.path()).is_empty());
    }

    #[test]
    fn skips_an_entry_that_does_not_exist() {
        let tmp = tempfile::tempdir().unwrap();
        let missing = tmp.path().join("missing");

        assert!(executables_on_path("protoc", &search_path(&[&missing]), tmp.path()).is_empty());
    }

    #[test]
    fn the_path_that_find_protoc_gives_is_a_file_that_exists() {
        // The caller prints this path as `cargo:rerun-if-changed`. Cargo treats a path that
        // does not exist as changed in every run, and the bare name `protoc` is such a path:
        // it made the build script run again in each `cargo` run. Where there is no protoc,
        // `find_protoc` gives `None`, and there is nothing to check.
        if let Some(protoc) = find_protoc().unwrap() {
            assert!(protoc.exists(), "`{}` is not a file", protoc.display());
        }
    }
}
