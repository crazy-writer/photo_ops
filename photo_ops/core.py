from __future__ import annotations  # Python 3.8+ compatibility for X | Y union syntax
import hashlib
import os
import threading
import time
import urllib.request
import webbrowser

from ._rust_bridge import *
from ._types import BulkResult, PathLike, WorkerSpec


def homepage() -> None:
    """
    Open the photo_ops API documentation in your default browser.

    Navigates to the GitHub Pages documentation site where every function
    is documented with full examples, bytes I/O notes, cloud usage, and
    the complete workers spec.

    Example
    -------
    >>> import photo_ops as ip
    >>> ip.homepage()         # opens browser to docs site
    >>>                       # or visit directly:
    >>> # https://crazy-writer.github.io/photo_ops/

    Returns
    -------
    None
    """
    url = "https://crazy-writer.github.io/photo_ops/"
    print(f"Opening documentation: {url}")
    webbrowser.open(url)


from ._helpers import (
    _bytes_support,
    _check,
    _handle_error,
    _parse_color,
    _require_binding,
    _validate_bool,
    _validate_non_negative_int,
    _validate_positive_int,
    NotProcessedError,
)

# â”€â”€ CHANGE 1: _decode_cloud_url â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# OLD: converted gs:// â†’ https://storage.googleapis.com/ before Rust saw it,
#      stripping all authentication that the Rust layer applies to gs:// URLs.
# NEW: only strips accidental "gsutil cp" shell command prefixes; leaves gs://,
#      s3://, az://, and ipfs:// intact so Rust can auth-handle them.
from .cloud import _expand_cloud_folder, _is_cloud_folder, _resolve_input

# Adaptive Worker Constants
CPU_PAUSE = 90
CPU_SCALE_DOWN = 75
CPU_SCALE_UP = 50
TEMP_LIMIT_C = 85.0
RAM_MIN_MB = 300
RAM_SCALE_UP_MB = 600
THERMAL_PAUSE_S = 5
MONITOR_INTERVAL = 3


def _get_cpu_temp() -> float:
    return 0.0


def get_initial_workers() -> int:
    physical_cores = os.cpu_count() or 1
    return max(min(physical_cores // 2, 8), 1)


class AdaptiveWorkerController:
    def __init__(self, initial: int, max_workers: int):
        self._workers = initial
        self._lock = threading.Lock()
        self.max_workers = max_workers
        self._stop = threading.Event()
        self._sem = threading.Semaphore(initial)
        self._thread = threading.Thread(target=self._monitor, daemon=True)

    @property
    def target_workers(self) -> int:
        with self._lock:
            return self._workers

    def acquire(self):
        self._sem.acquire()

    def release(self):
        self._sem.release()

    def _set_workers(self, new_val: int):
        with self._lock:
            old = self._workers
            new_val = max(1, min(new_val, self.max_workers))
            if new_val == old:
                return
            delta = new_val - old
            self._workers = new_val
            if delta > 0:
                for _ in range(delta):
                    self._sem.release()
            else:
                for _ in range(-delta):
                    acquired = self._sem.acquire(blocking=False)
                    if not acquired:
                        self._workers = old + delta + 1
                        break

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=15)

    def wait_if_hot(self):
        try:
            import psutil

            while not self._stop.is_set():
                if psutil.cpu_percent() > CPU_PAUSE:
                    time.sleep(1)
                else:
                    break
        except ImportError:
            pass

    def _monitor(self):
        try:
            import psutil
        except ImportError:
            while not self._stop.is_set():
                time.sleep(MONITOR_INTERVAL)
            return

        while not self._stop.is_set():
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=None)

            if mem.available < RAM_MIN_MB * 1024 * 1024 or cpu > CPU_PAUSE:
                # Scale down
                target = max(1, self.target_workers - 1)
                self._set_workers(target)
            elif mem.available > RAM_SCALE_UP_MB * 1024 * 1024 and cpu < CPU_SCALE_UP:
                # Scale up
                target = min(self.max_workers, self.target_workers + 1)
                self._set_workers(target)

            time.sleep(MONITOR_INTERVAL)


def _get_worker_setup(workers):
    """Parse *workers* into ``(max_w, controller)`` â€” universal worker-count resolver.

    Accepted forms
    --------------
    False / None / 0  â†’ serial (1 thread)
    True              â†’ 10 threads
    int > 0           â†’ exact thread count
    int < 0           â†’ max(1, cpu_count - abs(workers))  (leave N CPUs free)
    "auto"            â†’ adaptive controller (starts low, ramps up)
    "max" / "all"     â†’ all logical CPUs Ã— 1
    "N"               â†’ int(N) threads  (e.g. "4")
    "Nw" / "Nt"       â†’ int(N) threads  (e.g. "4w", "4t")
    "N%"              â†’ N% of logical CPUs  (e.g. "50%")

    Examples
    --------
    >>> _get_worker_setup(False)      # (1, None) â€” serial
    >>> _get_worker_setup(4)          # (4, None)
    >>> _get_worker_setup("auto")     # (10, <AdaptiveWorkerController>)
    >>> _get_worker_setup("max")      # (cpu_count, None)
    >>> _get_worker_setup("4w")       # (4, None)
    >>> _get_worker_setup("50%")      # (cpu_count//2, None)
    >>> _get_worker_setup(-2)         # (max(1, cpu_count-2), None)
    """
    cpu = os.cpu_count() or 4
    controller = None

    # â”€â”€ Falsy / serial â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if workers is False or workers is None or workers == 0:
        return 1, None

    # â”€â”€ True â†’ 10 threads (backward-compat) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if workers is True:
        return 10, None

    # â”€â”€ Adaptive controller â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(workers, str) and workers.lower() == "auto":
        initial = get_initial_workers()
        max_w = 10
        controller = AdaptiveWorkerController(initial=initial, max_workers=max_w)
        controller.start()
        return max_w, controller

    # â”€â”€ String forms â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(workers, str):
        s = workers.strip().lower()

        if s in ("max", "all", "full"):
            return cpu, None

        # "N%"  e.g. "50%"
        if s.endswith("%"):
            try:
                pct = float(s[:-1])
                max_w = max(1, int(cpu * pct / 100))
                return max_w, None
            except ValueError:
                pass

        # "Nw" / "Nt"  e.g. "4w", "8t"
        if s.endswith(("w", "t")):
            try:
                max_w = max(1, int(s[:-1]))
                return max_w, None
            except ValueError:
                pass

        # Plain numeric string e.g. "4"
        try:
            max_w = max(1, int(s))
            return max_w, None
        except ValueError:
            pass

        # Unknown string â†’ warn and use serial
        import warnings
        warnings.warn(
            f"photo_ops: unrecognised workers value {workers!r}; "
            "defaulting to serial. Valid values: False, True, int, "
            "'auto', 'max', 'Nw', 'N%'.",
            stacklevel=3,
        )
        return 1, None

    # â”€â”€ Integer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(workers, int):
        if workers < 0:
            max_w = max(1, cpu + workers)   # cpu - abs(workers)
        else:
            max_w = max(1, workers)
        # Reasonable cap: never launch more than 8Ã— CPU to avoid thrashing
        max_w = min(max_w, cpu * 8)
        return max_w, None

    # â”€â”€ Float (e.g. 0.5 meaning 50%) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if isinstance(workers, float):
        max_w = max(1, int(cpu * workers))
        return max_w, None

    # â”€â”€ Fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    import warnings
    warnings.warn(
        f"photo_ops: unrecognised workers type {type(workers).__name__}={workers!r}; "
        "defaulting to serial.",
        stacklevel=3,
    )
    return 1, None



# â”€â”€ CHANGE 4: download â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# OLD bugs:
#   1. _decode_cloud_url called on every URL, stripping gs:// auth scheme.
#   2. A cloud folder URL passed as single `url` was sent to urlretrieve,
#      which downloaded the XML listing page and saved it as the output file.
# NEW: detect cloud folder URLs, expand them, copy cached files to output_path.


# â”€â”€ CHANGE 5: new public functions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


from .batch import _bulk_task

# â”€â”€ CHANGE 6: _run_op_on_folder (inserted before op functions) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Shared helper used by every op function to handle cloud folder input.
# Downloads all images into a temp dir, then delegates to _bulk_task so all
# existing worker-pool, logging, and success/failed-dir logic is reused.
from .cloud import _run_op_on_folder


