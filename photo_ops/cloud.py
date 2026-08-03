from __future__ import annotations  # Python 3.8+ compatibility
import os
import sys
import urllib.request
from functools import partial
from pathlib import Path

# Import helpers
from ._helpers import (
    _check,
    _handle_error,
    _is_probable_file_path,
)
from .logging import log_advanced

# Constants from core.py
_CLOUD_FOLDER_SCHEMES = ("gs://", "s3://", "az://", "ipfs://")
_CLOUD_FOLDER_HOSTS = (
    "storage.googleapis.com",
    "s3.amazonaws.com",
    "blob.core.windows.net",
)
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


def _decode_cloud_url(url):
    """Strip optional 'gsutil cp' shell prefix from a URL string."""
    url_str = str(url).strip()
    for prefix in ("gsutil -m cp ", "gsutil cp "):
        if url_str.startswith(prefix):
            url_str = url_str[len(prefix) :].strip()
    return url_str


def _is_cloud_folder(url):
    """Return True if *url* is a cloud bucket/prefix rather than a single file."""
    if not isinstance(url, str):
        return False
    is_cloud = any(url.startswith(s) for s in _CLOUD_FOLDER_SCHEMES) or any(
        h in url for h in _CLOUD_FOLDER_HOSTS
    )
    if not is_cloud:
        return False
    if url.endswith("/"):
        return True
    suffix = Path(url).suffix.lower()
    return not bool(suffix and suffix in _IMAGE_EXTS)


def _expand_cloud_folder(url):
    """Call Rust to expand cloud folder."""
    core_module = sys.modules.get("photo_ops.core")
    py_resolve_folder = getattr(core_module, "py_resolve_folder", None) if core_module else None

    if not py_resolve_folder:
        raise RuntimeError("Rust core bindings not available.")

    try:
        paths = py_resolve_folder(url)
    except Exception as exc:
        raise ValueError(f"Could not expand cloud folder '{url}': {exc}") from exc
    if not paths:
        raise ValueError(f"No image files found in cloud folder: {url}")
    return paths


def _resolve_input(path):
    """Resolve a path or URL to a local file path using the Rust core."""
    path_str = _decode_cloud_url(path)

    if _is_cloud_folder(path_str):
        return path_str

    core_module = sys.modules.get("photo_ops.core")
    py_resolve_url = getattr(core_module, "py_resolve_url", None) if core_module else None

    if not py_resolve_url:
        return path_str

    try:
        return py_resolve_url(path_str)
    except Exception as e:
        print(f" Warning: Rust URL resolver failed: {e}. Using raw path.")
        return path_str


