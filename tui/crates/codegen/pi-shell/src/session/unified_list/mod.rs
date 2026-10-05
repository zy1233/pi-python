mod envelope;
mod row;
use agent_client_protocol as acp;
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
