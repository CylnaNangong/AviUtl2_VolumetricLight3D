"""Performance / resource budget checks (cache dots, slice scaling, wall clock)."""
from __future__ import annotations

import math
import statistics

import numpy as np

from vl3dsb import assets, session
from vl3dsb.config import REPO_ROOT
from vl3dsb.harness import check, require
from vl3dsb.host import Layer
from vl3dsb.session import run_frame, script

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


_CONSTS_LUA = REPO_ROOT / "src" / "VolumetricLight3D" / "modules" / "00a_consts.lua"


@check("perf: 2D occlusion atlas side is derived from the dot budget",
       tags=["perf", "contract", "core"])
def atlas_side_from_budget():
    """1344 不是调参数，是 sqrt(180 万点预算) 向上取 8 的整倍数。

    这里直接在 LuaJIT 里执行 00a_consts.lua（连带跑完它末尾的 assert），
    再读回运行时真实值 —— 比解析文本更接近"脚本实际看到的常量"。
    """
    from vl3dsb.consts import lua_consts

    K = lua_consts()
    budget = int(K["ATLAS"]["BUDGET_DOTS"])
    align = int(K["ATLAS"]["ALIGN"])
    side = K["ATLAS"]["SIDE_3"]

    require(budget == 1_800_000, f"single-atlas dot budget changed: {budget}")
    require(align == 8, f"atlas alignment changed: {align}")
    require(side == 1344, f"atlas side drifted: {side}")
    require(side == align * math.ceil(math.sqrt(budget) / align),
            f"{side} is not ceil{_align_name(align)}(sqrt({budget}))")
    # 最小性：再小一档就装不下预算
    require((side - align) ** 2 < budget <= side * side,
            f"{side} is not the smallest {align}-aligned side covering {budget} dots")
    # 2x2 / 3x3 网格整除，切分后不留余数行
    require(side % 2 == 0 and side % 3 == 0,
            f"{side} cannot be split evenly into 2x2 / 3x3 grids")

    # 单一来源：生成物里不允许再出现裸的 1344 / 1800000
    text = script().text.replace("\r\n", "\n")
    require("= 1344, 1344" not in text, "naked 1344 atlas side still in the script")
    require("K.ATLAS.SIDE_3" in text, "the atlas side is no longer read from K.ATLAS")
    require("local OCC_SINGLE_BUDGET = 1800000" not in text,
            "OCC_SINGLE_BUDGET re-hardcoded instead of deriving from K.ATLAS.BUDGET_DOTS")

    return {"budget_dots": budget, "align": align, "side": side,
            "side_sq": side * side, "over_budget_pct": round((side * side / budget - 1) * 100, 3),
            "tiles_2x2": side // 2, "tiles_3x3": side // 3}


def _align_name(n: int) -> str:
    return {8: "₈", 16: "₁₆", 32: "₃₂", 64: "₆₄"}.get(n, str(n))


@check("perf: host limits live in one table and every texture clamp is announced",
       tags=["perf", "contract", "core"])
def host_limits():
    """宿主的硬上限（缓存点数 / 纹理边长）只能在其内分配，且压低画质时必须出声。

    以前这些 clamp 全是静默的：宿主 TemporaryImageCacheSize 不够、或 image_max
    小于画质档时，图集会被悄悄缩小 —— 用户只看到「画面突然变糊」，日志里没有
    一个字。现在断言两件事：
      1. 上限只有一处定义（K.HOST），渲染路径不再裸写 4096 / 2048；
      2. 一旦 clamp 真的生效，必须打且只打一条说明（用真实帧验证，不靠 grep）。
    """
    from vl3dsb.consts import lua_consts

    h = lua_consts()["HOST"]
    require(h["TEX_MAX"] == 4096, f"D3D11 texture limit is 4096, table says {h['TEX_MAX']}")
    require(h["TEX_CAP"] <= h["TEX_MAX"], "project cap exceeds the host hard limit")
    require(h["CACHE_DOTS"] == 16_000_000,
            f"TemporaryImageCacheSize changed: {h['CACHE_DOTS']}")
    require(2 * h["TEX_CAP"] ** 2 <= h["CACHE_DOTS"],
            "two TEX_CAP buffers no longer fit the host cache budget")

    text = script().text.replace("\r\n", "\n")
    for banned in ("local max_w, max_h = 4096, 4096",
                   "ww > 4096 or hh > 4096",
                   "2048.0 / math.max(want_w, 1.0)"):
        require(banned not in text, f"naked host limit still in the script: {banned!r}")
    require(text.count("K.HOST.TEX_MAX") >= 2 and text.count("K.HOST.TEX_CAP") >= 3,
            "the render paths no longer read the host limits from K.HOST")

    # 行为验证：画质档 2560 > 宿主 image_max 1080 → 必须报警；给足 4096 → 不许报
    params = {"src_layer": 1.0, "intensity": 200.0, "slices": 8.0, "auto_slice": 0.0}
    layer = [Layer(index=1, image=assets.stained_window(64, 64))]
    tight = run_frame(layer, params, width=160, height=90)
    notes = [p for p in tight.host.prints if "2560" in p and "1080" in p]
    require(len(notes) == 1,
            f"expected exactly one clamp notice, got {len(notes)}: {tight.host.prints}")
    roomy = run_frame(layer, params, width=160, height=90,
                      extra_cfg={"image_max": (4096, 4096)})
    require(not [p for p in roomy.host.prints if "clamp" in p.lower() or "縮小" in p
                 or "缩小" in p],
            f"spurious clamp notice when the host has room: {roomy.host.prints}")

    return {"tex_max": h["TEX_MAX"], "tex_cap": h["TEX_CAP"],
            "cache_dots": h["CACHE_DOTS"], "notice": notes[0].split("\t")[-1][:60]}
