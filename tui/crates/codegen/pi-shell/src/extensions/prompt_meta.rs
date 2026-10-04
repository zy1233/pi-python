use serde::{Deserialize, Serialize};

/// Typed metadata for a prompt `TextContent._meta` field.
///
/// Replaces ad-hoc `serde_json::json!()` construction on the sender side
/// and manual `.get()` parsing on the receiver side.
///
/// Wire-compatible with the existing format: `{"bash_command": "ls -la"}`
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptBlockMeta {
    /// Direct bash command to execute (bypasses agent loop).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bash_command: Option<String>,
}

impl PromptBlockMeta {
    /// Create meta for a direct bash command.
    pub fn bash(command: impl Into<String>) -> Self {
        Self {
            bash_command: Some(command.into()),
        }
    }

}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bash_roundtrip_serde() {
        let meta = PromptBlockMeta::bash("ls -la");
        let json = serde_json::to_value(&meta).unwrap();
        let parsed: PromptBlockMeta = serde_json::from_value(json).unwrap();
        assert_eq!(parsed.bash_command, Some("ls -la".to_string()));
    }

    #[test]
    fn skip_serializing_none() {
        let meta = PromptBlockMeta { bash_command: None };
        let json = serde_json::to_value(&meta).unwrap();
        assert!(!json.as_object().unwrap().contains_key("bash_command"));
    }
}
