#!/usr/bin/env python3
"""逐位金标准：把「全部会碰到常量表的渲染分支」抓成 npz，改代码前后各抓一次比对。

用法::

    python sandbox/tools/golden.py capture /tmp/base.npz     # 改前
    python sandbox/tools/golden.py compare /tmp/base.npz     # 改后（在同一次比对里重抓）

compare 会用**当前** Script/VolumetricLight3D.obj2 重新渲染，再与 npz 里的数组
逐字节比较（dtype/shape 也必须一致）。它抓的是渲染中间产物而不是只抓成图，
因为「常量改错但画面看着差不多」正是要防的情况：

  * volume / alpha / rgb        —— 3D 光束本体
  * quads_alpha / quads_verts   —— 交给宿主的四边形（poly_alpha 与几何）
  * cache:vl3d/camocc           —— 相机遮挡深度（prox 8bit RGB 编码后的字节）
  * cache:vl3d/occ_a            —— 2D 遮挡图集
  * cache:vl3d/mip              —— 缩略源图（分辨率预算路径）
  * OIT 参数位 / 3D 合成图      —— 宿主常量缓冲与最终合成

注意：本工具属于「验证脚手架」，不属于脚本本体；脚本改动不需要它，但改动常量后
应当跑一次 capture+compare。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vl3dsb import assets  # noqa: E402
from vl3dsb.compositor3d import composite_scene_3d  # noqa: E402
from vl3dsb.host import Host, HostConfig, Layer  # noqa: E402
from vl3dsb.session import run_frame, script, shader_library, standard_scene  # noqa: E402

W, H = 320, 180
CAM = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0, "rz": 0.0, "d": 1024.0}

# 需要逐位比对的中间缓冲（宿主模拟器以 cache: 名字存放）
_BUFFERS = ("cache:vl3d/camocc", "cache:vl3d/occ_a", "cache:vl3d/mip")


def _window(size=200, **kw):
    return Layer(index=1, image=assets.stained_window(size, size), kind="image", **kw)


def _occ(size=(90, 150), **kw):
    return Layer(index=3, image=assets.silhouette(*size), kind="image", **kw)


def scenes():
    """(名字, 构造闭包) 覆盖所有会碰到常量表（OIT alpha / 增益 / prox / 预算）的分支。"""

    def plain():
        return dict(layers=[_window()],
                    params={"src_layer": 1.0, "intensity": 220.0, "slices": 24.0,
                            "decay": 25.0, "fog": 18.0, "auto_slice": 0.0},
                    camera=CAM, camera_mode=1)

    def parallel_occ2d():
        # occ_mode=2 → 相机遮挡深度图（prox 编码）+ 2D 遮挡图集两条路径都会走
        return dict(layers=[_window(), _occ(x=40, y=30, z=-200)],
                    params={"src_layer": 1.0, "intensity": 230.0, "slices": 24.0,
                            "occ_mode": 2.0, "occ_soft": 12.0, "fog": 15.0,
                            "auto_slice": 0.0},
                    camera=CAM, camera_mode=1)

    def point_light():
        return dict(layers=[_window()],
                    params={"src_layer": 1.0, "intensity": 200.0, "slices": 20.0,
                            "ray_mode": 1.0, "light_x": 60.0, "light_y": -120.0,
                            "length": 1800.0, "spread": 30.0, "fog": 25.0,
                            "auto_slice": 0.0},
                    camera=CAM, camera_mode=1)

    return [("plain", plain), ("parallel_occ2d", parallel_occ2d), ("point_light", point_light)]


def _spot_host(layers, params):
    """注入假的相机光源表，复现「虚拟聚光」分支（poly_alpha = 0.064125）。"""
    def factory(c: HostConfig) -> Host:
        h = Host(script(), layers, c, shader_lib=shader_library(), params=params, native=None)
        h.lua.execute("""_G.VL3D_TEST_CAM_LIGHTS = {
            count=1, unlit=0, amb_r=0, amb_g=0, amb_b=0, peak_gain=1,
            suppressed=false, primary={ltype=5, dx=0, dy=0, dz=-1,
              r=1, g=1, b=1, lum=1, px=0, py=0, pz=-800, cos_ang=0.7}
        }""")
        return h
    return factory


def _buf_entries(entries, meta, name, r):
    for bk in _BUFFERS:
        buf = r.host.buffers.get(bk)
        short = bk.split("/")[-1]
        entries[f"{name}/{short}"] = (np.asarray(buf) if buf is not None
                                      else np.zeros((1,), np.uint8))
    meta[name]["buffers"] = {bk: int(np.asarray(r.host.buffers[bk]).size)
                             for bk in _BUFFERS if bk in r.host.buffers}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "capture"
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/golden.npz")

    entries, meta = {}, {}

    # --- 非聚光场景 -------------------------------------------------
    for name, build in scenes():
        spec = build()
        w, h = spec.get("width", W), spec.get("height", H)
        r = run_frame(spec["layers"], spec["params"], width=w, height=h,
                      camera=spec.get("camera"), camera_mode=spec.get("camera_mode", 1),
                      rasterize_2d=True)
        vol = r.volume if r.volume is not None else np.zeros((4, 4, 4), np.float32)
        entries[f"{name}/volume"] = np.asarray(vol, np.float32)
        entries[f"{name}/alpha"] = np.asarray(r.alpha, np.float32)
        entries[f"{name}/rgb"] = np.asarray(r.rgb, np.float32)
        qa = np.array([q["alpha"] for q in r.host.quads], np.float64)
        entries[f"{name}/quads_alpha"] = qa
        vv = []
        for q in r.host.quads:
            for vtx in q["verts"]:
                vv.extend([float(v) for v in vtx])
        entries[f"{name}/quads_verts"] = np.array(vv, np.float64)
        meta[name] = {"quads": len(qa), "alpha_mean": float(np.mean(r.alpha)),
                      "bytes": hashlib.sha256(np.ascontiguousarray(vol).tobytes()).hexdigest()}
        _buf_entries(entries, meta if isinstance(meta.get(name), dict) else meta, name, r)
        print(f"{name:22s} quads={len(qa):4d} alpha_mean={np.mean(r.alpha):.6f} "
              f"sha={meta[name]['bytes'][:12]} errors={r.host.errors}")

    # --- 虚拟聚光（0.064125 / 4-255 分支）---------------------------
    for inten in (60.0, 180.0, 420.0):
        name = f"spot_i{int(inten)}"
        layers = standard_scene(96, 54, with_occluder=False, light_size=64)
        params = {"src_layer": 0.0, "cam_light_mode": 1.0, "intensity": inten,
                  "res_mode": 2.0, "auto_slice": False, "slices": 16.0,
                  "log_mode": False, "show_info": False}
        r = run_frame(layers, params, width=96, height=54, layer_index=4, camera_mode=1,
                      host_factory=_spot_host(layers, params), rasterize_2d=True)
        vol = r.volume if r.volume is not None else np.zeros((4, 4, 4), np.float32)
        entries[f"{name}/volume"] = np.asarray(vol, np.float32)
        entries[f"{name}/alpha"] = np.asarray(r.alpha, np.float32)
        qa = np.array([q["alpha"] for q in r.host.quads], np.float64)
        entries[f"{name}/quads_alpha"] = qa
        tr = r.host.shader_traces["beamatlas"]["params"]
        entries[f"{name}/p12_z_min_opt_a"] = np.array([float(tr[12 * 4 + 2])], np.float64)
        entries[f"{name}/p25_w_spot"] = np.array([float(tr[25 * 4 + 3])], np.float64)
        meta[name] = {"quads": len(qa), "alpha_mean": float(np.mean(r.alpha)),
                      "poly_alpha": float(qa[0]) if len(qa) else None,
                      "bytes": hashlib.sha256(np.ascontiguousarray(vol).tobytes()).hexdigest()}
        _buf_entries(entries, meta, name, r)
        print(f"{name:22s} quads={len(qa):4d} poly_alpha={meta[name]['poly_alpha']} "
              f"sha={meta[name]['bytes'][:12]} errors={r.host.errors}")

    # --- 3D OIT 合成（compositor3d）----------------------------------
    layers = [Layer(index=1, image=assets.stained_window(160, 120), kind="image",
                    z=600.0, zoom=220.0)]
    params = {"src_layer": 1.0, "intensity": 200.0, "slices": 24.0, "auto_slice": 0.0,
              "direction": 90.0, "pitch": 63.8, "length": 900.0, "decay": 55.0,
              "occ_mode": 0.0}
    r = run_frame(layers, params, width=W, height=H, camera=CAM, camera_mode=1,
                  rasterize_2d=True)
    comp = composite_scene_3d(W, H, CAM, layers, r.host.quads, oit_mode="wboit_uniform")
    entries["oit3d/image"] = np.asarray(comp, np.float32)
    meta["oit3d"] = {"shape": list(comp.shape),
                     "bytes": hashlib.sha256(np.ascontiguousarray(comp).tobytes()).hexdigest()}
    print(f"{'oit3d':22s} shape={comp.shape} sha={meta['oit3d']['bytes'][:12]}")

    if mode == "capture":
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **entries)
        Path(str(path) + ".json").write_text(
            __import__("json").dumps(meta, indent=2), encoding="utf-8")
        print(f"\ncaptured {len(entries)} arrays -> {path}")
        return 0

    base = np.load(path)
    bad = ok = 0
    for key in sorted(base.files):
        if key not in entries:
            print(f"MISSING {key}")
            bad += 1
            continue
        a, b = base[key], entries[key]
        if a.dtype != b.dtype or a.shape != b.shape:
            print(f"SHAPE   {key}: {a.dtype}{a.shape} -> {b.dtype}{b.shape}")
            bad += 1
        elif not np.array_equal(a, b):
            diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
            print(f"DIFFER  {key}: max|d|={diff.max():.6g} n={(diff > 0).sum()}/{a.size}")
            bad += 1
        else:
            ok += 1
    print(f"\n== {ok} arrays bit-identical, {bad} differ, {len(base.files)} total ==")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
