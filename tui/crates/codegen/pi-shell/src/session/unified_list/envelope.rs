use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SessionKind {
    #[default]
    Build,
    Chat,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum FacetValue {
    One(serde_json::Value),
    Many(Vec<serde_json::Value>),
}

pub type FacetMap = BTreeMap<String, FacetValue>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionMetaEnvelope {
    pub kind: SessionKind,
    #[serde(default, skip_serializing_if = "FacetMap::is_empty")]
    pub facets: FacetMap,
}
