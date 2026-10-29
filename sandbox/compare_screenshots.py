#!/usr/bin/env python3
"""Measure screenshots, optionally comparing a geometrically aligned preview.

This compares 8-bit *display screenshots*, not the original rendered frames,
linear-light values, or isolated beam energy. Reference screenshots must use
the same screen coordinates as --export; this script does not automatically
align different player layouts. Preview alignment must be supplied explicitly.

Example for the 2026-10-29 v3.14 screenshots, from the repository root:
  python3 sandbox/compare_screenshots.py \
    --preview /path/to/preview.png --export /path/to/player.png \
    --scale 0.521875 --dx 243 --dy 52 --exclude-row 450 --exclude-col 1040

The screenshots are read-only and not copied into the repository. See the
branch review for the original screenshots, limits, and registration procedure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

OUT_ROOT = Path(__file__).resolve().parent / "out"

# Encoded-domain luma proxy; input is NOT converted to linear light.
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
DEFAULT_ROIS = [
    ("left_beam_region", 13, 563, 269, 1289),
    ("right_beam_region", 2416, 563, 2546, 1289),
]


def _load(path: Path) -> tuple[Image.Image, dict]:
    with Image.open(path) as source:
        image = source.convert("RGB")
    return image, {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "size": list(image.size)}


def _parse_roi(parts: list[str]) -> tuple[str, int, int, int, int]:
    name, *coords = parts
    if not name:
        raise ValueError("ROI name must not be empty")
    try:
        x0, y0, x1, y1 = map(int, coords)
    except ValueError as exc:
        raise ValueError(f"ROI {name!r} coordinates must be integers") from exc
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ValueError(f"ROI {name!r} must have nonnegative, increasing bounds")
    return name, x0, y0, x1, y1


def _raw_stats(image: Image.Image, roi: tuple[str, int, int, int, int]) -> dict:
    name, x0, y0, x1, y1 = roi
    if x1 > image.width or y1 > image.height:
        raise ValueError(f"ROI {name!r} exceeds image size {image.size}")
    rgb = np.asarray(image.crop((x0, y0, x1, y1)), dtype=np.float64)
    return {"luma_mean": float(np.mean(rgb @ LUMA)),
            "rgb_mean": [float(x) for x in rgb.mean(axis=(0, 1))],
            "pixels": int(rgb.shape[0] * rgb.shape[1])}


def _aligned_stats(preview: np.ndarray, scaled_export: np.ndarray, roi: tuple,
                   scale: float, dx: int, dy: int, rows: list[int],
                   cols: list[int], radius: int) -> dict:
    name, x0, y0, x1, y1 = roi
    sx0, sy0 = math.ceil(x0 * scale), math.ceil(y0 * scale)
    sx1, sy1 = math.floor(x1 * scale), math.floor(y1 * scale)
    if sx1 <= sx0 or sy1 <= sy0:
        raise ValueError(f"ROI {name!r} became empty after scaling")
    if not (0 <= sx0 < sx1 <= scaled_export.shape[1] and
            0 <= sy0 < sy1 <= scaled_export.shape[0] and
            0 <= sx0 + dx < sx1 + dx <= preview.shape[1] and
            0 <= sy0 + dy < sy1 + dy <= preview.shape[0]):
        raise ValueError(f"Aligned ROI {name!r} is outside the screenshot")
    exp = scaled_export[sy0:sy1, sx0:sx1]
    pre = preview[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx]
    xx = np.arange(sx0 + dx, sx1 + dx)[None, :]
    yy = np.arange(sy0 + dy, sy1 + dy)[:, None]
    mask = np.ones(exp.shape[:2], dtype=bool)
    for row in rows:
        mask &= np.abs(yy - row) > radius
    for col in cols:
        mask &= np.abs(xx - col) > radius
    if not mask.any():
        raise ValueError(f"All pixels in ROI {name!r} were excluded")
    pre_luma, exp_luma = pre @ LUMA, exp @ LUMA
    difference = pre_luma - exp_luma
    rgb_difference = pre - exp
    return {
        "pixels_after_mask": int(mask.sum()),
        "preview_luma_mean": float(pre_luma[mask].mean()),
        "export_luma_mean": float(exp_luma[mask].mean()),
        "mean_signed_luma_difference": float(difference[mask].mean()),
        "mean_absolute_luma_difference": float(np.abs(difference)[mask].mean()),
        "p95_absolute_luma_difference": float(np.percentile(np.abs(difference)[mask], 95)),
        "mean_absolute_rgb_difference": float(np.abs(rgb_difference)[mask].mean()),
        "mean_signed_rgb_difference": [float(x) for x in rgb_difference[mask].mean(axis=0)],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", type=Path, required=True, help="screenshot of the exported video/frame")
    ap.add_argument("--preview", type=Path, help="editor preview screenshot, optional")
    ap.add_argument("--reference", action="append", default=[], metavar="NAME=PATH",
                    help="additional same-layout screenshot; may be repeated")
    ap.add_argument("--scale", type=float, help="map export screenshot to preview pixels")
    ap.add_argument("--dx", type=int, help="horizontal offset of the scaled export in the preview screenshot")
    ap.add_argument("--dy", type=int, help="vertical offset of the scaled export in the preview screenshot")
    ap.add_argument("--exclude-row", action="append", type=int, default=[],
                    help="preview overlay row to exclude; may be repeated")
    ap.add_argument("--exclude-col", action="append", type=int, default=[],
                    help="preview overlay column to exclude; may be repeated")
    ap.add_argument("--mask-radius", type=int, default=5, help="pixels on either side of each excluded line")
    ap.add_argument("--roi", nargs=5, action="append", metavar=("NAME", "X0", "Y0", "X1", "Y1"),
                    help="export-coordinate ROI, left/top inclusive and right/bottom exclusive; may be repeated")
    ap.add_argument("--json", type=Path, default=OUT_ROOT / "screenshot-comparison.json")
    args = ap.parse_args()
    if args.preview and (args.scale is None or args.dx is None or args.dy is None):
        ap.error("--preview requires --scale, --dx and --dy")
    if args.scale is not None and (not math.isfinite(args.scale) or args.scale <= 0):
        ap.error("--scale must be finite and positive")
    if args.mask_radius < 0:
        ap.error("--mask-radius must be nonnegative")
    try:
        rois = [_parse_roi(r) for r in args.roi] if args.roi else DEFAULT_ROIS
    except ValueError as exc:
        ap.error(str(exc))
    if len({r[0] for r in rois}) != len(rois):
        ap.error("ROI names must be unique")

    input_paths = {"export": args.export.expanduser().resolve()}
    if args.preview:
        input_paths["preview"] = args.preview.expanduser().resolve()
    for item in args.reference:
        if "=" not in item:
            ap.error("--reference must be NAME=PATH")
        name, path = item.split("=", 1)
        if not name or name in ("preview", "export") or name in input_paths:
            ap.error(f"reference name is empty, reserved or duplicated: {name!r}")
        input_paths[name] = Path(path).expanduser().resolve()
    for path in input_paths.values():
        if not path.is_file():
            ap.error(f"file does not exist: {path}")
    result_path = args.json.expanduser().resolve()
    if result_path in input_paths.values():
        ap.error("--json must not overwrite a screenshot")

    images, metadata = {}, {}
    for name, path in input_paths.items():
        images[name], metadata[name] = _load(path)
    export = images["export"]
    for name, image in images.items():
        if name != "preview" and image.size != export.size:
            ap.error(f"{name!r} size {image.size} differs from export {export.size}; same coordinates required")
    out = {
        "warning": "Encoded 8-bit screenshot luma proxy including background; not original frames or physical beam energy.",
        "images": metadata,
        "rois": {},
    }
    scaled = preview_array = None
    if args.preview:
        new_size = (round(export.width * args.scale), round(export.height * args.scale))
        if min(new_size) < 1:
            ap.error("scaled export is empty")
        scaled = np.asarray(export.resize(new_size, Image.Resampling.LANCZOS), dtype=np.float64)
        preview_array = np.asarray(images["preview"], dtype=np.float64)
        out["preview_alignment"] = {"scale": args.scale, "offset_x": args.dx, "offset_y": args.dy,
                                    "resize": "Lanczos", "scaled_size": list(new_size),
                                    "excluded_preview_rows": args.exclude_row,
                                    "excluded_preview_columns": args.exclude_col,
                                    "mask_radius": args.mask_radius}
    try:
        for roi in rois:
            name, x0, y0, x1, y1 = roi
            row = {"bounds_xyxy": [x0, y0, x1, y1], "screenshots": {}}
            for key, image in images.items():
                if key != "preview":
                    row["screenshots"][key] = _raw_stats(image, roi)
            if preview_array is not None:
                row["aligned_preview_vs_export"] = _aligned_stats(
                    preview_array, scaled, roi, args.scale, args.dx, args.dy,
                    args.exclude_row, args.exclude_col, args.mask_radius)
            out["rois"][name] = row
            print(f"{name}: " + ", ".join(f"{k}={v['luma_mean']:.3f}" for k, v in row["screenshots"].items()))
            if "aligned_preview_vs_export" in row:
                a = row["aligned_preview_vs_export"]
                print(f"  aligned preview={a['preview_luma_mean']:.3f}, export={a['export_luma_mean']:.3f}; "
                      f"mean absolute luma difference={a['mean_absolute_luma_difference']:.3f}")
    except ValueError as exc:
        ap.error(str(exc))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Results: {result_path}\n{out['warning']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
