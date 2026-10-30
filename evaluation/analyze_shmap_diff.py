#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detailed Lua-vs-native-C++ shadow map diff analysis (reruns E1/E2 scenarios)."""
import pathlib
import sys

PKG = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PKG))

import cpu_gpu_shim  # noqa: E402
import aviutl2_sandbox  # noqa: E402

cpu_gpu_shim.install_cpu_backend(aviutl2_sandbox)

from sandbox_v2_fidelity import FidelityLayer, PersistentAviUtl2Session, VL3DBridgeMockHost  # noqa: E402
from generate_assets import make_birdcage_obj, make_stained_glass_png  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

OUT = PKG / "outputs"
W, H = 480, 270
CAM = {"x": 0.0, "y": -40.0, "z": -1024.0, "tx": 0.0, "ty": 20.0, "tz": 0.0,
       "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
PARAMS = {"src_layer": 2, "intensity": 150.0, "direction": 55.0, "pitch": 44.0,
          "occ_mode": 3, "occ_3d_mode": 0, "occ_target": 2, "occ_list": "3",
          "occ_strength": 100.0, "occ_soft": 8.0, "log_mode": False, "show_info": False}

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


def run(bridge_mode):
    SNAP.clear()
    br = VL3DBridgeMockHost(mode=bridge_mode, scene_id=1)
    sess = PersistentAviUtl2Session(str(pathlib.Path(
        "/home/user/AviUtl2_VolumetricLight3D/Script/体積光（3D）.obj2")),
        width=W, height=H, strict_getvalue=True, bridge=br)
    light = FidelityLayer(index=2, name="light", x=0.0, y=-120.0, z=320.0, zoom=100.0,
                          alpha=1.0, image=make_stained_glass_png(OUT / "stained_glass.png"),
                          kind_override="OTHER")
    cage = FidelityLayer(index=3, name="cage", x=0.0, y=47.0, z=0.0, zoom=20.0, alpha=1.0,
                         effects={"モデルファイル": {"ファイル": str(OUT / "birdcage.obj"),
                                                     "座標系を調整": 0}},
                         kind_override="OBJ", model_path=str(OUT / "birdcage.obj"))
    res = sess.render_frame(params=PARAMS, camera=CAM, layers={2: light, 3: cage},
                            self_layer=10, frame=146, totalframe=619, frame_s=619, scene_id=1)
    assert res["error"] is None, res["error"]
    return SNAP.get("cache:vl3d/shmap")


a = run("v290_sdk_lock")   # pure-Lua rasterizer
b = run("v120_native")     # native C++ rasterizer (fresh build from repo src)
assert a is not None and b is not None and a.shape == b.shape
Image.fromarray(a).save(OUT / "shmap_lua.png")
Image.fromarray(b).save(OUT / "shmap_native.png")

d = np.abs(a.astype(np.int16) - b.astype(np.int16))
nz_mask = (a[..., 3] > 0) | (b[..., 3] > 0)
n_cmp = nz_mask.sum()
print(f"shmap shape={a.shape}, covered px={n_cmp}")
print(f"pixels with any channel diff: {(d.max(axis=2) > 0).sum()} "
      f"({100.0 * (d.max(axis=2) > 0).sum() / max(1, n_cmp):.4f}% of covered)")
for ci, cn in enumerate("RGBA"):
    dc = d[..., ci]
    print(f"  channel {cn}: maxdiff={dc.max()}, ndiff={(dc > 0).sum()}, "
          f"mean|diff|covered={dc[nz_mask].mean() if n_cmp else 0:.5f}")
# alpha (coverage) disagreement only
cov_a, cov_b = a[..., 3] > 0, b[..., 3] > 0
dis = (cov_a != cov_b).sum()
print(f"coverage disagreements (A==0 xor): {dis} px")
# depth delta among mutually covered pixels, in 24-bit prox units
both = cov_a & cov_b
if both.any():
    pa = a[..., 0].astype(np.int64) * 65536 + a[..., 1].astype(np.int64) * 256 + a[..., 2].astype(np.int64)
    pb = b[..., 0].astype(np.int64) * 65536 + b[..., 1].astype(np.int64) * 256 + b[..., 2].astype(np.int64)
    dp = np.abs(pa[both] - pb[both])
    print(f"24-bit prox delta among covered: max={dp.max()} (of 16777216), "
          f"mean={dp.mean():.2f}, p99={np.percentile(dp, 99):.0f}")
    big = (dp > 65536).sum()
    print(f"covered px with prox delta > 1 R-LSB (65536): {big}")
# where do coverage disagreements sit? distance from silhouette edge
if dis:
    from scipy import ndimage  # noqa
    edge = ndimage.binary_dilation(cov_a, iterations=2) & ~ndimage.binary_erosion(cov_a, iterations=2)
    print(f"coverage disagreements within 2px of Lua silhouette edge: "
          f"{(dis_mask := (cov_a != cov_b) & ndimage.binary_dilation(edge, iterations=1)).sum() if False else int(((cov_a != cov_b) & edge).sum())}/{dis}")
