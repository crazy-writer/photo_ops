use image::{GenericImageView, GrayImage, Luma, Rgba, imageops};
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;

/// Pure Rust Gaussian Blur
pub fn blur(input: &str, output: &str, sigma: f32) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| e)?;
    let blurred = imageops::blur(&img, sigma);
    blurred.save(output).map_err(|e| format!("Failed to save: {}", e))?;
    Ok(())
}

/// Pure Rust Unsharp Mask (Sharpen)
pub fn sharpen(input: &str, output: &str, sigma: f32, amount: i32) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| e)?;
    let sharpened = imageops::unsharpen(&img, sigma, amount);
    sharpened.save(output).map_err(|e| format!("Failed to save: {}", e))?;
    Ok(())
}

/// Pure Rust Edge Detection (Sobel-like)
pub fn edge(input: &str, output: &str) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| e)?;
    let luma = img.to_luma8();
    let (w, h) = luma.dimensions();
    let mut out = GrayImage::new(w, h);

    for y in 1..h-1 {
        for x in 1..w-1 {
            let gx = -1.0 * luma.get_pixel(x-1, y-1)[0] as f32 + 1.0 * luma.get_pixel(x+1, y-1)[0] as f32
                   - 2.0 * luma.get_pixel(x-1, y)[0] as f32   + 2.0 * luma.get_pixel(x+1, y)[0] as f32
                   - 1.0 * luma.get_pixel(x-1, y+1)[0] as f32 + 1.0 * luma.get_pixel(x+1, y+1)[0] as f32;
            
            let gy = -1.0 * luma.get_pixel(x-1, y-1)[0] as f32 - 2.0 * luma.get_pixel(x, y-1)[0] as f32 - 1.0 * luma.get_pixel(x+1, y-1)[0] as f32
                   + 1.0 * luma.get_pixel(x-1, y+1)[0] as f32 + 2.0 * luma.get_pixel(x, y+1)[0] as f32 + 1.0 * luma.get_pixel(x+1, y+1)[0] as f32;
            
            let mag = (gx*gx + gy*gy).sqrt().min(255.0) as u8;
            out.put_pixel(x, y, Luma([mag]));
        }
    }
    out.save(output).map_err(|e| format!("Failed to save: {}", e))?;
    Ok(())
}

/// Stylized Edge Art (Gray background + White Edges)
pub fn edge_shadow(input: &str, output: &str, amount: i32) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| e)?;
    let luma = img.to_luma8();
    let (w, h) = luma.dimensions();
    
    // Create background (blurred and desaturated)
    let bg_sigma = (amount as f32 / 10.0).max(0.1);
    let blurred = imageops::blur(&img, bg_sigma);
    let mut bg = image::DynamicImage::ImageRgba8(blurred).to_luma8();

    // Sobel pass
    for y in 1..h-1 {
        for x in 1..w-1 {
            let gx = luma.get_pixel(x+1, y)[0] as i32 - luma.get_pixel(x-1, y)[0] as i32;
            let gy = luma.get_pixel(x, y+1)[0] as i32 - luma.get_pixel(x, y-1)[0] as i32;
            let mag = ((gx*gx + gy*gy) as f32).sqrt();
            
            if mag > 30.0 {
                bg.put_pixel(x, y, Luma([255])); // Draw white edge
            }
        }
    }

    bg.save(output).map_err(|e| format!("Failed to save: {}", e))?;
    Ok(())
}

/// Portrait Blur Fallback (Pure Rust)
pub fn blur_background(input: &str, output: &str, amount: i32) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| e)?;
    let sigma = (amount as f32 / 5.0).max(0.1);
    let blurred = imageops::blur(&img, sigma);
    
    // Simple circular mask for portrait effect (center remains sharp)
    let (w, h) = img.dimensions();
    let cx = w as f32 / 2.0;
    let cy = h as f32 / 2.0;
    let r_max = w.min(h) as f32 / 2.5;

    let mut result = img.to_rgba8();
    let blurred_rgba = blurred; 

    for y in 0..h {
        for x in 0..w {
            let dx = x as f32 - cx;
            let dy = y as f32 - cy;
            let dist = (dx*dx + dy*dy).sqrt();
            
            if dist > r_max {
                // Blend with blur based on distance
                let factor = ((dist - r_max) / (r_max * 0.5)).min(1.0).max(0.0);
                let p1 = result.get_pixel(x, y);
                let p2 = blurred_rgba.get_pixel(x, y);
                
                let r = (p1[0] as f32 * (1.0 - factor) + p2[0] as f32 * factor) as u8;
                let g = (p1[1] as f32 * (1.0 - factor) + p2[1] as f32 * factor) as u8;
                let b = (p1[2] as f32 * (1.0 - factor) + p2[2] as f32 * factor) as u8;
                
                result.put_pixel(x, y, Rgba([r, g, b, 255]));
            }
        }
    }

    result.save(output).map_err(|e| format!("Failed to save: {}", e))?;
    Ok(())
}

/// Match Look Fallback (Pure Rust)
pub fn match_to_reference(input: &str, reference: &str, output: &str) -> Result<(), String> {
    let mut img = crate::utils::read_image(input).map_err(|e| e)?.to_rgb8();
    let ref_img = crate::utils::read_image(reference).map_err(|e| e)?.to_rgb8();
    
    let mut img_sum = [0u64; 3];
    for p in img.pixels() {
        img_sum[0] += p[0] as u64; img_sum[1] += p[1] as u64; img_sum[2] += p[2] as u64;
    }
    let img_count = (img.width() * img.height()) as u64;
    
    let mut ref_sum = [0u64; 3];
    for p in ref_img.pixels() {
        ref_sum[0] += p[0] as u64; ref_sum[1] += p[1] as u64; ref_sum[2] += p[2] as u64;
    }
    let ref_count = (ref_img.width() * ref_img.height()) as u64;

    let shifts = [
        (ref_sum[0] as f64 / ref_count as f64) - (img_sum[0] as f64 / img_count as f64),
        (ref_sum[1] as f64 / ref_count as f64) - (img_sum[1] as f64 / img_count as f64),
        (ref_sum[2] as f64 / ref_count as f64) - (img_sum[2] as f64 / img_count as f64),
    ];

    for p in img.pixels_mut() {
        p[0] = (p[0] as f64 + shifts[0]).clamp(0.0, 255.0) as u8;
        p[1] = (p[1] as f64 + shifts[1]).clamp(0.0, 255.0) as u8;
        p[2] = (p[2] as f64 + shifts[2]).clamp(0.0, 255.0) as u8;
    }

    img.save(output).map_err(|e| format!("Failed to save: {}", e))?;
    Ok(())
}

// PyO3 Wrappers
#[pyfunction]
pub fn py_blur(input: &str, output: &str, amount: i32) -> PyResult<()> {
    let sigma = (amount as f32 / 10.0).max(0.1);
    blur(input, output, sigma).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_sharpen(input: &str, output: &str) -> PyResult<()> {
    sharpen(input, output, 1.0, 1).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_edge(input: &str, output: &str) -> PyResult<()> {
    edge(input, output).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_edge_shadow(input: &str, output: &str, amount: i32) -> PyResult<()> {
    edge_shadow(input, output, amount).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_blur_background(input: &str, output: &str, amount: i32) -> PyResult<()> {
    blur_background(input, output, amount).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_match_to_reference(input: &str, reference: &str, output: &str) -> PyResult<()> {
    match_to_reference(input, reference, output).map_err(PyValueError::new_err)
}
