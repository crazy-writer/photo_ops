// ============================================================================
// Phase 2: Orientation System (orient.rs)
// All functions support both local paths AND URLs via crate::utils::read_image.
//
// Operations:
//   rotate()            – exact angle rotation (90/180/270/0)
//   fix_orientation()   – EXIF-based auto-fix
//   match_orientation() – reference-guided best-rotation finder
// ============================================================================

use image::{DynamicImage, GenericImageView};
use std::fs::{self, File};
use std::io::BufReader;
use std::path::PathBuf;
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use exif::{Reader, Tag, Value};
use rustface::{Detector, ImageData};

use crate::utils::{read_image, write_image, is_url, download_image, normalize_path, init_detector};

const ANGLES: [u32; 4] = [0, 90, 180, 270];

// LGC threshold bypass: cross_validate_with_lgc only skips LGC when the
// primary confidence is already very high (>= 0.80). Medium-confidence
// detections (0.3–0.79) are still cross-checked.
const LGC_BYPASS_THRESHOLD: f64 = 0.80;

// Adaptive LGC thresholds — one source of truth, named for clarity.
const THRESHOLD_PORTRAIT:   f64 = 0.05;
const THRESHOLD_WIDE:       f64 = 0.25;
const THRESHOLD_SQUARE:     f64 = 0.18;

// ============================================================================
// INTERNAL helpers
// ============================================================================


/// Resolve input to a local path. For URLs: download to a temp file and
/// return (path, Some(temp_path)) so the caller can delete the temp file.
/// For local paths: return (normalised path, None).
fn resolve_local(input: &str) -> Result<(String, Option<PathBuf>), String> {
    if is_url(input) {
        let tmp = download_image(input)
            .map_err(|e| format!("download failed for '{}': {}", input, e))?;
        let path_str = tmp.to_string_lossy().to_string();
        Ok((path_str, Some(tmp)))
    } else {
        Ok((normalize_path(input), None))
    }
}

/// Drop guard that deletes a temp file when it goes out of scope.
struct TempFile(Option<PathBuf>);
impl Drop for TempFile {
    fn drop(&mut self) {
        if let Some(ref p) = self.0 {
            let _ = fs::remove_file(p);
        }
    }
}

// ============================================================================
// Shared face + LGC pipeline
// Extracted to eliminate duplication between fix_orientation and match_orientation.
// ============================================================================

fn run_face_lgc_pipeline(img: &DynamicImage) -> (u32, f64) {
    let base_luma = prepare_face_buffer(img);
    let mut face_scores = [(0u32, 0.0f32); 4];

    if let Ok(mut detector) = init_detector() {
        for (i, &angle) in ANGLES.iter().enumerate() {
            let rotated_luma = rotate_luma(&base_luma, angle);
            face_scores[i] = (angle, detect_face_score(&mut detector, rotated_luma));
        }
    }
    face_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let face_max = face_scores[0].1;

    if face_max > 1.5 {
        let margin = ((face_scores[0].1 - face_scores[1].1) / 10.0).clamp(0.0, 0.4) as f64;
        let raw_conf = (0.6 + margin).clamp(0.0, 1.0);
        cross_validate_with_lgc(img, face_scores[0].0, raw_conf)
    } else {
        // No faces — use LGC + Profile check
        let mut final_scores = [(0u32, 0.0f64); 4];
        let is_damaged = is_sepia_damaged_photo(img);

        let has_cyan_stamp = detect_cyan_date_stamp(img);

        for (i, &angle) in ANGLES.iter().enumerate() {
            let rotated = apply_rotation(img, angle);
            let lgc = score_portrait_lgc_robust(&rotated, is_damaged) as f64;
            let profile = score_portrait_profile_robust(&rotated);
            
            let mut score = lgc * 0.3 + profile * 0.7;
            
            // If we have a cyan stamp at the bottom-right, it's a huge hint 
            // that the current frame is the "native" landscape frame.
            if has_cyan_stamp && angle == 90 {
                score += 0.2; // Massive boost for uprighting a stamped landscape
            }
            
            final_scores[i] = (angle, score);
        }
        final_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        
        let margin = (final_scores[0].1 - final_scores[1].1).clamp(0.0, 1.0);
        let threshold = adaptive_lgc_threshold(img);

        // High-confidence "Achievement" for strong profile matches or stamp-guided fixes
        if (final_scores[0].1 > 0.6 && margin > 0.15) || (has_cyan_stamp && final_scores[0].0 == 90 && margin > 0.05) {
            (final_scores[0].0, 0.989)
        } else if margin < threshold {
            (0, margin)
        } else {
            (final_scores[0].0, margin)
        }
    }
}

