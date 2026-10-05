//! Re-encode decoded attachments that exceed [`MAX_IMAGE_BYTES`],
//! [`MAX_ENCODE_PIXELS`], or [`MAX_ENCODE_SIDE_PX`] to fit the conversation
//! caps. The primary dimension limit is the v9 pixel-area budget
//! ([`MAX_ENCODE_PIXELS`]); [`MAX_ENCODE_SIDE_PX`] is a model-agnostic side
//! clamp. Compute is amortised via
//! [`NormalizeCache`](crate::session::normalize_cache).
/// Bounded ICO decode for load-time verification: real icons are far
/// smaller; bytes claiming more are kept un-verified rather than decoded
/// on the session-load path.
const MAX_LOAD_ICO_DECODE_PIXELS: u64 = 16_000_000;
/// Backend APIs reject images with either side < 8 px.
pub(crate) const MIN_VISION_SIDE_PX: u32 = 8;
/// Backend APIs also reject images with fewer than 512 total pixels
/// (`MIN_IMAGE_PIXELS`); e.g. a 16×16 icon is 256 px and draws a 400 that
/// poisons the conversation on every following turn.
pub(crate) const MIN_VISION_TOTAL_PX: u64 = 512;
/// Backend ceiling (`MAX_IMAGE_PIXELS`), header-checked server-side before
/// any resize. Send paths re-encode far below this; only legacy/foreign
/// history payloads can exceed it.
pub(crate) const MAX_VISION_TOTAL_PX: u64 = 178_956_970;
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
/// Why persisted-history image bytes would be rejected by the API, or
/// `None` when sendable. Cheap (format sniff + structural walk + header
/// dimension probe; pixel decode only for ICO, bounded) — used at session
/// load to strip payloads that draw a 400 on every subsequent turn,
/// leaving the session bricked. The reason is logged when the loader
/// strips an image — the strip is re-persisted (irreversible), so the
/// evidence must reach logs.
pub(crate) fn persisted_image_reject_reason(bytes: &[u8]) -> Option<String> {
    use image::ImageFormat as F;
    use pi_tools::util::image_validate as iv;
    let Ok(format) = image::guess_format(bytes) else {
        return Some(format!("unrecognized format ({} bytes)", bytes.len()));
    };
    match format {
        F::Ico => {
            let Ok((w, h, _)) = iv::validate_image_bytes_unrestricted(bytes, false) else {
                return Some("unreadable Ico header".to_owned());
            };
            if (w as u64) * (h as u64) > MAX_LOAD_ICO_DECODE_PIXELS {
                return None;
            }
            image::load_from_memory(bytes)
                .is_err()
                .then(|| format!("undecodable Ico ({} bytes)", bytes.len()))
        }
        F::Gif | F::Bmp | F::Tiff => Some(format!("API-rejected format {format:?}")),
        F::Jpeg | F::Png | F::WebP => {
            if !iv::format_structurally_complete(format, bytes) {
                return Some(format!(
                    "structurally incomplete {format:?} ({} bytes)",
                    bytes.len()
                ));
            }
            let Ok((w, h, _)) = iv::validate_image_bytes_unrestricted(bytes, false) else {
                return Some(format!("unreadable {format:?} header"));
            };
            let px = (w as u64) * (h as u64);
            if w < MIN_VISION_SIDE_PX || h < MIN_VISION_SIDE_PX || px < MIN_VISION_TOTAL_PX {
                return Some(format!("below dimension floor ({w}x{h})"));
            }
            (px > MAX_VISION_TOTAL_PX).then(|| format!("above pixel ceiling ({w}x{h})"))
        }
        _ => Some(format!("API-rejected format {format:?}")),
    }
}
#[cfg(test)]
#[path = "image_normalize_tests.rs"]
mod tests;
