#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPU-backed sandbox verification suite for 体積光（3D）.obj2
(v3.06 bundled in sandbox + v3.13 from the repository).

Runs the real LuaJIT script end-to-end through the sandbox host simulation
with a numpy CPU GPU-shim (identity approximation for ps_* fullscreen passes;
exact rasterization for drawpoly / native C-API rasterizer cross-checks).
"""
import json
import pathlib
import sys
import time

PKG = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PKG))

import cpu_gpu_shim  # noqa: E402
import aviutl2_sandbox  # noqa: E402

cpu_gpu_shim.install_cpu_backend(aviutl2_sandbox)

from sandbox_v2_fidelity import FidelityLayer, PersistentAviUtl2Session, VL3DBridgeMockHost  # noqa: E402
from generate_assets import make_plaster_bust_png, make_stained_glass_png  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

OUT = PKG / "outputs"
OUT.mkdir(parents=True, exist_ok=True)
(PKG / "assets").mkdir(parents=True, exist_ok=True)

W, H = 640, 360
CAM = {"x": 0.0, "y": -40.0, "z": -1024.0, "tx": 0.0, "ty": 20.0, "tz": 0.0,
       "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
PARAMS = {"src_layer": 2, "intensity": 150.0, "direction": 55.0, "pitch": 44.0,
          "occ_mode": 1, "occ_3d_mode": 0, "occ_target": 2, "occ_list": "4",
          "occ_strength": 100.0, "occ_soft": 8.0, "log_mode": True, "show_info": False}

REPORT = {"checks": [], "env": {"width": W, "height": H, "gpu": "cpu-shim"}}

# snapshot cache:vl3d/* buffers at the moment they are about to be shrunk
# (the script releases big caches to 1x1 at end-of-frame by design)
SNAP = {}
_orig_ensure = aviutl2_sandbox.AviUtl2Sandbox.ensure_buffer


def _ensure_snap(self, name, w, h, clear=False):
    cur = self.buffers.get(name)
    if (cur is not None and name.startswith("cache:vl3d/")
            and int(w) * int(h) < cur.w * cur.h and cur.w * cur.h > 4):
        try:
            SNAP[name] = self.read_numpy(name).copy()
        except Exception:
            pass
    return _orig_ensure(self, name, w, h, clear)


aviutl2_sandbox.AviUtl2Sandbox.ensure_buffer = _ensure_snap


def check(name, ok, detail=""):
    REPORT["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def statue_layer(img, raw=None):
    return FidelityLayer(index=4, name="statue", x=0.0, y=-30.0, z=140.0, zoom=110.0,
                         alpha=1.0, image=img, raw_image=raw, kind_override="OTHER")


def make_session(script, bridge_mode="v290_sdk_lock", width=W, height=H, restrict=False):
    br = VL3DBridgeMockHost(mode=bridge_mode, scene_id=1)
    sess = PersistentAviUtl2Session(str(script), width=width, height=height,
                                    strict_getvalue=True, bridge=br)
    if restrict:
        sess.lua.execute("_G.os = nil; _G.io = nil")
    return sess, br


def render_verse1(sess, params=None, frame=146, statue_img=None, raw=None, save=None):
    light = FidelityLayer(index=2, name="light", x=0.0, y=-100.0, z=333.0, zoom=435.0,
                          alpha=1.0, image=make_stained_glass_png(OUT / "stained_glass.png"),
                          kind_override="OTHER")
    res = sess.render_frame(params=dict(PARAMS, **(params or {})), camera=CAM,
                            layers={2: light, 4: statue_layer(statue_img, raw=raw)},
                            self_layer=10, frame=frame, totalframe=619,
                            frame_s=619, scene_id=1)
    if save and res["error"] is None:
        Image.fromarray(res["rgba8"]).save(OUT / save)
    return res


def shmap_of(sess):
    b = sess.sb.buffers.get("cache:vl3d/shmap")
    if b is None:
        return None
    return sess.sb.read_numpy("cache:vl3d/shmap")


def main():
    ok_all = True
    v306 = PKG / "Script" / "体積光（3D）.obj2"
    v313 = pathlib.Path("/home/user/AviUtl2_VolumetricLight3D/Script/体積光（3D）.obj2")
    bust = make_plaster_bust_png(PKG / "assets" / "plaster_bust.png")

    # ================= A. v3.06 bundled reference checks =================
    print("\n=== A. sandbox-bundled v3.06 reference checks ===")
    sess, br = make_session(v306)
    r1 = render_verse1(sess, statue_img=bust, save="cpuA1_v306_verse1_m1.png")
    ok_all &= check("A1 v3.06 verse1 render: no Lua error", r1["error"] is None, r1["error"])
    hud = next((l for l in r1["logs"] if "遮蔽" in l or "遮挡" in l), "")
    ok_all &= check("A2 v3.06 HUD shows L4(m1) occlusion hit", ("L4" in hud and "m1" in hud), hud[:120])
    sh1 = shmap_of(sess)  # snapshot BEFORE the occ-off reference render shrinks it
    REPORT["env"]["v306_shmap_shape"] = list(sh1.shape) if sh1 is not None else None

    r0 = render_verse1(sess, params={"occ_mode": 0}, statue_img=bust, save="cpuA3_v306_noocc.png")
    ok_all &= check("A3 v3.06 occ OFF reference: no error", r0["error"] is None)
    diff = np.clip(r0["rgba8"][..., :3].astype(np.int16) - r1["rgba8"][..., :3].astype(np.int16),
                   0, 255).sum(axis=2)
    ys, xs = np.nonzero(diff > 30)
    if len(xs):
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        fill = len(xs) / (bw * bh)
        ok_all &= check("A4 on-screen shadow bbox fill < 0.9 (silhouette, not rect)", fill < 0.9, f"fill={fill:.3f}")
    if (sh1 is None or (sh1[..., 3] > 0).sum() <= 4) and "cache:vl3d/shmap" in SNAP:
        sh1 = SNAP["cache:vl3d/shmap"]
    if sh1 is not None and (sh1[..., 3] > 0).sum() > 4:
        nz = (sh1[..., 3] > 0)
        ys2, xs2 = np.nonzero(nz)
        bw, bh = xs2.max() - xs2.min() + 1, ys2.max() - ys2.min() + 1
        fill2 = nz.sum() / (bw * bh)
        ok_all &= check("A4b shmap atlas silhouette fill 0.3-0.9", 0.3 < fill2 < 0.9, f"fill={fill2:.3f}")
    else:
        print("  [SKIP] A4b — 2D-only scene produces no 3D shadow map by design (see E1c)")
    peak = sess.sb.peak_cache_dots
    ok_all &= check("A5 v3.06 peak cache dots <= 16M budget", peak <= 16_000_000, f"peak={peak:,}")
    REPORT["env"]["v306_peak_cache_dots"] = peak

    # ---- A6: broken fx-load fallback (L4*) ----
    opaque = np.full((512, 512, 4), 200, dtype=np.uint8)
    opaque[..., 3] = 255
    r3 = render_verse1(sess, statue_img=opaque, raw=bust, save="cpuA6_v306_fxfallback.png")
    hud3 = next((l for l in r3["logs"] if "遮蔽" in l or "遮挡" in l), "")
    stats3 = sess.lua.eval(
        "(function() local t=_G._VL3D_PERSIST_V280 and _G._VL3D_PERSIST_V280.occ_stats or {} "
        "local r={} for k,v in pairs(t) do r[k]=v end return r end)()")
    stats3 = dict(stats3) if stats3 else {}
    fell = stats3.get("opaque_rect_fallback", 0) >= 1
    star = "L4*" in hud3
    ok_all &= check("A6 v3.06 opaque-rect fallback fired (L4*)", fell and star,
                    f"fallback={fell} marker={star}")

    # ---- A7: restricted host ----
    sess_r, _ = make_session(v306, restrict=True)
    r4 = render_verse1(sess_r, statue_img=bust, save="cpuA7_v306_restrict.png")
    init_ok = any(("初期化完了" in l or "初始化完成" in l) for l in r4["logs"])
    ok_all &= check("A7 v3.06 restricted host (os/io=nil) init-complete + no error",
                    init_ok and r4["error"] is None, r4["error"])

    # ================= B. v3.13 (repository version) =================
    print("\n=== B. repository v3.13 checks ===")
    sess13, br13 = make_session(v313)
    t0 = time.perf_counter()
    rB1 = render_verse1(sess13, statue_img=bust, save="cpuB1_v313_verse1_m1.png")
    print(f"    (v3.13 frame render {time.perf_counter()-t0:.1f}s)")
    ok_all &= check("B1 v3.13 verse1 render: no Lua error", rB1["error"] is None, rB1["error"])
    hudB = next((l for l in rB1["logs"] if "遮蔽" in l or "遮挡" in l), "")
    ok_all &= check("B2 v3.13 HUD occlusion L4(m1)", ("L4" in hudB and "m1" in hudB), hudB[:120])
    peak13 = sess13.sb.peak_cache_dots
    ok_all &= check("B3 v3.13 peak cache dots <= 16M", peak13 <= 16_000_000, f"peak={peak13:,}")
    REPORT["env"]["v313_peak_cache_dots"] = peak13

    # B4: multi-frame stability (same session, advancing frames)
    errs = 0
    for fr in (147, 148, 149):
        rf = render_verse1(sess13, frame=fr, statue_img=bust)
        if rf["error"] is not None:
            errs += 1
            print("      frame", fr, "error:", rf["error"][:200])
    ok_all &= check("B4 v3.13 multi-frame (147-149) stable, no errors", errs == 0, f"errs={errs}")

    # B5: parameter sweep (smaller canvas for speed)
    sweep = [
        ("parallel-light default", {}),
        ("radial point source", {"ray_mode": 1, "light_x": 100.0, "light_y": -260.0, "light_z": 900.0}),
        ("convergent beams", {"ray_mode": 2, "light_x": -80.0, "light_y": -240.0, "light_z": 1100.0}),
        ("solid fill mode", {"fill_mode": 1}),
        ("occ mode2 mask-only", {"occ_mode": 2}),
        ("occ mode3 3D+mask", {"occ_mode": 3}),
        ("occ mode1 + soft 40", {"occ_mode": 1, "occ_soft": 40.0}),
        ("fog on", {"fog": 45.0, "fog_scale": 300.0, "wind_speed": 120.0}),
        ("autoblend on", {"ab_enable": 1, "ab_list": "4"}),
        ("hi-res mode", {"res_mode": 0}),
        ("lightweight mode", {"res_mode": 2}),
        ("manual slices 32", {"auto_slice": 0, "slices": 32}),
        ("cam-light follow", {"cam_light_mode": 1}),
        ("cam-light suppress", {"cam_light_mode": 2}),
    ]
    sessS, _ = make_session(v313, width=480, height=270)
    fails = []
    for tag, extra in sweep:
        rs = render_verse1(sessS, params=extra, statue_img=bust)
        if rs["error"] is not None:
            fails.append((tag, rs["error"][:160]))
        else:
            print(f"      sweep ok: {tag}  (ps={rs['pixelshader_calls']}, quads={rs['total_quads']})")
    ok_all &= check("B5 v3.13 param sweep x14 no errors", not fails, str(fails))
    peakS = sessS.sb.peak_cache_dots
    ok_all &= check("B5b sweep peak cache dots <= 16M", peakS <= 16_000_000, f"peak={peakS:,}")

    # ================= C. native bridge cross-validation =================
    print("\n=== C. native VL3DBridge (.so fresh build from repo src) cross-check ===")
    # (2D-only scene has no 3D shadow map — full native/Lua rasterizer checks are in E)
    # ================= D. no-bridge standalone mode =================
    print("\n=== D. standalone (no bridge) mode ===")
    sessD, _ = make_session(v313, width=480, height=270, bridge_mode="none")
    rD = render_verse1(sessD, statue_img=bust)
    ok_all &= check("D1 standalone (obj.module=nil) full fallback chain works",
                    rD["error"] is None, rD["error"])

    # ================= E. 3D model (.obj) shadow-map + native bridge =================
    print("\n=== E. 3D model (birdcage .obj) shadow map & native bridge ===")
    from generate_assets import make_birdcage_obj
    cage_path = OUT / "birdcage.obj"
    make_birdcage_obj(cage_path)
    n_tris = sum(1 for line in cage_path.read_text().splitlines() if line.startswith("f "))

    def render_obj_scene(sess, params=None, save=None):
        light = FidelityLayer(index=2, name="light", x=0.0, y=-120.0, z=320.0, zoom=100.0,
                              alpha=1.0, image=make_stained_glass_png(OUT / "stained_glass.png"),
                              kind_override="OTHER")
        cage = FidelityLayer(index=3, name="3Dmodel_cage", x=0.0, y=47.0, z=0.0, zoom=20.0,
                             alpha=1.0,
                             effects={"モデルファイル": {"ファイル": str(cage_path), "座標系を調整": 0}},
                             kind_override="OBJ", model_path=str(cage_path))
        p = dict(PARAMS)
        p.update({"occ_mode": 3, "occ_target": 2, "occ_list": "3", "src_layer": 2})
        p.update(params or {})
        return sess.render_frame(params=p, camera=CAM,
                                 layers={2: light, 3: cage},
                                 self_layer=10, frame=146, totalframe=619,
                                 frame_s=619, scene_id=1), cage

    # E1: pure-Lua CPU rasterizer with real OBJ mesh
    SNAP.clear()
    sessE, _ = make_session(v313, width=480, height=270)
    rE, _ = render_obj_scene(sessE, save="cpuE1_v313_obj_lua_rast.png")
    ok_all &= check("E1 v3.13 OBJ-layer render (pure-Lua rasterizer): no error", rE["error"] is None, rE["error"])
    stE = sessE.lua.eval(
        "(function() local t=_G._VL3D_PERSIST_V280 and _G._VL3D_PERSIST_V280.occ_stats or {} "
        "local r={} for k,v in pairs(t) do r[k]=v end return r end)()")
    stE = dict(stE) if stE else {}
    last_tris = int(stE.get("last_tris", 0) or 0)
    ok_all &= check("E1b Lua CPU rasterizer drew the OBJ triangles", last_tris > 0,
                    f"tris={last_tris} (obj file has {n_tris})")
    shE = SNAP.get("cache:vl3d/shmap")
    if shE is not None:
        nz = (shE[..., 3] > 0)
        if nz.any():
            ysE, xsE = np.nonzero(nz)
            bw, bh = xsE.max() - xsE.min() + 1, ysE.max() - ysE.min() + 1
            fillE = nz.sum() / (bw * bh)
            ok_all &= check("E1c shmap silhouette fill 0.1-0.95 (cage shape, not solid rect)",
                            0.1 < fillE < 0.95, f"fill={fillE:.3f} shape={shE.shape}")
        else:
            ok_all &= check("E1c shmap has pixels", False, "empty")
    else:
        ok_all &= check("E1c shmap snapshot captured", False, "no snapshot")

    # E2: native C++ rasterizer path (v120_native)
    SNAP.clear()
    sessEN, brEN = make_session(v313, width=480, height=270, bridge_mode="v120_native")
    rEN, _ = render_obj_scene(sessEN, save="cpuE2_v313_obj_native_rast.png")
    ok_all &= check("E2 v3.13 OBJ-layer render (native C-API rasterizer): no error",
                    rEN["error"] is None, rEN["error"])
    callsE = (getattr(brEN, "rast_shadow_calls", 0), getattr(brEN, "has_mesh_hits", 0),
              getattr(brEN, "upload_mesh_calls", 0))
    ok_all &= check("E2b native bridge invoked from Lua (upload_mesh + rasterize_shadow)",
                    callsE[0] > 0 and callsE[1] > 0 and callsE[2] > 0,
                    f"rast_shadow={callsE[0]} has_mesh={callsE[1]} upload_mesh={callsE[2]}")
    stEN = sessEN.lua.eval(
        "(function() local t=_G._VL3D_PERSIST_V280 and _G._VL3D_PERSIST_V280.occ_stats or {} "
        "local r={} for k,v in pairs(t) do r[k]=v end return r end)()")
    stEN = dict(stEN) if stEN else {}
    ok_all &= check("E2c native rasterizer tris > 0", int(stEN.get("last_tris", 0) or 0) > 0,
                    f"tris={stEN.get('last_tris')} backend={stEN.get('last_backend')}")

    # E3: cross-validate Lua vs native C++ rasterizer shadow maps
    shEN = SNAP.get("cache:vl3d/shmap")
    if shE is not None and shEN is not None and shE.shape == shEN.shape:
        a, b = shE.astype(np.int16), shEN.astype(np.int16)
        d = int(np.abs(a - b).max())
        ok_all &= check("E3 Lua vs native C++ rasterizer shmap consistency", d <= 4, f"maxdiff={d}")
    else:
        ok_all &= check("E3 shmap snapshots comparable", False,
                        f"{None if shE is None else shE.shape} vs {None if shEN is None else shEN.shape}")

    # ================= summary =================
    print("\n" + "=" * 62)
    n_pass = sum(1 for c in REPORT["checks"] if c["ok"])
    n_tot = len(REPORT["checks"])
    print(f"CPU-SANDBOX RESULT: {n_pass}/{n_tot} checks passed")
    print("=" * 62)
    with open(OUT / "cpu_sandbox_report.json", "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
