#!/usr/bin/env python3
"""
体積光（3D）→ 雪（3D）受光連動の「見える化」デモ。

`integrations/snow3d/apply_lightfield.py` を適用した雪（3D）をサンドボックスで
1 フレーム実行し、記録されたクアッド（粒子ビルボード）を**簡易ピンホール投影**で
画像化します。受光パス（加算合成）のクアッドだけを光束色で塗るので、
「どの雪粒が光束に当たっているか」がそのまま見えます。

    python3 render_lightfield_demo.py                 # out/lightfield/*.png
    python3 render_lightfield_demo.py --snow3d ../Snow3D
    python3 render_lightfield_demo.py --count 4000 --spread 55

サンドボックスの合成器は本来 VolumetricLight3D 自身の直交投影クアッド用なので、
ここでは粒子ビルボード専用の軽量投影を自前で行います（真機のピクセル一致を
主張するものではありません）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from vl3dsb import assets  # noqa: E402
from vl3dsb.host import Host, HostConfig, Layer  # noqa: E402
from vl3dsb.session import run_frame, script, shader_library  # noqa: E402

PATCHER = REPO / "integrations" / "snow3d" / "apply_lightfield.py"


def find_snow3d(explicit: str | None) -> Path | None:
    cands = [explicit] if explicit else []
    cands += [os.environ.get("VL3DSB_SNOW3D"), str(REPO.parent / "AviUtl2_Snow3D"),
              str(REPO.parent / "Snow3D")]
    for c in cands:
        if not c:
            continue
        root = Path(c).expanduser()
        for name in ("雪（3D）.obj2", "雪(3D).obj2"):
            if (root / "Script" / name).is_file():
                return root / "Script" / name
    return None


def patched_copy(src: Path, tmp: Path) -> Path:
    dst = tmp / "Script" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    subprocess.run([sys.executable, str(PATCHER), "--script", str(dst), "--no-backup"],
                   check=True, capture_output=True)
    return dst


def splat(quads, colour, w, h, focal, cam_z, gain=1.0):
    """ピンホール投影＋ガウス状スプラット（前後関係は無視した簡易表示）。"""
    img = np.zeros((h, w, 3), np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    for q in quads:
        v = np.array([tuple(x)[:3] for x in q["verts"]], float)
        c = v.mean(axis=0)
        depth = c[2] - cam_z
        if depth < 1.0:
            continue
        sx = w * 0.5 + (c[0] / depth) * focal
        sy = h * 0.5 + (c[1] / depth) * focal
        rad = max(1.0, float(np.linalg.norm(v[0] - v[2]) * 0.5) / depth * focal)
        if not (-rad <= sx <= w + rad and -rad <= sy <= h + rad):
            continue
        d2 = ((xx - sx) ** 2 + (yy - sy) ** 2) / (rad * rad)
        m = np.clip(1.0 - d2, 0.0, 1.0) ** 1.5
        a = float(q.get("alpha", 1.0)) * gain
        img += (m * a)[:, :, None] * np.asarray(colour, np.float32)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snow3d", default=None, help="AviUtl2_Snow3D リポジトリのパス")
    ap.add_argument("--count", type=float, default=2500.0)
    ap.add_argument("--spread", type=float, default=40.0)
    ap.add_argument("--intensity", type=float, default=240.0)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--out", default=str(HERE / "out" / "lightfield"))
    args = ap.parse_args()

    src = find_snow3d(args.snow3d)
    if src is None:
        print("[SKIP] 雪（3D）が見つかりません（--snow3d か VL3DSB_SNOW3D で指定）")
        return 2

    W, H = args.width, args.height
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cam = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
           "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}

    with tempfile.TemporaryDirectory() as td:
        sc_file = patched_copy(src, Path(td))
        sc = script(sc_file)

        beam = run_frame(
            [Layer(index=1, image=assets.stained_window(W, H), kind="image")],
            {"src_layer": 1, "intensity": args.intensity, "slices": 24.0,
             "threshold": 0.0, "spread": args.spread},
            width=W, height=H, layer_index=4).host

        def frame(params, publish):
            h = Host(sc, [], HostConfig(screen_w=W, screen_h=H, camera_mode=1, camera=cam),
                     shader_lib=shader_library(sc_file), params=params)
            if publish:
                h.import_global(beam.export_global())
            h.layer_index = 3
            if not h.run_frame(0):
                raise SystemExit(f"snow frame failed: {h.errors[:2]}")
            return h

        off = frame({"lf_enable": False, "count": args.count}, False)
        on = frame({"lf_enable": True, "count": args.count,
                    "lf_strength": 220.0, "lf_tint": 85.0}, True)
        log = next((p for p in on.prints if "体積光連動" in p), "")

    n_base = len(off.quads)
    n_lit = len(on.quads) - n_base
    focal = cam["d"]
    base_c = np.array([0.55, 0.62, 0.72], np.float32)
    lit_c = np.array([1.0, 0.92, 0.72], np.float32)

    a = splat(off.quads, base_c, W, H, focal, cam["z"], gain=0.55)
    b = splat(on.quads[:n_base], base_c, W, H, focal, cam["z"], gain=0.55)
    b += splat(on.quads[n_base:], lit_c, W, H, focal, cam["z"], gain=1.6)

    def to8(x):
        return (np.clip(x, 0.0, 1.0) ** (1 / 2.2) * 255).astype(np.uint8)

    sep = np.full((H, 6, 3), 0.12, np.float32)
    Image.fromarray(to8(np.concatenate([a, sep, b], axis=1))).save(out / "snow_lit_compare.png")
    Image.fromarray(to8(np.clip(b - a, 0.0, 1.0))).save(out / "snow_lit_only.png")
    print(f"[OK] 受光粒子 {n_lit} / {n_base}（{log.strip() or 'ログなし'}）")
    print(f"[OK] {out / 'snow_lit_compare.png'}  (左: OFF / 右: ON)")
    print(f"[OK] {out / 'snow_lit_only.png'}      (加算発光分のみ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
