//! Model fetching, resolution, and management.

use chrono::{DateTime, Duration as ChronoDuration, Utc};
use indexmap::IndexMap;

use crate::agent::config::{self, ModelEntry};
use crate::auth::{AuthManager, GrokAuth, GrokComConfig};
use crate::remote::{FetchModelsResult, fetch_models_blocking};

// ── Auth method for model fetching ──────────────────────────────────────────

/// Credential for `/v1/models` fetching.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ModelFetchAuth {
    Session,
    ApiKey,
    Deployment,
    CustomEndpoint,
}

impl ModelFetchAuth {
    /// custom_endpoint > session > deployment > API key.
    pub(crate) fn resolve(endpoints: &config::EndpointsConfig, has_cached_session: bool) -> Self {
        if endpoints.has_custom_endpoint() {
            Self::CustomEndpoint
        } else if has_cached_session {
            Self::Session
        } else if endpoints.deployment_key.is_some() {
            Self::Deployment
        } else if crate::agent::auth_method::has_pi_api_key_env() {
            Self::ApiKey
        } else {
            Self::Session
        }
    }

    fn cache_auth_method(&self) -> CacheAuthMethod {
        match self {
            Self::CustomEndpoint | Self::ApiKey => CacheAuthMethod::ApiKey,
            Self::Session => CacheAuthMethod::Session,
            Self::Deployment => CacheAuthMethod::Deployment,
        }
    }
}

#[derive(serde::Serialize, serde::Deserialize, PartialEq, Eq, Clone, Debug)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CacheAuthMethod {
    Session,
    ApiKey,
    Deployment,
}

mod cache;
mod fetch;

pub(crate) use cache::*;
pub use fetch::{
    EarlyPrefetchHandle, EarlyPrefetchResult, start_early_prefetch,
    start_early_prefetch_settings_only, start_early_prefetch_with_auth,
};

#[cfg(test)]
mod tests;
