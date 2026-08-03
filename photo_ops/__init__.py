"""
photo_ops — High-Performance Image Intelligence for Large-Scale Data Processing.

Built on a pure-Rust core (via PyO3) for maximum throughput and low-latency
performance. Provides a seamless Python API for intelligent structural recognition,
orientation correction, signature extraction, and subject-aware cropping.

Documentation : https://crazy-writer.github.io/photo_ops/
Feedback form : https://forms.gle/CBhJVP3XdbZh1B2J7

──────────────────────────────────────────────────────────────────────────────
THREADING & GIL MODEL — IMPORTANT FOR PERFORMANCE-CRITICAL USERS
──────────────────────────────────────────────────────────────────────────────

photo_ops uses two distinct threading layers:

1. RAYON (Rust-internal multi-threading)
   • Active INSIDE a single batch operation (e.g. batch_resize, batch_process).
   • The Rust core releases the GIL while processing, enabling all CPU cores to
     work on image pixels in parallel.
   • ✅ You get true multi-core scaling automatically — no Python threads needed.

2. PYTHON GIL (Python-level loops)
   • If you call ip.gray(in, out) inside a plain Python for-loop, the GIL is
     re-acquired at every FFI boundary crossing.
   • ❌ This limits you to ~1 effective core for the loop overhead.
   • ✅ FIX: Use ip.batch_process() or ip.process() instead. These functions
     enter Rust once, spawn Rayon threads internally, and release the GIL for
     the entire batch — giving you full multi-core throughput.

Quick example — CORRECT way to batch-process at full speed:

    import photo_ops as ip

    # ✅ FAST: Rust Rayon handles parallelism; GIL released for the whole batch
    ip.batch_process("input_folder/", "output_folder/", "grayscale")

    # ✅ ALSO FAST: process() engine with workers="auto"
    ip.process({
        "input":  "scans/",
        "output": "results/",
        "ops":    ["fix_turn", "gray"],
        "workers": "auto",   # Adaptive Rayon thread pool
        "stats":  True,
    })

    # ❌ SLOW: Python loop loses multi-core benefit
    import os
    for f in os.listdir("input_folder/"):
        ip.gray(f"input_folder/{f}", f"output_folder/{f}")

──────────────────────────────────────────────────────────────────────────────
SIGNATURE INTELLIGENCE — WHAT IT DOES & WHAT IT DOESN'T
──────────────────────────────────────────────────────────────────────────────

The signature detection pipeline (sign_fix, sign_check, sub_fix, sub_check) uses an
advanced heuristic engine — NOT a neural network. This is intentional:

Pipeline stages:
  1. Exposure normalisation   (1st/99th percentile histogram stretch)
  2. CLAHE local contrast     (recovers faded ink, 8×8 tiles, clip=3.0)
  3. Otsu binarisation        (adaptive threshold on CLAHE output)
  4. Stroke-width erosion     (5×5 SE kills stamp solid borders, keeps thin ink)
  5. Connected-component CCL  (label all ink blobs)
  6. Multi-feature scoring    (aspect ratio + ink density + position + stamp rejection)

Why heuristics and NOT YOLOv8?
  The bundled YOLOv8n model is trained on COCO (80 classes: person, dog, car …).
  It has NO "signature" class. Running it for signature detection would always
  return zero detections. The heuristic engine is the correct, production-grade
  approach for this task.

Limitations (honest):
  • Complex backgrounds (security printing + stamp overlap) may need pre-cleaning.
  • For highest accuracy, pass images through enhance_signature() first.

──────────────────────────────────────────────────────────────────────────────
PLATFORM COMPATIBILITY
──────────────────────────────────────────────────────────────────────────────

photo_ops uses tract-onnx (pure Rust ONNX runtime). No C++ runtime, no
download-binaries. Pre-compiled wheels are available for:

  ✅ Windows   x64, x86
  ✅ Linux     x64 (manylinux), aarch64, armv7 (musl + glibc)
  ✅ macOS     x64, Apple Silicon (M1/M2/M3 — Universal2)

Air-gap / firewall safe: all models are embedded in the wheel (.pyd/.so).
Zero network calls at runtime.
"""

from __future__ import annotations  # Python 3.8+ compatibility for X | Y syntax

from .core import *
from .batch import process, run
from .cloud import download, resolve_folder, auth_status, refresh_gcp_token

__version__ = "0.1.0"
__homepage__ = "https://crazy-writer.github.io/photo_ops/"


__all__ = [
    # Core
    "gray",
    "resize",
    "info",
    "rotate",
    # Crop & Transform
    "crop",
    "auto_crop",
    "crop_center",
    "flip",
    "scale",
    # Adjustments
    "brightness",
    "saturation",
    "tint",
    "color_grade",
    "draw_rect",
    "add_text",
    "morphology",
    # Filters
    "blur",
    "sharpen",
    "edges",
    "edge_art",
    "portrait",
    "enhance",
    "deblur",
    "compress",
    # Frames / Animation
    "extract_frames",
    "extract_frames_ex",
    # Signature & Subject (Phase 2 — under testing)
    "enhance_signature",
    "has_faces",
    "sign_fix",
    "sign_check",
    "sub_fix",
    "sub_check",
    # Download / Cloud
    "download",
    "resolve_folder",
    "auth_status",
    "refresh_gcp_token",
    # Batch
    "batch_resize",
    "batch_process",
    # Engine
    "process",
    "run",
    # Health
    "works",
    "homepage",
    "calls",
    "install_models",
]


def calls():
    """Print all available functions in photo_ops and return the list."""
    print(f"photo_ops v{__version__} — Available operations:")
    for fn in __all__:
        if fn != "calls":
            print(f"  - {fn}()")
    return __all__
