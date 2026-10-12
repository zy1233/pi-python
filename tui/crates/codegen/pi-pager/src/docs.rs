//! How-to documentation data (embedded markdown).
//!
//! Single source of truth: the static `USER_GUIDE` array holds every doc. All
//! lookups are zero-allocation; `DocEntry` exists only for backward
//! compatibility with the TUI doc picker.
//!
//! The pages are compiled into the binary. `zypi` does not write them to disk.
//! No slash command opens the doc picker or the doc viewer now; the pages are
//! also the repository's user guide (`docs/user-guide/`).

/// A compile-time document entry. All fields are `&'static str`.
#[derive(Debug)]
pub struct Doc {
    pub filename: &'static str,
    pub title: &'static str,
    pub description: &'static str,
    pub content: &'static str,
}

/// Owned variant for the TUI doc picker (backward compat).
#[derive(Debug, Clone)]
pub struct DocEntry {
    pub title: String,
    pub description: String,
    /// Embedded markdown content.
    pub content: &'static str,
}

impl From<&Doc> for DocEntry {
    fn from(d: &Doc) -> Self {
        Self {
            title: d.title.into(),
            description: d.description.into(),
            content: d.content,
        }
    }
}

// ── Static doc tables ────────────────────────────────────────────────────────

macro_rules! guide {
    ($file:literal, $title:literal, $desc:literal) => {
        Doc {
            filename: $file,
            title: $title,
            description: $desc,
            content: include_str!(concat!("../docs/user-guide/", $file)),
        }
    };
}

pub static USER_GUIDE: &[Doc] = &[
    guide!(
        "01-getting-started.md",
        "Getting Started",
        "Set up the model, start zypi, and the keys to learn first"
    ),
    guide!(
        "04-slash-commands.md",
        "Slash Commands",
        "The built-in / commands and the command palette"
    ),
    guide!(
        "06-theming.md",
        "Theming and Appearance",
        "Themes, auto mode, minimal mode, and pager.toml"
    ),
    guide!(
        "17-sessions.md",
        "Sessions",
        "Where sessions are stored, how to continue one, and how to export one"
    ),
    guide!(
        "21-terminal-support.md",
        "Terminal Support and Troubleshooting",
        "zypi doctor, tmux, SSH, truecolor, and the clipboard"
    ),
    guide!(
        "22-permissions-and-safety.md",
        "Permissions and Safety",
        "Permission modes, project trust, and what the agent sends to the model provider"
    ),
    guide!(
        "25-status-line.md",
        "Status Line",
        "The status row and its stdin JSON contract (empty with the Python agent)"
    ),
];

// ── Public API ───────────────────────────────────────────────────────────────

/// Find a doc by title (case-insensitive). Returns the static entry.
pub fn find_doc(title: &str) -> Option<&'static Doc> {
    USER_GUIDE
        .iter()
        .find(|d| d.title.eq_ignore_ascii_case(title))
}

/// All doc titles, zero allocation.
pub fn all_titles() -> impl Iterator<Item = &'static str> {
    USER_GUIDE.iter().map(|d| d.title)
}

/// Returns the content of a how-to document by exact title match (case-insensitive).
pub fn get_howto_doc(title: &str) -> Option<&'static str> {
    find_doc(title).map(|d| d.content)
}

/// Returns a list of available how-to titles for the model to choose from.
pub fn list_howto_titles() -> Vec<String> {
    all_titles().map(String::from).collect()
}

/// Returns all docs as owned `DocEntry` values for the TUI doc picker.
pub fn default_howto_entries() -> Vec<DocEntry> {
    USER_GUIDE.iter().map(DocEntry::from).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_guide_entries_are_valid() {
        for doc in USER_GUIDE {
            assert!(!doc.content.is_empty(), "Doc {} is empty", doc.filename);
            assert!(
                !doc.title.is_empty(),
                "Doc {} has empty title",
                doc.filename
            );
            assert!(
                !doc.description.is_empty(),
                "Doc {} has empty description",
                doc.filename
            );
            assert!(
                doc.content.starts_with('#'),
                "Doc {} should start with a markdown header",
                doc.filename
            );
        }
    }

    #[test]
    fn user_guide_entries_have_no_duplicates() {
        let mut seen = std::collections::HashSet::new();
        for doc in USER_GUIDE {
            assert!(
                seen.insert(doc.filename),
                "Duplicate doc in list: {}",
                doc.filename
            );
        }
    }

    #[test]
    fn default_howto_entries_includes_all_user_guide_docs() {
        let entries = default_howto_entries();
        assert_eq!(entries.len(), USER_GUIDE.len());
        for (i, doc) in USER_GUIDE.iter().enumerate() {
            assert_eq!(entries[i].title, doc.title, "Entry {} title mismatch", i);
        }
    }

    #[test]
    fn find_doc_is_case_insensitive() {
        let doc = find_doc("getting started").expect("should find Getting Started");
        assert_eq!(doc.title, "Getting Started");
        assert!(find_doc("nonexistent guide").is_none());
    }

    #[test]
    fn all_titles_covers_the_user_guide() {
        let titles: Vec<_> = all_titles().collect();
        assert_eq!(titles.len(), USER_GUIDE.len());
    }

    #[test]
    fn get_howto_doc_delegates_to_find_doc() {
        assert!(get_howto_doc("Getting Started").is_some());
        assert!(get_howto_doc("no such doc").is_none());
    }

    #[test]
    fn list_howto_titles_returns_all() {
        let titles = list_howto_titles();
        assert_eq!(titles.len(), USER_GUIDE.len());
    }

    /// The pages describe `zypi`. A page that still names the product it was
    /// forked from, or its paths and hosts, is stale (the `GROK_*` environment
    /// variables are real names and are upper case, so they do not match).
    #[test]
    fn user_guide_pages_do_not_name_the_old_product() {
        for doc in USER_GUIDE {
            for stale in ["Grok", "grok ", "~/.grok", ".grok/", "x.ai", "SpaceXAI"] {
                assert!(
                    !doc.content.contains(stale),
                    "{} still contains {stale:?}",
                    doc.filename
                );
            }
        }
    }

    /// A page file that `USER_GUIDE` does not list is compiled into nothing
    /// and cannot be reached: it must be listed or deleted.
    #[test]
    fn every_page_in_the_guide_directory_is_listed() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("docs/user-guide");
        let listed: std::collections::HashSet<&str> =
            USER_GUIDE.iter().map(|d| d.filename).collect();
        for entry in std::fs::read_dir(&dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name == "README.md" {
                continue;
            }
            assert!(
                listed.contains(name.as_str()),
                "{name} is in docs/user-guide but not in USER_GUIDE"
            );
        }
    }
}
