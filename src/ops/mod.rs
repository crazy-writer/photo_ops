pub mod core;      // Phase 1: Uses `image` crate
pub mod orient;    // Phase 2: Orientation (pure Rust)
pub mod pixel;     // Phase 2+: Pixel adjustments (pure Rust)
pub mod transform; // Transformation logic
pub mod filter_pure; // Stylized filters (pure Rust)
// pub mod vision;    // Phase 4: Vision & Filters (OpenCV)
