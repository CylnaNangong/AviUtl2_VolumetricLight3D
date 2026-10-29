#!/usr/bin/env python3
"""Investigate the archived v3.01–v3.06 brightness-related changes.

Runs cached historical pool/prep HLSL in the CPU simulator, verifies the
v3.04 stride arithmetic, and reverses the archived v3.06 patch as a *synthetic*
control. The reversed file is not an authenticated original v3.05 release.
No user-owned assets or production files are written.

    python3 sandbox/probe_history_301_306.py

The CPU renderer is not AviUtl2/D3D11 and its light values are only model
proxies. Run the separate non-zero-scene-frame negative control with:
    python3 sandbox/run_checks.py --only 'non-zero clip start,effect-only opaque'
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import zipfile

import numpy as np

from vl3dsb import assets, session
from vl3dsb.hlsl.executor import ShaderLibrary, extract_shaders
from vl3dsb.host import Layer
import compare_brightness as compare

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "VolumetricLight3D-Sandbox/"
CACHE = {
    "pool_no_stride": "ps_vl_pool_5ad5e7d0e0e5a507a45d1a5d.hlsl",
    "pool_integer_stride": "ps_vl_pool_f8028e1e0a6e7a8e509b5484.hlsl",
    "pool_float_stride": "ps_vl_pool_8eb026bd31eb493d7132ed21.hlsl",
    "prep_no_stride": "ps_vl_prep_7c9aabffa69dabefde220908.hlsl",
    "prep_integer_stride": "ps_vl_prep_b9f8620db2c60f194bec1ff8.hlsl",
    "prep_float_stride": "ps_vl_prep_62e0891fbd0c7d52bd50c44c.hlsl",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unpack_patch(patch: str):
    """Parse literal replacements from the historical script without executing it."""
    direct = []
    versions = None
    for node in ast.parse(patch).body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "rep"):
            call = node.value
            require(len(call.args) in (2, 3), f"unexpected rep syntax at {node.lineno}")
            old, new = (ast.literal_eval(arg) for arg in call.args[:2])
            kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            count = ast.literal_eval(call.args[2]) if len(call.args) == 3 else kwargs.get("count", 1)
            direct.append((old, new, int(count), node.lineno))
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
            versions = ast.literal_eval(node.iter)
    require(len(direct) == 9 and versions is not None and len(versions) == 8,
            "archived patch has an unexpected structure")
    return direct, versions


def reverse_patch(reference: str, patch: str):
    """Reverse the patch and require applying it again reproduces v3.06 exactly.

    The Japanese version replacement also appears in the information header,
    which the patch has already changed earlier. Protect that header while
    undoing the later global replacements.
    """
    direct, versions = unpack_patch(patch)
    header = "--information:体積光（3D）v3.06\r\n"
    marker = "--information:__VL_PATCH_HEADER_PROTECTED__\r\n"
    require(reference.count(header) == 1 and marker not in reference, "unexpected header")
    work = reference.replace(header, marker, 1)
    for old, new in reversed(versions):
        require(work.count(new) > 0, f"missing patched version string {new}")
        work = work.replace(new, old)
    work = work.replace(marker, header, 1)
    for old, new, count, line in reversed(direct):
        require(work.count(new) == count, f"reverse replacement count mismatch at patch line {line}")
        work = work.replace(new, old, count)
    reversed_text = work

    for old, new, count, line in direct:
        require(work.count(old) == count, f"forward replacement count mismatch at patch line {line}")
        work = work.replace(old, new, count)
    for old, new in versions:
        work = work.replace(old, new)
    require(work.encode("utf-8") == reference.encode("utf-8"), "patch round-trip was not byte-identical")
    return reversed_text, len(direct), len(versions)


def renamed_shaders(cache):
    compiled = {}
    for alias, text in cache.items():
        name = "ps_vl_pool" if alias.startswith("pool_") else "ps_vl_prep"
        require(text.count(name + "(") == 1, f"unexpected shader entry in {alias}")
        compiled[alias] = text.replace(name + "(", alias + "(", 1)
    return compiled


def shader_run(lib, name, factor, texture, out_hw=(1, 1)):
    h, w = texture.shape[:2]
    params = [w, h, factor, 0.15]
    if name.startswith("prep_"):
        params += [1.0, 1.0, 1.0, 1.0]
    return lib.run(name, params, [texture], out_hw=out_hw)


def shader_comparisons(cache, reference):
    canonical = extract_shaders(reference)
    for kind in ("pool", "prep"):
        require(canonical[f"ps_vl_{kind}"].split() == cache[f"{kind}_float_stride"].split(),
                f"v3.06 {kind} cache does not match the reference shader")

    # Lua passes positive integers. Binary fractions 1/32 and 1/16 are exact;
    # also verify the relevant 16K range with float32, not Python float64.
    factors = np.arange(1, 16385, dtype=np.int32)
    eqs = {}
    for kind, n in (("pool", 32), ("prep", 16)):
        integer = np.maximum((factors + n - 1) // n, 1)
        floating = np.maximum(np.ceil(factors.astype(np.float32) * np.float32(1.0 / n)), 1).astype(np.int32)
        require(np.array_equal(integer, floating), f"{kind} integer/float stride mismatch")
        eqs[kind] = {"factor_min": 1, "factor_max": 16384, "equal_count": int(len(factors))}

    lib = ShaderLibrary(renamed_shaders(cache))
    rng = np.random.default_rng(20261029)
    img = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
    img[..., 3] = 255
    # Isolate the shader with factors calculated from the screenshot dimensions;
    # this is not an actual 6501x4751 full-pipeline render.
    screenshot_factors = {"pool": 32, "prep": 11}
    screenshot_results = {}
    for kind, factor in screenshot_factors.items():
        hw = (math.ceil(64 / factor), math.ceil(64 / factor))
        a = shader_run(lib, f"{kind}_no_stride", factor, img, hw)
        b = shader_run(lib, f"{kind}_float_stride", factor, img, hw)
        require(np.array_equal(a, b), f"{kind} failed same-factor shader comparison")
        screenshot_results[kind] = {"factor": factor, "output_hw": list(hw),
                                    "equal_rgba8": True, "sha256": sha(a.tobytes())}

    integer_float_cases = []
    for kind in ("pool", "prep"):
        for factor in (1, 11, 16, 17, 32, 33, 65):
            a = shader_run(lib, f"{kind}_integer_stride", factor, img)
            b = shader_run(lib, f"{kind}_float_stride", factor, img)
            require(np.array_equal(a, b), f"{kind} factor {factor} shader differs")
            integer_float_cases.append({"shader": kind, "factor": factor, "equal_rgba8": True})

    # Adversarial vertical one-pixel stripes expose the changed sample set; the
    # direction is texture-dependent and is not a prediction for user assets.
    examples = []
    for kind, factor in (("pool", 33), ("prep", 17)):
        for bright_columns in ("even", "odd"):
            texture = np.zeros((64, 64, 4), np.uint8)
            texture[..., 3] = 255
            start = 0 if bright_columns == "even" else 1
            texture[:, start::2, :3] = 255
            before = shader_run(lib, f"{kind}_no_stride", factor, texture)[0, 0].tolist()
            after = shader_run(lib, f"{kind}_float_stride", factor, texture)[0, 0].tolist()
            require(before != after, f"{kind} adversarial texture failed to expose sampling")
            examples.append({"shader": kind, "factor": factor, "bright_columns": bright_columns,
                             "no_stride_rgba8": before, "stride_rgba8": after})
    return {"cached_stride_arithmetic_float32": eqs,
            "forced_same_factor_shader_runs": screenshot_results,
            "integer_vs_float_shader_cases": integer_float_cases,
            "adversarial_sampling_examples": examples}


def patch_ablation(reference_path, control_path, reference, control):
    ref_shaders, ctl_shaders = extract_shaders(reference), extract_shaders(control)
    require(ref_shaders == ctl_shaders, "patch ablation unexpectedly changed HLSL blocks")
    records = []
    for mode in (1, 2):
        for inject_opaque in (False, True):
            layers = [
                Layer(index=1, image=assets.stained_window(160, 120)),
                Layer(index=3, image=assets.silhouette(100, 100), z=-200,
                      fx_name="synthetic effect",
                      effects={"__fx_opaque_bug__": {"on": inject_opaque}}),
            ]
            params = {"src_layer": 1, "intensity": 100.0, "threshold": 15,
                      "slices": 20.0, "occ_mode": mode, "occ_target": 2.0,
                      "occ_list": "3", "log_mode": True}
            results = {}
            row = {"occ_mode": mode, "injected_fx_opaque_rect": inject_opaque, "versions": {}}
            for label, path in (("v306", reference_path), ("v306_minus_patch", control_path)):
                r = session.run_frame(layers, params, width=192, height=108,
                                      layer_index=4, script_path=path)
                require(not r.host.errors and r.volume is not None, f"{label} render failed: {r.host.errors}")
                line = next((line for line in r.log if "GPU=" in line), "")
                require("L3" in line, f"{label} did not select L3: {line}")
                results[label] = r
                row["versions"][label] = {
                    "status": line,
                    "quads": len(r.host.quads),
                    "modeled_emission_luma": compare._modeled_values(r)["weighted"]["emission_luma_unclipped"],
                }
            a, b = results["v306"], results["v306_minus_patch"]
            row["same_rgba8_preview"] = bool(np.array_equal(a.volume, b.volume))
            row["atlas_comparison"] = compare._atlas_compare(b, a)
            if not inject_opaque:
                require(row["same_rgba8_preview"], f"healthy fixture differs for mode {mode}")
            else:
                require("L3*" in row["versions"]["v306"]["status"], "v3.06 fallback not reported")
                require("L3*" not in row["versions"]["v306_minus_patch"]["status"], "control unexpectedly reports fallback")
                if mode == 2:
                    require(not row["same_rgba8_preview"], "opaque rectangle fixture should change mode-2 preview")
            records.append(row)
    return {"shader_blocks_identical": len(ref_shaders), "rows": records}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", type=Path, default=ROOT / "体積光3D_GPU沙箱_v3.06.zip")
    ap.add_argument("--out", type=Path, default=session.OUT_ROOT / "history-301-306")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip) as archive:
        ref_bytes = archive.read(PREFIX + "Script/体積光（3D）.obj2")
        patch_bytes = archive.read(PREFIX + "archive/legacy_root/patch_v306.py")
        cache = {alias: archive.read(PREFIX + "spv_cache/" + name).decode("utf-8")
                 for alias, name in CACHE.items()}
    reference, patch = ref_bytes.decode("utf-8"), patch_bytes.decode("utf-8")
    control, direct_count, version_count = reverse_patch(reference, patch)
    ref_path = (args.out / "v306-archived-reference.obj2").resolve()
    control_path = (args.out / "v306-minus-archived-patch-SYNTHETIC.obj2").resolve()
    ref_path.write_bytes(ref_bytes)
    control_path.write_bytes(control.encode("utf-8"))
    require(control != reference, "synthetic control is unchanged")

    # The screenshot's HUD gives the loaded source buffer, not necessarily the
    # original media dimensions. These formulas are from v3.06's Lua pipeline.
    w, h = 6501, 4751
    pool_f = max(1, math.ceil(max(w, h) / 256.0), min(32, math.ceil(math.sqrt(w * h / 4096.0))))
    prep_f = max(1, math.ceil(max(w, h) / 640.0))
    require((pool_f, prep_f) == (32, 11), "unexpected screenshot factor calculation")
    result = {
        "warning": "Synthetic CPU checks, not original v3.01–v3.05 releases or AviUtl2/D3D11.",
        "archive_sha256": sha(args.zip.read_bytes()),
        "v306_script_sha256": sha(ref_bytes),
        "synthetic_reverse_sha256": sha(control.encode("utf-8")),
        "patch_round_trip_exact": True,
        "patch_direct_replacements": direct_count,
        "patch_version_replacements": version_count,
        "hud_source_buffer": [w, h],
        "v306_factors_for_hud_buffer": {"pool": pool_f, "prep": prep_f},
        "shaders": shader_comparisons(cache, reference),
        "v306_patch_ablation": patch_ablation(ref_path, control_path, reference, control),
    }
    out = args.out / "history-probe.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"v3.06 reverse-patch round trip: exact; {direct_count} replacements, {version_count} version strings")
    print(f"v3.06 factors for HUD {w}x{h}: pool={pool_f}, prep={prep_f}")
    print("cached shader factor 11/32 tests: no-stride and float-stride RGBA8 identical")
    print("integer/float stride: all 1..16384 factors equivalent; 14 shader cases identical")
    for row in result["v306_patch_ablation"]["rows"]:
        va = row["versions"]["v306"]["modeled_emission_luma"]
        vb = row["versions"]["v306_minus_patch"]["modeled_emission_luma"]
        print(f"  mode={row['occ_mode']} injected_opaque={row['injected_fx_opaque_rect']}: "
              f"v306={va:.8f}, synthetic_minus_patch={vb:.8f}, "
              f"same_preview={row['same_rgba8_preview']}")
    print(f"results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
