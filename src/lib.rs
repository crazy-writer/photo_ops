use pyo3::prelude::*;

// Phase 0: Infrastructure
pub mod cache;
pub mod logger;

// Phase 1: Core Operations (uses `image` crate)
pub mod ops;

// Phase 7: Batch processing
pub mod batch;

// Re-export utils so child modules can access via crate::utils
pub mod utils;
// pub mod ops_match;

// Test function to verify Rust-Python bridge
#[pyfunction]
fn _dummy_health_check() -> PyResult<String> {
    Ok("photo_ops Rust core v0.1.0 initialized".to_string())  // keep in sync with Cargo.toml
}

// Phase 0: Logging control (expose logger function directly)
#[pyfunction]
#[pyo3(signature = (enabled, path=None))]
fn set_logging(enabled: bool, path: Option<&str>) -> PyResult<()> {
    logger::set_logging(enabled, path);
    Ok(())
}

// Main PyO3 module - named _rust to avoid conflict with Python package
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Initialize logger
    logger::init_logger();
    
    // Health check
    m.add_function(wrap_pyfunction!(_dummy_health_check, m)?)?;
    
    // Phase 0: Logging
    m.add_function(wrap_pyfunction!(set_logging, m)?)?;
    
    // Phase 1: Core Operations
    m.add_function(wrap_pyfunction!(ops::core::py_to_gray, m)?)?;
    m.add_function(wrap_pyfunction!(ops::core::py_resize, m)?)?;
    m.add_function(wrap_pyfunction!(ops::core::py_info, m)?)?;
    
    // Phase 2: Orientation System
    m.add_function(wrap_pyfunction!(ops::orient::py_rotate, m)?)?;
    m.add_function(wrap_pyfunction!(ops::orient::py_fix_orientation, m)?)?;
    m.add_function(wrap_pyfunction!(ops::orient::py_match_orientation, m)?)?;

    // Phase 2+: Pixel adjustments (Pure Rust & OpenCV)
    m.add_function(wrap_pyfunction!(ops::pixel::py_brightness, m)?)?;
    m.add_function(wrap_pyfunction!(ops::pixel::py_saturation, m)?)?;
    m.add_function(wrap_pyfunction!(ops::pixel::py_tint, m)?)?;


    // Phase 3: Transformations
    m.add_function(wrap_pyfunction!(ops::transform::py_crop, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_auto_crop, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_smart_crop, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_flip, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_scale, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_enhance_signature, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_is_signature, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_is_signature_fallback, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_has_faces, m)?)?;
    // New Phase 5: Invert, dark-bg detect, arbitrary rotation, smart_crop_v2, tilt
    m.add_function(wrap_pyfunction!(ops::transform::py_rotate_any, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_invert, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_is_dark_background, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_smart_crop_v2, m)?)?;
    m.add_function(wrap_pyfunction!(ops::transform::py_detect_tilt_angle, m)?)?;

    // Phase 4: Vision & Filters (Pure Rust)
    m.add_function(wrap_pyfunction!(ops::filter_pure::py_blur, m)?)?;
    m.add_function(wrap_pyfunction!(ops::filter_pure::py_sharpen, m)?)?;
    m.add_function(wrap_pyfunction!(ops::filter_pure::py_edge, m)?)?;
    m.add_function(wrap_pyfunction!(ops::filter_pure::py_edge_shadow, m)?)?;
    m.add_function(wrap_pyfunction!(ops::filter_pure::py_blur_background, m)?)?;
    m.add_function(wrap_pyfunction!(ops::filter_pure::py_match_to_reference, m)?)?;

    /*
     // Phase 4: Vision (OpenCV)
     m.add_function(wrap_pyfunction!(ops::vision::py_detect_face, m)?)?;
     m.add_function(wrap_pyfunction!(ops::vision::py_remove_background, m)?)?;
     m.add_function(wrap_pyfunction!(ops::vision::py_detect_objects, m)?)?;
     
     // Phase 5: Matching
     m.add_function(wrap_pyfunction!(ops_match::py_image_similarity, m)?)?;
     m.add_function(wrap_pyfunction!(ops_match::py_smart_crop_v2, m)?)?;
    */

    // Phase 7: Batch Processing
    m.add_function(wrap_pyfunction!(batch::py_batch_resize, m)?)?;
    m.add_function(wrap_pyfunction!(batch::py_batch_process, m)?)?;
    m.add_function(wrap_pyfunction!(batch::py_batch_pipeline, m)?)?;
    
    Ok(())
}
