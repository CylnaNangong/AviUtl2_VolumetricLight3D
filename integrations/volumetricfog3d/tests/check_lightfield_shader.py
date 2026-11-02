#!/usr/bin/env python3
"""Small CPU-executed HLSL regression for the VolumetricFog3D light-field path.

Run from the repository root with the sandbox Python dependencies installed:
    python integrations/volumetricfog3d/tests/check_lightfield_shader.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sandbox"))
from vl3dsb.hlsl.executor import ShaderLibrary, extract_shaders  # noqa: E402

SCRIPT = ROOT / "integrations/volumetricfog3d/Script/VolumetricFog3D.obj2"


def _base_constants() -> list[float]:
    p = [0.0] * 8240
    p[0:4] = [4.0, 8.0, 8.0, 0.0]       # atlas tile geometry
    p[4:8] = [0.0, 0.0, 0.0, 1.0]       # origin, start distance
    p[8:12] = [0.0, 0.0, 1.0, 1.0]      # forward, slice step
    p[12:16] = [1.0, 0.0, 0.0, 0.5]    # right, horizontal extent
    p[16:20] = [0.0, 1.0, 0.0, 0.5]    # down, vertical extent
    p[24:28] = [0.0, 0.0, 0.0, 0.5]    # wind, optical depth
    p[28:32] = [1.0, 0.0, 0.0, 0.0]    # pattern, fade, sphere flag, time
    p[32:36] = [0.0, 0.0, 1.0, 0.0]    # manual direction, isotropic HG
    p[36:40] = [0.2, 0.2, 0.2, 0.0]    # manual scattered colour
    p[40:44] = [0.0, 0.0, 0.0, 0.0]
    p[44:48] = [0.0, 0.0, -100.0, 0.0] # camera position, manual mode
    return p


def _run(lib: ShaderLibrary, params: list[float]) -> np.ndarray:
    return lib.run("fogatlas", params, size=(32, 4), out_hw=(4, 32))


def main() -> int:
    text = SCRIPT.read_text(encoding="utf-8")
    if "VL3D_LF_IDX:" not in text or "VL3D_LF:" not in text:
        raise AssertionError("fog receiver is missing the VL3DLF v3 scene/slot keys")
    if "math.abs(cur_frame - sframe) > 1" not in text:
        raise AssertionError("fog receiver must reject stale frame packets")
    if "P.scatter_source = tonumber(scatter_source) or 0" not in text:
        raise AssertionError("manual direction must remain the runtime fallback")
    if "--select@scatter_source:散射光来源=0,手动方向=0,体积光联动=1" not in text:
        raise AssertionError("select declaration must encode manual direction as the host default")
    if "local LF_GRID = 4" not in text or "64 * LF_GRID * LF_GRID" not in text:
        raise AssertionError("expected a 4x4 spatial sample grid for up to 64 slices")

    shaders = extract_shaders(text)
    lib = ShaderLibrary(shaders, workdir=Path("/tmp/vfog-shader-regression"))
    if lib.info["fogatlas"].cb_floats != 8240:
        raise AssertionError(f"unexpected fog cbuffer size: {lib.info['fogatlas'].cb_floats}")

    manual = _base_constants()
    manual_out = _run(lib, manual)
    if not np.any(manual_out[..., :3]):
        raise AssertionError("manual scattering fallback produced no light")

    # Put a different local light colour into each of the 4x4 cells of the first slice.
    auto_red = _base_constants()
    auto_red[47] = 1.0
    for i in range(16):
        cell = 48 + (3 * 16 + i) * 4
        auto_red[cell:cell + 4] = [0.8, 0.0, 0.0, 1.0]
        direction = 48 + 4096 + (3 * 16 + i) * 4
        auto_red[direction:direction + 4] = [0.0, 0.0, 1.0, 0.0]
    red_out = _run(lib, auto_red)
    if int(red_out[..., 0].max()) == 0 or int(red_out[..., 1].max()) != 0:
        raise AssertionError("auto-scattering did not use the spatial light-field colour")

    auto_green = list(auto_red)
    for i in range(16):
        cell = 48 + (3 * 16 + i) * 4
        auto_green[cell:cell + 4] = [0.0, 0.8, 0.0, 1.0]
    green_out = _run(lib, auto_green)
    if int(green_out[..., 1].max()) == 0 or int(green_out[..., 0].max()) != 0:
        raise AssertionError("auto-scattering did not follow the local light-field sample")

    # A disabled/absent field must use the existing manual scattering controls,
    # regardless of whatever stale values remain in the two cbuffer arrays.
    manual_stale = list(auto_red)
    manual_stale[47] = 0.0
    if not np.array_equal(manual_out, _run(lib, manual_stale)):
        raise AssertionError("manual mode changed when inactive light-field constants were present")

    # A sharp change between adjacent spatial samples must interpolate smoothly,
    # not appear as a nearest-neighbour rectangular patch in the rendered fog.
    transition = _base_constants()
    transition[0:4] = [32.0, 8.0, 8.0, 0.0]
    transition[27] = 1.0
    transition[28] = 0.0  # uniform fog density, isolate light-field interpolation
    transition[41] = 0.9  # keep the sample region away from the slice edge fade
    transition[47] = 1.0
    for gy in range(4):
        for gx in range(4):
            i = 3 * 16 + gy * 4 + gx
            cell = 48 + i * 4
            transition[cell:cell + 4] = [0.8 if gx < 2 else 0.0, 0.0, 0.0, 1.0]
            direction = 48 + 4096 + i * 4
            transition[direction:direction + 4] = [0.0, 0.0, 1.0, 0.0]
    transition_out = lib.run("fogatlas", transition, size=(256, 32), out_hw=(32, 256))
    row = transition_out[16, 96:128, 0].astype(np.int16)
    if not (0 < int(row[16]) < int(row[12])):
        raise AssertionError(f"light-field sample boundary was not interpolated: {row.tolist()}")
    if int(np.abs(np.diff(row[8:24])).max()) > 20:
        raise AssertionError(f"light-field interpolation still has a visible step: {row.tolist()}")

    print("PASS: receiver contract, 8240-float cbuffer, manual fallback, spatial colour interpolation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
