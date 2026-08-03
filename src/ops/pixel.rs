// ops/pixel.rs — pure-Rust per-pixel adjustments (no OpenCV)
// Used by stress tests via ip.brightness(), ip.saturation(), ip.tint()

use image::DynamicImage;
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use crate::utils::{read_image, write_image};

fn clamp_u8(v: f32) -> u8 { v.clamp(0.0, 255.0) as u8 }

// ---------------------------------------------------------------------------
// Internal: apply a per-pixel RGB transform while preserving the alpha channel.
//
// `transform` receives (r, g, b) as f32 and returns the adjusted [r, g, b].
// The alpha channel is read from the source and written to the output unchanged.
//
// When the source has no alpha (RGB), an RGB output is produced.
// When the source has alpha (RGBA), an RGBA output is produced.
// This means a PNG with transparency in → PNG with transparency out, with
// colour adjustments applied only to the colour channels.
// ---------------------------------------------------------------------------
fn apply_rgb_transform<F>(img: DynamicImage, transform: F) -> Result<DynamicImage, String>
where
    F: Fn(f32, f32, f32) -> [u8; 3],
{
    let has_alpha = img.color().has_alpha();

    if has_alpha {
        let rgba = img.to_rgba8();
        let (w, h) = rgba.dimensions();
        let data: Vec<u8> = rgba
            .pixels()
            .flat_map(|p| {
                let [r, g, b, a] = p.0;
                let [nr, ng, nb] = transform(r as f32, g as f32, b as f32);
                [nr, ng, nb, a]   // alpha passed through unchanged
            })
            .collect();
        let buf = image::ImageBuffer::from_raw(w, h, data)
            .ok_or("apply_rgb_transform: RGBA buffer size mismatch")?;
        Ok(DynamicImage::ImageRgba8(buf))
    } else {
        let rgb = img.to_rgb8();
        let (w, h) = rgb.dimensions();
        let data: Vec<u8> = rgb
            .pixels()
            .flat_map(|p| {
                let [r, g, b] = p.0;
                transform(r as f32, g as f32, b as f32)
            })
            .collect();
        let buf = image::ImageBuffer::from_raw(w, h, data)
            .ok_or("apply_rgb_transform: RGB buffer size mismatch")?;
        Ok(DynamicImage::ImageRgb8(buf))
    }
}

/// Brightness: add `value` to every RGB channel. Alpha is preserved.
/// value=200 blows out, value=-230 crushes to black.
pub fn brightness(input: &str, output: &str, value: f64) -> Result<(), String> {
    let img = read_image(input)?;
    let v = value as f32;
    let out_img = apply_rgb_transform(img, |r, g, b| {
        [clamp_u8(r + v), clamp_u8(g + v), clamp_u8(b + v)]
    })?;
    write_image(output, &out_img)
}

/// Saturation: scale chroma. Alpha is preserved.
/// factor=1.0 = identity, 5.0 = hyper-saturated, 0.0 = grayscale.
pub fn saturation(input: &str, output: &str, factor: f64) -> Result<(), String> {
    let img = read_image(input)?;
    let f = factor as f32;
    let out_img = apply_rgb_transform(img, |r, g, b| {
        // Rec.601 luma as the desaturation target
        let gray = 0.299 * r + 0.587 * g + 0.114 * b;
        [
            clamp_u8(gray + (r - gray) * f),
            clamp_u8(gray + (g - gray) * f),
            clamp_u8(gray + (b - gray) * f),
        ]
    })?;
    write_image(output, &out_img)
}

/// Tint: shift individual RGB channels. Alpha is preserved.
/// Values outside [-255, 255] are clamped.
pub fn tint(input: &str, output: &str, r_shift: f64, g_shift: f64, b_shift: f64) -> Result<(), String> {
    let img = read_image(input)?;
    let (rs, gs, bs) = (r_shift as f32, g_shift as f32, b_shift as f32);
    let out_img = apply_rgb_transform(img, |r, g, b| {
        [clamp_u8(r + rs), clamp_u8(g + gs), clamp_u8(b + bs)]
    })?;
    write_image(output, &out_img)
}

// PyO3 wrappers
#[pyfunction]
pub fn py_brightness(input: &str, output: &str, value: f64) -> PyResult<()> {
    brightness(input, output, value).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_saturation(input: &str, output: &str, factor: f64) -> PyResult<()> {
    saturation(input, output, factor).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_tint(input: &str, output: &str, r_shift: f64, g_shift: f64, b_shift: f64) -> PyResult<()> {
    tint(input, output, r_shift, g_shift, b_shift).map_err(PyValueError::new_err)
}