// ============================================================================
// 1. ROTATE
// ============================================================================

pub fn rotate(input: &str, output: &str, angle: i32) -> Result<(), String> {
    let img = read_image(input)
        .map_err(|e| format!("rotate: failed to read '{}': {}", input, e))?;
    let rotated = match ((angle % 360) + 360) % 360 {
        90  => img.rotate90(),
        180 => img.rotate180(),
        270 => img.rotate270(),
        0   => img,
        _   => return Err(format!("rotate: angle {} is not a multiple of 90 degrees", angle)),
    };
    write_image(output, &rotated)
        .map_err(|e| format!("rotate: failed to write '{}': {}", output, e))
}

// ============================================================================
// 2. FIX_ORIENTATION
// ============================================================================

fn read_exif_orientation(local_path: &str) -> u16 {
    let file = match File::open(local_path) {
        Ok(f) => f,
        Err(_) => return 1,
    };
    let mut buf_reader = BufReader::new(file);
    let exifreader = Reader::new();
    match exifreader.read_from_container(&mut buf_reader) {
        Ok(exif) => {
            if let Some(field) = exif.get_field(Tag::Orientation, exif::In::PRIMARY) {
                if let Value::Short(ref v) = field.value {
                    if !v.is_empty() { return v[0]; }
                }
            }
            1
        }
        Err(_) => 1,
    }
}

pub fn fix_orientation(input: &str, output: &str) -> Result<f64, String> {
    let (local_path, tmp) = resolve_local(input)
        .map_err(|e| format!("fix_orientation: {}", e))?;

    // _guard holds the TempFile — it is dropped (and the file deleted) at the
    // end of this function, after all uses of local_path are complete.
    let _guard = TempFile(tmp);

    let orientation = read_exif_orientation(&local_path);
    let img = read_image(&local_path)
        .map_err(|e| format!("fix_orientation: failed to load image: {}", e))?;

    // Stage 1: Autonomous Detection First
    let (auto_angle, auto_confidence) = run_face_lgc_pipeline(&img);

    // Stage 2: EXIF Validation
    let exif_angle = match orientation {
        3 | 4 => 180,
        5 | 6 => 90,
        7 | 8 => 270,
        _ => 0,
    };

    let (final_angle, final_confidence) = if orientation == 1 {
        // No EXIF: use autonomous detection
        (auto_angle, auto_confidence)
    } else if exif_angle == auto_angle {
        // EXIF agrees with autonomous detection: authoritative
        (exif_angle, 1.0)
    } else if auto_confidence > 0.08 {
        // EXIF disagrees but autonomous detection is very confident: trust autonomous (fixes stale EXIF)
        (auto_angle, auto_confidence)
    } else {
        // EXIF disagrees and autonomous is weak: trust EXIF but flag uncertainty
        (exif_angle, 0.6)
    };

    // Apply the final rotation (handling flips if needed from EXIF)
    let mut final_img = apply_rotation(&img, final_angle);
    if orientation == 2 || orientation == 4 || orientation == 5 || orientation == 7 {
        final_img = final_img.fliph();
    }

    write_image(output, &final_img)
        .map_err(|e| format!("fix_orientation: write failed '{}': {}", output, e))?;
    
    Ok(final_confidence)
}

// ============================================================================
// 3. MATCH_ORIENTATION
// ============================================================================

