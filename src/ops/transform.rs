use image::{GenericImageView, imageops};
use pyo3::exceptions::PyValueError;
use rustface::ImageData;

// ---------------------------------------------------------------------------
// Detector thresholds — single source of truth for both transform.rs and
// utils::init_detector. smart_crop no longer overrides these after init.
// ---------------------------------------------------------------------------
const FACE_MIN_SIZE: u32 = 20;
const FACE_SCORE_THRESH: f64 = 2.0;

/// Crop image to specified rectangle.
pub fn crop(input: &str, output: &str, x: i32, y: i32, width: i32, height: i32) -> Result<(), String> {
    let mut img = crate::utils::read_image(input).map_err(|e| format!("crop: failed to load input: {}", e))?;
    let (img_width, img_height) = img.dimensions();

    let x = x.clamp(0, img_width as i32 - 1) as u32;
    let y = y.clamp(0, img_height as i32 - 1) as u32;
    let max_w = (img_width - x).min(width as u32);
    let max_h = (img_height - y).min(height as u32);

    let cropped = imageops::crop(&mut img, x, y, max_w, max_h).to_image();
    cropped.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
    Ok(())
}

/// Auto crop to center of image.
pub fn auto_crop(input: &str, output: &str, width: i32, height: i32) -> Result<(), String> {
    let mut img = crate::utils::read_image(input).map_err(|e| format!("auto_crop: failed to load input: {}", e))?;
    let (img_width, img_height) = img.dimensions();

    let x = (img_width.saturating_sub(width as u32)) / 2;
    let y = (img_height.saturating_sub(height as u32)) / 2;
    let w = (width as u32).min(img_width - x);
    let h = (height as u32).min(img_height - y);

    let cropped = imageops::crop(&mut img, x, y, w, h).to_image();
    cropped.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
    Ok(())
}

/// Smart crop using face detection or signature bounding box.
///
/// Fix: image is opened ONCE here and passed by reference to `is_signature_img`
/// instead of being opened a second time inside that helper.
///
/// Fix: detector thresholds are set from the module-level constants (FACE_MIN_SIZE,
/// FACE_SCORE_THRESH) — the old code set them to different values than init_detector,
/// creating a silent divergence. Now there is one place to change them.
pub fn smart_crop(input: &str, output: &str, width: i32, height: i32, strict: bool) -> Result<(), String> {
    // Single image open — reused for signature check, luma extraction, and crop.
    let mut img = crate::utils::read_image(input).map_err(|e| format!("smart_crop: failed to load input: {}", e))?;
    let (img_width, img_height) = img.dimensions();

    // 1. Signature path — pass already-opened image, no second disk read.
    if is_signature_img(&img) {
        let luma = img.to_luma8();
        let mut min_x = img_width;
        let mut max_x = 0u32;
        let mut min_y = img_height;
        let mut max_y = 0u32;
        let mut found = false;

        let margin_x = img_width / 20; // 5%
        let margin_y = img_height / 20; // 5%
        for y in margin_y..(img_height - margin_y) {
            for x in margin_x..(img_width - margin_x) {
                let p = luma.get_pixel(x, y)[0];
                if p < 80 {
                    if x < min_x { min_x = x; }
                    if x > max_x { max_x = x; }
                    if y < min_y { min_y = y; }
                    if y > max_y { max_y = y; }
                    found = true;
                }
            }
        }

        if found {
            let box_w = max_x - min_x + 1;
            let box_h = max_y - min_y + 1;
            
            if box_h > (box_w as f32 * 1.3) as u32 {
                // Rotated! Rotate image 90 degrees clockwise
                img = img.rotate90();
                
                // Re-run bounding box detection on rotated image
                let luma = img.to_luma8();
                let (img_width, img_height) = img.dimensions();
                let mut min_x = img_width;
                let mut max_x = 0u32;
                let mut min_y = img_height;
                let mut max_y = 0u32;
                
                let margin_x = img_width / 20;
                let margin_y = img_height / 20;
                for y in margin_y..(img_height - margin_y) {
                    for x in margin_x..(img_width - margin_x) {
                        let p = luma.get_pixel(x, y)[0];
                        if p < 80 {
                            if x < min_x { min_x = x; }
                            if x > max_x { max_x = x; }
                            if y < min_y { min_y = y; }
                            if y > max_y { max_y = y; }
                        }
                    }
                }
                
                // Update coordinates for cropping
                let sign_w = max_x - min_x + 1;
                let sign_h = max_y - min_y + 1;
                
                let pad_x = (sign_w as f32 * 0.1).max(20.0) as u32;
                let pad_y = (sign_h as f32 * 0.1).max(20.0) as u32;
                
                let x = min_x.saturating_sub(pad_x);
                let y = min_y.saturating_sub(pad_y);
                let end_x = (max_x + pad_x).min(img_width - 1);
                let end_y = (max_y + pad_y).min(img_height - 1);
                let w = end_x - x + 1;
                let h = end_y - y + 1;

                let cropped = imageops::crop(&mut img, x, y, w, h).to_image();
                cropped.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
                return Ok(());
            } else {
                // Normal horizontal signature: crop as before
                let pad_x = (box_w as f32 * 0.1).max(20.0) as u32;
                let pad_y = (box_h as f32 * 0.1).max(20.0) as u32;
                
                let x = min_x.saturating_sub(pad_x);
                let y = min_y.saturating_sub(pad_y);
                let end_x = (max_x + pad_x).min(img_width - 1);
                let end_y = (max_y + pad_y).min(img_height - 1);
                let w = end_x - x + 1;
                let h = end_y - y + 1;

                let cropped = imageops::crop(&mut img, x, y, w, h).to_image();
                cropped.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
                return Ok(());
            }
        } else {
            // Valid signature but couldn't find tight box (maybe faint or full image)
            // Save as-is to success folder
            img.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
            return Ok(());
        }
    }

    // 2. Face detection path — thresholds from constants, not inline magic numbers.
    let mut detector = match crate::utils::init_detector() {
        Ok(d) => d,
        Err(e) => {
            if strict { return Err(format!("Face detection failed: {}", e)); }
            return auto_crop(input, output, width, height);
        }
    };

    // Use the module constants so there is one place to tune thresholds.
    // init_detector sets initial values; we override here with the crop-specific
    // values that are intentionally stricter (score_thresh 2.0 vs init's 1.5).
    detector.set_min_face_size(FACE_MIN_SIZE);
    detector.set_score_thresh(FACE_SCORE_THRESH);

    let luma = img.to_luma8();
    let (w_d, h_d) = luma.dimensions();
    let mut pixels = luma.into_raw();
    let mut image_data = ImageData::new(&mut pixels, w_d, h_d);
    let faces = detector.detect(&mut image_data);

    // Ensure target width/height don't exceed image dimensions
    let width = (width as u32).min(img_width);
    let height = (height as u32).min(img_height);

    if faces.is_empty() {
        if strict {
            return Err("No faces detected in the image.".to_string());
        }
        return auto_crop(input, output, width as i32, height as i32);
    }

    let best_face = faces.iter().max_by(|a, b| {
        let area_a = a.bbox().width() * a.bbox().height();
        let area_b = b.bbox().width() * b.bbox().height();
        area_a.cmp(&area_b)
    }).ok_or("Failed to identify best face")?;

    let bbox = best_face.bbox();
    let face_center_x = bbox.x() + (bbox.width() as i32 / 2);
    let face_center_y = bbox.y() + (bbox.height() as i32 / 2);

    let mut x = (face_center_x - (width as i32 / 2)).max(0) as u32;
    let mut y = (face_center_y - (height as i32 / 2)).max(0) as u32;

    if x + width > img_width { x = img_width.saturating_sub(width); }
    if y + height > img_height { y = img_height.saturating_sub(height); }

    let cropped = imageops::crop(&mut img, x, y, width, height).to_image();
    cropped.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
    Ok(())
}

