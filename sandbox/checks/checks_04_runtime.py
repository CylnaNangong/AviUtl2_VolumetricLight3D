"""Dynamic behaviour of the .obj2 script inside the emulated AviUtl2 host."""
from __future__ import annotations

import numpy as np

from vl3dsb import assets, session
from vl3dsb.harness import check, fail, require, skip
from vl3dsb.host import Layer
from vl3dsb.session import OUT_ROOT, composite, run_frame, save_png, standard_scene

BASE = {"src_layer": 1, "intensity": 180.0, "slices": 20.0}


def _render(params=None, *, layers=None, w=256, h=144, **kw):
    p = dict(BASE)
    p.update(params or {})
    return run_frame(layers if layers is not None else standard_scene(128, 128),
                     p, width=w, height=h, layer_index=4, **kw)


def _emission_diff(a, b) -> float:
    # Equal-depth-weight model; use emitted RGB rather than coverage alpha.
    return float(np.abs(a.assumed_emission()[..., :3] - b.assumed_emission()[..., :3]).mean() * 255)


def _has_notice(r, needle: str) -> bool:
    for c in r.host.draw_calls:
        if c.get("type") == "text" and needle in c.get("text", ""):
            return True
    return any(needle in p for p in r.log)


@check("runtime: default parameters render a beam without host errors", tags=["runtime", "core"])
def basic():
    r = _render()
    require(not r.host.errors, f"host raised: {r.host.errors[:1]}")
    require(r.volume is not None, "no geometry was emitted")
    st = r.stats()
    # Constant-token OIT (v3.00..v3.07, v3.14) emits a minimal coverage token
    # (1/255 * 0.27 ~= 0.001059 per slice -> preview alpha_mean ~ 0.004) while
    # carrying full brightness in RGB (rgb_mean ~ 0.031).
    require(st["alpha_mean"] > 0.002 and st["rgb_mean"] > 0.015,
            f"beam is essentially empty: {st}")
    log = "\n".join(r.log)
    require("初期化中" in log or "Initializing" in log, "missing init-start log line")
    require("初期化完了" in log or "Initialized" in log, "missing init-complete log line")
    require("GPU=" in log, "missing per-frame status log line")
    return {**st, "draw_calls": len(r.host.draw_calls)}


@check("runtime: three ray modes are distinct and stable", tags=["runtime"])
def ray_modes():
    out = {}
    for mode in (0, 1, 2):
        r = _render({"ray_mode": float(mode)})
        require(r.volume is not None, f"ray_mode {mode} produced no geometry")
        out[mode] = r.stats()["alpha_mean"]
        require(out[mode] > 0, f"ray_mode {mode} is black")
    require(len({round(v, 5) for v in out.values()}) == 3,
            f"ray modes are not distinguishable: {out}")
    return {"alpha_mean": out}


@check("runtime: fill style / fog / light colour change the image", tags=["runtime"])
def param_effects():
    a = _render({"fill_mode": 0.0})
    b = _render({"fill_mode": 1.0})
    d_fill = float(np.abs(a.alpha.astype(int) - b.alpha.astype(int)).mean())
    require(d_fill > 0.5, f"fill_mode had no effect (diff={d_fill})")

    c = _render({"fog": 0.0})
    d = _render({"fog": 70.0})
    d_fog = _emission_diff(c, d)
    require(d_fog > 0.2, f"fog had no effect on assumed emitted RGB (diff={d_fog})")

    e = _render({"light_color": 0xFF3020})
    rgb_e = e.rgb.astype(float)
    rgb_a = a.rgb.astype(float)
    d_col = float(np.abs(rgb_e - rgb_a).mean())
    require(d_col > 0.2, f"light colour had no effect (diff={d_col})")
    return {"fill_diff": round(d_fill, 3), "fog_diff": round(d_fog, 3), "colour_diff": round(d_col, 3)}


