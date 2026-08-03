use opencv::{
    core::{self, Mat, DMatch, VectorOfDMatch, VectorOfKeyPoint, CV_8UC1, CV_32F},
    imgcodecs,
    features2d,
    imgproc,
    prelude::*,
};
use pyo3::exceptions::PyValueError;
use std::path::Path;

// Lowe's ratio test threshold: a match is good only when the best match distance
// is significantly smaller than the second-best. 0.75 is the value from the
// original paper and works well for ORB descriptors.
const LOWE_RATIO: f32 = 0.75;

/// Calculate image similarity using ORB + Lowe's ratio test (0.0 to 1.0).
pub fn image_similarity(img1_path: &str, img2_path: &str, _method: &str) -> Result<f64, String> {
    let img1 = imgcodecs::imread(img1_path, imgcodecs::IMREAD_GRAYSCALE)
        .map_err(|e| format!("Failed to read {}: {}", img1_path, e))?;
    let img2 = imgcodecs::imread(img2_path, imgcodecs::IMREAD_GRAYSCALE)
        .map_err(|e| format!("Failed to read {}: {}", img2_path, e))?;

    if img1.empty() || img2.empty() {
        return Err("One or both images are empty".to_string());
    }

    // Initialise ORB detector
    let orb = features2d::ORB::create(
        500, 1.2, 8, 31, 0, 2, features2d::ORB_ScoreType::HARRIS_SCORE, 31, 20,
    ).map_err(|e| format!("Failed to create ORB: {}", e))?;

    // Detect and compute features for both images
    let mut kp1 = VectorOfKeyPoint::new();
    let mut desc1 = Mat::default();
    orb.detect_and_compute(&img1, &Mat::default(), &mut kp1, &mut desc1, false)
        .map_err(|e| format!("Failed to detect features in image 1: {}", e))?;

    let mut kp2 = VectorOfKeyPoint::new();
    let mut desc2 = Mat::default();
    orb.detect_and_compute(&img2, &Mat::default(), &mut kp2, &mut desc2, false)
        .map_err(|e| format!("Failed to detect features in image 2: {}", e))?;

    if desc1.empty() || desc2.empty() || kp1.len() == 0 || kp2.len() == 0 {
        return Ok(0.0);
    }

    // BFMatcher with crossCheck=FALSE — crossCheck is incompatible with knn_match.
    // knn_match needs to return the top-2 candidates per query descriptor so we
    // can apply Lowe's ratio test; crossCheck collapses that to a single match.
    let bf = features2d::BFMatcher::create(core::NORM_HAMMING, false)
        .map_err(|e| format!("Failed to create matcher: {}", e))?;

    // knn_match with k=2: for each descriptor in desc1, return its 2 nearest
    // neighbours in desc2. The result is a VectorOfVectorOfDMatch where each
    // inner vector has exactly 2 elements (best and second-best match).
    let mut knn_matches = opencv::types::VectorOfVectorOfDMatch::new();
    bf.knn_match(&desc1, &desc2, &mut knn_matches, 2, &Mat::default(), false)
        .map_err(|e| format!("Failed to knn_match: {}", e))?;

    // Lowe's ratio test: keep a match only when the best match is significantly
    // closer than the second-best. This filters out ambiguous matches where two
    // descriptors in img2 look equally similar to a descriptor in img1.
    let good_count = knn_matches
        .iter()
        .filter(|pair| {
            // pair must have exactly 2 entries; skip degenerate cases
            if pair.len() < 2 {
                return false;
            }
            let best = pair.get(0).unwrap();
            let second = pair.get(1).unwrap();
            best.distance < LOWE_RATIO * second.distance
        })
        .count();

    if good_count == 0 {
        return Ok(0.0);
    }

    // Normalise against the smaller keypoint set so the score stays in [0, 1].
    let max_possible = kp1.len().min(kp2.len());
    let similarity = if max_possible > 0 {
        (good_count as f64) / (max_possible as f64)
    } else {
        0.0
    };

    Ok(similarity.min(1.0))
}

/// Smart crop v2 with multi-fallback (Face -> Contour -> Center).
/// Contour/face paths are stubs — see Phase 4 plan for full implementation.
pub fn smart_crop_v2(input: &str, output: &str, width: i32, height: i32, _strict: bool) -> Result<(), String> {
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;

    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }

    let img_width = img.cols();
    let img_height = img.rows();

    // Phase 4 will wire up face detection and edge-based crop centering.
    // For now fall through to center crop so the function produces valid output.
    let x = ((img_width - width) / 2).max(0);
    let y = ((img_height - height) / 2).max(0);
    let w = width.min(img_width - x);
    let h = height.min(img_height - y);

    let rect = core::Rect::new(x, y, w, h);
    let cropped = Mat::roi(&img, rect)
        .map_err(|e| format!("Failed to crop: {}", e))?;

    let mut result = Mat::default();
    cropped.copy_to(&mut result)
        .map_err(|e| format!("Failed to copy cropped image: {}", e))?;

    imgcodecs::imwrite(output, &result, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;

    Ok(())
}

// PyO3 wrappers
use pyo3::prelude::*;

#[pyfunction]
pub fn py_image_similarity(img1: &str, img2: &str, method: &str) -> PyResult<f64> {
    image_similarity(img1, img2, method).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_smart_crop_v2(input: &str, output: &str, width: i32, height: i32, strict: bool) -> PyResult<()> {
    smart_crop_v2(input, output, width, height, strict).map_err(|e| PyValueError::new_err(e))
}