def _download_progress(filename, prefix, count, block_size, total_size):
    """Callback for urllib to show a progress bar."""
    if total_size <= 0:
        return
    downloaded = count * block_size
    percent = min(100, int(downloaded * 100 / total_size))

    bar_length = 15
    filled = int(bar_length * percent / 100)
    bar = "=" * filled + "-" * (bar_length - filled)

    dl_mb = downloaded / (1024 * 1024)
    tot_mb = total_size / (1024 * 1024)

    display_name = os.path.basename(filename)
    if len(display_name) > 20:
        display_name = display_name[:17] + "..."

    try:
        if percent >= 100:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.write(f"{prefix} ✔ {display_name} ({tot_mb:.1f} MB)\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(f"\r{prefix} [{bar}] {display_name} ({dl_mb:.1f}/{tot_mb:.1f} MB)")
            sys.stdout.flush()
    except UnicodeEncodeError:
        if percent >= 100:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.write(f"{prefix} [OK] {display_name} ({tot_mb:.1f} MB)\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(f"\r{prefix} [{bar}] {display_name} ({dl_mb:.1f}/{tot_mb:.1f} MB)")
            sys.stdout.flush()


def download(
    url: str | list[str] | tuple[str, ...],
    output_path: str | os.PathLike,
    prefix: str = "",
    log_file: str | None = None,
    progress: bool = True,
    workers: bool | int | str = False,
) -> bool | dict[str, str]:
    """
    Download a file or list of files from a URL (HTTP/HTTPS/gs://s3://az://) to local disk.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.
        output_path : str | Path
            Local file path or directory.  For bulk downloads, must be a directory.
        prefix : str, default ''
            Optional prefix for progress messages.
        log_file : str | None, default None
        progress : bool, default True
            Show progress bar in terminal.
        workers : WorkerSpec, default False
            Concurrency for bulk downloads.

        Returns
        -------
        True (single file) | dict[str, str] (bulk)

        Example
        -------
        >>> import photo_ops as ip
        >>> # HTTP
        >>> ip.download("https://example.com/photo.jpg", "local.jpg")
        >>>
        >>> # Google Cloud Storage
        >>> ip.download("gs://my-bucket/photos/portrait.jpg", "portrait.jpg")
        >>>
        >>> # Amazon S3
        >>> ip.download("s3://my-bucket/image.jpg", "image.jpg")
        >>>
        >>> # Bulk download with progress bar
        >>> urls = ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        >>> ip.download(urls, "local_dir/", workers=4, progress=True)
        >>>
        >>> # Check auth first
        >>> print(ip.auth_status())

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.download("https://example.com/photo.jpg", "local.jpg")   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.download("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    # Import locally to avoid circular dependency
    from .batch import _get_worker_setup

    if isinstance(url, str):
        url = _decode_cloud_url(url)
    elif isinstance(url, (list, tuple)):
        url = [_decode_cloud_url(u) for u in url]

    if isinstance(url, str) and _is_cloud_folder(url):
        import shutil

        os.makedirs(output_path, exist_ok=True)
        local_paths = _expand_cloud_folder(url)
        results = {}
        for i, src in enumerate(local_paths, 1):
            fname = os.path.basename(src)
            dest = os.path.join(output_path, fname)
            try:
                shutil.copy2(src, dest)
                print(f"[{i}/{len(local_paths)}] ✔ {fname}")
                results[fname] = "success"
                if log_file:
                    log_advanced(dest, "success", "downloaded", "", log_file, split_csv=False)
            except Exception as exc:
                print(f"[{i}/{len(local_paths)}] ✘ {fname} ({exc})")
                results[fname] = f"failed: {exc}"
                if log_file:
                    log_advanced(dest, "failed", "", str(exc), log_file, split_csv=False)
        return results

    if isinstance(url, (list, tuple)):
        if not os.path.isdir(output_path) and not _is_probable_file_path(output_path):
            os.makedirs(output_path, exist_ok=True)
        elif _is_probable_file_path(output_path):
            raise ValueError("For bulk downloads, output_path must be a directory, not a file.")

        results = {}
        total = len(url)
        print(f"Starting bulk download of {total} files...")
        max_w, controller = _get_worker_setup(workers)

        if max_w > 1:
            from concurrent.futures import ThreadPoolExecutor

            def single_dl_adaptive(args):
                i, u = args
                if controller:
                    controller.wait_if_hot()
                    controller.acquire()
                try:
                    filename = os.path.basename(u.split("?")[0])
                    if not filename or len(filename) > 200:
                        filename = f"download_{i}.jpg"
                    dest = os.path.join(output_path, filename)
                    download(
                        u,
                        dest,
                        prefix=f"[{i}/{total}]",
                        log_file=log_file,
                        progress=False,
                        workers=False,
                    )
                    return u, "success"
                except Exception as e:
                    return u, f"failed: {str(e)}"
                finally:
                    if controller:
                        controller.release()

            try:
                with ThreadPoolExecutor(max_workers=max_w) as executor:
                    for i, (u, status) in enumerate(
                        executor.map(single_dl_adaptive, enumerate(url, 1)), 1
                    ):
                        results[u] = status
                        filename = os.path.basename(u.split("?")[0])
                        if not filename or len(filename) > 200:
                            filename = f"download_{i}.jpg"
                        if status == "success":
                            print(f"[{i}/{total}] ✔ {filename}")
                        else:
                            print(f"[{i}/{total}] ✘ {filename} ({status})")
            finally:
                if controller:
                    controller.stop()

            return results
        else:
            for i, u in enumerate(url, 1):
                filename = os.path.basename(u.split("?")[0])
                if not filename or len(filename) > 200:
                    filename = f"download_{i}.jpg"
                dest = os.path.join(output_path, filename)
                try:
                    download(
                        u,
                        dest,
                        prefix=f"[{i}/{total}]",
                        log_file=log_file,
                        progress=True,
                        workers=False,
                    )
                    results[u] = "success"
                except Exception as e:
                    results[u] = f"failed: {str(e)}"
                    print(f" Failed to download {u}: {e}")
            return results

    try:
        if progress and not prefix:
            import sys

            sys.stdout.write(f"Connecting to {url}...")
            sys.stdout.flush()

        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        if progress:
            callback = partial(_download_progress, output_path, prefix)
            urllib.request.urlretrieve(str(url), str(output_path), reporthook=callback)
        else:
            urllib.request.urlretrieve(str(url), str(output_path))
        if log_file:
            log_advanced(output_path, "success", "downloaded", "", log_file, split_csv=False)
        return True
    except Exception as e:
        import sys

        try:
            sys.stdout.write("\r" + " " * 80 + "\r")
            display_name = os.path.basename(output_path)
            sys.stdout.write(f"{prefix} ✘ {display_name} (Failed)\n")
            sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.write(f"{prefix} [FAIL] {os.path.basename(output_path)}\n")
            sys.stdout.flush()
        if log_file:
            log_advanced(output_path, "failed", "", str(e), log_file, split_csv=False)
        _handle_error("download", e)


def resolve_folder(url, output_path=None):
    """
    List all image files in a cloud folder (gs://, s3://, az://) and return cached local paths.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.

        Returns
        -------
        list[str] — absolute local paths of downloaded images

        Example
        -------
        >>> import photo_ops as ip
        >>> paths = ip.resolve_folder("gs://my-bucket/photos/")
        >>> print(f"Found {len(paths)} images")
        >>>
        >>> # Use paths with any photo_ops function
        >>> for path in paths:
        ...     ip.gray(path, path.replace("cache/", "out/"))
        >>>
        >>> # Check auth before use
        >>> print(ip.auth_status())

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.resolve_folder("gs://bucket/photos/")   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.resolve_folder("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    local_paths = _expand_cloud_folder(_decode_cloud_url(url))
    if output_path:
        import shutil

        os.makedirs(output_path, exist_ok=True)
        copied = []
        for src in local_paths:
            dest = os.path.join(output_path, os.path.basename(src))
            shutil.copy2(src, dest)
            copied.append(dest)
        return copied
    return local_paths


def auth_status():
    """
    Print and return a table showing which cloud auth methods are active.

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.

        Returns
        -------
        str — status message

        Example
        -------
        >>> import photo_ops as ip
        >>> status = ip.auth_status()
        >>> print(status)
        # Prints a table like:
        # ┌─────────────────────────────┬────────────────┐
        # │ Method                      │ Status         │
        # ├─────────────────────────────┼────────────────┤
        # │ GCP Application Default     │ ✅ Active       │
        # │ GCP Service Account         │ ❌ Not set      │
        # │ AWS_ACCESS_KEY_ID           │ ❌ Not set      │
        # │ AZURE_STORAGE_ACCOUNT       │ ❌ Not set      │
        # └─────────────────────────────┴────────────────┘
        >>>
        >>> # Set up GCP auth from terminal
        >>> # $ gcloud auth application-default login
        >>> if "Active" in ip.auth_status():
        ...     ip.gray("gs://bucket/photos/", "out/")

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.auth_status("photo.jpg")   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.auth_status("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    core_module = sys.modules.get("photo_ops.core")
    py_auth_status = getattr(core_module, "py_auth_status", None) if core_module else None
    if not py_auth_status:
        print("auth_status() requires Rust rebuild.")
        return
    rows = py_auth_status()
    width = max(len(m) for _, m, _ in rows) + 2
    for provider, method, available in rows:
        print(f"{provider:<6} {method:<{width}} {'✔' if available else '✗'}")


def refresh_gcp_token():
    """
    Force-refresh the Google Cloud access token (normally auto-refreshed every 55 min).

        Parameters
        ----------
        input_path : str | Path | bytes
            Image file, folder, cloud URL (gs://, s3://, az://) or raw bytes.
        output_path : str | Path | None
            Destination file or folder.  Pass ``None`` to get result as **bytes**.

        Returns
        -------
        None

        Example
        -------
        >>> import photo_ops as ip
        >>> ip.refresh_gcp_token()
        >>> print("Token refreshed")
        >>>
        >>> # Tokens expire after 1 hour.  photo_ops auto-refreshes,
        >>> # but call this manually if you see 401 Unauthorized errors.
        >>>
        >>> # From terminal — set up auth:
        >>> # $ gcloud auth application-default login

        Bytes I/O
        ---------
        Pass ``None`` as *output_path* to receive result as bytes instead of
        writing a file::

            data = ip.refresh_gcp_token()   # returns bytes

        Cloud / folder
        --------------
        Any local path can be replaced with a cloud URL or folder::

            ip.refresh_gcp_token("gs://bucket/folder/", "out/", workers="auto")

        Workers
        -------
        Accepted values: ``False``/``None``/``0`` (serial), ``True`` (10 threads),
        int (exact), ``-1`` (cpu-1), ``0.5`` (50% CPUs), ``"4w"``, ``"50%"``,
        ``"max"``/``"all"``, ``"auto"`` (adaptive).
        Ignored for single-file input.
    """
    _check()
    core_module = sys.modules.get("photo_ops.core")
    py_refresh_gcp_token = (
        getattr(core_module, "py_refresh_gcp_token", None) if core_module else None
    )
    if not py_refresh_gcp_token:
        return None
    tok = py_refresh_gcp_token()
    if tok:
        print(f"[auth/GCP] Token refreshed (first 20 chars): {tok[:20]}…")
    else:
        print("[auth/GCP] No GCP credentials found.")
    return tok


def _run_op_on_folder(cloud_folder_url, output_path, op_fn, log_file=None, workers=False, **kwargs):
    """Expand a cloud folder and apply *op_fn* to every downloaded image."""
    from .batch import _bulk_task  # Import locally to avoid circular dependency

    print(f"[{op_fn.__name__}] Expanding cloud folder: {cloud_folder_url}")
    local_paths = _expand_cloud_folder(cloud_folder_url)

    return _bulk_task(
        local_paths,
        output_path,
        op_fn,
        log_file=log_file,
        op_name=op_fn.__name__,
        workers=workers,
        **kwargs,
    )