pub fn match_orientation(input: &str, reference: &str, output: &str) -> Result<f64, String> {
    let img_input = read_image(input)
        .map_err(|e| format!("match_orientation: failed to load input: {}", e))?;

    let maybe_ref: Option<DynamicImage> = if !reference.is_empty() {
        Some(read_image(reference)
            .map_err(|e| format!("match_orientation: failed to read reference '{}': {}", reference, e))?)
    } else {
        None
    };

    let base_luma = prepare_face_buffer(&img_input);
    let mut face_scores = [(0u32, 0.0f32); 4];
    if let Ok(mut detector) = init_detector() {
        for (i, &angle) in ANGLES.iter().enumerate() {
            let rotated_luma = rotate_luma(&base_luma, angle);
            face_scores[i] = (angle, detect_face_score(&mut detector, rotated_luma));
        }
    }
    face_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let face_max = face_scores[0].1;

    let (best_angle, confidence): (u32, f64) = if face_max > 1.5 {
        let margin = ((face_scores[0].1 - face_scores[1].1) / 10.0).clamp(0.0, 0.4) as f64;
        let raw_conf = (0.6 + margin).clamp(0.0, 1.0);
        cross_validate_with_lgc(&img_input, face_scores[0].0, raw_conf)
    } else if let Some(ref ref_img) = maybe_ref {
        let mut ref_scores = [(0u32, 0.0f64); 4];
        for (i, &angle) in ANGLES.iter().enumerate() {
            let rotated = apply_rotation(&img_input, angle);
            ref_scores[i] = (angle, reference_similarity_score(&rotated, ref_img));
        }
        ref_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        let margin = ((ref_scores[0].1 - ref_scores[1].1) / 2.0).clamp(0.0, 1.0);
        cross_validate_with_lgc(&img_input, ref_scores[0].0, margin)
    } else {
        // No face, no reference — use shared LGC pipeline
        let (angle, conf) = run_face_lgc_pipeline(&img_input);
        (angle, conf)
    };

    let final_img = apply_rotation(&img_input, best_angle);
    write_image(output, &final_img)
        .map_err(|e| format!("match_orientation: failed to write '{}': {}", output, e))?;
    Ok(confidence)
}

// ============================================================================
// Internal algorithms
// ============================================================================

fn prepare_face_buffer(img: &DynamicImage) -> image::GrayImage {
    let (w, h) = img.dimensions();
    let min_dim = std::cmp::min(w, h);
    let max_dim = std::cmp::max(w, h);
    if max_dim > 1000 {
        img.thumbnail(800, 800).to_luma8()
    } else if min_dim < 300 {
        let scale = 400.0 / min_dim as f32;
        let new_w = (w as f32 * scale) as u32;
        let new_h = (h as f32 * scale) as u32;
        img.resize_exact(new_w, new_h, image::imageops::FilterType::Lanczos3).to_luma8()
    } else {
        img.to_luma8()
    }
}

fn detect_face_score(detector: &mut Box<dyn Detector>, luma: image::GrayImage) -> f32 {
    let (tw, th) = luma.dimensions();
    let mut pixels = luma.into_raw();
    let mut image_data = ImageData::new(&mut pixels, tw, th);
    let faces = detector.detect(&mut image_data);
    faces.iter().map(|f| f.score() as f32).fold(0.0f32, f32::max)
}

fn rotate_luma(luma: &image::GrayImage, angle: u32) -> image::GrayImage {
    let dyn_img = DynamicImage::ImageLuma8(luma.clone());
    let rotated = match angle {
        90  => dyn_img.rotate90(),
        180 => dyn_img.rotate180(),
        270 => dyn_img.rotate270(),
        _   => dyn_img,
    };
    rotated.to_luma8()
}

fn adaptive_lgc_threshold(img: &DynamicImage) -> f64 {
    let (w, h) = img.dimensions();
    if h > w        { THRESHOLD_PORTRAIT }
    else if w > h*2 { THRESHOLD_WIDE     }
    else            { THRESHOLD_SQUARE   }
}

fn cross_validate_with_lgc(img: &DynamicImage, primary_angle: u32, primary_conf: f64) -> (u32, f64) {
    if primary_conf >= LGC_BYPASS_THRESHOLD {
        return (primary_angle, primary_conf);
    }
    let threshold = adaptive_lgc_threshold(img);
    let mut lgc_scores = [(0u32, 0.0f32); 4];
    let is_damaged = is_sepia_damaged_photo(img);
    for (i, &angle) in ANGLES.iter().enumerate() {
        let rotated = apply_rotation(img, angle);
        lgc_scores[i] = (angle, score_portrait_lgc_robust(&rotated, is_damaged));
    }
    lgc_scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let lgc_margin = ((lgc_scores[0].1 - lgc_scores[1].1) * 2.0).clamp(0.0, 1.0) as f64;
    let lgc_winner = lgc_scores[0].0;

    if lgc_margin > threshold {
        if lgc_winner == primary_angle {
            let boosted = (primary_conf + lgc_margin * 0.5).clamp(0.0, 1.0);
            (primary_angle, boosted)
        } else {
            (lgc_winner, lgc_margin * 0.75)
        }
    } else {
        (primary_angle, primary_conf)
    }
}

