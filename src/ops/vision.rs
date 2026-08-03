use opencv::{
    core::{self, Mat, Rect, Scalar, Point, Size, CV_8UC1, CV_8UC3, CV_32F},
    imgcodecs,
    imgproc,
    prelude::*,
};
use pyo3::exceptions::PyValueError;

/// Detect faces using OpenCV DNN face detector.
/// TODO (Phase 4): wire up YuNet/ONNX model. Returns Err until implemented
/// so callers are never silently given fake output.
pub fn detect_face(_input: &str, _output: &str) -> Result<(), String> {
    Err("detect_face: not yet implemented — YuNet/ONNX model not wired up".to_string())
}

/// Blur background while keeping the face region sharp (portrait effect).
///
/// Blending strategy:
///   result = original * face_mask + blurred * background_mask
///
/// `face_mask`       — white (255) inside the face rect, black elsewhere.
/// `background_mask` — inverse of face_mask.
///
/// Each channel is blended independently using `core::add` after masking so
/// no floating-point conversion is needed.
///
/// TODO (Phase 4): replace the hardcoded center rect with real face detection.
pub fn blur_background(input: &str, output: &str, blur_ksize: i32, _strict: bool) -> Result<(), String> {
    if !std::path::Path::new(input).exists() {
        return Err(format!("Input file not found: {}", input));
    }
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;

    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }

    let img_width = img.cols();
    let img_height = img.rows();

    // --- Build face mask (white = keep sharp) --------------------------------
    let mut face_mask = Mat::new_rows_cols_with_default(
        img_height, img_width, CV_8UC1, Scalar::new(0.0, 0.0, 0.0, 0.0),
    ).map_err(|e| format!("Failed to create face mask: {}", e))?;

    let face_w = img_width / 4;
    let face_h = img_height / 4;
    let x = (img_width - face_w) / 2;
    let y = (img_height - face_h) / 2;
    let rect = Rect::new(x, y, face_w, face_h);

    imgproc::rectangle(
        &mut face_mask, rect,
        Scalar::new(255.0, 255.0, 255.0, 0.0),
        -1, imgproc::LINE_8, 0,
    ).map_err(|e| format!("Failed to draw face mask: {}", e))?;

    // --- Background mask (inverse of face mask) ------------------------------
    let mut bg_mask = Mat::default();
    core::bitwise_not(&face_mask, &mut bg_mask, &core::no_array())
        .map_err(|e| format!("Failed to invert mask: {}", e))?;

    // --- Blur the whole image -------------------------------------------------
    let ksize = if blur_ksize % 2 == 0 { blur_ksize + 1 } else { blur_ksize };
    let mut blurred = Mat::default();
    imgproc::gaussian_blur(
        &img, &mut blurred,
        Size::new(ksize, ksize),
        0.0, 0.0, core::BORDER_DEFAULT,
    ).map_err(|e| format!("Failed to blur: {}", e))?;

    // --- Blend: sharp face region + blurred background -----------------------
    // Copy original pixels where face_mask is white → sharp_part
    let mut sharp_part = Mat::default();
    img.copy_to_masked(&mut sharp_part, &face_mask)
        .map_err(|e| format!("Failed to mask original: {}", e))?;

    // Copy blurred pixels where bg_mask is white → blur_part
    let mut blur_part = Mat::default();
    blurred.copy_to_masked(&mut blur_part, &bg_mask)
        .map_err(|e| format!("Failed to mask blurred: {}", e))?;

    // Add the two masked images together to produce the final composite
    let mut result = Mat::default();
    core::add(&sharp_part, &blur_part, &mut result, &core::no_array(), -1)
        .map_err(|e| format!("Failed to blend: {}", e))?;

    imgcodecs::imwrite(output, &result, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;

    Ok(())
}

/// Remove background (threshold-based, requires PNG for transparency).
pub fn remove_background(input: &str, output: &str) -> Result<(), String> {
    if !output.to_lowercase().ends_with(".png") {
        return Err("remove_background requires PNG output for transparency".to_string());
    }

    if !std::path::Path::new(input).exists() {
        return Err(format!("Input file not found: {}", input));
    }
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;

    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }

    let mut gray = Mat::default();
    imgproc::cvt_color(&img, &mut gray, imgproc::COLOR_BGR2GRAY, 0)
        .map_err(|e| format!("Failed to convert to grayscale: {}", e))?;

    let mut mask = Mat::default();
    imgproc::threshold(&gray, &mut mask, 240.0, 255.0, imgproc::THRESH_BINARY_INV)
        .map_err(|e| format!("Failed to threshold: {}", e))?;

    let mut bgra = Mat::default();
    imgproc::cvt_color(&img, &mut bgra, imgproc::COLOR_BGR2BGRA, 0)
        .map_err(|e| format!("Failed to convert to BGRA: {}", e))?;

    let mut channels = opencv::types::VectorOfMat::new();
    core::split(&bgra, &mut channels)
        .map_err(|e| format!("Failed to split channels: {}", e))?;

    if channels.len() >= 4 {
        channels.set(3, mask);
        let mut result = Mat::default();
        core::merge(&channels, &mut result)
            .map_err(|e| format!("Failed to merge channels: {}", e))?;

        imgcodecs::imwrite(output, &result, &opencv::types::VectorOfi32::new())
            .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    }

    Ok(())
}

/// Detect objects using ONNX-based detection.
/// TODO (Phase 4): wire up ONNX Runtime. Returns Err until implemented
/// so callers are never silently given fake output.
pub fn detect_objects(_input: &str, _output: &str, _classes: Vec<String>) -> Result<(), String> {
    Err("detect_objects: not yet implemented — ONNX Runtime not wired up".to_string())
}

// PyO3 wrappers
use pyo3::prelude::*;

#[pyfunction]
pub fn py_detect_face(input: &str, output: &str) -> PyResult<()> {
    detect_face(input, output).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_blur_background(input: &str, output: &str, blur: i32, strict: bool) -> PyResult<()> {
    blur_background(input, output, blur, strict).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_remove_background(input: &str, output: &str) -> PyResult<()> {
    remove_background(input, output).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_detect_objects(input: &str, output: &str, classes: Vec<String>) -> PyResult<()> {
    detect_objects(input, output, classes).map_err(|e| PyValueError::new_err(e))
}