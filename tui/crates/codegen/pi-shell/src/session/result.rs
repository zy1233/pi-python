use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ExtMethodError {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

/// Extension method result: `{ result: T | null, error?: string | ExtMethodError }`
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExtMethodResult<T: Serialize> {
    pub result: Option<T>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<serde_json::Value>,
}

impl<T: Serialize> From<crate::terminal::TerminalExtError> for ExtMethodResult<T> {
    fn from(err: crate::terminal::TerminalExtError) -> Self {
        let data = err
            .terminal_id()
            .map(|id| serde_json::json!({ "terminalId": id }));
        Self {
            result: None,
            error: serde_json::to_value(ExtMethodError {
                code: err.code().to_string(),
                message: err.to_string(),
                data,
            })
            .ok(),
        }
    }
}

impl<T: Serialize> ExtMethodResult<T> {
    pub fn success(result: T) -> Self {
        Self {
            result: Some(result),
            error: None,
        }
    }

    pub fn failure(error: impl std::fmt::Display) -> Self {
        Self {
            result: None,
            error: Some(serde_json::Value::String(error.to_string())),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Empty {}

#[cfg(test)]
mod tests {}
