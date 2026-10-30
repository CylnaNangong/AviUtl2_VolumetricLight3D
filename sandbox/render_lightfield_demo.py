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
from PIL import Image  # noqa: E402
from vl3dsb.host import Host, HostConfig, Layer  # noqa: E402
from vl3dsb.session import run_frame, script, shader_library  # noqa: E402

PATCHER = REPO / "integrations" / "snow3d" / "apply_lightfield.py"
PAYLOAD_MIN = 189


def decode(b: bytes) -> dict:
    """VL3D Light Field v2 のペイロードを読む（デモ表示用の最小実装）。"""
    import struct

    if len(b) < PAYLOAD_MIN or b[0:5] != b"VL3LF":
        return {}
    vals = struct.unpack_from("<26f", b, 85)
    names = ("wx wy wz ax ay az rx ry rz dx dy dz apex_x apex_y apex_z "
             "r0 hx hy length kdec col_r col_g col_b lum power coverage").split()
    out = dict(zip(names, vals))
    out["camera"] = struct.unpack_from("<15f", b, 23)
    out["col_grid"] = None
    if len(b) >= 197 and b[189]:
        n = b[189] ** 2 * 3
        if len(b) >= 197 + n:
            out["col_grid"] = np.frombuffer(b[197:197 + n], np.uint8).reshape(
                b[189], b[189], 3)
    return out


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


def load_rgba(path: Path, premultiply: bool = True) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im).astype(np.float32) / 255.0
    if premultiply:
        a[..., :3] *= a[..., 3:4]
    return (a * 255.0 + 0.5).astype(np.uint8)


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
    ap.add_argument("--source", default=None,
                    help="光源画像（既定: assets/lightfield/stained_glass.jpg）")
    ap.add_argument("--occluder", default=None,
                    help="前景遮蔽画像（既定: assets/lightfield/figure.png）")
    ap.add_argument("--no-occluder", action="store_true")
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

        src_path = Path(args.source) if args.source else (
            HERE / "assets" / "lightfield" / "stained_glass.jpg")
        occ_path = Path(args.occluder) if args.occluder else (
            HERE / "assets" / "lightfield" / "figure.png")
        source = (load_rgba(src_path, premultiply=False) if src_path.is_file()
                  else assets.stained_window(W, H))
        layers = [Layer(index=1, image=source, kind="image")]
        occ = None
        if not args.no_occluder and occ_path.is_file():
            occ = load_rgba(occ_path)
            layers.append(Layer(index=3, image=occ, kind="image", zoom=58.0, z=-150.0))
        beam = run_frame(
            layers,
            {"src_layer": 1, "intensity": args.intensity, "slices": 24.0,
             "threshold": 0.0, "spread": args.spread, "occ_mode": 2.0 if occ is not None else 0.0},
            width=W, height=H, layer_index=5).host

        def frame(params, publish):
            h = Host(sc, [], HostConfig(screen_w=W, screen_h=H, camera_mode=1, camera=cam),
                     shader_lib=shader_library(sc_file), params=params)
            if publish:
                h.import_global(beam.export_global())
            h.layer_index = 3
            if not h.run_frame(0):
                raise SystemExit(f"snow frame failed: {h.errors[:2]}")
            return h

        off = frame({"lf_enable": False, "count": args.count}, True)
        on = frame({"lf_enable": True, "count": args.count,
                    "lf_strength": 220.0, "lf_tint": 85.0}, True)
        log = next((p for p in on.prints if "体積光連動" in p), "")

    n_base = len(off.quads)
    n_lit = len(on.quads) - n_base
    focal = cam["d"]
    base_c = np.array([0.62, 0.68, 0.78], np.float32)

    # 背景（光源画像）と前景（遮蔽物）を参考として敷く
    def fit(path: Path, height_frac: float) -> np.ndarray:
        im = Image.open(path).convert("RGBA")
        w0 = max(1, int(im.width * (H * height_frac) / im.height))
        im = im.resize((w0, int(H * height_frac)), Image.LANCZOS)
        return np.asarray(im).astype(np.float32) / 255.0

    canvas = np.zeros((H, W, 3), np.float32)
    if src_path.is_file():
        bg = Image.open(src_path).convert("RGB").resize((W, H), Image.LANCZOS)
        canvas += (np.asarray(bg).astype(np.float32) / 255.0) ** 2.2 * 0.55
    if occ is not None:
        fg = fit(occ_path, 0.9)
        fh, fw = fg.shape[:2]
        x0 = (W - fw) // 2
        y0 = H - fh
        a = fg[..., 3:4] * 0.55
        canvas[y0:y0 + fh, x0:x0 + fw] = (canvas[y0:y0 + fh, x0:x0 + fw] * (1 - a)
                                          + fg[..., :3] * a * 0.35)

    # 光場（光源色グリッド）を読み、受光粒子を粒子ごとの色で描く
    payload = bytes.fromhex(beam.global_string("VL3D_LF:5"))
    d = decode(payload)
    col = np.array([1.0, 0.92, 0.72], np.float32)

    def flake_colour(c):
        if d.get("col_grid") is None:
            return col
        rel = c - np.array([d["wx"], d["wy"], d["wz"]])
        u = 0.5 + 0.5 * (rel @ np.array([d["rx"], d["ry"], d["rz"]])) / d["hx"]
        v = 0.5 + 0.5 * (rel @ np.array([d["dx"], d["dy"], d["dz"]])) / d["hy"]
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return col
        g = d["col_grid"]
        G = g.shape[0]
        gi = min(G - 1, int(u * G))
        gj = min(G - 1, int(v * G))
        return g[gj, gi].astype(np.float32) / 255.0

    a = splat(off.quads, base_c, W, H, focal, cam["z"], gain=0.55)
    b = splat(on.quads[:n_base], base_c, W, H, focal, cam["z"], gain=0.55)
    for q in on.quads[n_base:]:
        centre = np.array([tuple(v)[:3] for v in q["verts"]], float).mean(axis=0)
        b += splat([q], flake_colour(centre), W, H, focal, cam["z"], gain=1.05)

    def to8(x):
        return (np.clip(x, 0.0, 1.0) ** (1 / 2.2) * 255).astype(np.uint8)

    left = a * (1.0 - 0.55) + canvas * 0.55
    right = b * (1.0 - 0.55) + canvas * 0.55
    sep = np.full((H, 6, 3), 0.12, np.float32)
    Image.fromarray(to8(np.concatenate([left, sep, right], axis=1))).save(
        out / "snow_lit_compare.png")
    Image.fromarray(to8(np.clip(b - a, 0.0, 1.0))).save(out / "snow_lit_only.png")
    print(f"[OK] 受光粒子 {n_lit} / {n_base}（{log.strip() or 'ログなし'}）")
    print(f"[OK] {out / 'snow_lit_compare.png'}  (左: OFF / 右: ON)")
    print(f"[OK] {out / 'snow_lit_only.png'}      (加算発光分のみ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
