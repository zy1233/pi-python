/// Apply auth headers to outbound visibility requests.
/// Implemented by the embedding application (for example
/// `StaticAuthCredentialProvider`) to keep credential construction out of the
/// data-collector code.
pub trait HttpAuth: Send + Sync {
    fn apply(&self, builder: reqwest::RequestBuilder, base_url: &str) -> reqwest::RequestBuilder;
}