@check("runtime: resolution presets scale the atlas", tags=["runtime", "perf"])
def res_modes():
    sizes = {}
    for mode in (0, 1, 2):
        r = _render({"res_mode": float(mode)})
        sizes[mode] = r.host.shader_traces.get("ps_vl_pack", {}).get("size") or \
            r.host.shader_traces.get("ps_vl_blur", {}).get("size")
        if r.volume is None:
            fail(f"res_mode {mode} produced no geometry")
    # atlas tile size is derived from res_mode; at minimum the peak cache must scale
    peaks = {m: _render({"res_mode": float(m)}).stats()["peak_cache_px"] for m in (0, 1, 2)}
    require(peaks[0] >= peaks[2], f"high-precision preset should not use less cache: {peaks}")
    return {"peaks": {k: int(v) for k, v in peaks.items()}}


@check("runtime: determinism (same input twice = same output)", tags=["runtime", "core"])
def determinism():
    occ = {"seed": 7.0, "fog": 30.0, "occ_mode": 3.0, "occ_target": 2.0, "occ_list": "3"}
    a = _render(occ, layers=standard_scene(128, 128, occluder_z=-200.0))
    b = _render(occ, layers=standard_scene(128, 128, occluder_z=-200.0))
    require(np.array_equal(a.volume, b.volume), "two identical frames differ")
    c = _render(occ, layers=standard_scene(128, 128, with_occluder=False))
    require(not np.array_equal(a.volume, c.volume), "removing the occluder changed nothing")
    return {"detail": "frame output is reproducible"}


@check("runtime: unselected / self / empty source layers are handled", tags=["runtime", "edge"])
def bad_sources():
    r0 = _render({"src_layer": 0.0})
    require(r0.volume is None or r0.alpha.max() == 0, "layer 0 must not draw anything")
    require(_has_notice(r0, "未選択") or _has_notice(r0, "未选定"), "missing 'no source layer' notice")

    r_self = _render({"src_layer": 4.0})
    require(r_self.volume is None or r_self.alpha.max() == 0, "self layer must not draw")
    require(_has_notice(r_self, "自身") or _has_notice(r_self, "自身图层"), "missing self-layer notice")

    r_empty = _render({"src_layer": 9.0})
    require(r_empty.volume is None or r_empty.alpha.max() == 0, "empty layer must not draw")
    require(_has_notice(r_empty, "#9"), "missing 'layer has no image' notice")
    return {"detail": "all three guard messages emitted"}


@check("runtime: intensity / length / alpha zero short-circuit cleanly", tags=["runtime", "edge"])
def zero_params():
    for key, val in (("intensity", 0.0), ("length", 0.0)):
        r = _render({key: val})
        require(not r.host.errors, f"{key}=0 raised {r.host.errors[:1]}")
        require(r.volume is None or r.alpha.max() == 0, f"{key}=0 still drew geometry")
        require(r.host.setoptions.get("draw_state") is True,
                f"{key}=0 must restore draw_state")
    # layer alpha 0 is injected through the object state
    r = run_frame(standard_scene(128, 128), dict(BASE), width=256, height=144,
                  host_factory=lambda cfg: _alpha_host(cfg))
    require(not r.host.errors, "alpha=0 raised")
    return {"detail": "no geometry, draw_state restored"}


def _alpha_host(cfg):
    from vl3dsb.host import Host
    h = Host(session.script(), standard_scene(128, 128), cfg,
             shader_lib=session.shader_library(), params=dict(BASE), native=session.native())
    orig = h.sync_fields

    def sync():
        orig()
        h.lua.globals()["alpha"] = 0.0
        h.lua.execute("obj.alpha = 0.0")
    h.sync_fields = sync
    return h


@check("runtime: HUD gating (gui / show_info) and log_mode", tags=["runtime"])
def hud():
    # NB: --check parameters arrive as Lua booleans; a numeric 0.0 would be *truthy*
    off = _render({"show_info": False}, gui=True)
    require(not any(c.get("type") == "text" for c in off.host.draw_calls),
            "HUD text was drawn while 'show state' is off")

    on = _render({"show_info": True}, gui=True)
    texts = [c for c in on.host.draw_calls if c.get("type") == "text"]
    require(texts, "HUD text missing although show_info=1")
    require(any("v3.13" in t["text"] or "3D" in t["text"] for t in texts), "HUD text looks wrong")

    exporting = _render({"show_info": True}, gui=False)
    require(not any(c.get("type") == "text" for c in exporting.host.draw_calls),
            "HUD must be suppressed while exporting (obj.getoption('gui') == false)")

    quiet = _render({"log_mode": False})
    require(not any(p.startswith("@info") for p in quiet.log),
            f"log_mode=false still printed: {quiet.log[:2]}")
    verbose = _render({"log_mode": True})
    require(any(p.startswith("@info") for p in verbose.log), "log_mode=true printed nothing")
    return {"hud_chars": len(texts[0]["text"]) if texts else 0}