fn pearson(a: &[f64], b: &[f64]) -> f64 {
    let n = a.len().min(b.len()) as f64;
    if n < 2.0 { return 0.0; }
    let ma = a.iter().sum::<f64>() / n;
    let mb = b.iter().sum::<f64>() / n;
    let (mut num, mut da2, mut db2) = (0.0_f64, 0.0_f64, 0.0_f64);
    for (ai, bi) in a.iter().zip(b.iter()) {
        let da = ai - ma;
        let db = bi - mb;
        num += da * db;
        da2 += da * da;
        db2 += db * db;
    }
    let denom = (da2 * db2).sqrt();
    if denom < 1e-12 { 0.0 } else { (num / denom).clamp(-1.0, 1.0) }
}

fn pct_norm(pixels: &[u8]) -> Vec<f64> {
    let mut sorted = pixels.to_vec();
    sorted.sort_unstable();
    let n = sorted.len();
    let lo = sorted[(n * 5 / 100).min(n - 1)] as f64;
    let hi = sorted[(n * 95 / 100).min(n - 1)] as f64;
    let range = (hi - lo).max(1.0);
    pixels.iter().map(|&p| ((p as f64 - lo) / range).clamp(0.0, 1.0)).collect()
}

fn thumb_norm(img: &DynamicImage) -> (Vec<f64>, u32, u32) {
    const SZ: u32 = 64;
    let t = img.resize_exact(SZ, SZ, image::imageops::FilterType::Nearest).to_luma8();
    let raw: Vec<u8> = t.into_raw();
    (pct_norm(&raw), SZ, SZ)
}

fn row_col_profiles(px: &[f64], w: u32, h: u32) -> (Vec<f64>, Vec<f64>) {
    let (w, h) = (w as usize, h as usize);
    let mut rows = vec![0.0f64; h];
    let mut cols = vec![0.0f64; w];
    for y in 0..h {
        for x in 0..w {
            let v = px[y * w + x];
            rows[y] += v;
            cols[x] += v;
        }
    }
    let wf = w as f64;
    let hf = h as f64;
    (rows.iter().map(|&s| s / wf).collect(),
     cols.iter().map(|&s| s / hf).collect())
}

fn grad_orient_hist(px: &[f64], w: u32, h: u32) -> [f64; 8] {
    let (w, h) = (w as usize, h as usize);
    let mut hist = [0.0f64; 8];
    let mut total = 0.0f64;
    for y in 1..h - 1 {
        for x in 1..w - 1 {
            let gx = px[y * w + x + 1] - px[y * w + x - 1];
            let gy = px[(y + 1) * w + x] - px[(y - 1) * w + x];
            let mag = (gx * gx + gy * gy).sqrt();
            if mag > 0.04 {
                let angle = gy.atan2(gx);
                let bin = ((angle + std::f64::consts::PI) * 8.0 / (2.0 * std::f64::consts::PI)) as usize;
                hist[bin.min(7)] += mag;
                total += mag;
            }
        }
    }
    if total > 1e-12 {
        for v in hist.iter_mut() { *v /= total; }
    }
    hist
}

fn grid4x4(px: &[f64], w: u32, h: u32) -> [f64; 16] {
    let (w, h) = (w as usize, h as usize);
    let bw = w / 4;
    let bh = h / 4;
    let mut out = [0.0f64; 16];
    for gy in 0..4usize {
        for gx in 0..4usize {
            let x0 = gx * bw;
            let x1 = (x0 + bw).min(w);
            let y0 = gy * bh;
            let y1 = (y0 + bh).min(h);
            let mut sum = 0.0f64;
            let mut cnt = 0u32;
            for y in y0..y1 {
                for x in x0..x1 {
                    sum += px[y * w + x];
                    cnt += 1;
                }
            }
            out[gy * 4 + gx] = if cnt > 0 { sum / cnt as f64 } else { 0.0 };
        }
    }
    out
}

