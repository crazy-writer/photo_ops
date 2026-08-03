use rayon::prelude::*;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

const SUPPORTED_EXTENSIONS: &[&str] = &["jpg", "jpeg", "png", "bmp", "tiff"];

// ---------------------------------------------------------------------------
// Internal helper: collect all image paths from a folder.
// Extracted to avoid duplicating the read_dir + extension filter in every
// batch function. Returns an error if the folder does not exist.
// ---------------------------------------------------------------------------
fn collect_image_paths(folder: &str) -> Result<Vec<PathBuf>, String> {
    let path = Path::new(folder);
    if !path.exists() || !path.is_dir() {
        return Err(format!("Input folder does not exist: {}", folder));
    }
    let paths = fs::read_dir(path)
        .map_err(|e| format!("Failed to read input folder: {}", e))?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.extension()
                .and_then(|ext| ext.to_str())
                .map(|ext| SUPPORTED_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
                .unwrap_or(false)
        })
        .collect();
    Ok(paths)
}

/// Batch resize all images in a folder.
pub fn batch_resize(
    input_folder: &str,
    output_folder: &str,
    width: i32,
    height: i32,
    lock_aspect: bool,
) -> Result<(usize, usize), String> {
    let output_path = Path::new(output_folder);
    fs::create_dir_all(output_path)
        .map_err(|e| format!("Failed to create output folder: {}", e))?;

    let entries = collect_image_paths(input_folder)?;
    let total = entries.len();
    let success_count = Arc::new(AtomicUsize::new(0));

    entries.par_iter().for_each(|input_file| {
        let file_name = input_file.file_name().unwrap();
        let output_file = output_path.join(file_name);
        match resize_image(input_file, &output_file, width, height, lock_aspect) {
            Ok(_) => { success_count.fetch_add(1, Ordering::SeqCst); }
            Err(e) => eprintln!("Error processing {:?}: {}", input_file, e),
        }
    });

    Ok((success_count.load(Ordering::SeqCst), total))
}

/// Resize a single image file (no OpenCV).
fn resize_image(input: &Path, output: &Path, width: i32, height: i32, lock_aspect: bool) -> Result<(), String> {
    let img = image::open(input)
        .map_err(|e| format!("Failed to open {}: {}", input.display(), e))?;

    let (new_w, new_h) = if lock_aspect {
        let aspect = img.width() as f64 / img.height() as f64;
        if (width as f64 / height as f64) > aspect {
            ((height as f64 * aspect) as u32, height as u32)
        } else {
            (width as u32, (width as f64 / aspect) as u32)
        }
    } else {
        (width as u32, height as u32)
    };

    let resized = img.resize_exact(new_w, new_h, image::imageops::FilterType::Lanczos3);

    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Failed to create dir: {}", e))?;
    }

    resized.save(output)
        .map_err(|e| format!("Failed to save {}: {}", output.display(), e))
}

/// Batch process with a named single-step operation ("grayscale" | "resize").
pub fn batch_process(
    input_folder: &str,
    output_folder: &str,
    operation: &str,
) -> Result<(usize, usize), String> {
    let output_path = Path::new(output_folder);
    fs::create_dir_all(output_path)
        .map_err(|e| format!("Failed to create output folder: {}", e))?;

    let entries = collect_image_paths(input_folder)?;
    let total = entries.len();
    let success_count = Arc::new(AtomicUsize::new(0));

    entries.par_iter().for_each(|input_file| {
        let file_name = input_file.file_name().unwrap();
        let output_file = output_path.join(file_name);

        let result = match operation {
            "grayscale" | "to_gray" => {
                image::open(input_file)
                    .map_err(|e| format!("Failed to open: {}", e))
                    .and_then(|img| {
                        img.grayscale()
                            .save(&output_file)
                            .map_err(|e| format!("Failed to save: {}", e))
                    })
            }
            "resize" => resize_image(input_file, &output_file, 800, 600, false),
            _ => Err(format!("Unknown operation: {}", operation)),
        };

        match result {
            Ok(_) => { success_count.fetch_add(1, Ordering::SeqCst); }
            Err(e) => eprintln!("Error processing {:?}: {}", input_file, e),
        }
    });

    Ok((success_count.load(Ordering::SeqCst), total))
}

/// Batch pipeline: apply multiple steps defined in a JSON spec.
///
/// `steps_json` format (planned for Phase 4):
/// ```json
/// [
///   {"op": "resize", "width": 1920, "height": 1080, "lock_aspect": true},
///   {"op": "grayscale"}
/// ]
/// ```
///
/// Returns Err until the step parser is implemented so callers are never
/// silently given wrong output (the old behaviour was to silently hardcode
/// a resize to 800×600 regardless of the steps_json value).
pub fn batch_pipeline(
    _input_folder: &str,
    _output_folder: &str,
    _steps_json: &str,
) -> Result<(usize, usize), String> {
    Err(
        "batch_pipeline: step parsing not yet implemented. \
         Use batch_resize or batch_process for single-step operations. \
         Multi-step pipeline support is planned for Phase 4."
            .to_string(),
    )
}

// PyO3 wrappers
#[pyfunction]
pub fn py_batch_resize(
    input_folder: &str,
    output_folder: &str,
    width: i32,
    height: i32,
    lock_aspect: bool,
) -> PyResult<(usize, usize)> {
    batch_resize(input_folder, output_folder, width, height, lock_aspect)
        .map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_batch_process(
    input_folder: &str,
    output_folder: &str,
    operation: &str,
) -> PyResult<(usize, usize)> {
    batch_process(input_folder, output_folder, operation)
        .map_err(PyValueError::new_err)
}

#[pyfunction]
pub fn py_batch_pipeline(
    input_folder: &str,
    output_folder: &str,
    steps_json: &str,
) -> PyResult<(usize, usize)> {
    batch_pipeline(input_folder, output_folder, steps_json)
        .map_err(PyValueError::new_err)
}