@check("runtime: works inside the restricted script sandbox (os=nil, io=nil, no require)", tags=["runtime", "edge"])
def restricted():
    r = _render(extra_cfg={"restricted_env": True})
    require(not r.host.errors, f"restricted host raised: {r.host.errors[:1]}")
    require(r.volume is not None and r.alpha.max() > 0, "restricted host produced no light")
    return {"alpha_mean": round(r.stats()["alpha_mean"], 4)}


@check("runtime: non-zero clip start passes scene frame to bridge (v3.05 regression)",
       tags=["runtime", "regression"])
def scene_frame():
    # This exercises a synthetic scene timeline, not an actual nested .aup2.
    # The previous check was a false positive: Host.run_frame reset frame_s to
    # zero, the bridge ignored its frame argument, and all layers were active.
    import tempfile
    from pathlib import Path
    from vl3dsb.host import Host

    layers = standard_scene(128, 128, occluder_z=-200.0)
    for layer in layers:
        layer.start, layer.end = 619, 1000
    params = {"src_layer": 1, "intensity": 180.0, "slices": 20.0,
              "occ_mode": 3.0, "occ_target": 2.0, "occ_list": "3", "log_mode": True,
              "ab_enable": True, "ab_target": 2.0, "ab_list": "3"}

    class QueryHost(Host):
        def __init__(self, *args, **kwargs):
            self.resolve_queries = []
            super().__init__(*args, **kwargs)

        def _api_br_resolve_layer(self, layer_idx, frame=0):
            self.resolve_queries.append((int(layer_idx), int(frame)))
            return super()._api_br_resolve_layer(layer_idx, frame)

    def render(path=None):
        def factory(cfg):
            return QueryHost(session.script(path), layers, cfg, params=params,
                             shader_lib=session.shader_library(path), native=session.native())
        return run_frame(layers, params, width=256, height=144, frame=146, frame_s=619,
                         scene_id=1, script_path=path, host_factory=factory)

    r = render()
    require(not r.host.errors, f"raised: {r.host.errors[:1]}")
    require(r.host.frame_s == 619 and r.host.lua.eval("obj.frame_s") == 619,
            "configured scene start was not passed to Lua")
    require((3, 765) in r.host.resolve_queries,
            f"expected scene frame 619+146=765, got {r.host.resolve_queries}")
    desc = next((p for p in r.log if "GPU=" in p), "")
    require("L3(m3)+AB(L3)" in desc, f"active occluder / blend target not detected: {desc}")

    # Negative control: change only the scene-frame expression to local frame.
    # This is an intentionally broken variant, NOT an original v3.04 script.
    source = session.script().text
    needle = "local sf = (fs or 0) + f"
    require(source.count(needle) == 1, "scene-frame expression not found exactly once")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vl-frame-control-", dir=OUT_ROOT) as td:
        control_path = Path(td) / "relative-frame-only.obj2"
        control_path.write_text(source.replace(needle, "local sf = f", 1), encoding="utf-8")
        control = render(control_path)
    require(not control.host.errors, f"negative control raised: {control.host.errors[:1]}")
    control_desc = next((p for p in control.log if "GPU=" in p), "")
    require((3, 146) in control.host.resolve_queries and "0hit" in control_desc
            and "+AB(" not in control_desc,
            f"negative control should miss both targets: {control.host.resolve_queries}, {control_desc}")
    return {"scene_frame": 765, "positive": "L3(m3)+AB(L3)",
            "relative_frame_control": "0hit, no AB"}


@check("host: synthetic effect-only opaque failure preserves raw layer alpha",
       tags=["runtime", "regression"])
