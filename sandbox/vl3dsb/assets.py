"""Procedural test assets (no external files, no copyrighted material).

All layer images are RGBA8 **premultiplied**, matching what AviUtl2 hands to a
custom object through `obj.load("layer", n)`.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Tuple

import numpy as np


def premultiply(rgb: np.ndarray, a: np.ndarray) -> np.ndarray:
    out = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    af = np.clip(a, 0.0, 1.0).astype(np.float32)
    out[..., :3] = np.clip(rgb.astype(np.float32) * af[..., None], 0, 255).astype(np.uint8)
    out[..., 3] = (af * 255.0 + 0.5).astype(np.uint8)
    return out


def stained_window(w: int = 320, h: int = 320) -> np.ndarray:
    """Dark gothic wall with a bright coloured rose window (a classic god-ray source)."""
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w * 0.5, h * 0.45
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r = d / (min(w, h) * 0.5)

    rgb = np.zeros((h, w, 3), np.float32)
    a = np.zeros((h, w), np.float32)

    # stone wall
    wall = 0.06 + 0.03 * np.sin(xx * 0.11) * np.cos(yy * 0.07)
    rgb[..., 0] += wall * 0.75
    rgb[..., 1] += wall * 0.70
    rgb[..., 2] += wall * 0.85
    a[:] = 1.0

    # window aperture
    inside = r < 0.62
    ang = np.arctan2(yy - cy, xx - cx)
    petal = 0.5 + 0.5 * np.cos(ang * 8.0)
    ring = 0.55 + 0.45 * np.cos(r * 26.0)
    pane = np.clip(0.35 + 0.65 * petal * ring, 0, 1)

    hue = 0.5 + 0.5 * np.cos(ang * 3.0 + 1.2)
    col_r = 0.55 + 0.45 * hue
    col_g = 0.45 + 0.35 * np.sin(ang * 2.0)
    col_b = 0.95 - 0.35 * hue
    bright = 1.35
    rgb[..., 0] = np.where(inside, np.clip(col_r * pane * bright, 0, 1), rgb[..., 0])
    rgb[..., 1] = np.where(inside, np.clip(col_g * pane * bright, 0, 1), rgb[..., 1])
    rgb[..., 2] = np.where(inside, np.clip(col_b * pane * bright, 0, 1), rgb[..., 2])
    # mullions (dark bars) create crisp shadow spokes
    bars = (np.abs(np.sin(ang * 6.0)) < 0.10) | (np.abs(np.sin(r * 18.0)) < 0.06)
    rgb[inside & bars] *= 0.12
    return premultiply(np.clip(rgb * 255.0, 0, 255), a)


def silhouette(w: int = 240, h: int = 400, tint=(40, 40, 55)) -> np.ndarray:
    """Head + shoulders silhouette used as a 2D foreground occluder."""
    yy, xx = np.mgrid[0:h, 0:w]
    cx = w * 0.5
    head = ((xx - cx) / (w * 0.20)) ** 2 + ((yy - h * 0.22) / (h * 0.16)) ** 2 < 1.0
    neck = (np.abs(xx - cx) < w * 0.09) & (yy > h * 0.34) & (yy < h * 0.44)
    body = ((xx - cx) / (w * 0.42)) ** 2 + ((yy - h * 0.72) / (h * 0.34)) ** 2 < 1.0
    mask = (head | neck | body).astype(np.float32)
    rgb = np.zeros((h, w, 3), np.float32)
    rgb[..., 0] = tint[0]
    rgb[..., 1] = tint[1]
    rgb[..., 2] = tint[2]
    return premultiply(rgb, mask)


def gradient(w: int = 320, h: int = 180) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    t = xx / max(1, w - 1)
    u = yy / max(1, h - 1)
    rgb = np.stack([
        30 + 90 * t,
        40 + 70 * (1 - u),
        90 + 120 * (1 - t) * u,
    ], axis=-1)
    a = np.ones((h, w), np.float32)
    return premultiply(rgb, a)


def uniform(w: int, h: int, rgba=(255, 255, 255, 255)) -> np.ndarray:
    img = np.zeros((h, w, 4), np.uint8)
    img[...] = rgba
    return img


#: `load_wavefront_obj_mesh()` in the script multiplies OBJ coordinates by 100
#: (and negates Y, plus Z when "adjust coordinates" is on), so a cube written with
#: half-extent 1.0 occupies +/-100 AviUtl2 scene units.
OBJ_UNIT_SCALE = 100.0


def write_test_obj(path: Path, size: float = 2.6, *, scene_units: bool = False) -> Path:
    """A unit cube OBJ (no MTL/textures) for 3D occlusion tests.

    `size` is the cube half-extent **in OBJ units**; one OBJ unit becomes
    `OBJ_UNIT_SCALE` (=100) scene units inside the effect.  Pass
    ``scene_units=True`` to give the half-extent directly in scene units.
    """
    s = size / OBJ_UNIT_SCALE if scene_units else size
    verts = []
    for z in (-s, s):
        for y in (-s, s):
            for x in (-s, s):
                verts.append((x, y, z))
    # 0..7 cube corners
    faces = [
        (0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
        (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3),
    ]
    lines = ["# sandbox test cube", "o cube"]
    for v in verts:
        lines.append(f"v {v[0]:.3f} {v[1]:.3f} {v[2]:.3f}")
    lines.append("vt 0 0")
    for f in faces:
        a, b, c, d = f
        lines.append(f"f {a+1}/1 {b+1}/1 {c+1}/1 {d+1}/1")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
