use opencv::{
    core::{self, Mat, Vec3b, CV_8UC3, CV_32F},
    imgcodecs,
    imgproc,
    prelude::*,
};
use std::path::Path;
use pyo3::exceptions::PyValueError;

/// Apply brightness adjustment (-100 to +100 scale, or any value)
pub fn adjust_brightness(input: &str, output: &str, value: f64) -> Result<(), String> {
    if !std::path::Path::new(input).exists() {
        return Err(format!("Input file not found: {}", input));
    }
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    let mut result = Mat::default();
    img.convert_to(&mut result, CV_32F, 1.0, value)
        .map_err(|e| format!("Failed to adjust brightness: {}", e))?;
    
    // Clip values to 0-255
    let mut out = Mat::default();
    result.convert_to(&mut out, CV_8UC3, 1.0, 0.0)
        .map_err(|e| format!("Failed to convert back: {}", e))?;
    
    imgcodecs::imwrite(output, &out, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    
    Ok(())
}

/// Apply saturation adjustment (0.0 to 2.0, where 1.0 = original)
pub fn adjust_saturation(input: &str, output: &str, factor: f64) -> Result<(), String> {
    if !std::path::Path::new(input).exists() {
        return Err(format!("Input file not found: {}", input));
    }
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    // Convert to HSV
    let mut hsv = Mat::default();
    imgproc::cvt_color(&img, &mut hsv, imgproc::COLOR_BGR2HSV, 0)
        .map_err(|e| format!("Failed to convert to HSV: {}", e))?;
    
    // Split channels
    let mut channels = opencv::types::VectorOfMat::new();
    core::split(&hsv, &mut channels)
        .map_err(|e| format!("Failed to split channels: {}", e))?;
    
    if channels.len() >= 3 {
        // Multiply saturation channel (index 1) by factor
        let s_channel = channels.get(1).unwrap();
        let mut s_adjusted = Mat::default();
        s_channel.convert_to(&mut s_adjusted, -1, factor, 0.0)
            .map_err(|e| format!("Failed to adjust saturation: {}", e))?;
        
        channels.set(1, s_adjusted);
        
        // Merge back
        let mut merged = Mat::default();
        core::merge(&channels, &mut merged)
            .map_err(|e| format!("Failed to merge channels: {}", e))?;
        
        // Convert back to BGR
        let mut result = Mat::default();
        imgproc::cvt_color(&merged, &mut result, imgproc::COLOR_HSV2BGR, 0)
            .map_err(|e| format!("Failed to convert back to BGR: {}", e))?;
        
        imgcodecs::imwrite(output, &result, &opencv::types::VectorOfi32::new())
            .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    }
    
    Ok(())
}

/// Apply tint (shift color balance towards a specific color)
pub fn adjust_tint(input: &str, output: &str, r_shift: f64, g_shift: f64, b_shift: f64) -> Result<(), String> {
    if !std::path::Path::new(input).exists() {
        return Err(format!("Input file not found: {}", input));
    }
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    let mut result = Mat::default();
    img.convert_to(&mut result, CV_32F, 1.0, 0.0)
        .map_err(|e| format!("Failed to convert: {}", e))?;
    
    // Split channels (BGR order in OpenCV)
    let mut channels = opencv::types::VectorOfMat::new();
    core::split(&result, &mut channels)
        .map_err(|e| format!("Failed to split: {}", e))?;
    
    if channels.len() >= 3 {
        // Adjust each channel
        let b = channels.get(0).unwrap();
        let g = channels.get(1).unwrap();
        let r = channels.get(2).unwrap();
        
        let mut b_adj = Mat::default();
        let mut g_adj = Mat::default();
        let mut r_adj = Mat::default();
        
        b.convert_to(&mut b_adj, -1, 1.0, b_shift)
            .map_err(|e| format!("Failed to adjust blue: {}", e))?;
        g.convert_to(&mut g_adj, -1, 1.0, g_shift)
            .map_err(|e| format!("Failed to adjust green: {}", e))?;
        r.convert_to(&mut r_adj, -1, 1.0, r_shift)
            .map_err(|e| format!("Failed to adjust red: {}", e))?;
        
        channels.set(0, b_adj);
        channels.set(1, g_adj);
        channels.set(2, r_adj);
        
        let mut merged = Mat::default();
        core::merge(&channels, &mut merged)
            .map_err(|e| format!("Failed to merge: {}", e))?;
        
        // Clip to 0-255 and convert back
        let mut out = Mat::default();
        merged.convert_to(&mut out, CV_8UC3, 1.0, 0.0)
            .map_err(|e| format!("Failed to convert back: {}", e))?;
        
        imgcodecs::imwrite(output, &out, &opencv::types::VectorOfi32::new())
            .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    }
    
    Ok(())
}

/// Auto-match brightness/saturation/tint to reference image
pub fn match_to_reference(input: &str, reference: &str, output: &str) -> Result<(), String> {
    if !std::path::Path::new(input).exists() {
        return Err(format!("Input file not found: {}", input));
    }
    if !std::path::Path::new(reference).exists() {
        return Err(format!("Reference file not found: {}", reference));
    }
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read input {}: {}", input, e))?;
    let ref_img = imgcodecs::imread(reference, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read reference {}: {}", reference, e))?;
    
    if img.empty() || ref_img.empty() {
        return Err("Input or reference image is empty".to_string());
    }
    
    // Calculate mean brightness of both images
    let mut img_hsv = Mat::default();
    let mut ref_hsv = Mat::default();
    imgproc::cvt_color(&img, &mut img_hsv, imgproc::COLOR_BGR2HSV, 0)
        .map_err(|e| format!("Failed to convert input to HSV: {}", e))?;
    imgproc::cvt_color(&ref_img, &mut ref_hsv, imgproc::COLOR_BGR2HSV, 0)
        .map_err(|e| format!("Failed to convert reference to HSV: {}", e))?;
    
    // Get average Value (brightness) channel
    let mut img_channels = opencv::types::VectorOfMat::new();
    let mut ref_channels = opencv::types::VectorOfMat::new();
    core::split(&img_hsv, &mut img_channels)
        .map_err(|e| format!("Failed to split input: {}", e))?;
    core::split(&ref_hsv, &mut ref_channels)
        .map_err(|e| format!("Failed to split reference: {}", e))?;
    
    if img_channels.len() >= 3 && ref_channels.len() >= 3 {
        let img_v = img_channels.get(2).unwrap();
        let ref_v = ref_channels.get(2).unwrap();
        
        let img_mean = core::mean(&img_v, &core::no_array())
            .map_err(|e| format!("Failed to get input mean: {}", e))?;
        let ref_mean = core::mean(&ref_v, &core::no_array())
            .map_err(|e| format!("Failed to get reference mean: {}", e))?;
        
        let brightness_adjust = ref_mean[0] - img_mean[0];
        
        // Apply brightness adjustment
        let mut result = Mat::default();
        img.convert_to(&mut result, CV_32F, 1.0, brightness_adjust)
            .map_err(|e| format!("Failed to match brightness: {}", e))?;
        
        let mut out = Mat::default();
        result.convert_to(&mut out, CV_8UC3, 1.0, 0.0)
            .map_err(|e| format!("Failed to convert back: {}", e))?;
        
        imgcodecs::imwrite(output, &out, &opencv::types::VectorOfi32::new())
            .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    }
    
    Ok(())
}

// PyO3 wrappers
use pyo3::prelude::*;

#[pyfunction]
pub fn py_brightness(input: &str, output: &str, value: f64) -> PyResult<()> {
    adjust_brightness(input, output, value).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_saturation(input: &str, output: &str, factor: f64) -> PyResult<()> {
    adjust_saturation(input, output, factor).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_tint(input: &str, output: &str, r_shift: f64, g_shift: f64, b_shift: f64) -> PyResult<()> {
    adjust_tint(input, output, r_shift, g_shift, b_shift).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_match_to_reference(input: &str, reference: &str, output: &str) -> PyResult<()> {
    match_to_reference(input, reference, output).map_err(|e| PyValueError::new_err(e))
}
