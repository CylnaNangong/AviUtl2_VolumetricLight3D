#!/usr/bin/env python3
"""Compare two or three .obj2 versions in the same small CPU sandbox scene.

Example (paths are examples; do not copy user-owned assets into the repo):
  python3 sandbox/compare_brightness.py \
    --reference /path/to/v306.obj2 \
    --before Script/VolumetricLight3D.obj2 \
    --after /path/to/candidate/Script/VolumetricLight3D.obj2 \
    --window /path/to/window.jpg --character /path/to/character.png \
    --camera-probes

This is a controlled, approximate scene, not a reconstruction of an .aup2
project and not a measurement of AviUtl2's GPU output. See --help and README.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image, ImageOps

from vl3dsb import assets, session
from vl3dsb.host import Host, Layer

W, H = 256, 144
BASE = {"src_layer": 1, "intensity": 180.0, "res_mode": 2, "ray_mode": 0,
        "auto_slice": True, "log_mode": False}
SCENARIOS = {
    "no_occlusion": {"occ_mode": 0},
    "character_occlusion": {"occ_mode": 2, "occ_target": 2, "occ_list": "3"},
    "character_and_fog": {"occ_mode": 2, "occ_target": 2, "occ_list": "3", "fog": 70},
    "point_geometry": {"occ_mode": 0, "ray_mode": 1},
}
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float64)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_asset(path: Path, max_size: int) -> np.ndarray:
    with Image.open(path) as source:
        img = ImageOps.exif_transpose(source).convert("RGBA")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        arr = np.array(img, dtype=np.uint8)
    return assets.premultiply(arr[..., :3], arr[..., 3].astype(np.float32) / 255.0)


def _distinct_atlases(quads) -> List[np.ndarray]:
    result = []
    ids = set()
    for quad in quads:
        atlas = quad.get("atlas")
        if atlas is not None and id(atlas) not in ids:
            ids.add(id(atlas))
            result.append(atlas)
    return result


def _wboit_ka(result, path: Path):
    # v3.06's cbuffer does not have p26: the same numeric offset is part of
    # its occluder array and must not be misreported as wboit_ka.
    source = session.script(path).shaders["beamatlas"]
    import re
    if not re.search(r"\bfloat4\s+p26\s*;", source):
        return None
    return result.host.shader_traces["beamatlas"]["params"][26 * 4 + 3]


def _modeled_values(result) -> Dict:
    out = {}
    for mode in ("weighted", "over"):
        image = result.assumed_emission(mode)
        rgb, opacity = image[..., :3], image[..., 3]
        # Input images are 8-bit encoded, so this is an encoded-domain luma
        # proxy, not a calibrated linear-light or GPU brightness measurement.
        out[mode] = {
            "emission_luma_unclipped": float(np.mean(rgb @ LUMA)),
            "mean_opacity": float(opacity.mean()),
            "black_composite_luma": float(np.mean(np.clip(rgb, 0, 1) @ LUMA)),
            "gray_0_5_composite_luma": float(np.mean(
                np.clip(rgb + 0.5 * (1.0 - opacity[..., None]), 0, 1) @ LUMA)),
        }
    return out


def _atlas_compare(before, after) -> Dict:
    aa = _distinct_atlases(before.host.quads)
    bb = _distinct_atlases(after.host.quads)
    if len(aa) != len(bb) or any(a.shape != b.shape for a, b in zip(aa, bb)):
        return {"comparable": False, "before_chunks": len(aa), "after_chunks": len(bb)}
    shared = only_before = only_after = rgb_diff = alpha_diff = 0
    for a, b in zip(aa, bb):
        am, bm = a[..., 3] > 0, b[..., 3] > 0
        both = am & bm
        shared += int(both.sum())
        only_before += int((am & ~bm).sum())
        only_after += int((bm & ~am).sum())
        rgb_diff += int(np.any(a[..., :3][both] != b[..., :3][both], axis=-1).sum())
        alpha_diff += int((a[..., 3][both] != b[..., 3][both]).sum())
    qa, qb = before.host.quads, after.host.quads
    same_geometry = len(qa) == len(qb) and all(
        a["alpha"] == b["alpha"] and np.array_equal(a["verts"], b["verts"])
        for a, b in zip(qa, qb))
    return {"comparable": True, "chunks": len(aa), "identical_quad_geometry": bool(same_geometry),
            "shared_lit_texels": shared, "before_only_lit_texels": only_before,
            "after_only_lit_texels": only_after, "different_rgb_on_shared_texels": rgb_diff,
            "different_alpha_on_shared_texels": alpha_diff}


def _camera_probe(path: Path, source_layer: int, light_type: int) -> Tuple[dict, object]:
    layers = session.standard_scene(96, 54, with_occluder=False, light_size=64)
    params = {"src_layer": source_layer, "cam_light_mode": 1, "intensity": 180.0,
              "res_mode": 2, "auto_slice": False, "slices": 16, "log_mode": False}

    def factory(cfg):
        host = Host(session.script(path), layers, cfg, shader_lib=session.shader_library(path),
                    params=params, native=session.native())
        # Script's test hook, not a real CameraLight64 / GPU or host lighting.
        host.lua.execute(f'''_G.VL3D_TEST_CAM_LIGHTS = {{
            count=1, unlit=0, amb_r=0, amb_g=0, amb_b=0, peak_gain=1,
            suppressed=false, primary={{ltype={light_type}, dx=0, dy=0, dz=-1,
              r=1, g=1, b=1, lum=1, px=0, py=0, pz=-800, cos_ang=.7}}
        }}''')
        return host

    result = session.run_frame(layers, params, width=96, height=54, layer_index=4,
                               camera_mode=1, script_path=path, host_factory=factory)
    if result.host.errors or "beamatlas" not in result.host.shader_traces:
        raise RuntimeError(f"camera probe failed: {result.host.errors}; log={result.log}")
    uniforms = result.host.shader_traces["beamatlas"]["params"]
    return {"source_layer": source_layer, "camera_light_type": light_type,
            "wboit_ka": _wboit_ka(result, path), "spot_ray_env_mode": uniforms[25 * 4 + 3],
            "quad_count": len(result.host.quads)}, result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, required=True, help="baseline .obj2, e.g. v3.13")
    ap.add_argument("--after", type=Path, required=True, help="candidate .obj2, e.g. v3.14")
    ap.add_argument("--reference", type=Path, help="optional older reference .obj2, e.g. v3.06")
    ap.add_argument("--window", type=Path, help="source layer image; use together with --character")
    ap.add_argument("--character", type=Path, help="foreground occluder image")
    ap.add_argument("--camera-probes", action="store_true", help="also exercise simulated camera-light paths")
    ap.add_argument("--intensity-sweep", action="store_true", help="also compare seven intensities with occlusion")
    ap.add_argument("--json", type=Path, default=session.OUT_ROOT / "brightness-compare.json")
    args = ap.parse_args()
    if bool(args.window) != bool(args.character):
        ap.error("--window and --character must be supplied together")
    paths = {k: p.expanduser().resolve() for k, p in
             (("reference", args.reference), ("before", args.before), ("after", args.after)) if p}
    for path in list(paths.values()) + ([args.window, args.character] if args.window else []):
        if not path.is_file():
            ap.error(f"file does not exist: {path}")
    if args.window:
        window = _load_asset(args.window, 220)
        character = _load_asset(args.character, 120)
        asset_info = {"kind": "provided files", "window_sha256": _sha256(args.window),
                      "character_sha256": _sha256(args.character)}
    else:
        window = assets.stained_window(220, 158)
        character = assets.silhouette(120, 120)
        asset_info = {"kind": "sandbox procedural fixtures"}
    asset_info.update({"window_rgba_shape": list(window.shape),
                       "character_rgba_shape": list(character.shape)})
    layers = [Layer(index=1, image=window),
              Layer(index=3, image=character, x=15, y=10, z=-200)]
    out = {
        "warning": "Approximate equal-depth and over CPU models; NOT AviUtl2 or an .aup2 reproduction.",
        "canvas": [W, H], "frame": 0, "assets": asset_info,
        "versions": {name: {"path": str(path), "sha256": _sha256(path),
                            "information": session.script(path).information}
                     for name, path in paths.items()},
        "scenarios": {},
    }
    print(out["warning"])
    for scenario, overrides in SCENARIOS.items():
        params = {**BASE, **overrides}
        print(f"\n[{scenario}]")
        results = {}
        rows = {}
        for name, path in paths.items():
            result = session.run_frame(layers, params, width=W, height=H, layer_index=4, script_path=path)
            if result.host.errors or result.volume is None or "beamatlas" not in result.host.shader_traces:
                raise RuntimeError(f"{scenario}/{name} failed: {result.host.errors}; log={result.log}")
            results[name] = result
            rows[name] = {"wboit_ka": _wboit_ka(result, path), "quads": len(result.host.quads),
                          "atlas_chunks": len(_distinct_atlases(result.host.quads)),
                          "models": _modeled_values(result)}
            values = rows[name]["models"]["weighted"]
            print(f"  {name:9} ka={str(rows[name]['wboit_ka']):>4}  "
                  f"luma={values['emission_luma_unclipped']:.6f}  opacity={values['mean_opacity']:.6f}  "
                  f"quads={rows[name]['quads']}")
        pair = _atlas_compare(results["before"], results["after"])
        print(f"  atlas compare: {pair}")
        out["scenarios"][scenario] = {"parameters": params, "results": rows,
                                     "before_after_atlas": pair}
    if args.intensity_sweep:
        out["intensity_sweep"] = []
        print("\n[intensity sweep: character occlusion, modeled light only]")
        for intensity in (1.0, 5.0, 10.0, 30.0, 60.0, 180.0, 600.0):
            params = {**BASE, **SCENARIOS["character_occlusion"], "intensity": intensity}
            row = {"intensity": intensity, "results": {}}
            for name, path in paths.items():
                result = session.run_frame(layers, params, width=W, height=H,
                                           layer_index=4, script_path=path)
                if result.host.errors or "beamatlas" not in result.host.shader_traces:
                    raise RuntimeError(f"intensity {intensity}/{name} failed: {result.host.errors}")
                trace = result.host.shader_traces["beamatlas"]["params"]
                row["results"][name] = {
                    "emission_luma_unclipped": _modeled_values(result)["weighted"]["emission_luma_unclipped"],
                    "min_opt_a": trace[12 * 4 + 2],
                    "drawpoly_alpha": max((q["alpha"] for q in result.host.quads), default=0.0),
                }
            out["intensity_sweep"].append(row)
            print(f"  {intensity:>5g}: " + " ".join(
                f"{k}={v['emission_luma_unclipped']:.8f}" for k, v in row["results"].items()))
    if args.camera_probes:
        out["camera_probes"] = []
        print("\n[camera probes: synthetic test hook, not actual host lights]")
        for source_layer, light_type in ((0, 4), (0, 5), (0, 3), (1, 4)):
            before, rb = _camera_probe(paths["before"], source_layer, light_type)
            after, ra = _camera_probe(paths["after"], source_layer, light_type)
            pair = _atlas_compare(rb, ra)
            equal_atlases = (pair.get("comparable") and pair["before_only_lit_texels"] == 0 and
                             pair["after_only_lit_texels"] == 0 and
                             pair["different_rgb_on_shared_texels"] == 0 and
                             all(np.array_equal(a, b) for a, b in
                                 zip(_distinct_atlases(rb.host.quads), _distinct_atlases(ra.host.quads))))
            item = {"before": before, "after": after, "atlases_identical": bool(equal_atlases),
                    "quad_geometry_identical": bool(pair.get("identical_quad_geometry"))}
            out["camera_probes"].append(item)
            print(f"  source={source_layer} type={light_type}: "
                  f"ka {before['wboit_ka']} -> {after['wboit_ka']}, "
                  f"spot={before['spot_ray_env_mode']} -> {after['spot_ray_env_mode']}, "
                  f"same_atlas={bool(equal_atlases)}")
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nresults: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