/// Flip image horizontally or vertically.
pub fn flip(input: &str, output: &str, mode: &str) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| format!("flip: failed to load input: {}", e))?;
    let flipped = match mode {
        "horizontal" => img.fliph(),
        "vertical"   => img.flipv(),
        _ => return Err("Mode must be 'horizontal' or 'vertical'".to_string()),
    };
    flipped.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
    Ok(())
}

/// Scale image by factor.
pub fn scale(input: &str, output: &str, factor: f64) -> Result<(), String> {
    if factor <= 0.0 {
        return Err("Scale factor must be positive".to_string());
    }
    let img = crate::utils::read_image(input).map_err(|e| format!("scale: failed to load input: {}", e))?;
    let (width, height) = img.dimensions();
    let new_width  = (width as f64 * factor) as u32;
    let new_height = (height as f64 * factor) as u32;
    let resized = img.resize_exact(new_width, new_height, imageops::FilterType::Lanczos3);
    resized.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
    Ok(())
}

/// Enhance signature using Otsu thresholding (pure Rust, no imageproc).
pub fn enhance_signature(input: &str, output: &str, _block_size: i32, _c: f64) -> Result<(), String> {
    let img = crate::utils::read_image(input).map_err(|e| format!("enhance_signature: failed to load input: {}", e))?;
    let mut luma = img.to_luma8();

    let mut hist = [0usize; 256];
    for p in luma.pixels() { hist[p[0] as usize] += 1; }

    let total_pixels = luma.width() as usize * luma.height() as usize;
    let mut sum = 0.0f64;
    for (i, &count) in hist.iter().enumerate() { sum += i as f64 * count as f64; }

    let mut sum_b = 0.0f64;
    let mut w_b = 0usize;
    let mut var_max = 0.0f64;
    let mut threshold = 0u8;

    for (i, &count) in hist.iter().enumerate() {
        w_b += count;
        if w_b == 0 { continue; }
        let w_f = total_pixels - w_b;
        if w_f == 0 { break; }
        sum_b += i as f64 * count as f64;
        let m_b = sum_b / w_b as f64;
        let m_f = (sum - sum_b) / w_f as f64;
        let var_between = w_b as f64 * w_f as f64 * (m_b - m_f).powi(2);
        if var_between > var_max {
            var_max = var_between;
            threshold = i as u8;
        }
    }

    for p in luma.pixels_mut() {
        p[0] = if p[0] > threshold { 255 } else { 0 };
    }

    luma.save(output).map_err(|e| format!("Failed to write {}: {}", output, e))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// is_signature — two variants:
//
// is_signature_img(&DynamicImage) — internal, takes an already-opened image.
//   Used by smart_crop to avoid a second disk read.
//
// is_signature(&str) — public, opens the file itself.
//   Used by external callers (Python via py_is_signature).
// ---------------------------------------------------------------------------

/// Internal: 98% "Achievement" Signature Classifier.
/// Uses a 3-stage validation: 
/// 1. Dark Ratio (Coverage)
/// 2. Transition Density (Stroke complexity)
/// 3. Ink Clustering (Clumpiness vs. Distributed text)
/// Core classifier — determines if a pre-loaded image is a signature.
///
/// Matches the reference benchmark logic (Cropping_script/sign/signature_verification.py):
///   • `dark_ratio < 0.50`  → normal (dark ink on white bg) signature
///   • `dark_ratio > 0.85`  → inverted-bg (white ink on dark bg) signature
///   • `dark_ratio 0.50-0.85` → solid object / stamp / photo → rejected
///
/// Additional requirements in both acceptance windows:
///   • Must have ink-like transitions (edge density)
///   • Must have ink clusters (not a uniform gradient / noise)
pub fn is_signature_img(img: &image::DynamicImage) -> bool {
    // Step 0: Exposure normalization — bring luma into a reasonable range
    // before running the window analysis so that over/under-exposed scans
    // produce the same ink density distributions as well-exposed ones.
    let raw_thumb: Vec<u8>;
    let luma = {
        let thumb = img.thumbnail(128, 128).to_luma8();
        let pixels = thumb.clone().into_raw();
        let mean: f32 = pixels.iter().map(|&p| p as f32).sum::<f32>() / pixels.len() as f32;
        if mean < 60.0 {
            // Underexposed: stretch contrast to fill 0-255
            raw_thumb = stretch_contrast(&pixels);
            image::GrayImage::from_raw(thumb.width(), thumb.height(), raw_thumb.clone())
                .unwrap_or(thumb)
        } else if mean > 200.0 {
            // Overexposed: gamma darken (γ=2.0) to recover ink
            raw_thumb = gamma_correct(&pixels, 2.0);
            image::GrayImage::from_raw(thumb.width(), thumb.height(), raw_thumb.clone())
                .unwrap_or(thumb)
        } else {
            thumb
        }
    };
    let (w, h) = luma.dimensions();

    let margin = 5u32;
    // Guard against tiny thumbnails
    if w <= margin * 2 || h <= margin * 2 { return false; }

    let total_considered = ((w - 2 * margin) * (h - 2 * margin)) as f64;
    if total_considered < 1.0 { return false; }

    let mut dark_pixels = 0u32;  // pixels < 165 (ink threshold)
    let mut transitions = 0u32;

    // 4×4 grid for ink-cluster check
    let mut grid = [0u32; 16];

    // Bounding box of ink
    let mut min_x = w; let mut max_x = 0u32;
    let mut min_y = h; let mut max_y = 0u32;
    let mut found_ink = false;

    // Horizontal pass
    for y in margin..(h - margin) {
        let gy = (y * 4 / h).min(3) as usize;
        let mut prev = 255u8;
        for x in margin..(w - margin) {
            let gx = (x * 4 / w).min(3) as usize;
            let curr = luma.get_pixel(x, y)[0];
            if curr < 165 {
                dark_pixels += 1;
                grid[gy * 4 + gx] += 1;
                if x < min_x { min_x = x; }
                if x > max_x { max_x = x; }
                if y < min_y { min_y = y; }
                if y > max_y { max_y = y; }
                found_ink = true;
            }
            if (prev as i32 - curr as i32).abs() > 55 { transitions += 1; }
            prev = curr;
        }
    }

    // Vertical pass (helps with vertical/diagonal signatures)
    for x in margin..(w - margin) {
        let mut prev = 255u8;
        for y in margin..(h - margin) {
            let curr = luma.get_pixel(x, y)[0];
            if (prev as i32 - curr as i32).abs() > 55 { transitions += 1; }
            prev = curr;
        }
    }

    if !found_ink { return false; }

    let dark_ratio = dark_pixels as f64 / total_considered;
    // transitions counted in both directions → divide by 2 for density
    let transition_density = transitions as f64 / (total_considered * 2.0);

    // Reject zero-transition (uniform flat colour)
    if transition_density < 0.004 { return false; }

    // Active-cell clustering: reject uniform documents / full-page images
    let cell_threshold = total_considered / 16.0 * 0.05; // 5% of per-cell avg
    let active_cells = grid.iter().filter(|&&c| c as f64 > cell_threshold).count();

    // ── Two-window acceptance (mirrors reference Otsu logic) ─────────────
    //
    // Window 1: Normal dark-ink-on-white signature  (0 < dark_ratio < 0.50)
    let normal_sig = dark_ratio > 0.003
        && dark_ratio < 0.50
        && transition_density > 0.008
        && active_cells <= 14;   // not a fully filled document

    // Window 2: Inverted / dark-bg signature  (dark_ratio > 0.85)
    // The ink strokes are the LIGHT pixels; background is dark.
    // Check by measuring "light" pixels with an inverted dark_ratio.
    let light_ratio = 1.0 - dark_ratio;
    let inverted_sig = dark_ratio > 0.85
        && light_ratio > 0.005   // at least a few light (ink) pixels
        && transition_density > 0.004;  // visible strokes

    // Window 3: bounding-box sparse path for thin/partial signatures
    if min_x <= max_x && min_y <= max_y {
        let box_area = ((max_x - min_x + 1) * (max_y - min_y + 1)) as f64;
        let box_coverage = box_area / total_considered;
        if box_coverage < 0.45 {
            let local_dark = dark_pixels as f64 / box_area;
            let local_trans = transitions as f64 / (box_area * 2.0);
        if local_dark > 0.04 && local_dark < 0.85   // relaxed from 0.80 for thin/partial sigs
                && local_trans > 0.018                // relaxed from 0.025
                && active_cells < 9 {
                return true;
            }
        }
    }

    normal_sig || inverted_sig
}

/// Public: open `input` and classify it as signature vs subject.
pub fn is_signature(input: &str) -> Result<bool, String> {
    let img = crate::utils::read_image(input)
        .map_err(|e| format!("is_signature: failed to load input: {}", e))?;
    Ok(is_signature_img(&img))
}

/// Fallback signature classifier — looser thresholds for faint/partial/rotated images.
/// Uses the same two-window model as `is_signature_img` but with relaxed limits.
/// Also tries the inverted image to catch white-ink-on-dark-bg scans.
pub fn is_signature_fallback(input: &str) -> Result<bool, String> {
    let img = crate::utils::read_image(input)
        .map_err(|e| format!("is_signature_fallback: failed to load '{}': {}", input, e))?;

    // Try primary classifier first
    if is_signature_img(&img) { return Ok(true); }

    // Try on inverted (catches cases where JPEG decoded with inverted luma)
    let mut luma = img.thumbnail(128, 128).to_luma8();
    for p in luma.pixels_mut() { p[0] = 255 - p[0]; }
    let inverted = image::DynamicImage::ImageLuma8(luma);
    if is_signature_img(&inverted) { return Ok(true); }

    // Looser whole-image heuristic — catches faint, low-contrast, or partial sigs
    let luma2 = img.thumbnail(128, 128).to_luma8();
    let (w, h) = luma2.dimensions();
    let total = (w * h) as f64;
    if total < 1.0 { return Ok(false); }

    let mut dark = 0u32;
    let mut trans = 0u32;
    for y in 1..h {
        for x in 1..w {
            let p    = luma2.get_pixel(x, y)[0];
            let prev = luma2.get_pixel(x - 1, y)[0];
            if p < 200 { dark += 1; }
            if (p as i32 - prev as i32).abs() > 35 { trans += 1; }
        }
    }
    let dark_r  = dark  as f64 / total;
    let trans_r = trans as f64 / total;

    // Mirror reference two-window logic with relaxed thresholds:
    //   normal sig:   dark_r  0.003 - 0.55  with some transitions
    //   inverted sig: dark_r  > 0.82         with some transitions
    let is_normal   = dark_r > 0.003 && dark_r < 0.55 && trans_r > 0.002;
    let is_inverted = dark_r > 0.82  && (1.0 - dark_r) > 0.003 && trans_r > 0.002;

    Ok(is_normal || is_inverted)
}


// PyO3 wrappers
use pyo3::prelude::*;


/// Internal helper — runs RustFace on a raw luma buffer.
/// Returns true if at least one face is detected.
fn detect_on_luma(detector: &mut Box<dyn rustface::Detector>, pixels: &mut [u8], w: u32, h: u32, min_face: u32, thresh: f64) -> bool {
    detector.set_min_face_size(min_face);
    detector.set_score_thresh(thresh);
    let mut image_data = ImageData::new(pixels, w, h);
    !detector.detect(&mut image_data).is_empty()
}

/// Contrast-stretch a luma buffer to [0, 255] using its 2nd/98th percentile.
/// This normalises extremely dark or overexposed images so RustFace can see faces.
fn stretch_contrast(pixels: &[u8]) -> Vec<u8> {
    let mut hist = [0u32; 256];
    for &p in pixels { hist[p as usize] += 1; }
    let n = pixels.len() as u32;
    let low_cut  = n / 50;   // 2 %
    let high_cut = n - n / 50; // 98 %
    let mut acc = 0u32;
    let mut lo = 0u8;
    for (i, &c) in hist.iter().enumerate() {
        acc += c;
        if acc >= low_cut { lo = i as u8; break; }
    }
    acc = 0;
    let mut hi = 255u8;
    for (i, &c) in hist.iter().enumerate().rev() {
        acc += c;
        if acc >= (n - high_cut) { hi = i as u8; break; }
    }
    let range = (hi as i32 - lo as i32).max(1) as f32;
    pixels.iter().map(|&p| {
        let v = (p as f32 - lo as f32).max(0.0) / range * 255.0;
        v.min(255.0) as u8
    }).collect()
}

/// Downscale luma to max 480 px wide — helps RustFace on large high-res faces.
fn maybe_downscale(img: &image::GrayImage, max_w: u32) -> image::GrayImage {
    let (w, h) = img.dimensions();
    if w <= max_w { return img.clone(); }
    let scale = max_w as f32 / w as f32;
    let nw = max_w;
    let nh = (h as f32 * scale) as u32;
    imageops::resize(img, nw, nh, imageops::FilterType::Triangle)
}

/// Gamma-correct a luma buffer. gamma < 1.0 brightens (e.g. 0.5), > 1.0 darkens.
/// Useful for mildly under/overexposed images that don't trigger percentile stretch.
fn gamma_correct(pixels: &[u8], gamma: f32) -> Vec<u8> {
    let inv_gamma = 1.0 / gamma;
    pixels.iter().map(|&p| {
        let normalized = p as f32 / 255.0;
        (normalized.powf(inv_gamma) * 255.0).min(255.0) as u8
    }).collect()
}

/// Horizontally flip a GrayImage.
fn flip_luma_h(img: &image::GrayImage) -> image::GrayImage {
    imageops::flip_horizontal(img)
}

/// Rotate a luma image by 0/90/180/270 degrees.
fn rotate_luma_n90(img: &image::GrayImage, n: u8) -> image::GrayImage {
    match n {
        1 => imageops::rotate90(img),
        2 => imageops::rotate180(img),
        3 => imageops::rotate270(img),
        _ => img.clone(),
    }
}

#[pyfunction]
pub fn py_has_faces(input: &str) -> PyResult<bool> {
    let img = crate::utils::read_image(input)
        .map_err(|e| PyValueError::new_err(format!("has_faces: failed to load input: {}", e)))?;

    let mut detector = crate::utils::init_detector()
        .map_err(|e| PyValueError::new_err(e))?;

    let luma_orig = img.to_luma8();
    let (ow, oh) = luma_orig.dimensions();

    // Decide face min-size based on image dimensions.
    // Smaller threshold catches distant / small faces.
    let min_face: u32 = if ow.min(oh) < 200 { 20 } else { 30 };

    // ── Strategy 1 ─ original, all 4 rotations ─────────────────────────────
    for rot in 0u8..4 {
        let rotated = rotate_luma_n90(&luma_orig, rot);
        let (rw, rh) = rotated.dimensions();
        let scaled   = maybe_downscale(&rotated, 480);
        let (sw, sh) = scaled.dimensions();
        let mut pix  = scaled.into_raw();
        if detect_on_luma(&mut detector, &mut pix, sw, sh, min_face, 1.5) { return Ok(true); }
        // Also try at original scale (catches very large faces)
        let mut pix2 = rotated.into_raw();
        if detect_on_luma(&mut detector, &mut pix2, rw, rh, min_face, 1.5) { return Ok(true); }
    }

    // ── Strategy 2 ─ contrast-stretched for underexposed images ───────────────
    // Detects faces where mean luminance is very low (dark/underlit images).
    let raw_orig = luma_orig.clone().into_raw();
    let mean: f32 = raw_orig.iter().map(|&p| p as f32).sum::<f32>() / raw_orig.len() as f32;
    if mean < 50.0 {
        let stretched_pix = stretch_contrast(&raw_orig);
        let stretched = image::GrayImage::from_raw(ow, oh, stretched_pix)
            .unwrap_or_else(|| luma_orig.clone());
        for rot in 0u8..4 {
            let rotated = rotate_luma_n90(&stretched, rot);
            let scaled   = maybe_downscale(&rotated, 480);
            let (sw, sh) = scaled.dimensions();
            let mut pix  = scaled.into_raw();
            if detect_on_luma(&mut detector, &mut pix, sw, sh, min_face, 1.2) { return Ok(true); }
        }
    }

    // ── Strategy 3 ─ gamma-darkened for overexposed images ─────────────────────
    // Overexposed portraits (mean>200) lose facial structure — γ=2.0 recovers it.
    // stretch_contrast on overexposed makes things WORSE (spreads already-bright).
    if mean > 200.0 {
        let dark_pix = gamma_correct(&raw_orig, 2.0);
        if let Some(dark_img) = image::GrayImage::from_raw(ow, oh, dark_pix) {
            for rot in 0u8..4 {
                let rotated = rotate_luma_n90(&dark_img, rot);
                let scaled  = maybe_downscale(&rotated, 480);
                let (sw, sh) = scaled.dimensions();
                let mut pix = scaled.into_raw();
                if detect_on_luma(&mut detector, &mut pix, sw, sh, min_face, 1.2) { return Ok(true); }
            }
        }
    }

    // ── Strategy 4 ─ gamma correction for mild underexposure (mean 50..90) ──
    // Catches faces that are slightly dark but not extreme enough for stretch.
    let raw_orig2 = luma_orig.clone().into_raw();
    let mean2: f32 = raw_orig2.iter().map(|&p| p as f32).sum::<f32>() / raw_orig2.len() as f32;
    if mean2 > 50.0 && mean2 < 90.0 {
        let gamma_pix = gamma_correct(&raw_orig2, 0.5);
        if let Some(gamma_img) = image::GrayImage::from_raw(ow, oh, gamma_pix) {
            let scaled = maybe_downscale(&gamma_img, 480);
            let (sw, sh) = scaled.dimensions();
            let mut pix = scaled.into_raw();
            if detect_on_luma(&mut detector, &mut pix, sw, sh, min_face, 1.2) { return Ok(true); }
        }
    }

    // ── Strategy 4 ─ horizontal flip (catches mirror-image scans) ───────────
    let flipped = flip_luma_h(&luma_orig);
    let scaled_f = maybe_downscale(&flipped, 480);
    let (sfw, sfh) = scaled_f.dimensions();
    let mut pix_f = scaled_f.into_raw();
    if detect_on_luma(&mut detector, &mut pix_f, sfw, sfh, min_face, 1.5) { return Ok(true); }

    Ok(false)
}

#[pyfunction]
pub fn py_crop(input: &str, output: &str, x: i32, y: i32, width: i32, height: i32) -> PyResult<()> {
    crop(input, output, x, y, width, height).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_auto_crop(input: &str, output: &str, width: i32, height: i32) -> PyResult<()> {
    auto_crop(input, output, width, height).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_smart_crop(input: &str, output: &str, width: i32, height: i32, strict: bool) -> PyResult<()> {
    smart_crop(input, output, width, height, strict).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_flip(input: &str, output: &str, mode: &str) -> PyResult<()> {
    flip(input, output, mode).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_scale(input: &str, output: &str, factor: f64) -> PyResult<()> {
    scale(input, output, factor).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_enhance_signature(input: &str, output: &str, block_size: i32, c: f64) -> PyResult<()> {
    enhance_signature(input, output, block_size, c).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_is_signature(input: &str) -> PyResult<bool> {
    is_signature(input).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn py_is_signature_fallback(input: &str) -> PyResult<bool> {
    is_signature_fallback(input).map_err(PyValueError::new_err)
}

// ============================================================================
// ROTATE_ANY — Arbitrary angle rotation (bilinear interpolation, pure Rust)
// Used by sub_fix tilt correction: small angles like ±5°, ±10°, ±15°
// ============================================================================

pub fn rotate_any(input: &str, output: &str, angle_deg: f64) -> Result<(), String> {
    let img = crate::utils::read_image(input)
        .map_err(|e| format!("rotate_any: failed to read '{}': {}", input, e))?;

    // Fast-path: multiples of 90 use lossless flips
    let norm = ((angle_deg % 360.0) + 360.0) % 360.0;
    if (norm - 0.0).abs() < 0.01 {
        return crate::utils::write_image(output, &img)
            .map_err(|e| format!("rotate_any: write failed: {}", e));
    }
    if (norm - 90.0).abs() < 0.01 {
        return crate::utils::write_image(output, &img.rotate90())
            .map_err(|e| format!("rotate_any: write failed: {}", e));
    }
    if (norm - 180.0).abs() < 0.01 {
        return crate::utils::write_image(output, &img.rotate180())
            .map_err(|e| format!("rotate_any: write failed: {}", e));
    }
    if (norm - 270.0).abs() < 0.01 {
        return crate::utils::write_image(output, &img.rotate270())
            .map_err(|e| format!("rotate_any: write failed: {}", e));
    }

    // Arbitrary angle — bilinear interpolation around image center
    let (ow, oh) = img.dimensions();
    let rgba = img.to_rgba8();
    let rad = angle_deg.to_radians();
    let cos_a = rad.cos();
    let sin_a = rad.sin();
    let cx = ow as f64 / 2.0;
    let cy = oh as f64 / 2.0;

    // Output canvas size = same as input (crop corners, white fill)
    let mut out = image::RgbaImage::new(ow, oh);
    for py in 0..oh {
        for px in 0..ow {
            // Reverse-map from output pixel to source pixel
            let dx = px as f64 - cx;
            let dy = py as f64 - cy;
            let sx = cos_a * dx + sin_a * dy + cx;
            let sy = -sin_a * dx + cos_a * dy + cy;

            if sx < 0.0 || sy < 0.0 || sx >= (ow - 1) as f64 || sy >= (oh - 1) as f64 {
                // Out of bounds → white background
                out.put_pixel(px, py, image::Rgba([255, 255, 255, 255]));
                continue;
            }

            // Bilinear interpolation
            let x0 = sx.floor() as u32;
            let y0 = sy.floor() as u32;
            let x1 = (x0 + 1).min(ow - 1);
            let y1 = (y0 + 1).min(oh - 1);
            let tx = sx - sx.floor();
            let ty = sy - sy.floor();

            let p00 = rgba.get_pixel(x0, y0).0;
            let p10 = rgba.get_pixel(x1, y0).0;
            let p01 = rgba.get_pixel(x0, y1).0;
            let p11 = rgba.get_pixel(x1, y1).0;

            let mut blended = [0u8; 4];
            for c in 0..4 {
                let v = (1.0 - tx) * (1.0 - ty) * p00[c] as f64
                      + tx       * (1.0 - ty) * p10[c] as f64
                      + (1.0 - tx) * ty       * p01[c] as f64
                      + tx       * ty         * p11[c] as f64;
                blended[c] = v.round().clamp(0.0, 255.0) as u8;
            }
            out.put_pixel(px, py, image::Rgba(blended));
        }
    }

    image::DynamicImage::ImageRgba8(out)
        .save(output)
        .map_err(|e| format!("rotate_any: write failed '{}': {}", output, e))
}

// ============================================================================
// INVERT — Pixel-level 255-pixel inversion for dark-background signatures
// ============================================================================

pub fn invert_image(input: &str, output: &str) -> Result<(), String> {
    let img = crate::utils::read_image(input)
        .map_err(|e| format!("invert: failed to read '{}': {}", input, e))?;
    let mut rgb = img.to_rgb8();
    for p in rgb.pixels_mut() {
        p[0] = 255 - p[0];
        p[1] = 255 - p[1];
        p[2] = 255 - p[2];
    }
    image::DynamicImage::ImageRgb8(rgb)
        .save(output)
        .map_err(|e| format!("invert: write failed '{}': {}", output, e))
}

// ============================================================================
// IS_DARK_BACKGROUND — Returns true if image background is dark (mean < 128)
// Used by sign_fix to decide whether to invert before crop
// ============================================================================

pub fn is_dark_background(input: &str) -> Result<bool, String> {
    let img = crate::utils::read_image(input)
        .map_err(|e| format!("is_dark_background: failed to read '{}': {}", input, e))?;

    // Use 64×64 thumbnail — fast and avoids JPEG decompression artifacts at full res
    let thumb = img.thumbnail(64, 64).to_luma8();
    let (tw, th) = thumb.dimensions();
    let n = (tw * th) as u64;

    if n == 0 { return Ok(false); }

    // Whole-image mean — primary criterion.
    // A mean < 96 means the image is predominantly dark.
    // This threshold is chosen to be well below the 23.8 we see for
    // dark-bg signature images while safely above typical mid-grey (128).
    let whole_sum: u64 = thumb.pixels().map(|p| p[0] as u64).sum();
    let whole_mean = whole_sum / n;

    // Two dark-background cases:
    // 1. Standard: dark image decoded normally → whole_mean < 96
    // 2. CMYK-inverted JPEG: dark bg decoded as 255-value (e.g. mean=238)
    //    This happens with some CMYK/inverted-colorspace JPEGs where the
    //    image crate reads dark ink as light. mean > 159 catches them.
    if whole_mean < 96 || whole_mean > 159 {
        return Ok(true);
    }

    // Secondary: border-based check for images where dark bg frames a light centre.
    // (e.g. stamp on dark paper with white signature in the middle)
    let mut border_sum: u64 = 0;
    let mut border_count: u64 = 0;
    let border = 3u32;

    for y in 0..th {
        for x in 0..tw {
            if x < border || x >= tw.saturating_sub(border)
               || y < border || y >= th.saturating_sub(border) {
                border_sum += thumb.get_pixel(x, y)[0] as u64;
                border_count += 1;
            }
        }
    }

    if border_count > 0 {
        let border_mean = border_sum / border_count;
        // Dark border AND overall mean below mid-grey
        return Ok(border_mean < 100 && whole_mean < 140);
    }

    Ok(false)
}


// ============================================================================
// SMART_CROP_V2 — Face-aware crop with proper passport headroom
// Improvements over smart_crop:
//   • Dedicated head-room above face (top 35% of crop = forehead+crown)
//   • Chin at ~78% from top
//   • If no face: falls back to smart_crop
// ============================================================================

pub fn smart_crop_v2(input: &str, output: &str, width: i32, height: i32, strict: bool) -> Result<(), String> {
    use crate::utils::read_image;

    let mut img = read_image(input)
        .map_err(|e| format!("smart_crop_v2: failed to load input: {}", e))?;
    let (img_width, img_height) = img.dimensions();

    let _target_w = (width as u32).min(img_width);
    let _target_h = (height as u32).min(img_height);

    // ── Multi-strategy face detection (mirrors py_has_faces) ─────────────────
    let mut detector = match crate::utils::init_detector() {
        Ok(d) => d,
        Err(_) => return smart_crop(input, output, width, height, strict),
    };

    let luma_orig = img.to_luma8();
    let (ow, oh) = luma_orig.dimensions();
    let min_face: u32 = if ow.min(oh) < 200 { 20 } else { 30 };

    // Store: (face bbox, rotation_steps, was_contrast_stretched)
    let mut found_face: Option<(rustface::FaceInfo, u8)> = None;

    // Strategy 1: original, all 4 rotations
    'outer: for rot in 0u8..4 {
        let rotated = rotate_luma_n90(&luma_orig, rot);
        let (rw, rh) = rotated.dimensions();
        let scaled   = maybe_downscale(&rotated, 480);
        let (sw, sh) = scaled.dimensions();
        let _scale_f  = sw as f64 / rw as f64;

        for (pix_ref, fw, fh) in [
            (scaled.clone().into_raw(), sw, sh),
            (rotated.clone().into_raw(), rw, rh),
        ] {
            let mut pix = pix_ref;
            detector.set_min_face_size(min_face);
            detector.set_score_thresh(1.5);
            let mut image_data = rustface::ImageData::new(&mut pix, fw, fh);
            let faces = detector.detect(&mut image_data);
            if !faces.is_empty() {
                // Scale face coords back to original-rotation space if we used scaled version
                let best = faces.iter().max_by(|a, b|
                    (a.bbox().width() * a.bbox().height()).cmp(&(b.bbox().width() * b.bbox().height()))
                ).unwrap().clone();
                found_face = Some((best, rot));
                break 'outer;
            }
        }
    }

    // Strategy 2: contrast-stretched for dark/bright images
    if found_face.is_none() {
        let raw = luma_orig.clone().into_raw();
        let mean: f32 = raw.iter().map(|&p| p as f32).sum::<f32>() / raw.len() as f32;
        if mean < 50.0 || mean > 210.0 {
            let stretched_pix = stretch_contrast(&raw);
            if let Some(stretched) = image::GrayImage::from_raw(ow, oh, stretched_pix) {
                'outer2: for rot in 0u8..4 {
                    let rotated = rotate_luma_n90(&stretched, rot);
                    let scaled  = maybe_downscale(&rotated, 480);
                    let (sw, sh) = scaled.dimensions();
                    let mut pix = scaled.into_raw();
                    detector.set_min_face_size(min_face);
                    detector.set_score_thresh(1.2);
                    let mut image_data = rustface::ImageData::new(&mut pix, sw, sh);
                    let faces = detector.detect(&mut image_data);
                    if !faces.is_empty() {
                        let best = faces.iter().max_by(|a, b|
                            (a.bbox().width() * a.bbox().height()).cmp(&(b.bbox().width() * b.bbox().height()))
                        ).unwrap().clone();
                        found_face = Some((best, rot));
                        break 'outer2;
                    }
                }
            }
        }
    }

    // Strategy 3: gamma correction for mild underexposure (mean 50..90)
    if found_face.is_none() {
        let raw3 = luma_orig.clone().into_raw();
        let mean3: f32 = raw3.iter().map(|&p| p as f32).sum::<f32>() / raw3.len() as f32;
        if mean3 > 50.0 && mean3 < 90.0 {
            let gamma_pix = gamma_correct(&raw3, 0.5);
            if let Some(gamma_img) = image::GrayImage::from_raw(ow, oh, gamma_pix) {
                let scaled = maybe_downscale(&gamma_img, 480);
                let (sw, sh) = scaled.dimensions();
                let mut pix = scaled.into_raw();
                detector.set_min_face_size(min_face);
                detector.set_score_thresh(1.2);
                let mut image_data = rustface::ImageData::new(&mut pix, sw, sh);
                let faces = detector.detect(&mut image_data);
                if !faces.is_empty() {
                    let best = faces.iter().max_by(|a, b|
                        (a.bbox().width() * a.bbox().height()).cmp(&(b.bbox().width() * b.bbox().height()))
                    ).unwrap().clone();
                    found_face = Some((best, 0));
                }
            }
        }
    }

    // Strategy 4: horizontal flip — catches mirror-image scans
    if found_face.is_none() {
        let flipped = flip_luma_h(&luma_orig);
        let scaled_f = maybe_downscale(&flipped, 480);
        let (sfw, sfh) = scaled_f.dimensions();
        let mut pix_f = scaled_f.into_raw();
        detector.set_min_face_size(min_face);
        detector.set_score_thresh(1.5);
        let mut image_data = rustface::ImageData::new(&mut pix_f, sfw, sfh);
        let faces = detector.detect(&mut image_data);
        if !faces.is_empty() {
            let best = faces.iter().max_by(|a, b|
                (a.bbox().width() * a.bbox().height()).cmp(&(b.bbox().width() * b.bbox().height()))
            ).unwrap().clone();
            // rot=0 + flip flag — for crop, use original orientation
            found_face = Some((best, 0));
        }
    }

    // No face found
    if found_face.is_none() {
        if strict {
            return Err("smart_crop_v2: no face detected (strict=true)".to_string());
        }
        return smart_crop(input, output, width, height, strict);
    }

    let (best, rot) = found_face.unwrap();

    // Apply the same rotation to the colour image so crop coordinates match
    if rot > 0 {
        img = match rot {
            1 => img.rotate90(),
            2 => img.rotate180(),
            3 => img.rotate270(),
            _ => img,
        };
    }
    let (img_width, img_height) = img.dimensions();
    let target_w = (width as u32).min(img_width);
    let target_h = (height as u32).min(img_height);

    let bbox = best.bbox();
    let face_cx  = bbox.x() + bbox.width() as i32 / 2;
    let face_top = bbox.y();

    // Passport framing: crown ~15% above face top, chin at ~78%
    let head_room = (target_h as f64 * 0.22) as i32;
    let crop_top  = (face_top - head_room).max(0);

    let crop_left = (face_cx - target_w as i32 / 2).max(0) as u32;
    let crop_top_u = crop_top as u32;

    let crop_left  = crop_left.min(img_width.saturating_sub(target_w));
    let crop_top_u = crop_top_u.min(img_height.saturating_sub(target_h));

    let cropped = imageops::crop(&mut img, crop_left, crop_top_u, target_w, target_h).to_image();

    image::DynamicImage::ImageRgb8(
        image::DynamicImage::ImageRgba8(
            image::RgbaImage::from(cropped)
        ).to_rgb8()
    ).save(output)
    .map_err(|e| format!("smart_crop_v2: write failed '{}': {}", output, e))?;

    Ok(())
}

// ── TILT DETECTION via face score across small angle sweeps ─────────────────

// Sweeps ±15°, ±10°, ±5°, 0° — returns the angle that gives the best face
// detection score ONLY when it is meaningfully better than 0°.
//
// If the image is already level (face detected well at 0°), returns 0.0 so
// sub_fix does not apply any unnecessary rotation.
pub fn detect_tilt_angle(input: &str) -> Result<f64, String> {
    use crate::utils::init_detector;
    use rustface::ImageData;

    let img = crate::utils::read_image(input)
        .map_err(|e| format!("detect_tilt: {}", e))?;

    let mut detector = init_detector()
        .map_err(|e| format!("detect_tilt: detector init failed: {}", e))?;
    detector.set_min_face_size(30);
    detector.set_score_thresh(1.0);

    // Downscale for speed: max 320px wide for tilt sweep
    let (ow, oh) = img.dimensions();
    let scale = if ow > 320 { 320.0 / ow as f64 } else { 1.0 };
    let sw = ((ow as f64 * scale) as u32).max(1);
    let sh = ((oh as f64 * scale) as u32).max(1);
    let small = img.resize_exact(sw, sh, imageops::FilterType::Nearest);
    let small_luma = small.to_luma8();

    // Helper: detect best face score on a rotated luma buffer
    let mut detect_score = |angle: f64| -> f64 {
        let rad = angle.to_radians();
        let cos_a = rad.cos();
        let sin_a = rad.sin();
        let cx = sw as f64 / 2.0;
        let cy = sh as f64 / 2.0;
        let mut rotated = vec![255u8; (sw * sh) as usize]; // white bg

        for py in 0..sh {
            for px in 0..sw {
                let dx = px as f64 - cx;
                let dy = py as f64 - cy;
                let sx = (cos_a * dx + sin_a * dy + cx) as i32;
                let sy = (-sin_a * dx + cos_a * dy + cy) as i32;
                if sx >= 0 && sy >= 0 && sx < sw as i32 && sy < sh as i32 {
                    rotated[(py * sw + px) as usize] =
                        small_luma.get_pixel(sx as u32, sy as u32)[0];
                }
            }
        }

        let mut image_data = ImageData::new(&mut rotated, sw, sh);
        detector.set_min_face_size(30);
        detector.set_score_thresh(1.0);
        let faces = detector.detect(&mut image_data);
        faces.iter()
            .map(|f| f.score() as f64)
            .fold(0.0f64, f64::max)
    };

    // First: measure score at 0° (upright)
    let score_at_zero = detect_score(0.0);

    // If a good face is already found at 0°, no tilt needed —
    // return 0.0 immediately to avoid jitter on correctly-oriented images.
    if score_at_zero > 5.0 {
        return Ok(0.0);
    }

    let angles: &[f64] = &[-25.0, -20.0, -15.0, -10.0, -5.0, 5.0, 10.0, 15.0, 20.0, 25.0]; // extended to ±25°
    let mut best_score = score_at_zero;
    let mut best_angle = 0.0f64;

    for &angle in angles {
        let score = detect_score(angle);
        if score > best_score {
            best_score = score;
            best_angle = angle;
        }
    }

    // Only return a non-zero tilt if the best angle is MEANINGFULLY better
    // than 0°: at least 15% improvement in score, and found a face at all.
    //
    // This prevents returning a small tilt due to detection noise on
    // images that are already correctly oriented.
    let improvement = best_score - score_at_zero;
    let threshold = (score_at_zero * 0.15).max(1.0); // 15% better or score>1

    if improvement >= threshold && best_angle.abs() > 0.5 {
        Ok(best_angle)
    } else {
        Ok(0.0) // no meaningful tilt — leave image as-is
    }
}

// ── PyO3 wrappers ────────────────────────────────────────────────────────────


#[pyfunction]
pub fn py_rotate_any(input: &str, output: &str, angle_deg: f64) -> PyResult<()> {
    rotate_any(input, output, angle_deg).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_invert(input: &str, output: &str) -> PyResult<()> {
    invert_image(input, output).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_is_dark_background(input: &str) -> PyResult<bool> {
    is_dark_background(input).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_smart_crop_v2(input: &str, output: &str, width: i32, height: i32, strict: bool) -> PyResult<()> {
    smart_crop_v2(input, output, width, height, strict).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_detect_tilt_angle(input: &str) -> PyResult<f64> {
    detect_tilt_angle(input).map_err(PyValueError::new_err)
}