//! Resume-by-title selection.
//!
//! `--resume <title>` is matched in memory against the rows `session/list` returns
//! ([`super::session_resolve::resolve_startup`]). The pager reads no session files, so a title is
//! whatever the agent says it is (ADR1; execution log section 10.16).

use super::session_resolve::ListedSession;

/// UUID-shaped resume args always take the id path, even when no such id
/// exists and a session is titled with that exact UUID.
pub(crate) fn is_uuid_shaped(arg: &str) -> bool {
    uuid::Uuid::try_parse(arg).is_ok()
}

/// Canonical key for title equality: trimmed `str::to_lowercase`. Plain
/// case-insensitive equality, not full Unicode caseless matching.
fn title_key(s: &str) -> String {
    s.trim().to_lowercase()
}

/// Hint appended to the failure for a non-id resume target, so the title miss
/// stays visible. Debug formatting: the arg is arbitrary user text.
pub(crate) fn title_miss_hint(arg: &str) -> String {
    format!(
        "no session id or title matched {arg:?} for this directory; \
         try `{} --resume <session-id>`",
        crate::brand::CLI_NAME
    )
}

/// Select the session a resume arg names by title, among the rows of `session/list`.
///
/// - `Ok(None)`: UUID-shaped or blank arg, or no title matched.
/// - `Ok(Some)`: exactly one row has that title.
/// - `Err`: more than one has. Never silently pick one: a script needs the same session every
///   time. The agent's titles are the first message of a session, so two sessions that began
///   with the same words are common; the message lists them with their last activity. Titles
///   are Debug-escaped: they are arbitrary user text, and raw control characters would corrupt
///   the listing.
pub(crate) fn select_by_title<'a>(
    arg: &str,
    rows: &'a [ListedSession],
) -> anyhow::Result<Option<&'a ListedSession>> {
    if is_uuid_shaped(arg) {
        return Ok(None);
    }
    let needle = title_key(arg);
    if needle.is_empty() {
        return Ok(None);
    }
    let matches: Vec<&ListedSession> = rows
        .iter()
        .filter(|row| row.title.as_deref().is_some_and(|t| title_key(t) == needle))
        .collect();
    match matches.as_slice() {
        [] => Ok(None),
        [only] => Ok(Some(*only)),
        _ => {
            let listing = matches
                .iter()
                .map(|row| {
                    let active = row
                        .updated_at
                        .map(|t| t.format("%Y-%m-%d %H:%M UTC").to_string())
                        .unwrap_or_else(|| "unknown".to_owned());
                    format!(
                        "  {}  {:?}  (last activity {active})",
                        row.id,
                        row.title.as_deref().unwrap_or_default()
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
            anyhow::bail!(
                "Multiple sessions match title {:?}:\n{listing}\n\
                 Resume by session id instead: {} --resume <session-id>",
                arg.trim(),
                crate::brand::CLI_NAME
            );
        }
    }
}

#[cfg(test)]
#[path = "session_title_resolve_tests.rs"]
mod tests;
