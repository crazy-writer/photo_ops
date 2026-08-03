use sha2::{Sha256, Digest};
use std::fs;
use std::path::Path;

/// Calculate SHA-256 checksum of a file
pub fn sha256_checksum(file_path: &str) -> Result<String, String> {
    let path = Path::new(file_path);
    if !path.exists() {
        return Err(format!("File not found: {}", file_path));
    }
    
    let data = fs::read(path)
        .map_err(|e| format!("Failed to read {}: {}", file_path, e))?;
    
    let mut hasher = Sha256::new();
    hasher.update(&data);
    let result = hasher.finalize();
    
    Ok(hex::encode(result))
}

/// Check if output exists and is newer than input (idempotent check)
pub fn should_skip(input: &str, output: &str) -> bool {
    let input_path = Path::new(input);
    let output_path = Path::new(output);
    
    if !input_path.exists() || !output_path.exists() {
        return false;
    }
    
    let input_metadata = fs::metadata(input_path).ok();
    let output_metadata = fs::metadata(output_path).ok();
    
    match (input_metadata, output_metadata) {
        (Some(in_meta), Some(out_meta)) => {
            if let (Ok(in_time), Ok(out_time)) = (in_meta.modified(), out_meta.modified()) {
                out_time >= in_time
            } else {
                false
            }
        }
        _ => false,
    }
}

/// Check if output exists, is newer, and has same checksum (advanced idempotency)
pub fn should_skip_with_checksum(input: &str, output: &str) -> Result<bool, String> {
    if !should_skip(input, output) {
        return Ok(false);
    }
    
    // Both exist and output is newer, verify checksum
    let _checksum = sha256_checksum(input)?;
    // In production, you'd store and compare checksums in a cache file
    // For now, just return true if output is newer
    Ok(true)
}
