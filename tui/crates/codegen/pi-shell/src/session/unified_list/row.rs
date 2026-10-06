use std::path::PathBuf;

use agent_client_protocol as acp;
use serde::Serialize;

use super::envelope::SessionMetaEnvelope;
#[derive(Debug, Clone, Serialize)]
pub(crate) struct RowMeta {
    #[serde(rename = "x.ai/session")]
    pub session: SessionMetaEnvelope,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SessionInfo {
    pub session_id: String,
    pub cwd: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    #[serde(rename = "_meta")]
    pub meta: RowMeta,
}

/// The ACP schema types `cwd` as an absolute path, so a row without one has no
/// representation there.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct CwdNotAbsolute;

impl TryFrom<SessionInfo> for acp::SessionInfo {
    type Error = CwdNotAbsolute;

    fn try_from(info: SessionInfo) -> Result<Self, Self::Error> {
        if !PathBuf::from(&info.cwd).is_absolute() {
            return Err(CwdNotAbsolute);
        }
        let SessionInfo {
            session_id,
            cwd,
            title,
            updated_at,
            meta,
        } = info;
        let meta = super::to_meta(serde_json::to_value(&meta));
        Ok(
            acp::SessionInfo::new(acp::SessionId::new(session_id), PathBuf::from(cwd))
                .title(title)
                .updated_at(updated_at)
                .meta(meta),
        )
    }
}

#[cfg(test)]
mod tests {}
