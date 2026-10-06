#![cfg_attr(rustfmt, rustfmt::skip)]
use super::*;
use crate::session::info::Info;
use agent_client_protocol as acp;
use tempfile::TempDir;
fn create_test_info() -> Info {
    Info {
        id: acp::SessionId::new("test-session-123"),
        cwd: "/test/workspace".to_string(),
    }
}
#[tokio::test]
async fn write_compaction_segment_numbers_and_indexes_resume_safely() {
    use crate::extensions::notification::CompactionSegmentFile;
    use pi_sampling_types::ConversationItem;
    let temp_dir = TempDir::new().unwrap();
    let info = create_test_info();
    let seg = |summary: &str| CompactionSegmentFile {
        items: vec![ConversationItem::user("a"), ConversationItem::user("b")],
        summary: summary.to_string(),
        detail: pi_chat_state::CompactionDetail::Verbose,
        timestamp: "2026-01-01T00:00:00Z".to_string(),
    };
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    adapter.write_compaction_segment(&info, &seg("first")).await.unwrap();
    adapter.write_compaction_segment(&info, &seg("second")).await.unwrap();
    let base = adapter
        .session_dir(&info)
        .join(pi_compaction_transcript::COMPACTION_DIR);
    let read = |p: &str| std::fs::read_to_string(base.join(p)).unwrap();
    assert!(read("segment_000.md").contains("# HISTORICAL -- DO NOT EDIT"));
    assert!(read("segment_001.md").contains("second"));
    let index = read("INDEX.md");
    assert_eq!(
            index.matches("# Compaction Segment Index").count(),
            1,
            "title + header written exactly once"
        );
    assert!(index.contains("| 000 | segment_000.md | 2 |"));
    assert!(index.contains("| 001 | segment_001.md | 2 |"));
    let resumed = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    resumed.write_compaction_segment(&info, &seg("third")).await.unwrap();
    assert!(read("segment_000.md").contains("first"));
    assert!(base.join("segment_002.md").exists());
    let index = read("INDEX.md");
    assert_eq!(index.matches("# Compaction Segment Index").count(), 1);
    assert_eq!(index.lines().filter(|l| l.contains("segment_")).count(), 3);
}
#[tokio::test]
async fn test_load_prompts_only_nonexistent_session() {
    let temp_dir = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    let info = Info {
        id: acp::SessionId::new("nonexistent"),
        cwd: "/nonexistent".to_string(),
    };
    let prompts = adapter.load_prompts_only(&info).await.unwrap();
    assert!(prompts.is_empty());
}
#[test]
fn scan_session_dirs_returns_empty_when_no_sessions_dir() {
    let tmp = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(tmp.path().to_path_buf());
    assert!(adapter.scan_session_dirs(None).unwrap().is_empty());
}
#[test]
fn scan_session_dirs_skips_non_directory_entries() {
    let tmp = TempDir::new().unwrap();
    let cwd = crate::util::grok_home::encode_cwd_dirname("/project");
    let cwd_dir = tmp.path().join("sessions").join(&cwd);
    std::fs::create_dir_all(&cwd_dir).unwrap();
    std::fs::write(cwd_dir.join("stray-file.txt"), b"oops").unwrap();
    std::fs::create_dir(cwd_dir.join("real-session")).unwrap();
    std::fs::write(cwd_dir.join("real-session/summary.json"), b"{}").unwrap();
    let adapter = JsonlStorageAdapter::with_root(tmp.path().to_path_buf());
    let dirs = adapter.scan_session_dirs(None).unwrap();
    assert_eq!(dirs.len(), 1);
    assert!(dirs[0].ends_with("real-session"));
}
#[tokio::test]
async fn list_sessions_recent_empty_dir() {
    let tmp = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(tmp.path().to_path_buf());
    let recent = adapter.list_sessions_recent(10).await.unwrap();
    assert!(recent.is_empty());
}
fn test_png_bytes() -> Vec<u8> {
    use image::{ImageBuffer, Rgba};
    let img: ImageBuffer<Rgba<u8>, Vec<u8>> = ImageBuffer::from_pixel(
        32,
        32,
        Rgba([10, 20, 30, 255]),
    );
    let mut buf = Vec::new();
    img.write_to(&mut std::io::Cursor::new(&mut buf), image::ImageFormat::Png).unwrap();
    buf
}
fn test_jpeg_bytes() -> Vec<u8> {
    use image::codecs::jpeg::JpegEncoder;
    use image::{DynamicImage, ImageBuffer, Rgb};
    let img: ImageBuffer<Rgb<u8>, Vec<u8>> = ImageBuffer::from_fn(
        64,
        64,
        |x, y| Rgb([(x ^ y) as u8, x as u8, y as u8]),
    );
    let mut buf = Vec::new();
    JpegEncoder::new_with_quality(&mut buf, 85)
        .encode_image(&DynamicImage::ImageRgb8(img))
        .unwrap();
    buf
}
fn image_data_uri(mime: &str, bytes: &[u8]) -> String {
    use base64::Engine as _;
    format!(
            "data:{mime};base64,{}",
            base64::engine::general_purpose::STANDARD.encode(bytes)
        )
}
#[test]
fn strip_invalid_images_valid_data_uri_passes() {
    let url = image_data_uri("image/png", &test_png_bytes());
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Text {
                text: "look".into(),
            },
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 0);
    assert!(matches!(&items[0], ConversationItem::User(u) if u.content.len() == 2));
    assert!(matches!(&items[0], ConversationItem::User(u)
            if matches!(&u.content[1], ContentPart::Image { .. })));
}
#[test]
fn strip_invalid_images_corrupt_base64_stripped() {
    let url = "data:image/png;base64,!!!not-valid-base64!!!".to_string();
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Text {
                text: "look".into(),
            },
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
    if let ConversationItem::User(u) = &items[0] {
        assert_eq!(u.content.len(), 2);
        assert!(
                matches!(&u.content[1], ContentPart::Text { text } if text.contains("invalid data"))
            );
    } else {
        panic!("expected User");
    }
}
#[test]
fn strip_invalid_images_malformed_data_uri_no_base64_marker() {
    let url = "data:image/png,rawbytes".to_string();
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
    assert!(matches!(
            &items[0],
            ConversationItem::User(u) if matches!(&u.content[0], ContentPart::Text { .. })
        ));
}
#[test]
fn strip_invalid_images_malformed_data_uri_no_comma() {
    let url = "data:image/png;base64".to_string();
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
}
#[test]
fn strip_invalid_images_http_url_untouched() {
    let url = "https://example.com/photo.jpg".to_string();
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image {
                url: url.clone().into(),
            },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 0);
    assert!(matches!(
            &items[0],
            ConversationItem::User(u) if matches!(&u.content[0], ContentPart::Image { url: u } if u.as_ref() == "https://example.com/photo.jpg")
        ));
}
#[test]
fn strip_invalid_images_oversized_stripped() {
    use base64::Engine as _;
    let huge = vec![0u8; MAX_LOADED_IMAGE_BYTES + 1];
    let payload = base64::engine::general_purpose::STANDARD.encode(&huge);
    let url = format!("data:image/jpeg;base64,{payload}");
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
}
#[test]
fn strip_invalid_images_mixed_valid_and_invalid() {
    let valid_url = image_data_uri("image/png", &test_png_bytes());
    let invalid_url = "data:image/png;base64,!!!corrupt!!!".to_string();
    let http_url = "https://example.com/img.png".to_string();
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Text {
                text: "check these".into(),
            },
            ContentPart::Image {
                url: valid_url.clone().into(),
            },
            ContentPart::Image {
                url: invalid_url.into(),
            },
            ContentPart::Image {
                url: http_url.into(),
            },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
    if let ConversationItem::User(u) = &items[0] {
        assert_eq!(u.content.len(), 4);
        assert!(
                matches!(&u.content[0], ContentPart::Text { text } if text.as_ref() == "check these")
            );
        assert!(
                matches!(&u.content[1], ContentPart::Image { url } if url.as_ref() == valid_url.as_str())
            );
        assert!(
                matches!(&u.content[2], ContentPart::Text { text } if text.contains("invalid data"))
            );
        assert!(
                matches!(&u.content[3], ContentPart::Image { url } if url.as_ref() == "https://example.com/img.png")
            );
    } else {
        panic!("expected User");
    }
}
#[test]
fn strip_invalid_images_non_user_items_untouched() {
    let mut items = vec![
            ConversationItem::system("system prompt"),
            ConversationItem::assistant("response"),
            ConversationItem::tool_result("call_1", "result"),
        ];
    assert_eq!(strip_invalid_images(&mut items), 0);
    assert_eq!(items.len(), 3);
}
/// The read_file inline-attach shape: the poisoned
/// image lives in `ToolResultItem.images`, not in a user part. Invalid
/// entries are removed; valid ones survive.
#[test]
fn strip_invalid_images_heals_tool_result_images() {
    let mut png16 = Vec::new();
    image::ImageBuffer::from_pixel(16, 16, image::Rgba([9u8, 9, 9, 255]))
        .write_to(&mut std::io::Cursor::new(&mut png16), image::ImageFormat::Png)
        .unwrap();
    let bad_url = image_data_uri("image/png", &png16);
    let good_url = image_data_uri("image/png", &test_png_bytes());
    let mut items = vec![ConversationItem::tool_result_with_images(
            "call_1".to_string(),
            "Read image file: icon.png".to_string(),
            vec![
                ContentPart::Image {
                    url: good_url.clone().into(),
                },
                ContentPart::Image {
                    url: bad_url.into(),
                },
            ],
        )];
    assert_eq!(strip_invalid_images(&mut items), 1);
    let ConversationItem::ToolResult(t) = &items[0] else {
        panic!("expected ToolResult");
    };
    assert_eq!(t.images.len(), 1, "only the invalid image is removed");
    assert!(
            matches!(&t.images[0], ContentPart::Image { url } if url.as_ref() == good_url.as_str())
        );
}
#[test]
fn strip_invalid_images_empty_conversation() {
    let mut items: Vec<ConversationItem> = vec![];
    assert_eq!(strip_invalid_images(&mut items), 0);
}
#[test]
fn strip_invalid_images_empty_payload_stripped() {
    let url = "data:image/png;base64,".to_string();
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
}
#[test]
fn strip_invalid_images_case_insensitive_base64_marker() {
    use base64::Engine as _;
    let payload = base64::engine::general_purpose::STANDARD.encode(test_png_bytes());
    let url = format!("data:image/png;Base64,{payload}");
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 0);
    assert!(matches!(
            &items[0],
            ConversationItem::User(u) if matches!(&u.content[0], ContentPart::Image { .. })
        ));
}
/// Regression: a truncated JPEG persisted into history must be
/// stripped at load so resuming recovers.
#[test]
fn strip_invalid_images_truncated_jpeg_stripped() {
    let mut jpeg = test_jpeg_bytes();
    jpeg.truncate(jpeg.len() / 2);
    let url = image_data_uri("image/jpeg", &jpeg);
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Text {
                text: "[Image extracted from tool result above]".into(),
            },
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
    assert!(matches!(
            &items[0],
            ConversationItem::User(u)
                if matches!(&u.content[1], ContentPart::Text { text } if text.contains("invalid data"))
        ));
}
#[test]
fn strip_invalid_images_truncated_png_stripped() {
    let mut png = test_png_bytes();
    png.truncate(png.len() / 2);
    let url = image_data_uri("image/png", &png);
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
}
#[test]
fn strip_invalid_images_complete_jpeg_kept() {
    let url = image_data_uri("image/jpeg", &test_jpeg_bytes());
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 0);
}
/// Regression: a below-floor image persisted into history must be
/// stripped at load.
#[test]
fn strip_invalid_images_below_pixel_floor_stripped() {
    use image::{ImageBuffer, Rgba};
    let img: ImageBuffer<Rgba<u8>, Vec<u8>> = ImageBuffer::from_pixel(
        16,
        16,
        Rgba([10, 20, 30, 255]),
    );
    let mut png = Vec::new();
    img.write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png).unwrap();
    let url = image_data_uri("image/png", &png);
    let mut items = vec![ConversationItem::user_with_parts(vec![
            ContentPart::Image { url: url.into() },
        ])];
    assert_eq!(strip_invalid_images(&mut items), 1);
}
/// Write a chat_history.jsonl with the given lines into a fresh
/// session dir, then call `read_chat_history_sync` and return the
/// resulting `ConversationItem`s. Exercises the real on-read upgrade
/// path end-to-end (loader + serde + pi_sampling_types::
/// upgrade_legacy_reasoning).
fn load_lines(lines: &[&str]) -> Vec<ConversationItem> {
    let temp_dir = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    let info = create_test_info();
    let chat_path = adapter.chat_file(&info);
    std::fs::create_dir_all(chat_path.parent().unwrap()).unwrap();
    std::fs::write(&chat_path, lines.join("\n") + "\n").unwrap();
    adapter.read_chat_history_sync(chat_path, CHAT_FORMAT_VERSION).unwrap()
}
/// Real-shape legacy fixture from a web-search session.
/// The assistant carries `reasoning: { text, encrypted, id }` inline —
/// the legacy grok-build / Opus / chat-completions shape.
/// BackendToolCall sits as its own sibling line (it was already a
/// sibling variant in the legacy shape).
#[test]
fn read_chat_history_upgrades_legacy_singular_reasoning_to_sibling() {
    let items = load_lines(
        &[
            r#"{"type":"system","content":"You are helpful."}"#,
            r#"{"type":"user","content":[{"type":"text","text":"cats and dogs"}]}"#,
            r#"{"type":"backend_tool_call","kind":{"tool_type":"web_search","id":"ws_legacy_1","status":"completed","action":{"type":"search","query":"cats and dogs","sources":[]}}}"#,
            r#"{"type":"assistant","content":"results...","reasoning":{"text":"the results are about cats","encrypted":"enc-blob","id":"rs_legacy"},"model_id":"grok-build"}"#,
        ],
    );
    assert_eq!(
            items.len(),
            5,
            "system + user + backend_tool_call + reconstructed reasoning + assistant"
        );
    match &items[3] {
        ConversationItem::Reasoning(r) => {
            assert_eq!(r.id, "rs_legacy");
            assert_eq!(r.encrypted_content.as_deref(), Some("enc-blob"));
            let pi_sampling_types::rs::SummaryPart::SummaryText(s) = &r.summary[0];
            assert_eq!(s.text, "the results are about cats");
        }
        other => panic!("expected reconstructed Reasoning at index 3, got {other:?}"),
    }
    assert!(matches!(items[4], ConversationItem::Assistant(_)));
}
/// The `raw_output`-era shape: `raw_output: Vec<OutputItem>` on the assistant.
/// N parallel `tco_*` reasoning blobs survive as N sibling items, in
/// emission order, interleaved with backend tool calls.
#[test]
fn read_chat_history_upgrades_raw_output_parallel_tco_reasoning() {
    let lines = [
        r#"{"type":"system","content":"sys"}"#,
        r#"{"type":"user","content":[{"type":"text","text":"q"}]}"#,
        r#"{"type":"backend_tool_call","kind":{"tool_type":"web_search","id":"ws_1","status":"completed","action":{"type":"search","query":"q1","sources":[]}}}"#,
        r#"{"type":"backend_tool_call","kind":{"tool_type":"web_search","id":"ws_2","status":"completed","action":{"type":"search","query":"q2","sources":[]}}}"#,
        r#"{"type":"assistant","content":"answer","tool_calls":[],"raw_output":[{"type":"reasoning","id":"tco_1","summary":[],"encrypted_content":"e1"},{"type":"web_search_call","id":"ws_1","status":"completed","action":{"type":"search","query":"q1","sources":[]}},{"type":"reasoning","id":"tco_2","summary":[],"encrypted_content":"e2"},{"type":"web_search_call","id":"ws_2","status":"completed","action":{"type":"search","query":"q2","sources":[]}},{"type":"reasoning","id":"rs_main","summary":[{"type":"summary_text","text":"final"}]},{"type":"message","id":"m1","status":"completed","role":"assistant","content":[{"type":"output_text","text":"answer","annotations":[]}]}]}"#,
    ];
    let items = load_lines(&lines);
    let kinds: Vec<&'static str> = items
        .iter()
        .map(|i| match i {
            ConversationItem::System(_) => "system",
            ConversationItem::User(_) => "user",
            ConversationItem::Assistant(_) => "assistant",
            ConversationItem::ToolResult(_) => "tool_result",
            ConversationItem::BackendToolCall(_) => "backend_tool_call",
            ConversationItem::Reasoning(_) => "reasoning",
        })
        .collect();
    assert_eq!(
            kinds,
            vec![
                "system",
                "user",
                "backend_tool_call",
                "backend_tool_call",
                "reasoning",
                "reasoning",
                "reasoning",
                "assistant",
            ],
        );
    let reasoning_ids: Vec<&str> = items
        .iter()
        .filter_map(|i| match i {
            ConversationItem::Reasoning(r) => Some(r.id.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(reasoning_ids, vec!["tco_1", "tco_2", "rs_main"]);
}
/// Hybrid file — legacy-shape turns at the front of the file, new-shape
/// turns appended at the back (the realistic shape when a user loads an
/// old session with a new binary and takes another turn). Verifies:
///
/// 1. The legacy turn's `reasoning` field is reconstructed as a sibling
///    *before* the legacy assistant.
/// 2. The post-PR sibling Reasoning row passes through unchanged and
///    lands before the post-PR assistant (no double-emission).
/// 3. `sibling_btc_ids_seen` correctly tracks ids across the boundary:
///    a BackendToolCall that appears as a sibling row in the post-PR
///    section does not get re-emitted by a (hypothetical) later legacy
///    assistant's raw_output that lists the same id.
/// 4. Final item order is a uniform sibling-shape `Vec<ConversationItem>`
///    that downstream code can replay without knowing about the seam.
#[test]
fn read_chat_history_handles_hybrid_legacy_and_post_pr_lines() {
    let items = load_lines(
        &[
            r#"{"type":"system","content":"sys"}"#,
            r#"{"type":"user","content":[{"type":"text","text":"q1"}]}"#,
            r#"{"type":"backend_tool_call","kind":{"tool_type":"web_search","id":"ws_legacy_1","status":"completed","action":{"type":"search","query":"q1","sources":[]}}}"#,
            r#"{"type":"assistant","content":"a1","reasoning":{"text":"legacy thinking","encrypted":"enc","id":"rs_legacy"},"model_id":"grok-build"}"#,
            r#"{"type":"user","content":[{"type":"text","text":"q2"}]}"#,
            r#"{"type":"reasoning","id":"rs_postpr","summary":[{"type":"summary_text","text":"new thinking"}]}"#,
            r#"{"type":"backend_tool_call","kind":{"tool_type":"web_search","id":"ws_postpr","status":"completed","action":{"type":"search","query":"q2","sources":[]}}}"#,
            r#"{"type":"assistant","content":"a2","model_id":"grok-build"}"#,
        ],
    );
    let kinds: Vec<&'static str> = items
        .iter()
        .map(|i| match i {
            ConversationItem::System(_) => "system",
            ConversationItem::User(_) => "user",
            ConversationItem::Assistant(_) => "assistant",
            ConversationItem::ToolResult(_) => "tool_result",
            ConversationItem::BackendToolCall(_) => "backend_tool_call",
            ConversationItem::Reasoning(_) => "reasoning",
        })
        .collect();
    assert_eq!(
            kinds,
            vec![
                // Turn 1 (legacy lifted)
                "system",
                "user",
                "backend_tool_call", // ws_legacy_1 (passthrough sibling row)
                "reasoning",         // reconstructed from assistant.reasoning
                "assistant",         // legacy assistant with legacy fields stripped
                // Turn 2 (post-PR passthrough)
                "user",
                "reasoning",         // passthrough sibling
                "backend_tool_call", // passthrough sibling
                "assistant",
            ],
            "hybrid file produces uniform sibling-shape output with no \
             cross-boundary corruption"
        );
    let reasoning_ids: Vec<&str> = items
        .iter()
        .filter_map(|i| match i {
            ConversationItem::Reasoning(r) => Some(r.id.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(reasoning_ids, vec!["rs_legacy", "rs_postpr"]);
    let btc_ids: Vec<&str> = items
        .iter()
        .filter_map(|i| match i {
            ConversationItem::BackendToolCall(b) => Some(b.id()),
            _ => None,
        })
        .collect();
    assert_eq!(btc_ids, vec!["ws_legacy_1", "ws_postpr"]);
    let ConversationItem::Assistant(legacy_assistant) = &items[4] else {
        panic!("expected legacy assistant at index 4");
    };
    assert_eq!(legacy_assistant.content.as_ref(), "a1");
    assert_eq!(
            legacy_assistant.model_id.as_deref(),
            Some("grok-build"),
            "model_id preserved across the upgrade"
        );
    let ConversationItem::Reasoning(reconstructed) = &items[3] else {
        panic!("expected reconstructed Reasoning at index 3");
    };
    assert_eq!(reconstructed.id, "rs_legacy");
    assert_eq!(reconstructed.encrypted_content.as_deref(), Some("enc"));
    let pi_sampling_types::rs::SummaryPart::SummaryText(s) = &reconstructed
        .summary[0];
    assert_eq!(s.text, "legacy thinking");
}
/// Already-new-shape sessions are unchanged by the loader.
/// Idempotent: re-loading the file produces the same items.
#[test]
fn read_chat_history_is_idempotent_on_post_pr_sessions() {
    let items = load_lines(
        &[
            r#"{"type":"system","content":"sys"}"#,
            r#"{"type":"user","content":[{"type":"text","text":"q"}]}"#,
            r#"{"type":"reasoning","id":"rs_x","summary":[{"type":"summary_text","text":"thought"}]}"#,
            r#"{"type":"assistant","content":"a","model_id":"grok-build"}"#,
        ],
    );
    let kinds: Vec<&'static str> = items
        .iter()
        .map(|i| match i {
            ConversationItem::System(_) => "system",
            ConversationItem::User(_) => "user",
            ConversationItem::Assistant(_) => "assistant",
            ConversationItem::ToolResult(_) => "tool_result",
            ConversationItem::BackendToolCall(_) => "backend_tool_call",
            ConversationItem::Reasoning(_) => "reasoning",
        })
        .collect();
    assert_eq!(kinds, vec!["system", "user", "reasoning", "assistant"]);
}
/// Set up a session dir with a raw `chat_history.jsonl` and return
/// (adapter, chat path, loaded items).
fn load_raw_chat(
    temp_dir: &TempDir,
    raw: &[u8],
) -> (JsonlStorageAdapter, PathBuf, Vec<ConversationItem>) {
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    let info = create_test_info();
    let chat_path = adapter.chat_file(&info);
    std::fs::create_dir_all(chat_path.parent().unwrap()).unwrap();
    std::fs::write(&chat_path, raw).unwrap();
    let items = adapter
        .read_chat_history_sync(chat_path.clone(), CHAT_FORMAT_VERSION)
        .unwrap();
    (adapter, chat_path, items)
}
fn user_text(items: &[ConversationItem]) -> Vec<String> {
    items
        .iter()
        .filter_map(|i| match i {
            ConversationItem::User(u) => {
                u.content
                    .iter()
                    .find_map(|p| match p {
                        ContentPart::Text { text } => Some(text.to_string()),
                        _ => None,
                    })
            }
            _ => None,
        })
        .collect()
}
/// A record torn mid-object (crash / ENOSPC mid-append) is skipped;
/// every other record loads, and the damaged original is quarantined
/// as `chat_history.jsonl.corrupt`.
#[test]
fn read_chat_history_skips_torn_line_and_quarantines_original() {
    let good_1 = r#"{"type":"user","content":[{"type":"text","text":"first"}]}"#;
    let torn = r#"{"type":"assistant","content":"partial answer that got cut off mid-wr"#;
    let good_2 = r#"{"type":"user","content":[{"type":"text","text":"second"}]}"#;
    let raw = format!("{good_1}\n{torn}\n{good_2}\n");
    let temp_dir = TempDir::new().unwrap();
    let (_, chat_path, items) = load_raw_chat(&temp_dir, raw.as_bytes());
    assert_eq!(
            user_text(&items),
            vec!["first", "second"],
            "records around the torn line must survive"
        );
    assert_eq!(items.len(), 2, "the torn record itself is dropped");
    let quarantine = chat_path.with_extension("jsonl.corrupt");
    assert_eq!(
            std::fs::read_to_string(&quarantine).unwrap(),
            raw,
            "original file must be preserved byte-for-byte for recovery"
        );
}
/// An image strip is destructive (re-persisted on spawn) and its
/// verdicts are client-side heuristics — so the pre-strip original must
/// be quarantined exactly like a torn-line load, keeping a false drop
/// recoverable.
#[test]
fn read_chat_history_quarantines_original_on_image_strip() {
    use base64::Engine as _;
    use image::{ImageBuffer, Rgba};
    let img: ImageBuffer<Rgba<u8>, Vec<u8>> = ImageBuffer::from_pixel(
        16,
        16,
        Rgba([9u8, 9, 9, 255]),
    );
    let mut png = Vec::new();
    img.write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png).unwrap();
    let url = format!(
            "data:image/png;base64,{}",
            base64::engine::general_purpose::STANDARD.encode(&png)
        );
    let line = format!(r#"{{"type":"user","content":[{{"type":"image","url":"{url}"}}]}}"#);
    let raw = format!("{line}\n");
    let temp_dir = TempDir::new().unwrap();
    let (_, chat_path, items) = load_raw_chat(&temp_dir, raw.as_bytes());
    assert!(matches!(
            &items[0],
            ConversationItem::User(u)
                if matches!(&u.content[0], ContentPart::Text { text } if text.contains("invalid data"))
        ));
    let quarantine = chat_path.with_extension("jsonl.corrupt");
    assert_eq!(
            std::fs::read_to_string(&quarantine).unwrap(),
            raw,
            "pre-strip original must be preserved for recovery"
        );
}
/// The pre-strip backup copies the live file once; the first copy wins
/// so a later strip cannot overwrite the earliest (fullest) backup.
#[tokio::test]
async fn backup_chat_history_before_strip_copies_once() {
    let original = r#"{"type":"user","content":[{"type":"text","text":"with image"}]}"#;
    let temp_dir = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    let info = create_test_info();
    let chat_path = adapter.chat_file(&info);
    std::fs::create_dir_all(chat_path.parent().unwrap()).unwrap();
    std::fs::write(&chat_path, original).unwrap();
    adapter.backup_chat_history_before_strip(&info).await.unwrap();
    let backup = chat_path.with_extension("jsonl.pre-strip");
    assert_eq!(
            std::fs::read_to_string(&backup).unwrap(),
            original,
            "backup must capture the pre-strip file"
        );
    std::fs::write(&chat_path, "stripped").unwrap();
    adapter.backup_chat_history_before_strip(&info).await.unwrap();
    assert_eq!(
            std::fs::read_to_string(&backup).unwrap(),
            original,
            "first backup wins; a later strip must not overwrite it"
        );
}
/// A missing chat file (fresh session, strip before first write) must
/// not error or create a backup.
#[tokio::test]
async fn backup_chat_history_before_strip_noops_without_file() {
    let temp_dir = TempDir::new().unwrap();
    let adapter = JsonlStorageAdapter::with_root(temp_dir.path().to_path_buf());
    let info = create_test_info();
    adapter.backup_chat_history_before_strip(&info).await.unwrap();
    assert!(
            !adapter
                .chat_file(&info)
                .with_extension("jsonl.pre-strip")
                .exists()
        );
}
/// The exact incident shape: a partial record with the next record
/// appended straight onto it (no newline in between — the log-and-continue
/// append path pre-heal). The merged line fails with "expected `,` or `}`"
/// and is skipped; the load succeeds.
#[test]
fn read_chat_history_skips_merged_line_from_interrupted_append() {
    let good_1 = r#"{"type":"user","content":[{"type":"text","text":"kept"}]}"#;
    let partial = r#"{"type":"assistant","content":"cut mid-wri"#;
    let merged_onto = r#"{"type":"user","content":[{"type":"text","text":"lost"}]}"#;
    let good_2 = r#"{"type":"assistant","content":"after","model_id":"grok-build"}"#;
    let raw = format!("{good_1}\n{partial}{merged_onto}\n{good_2}\n");
    let temp_dir = TempDir::new().unwrap();
    let (_, _, items) = load_raw_chat(&temp_dir, raw.as_bytes());
    assert_eq!(items.len(), 2, "merged line dropped, neighbors kept");
    assert!(matches!(&items[0], ConversationItem::User(_)));
    assert!(
            matches!(&items[1], ConversationItem::Assistant(a) if a.content.as_ref() == "after")
        );
}
/// A line torn in the middle of a multi-byte UTF-8 codepoint must poison
/// only itself — not the whole file (the old `read_to_string` failed the
/// entire load with InvalidData on any invalid UTF-8 byte).
#[test]
fn read_chat_history_skips_line_torn_mid_utf8_codepoint() {
    let good = r#"{"type":"user","content":[{"type":"text","text":"survives"}]}"#;
    let mut raw = Vec::new();
    raw.extend_from_slice(good.as_bytes());
    raw.push(b'\n');
    raw.extend_from_slice(br#"{"type":"assistant","content":"price: "#);
    raw.extend_from_slice(&[0xE2, 0x82]);
    raw.push(b'\n');
    let temp_dir = TempDir::new().unwrap();
    let (_, _, items) = load_raw_chat(&temp_dir, &raw);
    assert_eq!(user_text(&items), vec!["survives"]);
    assert_eq!(items.len(), 1);
}
/// Structurally valid JSON that decodes as neither ConversationItem nor
/// legacy ChatRequestMessage (schema drift / foreign writer) is skipped,
/// not fatal.
#[test]
fn read_chat_history_skips_undecodable_but_valid_json_line() {
    let good = r#"{"type":"user","content":[{"type":"text","text":"kept"}]}"#;
    let raw = format!("[1,2,3]\n{good}\n");
    let temp_dir = TempDir::new().unwrap();
    let (_, _, items) = load_raw_chat(&temp_dir, raw.as_bytes());
    assert_eq!(user_text(&items), vec!["kept"]);
    assert_eq!(items.len(), 1);
}
/// A record torn at EOF with no trailing newline (crash artifact before
/// any healing append ran) is skipped on read.
#[test]
fn read_chat_history_skips_torn_tail_without_trailing_newline() {
    let good = r#"{"type":"user","content":[{"type":"text","text":"kept"}]}"#;
    let raw = format!(r#"{good}{}"#, "\n{\"type\":\"assistant\",\"content\":\"cut");
    let temp_dir = TempDir::new().unwrap();
    let (_, _, items) = load_raw_chat(&temp_dir, raw.as_bytes());
    assert_eq!(user_text(&items), vec!["kept"]);
    assert_eq!(items.len(), 1);
}
/// First detection wins: a later read of a (differently) corrupt file
/// must not clobber the original quarantine evidence.
#[test]
fn read_chat_history_quarantine_preserves_first_evidence() {
    let good = r#"{"type":"user","content":[{"type":"text","text":"kept"}]}"#;
    let first_corruption = format!("{good}\n{{\"type\":\"assistant\",\"content\":\"v1-torn\n");
    let temp_dir = TempDir::new().unwrap();
    let (adapter, chat_path, _) = load_raw_chat(&temp_dir, first_corruption.as_bytes());
    let quarantine = chat_path.with_extension("jsonl.corrupt");
    assert_eq!(
            std::fs::read_to_string(&quarantine).unwrap(),
            first_corruption
        );
    let second_corruption = format!("{good}\n{{\"type\":\"assistant\",\"content\":\"v2-torn\n");
    std::fs::write(&chat_path, &second_corruption).unwrap();
    adapter.read_chat_history_sync(chat_path.clone(), CHAT_FORMAT_VERSION).unwrap();
    assert_eq!(
            std::fs::read_to_string(&quarantine).unwrap(),
            first_corruption,
            "earliest corruption evidence must be preserved"
        );
}
/// A clean file must not leave a quarantine copy behind.
#[test]
fn read_chat_history_clean_file_writes_no_quarantine() {
    let good = r#"{"type":"user","content":[{"type":"text","text":"clean"}]}"#;
    let raw = format!("{good}\n");
    let temp_dir = TempDir::new().unwrap();
    let (_, chat_path, items) = load_raw_chat(&temp_dir, raw.as_bytes());
    assert_eq!(items.len(), 1);
    assert!(
            !chat_path.with_extension("jsonl.corrupt").exists(),
            "no corruption detected → no quarantine copy"
        );
}
