# Changelog

All notable changes to **photo_ops** are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

> Work in progress for the next release.

---

## [0.1.0] — 2026-08-03 🎉 First Public Release

### 🆕 Added

- **Full API documentation site** deployed to GitHub Pages:
  `https://crazy-writer.github.io/photo_ops/`
  Every function documented with live examples, bytes I/O, cloud, and workers spec.
- **GitHub Actions CI/CD pipeline** — automatic build, test, and publish on tag push:
  - `CI.yml` — lint, cross-platform build, and smoke tests on Windows / Linux / macOS / Apple Silicon
  - `release.yml` — multi-platform wheel build + PyPI publish + GitHub Release creation
  - `docs.yml` — auto-deploy `docs/index.html` to GitHub Pages on every `main` push
- **Bytes I/O on all operations** — pass `output_path=None` to receive result as `bytes`
  instead of writing to disk. Accepts raw `bytes` as input too.
- **Cloud I/O on all folder operations** — `gs://`, `s3://`, `az://` paths accepted
  anywhere a local path or folder is accepted.
- **WorkerSpec resolver** — unified API for parallel workers:
  `False / None / 0 / True / int / -1 / float / "4w" / "50%" / "max" / "auto"`
- **`sub_fix` large-background retry (inner-60% pre-crop)** — portraits with large
  plain white/colour backgrounds (e.g. passport photo on A4 sheet) are now correctly
  handled. Previously went to `not_processed/` — now succeeds.
- **`sub_fix` tilt correction sweep ±25°** — severely tilted scans and ID photos
  now de-tilted correctly (was ±15°).
- **Gamma boost strategy (γ=0.5)** in face detection — catches underexposed portraits
  (mean luminance 50–90) that the standard pipeline misses.
- **Horizontal flip strategy** in face detection — catches mirror-image scans and
  camera roll variants.
- **`add_text()`** — overlay text with font, size, colour, opacity, background fill,
  and named position (`center`, `top`, `bottom`).
- **`color_grade()`** — apply named colour grading looks: `cinematic`, `warm`,
  `cool`, `vintage`.
- **`draw_rect()`** — draw coloured rectangle outline with hex/named colour support.
- **`morphology()`** — dilate / erode binary masks.
- **`extract_frames_ex()`** — key-frame extraction with blur and similarity filtering
  to deduplicate GIF animations.
- **`process()` / `run()` pipeline engine** — chain multiple ops in a single pass,
  inline per-op parameters, cloud or local I/O.
- **`auth_status()`** — terminal-friendly table showing active GCP / AWS / Azure auth.
- **`refresh_gcp_token()`** — force-refresh GCP token (auto-refreshed every 55 min).
- **`PHOTO_OPS_VERBOSE=1`** environment variable — show optional Rust binding warnings.
- **Python 3.8 – 3.13 compatibility** — `from __future__ import annotations` in all
  modules; wheels for all CPython versions.
- **PyPI metadata** — full classifiers, keywords, project URLs.

### 🔄 Changed

- **`homepage()` URL** updated to the new GitHub Pages documentation site.
- **Temp file naming in `sign_fix` / `sub_fix`** — UUID-based temp files in `%TEMP%`
  instead of `output_path + ".rot.jpg"` (eliminates thread-collision races and
  the `None.rot.jpg` junk-file bug).
- **`sub_fix` strict mode** — `force=False` now correctly raises `NotProcessedError`
  when no face is found; previously silently fell back to a centre crop.
- **Signature thresholds (Window 3)** — `local_dark` upper bound `0.80 → 0.85`,
  `local_trans` lower bound `0.025 → 0.018` (catches thin / partial signatures).
- **Binary size** reduced 30–40% via `strip = "symbols"` and `panic = "abort"` in
  `[profile.release]` — zero runtime cost.
- **Version** bumped in `Cargo.toml` and `pyproject.toml`: `0.0.5 → 0.1.0`.

### 🐛 Fixed

- **E0596 Rust compile error** — `detect_score` closure in `detect_tilt_angle` now
  correctly declared `let mut`.
- **Stale pre-release description** in `pyproject.toml` removed.
- **Excel sheet name error** (`/` in sheet names) in audit log generator — now
  sanitised automatically.

---

## [0.0.5] — 2026-07-20

### Added

- Two-Window signature acceptance model in Rust (Otsu-density aligned).
- Multi-strategy face detection: 4-rotation sweep + contrast percentile stretching.
- Dynamic minimum face size (20 px for small images, 30 px standard).
- Tilt correction with 15% improvement threshold to prevent level-image jitter.
- Universal `WorkerSpec` resolver with 9 supported input formats.
- `NotProcessedError` routing to `not_processed/` for low-confidence images.

### Fixed

- Double-inversion bug in `_sign_classify` (Python-level inversion removed; handled in Rust).
- Tilt jitter on upright images (score improvement guard).

---

## [0.0.4] — 2026-07-15

### Added

- `batch.py` with `process()` and `run()` entry points.
- `AdaptiveWorkerController` using `psutil` for dynamic thread scaling.
- Audit log export to `.xlsx` and `.csv`.

---

## [0.0.1 – 0.0.3] — Internal Development

- Initial Rust core with PyO3 bindings.
- `py_to_gray`, `py_resize`, `py_rotate`, `py_fix_orientation`.
- `py_enhance_signature`, `py_is_signature`, `py_has_faces`.
- `py_smart_crop`, `py_smart_crop_v2`, `py_detect_tilt_angle`.
- `batch_resize` and `batch_process` Rayon-parallel batch operations.
