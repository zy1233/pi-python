use super::*;
fn make_test_png(width: u32, height: u32) -> Vec<u8> {
    use image::{ImageBuffer, Rgba};
    let img: ImageBuffer<Rgba<u8>, Vec<u8>> =
        ImageBuffer::from_pixel(width, height, Rgba([128, 64, 32, 255]));
    let mut buf = Vec::new();
    img.write_to(&mut std::io::Cursor::new(&mut buf), image::ImageFormat::Png)
        .unwrap();
    buf
}
#[test]
fn persisted_image_reject_reason_verdicts() {
    let reason = persisted_image_reject_reason;
    assert_eq!(reason(&make_test_png(32, 32)), None);
    let mut jpeg = {
        use image::codecs::jpeg::JpegEncoder;
        use image::{ImageBuffer, Rgb};
        let img: ImageBuffer<Rgb<u8>, Vec<u8>> =
            ImageBuffer::from_fn(64, 64, |x, y| Rgb([(x ^ y) as u8, x as u8, y as u8]));
        let mut buf = Vec::new();
        JpegEncoder::new_with_quality(&mut buf, 85)
            .encode_image(&image::DynamicImage::ImageRgb8(img))
            .unwrap();
        buf
    };
    assert_eq!(reason(&jpeg), None);
    jpeg.truncate(jpeg.len() / 2);
    assert!(
        reason(&jpeg).is_some_and(|r| r.contains("structurally incomplete")),
        "truncated JPEG must be rejected"
    );
    assert!(reason(&make_test_png(16, 16)).is_some_and(|r| r.contains("dimension floor")),);
    let mut gif = Vec::new();
    image::DynamicImage::ImageRgba8(image::ImageBuffer::from_pixel(
        64,
        64,
        image::Rgba([1u8, 2, 3, 255]),
    ))
    .write_to(&mut std::io::Cursor::new(&mut gif), image::ImageFormat::Gif)
    .unwrap();
    assert!(reason(&gif).is_some_and(|r| r.contains("format")));
    let ico = pi_test_utils::image::ico_with_png_frame(&make_test_png(16, 16), 16, 16);
    assert_eq!(reason(&ico), None);
    let mut cut_ico = ico.clone();
    cut_ico.truncate(cut_ico.len() - 8);
    assert!(
        reason(&cut_ico).is_some_and(|r| r.contains("Ico")),
        "truncated ICO must be rejected"
    );
    let mut garbage_ico = vec![0x00, 0x00, 0x01, 0x00];
    garbage_ico.extend_from_slice(&[0xAB; 64]);
    assert!(reason(&garbage_ico).is_some_and(|r| r.contains("Ico")));
    assert!(reason(b"not an image").is_some());
}
/// The API also 400s images whose header dims exceed its
/// `MAX_IMAGE_PIXELS` ceiling; a kept one would brick the session the
/// same way as a below-floor image. (SOF dims are patched because
/// encoding a real >178 Mpx fixture is infeasible.)
#[test]
fn persisted_image_reject_reason_pixel_ceiling() {
    use image::codecs::jpeg::JpegEncoder;
    use image::{DynamicImage, ImageBuffer, Rgb};
    let img: ImageBuffer<Rgb<u8>, Vec<u8>> =
        ImageBuffer::from_fn(64, 64, |x, y| Rgb([(x ^ y) as u8, x as u8, y as u8]));
    let mut jpeg = Vec::new();
    JpegEncoder::new_with_quality(&mut jpeg, 85)
        .encode_image(&DynamicImage::ImageRgb8(img))
        .unwrap();
    let sof = jpeg
        .windows(2)
        .position(|w| w == [0xFF, 0xC0])
        .expect("baseline SOF0 present");
    jpeg[sof + 5..sof + 9].copy_from_slice(&[0x40, 0x00, 0x40, 0x00]);
    assert!(
        persisted_image_reject_reason(&jpeg).is_some_and(|r| r.contains("above pixel ceiling")),
    );
}
