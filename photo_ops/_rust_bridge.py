import os
import sys

_rust_module = None

if sys.platform == "win32" and os.path.exists(r"C:\vcpkg\installed\x64-windows\bin"):
    os.add_dll_directory(r"C:\vcpkg\installed\x64-windows\bin")

try:
    from . import _rust as _inner

    _rust_module = _inner
except (ImportError, ValueError):
    try:
        import _rust as _direct

        _rust_module = _direct
    except ImportError:
        pass


def _validate_runtime_bindings():
    """Validate that required Rust bindings are present in the loaded module."""
    required = (
        "py_to_gray",
        "py_resize",
        "py_info",
        "py_rotate",
        "py_fix_orientation",
        "py_match_orientation",
        "py_crop",
        "py_auto_crop",
        "py_smart_crop",
        "py_flip",
        "py_scale",
        "py_enhance_signature",
        "py_is_signature",
        "py_is_signature_fallback",
        "py_has_faces",
        "py_blur",
        "py_sharpen",
        "py_edge",
        "py_edge_shadow",
        "py_blur_background",
        "py_brightness",
        "py_saturation",
        "py_tint",
        "py_batch_resize",
        "py_batch_process",
    )
    missing_required = [name for name in required if name not in globals()]
    if missing_required:
        raise RuntimeError(
            "Rust core is missing required bindings: "
            + ", ".join(sorted(missing_required))
            + ". Rebuild with: maturin develop --release"
        )

    optional = (
        # Phase 5 — new IP_final build (available after Rust rebuild)
        "py_rotate_any",
        "py_invert",
        "py_is_dark_background",
        "py_smart_crop_v2",
        "py_detect_tilt_angle",
        # Phase 6 — full build with extra features
        "py_color_grade",
        "py_draw_rect",
        "py_add_text",
        "py_compress",
        "py_ai_upscale",
        "py_deblur",
        "py_remove_bg_v2",
        "py_saliency_crop",
        "py_extract_frames",
        "py_extract_frames_ex",
        "py_morphology",
        "py_match_to_reference",
        "py_enhance",
        "py_resolve_url",
        "py_resolve_folder",
        "py_auth_status",
        "py_refresh_gcp_token",
        "py_detect_face",
        "py_remove_background",
        "py_detect_objects",
        "py_image_similarity",
    )
    missing_optional = [name for name in optional if name not in globals()]
    if missing_optional and os.getenv("PHOTO_OPS_VERBOSE"):
        import sys
        if sys.stdout and hasattr(sys.stdout, 'write'):
            try:
                print(
                    "[photo_ops] Optional bindings not in this build: "
                    + ", ".join(sorted(missing_optional))
                )
            except Exception:
                pass


_exposed_names = []
if _rust_module is not None:
    # Expose everything from Rust module to this module's globals
    for _name in dir(_rust_module):
        if not _name.startswith("_") or _name == "_dummy_health_check":
            globals()[_name] = getattr(_rust_module, _name)
            _exposed_names.append(_name)

    _validate_runtime_bindings()

__all__ = _exposed_names
