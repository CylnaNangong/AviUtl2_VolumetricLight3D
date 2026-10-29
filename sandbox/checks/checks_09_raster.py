"""Regression checks for software-rendering assumptions and shader literals."""
from __future__ import annotations

import numpy as np

from vl3dsb.harness import check, require
from vl3dsb.hlsl.executor import ShaderLibrary
from vl3dsb.host import Host, HostConfig
from vl3dsb.raster import DISCARD_W, rasterize_emission
from vl3dsb.session import script


def _quad(alpha, atlas=None):
    return {"verts": [[-1, -1, 0, .5, .5], [1, -1, 0, .5, .5],
                      [1, 1, 0, .5, .5], [-1, 1, 0, .5, .5]],
            "alpha": alpha, "atlas": atlas}


@check("raster: low-alpha cutoff and float OIT assumptions", tags=["runtime", "core"])
def low_alpha():
    atlas = np.full((2, 2, 4), [128, 0, 0, 1], np.uint8)
    # 1/255 * 0.2 < 0.001; 1/255 * 0.27 > 0.001. Test both
    # sides rather than assuming the host's equality boundary.
    below = rasterize_emission(None, [_quad(0.20, atlas)], 2, 2)
    require(not np.any(below), "fragment below the assumed cutoff survived")
    above = rasterize_emission(None, [_quad(0.27, atlas)], 2, 2)
    expected_a = 0.27 / 255.0
    expected_r = 128 / 255.0 * 0.27
    require(np.allclose(above[..., 3], expected_a, atol=2e-7),
            f"unexpected opacity, including the shared triangle edge: {above[..., 3]}")
    require(np.allclose(above[..., 0], expected_r, atol=2e-6),
            f"a single emissive fragment should resolve to tex.rgb*drawpoly_alpha: {above[..., 0]}")
    require(expected_a > DISCARD_W, "bad fixture: above-cutoff sample is not above threshold")
    # Two equal slices: verify the equal-depth WBOIT formula and no duplicate
    # fragments along the triangulation diagonal.
    twice = rasterize_emission(None, [_quad(0.27, atlas), _quad(0.27, atlas)], 2, 2)
    opacity = 1.0 - (1.0 - expected_a) ** 2
    expected = opacity * (128 / 255.0) * 255.0
    require(np.allclose(twice[..., 3], opacity, atol=2e-7), "wrong two-slice reveal")
    require(np.allclose(twice[..., 0], expected, atol=2e-5), "wrong two-slice weighted emission")
    over = rasterize_emission(None, [_quad(0.27, atlas), _quad(0.27, atlas)], 2, 2, mode="over")
    require(np.allclose(over[..., 0], expected_r * (2 - expected_a), atol=2e-6), "wrong over emission")
    return {"discard_assumption": DISCARD_W, "one_slice_alpha": expected_a,
            "one_slice_red": round(expected_r, 5)}


@check("raster: each drawpoly uses its own object-buffer snapshot", tags=["runtime", "core"])
def draw_snapshot():
    host = Host(script(), [], HostConfig(screen_w=2, screen_h=2), shader_lib=None)
    require(host._alloc("object", 2, 2), "could not allocate first buffer")
    host.buffers["object"][:] = [128, 0, 0, 255]
    call = ("obj.drawpoly({{-1,-1,0,.5,.5},{1,-1,0,.5,.5},"
            "{1,1,0,.5,.5},{-1,1,0,.5,.5}},4,0.5)")
    host.lua.execute(call)
    host.buffers["object"][:] = [0, 128, 0, 255]
    host.lua.execute(call)
    host.buffers["object"][:] = [0, 0, 128, 255]
    require(np.array_equal(host.quads[0]["atlas"][0, 0], [128, 0, 0, 255]),
            "first draw lost its red atlas")
    require(np.array_equal(host.quads[1]["atlas"][0, 0], [0, 128, 0, 255]),
            "second draw lost its green atlas")
    out = rasterize_emission(None, host.quads, 2, 2)
    require(out[0, 0, 0] > 0 and out[0, 0, 1] > 0 and out[0, 0, 2] == 0,
            f"snapshots were not both sampled: {out[0, 0]}")
    return {"draws": 2, "snapshot_count": 2}


@check("hlsl: hexadecimal integer literals execute with correct type and value", tags=["hlsl", "core"])
def hex_literals():
    shader = '''float4 hexprobe(float4 pos : SV_Position) : SV_Target {
        uint a = 0x5bd1u;
        uint b = 0x1b87u;
        uint x = (a ^ b) & 255u;
        int y = 0x1f;
        int z = 0xfe;
        return float4(float(x) / 255.0, float(y) / 255.0, float(z) / 255.0, 1.0);
    }'''
    lib = ShaderLibrary({"hexprobe": shader})
    px = lib.run("hexprobe", [], size=(1, 1))[0, 0].tolist()
    require(px == [86, 31, 254, 255], f"incorrect hexadecimal output: {px}")
    return {"rgba": px}