def effect_only_opaque_simulation():
    # This checks the simulator's injected failure, not the real AviUtl2 host.
    from vl3dsb.host import Host, HostConfig
    img = assets.silhouette(32, 32)
    layer = Layer(index=3, image=img, fx_name="synthetic effect",
                  effects={"__fx_opaque_bug__": {"on": True}})
    host = Host(session.script(), [layer], HostConfig(screen_w=32, screen_h=32),
                shader_lib=session.shader_library())
    require(host._api_load("layer", 3, True), "effect load failed")
    require(np.all(host.buf("object")[..., 3] == 255), "failure injection did not trigger")
    for effect in (None, False):
        require(host._api_load("layer", 3, effect), "raw load failed")
        require(np.array_equal(host.buf("object"), img),
                "effect-only failure leaked into raw load")
    return {"effect_alpha_min": 255, "raw_alpha_min": int(img[..., 3].min())}


@check("runtime: @水面 (water surface) source path executes ps_vl_water", tags=["runtime", "feature"])
def water_source():
    layer = Layer(index=1, kind="water", water_color=0x66C8FF,
                  effects={"水面": {"解像度": 50, "速度": 5, "方向": -120, "色": 0x66C8FF}})
    r = _render({"src_layer": 1.0}, layers=[layer])
    require(not r.host.errors, f"water source raised: {r.host.errors[:1]}")
    require("ps_vl_water" in r.host.shader_calls, f"ps_vl_water not used: {r.host.shader_calls}")
    require(r.volume is not None and r.alpha.max() > 0, "water source produced no light")
    save_png(composite(None, r.volume, 3.0), OUT_ROOT / "runtime" / "water_source.png")
    return {"alpha_mean": round(r.stats()["alpha_mean"], 4),
            "artifacts": [str(OUT_ROOT / "runtime" / "water_source.png")]}


@check("runtime: camera-controlled 3D light mode + camera basis", tags=["runtime", "feature"])
def camera_modes():
    base_cam = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
                "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
    moved_cam = dict(base_cam, x=400.0, z=-600.0)
    default = _render({}, camera=None, camera_mode=0)
    centered = _render({}, camera=base_cam, camera_mode=1)
    moved = _render({}, camera=moved_cam, camera_mode=1)
    require(not moved.host.errors, f"3D camera raised: {moved.host.errors[:1]}")
    require(moved.volume is not None and moved.alpha.max() > 0, "3D camera mode is black")
    # the default camera is a -Z camera at 1024, so an explicit identical camera must match
    d_same = float(np.abs(default.alpha.astype(int) - centered.alpha.astype(int)).mean())
    require(d_same < 0.01, f"explicit default camera differs from the implicit one (diff={d_same})")
    d = _emission_diff(centered, moved)
    require(d > 0.5, f"moving the camera did not change modeled emission (diff={d})")
    return {"diff_default_vs_moved": round(d, 3), "diff_default_vs_explicit": round(d_same, 4)}


@check("runtime: cache budget stays inside AviUtl2's 16M dot limit", tags=["runtime", "perf"])
def cache_budget():
    r = _render({"slices": 32.0}, w=640, h=360)
    peak = r.host.peak_cache_pixels
    require(peak <= 16_000_000, f"peak cache {peak} exceeds the 16M dot budget")
    leaks = {k: v.shape for k, v in r.host.buffers.items()
             if k.startswith("cache:vl3d/") and v.size > 4}
    require(not leaks, f"cache buffers leaked after the frame: {leaks}")
    own = r.host.buffers.get("object")
    return {"peak_px": peak, "peak_mb": round(peak * 4 / 1e6, 2),
            "own_buffer": None if own is None else list(own.shape),
            "own_buffer_mb": None if own is None else round(own.size * 4 / 1e6, 2)}


@check("runtime: uniform tables match the HLSL cbuffers", tags=["runtime", "hlsl"])
def uniform_consistency():
    r = _render({"slices": 24.0})
    mism = r.host.shader_param_mismatches
    over = [m for m in mism if m["lua_floats"] > m["hlsl_floats"]]
    require(not over, f"Lua passes more floats than the cbuffer declares: {over}")
    under = {}
    for m in mism:
        under.setdefault(m["shader"], set()).add((m["lua_floats"], m["hlsl_floats"]))
    return {"shaders_with_trailing_padding": {k: sorted(v) for k, v in under.items()}}


