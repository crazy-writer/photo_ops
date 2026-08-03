from __future__ import annotations  # Python 3.8+ compatibility
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

# Import helpers
from ._helpers import (
    _IMAGE_EXTS,
    NotProcessedError,
)
from .logging import log_advanced


def _get_worker_setup(workers):
    from .core import _get_worker_setup as core_get_worker_setup

    return core_get_worker_setup(workers)


_MAX_RECURSION_DEPTH = 20


def _bulk_task(
    input_path,
    output_path,
    task_fn,
    log_file=None,
    op_name="task",
    workers=False,
    stats=False,
    _depth=0,
    **kwargs,
):
    """Internal helper to handle directory processing with routing and auditing. Recursive.

    Threading model
    ---------------
    This function uses Python's ``ThreadPoolExecutor`` (when ``workers > 1``) to
    submit *individual file tasks* concurrently.  Each file task calls into a
    Rust function via PyO3.  The Rust side *releases the GIL* for the duration
    of the image-processing hot path, so all CPU cores can be active
    simultaneously even though this is Python-level threading.

    Rayon (Rust-internal thread pool) additionally parallelises *pixel-level*
    work inside each image operation (e.g. per-row SIMD loops in blur, Otsu
    histogram accumulation).  Rayon threads are independent of the Python thread
    pool — they run fully in Rust with no GIL interaction.

    Summary:
      Python ThreadPoolExecutor  → parallel file dispatch (I/O + FFI overhead)
      Rayon (inside Rust)        → parallel pixel processing (CPU hot paths)
      GIL                        → held only during Python-level bookkeeping,
                                   NOT during image pixel computation.
    """
    if _depth > _MAX_RECURSION_DEPTH:
        print(
            f" [WARNING] Max recursion depth ({_MAX_RECURSION_DEPTH}) reached at '{input_path}'. Skipping deeper subdirectories."
        )
        return {}
    results = {}

    thumbnail = kwargs.pop("thumbnail", False)

    if isinstance(input_path, list):
        files = [(p, os.path.basename(p)) for p in input_path]
    else:
        files = [
            (os.path.join(input_path, f), f)
            for f in os.listdir(input_path)
            if os.path.isfile(os.path.join(input_path, f))
            and f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"))
        ]

    if files:
        success_dir = os.path.join(output_path, "success")
        failed_dir  = os.path.join(output_path, "failed")
        not_proc_dir = os.path.join(output_path, "not_processed")
        os.makedirs(success_dir,  exist_ok=True)
        os.makedirs(failed_dir,   exist_ok=True)
        os.makedirs(not_proc_dir, exist_ok=True)

        max_w, controller = _get_worker_setup(workers)

        if max_w > 1:

            def single_task(item):
                in_file, f = item
                out_file = os.path.join(success_dir, f)
                # FIX #5: write to a UUID tmp file first; rename only on success
                # so a partially-written output never lands in success_dir.
                ext = os.path.splitext(out_file)[1]
                tmp_file = out_file + f".{uuid.uuid4().hex}.tmp{ext}"
                if controller:
                    controller.wait_if_hot()
                    controller.acquire()
                try:
                    task_fn(in_file, tmp_file, **kwargs)
                    os.replace(tmp_file, out_file)
                    if log_file:
                        log_advanced(
                            out_file,
                            "success",
                            op_name,
                            "",
                            log_file,
                            thumbnail=thumbnail,
                            before_path=in_file,
                        )
                    return f, "success"
                except NotProcessedError as e:
                    try:
                        if os.path.exists(tmp_file):
                            os.unlink(tmp_file)
                    except OSError:
                        pass
                    import shutil
                    shutil.copy2(in_file, os.path.join(not_proc_dir, f))
                    if log_file:
                        log_advanced(
                            os.path.join(not_proc_dir, f),
                            "not_processed",
                            op_name,
                            str(e).encode('ascii','replace').decode('ascii'),
                            log_file,
                            thumbnail=thumbnail,
                            before_path=in_file,
                        )
                    print(f" [NOT_PROCESSED] {f}: {str(e).encode('ascii','replace').decode('ascii')}")
                    return f, f"not_processed: {str(e)}"
                except Exception as e:
                    try:
                        if os.path.exists(tmp_file):
                            os.unlink(tmp_file)
                    except OSError:
                        pass
                    import shutil
                    shutil.copy2(in_file, os.path.join(failed_dir, f))
                    if log_file:
                        log_advanced(
                            os.path.join(failed_dir, f),
                            "failed",
                            op_name,
                            str(e).encode('ascii','replace').decode('ascii'),
                            log_file,
                            thumbnail=thumbnail,
                            before_path=in_file,
                        )
                    print(f" [FAILED] {f}: {str(e).encode('ascii','replace').decode('ascii')}")
                    return f, f"failed: {str(e)}"
                finally:
                    if controller:
                        controller.release()

            try:
                with ThreadPoolExecutor(max_workers=max_w) as executor:
                    for f, status in executor.map(single_task, files):
                        results[f] = status
            finally:
                if controller:
                    controller.stop()
        else:
            for in_file, f in files:
                out_file = os.path.join(success_dir, f)
                # FIX #5: atomic write via tmp sidecar.
                ext = os.path.splitext(out_file)[1]
                tmp_file = out_file + f".{uuid.uuid4().hex}.tmp{ext}"
                try:
                    task_fn(in_file, tmp_file, **kwargs)
                    os.replace(tmp_file, out_file)
                    results[f] = "success"
                    if log_file:
                        log_advanced(
                            out_file,
                            "success",
                            op_name,
                            "",
                            log_file,
                            thumbnail=thumbnail,
                            before_path=in_file,
                        )
                except NotProcessedError as e:
                    try:
                        if os.path.exists(tmp_file):
                            os.unlink(tmp_file)
                    except OSError:
                        pass
                    import shutil
                    shutil.copy2(in_file, os.path.join(not_proc_dir, f))
                    results[f] = f"not_processed: {str(e)}"
                    if log_file:
                        log_advanced(
                            os.path.join(not_proc_dir, f),
                            "not_processed",
                            op_name,
                            str(e).encode('ascii','replace').decode('ascii'),
                            log_file,
                            thumbnail=thumbnail,
                            before_path=in_file,
                        )
                    print(f" [NOT_PROCESSED] {f}")
                except Exception as e:
                    try:
                        if os.path.exists(tmp_file):
                            os.unlink(tmp_file)
                    except OSError:
                        pass
                    import shutil
                    shutil.copy2(in_file, os.path.join(failed_dir, f))
                    results[f] = f"failed: {str(e)}"
                    if log_file:
                        log_advanced(
                            os.path.join(failed_dir, f),
                            "failed",
                            op_name,
                            str(e).encode('ascii','replace').decode('ascii'),
                            log_file,
                            thumbnail=thumbnail,
                            before_path=in_file,
                        )
                    print(f" [FAILED] {f}: {str(e).encode('ascii','replace').decode('ascii')}")

        if stats:
            s   = len([v for v in results.values() if v == "success"])
            np  = len([v for v in results.values() if isinstance(v, str) and v.startswith("not_processed")])
            f_c = len([v for v in results.values() if isinstance(v, str) and v.startswith("failed")])
            print(
                f"\n[{op_name.capitalize()}] Complete: {len(results)} total, "
                f"{s} success, {np} not_processed, {f_c} failed."
            )

    if not isinstance(input_path, list):
        subdirs = [d for d in os.listdir(input_path) if os.path.isdir(os.path.join(input_path, d))]
        for d in subdirs:
            if d in ["success", "failed", "subject", "not_processed"]:
                continue
            subdir_results = _bulk_task(
                os.path.join(input_path, d),
                os.path.join(output_path, d),
                task_fn,
                log_file,
                op_name,
                workers=workers,
                stats=stats,
                _depth=_depth + 1,
                thumbnail=thumbnail,
                **kwargs,
            )
            if subdir_results:
                results[d] = subdir_results

    return results