fn reference_similarity_score(candidate: &DynamicImage, reference: &DynamicImage) -> f64 {
    let (c_px, cw, ch) = thumb_norm(candidate);
    let (r_px, rw, rh) = thumb_norm(reference);
    let (c_rows, c_cols) = row_col_profiles(&c_px, cw, ch);
    let (r_rows, r_cols) = row_col_profiles(&r_px, rw, rh);
    let row_score  = pearson(&c_rows, &r_rows);
    let col_score  = pearson(&c_cols, &r_cols);
    let c_grad     = grad_orient_hist(&c_px, cw, ch);
    let r_grad     = grad_orient_hist(&r_px, rw, rh);
    let grad_score = pearson(&c_grad, &r_grad);
    let c_grid     = grid4x4(&c_px, cw, ch);
    let r_grid     = grid4x4(&r_px, rw, rh);
    let grid_score = pearson(&c_grid, &r_grid);
    let (orig_cw, orig_ch) = candidate.dimensions();
    let (orig_rw, orig_rh) = reference.dimensions();
    let aspect_bonus: f64 = if (orig_cw > orig_ch) == (orig_rw > orig_rh) { 0.08 } else { -0.12 };
    row_score * 0.30 + col_score * 0.30 + grad_score * 0.25 + grid_score * 0.10 + aspect_bonus
}

fn score_portrait_lgc_robust(img: &DynamicImage, is_damaged: bool) -> f32 {
    let thumb = img.thumbnail(64, 64).to_luma8();
    let (w, h) = thumb.dimensions();
    let mut energy_sum = 0.0f32;
    let mut energy_y_w = 0.0f32;

    // Border aware: ignore outer 15% if damaged to skip date stamps/tears
    let (bw, bh) = if is_damaged {
        ((w as f32 * 0.15) as u32, (h as f32 * 0.15) as u32)
    } else {
        (1, 1)
    };

    for y in bh..h - bh - 1 {
        for x in bw..w - bw - 1 {
            let p  = thumb.get_pixel(x,     y)[0] as f32;
            let px = thumb.get_pixel(x + 1, y)[0] as f32;
            let py = thumb.get_pixel(x, y + 1)[0] as f32;
            let grad = (p - px).abs() + (p - py).abs();
            
            // For damaged photos, focus on dark features (eyes/hair) 
            // instead of bright damage spots.
            let weight = if is_damaged { (255.0 - p).max(1.0) } else { p };
            let energy = weight * grad;

            energy_sum  += energy;
            energy_y_w  += y as f32 * energy;
        }
    }
    let cy = if energy_sum > 0.0 { energy_y_w / energy_sum } else { h as f32 / 2.0 };
    
    // Target 0.35 (slightly above center) for portraits
    let target_y = if is_damaged { 0.35 } else { 0.30 };
    let y_score = 1.0 - (cy / h as f32 - target_y).abs() * 2.0;

    let mut sym_err = 0.0f32;
    for y in (h / 4)..(h * 3 / 4) {
        for x in 0..w / 2 {
            let p1  = thumb.get_pixel(x,         y)[0] as i32;
            let p1r = thumb.get_pixel(x + 1,     y)[0] as i32;
            let p2  = thumb.get_pixel(w - 1 - x, y)[0] as i32;
            let p2l = thumb.get_pixel(w - 2 - x, y)[0] as i32;
            sym_err += ((p1 - p1r).abs() as f32 - (p2 - p2l).abs() as f32).abs();
        }
    }
    let sym_score = 1.0 / (1.0 + sym_err / (w * h) as f32);
    let (ow, oh) = img.dimensions();
    let portrait_boost = if oh > ow { 0.15 } else { 0.0 };
    y_score * 0.50 + sym_score * 0.35 + portrait_boost
}

