"""Performance / resource budget checks (cache dots, slice scaling, wall clock)."""
from __future__ import annotations

import statistics

import numpy as np

from vl3dsb import assets, session
from vl3dsb.harness import check, require
from vl3dsb.host import Layer
from vl3dsb.session import run_frame

LIGHT = assets.stained_window(128, 128)
BASE = {"src_layer": 1, "intensity": 180.0}


def _time(params, n=2, w=320, h=180):
    out = []
    for _ in range(n):
        r = run_frame([Layer(index=1, image=LIGHT)], {**BASE, **params}, width=w, height=h)
        out.append(r.seconds)
    return statistics.median(out), r


@check("perf: frame time at the reference resolution", tags=["perf"])
def frame_time():
    t, r = _time({"slices": 24.0}, n=3)
    require(r.volume is not None, "no geometry emitted")
    # generous ceiling: the sandbox CPU shader emulator is ~50-100x slower than
    # the real GPU path, this only guards against pathological regressions.
    require(t < 30.0, f"single frame took {t:.1f}s")
    return {"median_s": round(t, 3), "px": r.volume.shape[0] * r.volume.shape[1]}


@check("perf: cost scales with the slice count, not super-linearly", tags=["perf"])
def slice_scaling():
    t16, _ = _time({"slices": 16.0})
    t48, _ = _time({"slices": 48.0})
    ratio = t48 / max(t16, 1e-6)
    require(ratio < 6.0, f"48 slices cost {ratio:.1f}x of 16 slices - super-linear blow-up")
    return {"t16": round(t16, 3), "t48": round(t48, 3), "ratio": round(ratio, 2)}


@check("perf: temporary image budget stays below the AviUtl2 limit", tags=["perf"])
def cache_budget():
    worst = 0
    detail = {}
    for params in ({"slices": 24.0}, {"slices": 64.0, "res_mode": 0.0},
                   {"slices": 32.0, "occ_mode": 3.0, "occ_target": 2.0, "occ_list": "3"}):
        layers = [Layer(index=1, image=LIGHT)]
        if params.get("occ_mode"):
            layers.append(Layer(index=3, image=assets.silhouette(60, 100), z=-200.0))
        r = run_frame(layers, {**BASE, **params}, width=640, height=360)
        peak = r.host.peak_cache_pixels
        worst = max(worst, peak)
        detail[str(params)] = peak
        require(peak <= 16_000_000,
                f"peak cache {peak} exceeds AviUtl2's TemporaryImageCacheSize (16M dots)")
        leaks = {k: v.shape for k, v in r.host.buffers.items()
                 if k.startswith("cache:vl3d/") and v.size > 4}
        require(not leaks, f"cache buffers leaked: {leaks}")
    return {"worst_peak_dots": worst, "worst_MB": round(worst * 4 / 1e6, 1),
            "per_case": detail}


@check("perf: shader dispatch count is bounded per frame", tags=["perf"])
def dispatch_count():
    r = run_frame([Layer(index=1, image=LIGHT)], {**BASE, "slices": 24.0}, width=320, height=180)
    n = len(r.host.shader_calls)
    require(n <= 40, f"{n} pixelshader dispatches per frame looks excessive")
    unique = sorted(set(r.host.shader_calls))
    require(len(unique) >= 4, f"expected the full pass chain (pool/prep/2x blur/beamatlas), saw {unique}")
    for need in ("ps_vl_pool", "ps_vl_prep", "ps_vl_blur", "beamatlas"):
        require(need in unique, f"{need} never dispatched")
    return {"dispatches": n, "unique": len(unique)}


@check("perf: draw call batching (one quad per slice, knots only for point lights)", tags=["perf"])
def draw_batching():
    par = run_frame([Layer(index=1, image=LIGHT)], {**BASE, "slices": 24.0, "ray_mode": 0.0},
                    width=320, height=180)
    pt = run_frame([Layer(index=1, image=LIGHT)], {**BASE, "slices": 24.0, "ray_mode": 1.0},
                   width=320, height=180)
    q_par, q_pt = len(par.host.quads), len(pt.host.quads)
    require(q_par > 0 and q_pt > 0, "no quads emitted")
    require(q_pt >= q_par, f"point light should subdivide at least as much: {q_par} vs {q_pt}")
    calls = len(par.host.draw_calls)
    require(calls <= 4, f"parallel beam should batch its quads, used {calls} drawpoly calls")
    return {"quads_parallel": q_par, "quads_point": q_pt, "drawpoly_calls": calls}
