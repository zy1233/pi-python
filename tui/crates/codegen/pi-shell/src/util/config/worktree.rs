use pi_fast_worktree::CreationMode;
use serde::{Deserialize, Serialize};

/// Worktree creation type configuration.
///
/// Mirrors the internal `CreationMode` enum from pi-fast-worktree but uses
/// config-friendly naming (lowercase strings in TOML).
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum WorktreeType {
    /// Linked worktree via `git worktree add --no-checkout` + parallel CoW copy.
    /// This is the fastest mode for large repos.
    #[default]
    Linked,
    /// Standalone repository copy with independent `.git/` directory.
    /// Can be promoted to replace the source via `rename()`.
    Standalone,
    /// Plain `git worktree add` with full checkout.
    Git,
}

impl std::str::FromStr for WorktreeType {
    type Err = ();

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "linked" => Ok(Self::Linked),
            "standalone" => Ok(Self::Standalone),
            "git" => Ok(Self::Git),
            _ => Err(()),
        }
    }
}

impl From<WorktreeType> for CreationMode {
    fn from(t: WorktreeType) -> Self {
        match t {
            WorktreeType::Linked => CreationMode::Linked,
            WorktreeType::Standalone => CreationMode::Standalone,
            WorktreeType::Git => CreationMode::GitCheckout,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_worktree_type_fromstr() {
        assert_eq!("linked".parse::<WorktreeType>(), Ok(WorktreeType::Linked));
        assert_eq!(
            "standalone".parse::<WorktreeType>(),
            Ok(WorktreeType::Standalone)
        );
        assert_eq!("git".parse::<WorktreeType>(), Ok(WorktreeType::Git));
        assert!("invalid".parse::<WorktreeType>().is_err());
        assert!("LINKED".parse::<WorktreeType>().is_err());
    }

    // === restore_code config tests ===
}