@check("runtime: multi-frame reuse does not leak state", tags=["runtime", "core"])
def multiframe():
    layers = standard_scene(128, 128, occluder_z=-200.0)
    host = None
    frames = []
    for f in (0, 1, 2, 3):
        r = run_frame(layers, {"src_layer": 1, "intensity": 180.0, "slices": 20.0,
                               "occ_mode": 3.0, "occ_target": 2.0, "occ_list": "3"},
                      width=192, height=108, frame=f)
        require(not r.host.errors, f"frame {f} raised: {r.host.errors[:1]}")
        frames.append(r.stats()["alpha_mean"])
        require(r.host.peak_cache_pixels <= 16_000_000, "cache budget exceeded on frame %d" % f)
        leaks = {k: v.shape for k, v in r.host.buffers.items()
                 if k.startswith("cache:vl3d/") and v.size > 4}
        require(not leaks, f"frame {f} leaked cache buffers: {leaks}")
    require(len({round(x, 6) for x in frames}) == 1, f"identical frames differ: {frames}")
    return {"alpha_mean": frames[0]}


@check("runtime: threshold above the source brightness yields a graceful no-op", tags=["runtime", "edge"])
def high_threshold():
    r = _render({"threshold": 99.0})
    require(not r.host.errors, f"raised: {r.host.errors[:1]}")
    if r.volume is not None and r.alpha.max() > 0:
        # a bright stained-glass source may still pass; that is acceptable but must be dim
        require(r.stats()["alpha_mean"] < 0.02, "threshold=99 did not dim the beam")
    return {"alpha_max": int(r.alpha.max()) if r.volume is not None else 0}


# ---------------------------------------------------------------------------
# Host discard margin
#
# Project notes and the archived GPU sandbox model a fragment cutoff near
# `tex.a * vertex_alpha <= 0.001` (not an official host specification). The script
# encodes slice coverage in `tex.a` (either the constant token `min_opt_a` or a
# proportional value) and the per-slice weight in the drawpoly alpha, so the
# *product* has to stay above that threshold -- otherwise the whole beam
# silently disappears with no error (this is exactly what v3.14's constant-alpha
# token sits on: min_opt_a*poly_alpha ~= 0.00103-0.0014).
# ---------------------------------------------------------------------------
_P12_Z = 12 * 4 + 2      # min_opt_a
_P26_W = 26 * 4 + 3      # wboit_ka


@check("runtime: slice alpha clears the assumed 0.001 discard threshold", tags=["runtime", "core"])
def discard_margin():
    from vl3dsb.raster import DISCARD_W
    rows = []
    worst = None
    worst_weight = float("inf")
    for params in ({"intensity": 60.0}, {"intensity": 180.0}, {"intensity": 600.0},
                   {"intensity": 180.0, "res_mode": 2.0}, {"intensity": 180.0, "fog": 80.0}):
        r = _render(params)
        require(r.volume is not None, f"no geometry for {params}")
        tr = r.host.shader_traces.get("beamatlas")
        require(tr is not None, "beamatlas never ran")
        # In the proportional path this is a lower bound, not the actual alpha
        # for every texel. In the constant-token path it is the emitted value.
        token = float(tr["params"][_P12_Z])
        ka = float(tr["params"][_P26_W])
        quad_a = min((q["alpha"] for q in r.host.quads), default=0.0)
        w = token * quad_a
        rows.append({"params": params, "min_opt_a": round(token, 6), "poly_alpha": round(quad_a, 6),
                     "w": w, "wboit_ka": ka,
                     "alpha_mean": round(r.stats()["alpha_mean"], 5)})
        require(r.stats()["alpha_mean"] > 0.0,
                f"frame is black for {params} (slice weight {w:.5f} vs discard {DISCARD_W})")
        if w < worst_weight:
            worst, worst_weight = rows[-1], w
    require(worst_weight > DISCARD_W,
            f"thinnest slice weight {worst_weight:.7f} is at/below the assumed discard {DISCARD_W}")
    return {"discard_w": DISCARD_W, "worst": worst,
            "margin_x": round(worst_weight / DISCARD_W, 3), "rows": len(rows)}
