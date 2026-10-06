//! Remote storage client for the backend.

pub(crate) mod client;

pub use client::{
    BackendError, FetchModelsResult, FetchedBundle, SettingsFetch, fetch_bundle,
    fetch_login_device_flow, fetch_settings_blocking, fetch_subagent_bundle, share_url,
};
pub(crate) use client::{fetch_models_blocking, models_list_url};
