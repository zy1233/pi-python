use super::*;
use std::path::PathBuf;

fn row(id: &str, title: Option<&str>) -> ListedSession {
    ListedSession {
        id: id.to_owned(),
        cwd: PathBuf::from("/ws"),
        title: title.map(str::to_owned),
        updated_at: None,
    }
}

fn id_of(row: Option<&ListedSession>) -> &str {
    &row.expect("expected a selected session").id
}

#[test]
fn blank_or_unmatched_arg_selects_none() {
    let rows = [row("a", Some("Fix Login"))];
    assert!(select_by_title("nope", &rows).unwrap().is_none());
    assert!(select_by_title("   ", &rows).unwrap().is_none());
}

#[test]
fn a_session_without_a_title_is_never_selected() {
    let rows = [row("a", None), row("b", Some("Other"))];
    assert!(select_by_title("a", &rows).unwrap().is_none());
    assert!(select_by_title("", &rows).unwrap().is_none());
}

#[test]
fn single_match_is_case_insensitive_and_trimmed() {
    let rows = [row("a", Some("Fix Login Bug")), row("b", Some("Other"))];
    assert_eq!(
        id_of(select_by_title("  FIX login bug ", &rows).unwrap()),
        "a"
    );
}

/// The contract is a simple lowercase comparison: accented letters match
/// across case, but one-to-many case folds do not (`to_lowercase` maps
/// U+00DF to itself, so "STRASSE" never equals a stored "straße").
#[test]
fn non_ascii_case_matching_contract() {
    let rows = [row("a", Some("Café Löschen"))];
    assert_eq!(id_of(select_by_title("CAFÉ LÖSCHEN", &rows).unwrap()), "a");
    let sharp = [row("b", Some("straße"))];
    assert!(select_by_title("STRASSE", &sharp).unwrap().is_none());
}

#[test]
fn duplicate_titles_error_lists_ids_with_escaped_titles_and_last_activity() {
    // A title with a newline would corrupt the one-match-per-line listing if
    // rendered raw.
    let mut first = row("id-a", Some("Dup\nTitle"));
    first.updated_at = Some("2026-10-16T09:30:00Z".parse().unwrap());
    let second = row("id-b", Some("Dup\nTitle"));
    let msg = select_by_title("dup\ntitle", &[first, second])
        .unwrap_err()
        .to_string();
    assert!(
        msg.contains("id-a") && msg.contains("id-b"),
        "both ids must be listed: {msg}"
    );
    assert!(
        msg.contains("Dup\\nTitle"),
        "titles must be Debug-escaped: {msg}"
    );
    assert!(
        msg.contains("last activity 2026-10-16 09:30 UTC") && msg.contains("last activity unknown"),
        "each line must say when the session was last active: {msg}"
    );
    assert!(
        msg.contains("--resume <session-id>"),
        "the way out must be named: {msg}"
    );
    assert!(
        !msg.starts_with("Error:"),
        "main prints the `Error: ` prefix itself: {msg}"
    );
}

/// No row is preferred over another: the old rule that a `/rename`d title beat colliding automatic
/// ones needs a flag the agent does not send, and an order that depends on the agent would make a
/// script pick another session from one day to the next.
#[test]
fn the_same_title_twice_is_ambiguous_whatever_the_order() {
    let forward = [row("a", Some("Dup")), row("b", Some("Dup"))];
    let backward = [row("b", Some("Dup")), row("a", Some("Dup"))];
    for rows in [&forward, &backward] {
        let msg = select_by_title("Dup", rows).unwrap_err().to_string();
        assert!(
            msg.contains("Multiple sessions match title"),
            "unexpected message: {msg}"
        );
    }
}

#[test]
fn uuid_shaped_arg_never_matches_titles() {
    let uuid = "12345678-1234-1234-1234-123456789abc";
    let rows = [row("a", Some(uuid))];
    assert!(select_by_title(uuid, &rows).unwrap().is_none());
}

#[test]
fn title_miss_hint_escapes_arg_and_suggests_search() {
    let hint = title_miss_hint("evil\ntitle");
    assert!(hint.contains("evil\\ntitle"), "arg must be escaped: {hint}");
    assert!(hint.contains("zypi --resume"), "missing hint: {hint}");
}