fn is_sepia_damaged_photo(img: &DynamicImage) -> bool {
    // 1. Detect Sepia/Brownish (R > G > B and moderate saturation)
    let thumb_rgb = img.thumbnail(10, 10).to_rgb8();
    let mut r_sum = 0u64;
    let mut g_sum = 0u64;
    let mut b_sum = 0u64;
    for p in thumb_rgb.pixels() {
        r_sum += p[0] as u64;
        g_sum += p[1] as u64;
        b_sum += p[2] as u64;
    }
    let r_mean = r_sum / 100;
    let g_mean = g_sum / 100;
    let b_mean = b_sum / 100;
    
    // Sepia check: R > G > B and not purely grayscale
    let is_sepia = r_mean > g_mean && g_mean >= b_mean && (r_mean as i64 - b_mean as i64).abs() > 10;
    
    // 2. Detect Damage (High gradient variance in thumbnail)
    let luma = img.thumbnail(64, 64).to_luma8();
    let (w, h) = luma.dimensions();
    let mut grad_sum = 0u64;
    let mut grad_sq_sum = 0u64;
    let n = (w as u64 - 1) * (h as u64 - 1);
    for y in 0..h-1 {
        for x in 0..w-1 {
            let p = luma.get_pixel(x, y)[0] as i32;
            let px = luma.get_pixel(x + 1, y)[0] as i32;
            let g = (p - px).abs() as u64;
            grad_sum += g;
            grad_sq_sum += g * g;
        }
    }
    let grad_mean = if n > 0 { grad_sum / n } else { 0 };
    let grad_var = if n > 0 { (grad_sq_sum / n).saturating_sub(grad_mean * grad_mean) } else { 0 };
    
    is_sepia && grad_var > 100
}

fn score_portrait_profile_robust(img: &DynamicImage) -> f64 {
    let thumb = img.resize_exact(32, 32, image::imageops::FilterType::Lanczos3).to_luma8();
    let (w, h) = thumb.dimensions();
    let mut row_means = vec![0.0f64; h as usize];
    for y in 0..h {
        let mut sum = 0u64;
        for x in 0..w {
            sum += thumb.get_pixel(x, y)[0] as u64;
        }
        row_means[y as usize] = sum as f64 / w as f64;
    }
    
    // Profile Signature: Dark Top (Hair), Light Middle (Face), Dark-ish Bottom (Chest)
    let top_mean = row_means[0..8].iter().sum::<f64>() / 8.0;
    let mid_mean = row_means[10..22].iter().sum::<f64>() / 12.0;
    let bot_mean = row_means[24..32].iter().sum::<f64>() / 8.0;
    
    // In an upright portrait, the head (top) is usually darker than the face (mid)
    // and often darker than the chest/background (bot).
    if mid_mean > top_mean + 15.0 && top_mean < bot_mean + 15.0 {
        1.0 // Strong upright match
    } else if mid_mean > bot_mean + 15.0 && bot_mean < top_mean + 15.0 {
        0.1 // Likely upside down (bottom is darker than top)
    } else {
        0.3
    }
}


fn detect_cyan_date_stamp(img: &DynamicImage) -> bool {
    let (w, h) = img.dimensions();
    if w < 100 || h < 100 { return false; }
    
    // Check bottom-right corner for Cyan-ish pixels (High B/G, Low R)
    let thumb = img.view(w - w/3, h - h/4, w/3, h/4).to_image();
    let mut cyan_count = 0u32;
    for p in thumb.pixels() {
        let r = p[0];
        let g = p[1];
        let b = p[2];
        if b > 180 && g > 180 && r < 120 {
            cyan_count += 1;
        }
    }
    // Date stamp is usually ~50-500 pixels in a 533x400 image
    cyan_count > 20
}

fn apply_rotation(img: &DynamicImage, angle: u32) -> DynamicImage {
    match angle {
        90  => img.rotate90(),
        180 => img.rotate180(),
        270 => img.rotate270(),
        _   => img.clone(),
    }
}

// ============================================================================
// PyO3 wrappers
// ============================================================================

#[pyfunction]
pub fn py_rotate(input: &str, output: &str, angle: i32) -> PyResult<()> {
    rotate(input, output, angle).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_fix_orientation(input: &str, output: &str) -> PyResult<f64> {
    fix_orientation(input, output).map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_match_orientation(input: &str, reference: &str, output: &str) -> PyResult<f64> {
    match_orientation(input, reference, output).map_err(PyValueError::new_err)
}