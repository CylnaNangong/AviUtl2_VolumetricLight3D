#!/usr/bin/env python3
"""Reproduce 3D scene brightness comparison on an ``.aup2`` sub-composition.

Example:
  python3 sandbox/verify_aup2_brightness.py \
      --aup2 /path/to/测试工程文件.txt \
      --scene autoblend测试 \
      --assets /path/to/assets_dir \
      --scripts v3.07=/tmp/vl3d_versions/v3.07.obj2 \
                v3.08=/tmp/vl3d_versions/v3.08.obj2 \
                v3.13=/tmp/vl3d_versions/v3.13.obj2 \
                v3.14=Script/VolumetricLight3D.obj2
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vl3dsb.aup2 import load_aup2_scene, map_aup2_params_to_obj2
from vl3dsb.compositor3d import composite_scene_3d
from vl3dsb.obj2 import parse_obj2
from vl3dsb.session import run_frame

DEFAULT_ROIS_2048x1080: Dict[str, Tuple[int, int, int, int]] = {
    "left_beam":         (10, 215, 380, 960),
    "right_beam":        (1933, 2037, 380, 960),
    "left_window_core":  (307, 676, 162, 648),
    "right_window_core": (1475, 1843, 162, 648),
    "char_face":         (963, 1270, 297, 567),
}


def _rec709_luma(rgb255: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb255[..., 0] + 0.7152 * rgb255[..., 1] + 0.0722 * rgb255[..., 2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aup2", type=Path, required=True, help="Path to .aup2 project file")
    ap.add_argument("--scene", default="autoblend测试", help="Scene name or numeric id")
    ap.add_argument("--frame", type=int, default=0, help="Timeline frame inside the scene")
    ap.add_argument("--assets", type=Path, nargs="+", required=True, help="Directories containing project image assets")
    ap.add_argument("--scripts", nargs="+", required=True, help="LABEL=PATH pairs for .obj2 versions to compare")
    ap.add_argument("--oit", default="wboit_uniform", choices=["wboit_uniform", "over", "wboit_exp", "wboit_pow2", "pure_additive"])
    ap.add_argument("--json", type=Path, default=None, help="Optional JSON report path")
    args = ap.parse_args(argv)

    scene = load_aup2_scene(args.aup2, args.scene, frame=args.frame, asset_dirs=args.assets)
    active_vl = next((v for v in scene.vl3d_items if v["enabled"]), scene.vl3d_items[0])
    extra = {"image_max": (4096, 4096), "max_buffer_dim": 8192}

    rows = {}
    print(f"Scene: {scene.name} ({scene.width}x{scene.height}) frame={args.frame} active_layer=L{active_vl['ui_layer']} ({active_vl['effect_name']})")
    print(f"{'Version':16s} {'Time':>6s} {'Quads':>5s} {'PolyA':>7s} {'min_a':>7s} {'ka':>6s} {'LeftBeam':>9s} {'RightBeam':>10s} {'LeftWin':>8s} {'RightWin':>9s} {'CharFace':>9s}")
    print("-" * 102)

    for spec in args.scripts:
        label, raw_p = spec.split("=", 1) if "=" in spec else (Path(spec).stem, spec)
        spath = Path(raw_p).resolve()
        obj2 = parse_obj2(spath)
        params = map_aup2_params_to_obj2(active_vl["raw_kv"], obj2, active_vl["rel_frame"], active_vl["span_frames"])
        params["show_info"] = 0.0
        t0 = time.perf_counter()
        res = run_frame(
            scene.layers, params,
            width=scene.width, height=scene.height, frame=args.frame,
            layer_index=active_vl["ui_layer"], scene_id=scene.scene_id,
            frame_s=active_vl["start"], frame_e=active_vl["end"],
            camera=scene.camera, camera_mode=1,
            script_path=spath, extra_cfg=extra, rasterize_2d=False,
        )
        dt = time.perf_counter() - t0
        beam_q = [q for q in res.host.quads if q["alpha"] < 0.5]
        pa = float(beam_q[0]["alpha"]) if beam_q else 0.0
        cb = res.host.shader_traces.get("beamatlas", {}).get("params", [])
        min_a = float(cb[12 * 4 + 2]) if len(cb) > 50 else 0.0
        ka = float(cb[26 * 4 + 3]) if len(cb) > 107 and obj2.version and obj2.version >= "v3.08" else 0.0

        comp = composite_scene_3d(scene.width, scene.height, scene.camera, scene.layers, res.host.quads, oit_mode=args.oit)
        rgb255 = np.clip(comp[..., :3] * 255.0, 0.0, 255.0)
        rois = {
            k: round(float(_rec709_luma(rgb255[y0:y1, x0:x1]).mean()), 2)
            for k, (x0, x1, y0, y1) in DEFAULT_ROIS_2048x1080.items()
        }
        rows[label] = {"seconds": round(dt, 2), "quads": len(res.host.quads), "poly_alpha": pa, "min_opt_a": min_a, "wboit_ka": ka, "rois": rois}
        print(f"{label:16s} {dt:5.1f}s {len(res.host.quads):5d} {pa:7.4f} {min_a*255:4.0f}/255 {ka:6.2f} "
              f"{rois['left_beam']:9.2f} {rois['right_beam']:10.2f} {rois['left_window_core']:8.2f} {rois['right_window_core']:9.2f} {rois['char_face']:9.2f}")
        del res
        gc.collect()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
