#!/usr/bin/env python3
"""Render a small gallery of VolumetricLight3D frames with the sandbox host.

    python3 render_demo.py                 # all presets -> out/demo/*.png
    python3 render_demo.py --only fog      # a single preset
    python3 render_demo.py --list

Each PNG uses the legacy RGBA8 preview over a dark backdrop. It is only a
qualitative illustration, not the host's HDR/WBOIT output or a brightness test.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np

from vl3dsb import assets, session
from vl3dsb.host import Layer
from vl3dsb.session import OUT_ROOT, composite, save_png

W, H = 480, 270
DEMO_DIR = OUT_ROOT / "demo"


def _bg(w=W, h=H):
    """A dark studio backdrop so the beam is easy to see."""
    yy, xx = np.mgrid[0:h, 0:w]
    t = (xx / max(w - 1, 1)) * 0.35 + (yy / max(h - 1, 1)) * 0.25
    img = np.zeros((h, w, 4), np.float32)
    img[..., 0] = 0.030 + 0.100 * t
    img[..., 1] = 0.038 + 0.085 * t
    img[..., 2] = 0.070 + 0.120 * t
    img[..., 3] = 1.0
    return np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _window_layer(size=200, **kw):
    return Layer(index=1, image=assets.stained_window(size, size), kind="image", **kw)


def _occ_layer(size=(90, 150), **kw):
    return Layer(index=3, image=assets.silhouette(*size), kind="image", **kw)


PRESETS = {
    "01_beam": dict(
        layers=lambda: [_window_layer()],
        params={"src_layer": 1, "intensity": 220, "slices": 40, "decay": 25, "fog": 18},
        gain=4.0),
    "02_occlusion_2d": dict(
        layers=lambda: [_window_layer(), _occ_layer(x=40, y=30, z=-200)],
        params={"src_layer": 1, "intensity": 230, "slices": 40, "occ_mode": 2,
                "occ_soft": 12, "fog": 15},
        gain=4.0),
    "03_point_light": dict(
        layers=lambda: [_window_layer()],
        params={"src_layer": 1, "intensity": 200, "slices": 36, "ray_mode": 1,
                "light_x": 60, "light_y": -120, "length": 1800, "spread": 30,
                "fog": 25},
        gain=3.2),
    "04_autoblend": dict(
        layers=lambda: [_window_layer(), _occ_layer(x=40, y=30, z=-200)],
        params={"src_layer": 1, "intensity": 230, "slices": 36, "occ_mode": 2,
                "ab_enable": True, "ab_target": 2, "ab_list": "3", "cloudy": 40},
        gain=4.0),
    "05_occlusion_3d": dict(
        layers=lambda: [_window_layer(220), Layer(
            index=3, kind="obj",
            model_path=str(assets.write_test_obj(OUT_ROOT / "assets" / "demo_cube.obj",
                                                90.0, scene_units=True)),
            effects={"モデルファイル": str(OUT_ROOT / "assets" / "demo_cube.obj")},
            z=-380, x=60, y=30)],
        params={"src_layer": 1, "intensity": 220, "slices": 40, "decay": 20,
                "occ_mode": 3, "occ_target": 2, "occ_list": "3", "occ_soft": 4},
        gain=4.0),
    "06_fog": dict(
        layers=lambda: [_window_layer()],
        params={"src_layer": 1, "intensity": 210, "slices": 40, "fog": 85,
                "fog_scale": 320, "wind_speed": 90, "seed": 7},
        gain=4.0),
    "07_water": dict(
        layers=lambda: [Layer(index=1, kind="water", water_color=0x66C8FF,
                              effects={"水面": {"解像度": 50, "速度": 6, "方向": -120,
                                                "色": 0x66C8FF}})],
        params={"src_layer": 1, "intensity": 200, "slices": 36, "decay": 40},
        gain=3.0),
}


def render(name: str) -> Path:
    spec = PRESETS[name]
    r = session.run_frame(spec["layers"](), spec["params"], width=W, height=H)
    if r.volume is None:
        return save_png(_bg(), DEMO_DIR / f"{name}.png")
    img = composite(_bg(), r.volume, spec.get("gain", 3.0))
    out = save_png(img, DEMO_DIR / f"{name}.png")
    st = r.stats()
    print(f"{name:18s} alpha_mean={st['alpha_mean']:.3f} max={st['alpha_max']:.3f} "
          f"quads={int(st['quads'])} {r.seconds:.2f}s -> {out}")
    return out


def gallery(paths) -> Path:
    from PIL import Image, ImageDraw
    cols = 2
    rows = (len(paths) + cols - 1) // cols
    tile_w, tile_h, pad, label = W, H, 8, 18
    sheet = Image.new("RGB", (cols * tile_w + pad * (cols + 1),
                              rows * (tile_h + label) + pad * (rows + 1)), (14, 14, 18))
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        im = Image.open(p).convert("RGB")
        cx = pad + (i % cols) * (tile_w + pad)
        cy = pad + (i // cols) * (tile_h + label + pad)
        sheet.paste(im, (cx, cy))
        draw.text((cx + 4, cy + tile_h + 3), Path(p).stem, fill=(220, 220, 230))
    out = DEMO_DIR / "gallery.png"
    sheet.save(out)
    print("gallery ->", out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="comma separated preset names (substring match)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-gallery", action="store_true")
    args = ap.parse_args(argv)
    names = list(PRESETS)
    if args.list:
        for n in names:
            print(n)
        return 0
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        names = [n for n in names if any(k in n for k in keys)]
    if not names:
        print("no preset matched", file=sys.stderr)
        return 2
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    outs = [render(n) for n in names]
    if not args.no_gallery and len(outs) > 1:
        gallery(outs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
