mod envelope;
mod row;
use agent_client_protocol as acp;
pub use envelope::{FacetMap, FacetValue, SessionKind, SessionMetaEnvelope};
/// Directory scope the returned sessions were drawn from. Wire form is the
/// `as_str` value (`x.ai/listScope`), so no serde derive is needed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ListScope {
    /// Scoped to the request cwd.
    #[default]
    Cwd,
    /// Relaxed to the cwd's repo when the cwd itself had no sessions.
    Repo,
    /// Relaxed to all directories when the cwd is not a git repo.
    All,
}
impl ListScope {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Cwd => "cwd",
            Self::Repo => "repo",
            Self::All => "all",
        }
    }
    /// True when the scope relaxed past the cwd, to the repo or to all directories.
    pub const fn is_relaxed(self) -> bool {
        !matches!(self, Self::Cwd)
    }
}
pub(super) fn to_meta<E: std::fmt::Display>(
    value: Result<serde_json::Value, E>,
) -> Option<acp::Meta> {
    match value {
        Ok(serde_json::Value::Object(map)) => Some(map),
        Ok(other) => {
            tracing::warn!(kind = ?other, "session list _meta was not an object");
            None
        }
        Err(e) => {
            tracing::warn!(error = %e, "session list _meta failed to serialize");
            None
        }
    }
}
#[cfg(test)]
mod tests {
    
}
