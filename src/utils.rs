use image::DynamicImage;
use std::path::{Path, PathBuf};
use std::fs;

/// Normalize path: convert relative to absolute, handle Windows/Unix
pub fn normalize_path(path: &str) -> String {
    // Check if it's a URL
    if path.starts_with("http://") || path.starts_with("https://") {
        return path.to_string();
    }
    
    let p = Path::new(path);
    
    // If already absolute, return as-is
    if p.is_absolute() {
        return p.to_string_lossy().to_string();
    }
    
    // Convert relative path to absolute (relative to current dir)
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let absolute = current_dir.join(p);
    absolute.to_string_lossy().to_string()
}

/// Check if string is a URL
pub fn is_url(s: &str) -> bool {
    s.starts_with("http://") || s.starts_with("https://")
}

/// Download image from URL to a temporary file (pure Rust, no clang-sys)
pub fn download_image(url: &str) -> Result<PathBuf, String> {
    // Create temp file
    let temp_dir = std::env::temp_dir().join("photo_ops_downloads");
    fs::create_dir_all(&temp_dir)
        .map_err(|e| format!("Failed to create temp dir: {}", e))?;
    
    let file_name = format!("dl_{}.tmp", std::process::id());
    let temp_path = temp_dir.join(file_name);
    
    // Use PowerShell on Windows or curl on Unix for downloading
    #[cfg(target_os = "windows")]
    {
        let ps_command = format!(
            "Invoke-WebRequest -Uri '{}' -OutFile '{}' -UseBasicParsing",
            url,
            temp_path.to_string_lossy()
        );
        
        let output = std::process::Command::new("powershell")
            .args(&["-Command", &ps_command])
            .output()
            .map_err(|e| format!("Failed to run PowerShell: {}", e))?;
        
        if !output.status.success() {
            let err_msg = String::from_utf8_lossy(&output.stderr);
            return Err(format!("PowerShell download failed: {}", err_msg));
        }
    }
    
    #[cfg(not(target_os = "windows"))]
    {
        let output = std::process::Command::new("curl")
            .args(&["-L", "-o", &temp_path.to_string_lossy(), url])
            .output()
            .map_err(|e| format!("Failed to run curl: {}", e))?;
        
        if !output.status.success() {
            let err_msg = String::from_utf8_lossy(&output.stderr);
            return Err(format!("curl download failed: {}", err_msg));
        }
    }
    
    Ok(temp_path)
}

/// Read image from path or URL, return DynamicImage
pub fn read_image(input: &str) -> Result<DynamicImage, String> {
    let path = normalize_path(input);
    
    if is_url(&path) {
        // Download to temp file
        let temp_path = download_image(&path)?;
        let img = image::open(&temp_path)
            .map_err(|e| format!("Failed to read downloaded image from {}: {}", path, e))?;
        
        // Cleanup temp file
        fs::remove_file(&temp_path).ok();
        Ok(img)
    } else {
        // Read local file
        let p = Path::new(&path);
        if !p.exists() {
            return Err(format!("Input file not found: {}", path));
        }
        if !p.is_file() {
            return Err(format!("Input path exists but is not a file: {}", path));
        }

        image::open(&path)
            .map_err(|e| format!("Failed to decode image {}: {}", path, e))
    }
}

/// Write image — JPEG always at quality=100 (library default, not user-adjustable).
/// Other formats (PNG, WEBP, BMP, etc.) use their own lossless defaults.
pub fn write_image(output: &str, img: &DynamicImage) -> Result<(), String> {
    let path = normalize_path(output);

    // Create output directory if needed
    if let Some(parent) = Path::new(&path).parent() {
        if !parent.as_os_str().is_empty() && !parent.exists() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create output directory '{}': {}", parent.display(), e))?;
        }
    }

    let ext = Path::new(&path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();

    if ext == "jpg" || ext == "jpeg" {
        // Explicit quality=100 encoder — never allow default lossy compression
        use std::io::Cursor;
        use image::ImageOutputFormat;
        let mut buf: Vec<u8> = Vec::new();
        img.write_to(&mut Cursor::new(&mut buf), ImageOutputFormat::Jpeg(100))
            .map_err(|e| format!("Failed to encode JPEG (q=100) '{}': {}", path, e))?;
        fs::write(&path, &buf)
            .map_err(|e| format!("Failed to write file '{}': {}", path, e))?;
    } else {
        img.save(&path)
            .map_err(|e| format!("Failed to save image to '{}': {}", path, e))?;
    }
    Ok(())
}
/// Initialize face detector using embedded SeetaFace model.
/// The model is extracted to a temporary directory if not already present.
pub fn init_detector() -> Result<Box<dyn rustface::Detector>, String> {
    let cache_dir = std::env::temp_dir().join("photo_ops_cache");
    if !cache_dir.exists() {
        fs::create_dir_all(&cache_dir).ok();
    }
    let model_path = cache_dir.join("seeta_fd_frontal_v1.0.bin");
    if !model_path.exists() {
        let bytes = include_bytes!("../seeta_fd_frontal_v1.0.bin");
        if fs::write(&model_path, bytes).is_err() {
            return Err("Face detector: could not write model to temp dir".to_string());
        }
    }
    let mut detector = rustface::create_detector(model_path.to_str().unwrap())
        .map_err(|e| format!("Face detector init failed: {:?}", e))?;
    detector.set_min_face_size(20);
    detector.set_score_thresh(1.5);
    detector.set_pyramid_scale_factor(0.8);
    detector.set_slide_window_step(4, 4);
    Ok(detector)
}
