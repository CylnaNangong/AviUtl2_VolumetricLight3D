#!/usr/bin/env python3
"""Run a small fixture informed by the 2026-10-29 v3.14 UI screenshot.

Visible settings: source layer 2, intensity 100, threshold 15, high atlas
quality, auto slices, foreground occlusion mode 1, and AutoBlend on layer 3.
The rest of the scene is *assumed*, not read from the .aup2. In particular the
fixtures, camera geometry, render size, atlas size and slice count are not the
real project. This is a CPU hypothesis model, not an AviUtl2 pixel comparison.

The extra AutoBlend quad is separated from the beam quads before calculating the
beam-only luma proxy, because it is not an emissive beam slice.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import compare_brightness as compare
from vl3dsb import assets, session
from vl3dsb.host import Layer
from vl3dsb.raster import rasterize_emission

WIDTH, HEIGHT = 256, 135
PARAMS = {"src_layer": 2, "intensity": 100.0, "threshold": 15.0, "res_mode": 0,
          "auto_slice": True, "occ_mode": 1, "occ_target": 2, "occ_list": "3",
          "ab_enable": True, "ab_target": 1, "ab_list": "3", "log_mode": True}


def _run(path: Path, layers: list[Layer], *, gui: bool):
    result = session.run_frame(layers, PARAMS, width=WIDTH, height=HEIGHT,
                               layer_index=5, camera_mode=0, gui=gui, script_path=path)
    if result.host.errors or "beamatlas" not in result.host.shader_traces:
        raise RuntimeError(f"{path}: {result.host.errors}; log={result.log}")
    trace = result.host.shader_traces["beamatlas"]
    atlas_size = tuple(trace["size"])
    beam_quads = [q for q in result.host.quads if q.get("atlas") is not None and
                  tuple(q["atlas"].shape[:2]) == atlas_size]
    non_beam_quads = [q for q in result.host.quads if q.get("atlas") is None or
                      tuple(q["atlas"].shape[:2]) != atlas_size]
    if not beam_quads or not non_beam_quads or "ps_vl_ab_shade" not in result.host.shader_calls:
        raise RuntimeError("Fixture must exercise both beamatlas and a distinct AutoBlend quad")
    ka = compare._wboit_ka(result, path)
    if ka is not None and abs(trace["params"][21 * 4]) > 1e-8:
        raise RuntimeError("Unexpected camera-light compensation in the no-light fixture")
    image = rasterize_emission(None, beam_quads, WIDTH, HEIGHT, mode="weighted")
    row = {
        "script_sha256": compare._sha256(path),
        "gui": gui,
        "wboit_ka": ka,
        "beam_quad_count": len(beam_quads),
        "non_beam_quad_count": len(non_beam_quads),
        "atlas_size": list(atlas_size),
        "atlas_chunks": len(compare._distinct_atlases(beam_quads)),
        "autoblend_shade_called": True,
        "assumed_beam_luma_unclipped": float(np.mean(image[..., :3] @ compare.LUMA)),
        "log": result.log,
    }
    return row, beam_quads, image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, help="optional v3.06 .obj2")
    ap.add_argument("--before", type=Path, required=True, help="baseline v3.13 .obj2")
    ap.add_argument("--after", type=Path, required=True, help="candidate v3.14 .obj2")
    ap.add_argument("--window", type=Path, help="light image; use together with --character")
    ap.add_argument("--character", type=Path, help="foreground image")
    ap.add_argument("--json", type=Path, default=session.OUT_ROOT / "observed-scene-probe.json")
    args = ap.parse_args()
    if bool(args.window) != bool(args.character):
        ap.error("--window and --character must be supplied together")
    paths = {name: path.expanduser().resolve() for name, path in
             (("reference", args.reference), ("before", args.before), ("after", args.after)) if path}
    supplied = [p.expanduser().resolve() for p in (args.window, args.character) if p]
    for path in [*paths.values(), *supplied]:
        if not path.is_file():
            ap.error(f"file does not exist: {path}")
    result_path = args.json.expanduser().resolve()
    if result_path in [*paths.values(), *supplied]:
        ap.error("--json must not overwrite an input file")
    if supplied:
        window, character = compare._load_asset(supplied[0], 220), compare._load_asset(supplied[1], 120)
        asset_info = {"kind": "provided files", "window_sha256": compare._sha256(supplied[0]),
                      "character_sha256": compare._sha256(supplied[1])}
    else:
        window, character = assets.stained_window(220, 158), assets.silhouette(120, 120)
        asset_info = {"kind": "sandbox procedural fixtures"}
    layers = [Layer(index=2, image=window), Layer(index=3, image=character, x=15, y=10, z=-200)]
    out = {
        "warning": "Parameter-informed CPU fixture, NOT the user's .aup2 or an actual GPU/export render.",
        "canvas": [WIDTH, HEIGHT], "parameters": PARAMS, "assets": asset_info,
        "assumptions": ["Layer placement, source/occluder sizes, ray direction and camera are synthetic.",
                        "Simulator image_max limits the atlas; the real screenshot shows 160 slices and 3584x3584x3.",
                        "The modeled luma excludes AutoBlend's quad and does not represent the final composite."],
        "versions": {},
    }
    print(out["warning"])
    results = {}
    for name, path in paths.items():
        row, quads, image = _run(path, layers, gui=True)
        out["versions"][name] = row
        results[name] = (quads, image)
        print(f"{name}: light={row['assumed_beam_luma_unclipped']:.9f}, "
              f"beam quads={row['beam_quad_count']}, atlas={row['atlas_size']}x{row['atlas_chunks']}, "
              f"AutoBlend={row['autoblend_shade_called']}")
    export_row, export_quads, export_image = _run(paths["after"], layers, gui=False)
    preview_quads, preview_image = results["after"]
    same_geometry = len(preview_quads) == len(export_quads) and all(
        x["alpha"] == y["alpha"] and np.array_equal(x["verts"], y["verts"])
        for x, y in zip(preview_quads, export_quads))
    pa, ea = compare._distinct_atlases(preview_quads), compare._distinct_atlases(export_quads)
    same_atlases = len(pa) == len(ea) and all(np.array_equal(x, y) for x, y in zip(pa, ea))
    out["after_mock_export"] = export_row
    out["mock_gui_vs_export"] = {"beam_geometry_identical": bool(same_geometry),
                                 "beam_atlases_identical": bool(same_atlases),
                                 "beam_model_output_identical": bool(np.array_equal(preview_image, export_image))}
    print(f"candidate mock GUI/export: {out['mock_gui_vs_export']}")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Results: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