def process(config: dict) -> dict | list[dict]:
    """Advanced Flexible Batch Engine with Intelligent Routing.

    Processes one image file (when ``input`` is a file path) or an entire
    directory tree (when ``input`` is a folder or cloud URL) through a
    user-defined pipeline of image operations.

    Parameters
    ----------
    config : dict
        Required keys:
          ``input``   – local file/folder path, list of paths, or cloud URL
                        (``gs://``, ``s3://``, ``az://``)
          ``output``  – destination folder (created automatically)
          ``ops``     – list of operation names or ``{op: [params]}`` dicts.
                        Supported ops: ``fix_turn``, ``gray``, ``resize``,
                        ``scale``, ``crop``, ``rotate``, ``flip``, ``blur``,
                        ``sharpen``, ``enhance``, ``deblur``, ``edges``,
                        ``edge_art``, ``portrait``, ``brightness``,
                        ``saturation``, ``tint``, ``color_grade``,
                        ``compress``, ``find_objects``, ``find_faces``,
                        ``remove_bg``, ``remove_bg_v2``, ``auto_crop``,
                        ``crop_face``, ``crop_center``, ``saliency_crop``,
                        ``ai_upscale``, ``draw_rect``, ``add_text``,
                        ``sign_fix``, ``sub_fix``, ``enhance_signature``.

        Optional keys:
          ``workers``  – ``False`` (sequential), ``True`` (auto threads),
                         ``"auto"`` (adaptive), or an integer thread count.
          ``width``    – target width for resize/crop operations.
          ``height``   – target height for resize/crop operations.
          ``stats``    – ``True`` to print a completion summary.
          ``log_file`` – path to Excel (.xlsx) or CSV (.csv) audit log.
          ``fmt``      – ``"xlsx"`` (default) or ``"csv"``.
          ``merge``    – ``True`` to merge all log shards into one file.
          ``thumbnail``– ``True`` to embed image thumbnails in the log.

    Returns
    -------
    dict
        Single-file result: ``{"status": "success"|"failed",
                               "file": "filename", "actions": [...]}``
    list[dict]
        Directory/cloud result: one dict per input file.

    Threading & GIL Model
    ---------------------
    Directory processing spawns a ``ThreadPoolExecutor``; each worker calls
    ``process()`` recursively for a single file.  The Rust PyO3 extension
    **releases the GIL** inside every image-processing function, so Python
    threads can run concurrently on separate CPU cores.

    Rayon (inside the Rust core) further parallelises pixel-level loops within
    a single image operation — this is invisible to Python but provides
    significant throughput gains for large images.

    GIL is only re-acquired during Python bookkeeping (log writing, ``os.rename``,
    result dict construction). For maximum throughput with large folders, use
    ``workers="auto"`` and keep the ``ops`` list short.

    Example
    -------
    >>> import photo_ops as ip
    >>> ip.process({
    ...     "input":  "scans/",
    ...     "output": "results/",
    ...     "ops":    ["fix_turn", "sign_fix", "gray"],
    ...     "workers": "auto",
    ...     "width":  400, "height": 200,
    ...     "stats":  True,
    ...     "log_file": "audit.xlsx",
    ... })
    """
    import shutil as _shutil

    # Import ops from core or other modules as needed
    # For now, let's assume they are imported from photo_ops.core or photo_ops!
    import photo_ops

    from .cloud import _decode_cloud_url, _expand_cloud_folder, _is_cloud_folder

    input_path = config.get("input")
    output_path = config.get("output")
    ops = config.get("ops", [])
    show_stats = config.get("stats", False)

    fmt = config.get("fmt", "xlsx")
    merge = config.get("merge", False)
    thumbnail = config.get("thumbnail", False)

    log_file = config.get("log_file")
    if not log_file:
        log_file = os.path.join(output_path, f"log.{fmt}")

    w = config.get("width")
    h = config.get("height")

    if not input_path or not output_path:
        raise ValueError("Config must contain 'input' and 'output' paths.")

    os.makedirs(output_path, exist_ok=True)
    success_dir = os.path.join(output_path, "success")
    failed_dir = os.path.join(output_path, "failed")
    for d in [success_dir, failed_dir]:
        os.makedirs(d, exist_ok=True)

    subject_dir = os.path.join(output_path, "subjects")
    if "sign_fix" in ops:
        os.makedirs(subject_dir, exist_ok=True)

    # Cloud folder → stage locally
    local_paths = None
    _decoded = _decode_cloud_url(str(input_path))
    if _is_cloud_folder(_decoded):
        print("[process] Cloud folder detected – downloading images …")
        local_paths = _expand_cloud_folder(_decoded)

    if isinstance(input_path, list) or local_paths is not None or os.path.isdir(input_path):
        if local_paths is not None:
            files = local_paths
        elif isinstance(input_path, list):
            files = input_path
        else:
            files = [
                os.path.join(input_path, f)
                for f in os.listdir(input_path)
                if os.path.isfile(os.path.join(input_path, f)) and f.lower().endswith(_IMAGE_EXTS)
            ]

        results = []
        max_w, controller = _get_worker_setup(config.get("workers", False))

        def process_file(f):
            if controller:
                controller.wait_if_hot()
                controller.acquire()
            try:
                return process({**config, "input": f})
            finally:
                if controller:
                    controller.release()

        try:
            with ThreadPoolExecutor(max_workers=max(max_w, 1)) as executor:
                results.extend(list(executor.map(process_file, files)))
        finally:
            if controller:
                controller.stop()

        if not isinstance(input_path, list) and local_paths is None:
            subdirs = [
                d for d in os.listdir(input_path) if os.path.isdir(os.path.join(input_path, d))
            ]
            for d in subdirs:
                if d in ["success", "failed", "subjects"]:
                    continue
                subdir_results = process(
                    {
                        **config,
                        "input": os.path.join(input_path, d),
                        "output": os.path.join(output_path, d),
                    }
                )
                if isinstance(subdir_results, list):
                    results.extend(subdir_results)
                else:
                    results.append(subdir_results)

        if show_stats:
            s = len([r for r in results if r.get("status") == "success"])
            print(
                f"\nBatch Complete: {len(results)} total, {s} success, {len(results) - s} failed."
            )
        return results

    filename = os.path.basename(input_path)
    current_file = input_path
    actions_taken = []
    status = "success"
    reason = ""

    try:
        # FIX #3: UUID temp path prevents collisions when workers > 1 and two
        # input files share the same basename (e.g. subdir_a/IMG_001.jpg and
        # subdir_b/IMG_001.jpg both map to the same output_path).
        temp_out = os.path.join(output_path, f"tmp_{uuid.uuid4().hex}_{filename}")

        for op_entry in ops:
            op_name = op_entry if isinstance(op_entry, str) else list(op_entry.keys())[0]
            op_params = [] if isinstance(op_entry, str) else op_entry[op_name]
            if not isinstance(op_params, (list, tuple)):
                op_params = [op_params]

            # Use photo_ops.<op> instead of local functions if they are not available!
            if op_name == "sign_fix":
                photo_ops.sign_fix(current_file, temp_out, w, h)
                current_file = temp_out
                actions_taken.append("sign_fix")
            
            elif op_name in ("gray", "grayscale", "to_gray"):
                photo_ops.gray(current_file, temp_out)
                current_file = temp_out
                actions_taken.append("grayscale")
            
            
            elif op_name == "resize":
                _w = int(op_params[0]) if len(op_params) > 0 else (w or 800)
                _h = int(op_params[1]) if len(op_params) > 1 else (h or 600)
                _kr = bool(op_params[2]) if len(op_params) > 2 else False
                photo_ops.resize(current_file, temp_out, _w, _h, _kr)
                current_file = temp_out
                actions_taken.append(f"resize_{_w}x{_h}")
            elif op_name == "scale":
                _f = float(op_params[0]) if op_params else 1.0
                photo_ops.scale(current_file, temp_out, _f)
                current_file = temp_out
                actions_taken.append(f"scale_{_f}")
            elif op_name == "crop":
                if len(op_params) >= 4:
                    photo_ops.crop(
                        current_file,
                        temp_out,
                        int(op_params[0]),
                        int(op_params[1]),
                        int(op_params[2]),
                        int(op_params[3]),
                    )
                else:
                    photo_ops.crop_center(current_file, temp_out, w or 500, h or 500)
                current_file = temp_out
                actions_taken.append("crop")
            elif op_name == "rotate":
                _angle = int(op_params[0]) if op_params else 90
                photo_ops.rotate(current_file, temp_out, _angle)
                current_file = temp_out
                actions_taken.append(f"rotate_{_angle}")
            elif op_name == "flip":
                _dir = str(op_params[0]) if op_params else "horizontal"
                photo_ops.flip(current_file, temp_out, _dir)
                current_file = temp_out
                actions_taken.append(f"flip_{_dir}")
            elif op_name in ("blur",):
                _amt = int(op_params[0]) if op_params else 5
                photo_ops.blur(current_file, temp_out, _amt)
                current_file = temp_out
                actions_taken.append(f"blur_{_amt}")
            elif op_name == "sharpen":
                photo_ops.sharpen(current_file, temp_out)
                current_file = temp_out
                actions_taken.append("sharpen")
            elif op_name == "enhance":
                photo_ops.enhance(current_file, temp_out)
                current_file = temp_out
                actions_taken.append("enhance")
            elif op_name == "deblur":
                photo_ops.deblur(current_file, temp_out)
                current_file = temp_out
                actions_taken.append("deblur")
            elif op_name in ("edges", "edge"):
                photo_ops.edges(current_file, temp_out)
                current_file = temp_out
                actions_taken.append("edges")
            elif op_name == "edge_art":
                _bg = int(op_params[0]) if op_params else 35
                photo_ops.edge_art(current_file, temp_out, _bg)
                current_file = temp_out
                actions_taken.append("edge_art")
            elif op_name in ("portrait", "blur_bg"):
                _bg = int(op_params[0]) if op_params else 25
                photo_ops.portrait(current_file, temp_out, _bg)
                current_file = temp_out
                actions_taken.append("portrait")
            elif op_name == "brightness":
                _v = float(op_params[0]) if op_params else 1.0
                photo_ops.brightness(current_file, temp_out, _v)
                current_file = temp_out
                actions_taken.append(f"brightness_{_v}")
            elif op_name == "saturation":
                _v = float(op_params[0]) if op_params else 1.0
                photo_ops.saturation(current_file, temp_out, _v)
                current_file = temp_out
                actions_taken.append(f"saturation_{_v}")
            elif op_name == "tint":
                _r = float(op_params[0]) if len(op_params) > 0 else 0
                _g = float(op_params[1]) if len(op_params) > 1 else 0
                _b = float(op_params[2]) if len(op_params) > 2 else 0
                photo_ops.tint(current_file, temp_out, _r, _g, _b)
                current_file = temp_out
                actions_taken.append("tint")
            elif op_name == "color_grade":
                _preset = str(op_params[0]) if op_params else "cinematic"
                photo_ops.color_grade(current_file, temp_out, _preset)
                current_file = temp_out
                actions_taken.append(f"color_grade_{_preset}")
            elif op_name == "compress":
                _fmt = str(op_params[0]) if len(op_params) > 0 else "webp"
                _q = int(op_params[1]) if len(op_params) > 1 else 80
                photo_ops.compress(current_file, temp_out, _fmt, _q)
                current_file = temp_out
                actions_taken.append(f"compress_{_fmt}_{_q}")
            
            
            
            elif op_name == "auto_crop":
                photo_ops.auto_crop(current_file, temp_out)
                current_file = temp_out
                actions_taken.append("auto_crop")
            
            elif op_name == "crop_center":
                photo_ops.crop_center(current_file, temp_out, w or 500, h or 500)
                current_file = temp_out
                actions_taken.append("crop_center")
            
            
            elif op_name == "draw_rect":
                _rx = int(op_params[0]) if len(op_params) > 0 else 0
                _ry = int(op_params[1]) if len(op_params) > 1 else 0
                _rw = int(op_params[2]) if len(op_params) > 2 else 100
                _rh = int(op_params[3]) if len(op_params) > 3 else 100
                photo_ops.draw_rect(current_file, temp_out, _rx, _ry, _rw, _rh)
                current_file = temp_out
                actions_taken.append("draw_rect")
            elif op_name == "add_text":
                _txt = str(op_params[0]) if len(op_params) > 0 else "Text"
                photo_ops.add_text(current_file, temp_out, _txt)
                current_file = temp_out
                actions_taken.append("add_text")
            else:
                raise ValueError(f"Unsupported operation '{op_name}' in process().")

        final_dest = os.path.join(success_dir, filename)
        if current_file == temp_out:
            if os.path.exists(final_dest):
                os.remove(final_dest)
            os.rename(temp_out, final_dest)
        else:
            _shutil.copy2(input_path, final_dest)

        if log_file:
            log_advanced(
                final_dest,
                "success",
                ", ".join(actions_taken),
                "",
                log_file,
                split_csv=not merge,
                thumbnail=thumbnail,
                before_path=input_path,
            )
        if show_stats:
            print("\nFile Complete: 1 total, 1 success, 0 failed.")
        return {"status": "success", "file": filename, "actions": actions_taken}

    except Exception as e:
        reason = str(e)
        return {"status": "failed", "file": filename, "message": reason}


run = process
