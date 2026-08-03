use opencv::{
    core::{self, Mat, Size, CV_8UC1, CV_8UC3},
    imgcodecs,
    imgproc,
    prelude::*,
};
use pyo3::exceptions::PyValueError;
use std::path::Path;

/// Apply Gaussian blur
pub fn blur(input: &str, output: &str, ksize: i32) -> Result<(), String> {
    if ksize % 2 == 0 {
        return Err("ksize must be odd".to_string());
    }
    
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    let mut blurred = Mat::default();
    let size = Size::new(ksize, ksize);
    imgproc::gaussian_blur(&img, &mut blurred, size, 0.0, 0.0, core::BORDER_DEFAULT)
        .map_err(|e| format!("Failed to blur: {}", e))?;
    
    imgcodecs::imwrite(output, &blurred, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    
    Ok(())
}

/// Sharpen image using unsharp mask
pub fn sharpen(input: &str, output: &str) -> Result<(), String> {
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    let mut blurred = Mat::default();
    let size = Size::new(5, 5);
    imgproc::gaussian_blur(&img, &mut blurred, size, 1.0, 0.0, core::BORDER_DEFAULT)
        .map_err(|e| format!("Failed to blur for sharpen: {}", e))?;
    
    let mut sharpened = Mat::default();
    // Sharpen = 2*original - blurred
    core::add_weighted(&img, 2.0, &blurred, -1.0, 0.0, &mut sharpened, -1)
        .map_err(|e| format!("Failed to sharpen: {}", e))?;
    
    imgcodecs::imwrite(output, &sharpened, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    
    Ok(())
}

/// Canny edge detection
pub fn edge(input: &str, output: &str) -> Result<(), String> {
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_GRAYSCALE)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    let mut edges = Mat::default();
    imgproc::canny(&img, &mut edges, 100.0, 200.0, 3, false)
        .map_err(|e| format!("Failed to detect edges: {}", e))?;
    
    imgcodecs::imwrite(output, &edges, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    
    Ok(())
}

/// Edge shadow effect (stylized soft blurred background with gray edges)
pub fn edge_shadow(input: &str, output: &str, blur: i32) -> Result<(), String> {
    let img = imgcodecs::imread(input, imgcodecs::IMREAD_COLOR)
        .map_err(|e| format!("Failed to read {}: {}", input, e))?;
    
    if img.empty() {
        return Err(format!("Input image is empty: {}", input));
    }
    
    // Convert to grayscale for edge detection
    let mut gray = Mat::default();
    imgproc::cvt_color(&img, &mut gray, imgproc::COLOR_BGR2GRAY, 0)
        .map_err(|e| format!("Failed to convert to grayscale: {}", e))?;
    
    // Detect edges
    let mut edges = Mat::default();
    imgproc::canny(&gray, &mut edges, 100.0, 200.0, 3, false)
        .map_err(|e| format!("Failed to detect edges: {}", e))?;
    
    // Blur the original image
    let mut blurred = Mat::default();
    let ksize = if blur % 2 == 0 { blur + 1 } else { blur };
    let size = Size::new(ksize, ksize);
    imgproc::gaussian_blur(&img, &mut blurred, size, 0.0, 0.0, core::BORDER_DEFAULT)
        .map_err(|e| format!("Failed to blur: {}", e))?;
    
    // Convert blurred to grayscale for background
    let mut blurred_gray = Mat::default();
    imgproc::cvt_color(&blurred, &mut blurred_gray, imgproc::COLOR_BGR2GRAY, 0)
        .map_err(|e| format!("Failed to convert blurred to grayscale: {}", e))?;
    
    // Create result: use edges as mask to combine
    // For simplicity, output the edges on gray background
    let mut result = Mat::default();
    blurred_gray.copy_to(&mut result)
        .map_err(|e| format!("Failed to copy: {}", e))?;
    
    // Add edges (white) on top
    let white = core::Scalar::new(255.0, 255.0, 255.0, 0.0);
    result.set_to(&white, &edges)
        .map_err(|e| format!("Failed to add edges: {}", e))?;
    
    imgcodecs::imwrite(output, &result, &opencv::types::VectorOfi32::new())
        .map_err(|e| format!("Failed to write {}: {}", output, e))?;
    
    Ok(())
}

// PyO3 wrappers
use pyo3::prelude::*;

#[pyfunction]
pub fn py_blur(input: &str, output: &str, ksize: i32) -> PyResult<()> {
    blur(input, output, ksize).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_sharpen(input: &str, output: &str) -> PyResult<()> {
    sharpen(input, output).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_edge(input: &str, output: &str) -> PyResult<()> {
    edge(input, output).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_edge_shadow(input: &str, output: &str, blur: i32) -> PyResult<()> {
    edge_shadow(input, output, blur).map_err(|e| PyValueError::new_err(e))
}
