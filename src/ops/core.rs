use image::DynamicImage;
use pyo3::exceptions::PyValueError;
use crate::utils::{normalize_path, read_image, write_image};
use crate::cache::{should_skip, sha256_checksum};

/// Convert image to grayscale (uses `image` crate for speed, ≤15ms target)
pub fn to_gray(input: &str, output: &str, skip_if_exists: bool) -> Result<(), String> {
    let input = normalize_path(input);
    let output = normalize_path(output);
    
    if skip_if_exists && should_skip(&input, &output) {
        return Ok(());
    }
    
    let img = read_image(&input)?;
    let gray = img.into_luma8();
    let gray_img = DynamicImage::ImageLuma8(gray);
    
    write_image(&output, &gray_img)?;
    Ok(())
}

/// Resize image (≤10ms target, uses `image` crate)
pub fn resize(input: &str, output: &str, width: i32, height: i32, lock_aspect: bool) -> Result<(), String> {
    let input = normalize_path(input);
    let output = normalize_path(output);
    
    let img = read_image(&input)?;
    
    let (new_w, new_h) = if lock_aspect {
        let aspect = img.width() as f64 / img.height() as f64;
        let (w, h) = if (width as f64 / height as f64) > aspect {
            ((height as f64 * aspect) as u32, height as u32)
        } else {
            (width as u32, (width as f64 / aspect) as u32)
        };
        (w, h)
    } else {
        (width as u32, height as u32)
    };
    
    let resized = img.resize_exact(new_w, new_h, image::imageops::FilterType::Lanczos3);
    write_image(&output, &resized)?;
    
    Ok(())
}

/// Get image info (≤5ms target, returns width/height/channels/checksum)
pub fn info(input: &str) -> Result<(i32, i32, i32, String), String> {
    let input = normalize_path(input);
    
    let img = read_image(&input)?;
    
    let width = img.width() as i32;
    let height = img.height() as i32;
    let channels = match img {
        DynamicImage::ImageLuma8(_) => 1,
        DynamicImage::ImageRgb8(_) => 3,
        DynamicImage::ImageRgba8(_) => 4,
        _ => 3,
    };
    
    let checksum = sha256_checksum(&input).unwrap_or_else(|_| "unknown".to_string());
    
    Ok((width, height, channels, checksum))
}

// PyO3 wrappers - keep py_ prefix to match core.py expectations
use pyo3::prelude::*;

#[pyfunction]
pub fn py_to_gray(input: &str, output: &str, skip_if_exists: bool) -> PyResult<()> {
    to_gray(input, output, skip_if_exists).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_resize(input: &str, output: &str, width: i32, height: i32, lock_aspect: bool) -> PyResult<()> {
    resize(input, output, width, height, lock_aspect).map_err(|e| PyValueError::new_err(e))
}

#[pyfunction]
pub fn py_info(input: &str) -> PyResult<(i32, i32, i32, String)> {
    info(input).map_err(|e| PyValueError::new_err(e))
}