# ======== CORE (Simple names) ========
@_bytes_support
def gray(
    input_path: PathLike,
    output_path: PathLike | None,
    skip_if_exists: bool = True,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Convert an image or folder of images to grayscale.

    Parameters
    ----------
    input_path : str | Path | bytes
        Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
    output_path : str | Path | None
        Destination path. Pass ``None`` to receive result as bytes.
    skip_if_exists : bool, default True
        Skip re-processing if the output file already exists.
    log_file : str | None, default None
        Path to audit log (.xlsx or .csv). Auto-created if not found.
    workers : WorkerSpec, default False
        Parallelism for folder input. Accepted forms:
        ``False``/``None``/``0`` → serial; ``True`` → 10 threads;
        ``int`` → exact count; ``"auto"`` → adaptive; ``"max"``/``"all"`` → all CPUs;
        ``"Nw"``/``"Nt"`` → N threads (e.g. ``"4w"``); ``"N%"`` → N% of CPUs;
        negative int → cpu_count − N; ``float`` → fraction of CPUs (e.g. ``0.5``).

    Returns
    -------
    None | bytes | BulkResult
        None on success, bytes when output_path=None, dict for folder input.

    Example
    -------
    >>> import photo_ops as ip
    >>> ip.gray("photo.jpg", "gray.jpg")
    >>> ip.gray("input_folder/", "output_folder/", workers="auto", log_file="audit.xlsx")
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, gray, log_file, workers, skip_if_exists=skip_if_exists
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            gray,
            log_file,
            "grayscale",
            workers=workers,
            skip_if_exists=skip_if_exists,
        )
    try:
        return py_to_gray(str(input_path), str(output_path), bool(skip_if_exists))
    except Exception as e:
        _handle_error("gray", e)


