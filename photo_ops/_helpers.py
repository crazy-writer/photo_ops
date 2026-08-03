import sys
import uuid
from functools import wraps
from pathlib import Path

_COLOR_MAP = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
}

_IMAGE_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".avif",
    ".heic",
    ".heif",
)


def _handle_error(op_name, e):
    """Print a pretty error message to terminal and re-raise."""
    # Avoid encoding errors with special characters on Windows console
    msg = str(e).encode("ascii", errors="replace").decode("ascii")
    print(f" ERROR: {op_name} failed")
    print(f" Message: {msg}")
    raise e


class NotProcessedError(Exception):
    """Raised when an image cannot be confidently processed (low confidence /
    algorithm uncertainty) and should be moved to 'not_processed/' rather than
    'failed/' (which is reserved for hard I/O or exception errors)."""
    pass


def _check():
    core_module = sys.modules.get("photo_ops.core")
    if core_module and "py_to_gray" not in dir(core_module):
        msg = "Rust core not built. Run: maturin develop --release"
        raise RuntimeError(msg)


def _require_binding(binding_name, feature_name=None):
    """Raise a clear error if an optional Rust binding is unavailable."""
    core_module = sys.modules.get("photo_ops.core")
    if core_module and binding_name in dir(core_module):
        return
    feature = feature_name or binding_name
    raise RuntimeError(
        f"{feature} is unavailable in this build (missing Rust binding '{binding_name}'). "
        f"Rebuild with the matching feature set using: maturin develop --release"
    )


def _get_path(*args):
    """Join path parts like os.path.join, but simpler."""
    return str(Path(*args))


def _validate_positive_int(name, value):
    """Validate a required positive integer argument."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be > 0")


def _validate_non_negative_int(name, value):
    """Validate a required non-negative integer argument."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


def _validate_bool(name, value):
    """Validate a required boolean argument."""
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


def _parse_color(color, default_rgb):
    """Parse color string, hex code, or return default RGB."""
    if color and isinstance(color, str):
        color = color.strip()
        if color.startswith("#"):
            h = color.lstrip("#")
            if len(h) == 3:
                h = "".join(c*2 for c in h)
            if len(h) == 6:
                return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return _COLOR_MAP.get(color.lower(), default_rgb)
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        return tuple(color[:3])
    return default_rgb


def _is_probable_file_path(path):
    """Best-effort check: does path look like a file path (not directory)?"""
    if not isinstance(path, str):
        return False
    suffix = Path(path).suffix.lower()
    return bool(suffix and suffix in _IMAGE_EXTS)


def _bytes_support(func):
    """Decorator to support passing bytes and returning bytes via mem fs."""
    import inspect

    sig = inspect.signature(func)
    has_output_path = "output_path" in sig.parameters
    is_check_func = func.__name__ in ("sign_check", "sub_check")

    @wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        input_path = bound.arguments.get("input_path")
        output_path = bound.arguments.get("output_path") if has_output_path else None

        in_is_bytes = isinstance(input_path, bytes)
        out_is_none = has_output_path and (output_path is None) and (not is_check_func)

        if in_is_bytes or out_is_none:
            core_module = sys.modules.get("photo_ops.core")
            py_write_mem = getattr(core_module, "py_write_mem", None)
            py_read_mem = getattr(core_module, "py_read_mem", None)
            py_delete_mem = getattr(core_module, "py_delete_mem", None)

            if not (py_write_mem and py_read_mem and py_delete_mem):
                raise RuntimeError("Rust memory fs bindings not available.")

            in_uri = None
            out_uri = None

            if in_is_bytes:
                in_uri = f"mem://{uuid.uuid4().hex}"
                py_write_mem(in_uri, input_path)
                bound.arguments["input_path"] = in_uri

            if out_is_none:
                out_uri = f"mem://{uuid.uuid4().hex}.png"
                bound.arguments["output_path"] = out_uri

            try:
                res = func(*bound.args, **bound.kwargs)
                if out_is_none:
                    return py_read_mem(out_uri)
                return res
            finally:
                if in_uri:
                    py_delete_mem(in_uri)
                if out_uri:
                    py_delete_mem(out_uri)
        return func(*args, **kwargs)

    return wrapper
