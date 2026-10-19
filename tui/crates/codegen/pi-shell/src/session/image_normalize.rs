//! What the pager is told about an image that was re-encoded smaller before it was sent.

#[derive(Debug, Clone, Copy)]
pub(crate) struct ImageCompressionInfo {
    pub index: usize,
    pub original_bytes: usize,
    pub compressed_bytes: usize,
    pub original_width: u32,
    pub original_height: u32,
    pub compressed_width: u32,
    pub compressed_height: u32,
}