@_bytes_support
def resize(
    input_path: PathLike,
    output_path: PathLike | None,
    width: int,
    height: int,
    keep_ratio: bool = False,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Resize an image to exact pixel dimensions.

    Parameters
    ----------
    input_path : str | Path | bytes
        Image file, folder, cloud URL or raw bytes.
    output_path : str | Path | None
        Destination path. Pass ``None`` to receive result as bytes.
    width : int
        Target width in pixels (must be > 0).
    height : int
        Target height in pixels (must be > 0).
    keep_ratio : bool, default False
        If True, resize while keeping aspect ratio (may not hit exact w/h).
    log_file : str | None, default None
        Path to audit log (.xlsx or .csv).
    workers : bool | int | str, default False
        Parallelism: False=sequential, True=auto, int=thread count.

    Example
    -------
    >>> import photo_ops as ip
    >>> ip.resize("photo.jpg", "small.jpg", 300, 400)
    >>> ip.resize("folder/", "out/", 800, 600, workers=4, log_file="log.csv")
    """
    _check()
    _validate_positive_int("width", width)
    _validate_positive_int("height", height)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path,
            output_path,
            resize,
            log_file,
            workers,
            width=width,
            height=height,
            keep_ratio=keep_ratio,
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            resize,
            log_file,
            f"resize_{width}x{height}",
            workers=workers,
            width=width,
            height=height,
            keep_ratio=keep_ratio,
        )
    try:
        return py_resize(
            str(input_path), str(output_path), int(width), int(height), bool(keep_ratio)
        )
    except Exception as e:
        _handle_error("resize", e)


@_bytes_support
def info(input_path: PathLike) -> dict[str, int | str | dict]:
    """
    Return width, height, channels and SHA-256 checksum of an image.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.

        Returns
        -------
        dict[str, int | str]  (single file) or dict[str, dict] (folder)

        Example
        -------
        >>> import photo_ops as ip
        >>> meta = ip.info("photo.jpg")
        >>> print(meta)
        {'width': 1920, 'height': 1080, 'channels': 3, 'checksum': 'a1b2...'}
        >>> w, h = meta['width'], meta['height']
        >>>
        >>> # Folder — returns dict keyed by filename
        >>> all_info = ip.info("photos/")
        >>> for fname, m in all_info.items():
        ...     print(fname, m['width'], 'x', m['height'])
        >>>
        >>> # Bytes input
        >>> with open("photo.jpg", "rb") as f: raw = f.read()
        >>> print(ip.info(raw))

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.info(img_bytes)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.info("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        local_paths = _expand_cloud_folder(input_path)
        return {os.path.basename(p): info(p) for p in local_paths}
    if os.path.isdir(input_path):
        results = {}
        for f in [
            f
            for f in os.listdir(input_path)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"))
        ]:
            results[f] = info(os.path.join(input_path, f))
        return results
    try:
        w, h, c, checksum = py_info(str(input_path))
        return {"width": w, "height": h, "channels": c, "checksum": checksum}
    except Exception as e:
        _handle_error("info", e)


# ======== ROTATE & FIX ========
@_bytes_support
def rotate(
    input_path: PathLike,
    output_path: PathLike | None,
    angle: int,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Rotate image by exactly 90, 180, or 270 degrees (lossless).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        angle : int
            Rotation angle.  Must be one of ``90``, ``180``, ``270``.
        workers : WorkerSpec, default False
            Parallelism for folder input (see *Workers* section).

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.rotate("photo.jpg", "rot90.jpg",  90)   # 90° clockwise
        >>> ip.rotate("photo.jpg", "rot180.jpg", 180)
        >>> ip.rotate("photo.jpg", "rot270.jpg", 270)
        >>>
        >>> # Result as bytes
        >>> data = ip.rotate("photo.jpg", None, 90)
        >>>
        >>> # Folder — rotate all images
        >>> ip.rotate("in/", "out/", 90, workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.rotate("photo.jpg", None, 90)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.rotate("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, rotate, log_file, workers, angle=angle)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            rotate,
            log_file,
            f"rotate_{angle}",
            workers=workers,
            angle=angle,
        )
    try:
        return py_rotate(str(input_path), str(output_path), int(angle))
    except Exception as e:
        _handle_error("rotate", e)






# ======== CROP & TRANSFORM ========
@_bytes_support
def crop(
    input_path: PathLike,
    output_path: PathLike | None,
    x: int,
    y: int,
    width: int,
    height: int,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Crop to an exact rectangle defined by top-left corner (x, y) and dimensions.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination.  Pass ``None`` to get result as **bytes**.
        x : int  Top-left x pixel (>= 0).
        y : int  Top-left y pixel (>= 0).
        width : int   Crop width  (> 0).
        height : int  Crop height (> 0).
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.crop("photo.jpg", "cropped.jpg", x=100, y=50, width=640, height=480)
        >>> ip.crop("photo.jpg", "tl.jpg", 0, 0, 300, 300)   # top-left 300x300
        >>>
        >>> data = ip.crop("photo.jpg", None, 0, 0, 200, 200)  # bytes
        >>> ip.crop("in/", "out/", x=0, y=0, width=800, height=600, workers=4)
        >>> ip.crop("gs://bucket/photos/", "out/", 0, 0, 512, 512, workers="auto")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes::

            data = ip.crop("photo.jpg", None, 0, 0, 200, 200)

        Cloud / folder
        --------------
        Any local path can be a cloud URL or folder::

            ip.crop("gs://bucket/folder/", "out/", 0, 0, 400, 400, workers="auto")

        Workers
        -------
        Accepted: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``"auto"`` (adaptive), ``"max"``/``"all"`` (all CPUs).
        Ignored for single-file input.
    """
    _check()
    _validate_non_negative_int("x", x)
    _validate_non_negative_int("y", y)
    _validate_positive_int("width", width)
    _validate_positive_int("height", height)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, crop, log_file, workers, x=x, y=y, width=width, height=height
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            crop,
            log_file,
            "manual_crop",
            workers=workers,
            x=x,
            y=y,
            width=width,
            height=height,
        )
    try:
        return py_crop(str(input_path), str(output_path), int(x), int(y), int(width), int(height))
    except Exception as e:
        _handle_error("crop", e)


@_bytes_support
def crop_center(
    input_path: PathLike,
    output_path: PathLike | None,
    width: int,
    height: int,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Crop a centred rectangle of the given size (auto_crop is an alias).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination.  Pass ``None`` to get result as **bytes**.
        width : int   Output width in pixels.
        height : int  Output height in pixels.
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.crop_center("photo.jpg", "center.jpg", 512, 512)
        >>> ip.crop_center("photo.jpg", "thumb.jpg",  128, 128)
        >>>
        >>> data = ip.crop_center("photo.jpg", None, 256, 256)  # bytes
        >>> ip.crop_center("in/", "out/", 800, 600, workers="auto")
        >>>
        >>> # auto_crop is an alias
        >>> ip.auto_crop("photo.jpg", "out.jpg", 400, 400)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes::

            data = ip.crop_center("photo.jpg", None, 256, 256)

        Cloud / folder
        --------------
            ip.crop_center("gs://bucket/photos/", "out/", 512, 512, workers="auto")

        Workers
        -------
        Same spec as all other bulk functions.  Ignored for single-file input.
    """
    _check()
    _validate_positive_int("width", width)
    _validate_positive_int("height", height)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, crop_center, log_file, workers, width=width, height=height
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            crop_center,
            log_file,
            "center_crop",
            workers=workers,
            width=width,
            height=height,
        )
    try:
        return py_auto_crop(str(input_path), str(output_path), int(width), int(height))
    except Exception as e:
        _handle_error("crop_center", e)


# FIX 1: alias auto_crop â†’ crop_center
auto_crop = crop_center




@_bytes_support
def flip(
    input_path: PathLike,
    output_path: PathLike | None,
    direction: str = "horizontal",
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Flip image horizontally or vertically.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        direction : str, default 'horizontal'
            ``'horizontal'`` (mirror) or ``'vertical'`` (upside-down).
        workers : WorkerSpec, default False
            Parallelism for folder input.

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.flip("photo.jpg", "flipped.jpg", "horizontal")   # mirror
        >>> ip.flip("photo.jpg", "flipped.jpg", "vertical")     # upside-down
        >>>
        >>> data = ip.flip("photo.jpg", None, "horizontal")     # bytes
        >>> ip.flip("in/", "out/", direction="horizontal", workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.flip("photo.jpg", None, "horizontal")   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.flip("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, flip, None, workers, direction=direction)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path, output_path, flip, None, "flip", workers=workers, direction=direction
        )
    try:
        return py_flip(str(input_path), str(output_path), direction)
    except Exception as e:
        _handle_error("flip", e)


@_bytes_support
def scale(
    input_path: PathLike,
    output_path: PathLike | None,
    factor: float,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Scale image by a float factor (0.5 = half, 2.0 = double).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        factor : float
            Scale multiplier.  e.g. ``0.5`` halves both dimensions.
        workers : WorkerSpec, default False
            Parallelism for folder input.

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.scale("photo.jpg", "half.jpg",   0.5)   # shrink to half
        >>> ip.scale("photo.jpg", "double.jpg", 2.0)   # double size
        >>>
        >>> data = ip.scale("photo.jpg", None, 0.25)   # bytes
        >>> ip.scale("in/", "out/", factor=0.5, workers="auto")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.scale("photo.jpg", None, 0.5)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.scale("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, scale, None, workers, factor=factor)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path, output_path, scale, None, "scale", workers=workers, factor=factor
        )
    try:
        return py_scale(str(input_path), str(output_path), float(factor))
    except Exception as e:
        _handle_error("scale", e)






# ======== FILTERS ========
@_bytes_support
def blur(
    input_path: PathLike,
    output_path: PathLike | None,
    amount: int = 5,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Apply Gaussian blur.  Higher amount = stronger blur.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        amount : int, default 5
            Blur kernel radius.  Must be >= 1.
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.blur("photo.jpg", "mild.jpg",   3)    # mild
        >>> ip.blur("photo.jpg", "strong.jpg", 15)   # strong
        >>> ip.blur("photo.jpg", "default.jpg")      # amount=5
        >>>
        >>> data = ip.blur("photo.jpg", None, 7)     # bytes
        >>> ip.blur("in/", "out/", amount=5, workers="auto", log_file="audit.xlsx")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.blur("photo.jpg", None, 7)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.blur("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, blur, log_file, workers, amount=amount)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            blur,
            log_file,
            f"blur_{amount}",
            workers=workers,
            amount=amount,
        )
    try:
        return py_blur(str(input_path), str(output_path), int(amount))
    except Exception as e:
        _handle_error("blur", e)


@_bytes_support
def sharpen(
    input_path: PathLike,
    output_path: PathLike | None,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Apply unsharp-mask sharpening to the image.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.sharpen("photo.jpg", "sharp.jpg")
        >>>
        >>> data = ip.sharpen("photo.jpg", None)     # bytes
        >>> ip.sharpen("in/", "out/", workers=4, log_file="sharpen.xlsx")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.sharpen("photo.jpg", None)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.sharpen("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, sharpen, log_file, workers)
    if os.path.isdir(input_path):
        return _bulk_task(input_path, output_path, sharpen, log_file, "sharpen", workers=workers)
    try:
        return py_sharpen(str(input_path), str(output_path))
    except Exception as e:
        _handle_error("sharpen", e)


@_bytes_support
def edges(
    input_path: PathLike,
    output_path: PathLike | None,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Run Canny edge detection — returns a binary edge map (white on black).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.edges("photo.jpg", "edges.jpg")
        >>>
        >>> data = ip.edges("photo.jpg", None)       # bytes
        >>> ip.edges("in/", "out/", workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.edges("photo.jpg", None)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.edges("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, edges, log_file, workers)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path, output_path, edges, log_file, "edge_detection", workers=workers
        )
    try:
        return py_edge(str(input_path), str(output_path))
    except Exception as e:
        _handle_error("edges", e)


@_bytes_support
def edge_art(
    input_path: PathLike,
    output_path: PathLike | None,
    blur_bg: int = 35,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Artistic effect: sharp edges overlaid on a heavily blurred background.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        blur_bg : int, default 35
            Background blur strength.  Higher = more painterly.
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.edge_art("photo.jpg", "art.jpg")                  # default blur
        >>> ip.edge_art("photo.jpg", "art.jpg", blur_bg=60)     # heavy blur
        >>>
        >>> data = ip.edge_art("photo.jpg", None, blur_bg=35)   # bytes
        >>> ip.edge_art("in/", "out/", blur_bg=40, workers="auto")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.edge_art("photo.jpg", None)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.edge_art("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, edge_art, None, workers, blur_bg=blur_bg)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path, output_path, edge_art, None, "edge_art", workers=workers, blur_bg=blur_bg
        )
    try:
        return py_edge_shadow(str(input_path), str(output_path), blur_bg)
    except Exception as e:
        _handle_error("edge_art", e)


# ======== SMART VISION ========


@_bytes_support
def portrait(
    input_path: PathLike,
    output_path: PathLike | None,
    blur_bg: int = 25,
    strict: bool = False,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Portrait mode: face region kept sharp, background blurred. No GPU required.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        blur_bg : int, default 25
            Blur strength for background pixels.
        strict : bool, default False
            If True, images without a detected face go to ``not_processed/``.
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.portrait("photo.jpg", "portrait.jpg")                  # default
        >>> ip.portrait("photo.jpg", "portrait.jpg", blur_bg=50)     # stronger blur
        >>> ip.portrait("photo.jpg", "portrait.jpg", strict=True)    # skip non-portraits
        >>>
        >>> data = ip.portrait("photo.jpg", None, blur_bg=30)        # bytes
        >>> ip.portrait("in/", "out/", blur_bg=25, workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.portrait("photo.jpg", None, blur_bg=25)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.portrait("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    # py_blur_background is ALWAYS available (filter_pure.rs, registered
    # unconditionally in lib.rs).  Do NOT guard with _require_binding.
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, portrait, None, workers, blur_bg=blur_bg, strict=strict
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            portrait,
            None,
            "portrait",
            workers=workers,
            blur_bg=blur_bg,
            strict=strict,
        )
    try:
        return py_blur_background(str(input_path), str(output_path), int(blur_bg), strict)  # noqa: F821
    except Exception as e:
        _handle_error("portrait", e)


@_bytes_support
def enhance(
    input_path: PathLike,
    output_path: PathLike | None,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Enhance image quality with adaptive local contrast (CLAHE-style).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.enhance("photo.jpg", "enhanced.jpg")
        >>>
        >>> data = ip.enhance("photo.jpg", None)       # bytes
        >>> ip.enhance("in/", "out/", workers="auto", log_file="audit.xlsx")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.enhance("photo.jpg", None)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.enhance("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, enhance, log_file, workers)
    if os.path.isdir(input_path):
        return _bulk_task(input_path, output_path, enhance, log_file, "enhance", workers=workers)
    try:
        return py_enhance(str(input_path), str(output_path))
    except Exception as e:
        _handle_error("enhance", e)






# ======== COMPARE ========


# FIX 4: image_similarity alias


# ======== BATCH (One-liners) ========
def batch_resize(
    folder: PathLike,
    output_folder: PathLike,
    width: int,
    height: int,
    keep_ratio: bool = False,
) -> tuple[int, int]:
    """
    Resize all images in a folder using the Rust Rayon engine (no Python workers= needed).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        width : int
        height : int
            Target dimensions in pixels.
        keep_ratio : bool, default False
            If True, preserve aspect ratio (may not fill exact w×h).

        Returns
        -------
        tuple[int, int] — (success_count, total_count)

        Example
        -------
        >>> import photo_ops as ip
        >>> success, total = ip.batch_resize("in/", "out/", 800, 600)
        >>> print(f"{success}/{total} images resized")
        >>>
        >>> # Keep aspect ratio
        >>> ip.batch_resize("in/", "out/", 800, 600, keep_ratio=True)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.batch_resize("in/", "out/", 800, 600)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.batch_resize("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    try:
        return py_batch_resize(
            str(folder), str(output_folder), int(width), int(height), bool(keep_ratio)
        )
    except Exception as e:
        _handle_error("batch_resize", e)


def batch_process(
    folder: PathLike,
    output_folder: PathLike,
    do_what: str,
    **options: int | bool,
) -> tuple[int, int]:
    """
    Apply a single named operation to a folder using the Rust Rayon engine.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        do_what : str
            Operation name: ``'gray'``, ``'resize'``, ``'blur'``, ``'sharpen'``,
            ``'brightness'``, ``'saturation'``, ``'enhance'``, ``'sign_fix'``,
            ``'sub_fix'``, ``'fix_turn'``, etc.
        **options
            Operation-specific keyword arguments (e.g. ``width=1280, height=720``).

        Returns
        -------
        tuple[int, int] — (success_count, total_count)

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.batch_process("in/", "out/", "gray")
        >>> ip.batch_process("in/", "out/", "resize", width=1280, height=720)
        >>> ip.batch_process("in/", "out/", "blur", amount=5)
        >>> ip.batch_process("in/", "out/", "brightness", value=30)
        >>> ip.batch_process("in/", "out/", "sign_fix", width=500, height=200)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.batch_process("in/", "out/", "gray")   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.batch_process("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    try:
        op_alias = {
            "gray": "grayscale",
            "greyscale": "grayscale",
            "to_gray": "grayscale",
            "remove_bg": "remove_bg_v2",
        }
        op = op_alias.get(str(do_what).strip().lower(), str(do_what).strip().lower())
        # FIX #9: removed the 7-op whitelist â€” Rust's apply_op already handles
        # 22+ operations and raises "Unknown operation: <op>" as a PyValueError.
        return py_batch_process(str(folder), str(output_folder), op, options or None)
    except Exception as e:
        _handle_error("batch_process", e)


# ======== HEALTH CHECK ========
def works() -> str:
    """
    Quick health-check: verifies the Rust core is loaded and functional.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.

        Returns
        -------
        str — health-check message, e.g. 'photo_ops Rust core OK'

        Example
        -------
        >>> import photo_ops as ip
        >>> result = ip.works()
        >>> print(result)   # 'photo_ops Rust core OK'
        >>>
        >>> # Guard before batch jobs
        >>> assert ip.works(), "Rust core not available!"
        >>>
        >>> # Check from terminal
        >>> # $ python -c "import photo_ops as ip; print(ip.works())" 

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.works()   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.works("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    return _dummy_health_check()


# ======== MODEL INSTALLATION (FIX #6) ========
_YOLO_MODEL_URL = "https://github.com/derronqi/yolov8-face/releases/download/v0.1/yolov8n-face.onnx"
# SHA-256 of the canonical yolov8n-face.onnx from the release above.
# Update this if the upstream file changes.
_YOLO_MODEL_SHA256 = ""  # Left empty; verification skipped when empty.


def install_models(
    dest_dir: str | None = None,
    url: str = _YOLO_MODEL_URL,
    expected_sha256: str = _YOLO_MODEL_SHA256,
    force: bool = False,
) -> str:
    """Download and cache ``yolov8n-face.onnx`` so YOLO face detection works.

    Parameters
    ----------
    dest_dir:
        Target directory. Defaults to ``<TEMP>/photo_ops_cache/``.
    url:
        Download URL. Override to use a mirror or a local file URI.
    expected_sha256:
        Hex-encoded SHA-256 of the expected file. Pass ``""`` to skip
        verification (useful during development / with mirrors).
    force:
        Re-download even if the file already exists.

    Returns
    -------
    str
        Absolute path to the installed ``yolov8n-face.onnx``.

    Example
    -------
    >>> import photo_ops
    >>> photo_ops.install_models()
    '/tmp/photo_ops_cache/yolov8n-face.onnx'
    """
    import tempfile
    from pathlib import Path as _Path

    cache_dir = _Path(dest_dir) if dest_dir else _Path(tempfile.gettempdir()) / "photo_ops_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "yolov8n-face.onnx"

    if target.exists() and not force:
        print(f"[photo_ops] yolov8n-face.onnx already present at {target}")
        return str(target)

    print(f"[photo_ops] Downloading yolov8n-face.onnx from {url} ...")
    try:
        tmp_path = target.with_suffix(".onnx.part")
        urllib.request.urlretrieve(url, str(tmp_path))
    except Exception as exc:
        raise RuntimeError(
            f"photo_ops.install_models() download failed: {exc}\n"
            "Check your internet connection or supply a local 'url=' parameter."
        ) from exc

    if expected_sha256:
        sha = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
        if sha != expected_sha256.lower():
            tmp_path.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 mismatch for yolov8n-face.onnx.\n"
                f"  Expected : {expected_sha256}\n"
                f"  Got      : {sha}\n"
                "File removed. Retry or pass a different url/expected_sha256."
            )
        print(f"[photo_ops] SHA-256 verified: {sha}")

    tmp_path.replace(target)
    print(f"[photo_ops] Model installed at {target}")
    return str(target)


# ======== ADJUSTMENTS ========
@_bytes_support
def brightness(
    input_path: PathLike,
    output_path: PathLike | None,
    value: float = 0.0,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Adjust brightness by adding a pixel offset (+ve = brighter, -ve = darker).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        value : float, default 0.0
            Additive brightness offset.  Clamped to ``[-255, +255]``.
        log_file : str | None, default None
            Path to audit log (.xlsx or .csv).
        workers : WorkerSpec, default False
            Parallelism for folder input.

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.brightness("photo.jpg", "bright.jpg",  30)   # lighten
        >>> ip.brightness("photo.jpg", "dark.jpg",   -40)   # darken
        >>>
        >>> data = ip.brightness("photo.jpg", None, 20)     # bytes
        >>> ip.brightness("in/", "out/", value=25, workers=4, log_file="audit.xlsx")
        >>>
        >>> # Cloud bucket
        >>> ip.brightness("gs://bucket/photos/", "out/", value=30, workers="auto")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.brightness("photo.jpg", None, 30)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.brightness("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, brightness, log_file, workers, value=value
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            brightness,
            log_file,
            f"brightness_{value}",
            workers=workers,
            value=value,
        )
    try:
        return py_brightness(str(input_path), str(output_path), float(value))
    except Exception as e:
        _handle_error("brightness", e)


@_bytes_support
def saturation(
    input_path: PathLike,
    output_path: PathLike | None,
    factor: float,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Multiply colour saturation (0.0=greyscale, 1.0=unchanged, 2.0=vivid).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        factor : float
            Saturation multiplier.  Must be >= 0.
        log_file : str | None, default None
            Path to audit log.
        workers : WorkerSpec, default False
            Parallelism for folder input.

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.saturation("photo.jpg", "vivid.jpg",  1.8)   # vivid
        >>> ip.saturation("photo.jpg", "faded.jpg",  0.2)   # faded
        >>> ip.saturation("photo.jpg", "gray.jpg",   0.0)   # greyscale
        >>>
        >>> data = ip.saturation("photo.jpg", None, 1.5)    # bytes
        >>> ip.saturation("in/", "out/", factor=1.5, workers="auto")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.saturation("photo.jpg", None, 1.5)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.saturation("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, saturation, log_file, workers, factor=factor
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            saturation,
            log_file,
            f"saturation_{factor}",
            workers=workers,
            factor=factor,
        )
    try:
        return py_saturation(str(input_path), str(output_path), float(factor))
    except Exception as e:
        _handle_error("saturation", e)


@_bytes_support
def tint(
    input_path: PathLike,
    output_path: PathLike | None,
    r_shift: float = 0,
    g_shift: float = 0,
    b_shift: float = 0,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Shift RGB channels by additive offsets (warm/cool/creative colour cast).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        r_shift : float, default 0
        g_shift : float, default 0
        b_shift : float, default 0
            Per-channel additive shift, clamped so output stays in [0, 255].
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.tint("photo.jpg", "warm.jpg",  r_shift=30, g_shift=10, b_shift=-20)
        >>> ip.tint("photo.jpg", "cool.jpg",  r_shift=-20, b_shift=30)
        >>>
        >>> data = ip.tint("photo.jpg", None, r_shift=20)   # bytes
        >>> ip.tint("in/", "out/", r_shift=20, workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.tint("photo.jpg", None, r_shift=30, b_shift=-20)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.tint("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path,
            output_path,
            tint,
            log_file,
            workers,
            r_shift=r_shift,
            g_shift=g_shift,
            b_shift=b_shift,
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            tint,
            log_file,
            f"tint_{r_shift}_{g_shift}_{b_shift}",
            workers=workers,
            r_shift=r_shift,
            g_shift=g_shift,
            b_shift=b_shift,
        )
    try:
        return py_tint(
            str(input_path), str(output_path), float(r_shift), float(g_shift), float(b_shift)
        )
    except Exception as e:
        _handle_error("tint", e)


@_bytes_support
def color_grade(
    input_path: PathLike,
    output_path: PathLike | None,
    preset: str = "cinematic",
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Apply a color grading preset."""
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, color_grade, log_file, workers, preset=preset
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            color_grade,
            log_file,
            f"color_grade_{preset}",
            workers=workers,
            preset=preset,
        )
    try:
        return py_color_grade(str(input_path), str(output_path), str(preset))
    except Exception as e:
        _handle_error("color_grade", e)


@_bytes_support
def draw_rect(
    input_path: PathLike,
    output_path: PathLike | None,
    x: int,
    y: int,
    width: int,
    height: int,
    r: int = 255,
    g: int = 0,
    b: int = 0,
    thickness: int = 1,
    color: str | None = None,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Draw a rectangle on the image."""
    _check()
    input_path = _resolve_input(input_path)

    r, g, b = _parse_color(color, (r, g, b))

    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path,
            output_path,
            draw_rect,
            log_file,
            workers,
            x=x,
            y=y,
            width=width,
            height=height,
            r=r,
            g=g,
            b=b,
            thickness=thickness,
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            draw_rect,
            log_file,
            f"draw_rect_{x}_{y}_{width}x{height}",
            workers=workers,
            x=x,
            y=y,
            width=width,
            height=height,
            r=r,
            g=g,
            b=b,
            thickness=thickness,
        )
    try:
        return py_draw_rect(
            str(input_path),
            str(output_path),
            int(x),
            int(y),
            int(width),
            int(height),
            int(r),
            int(g),
            int(b),
            int(thickness),
        )
    except Exception as e:
        _handle_error("draw_rect", e)


@_bytes_support
def add_text(
    input_path: PathLike,
    output_path: PathLike | None,
    text: str,
    font_path: str | None = None,
    font_size: float = 32.0,
    x: int | None = None,
    y: int | None = None,
    r: int = 255,
    g: int = 0,
    b: int = 0,
    color: str | None = None,
    bg_color: str | tuple[int, int, int] | None = None,
    opacity: float = 1.0,
    position: str | None = None,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Add text to the image."""
    _check()
    input_path = _resolve_input(input_path)

    r, g, b = _parse_color(color, (r, g, b))
    
    bg_r, bg_g, bg_b = None, None, None
    if bg_color is not None:
        if isinstance(bg_color, str):
            bg_r, bg_g, bg_b = _parse_color(bg_color, (0, 0, 0))
        elif isinstance(bg_color, (tuple, list)) and len(bg_color) == 3:
            bg_r, bg_g, bg_b = bg_color

    if position == "center":
        x = None
        y = None
        
    if font_path is None:
        # Default to Arial on Windows
        import sys
        if sys.platform == "win32":
            font_path = "C:\\Windows\\Fonts\\arial.ttf"
            if not os.path.exists(font_path):
                font_path = "C:\\Windows\\Fonts\\segoeui.ttf"
        else:
            # Unix-like fallback
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path,
            output_path,
            add_text,
            log_file,
            workers,
            text=text,
            font_path=font_path,
            font_size=font_size,
            x=x,
            y=y,
            r=r,
            g=g,
            b=b,
            color=color,
            bg_color=bg_color,
            opacity=opacity,
            position=position,
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            add_text,
            log_file,
            "add_text",
            workers=workers,
            text=text,
            font_path=font_path,
            font_size=font_size,
            x=x,
            y=y,
            r=r,
            g=g,
            b=b,
            color=color,
            bg_color=bg_color,
            opacity=opacity,
            position=position,
        )

    try:
        return py_add_text(
            str(input_path), str(output_path), str(text), str(font_path), float(font_size), x, y, int(r), int(g), int(b), bg_r, bg_g, bg_b, float(opacity)
        )
    except Exception as e:
        _handle_error("add_text", e)


@_bytes_support
def compress(
    input_path: PathLike,
    output_path: PathLike | None,
    format: str = "webp",
    quality: int = 80,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Re-encode image at the given quality to reduce file size.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        format : str, default 'webp'
            Output format: ``'webp'``, ``'jpg'``/``'jpeg'``, or ``'png'``.
        quality : int, default 80
            Quality 1 (smallest) – 100 (best).  Ignored for PNG (lossless).
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.compress("photo.jpg", "out.webp", "webp", 80)   # WebP 80
        >>> ip.compress("photo.jpg", "out.jpg",  "jpg",  75)   # JPEG 75
        >>> ip.compress("photo.jpg", "out.png",  "png",  90)   # PNG (lossless)
        >>>
        >>> data = ip.compress("photo.jpg", None, "webp", 70)  # bytes
        >>> ip.compress("in/", "out/", format="webp", quality=75, workers="auto")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.compress("photo.jpg", None, "webp", 80)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.compress("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, compress, log_file, workers, format=format, quality=quality
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            compress,
            log_file,
            f"compress_{format}_{quality}",
            workers=workers,
            format=format,
            quality=quality,
        )
    try:
        return py_compress(str(input_path), str(output_path), str(format), int(quality))
    except Exception as e:
        _handle_error("compress", e)




@_bytes_support
def deblur(
    input_path: PathLike,
    output_path: PathLike | None,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Remove motion blur using Wiener-inspired sharpening.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        log_file : str | None, default None
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.deblur("blurry.jpg", "deblurred.jpg")
        >>>
        >>> data = ip.deblur("blurry.jpg", None)       # bytes
        >>> ip.deblur("in/", "out/", workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.deblur("blurry.jpg", None)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.deblur("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_path, deblur, log_file, workers)
    if os.path.isdir(input_path):
        return _bulk_task(input_path, output_path, deblur, log_file, "deblur", workers=workers)
    try:
        return py_deblur(str(input_path), str(output_path))
    except Exception as e:
        _handle_error("deblur", e)






@_bytes_support
def extract_frames(
    input_path: PathLike,
    output_dir: PathLike,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Extract frames from GIF/WebP."""
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(input_path, output_dir, extract_frames, log_file, workers)
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path, output_dir, extract_frames, log_file, "extract_frames", workers=workers
        )
    try:
        if not str(input_path).lower().endswith(".gif"):
            raise ValueError(f"extract_frames only supports GIF images, got: {input_path}")
        return py_extract_frames(str(input_path), str(output_dir))
    except Exception as e:
        _handle_error("extract_frames", e)


@_bytes_support
def morphology(
    input_path: PathLike,
    output_path: PathLike | None,
    op: str = "dilate",
    kernel_size: int = 3,
    log_file: str | None = None,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """Apply dilation or erosion to a binary mask."""
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, morphology, log_file, workers, op=op, kernel_size=kernel_size
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            morphology,
            log_file,
            f"morphology_{op}_{kernel_size}",
            workers=workers,
            op=op,
            kernel_size=kernel_size,
        )
    try:
        return py_morphology(str(input_path), str(output_path), str(op), int(kernel_size))
    except Exception as e:
        _handle_error("morphology", e)




# ======== PHASE 9: SIGNATURE & SUBJECT FIX ========


import shutil as _shutil  # module-level import â€” avoids repeated imports in hot paths

_MAX_RECURSION_DEPTH = 20


# â”€â”€ enhance_signature â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@_bytes_support
def enhance_signature(
    input_path: PathLike,
    output_path: PathLike | None,
    block_size: int = 11,
    c: float = 2.0,
    workers: WorkerSpec = False,
) -> BulkResult | None:
    """
    Pre-process signature: remove shadow, boost local contrast (run BEFORE sign_fix).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        block_size : int, default 11
            Adaptive threshold block size (must be odd, >= 3).
        c : float, default 2.0
            Adaptive threshold constant.
        workers : WorkerSpec, default False

        Returns
        -------
        None | bytes | BulkResult

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.enhance_signature("sig.jpg", "sig_clean.jpg")
        >>>
        >>> # Typical pipeline: enhance → fix
        >>> ip.enhance_signature("sig.jpg", "sig_enh.jpg")
        >>> ip.sign_fix("sig_enh.jpg", "sig_final.jpg", width=500, height=200)
        >>>
        >>> data = ip.enhance_signature("sig.jpg", None)      # bytes
        >>> ip.enhance_signature("sigs/", "enh/", workers=4)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.enhance_signature("sig.jpg", None)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.enhance_signature("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path, output_path, enhance_signature, None, workers, block_size=block_size, c=c
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            enhance_signature,
            None,
            "enhance_signature",
            workers=workers,
            block_size=block_size,
            c=c,
        )
    try:
        return py_enhance_signature(str(input_path), str(output_path), int(block_size), float(c))
    except Exception as e:
        _handle_error("enhance_signature", e)


# â”€â”€ has_faces â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@_bytes_support
def has_faces(input_path: PathLike) -> bool:
    """
    Return True if the image contains at least one face (SeetaFace detector).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.

        Returns
        -------
        bool

        Example
        -------
        >>> import photo_ops as ip
        >>> if ip.has_faces("photo.jpg"):
        ...     print("Face found")
        >>>
        >>> # Bytes input
        >>> with open("photo.jpg", "rb") as f: raw = f.read()
        >>> print(ip.has_faces(raw))   # True or False
        >>>
        >>> # Use in a filter loop
        >>> import os
        >>> for fname in os.listdir("photos/"):
        ...     if ip.has_faces(os.path.join("photos/", fname)):
        ...         print("Portrait:", fname)

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.has_faces(raw_bytes)   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.has_faces("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    input_path = _resolve_input(input_path)
    try:
        return py_has_faces(str(input_path))
    except Exception as e:
        _handle_error("has_faces", e)


# â”€â”€ sign_fix â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@_bytes_support
def sign_fix(
    input_path: PathLike,
    output_path: PathLike | None,
    width: int | None = None,
    height: int | None = None,
    force: bool = False,
    log_file: str | None = None,
    workers: WorkerSpec = False,
    stats: bool = False,
) -> BulkResult | None:
    """Intelligent Signature Fix â€” auto-orient, invert-normalise, and tight-crop a signature.

    Pipeline
    --------
    1. Fix coarse orientation (EXIF + face/LGC detection, 90Â° steps)
    2. Invert normalisation â€” dark-background images (white-on-dark) are
       inverted so the smart-crop always sees dark ink on a white field.
    3. Signature confidence check (primary + fallback classifier).
       Low-confidence images go to ``not_processed/`` instead of failing hard.
    4. Smart crop to tight ink bounding box, resized to w Ã— h.

    Parameters
    ----------
    input_path : str | Path | bytes
        Image file, folder, cloud URL or raw bytes.
    output_path : str | Path | None
        Destination path. Pass ``None`` to receive result as bytes.
    width : int | None, default 500
        Target output width in pixels.
    height : int | None, default 200
        Target output height in pixels.
    force : bool, default False
        Skip signature classification â€” process every image regardless.
    log_file : str | None, default None
        Audit log path (.xlsx or .csv).
    workers : bool | int | str, default False
        Parallelism for folder input.
    stats : bool, default False
        Print summary counts after folder processing.

    Returns
    -------
    None | BulkResult
        None on success (single file), dict for folder input.

    Folders produce four sub-directories
    ------------------------------------
    success/       Correctly processed signatures.
    failed/        Hard I/O or exception errors.
    not_processed/ Low-confidence images (not a clear signature, not a face).

    Example
    -------
    >>> import photo_ops as ip
    >>> ip.sign_fix("sig.jpg", "out.jpg", width=500, height=200)
    >>> ip.sign_fix("sigs/", "out/", workers="auto", log_file="audit.xlsx", stats=True)
    """
    _check()
    _validate_bool("force", force)
    _validate_bool("stats", stats)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path,
            output_path,
            sign_fix,
            log_file,
            workers,
            width=width,
            height=height,
            force=force,
            stats=stats,
        )
    if os.path.isdir(input_path):
        return _bulk_task(
            input_path,
            output_path,
            sign_fix,
            log_file,
            "sign_fix",
            workers=workers,
            stats=stats,
            width=width,
            height=height,
            force=force,
        )

    w = width if width is not None else 500
    h = height if height is not None else 200
    _validate_positive_int("width", w)
    _validate_positive_int("height", h)

    import tempfile as _tf
    import uuid as _uuid
    _uid = _uuid.uuid4().hex
    temp_rot = os.path.join(_tf.gettempdir(), f"_photo_ops_{_uid}_sign_rot.jpg")
    temp_inv = os.path.join(_tf.gettempdir(), f"_photo_ops_{_uid}_sign_inv.jpg")
    working  = temp_rot          # file we pass to smart_crop
    try:
        # — Step 1: Fix coarse orientation (EXIF + LGC face detection) ———————
        # Signatures have no faces, so face-LGC orientation may raise.
        # Fallback: copy original as-is and continue with signature pipeline.
        try:
            py_fix_orientation(str(input_path), temp_rot)
        except Exception:
            import shutil as _sign_shutil
            _sign_shutil.copy2(str(input_path), temp_rot)

        # — Step 2: Invert normalization — dark-bg signatures ————————————————
        # If the image border pixels are predominantly dark, the ink is light
        # (white-on-dark). Invert so smart_crop always sees dark-ink-on-white.
        try:
            _dark = py_is_dark_background(temp_rot)
        except Exception:
            _dark = False

        if _dark:
            py_invert(temp_rot, temp_inv)
            working = temp_inv

        # — Step 3: Signature confidence check (skip if force=True) ———————————
        if not force:
            primary_ok   = py_is_signature(working)
            fallback_ok  = False
            if not primary_ok:
                try:
                    fallback_ok = py_is_signature_fallback(working)
                except Exception:
                    pass
            if not primary_ok and not fallback_ok:
                raise NotProcessedError(
                    f"Not a recognisable signature (dark_bg={_dark}). "
                    "Use force=True to bypass classification."
                )

        # — Step 4: Already-correct detection — skip re-encode if dims match —
        rot_info = info(working)
        if abs(rot_info["width"] - w) <= 2 and abs(rot_info["height"] - h) <= 2:
            import shutil as _sf_shutil
            _sf_shutil.copy2(working, str(output_path))
            return None

        # — Step 5: Upscale quality warning ——————————————————————————————————
        if w > rot_info["width"] * 2 or h > rot_info["height"] * 2:
            import warnings
            warnings.warn(
                f"sign_fix: requested {w}x{h} is >2x larger than source "
                f"{rot_info['width']}x{rot_info['height']}. "
                "Output quality will be degraded.",
                RuntimeWarning,
                stacklevel=2,
            )

        # — Step 6: Smart crop — ink bbox detection + exact resize ———————————
        # Use strict=False: signature is already validated by py_is_signature above.
        # With strict=True, faint sigs that miss ink-bbox fall through to face
        # detection and throw "No faces detected". strict=False gracefully falls
        # back to auto_crop when ink bbox is not found.
        py_smart_crop(working, str(output_path), w, h, False)
        return None

    except NotProcessedError:
        raise   # bubble up so _bulk_task routes to not_processed/
    except Exception as e:
        _handle_error("sign_fix", e)
    finally:
        for _tmp in (temp_rot, temp_inv):
            if os.path.exists(_tmp):
                try:
                    os.remove(_tmp)
                except OSError:
                    pass



def _check_classify_task(
    input_path,
    output_path,
    log_file,
    stats,
    workers,
    op_name,
    classify_fn,  # (in_file) -> "success" | "success (via fallback)" | "subject" | "failed"
    extra_dirs=None,
    _depth=0,
):
    """Shared directory-walk + classify + copy engine for sign_check and sub_check.

    Parameters
    ----------
    classify_fn : callable
        Receives a local file path and returns one of:
        ``"success"``, ``"success (via fallback)"``, ``"subject"``, ``"failed"``.
    extra_dirs : iterable of str, optional
        Additional subdirectory names to create under output_path (e.g. ``["subject"]``).
    """
    if _depth > _MAX_RECURSION_DEPTH:
        print(f" [WARNING] Max recursion depth reached at '{input_path}'. Skipping.")
        return {}

    results = {}
    files = [
        f
        for f in os.listdir(input_path)
        if os.path.isfile(os.path.join(input_path, f))
        and f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"))
    ]

    if files:
        dir_map = {
            "success": os.path.join(output_path, "success"),
            "failed":  os.path.join(output_path, "failed"),
            "not_processed": os.path.join(output_path, "not_processed"),
        }
        if extra_dirs:
            for d in extra_dirs:
                dir_map[d] = os.path.join(output_path, d)
        for d in dir_map.values():
            os.makedirs(d, exist_ok=True)

        def _process_one(f):
            in_file = os.path.join(input_path, f)
            try:
                label = classify_fn(in_file)
            except NotProcessedError as exc:
                dest = dir_map["not_processed"]
                _shutil.copy2(in_file, os.path.join(dest, f))
                if log_file:
                    log_advanced(f, "not_processed", op_name, str(exc)[:120],
                                 os.path.join(output_path, log_file))
                return f, "not_processed"
            except Exception as exc:
                msg = str(exc).encode('ascii','replace').decode('ascii')
                print(f"Error processing {f}: {msg}")
                if log_file:
                    log_advanced(f, "failed", "", msg,
                                 os.path.join(output_path, log_file))
                return f, f"error: {msg}"
            dest_dir = dir_map.get(label.split(" ")[0], dir_map["not_processed"])
            _shutil.copy2(in_file, os.path.join(dest_dir, f))
            if log_file:
                log_advanced(
                    f, label.split(" ")[0], op_name, "",
                    os.path.join(output_path, log_file)
                )
            return f, label

        max_w, controller = _get_worker_setup(workers)
        if max_w > 1:
            from concurrent.futures import ThreadPoolExecutor

            def _adaptive_one(f):
                if controller:
                    controller.wait_if_hot()
                    controller.acquire()
                try:
                    return _process_one(f)
                finally:
                    if controller:
                        controller.release()

            try:
                with ThreadPoolExecutor(max_workers=max_w) as executor:
                    for f, status in executor.map(_adaptive_one, files):
                        results[f] = status
            finally:
                if controller:
                    controller.stop()
        else:
            for f in files:
                fname, status = _process_one(f)
                results[fname] = status

    # Recurse into subdirectories
    skip = {"success", "failed", "subject", "not_processed"} | set(extra_dirs or [])
    for d in os.listdir(input_path):
        full = os.path.join(input_path, d)
        if not os.path.isdir(full) or d in skip:
            continue
        sub = _check_classify_task(
            full,
            os.path.join(output_path, d),
            log_file,
            False,
            workers,
            op_name,
            classify_fn,
            extra_dirs,
            _depth=_depth + 1,
        )
        if sub:
            results[d] = sub

    if stats:
        s     = sum(1 for v in results.values() if isinstance(v, str) and v.startswith("success"))
        sub_c = sum(1 for v in results.values() if v == "subject")
        np_c  = sum(1 for v in results.values() if v == "not_processed")
        f_c   = sum(1 for v in results.values() if v == "failed")
        print(
            f"\n[{op_name}] Complete: {len(results)} total, {s} success, "
            f"{sub_c} subjects, {np_c} not_processed, {f_c} failed."
        )

    return results


# — sign_check —————————————————————————————————————————————————————————————————————
@_bytes_support
def sign_check(
    input_path: PathLike,
    output_path: PathLike | None = None,
    force: bool = False,
    log_file: str | None = None,
    stats: bool = False,
    workers: WorkerSpec = False,
) -> bool | BulkResult:
    """Signature Check — classify images as signature / subject / not_processed.

    Runs a two-stage classifier (primary + fallback) on each image:
    - Primary  : strict ink-ratio + stroke-complexity + clustering check.
    - Fallback : looser threshold; also tests the inverted image for
                 white-on-dark signatures.

    Parameters
    ----------
    input_path : str | Path | bytes
        Image file, folder or cloud URL.
    output_path : str | Path | None
        Destination root for sub-folders.
    force : bool, default False
        Treat every image as a valid signature (skip classification).
    log_file : str | None, default None
        Audit log path (.xlsx or .csv).
    stats : bool, default False
        Print success/subject/not_processed/failed counts.
    workers : bool | int | str, default False
        Parallelism for folder input.

    Returns
    -------
    bool | BulkResult
        Single file: True if signature detected, False otherwise.
        Folder: dict mapping filename -> label.

    Folder output sub-directories
    ------------------------------
    success/       Confirmed signatures.
    subject/       Has faces but is not a signature.
    not_processed/ Unclassifiable (low confidence).
    failed/        Hard I/O errors.

    Example
    -------
    >>> import photo_ops as ip
    >>> is_sig = ip.sign_check("doc.jpg")           # True / False
    >>> ip.sign_check("sigs/", "out/", stats=True, log_file="audit.xlsx")
    """
    _check()
    _validate_bool("force", force)
    _validate_bool("stats", stats)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        import shutil
        import tempfile

        local_paths = _expand_cloud_folder(input_path)
        with tempfile.TemporaryDirectory(prefix="photo_ops_cloud_") as tmp_dir:
            for src in local_paths:
                shutil.copy2(src, os.path.join(tmp_dir, os.path.basename(src)))
            return sign_check(
                tmp_dir, output_path, force=force, log_file=log_file, stats=stats, workers=workers
            )
    if os.path.isdir(input_path):

        def _sign_classify(in_file):
            """Three-stage classifier. Dark-bg detection now handled in Rust."""
            if force:
                return "success"

            # Stage 1: primary classifier — two-window model (normal + inverted-bg)
            if py_is_signature(str(in_file)):
                return "success"

            # Stage 2: fallback — same two-window model with relaxed thresholds
            if py_is_signature_fallback(str(in_file)):
                return "success"

            # Stage 3: face check — this is a portrait, not a signature
            if has_faces(str(in_file)):
                return "subject"

            # Unclassifiable — route to not_processed/
            return "not_processed"


        return _check_classify_task(
            input_path,
            output_path,
            log_file,
            stats,
            workers,
            "sign_check",
            _sign_classify,
            extra_dirs=["subject"],
        )

    try:
        if force or py_is_signature(str(input_path)) or py_is_signature_fallback(str(input_path)):
            if output_path is not None:
                _shutil.copy2(input_path, output_path)
            return True
        return False
    except Exception as e:
        _handle_error("sign_check", e)


# — sub_fix ————————————————————————————————————————————————————————————————————————
@_bytes_support
def sub_fix(
    input_path: PathLike,
    output_path: PathLike | None = None,
    width: int | None = None,
    height: int | None = None,
    force: bool = False,
    log_file: str | None = None,
    workers: WorkerSpec = False,
    stats: bool = False,
) -> BulkResult | None:
    """Intelligent Subject Fix — orient, de-tilt, and passport-crop a portrait.

    Pipeline
    --------
    1. Fix coarse orientation (EXIF + face/LGC detection, 90° steps)
    2. Fine tilt correction: sweeps ±15° in 5° steps, picks angle with
       highest face-detection confidence (requires new Rust build).
    3. Face-aware passport crop with proper head-room (crown at ~15% from top,
       chin at ~78% from top — ICAO passport framing).
    4. Images with no detectable face go to ``not_processed/`` (use force=True
       to bypass face check and fall back to centre crop).

    Parameters
    ----------
    input_path : str | Path | bytes
        Image file, folder, cloud URL or raw bytes.
    output_path : str | Path | None
        Destination path. Pass ``None`` to receive result as bytes.
    width : int | None, default 600
        Target output width in pixels.
    height : int | None, default 600
        Target output height in pixels.
    force : bool, default False
        Bypass face requirement — uses centre crop if no face found.
    log_file : str | None, default None
        Audit log path (.xlsx or .csv).
    workers : bool | int | str, default False
        Parallelism for folder input.
    stats : bool, default False
        Print summary counts after folder processing.

    Returns
    -------
    None | BulkResult
        None on success (single file), dict for folder input.

    Folders produce four sub-directories
    ------------------------------------
    success/       Passport-cropped images.
    failed/        Hard I/O or exception errors.
    not_processed/ No face detected — cannot make passport crop.

    Example
    -------
    >>> import photo_ops as ip
    >>> ip.sub_fix("portrait.jpg", "passport.jpg", width=600, height=600)
    >>> ip.sub_fix("photos/", "out/", workers=4, log_file="audit.csv", stats=True)
    """
    _check()
    _validate_bool("force", force)
    _validate_bool("stats", stats)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        return _run_op_on_folder(
            input_path,
            output_path,
            sub_fix,
            log_file,
            workers,
            width=width,
            height=height,
            force=force,
            stats=stats,
        )
    if os.path.isdir(input_path):
        results = _bulk_task(
            input_path,
            output_path,
            sub_fix,
            log_file,
            "sub_fix",
            workers=workers,
            width=width,
            height=height,
            force=force,
        )
        if stats:
            s = sum(1 for v in results.values() if isinstance(v, str) and v.startswith("success"))
            print(
                f"\n[sub_fix] Complete: {len(results)} total, {s} success, {len(results) - s} failed."
            )
        return results

    w = width if width is not None else 600
    h = height if height is not None else 600
    _validate_positive_int("width", w)
    _validate_positive_int("height", h)

    import tempfile as _tf
    import uuid as _uuid
    _uid = _uuid.uuid4().hex
    temp_rot  = os.path.join(_tf.gettempdir(), f"_photo_ops_{_uid}_sub_rot.jpg")
    temp_tilt = os.path.join(_tf.gettempdir(), f"_photo_ops_{_uid}_sub_tilt.jpg")
    working   = temp_rot
    try:
        # — Step 1: Fix coarse orientation (EXIF + face-LGC, 90° multiples) —
        py_fix_orientation(str(input_path), temp_rot)

        # — Step 1.5: Already-correct detection ——————————————————————————————
        rot_info = info(temp_rot)
        if abs(rot_info["width"] - w) <= 2 and abs(rot_info["height"] - h) <= 2:
            import shutil as _sf_shutil
            _sf_shutil.copy2(temp_rot, str(output_path))
            return None

        # â”€â”€ Step 2: Fine tilt correction (Â±15Â° sweep via face score) â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Uses py_detect_tilt_angle if available (new Rust build).
        try:
            tilt_angle = py_detect_tilt_angle(temp_rot)
        except Exception:
            tilt_angle = 0.0

        if abs(tilt_angle) > 1.0:   # only correct meaningful tilt
            try:
                py_rotate_any(temp_rot, temp_tilt, -tilt_angle)  # counter-rotate
                working = temp_tilt
            except Exception:
                working = temp_rot   # fallback if rotate_any unavailable

        # -- Step 3: Face-aware passport crop (initial attempt) ----------
        _face_crop_done = False
        try:
            py_smart_crop_v2(working, str(output_path), w, h, not force)
            _face_crop_done = True
        except Exception:
            pass  # will retry below on inner region

        # -- Step 3b: Large-background / clear-image retry ---------------
        # Passport photos with large plain backgrounds can fool SeetaFace
        # because the face occupies a small fraction of the full canvas.
        # Fix: crop to inner 60% to strip border, re-run tilt detection
        # on that tighter region, then retry face-aware crop.
        if not _face_crop_done and not force:
            _inner_file = os.path.join(
                _tf.gettempdir(), f"_photo_ops_{_uid}_sub_inner.jpg"
            )
            _inner_tilt_file = os.path.join(
                _tf.gettempdir(), f"_photo_ops_{_uid}_sub_inner_tilt.jpg"
            )
            try:
                _ri = info(working)
                _cx = _ri["width"] // 5    # 20% padding each side -> inner 60%
                _cy = _ri["height"] // 5
                _cw = _ri["width"]  - 2 * _cx
                _ch = _ri["height"] - 2 * _cy
                if _cw > 80 and _ch > 80:
                    py_crop(working, _inner_file, _cx, _cy, _cw, _ch)
                    _inner_working = _inner_file
                    # Re-detect tilt on the tighter crop --
                    # a large background often masks subtle tilt
                    try:
                        _inner_tilt = py_detect_tilt_angle(_inner_file)
                        if abs(_inner_tilt) > 1.0:
                            try:
                                py_rotate_any(
                                    _inner_file,
                                    _inner_tilt_file,
                                    -_inner_tilt,
                                )
                                _inner_working = _inner_tilt_file
                            except Exception:
                                pass
                    except Exception:
                        pass  # tilt detection unavailable
                    # Retry face-aware crop on pre-cropped region
                    try:
                        py_smart_crop_v2(
                            _inner_working, str(output_path), w, h, True
                        )
                        _face_crop_done = True
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                for _inner_tmp in (_inner_file, _inner_tilt_file):
                    if os.path.exists(_inner_tmp):
                        try:
                            os.remove(_inner_tmp)
                        except OSError:
                            pass

        if not _face_crop_done:
            if not force:
                raise NotProcessedError(
                    "No face detected for passport crop (tried full-image"
                    " and inner-region pre-crop). Use force=True to bypass"
                    " face requirement (falls back to centre crop)."
                )
            # force=True: graceful centre-crop fallback
            py_smart_crop(working, str(output_path), w, h, False)

        return None

    except NotProcessedError:
        raise   # bubble up so _bulk_task routes to not_processed/
    except Exception as e:
        _handle_error("sub_fix", e)
    finally:
        for _tmp in (temp_rot, temp_tilt):
            if os.path.exists(_tmp):
                try:
                    os.remove(_tmp)
                except OSError:
                    pass


# â”€â”€ sub_check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@_bytes_support
def sub_check(
    input_path: PathLike,
    output_path: PathLike | None = None,
    force: bool = False,
    log_file: str | None = None,
    stats: bool = False,
    workers: WorkerSpec = False,
) -> bool | BulkResult:
    """Subject Check â€” classify images as containing a detectable face or not.

    Uses rustface (SeetaFace) with min_face_size=40 and score_thresh=1.5.

    Parameters
    ----------
    input_path : str | Path | bytes
        Image file, folder or cloud URL.
    output_path : str | Path | None
        Destination root for sub-folders.
    force : bool, default False
        Treat every image as containing a subject (skip face detection).
    log_file : str | None, default None
        Audit log path (.xlsx or .csv).
    stats : bool, default False
        Print success/not_processed/failed counts.
    workers : bool | int | str, default False
        Parallelism for folder input.

    Returns
    -------
    bool | BulkResult
        Single file: True if face detected, False otherwise.
        Folder: dict mapping filename -> label.

    Folder output sub-directories
    ------------------------------
    success/       Face(s) detected.
    not_processed/ No face detected.
    failed/        Hard I/O errors.

    Example
    -------
    >>> import photo_ops as ip
    >>> has_face = ip.sub_check("portrait.jpg")     # True / False
    >>> ip.sub_check("photos/", "out/", stats=True, log_file="audit.xlsx")
    """
    _check()
    _validate_bool("force", force)
    _validate_bool("stats", stats)
    input_path = _resolve_input(input_path)
    if _is_cloud_folder(input_path):
        import shutil
        import tempfile

        local_paths = _expand_cloud_folder(input_path)
        with tempfile.TemporaryDirectory(prefix="photo_ops_cloud_") as tmp_dir:
            for src in local_paths:
                shutil.copy2(src, os.path.join(tmp_dir, os.path.basename(src)))
            return sub_check(
                tmp_dir, output_path, force=force, log_file=log_file, stats=stats, workers=workers
            )
    if os.path.isdir(input_path):

        def _sub_classify(in_file):
            """Face-detection with orientation fix for a single image."""
            if force:
                return "success"
            # Try direct face detection
            if has_faces(str(in_file)):
                return "success"
            # Try after orientation fix (rotated portraits)
            try:
                import tempfile as _tf
                with _tf.NamedTemporaryFile(suffix='.jpg', delete=False) as _t:
                    _rot = _t.name
                py_fix_orientation(str(in_file), _rot)
                try:
                    if has_faces(_rot):
                        return "success"
                finally:
                    try:
                        os.remove(_rot)
                    except OSError:
                        pass
            except Exception:
                pass
            # No face found â€” goes to not_processed/ (not failed/)
            return "not_processed"

        return _check_classify_task(
            input_path,
            output_path,
            log_file,
            stats,
            workers,
            "sub_check",
            _sub_classify,
        )

    try:
        if force or has_faces(input_path):
            if output_path is not None:
                _shutil.copy2(input_path, output_path)
            return True
        else:
            return False
    except Exception as e:
        _handle_error("sub_check", e)


from .logging import log_advanced

# ======== VIDEO / ANIMATION ========


def extract_frames_ex(
    input_path: PathLike,
    output_dir: PathLike,
    blur_threshold: float = 0.0,
    min_diff: float = 3.0,
    score_pct: float = 0.3,
) -> int:
    """Extract key frames with fine-grained saliency control.

    Parameters
    ----------
    input_path      : Path to the input GIF file.
    output_dir      : Directory for output ``frame_NNNN.png`` files.
    blur_threshold  : Minimum edge-density score to keep a frame.
                      ``0.0`` (default) auto-sets to 20% of the max score.
    min_diff        : Minimum mean-pixel difference vs the last kept frame.
                      Lower = keep more frames. Default ``3.0``.
    score_pct       : Frames scoring below ``score_pct Ã— running_max`` are
                      dropped as relatively blurry. Default ``0.3`` (30%).

    Returns
    -------
    int
        Number of key frames extracted.

    Example
    -------
    >>> import photo_ops
    >>> # Strict: keep only frames with â‰¥50% of max sharpness and big content changes
    >>> n = photo_ops.extract_frames_ex("clip.gif", "kf/", score_pct=0.5, min_diff=8.0)
    """
    _check()
    try:
        return py_extract_frames_ex(
            str(input_path),
            str(output_dir),
            float(blur_threshold),
            float(min_diff),
            float(score_pct),
        )
    except Exception as e:
        _handle_error("extract_frames_ex", e)

