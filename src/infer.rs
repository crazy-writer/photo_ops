// ============================================================
// infer.rs — Pure-Rust ONNX inference engine
// Uses tract-onnx: pure Rust, no C++, no ONNX Runtime DLL.
//
// Two models (both embedded via include_bytes! — zero network calls):
//   1. sign_net_q8.onnx — SignatureNet CNN (1×128×128 luma → signature logit)
//   2. face_det.onnx    — UltraFace-320   (1×3×240×320 → face scores + boxes)
//
// Fallback behaviour when models are not yet embedded:
//   → Returns Err("model not available") so callers can use the
//     existing SeetaFace / heuristic path instead.
//
// HOW TO ACTIVATE:
//   1. Train models in colab_train.py
//   2. Copy .onnx files to d:/module/IP_final/models/
//   3. Uncomment the include_bytes! lines below
//   4. cargo build --release
// ============================================================

use std::sync::OnceLock;

// ── Model bytes — uncomment AFTER copying onnx files to models/ ─────────────
//
// static SIGN_MODEL_BYTES: &[u8] =
//     include_bytes!("../../models/sign_net_q8.onnx");
//
// static FACE_MODEL_BYTES: &[u8] =
//     include_bytes!("../../models/face_det.onnx");
//
// When commented out, SIGN_MODEL_BYTES / FACE_MODEL_BYTES are replaced by
// the stubs below so the code compiles but falls back to heuristics.

static SIGN_MODEL_BYTES: &[u8] = b"";  // placeholder — replace with include_bytes!
static FACE_MODEL_BYTES: &[u8] = b"";  // placeholder — replace with include_bytes!

// ── Cached model sessions (loaded once, reused forever) ─────────────────────
// tract::prelude::TypedRunnableModel is Send + Sync so safe in OnceLock.
use tract_onnx::prelude::*;

type TractModel = SimplePlan<TypedFact, Box<dyn TypedOp>, Graph<TypedFact, Box<dyn TypedOp>>>;

static SIGN_SESSION: OnceLock<Option<TractModel>> = OnceLock::new();
static FACE_SESSION: OnceLock<Option<TractModel>> = OnceLock::new();

// ─────────────────────────────────────────────────────────────────────────────
// Signature model loader
// ─────────────────────────────────────────────────────────────────────────────
fn load_sign_model() -> Option<TractModel> {
    if SIGN_MODEL_BYTES.is_empty() {
        return None; // model not yet embedded
    }
    tract_onnx::onnx()
        .model_for_read(&mut std::io::Cursor::new(SIGN_MODEL_BYTES))
        .and_then(|m| m.with_input_fact(0, f32::fact([1usize, 1, 128, 128]).into()))
        .and_then(|m| m.into_optimized())
        .and_then(|m| m.into_runnable())
        .ok()
}

// ─────────────────────────────────────────────────────────────────────────────
// Face model loader (UltraFace-320)
// ─────────────────────────────────────────────────────────────────────────────
fn load_face_model() -> Option<TractModel> {
    if FACE_MODEL_BYTES.is_empty() {
        return None;
    }
    tract_onnx::onnx()
        .model_for_read(&mut std::io::Cursor::new(FACE_MODEL_BYTES))
        .and_then(|m| m.with_input_fact(0, f32::fact([1usize, 3, 240, 320]).into()))
        .and_then(|m| m.into_optimized())
        .and_then(|m| m.into_runnable())
        .ok()
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API — called by transform.rs
// ─────────────────────────────────────────────────────────────────────────────

/// Returns `Ok(true)` if SignatureNet classifies the luma buffer as a signature.
/// `luma` must be a 128×128 grayscale buffer normalised to [0, 1].
/// Returns `Err` if model is not embedded — caller should use heuristic fallback.
pub fn classify_signature(luma_128x128: &[f32]) -> Result<(bool, f32), &'static str> {
    let session = SIGN_SESSION.get_or_init(load_sign_model);
    let model = match session {
        Some(m) => m,
        None => return Err("sign model not embedded"),
    };

    // Build input tensor (1×1×128×128)
    let tensor: Tensor = tract_ndarray::Array4::from_shape_fn(
        (1, 1, 128, 128),
        |(_, _, h, w)| luma_128x128[h * 128 + w],
    ).into();

    let result = model.run(tvec!(tensor.into())).map_err(|_| "sign inference error")?;
    let logit = result[0].to_scalar::<f32>().map_err(|_| "sign output error")?;
    let prob = 1.0_f32 / (1.0 + (-logit).exp());
    Ok((prob > 0.5, prob))
}

