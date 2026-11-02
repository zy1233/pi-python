//! The pages in `docs/user-guide` describe `zypi`. A page that still names the
//! product it was forked from, or its paths and hosts, is stale (the `GROK_*`
//! environment variables are real names and are upper case, so they do not
//! match).

use std::path::Path;

#[test]
fn user_guide_pages_do_not_name_the_old_product() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("docs/user-guide");
    let mut pages = 0;
    for entry in std::fs::read_dir(&dir)
        .expect("docs/user-guide exists")
        .flatten()
    {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name == "README.md" || !name.ends_with(".md") {
            continue;
        }
        pages += 1;
        let content = std::fs::read_to_string(entry.path()).expect("page is UTF-8");
        assert!(
            content.starts_with('#'),
            "{name} should start with a markdown header"
        );
        for stale in ["Grok", "grok ", "~/.grok", ".grok/", "x.ai", "SpaceXAI"] {
            assert!(!content.contains(stale), "{name} still contains {stale:?}");
        }
    }
    assert!(pages > 0, "no guide page found in {}", dir.display());
}
