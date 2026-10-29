#!/usr/bin/env python3
"""Paired, temporary HLSL ablation of the v3.13 alpha-encoding change.

Runs the same simulated non-camera-light scene with proportional or constant
output alpha, and with the shader's proportional-alpha discard gate on/off.
It also runs the candidate script and checks whether its atlas matches the
constant/no-gate mutation. This is NOT an AviUtl2 render or an .aup2 replay;
the two CPU composition models and fragment cutoff are explicit assumptions.

For example, from the repository root:
  python3 sandbox/ablate_brightness.py \
    --after sandbox/out/repo-cc99521/Script/VolumetricLight3D.obj2 \
    --window /path/to/window.jpg --character /path/to/character.png

The source scripts are read-only. Mutations are made only in temporary files
under sandbox/out/; supplied images are loaded but are not copied into the repo.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np

import compare_brightness as compare
from vl3dsb import assets, session
from vl3dsb.host import Layer

GATE = "if (out_a < min_a - (0.25 / 255.0))"
OUTPUT = "        return float4(q_rgb, out_a);"


def _variants(source: str) -> dict[str, str]:
    """Make four factorial variants; fail loudly if the source has drifted.

    The 'constant' mutation forces out_a *after* the original gate. Thus its
    on/off comparison isolates the shader gate with the output alpha held fixed.
    Other gates, including q_rgb's zero check and the assumed later host cutoff,
    remain in place. No claim is made that these counterfactuals are host renders.
    """
    for needle in (GATE, OUTPUT, "wboit_ka = 0.24"):
        if source.count(needle) != 1:
            raise ValueError(f"Expected exactly one {needle!r}; got {source.count(needle)}")
    without_gate = source.replace(GATE, "if (out_a < -1.0)")
    constant = "        out_a = p12.z;\n" + OUTPUT
    return {
        "proportional_gate_on": source,
        "proportional_gate_off": without_gate,
        "constant_gate_on": source.replace(OUTPUT, constant),
        "constant_gate_off": without_gate.replace(OUTPUT, constant),
    }


def _equivalence(a, b) -> dict:
    pair = compare._atlas_compare(a, b)
    aa = compare._distinct_atlases(a.host.quads)
    bb = compare._distinct_atlases(b.host.quads)
    identical = len(aa) == len(bb) and all(np.array_equal(x, y) for x, y in zip(aa, bb))
    return {"atlas_bytes_identical": bool(identical), **pair}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, default=session.DEFAULT_SCRIPT,
                    help="v3.13 .obj2 to mutate in temporary copies")
    ap.add_argument("--after", type=Path, required=True, help="candidate v3.14 .obj2")
    ap.add_argument("--window", type=Path, help="light source image; use together with --character")
    ap.add_argument("--character", type=Path, help="foreground occluder image")
    ap.add_argument("--intensities", type=float, nargs="+", default=[180.0, 30.0, 5.0])
    ap.add_argument("--json", type=Path, default=session.OUT_ROOT / "brightness-ablation.json")
    args = ap.parse_args()
    if bool(args.window) != bool(args.character):
        ap.error("--window and --character must be supplied together")
    before, after = args.before.expanduser().resolve(), args.after.expanduser().resolve()
    supplied = [p.expanduser().resolve() for p in (args.window, args.character) if p]
    for path in [before, after, *supplied]:
        if not path.is_file():
            ap.error(f"file does not exist: {path}")
    result_path = args.json.expanduser().resolve()
    if result_path in [before, after, *supplied]:
        ap.error("--json must not overwrite an input file")
    if any(not np.isfinite(value) or value < 0 for value in args.intensities):
        ap.error("intensities must be finite and nonnegative")

    if supplied:
        window = compare._load_asset(supplied[0], 220)
        character = compare._load_asset(supplied[1], 120)
        asset_info = {"kind": "provided files", "window_sha256": compare._sha256(supplied[0]),
                      "character_sha256": compare._sha256(supplied[1])}
    else:
        window = assets.stained_window(220, 158)
        character = assets.silhouette(120, 120)
        asset_info = {"kind": "sandbox procedural fixtures"}
    layers = [Layer(index=1, image=window),
              Layer(index=3, image=character, x=15, y=10, z=-200)]
    try:
        code_variants = _variants(before.read_text(encoding="utf-8"))
    except ValueError as exc:
        ap.error(str(exc))

    out = {
        "warning": "Paired CPU counterfactuals under approximate host models, not AviUtl2 or an .aup2 replay.",
        "before": {"path": str(before), "sha256": compare._sha256(before)},
        "after": {"path": str(after), "sha256": compare._sha256(after)},
        "assets": asset_info,
        "canvas": [compare.W, compare.H],
        "scene": "character_occlusion, layer source, no simulated camera lights",
        "notes": [
            "The shader alpha gate is disabled by replacing its test with an impossible negative-alpha test.",
            "Constant alpha is assigned after the gate, so gate-on still tests the original proportional alpha.",
            "The shader RGB zero check and assumed host fragment cutoff remain active in every variant.",
        ],
        "rows": [],
    }
    print(out["warning"])
    session.OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="brightness-ablation-", dir=session.OUT_ROOT) as tmp:
        paths = {"proportional_gate_on": before}
        for name in ("proportional_gate_off", "constant_gate_on", "constant_gate_off"):
            path = Path(tmp) / f"{name}.obj2"
            path.write_text(code_variants[name], encoding="utf-8")
            paths[name] = path
        paths["candidate"] = after
        for intensity in args.intensities:
            params = {**compare.BASE, **compare.SCENARIOS["character_occlusion"], "intensity": intensity}
            row = {"intensity": intensity, "parameters": params, "variants": {}}
            results = {}
            print(f"\n[intensity={intensity:g}, source layer + simulated character occlusion]")
            for name, path in paths.items():
                result = session.run_frame(layers, params, width=compare.W, height=compare.H,
                                           layer_index=4, camera_mode=0, script_path=path)
                if result.host.errors or result.volume is None or "beamatlas" not in result.host.shader_traces:
                    raise RuntimeError(f"{name}: {result.host.errors}; log={result.log}")
                trace = result.host.shader_traces["beamatlas"]["params"]
                if abs(trace[21 * 4]) > 1e-8:
                    raise RuntimeError(f"{name}: expected no camera-light compensation, got {trace[21 * 4]}")
                models = compare._modeled_values(result)
                row["variants"][name] = {
                    "camera_light_compensation": trace[21 * 4],
                    "wboit_ka": trace[26 * 4 + 3],
                    "quads": len(result.host.quads), "models": models,
                }
                results[name] = result
                weighted = models["weighted"]
                print(f"  {name:24} light={weighted['emission_luma_unclipped']:.9f}  "
                      f"opacity={weighted['mean_opacity']:.9f}  "
                      f"gray={weighted['gray_0_5_composite_luma']:.9f}")
            row["candidate_vs_constant_gate_off"] = _equivalence(results["constant_gate_off"],
                                                                  results["candidate"])
            equiv = row["candidate_vs_constant_gate_off"]
            print(f"  candidate atlas equals constant/no-gate mutation: {equiv['atlas_bytes_identical']}; "
                  f"quad geometry identical: {equiv.get('identical_quad_geometry', False)}")
            out["rows"].append(row)

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nresults: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