/// Returns `Ok(true)` if UltraFace detects at least one face in the RGB buffer.
/// `rgb` must be 320×240 RGB pixels interleaved (W×H×C order).
/// Normalises internally: (pixel - 127) / 128.
/// Returns `Err` if model is not embedded — caller uses SeetaFace fallback.
pub fn detect_face_ultraface(rgb_320x240: &[u8]) -> Result<bool, &'static str> {
    let session = FACE_SESSION.get_or_init(load_face_model);
    let model = match session {
        Some(m) => m,
        None => return Err("face model not embedded"),
    };

    // Normalise and transpose: HWC → CHW
    // Input buffer layout: rgb_320x240[y * 320 * 3 + x * 3 + c]
    let mut input = vec![0f32; 3 * 240 * 320];
    for y in 0..240usize {
        for x in 0..320usize {
            for c in 0..3usize {
                let src_idx = y * 320 * 3 + x * 3 + c;
                let dst_idx = c * 240 * 320 + y * 320 + x;
                input[dst_idx] = (rgb_320x240[src_idx] as f32 - 127.0) / 128.0;
            }
        }
    }

    let tensor: Tensor = tract_ndarray::Array4::from_shape_fn(
        (1, 3, 240, 320),
        |(_, c, h, w)| input[c * 240 * 320 + h * 320 + w],
    ).into();

    let result = model.run(tvec!(tensor.into())).map_err(|_| "face inference error")?;

    // UltraFace output:
    //   result[0]: scores  shape (1, N, 2)  — col 1 = face probability
    //   result[1]: boxes   shape (1, N, 4)
    let scores = result[0].to_array_view::<f32>().map_err(|_| "face output error")?;
    let threshold = 0.7_f32;
    let detected = scores.iter()
        .enumerate()
        .filter(|(i, _)| i % 2 == 1)   // every 2nd element = face probability
        .any(|(_, &s)| s > threshold);

    Ok(detected)
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper: resize a GrayImage to 128×128 and return normalised f32 buffer
// ─────────────────────────────────────────────────────────────────────────────
pub fn gray_to_128x128_f32(img: &image::GrayImage) -> Vec<f32> {
    use image::imageops;
    let resized = imageops::resize(img, 128, 128, imageops::FilterType::Triangle);
    resized.into_raw().iter().map(|&p| p as f32 / 255.0).collect()
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper: resize a DynamicImage to 320×240 RGB and return raw bytes
// ─────────────────────────────────────────────────────────────────────────────
pub fn img_to_320x240_rgb(img: &image::DynamicImage) -> Vec<u8> {
    use image::imageops;
    let rgb = img.to_rgb8();
    let resized = imageops::resize(&rgb, 320, 240, imageops::FilterType::Triangle);
    resized.into_raw()
}

// ─────────────────────────────────────────────────────────────────────────────
// Convenience: run signature classifier on a DynamicImage
// Returns Ok((is_sig, confidence)) or Err if model not loaded
// ─────────────────────────────────────────────────────────────────────────────
pub fn classify_signature_img(img: &image::DynamicImage) -> Result<(bool, f32), &'static str> {
    let luma = img.to_luma8();
    let buf = gray_to_128x128_f32(&luma);
    classify_signature(&buf)
}

// ─────────────────────────────────────────────────────────────────────────────
// Convenience: run UltraFace on a DynamicImage, trying all 4 rotations
// Returns Ok(true) if face found in any rotation
// ─────────────────────────────────────────────────────────────────────────────
pub fn detect_face_img(img: &image::DynamicImage) -> Result<bool, &'static str> {
    use image::DynamicImage;

    for rot in 0u8..4 {
        let rotated: DynamicImage = match rot {
            0 => img.clone(),
            1 => img.rotate90(),
            2 => img.rotate180(),
            3 => img.rotate270(),
            _ => unreachable!(),
        };
        let rgb = img_to_320x240_rgb(&rotated);
        match detect_face_ultraface(&rgb) {
            Ok(true) => return Ok(true),
            Ok(false) => {}
            Err(e) => return Err(e),
        }
    }
    Ok(false)
}

// ─────────────────────────────────────────────────────────────────────────────
// model_status — reports what is embedded (called by Python for diagnostics)
// ─────────────────────────────────────────────────────────────────────────────
pub fn model_status() -> (bool, bool) {
    let sign_ok = !SIGN_MODEL_BYTES.is_empty();
    let face_ok = !FACE_MODEL_BYTES.is_empty();
    (sign_ok, face_ok)
}
