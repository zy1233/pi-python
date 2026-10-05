//! Bundle status state and response types.
//!
//! Pager-side cache of what `pi-shell` reports from
//! `legacy ext RPC`. The shell now performs the actual bundle download in
//! the background post-auth; the pager only reads the resulting on-disk
//! catalog so it can populate the welcome-screen subagent pane.

use serde::Deserialize;

/// Pager-local snapshot of bundle availability on disk.
///
/// Populated from `legacy ext RPC` ACP responses.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct BundleState {
    pub has_cache: bool,
    pub version: String,
    pub personas: Vec<String>,
    pub roles: Vec<String>,
    pub agents: Vec<String>,
    pub skills: Vec<String>,
    pub persona_details: Vec<PersonaDetail>,
    pub role_details: Vec<RoleDetail>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PersonaDetail {
    pub name: String,
    pub description: Option<String>,
    pub has_inputs: bool,
    pub has_outputs: bool,
    /// Absolute path when the persona was loaded from disk (user/project).
    #[serde(default)]
    pub source_path: Option<String>,
    /// `"user"` or `"project"` for local personas; omitted for bundled catalog entries.
    #[serde(default)]
    pub scope_label: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RoleDetail {
    pub name: String,
    pub description: String,
}

#[cfg(test)]
mod tests {

}
