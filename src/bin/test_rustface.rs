use rustface::{Detector, ImageData};
use image::{DynamicImage, GenericImageView};
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    let img_path = if args.len() > 1 { &args[1] } else { "images/rotate_sample.jpg" };
    let mut detector = rustface::create_detector("seeta_fd_frontal_v1.0.bin").unwrap();
    detector.set_min_face_size(20);
    detector.set_score_thresh(0.0); // lower thresh
    detector.set_pyramid_scale_factor(0.8);
    detector.set_slide_window_step(4, 4);

    let img = image::open(img_path).unwrap().to_luma8();
    let (width, height) = img.dimensions();
    let mut pixels = img.into_raw();
    
    let mut image_data = ImageData::new(&mut pixels, width, height);
    let faces = detector.detect(&mut image_data);
    
    println!("File: {}, Found {} faces", img_path, faces.len());
    for face in faces {
        println!("Face at {:?} with score {}", face.bbox(), face.score());
    }
}
