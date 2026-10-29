"""Approximate software rasterisation of recorded ``obj.drawpoly`` calls.

The project documents a reverse-engineered fragment cutoff near 0.001 for
``texture alpha * drawpoly alpha``. This is *not* an official host API contract;
it may vary with host version. The models below do not implement the real
AviUtl2 projection, lighting, depth weighting, or Direct3D sampling in full.
In particular, they cannot establish actual brightness or color fidelity.

``rasterize_quads`` retains the original RGBA8 preview model used by the
existing sandbox checks. Its color calculation is a heuristic, not a faithful
simulation of HDR/OIT. ``rasterize_emission`` provides two explicit floating
point alternatives for controlled comparisons: an equal-depth-weight version
of the project's WBOIT equation, and the premultiplied-over model used in its
older GPU sandbox. Both treat shader RGB as an emissive premultiplied quantity
and preserve values > 1 until the final background composition.
"""
from __future__ import annotations

from typing import Iterator, Optional

import numpy as np

# Based on the project's reverse-engineering notes, not an official guarantee.
# We model values at the cutoff as discarded, matching the archived GPU sandbox.
DISCARD_W = 0.001


def _sample_bilinear(tex: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Sample an RGBA8 or 0..1 float texture at clamped normalized uv."""
    h, w = tex.shape[:2]
    x = np.clip(u, 0.0, 1.0) * w - 0.5
    y = np.clip(v, 0.0, 1.0) * h - 0.5
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)
    x0c = np.clip(x0, 0, w - 1)
    x1c = np.clip(x0 + 1, 0, w - 1)
    y0c = np.clip(y0, 0, h - 1)
    y1c = np.clip(y0 + 1, 0, h - 1)
    t = tex.astype(np.float32) / 255.0 if tex.dtype == np.uint8 else tex
    c00 = t[y0c, x0c]
    c10 = t[y0c, x1c]
    c01 = t[y1c, x0c]
    c11 = t[y1c, x1c]
    wx = fx[..., None]
    wy = fy[..., None]
    top = c00 * (1 - wx) + c10 * wx
    bot = c01 * (1 - wx) + c11 * wx
    return top * (1 - wy) + bot * wy


def _fragments(atlas: Optional[np.ndarray], quads, width: int, height: int) -> Iterator:
    """Yield surviving fragments as (y, x, sampled_rgba, drawpoly_alpha).

    A quad's ``atlas`` is the snapshot captured at its draw call. Falling back to
    the positional ``atlas`` keeps standalone callers using the old API working.
    Projection is orthographic: vertex x/y are relative to screen center.
    """
    cx, cy = width * 0.5, height * 0.5
    cache = {}
    for quad in quads:
        verts = quad["verts"]
        texture = quad.get("atlas", atlas)
        if len(verts) != 4 or texture is None or not texture.size:
            continue
        alpha = float(quad.get("alpha", 1.0))
        if alpha <= 0.0:
            continue
        key = id(texture)
        if key not in cache:
            cache[key] = (texture.astype(np.float32) / 255.0
                          if texture.dtype == np.uint8 else np.asarray(texture, np.float32))
        tex_float = cache[key]
        pts = np.array([[cx + v[0], cy + v[1]] for v in verts], np.float64)
        uvs = np.array([[v[3], v[4]] for v in verts], np.float64)
        for ti, (ia, ib, ic) in enumerate(((0, 1, 2), (0, 2, 3))):
            x0, y0 = pts[ia]
            x1, y1 = pts[ib]
            x2, y2 = pts[ic]
            area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(area) < 1e-9:
                continue
            minx = int(max(0, np.floor(min(x0, x1, x2))))
            maxx = int(min(width - 1, np.ceil(max(x0, x1, x2))))
            miny = int(max(0, np.floor(min(y0, y1, y2))))
            maxy = int(min(height - 1, np.ceil(max(y0, y1, y2))))
            if maxx < minx or maxy < miny:
                continue
            ys, xs = np.mgrid[miny:maxy + 1, minx:maxx + 1]
            px, py = xs + 0.5, ys + 0.5
            inv = 1.0 / area
            w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) * inv
            w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) * inv
            w2 = 1.0 - w0 - w1
            mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if ti == 1:
                # The shared diagonal is already owned by triangle 0. Without
                # this rule, pixels exactly on it count the same slice twice.
                mask &= w2 > 1e-6
            if not mask.any():
                continue
            u = w0 * uvs[ia, 0] + w1 * uvs[ib, 0] + w2 * uvs[ic, 0]
            v = w0 * uvs[ia, 1] + w1 * uvs[ib, 1] + w2 * uvs[ic, 1]
            tex = _sample_bilinear(tex_float, u[mask], v[mask])
            keep = tex[:, 3] * alpha > DISCARD_W
            if keep.any():
                yield ys[mask][keep], xs[mask][keep], tex[keep], alpha


def rasterize_quads(atlas: Optional[np.ndarray], quads, screen_w: int, screen_h: int,
                    normalize: bool = False) -> np.ndarray:
    """Return the legacy *approximate* straight-alpha RGBA8 preview.

    Alpha is coverage and RGB is a heuristic weighted average. This format is
    useful for shape tests but clips HDR and should not be used for photometry.
    """
    W, H = int(screen_w), int(screen_h)
    acc_rgb = np.zeros((H, W, 3), np.float32)
    reveal = np.ones((H, W), np.float32)
    wsum = np.zeros((H, W), np.float32)
    for ys, xs, tex, alpha in _fragments(atlas, quads, W, H):
        w = tex[:, 3] * alpha
        acc_rgb[ys, xs] += tex[:, :3] * w[:, None]
        wsum[ys, xs] += w
        reveal[ys, xs] *= 1.0 - np.clip(w, 0.0, 1.0)
    out = np.zeros((H, W, 4), np.float32)
    nz = wsum > 1e-6
    out[..., :3][nz] = acc_rgb[nz] / wsum[nz][:, None]
    if not normalize:
        out[..., 3] = 1.0 - reveal
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def rasterize_emission(atlas: Optional[np.ndarray], quads, screen_w: int, screen_h: int,
                       mode: str = "weighted") -> np.ndarray:
    """Resolve quads into *premultiplied* float RGBA under an assumed model.

    ``weighted``: equal depth weights, Reveal=product(1-a),
      RGB=(1-Reveal)*sum(tex.rgb*poly_alpha)/sum(tex.a*poly_alpha).
    ``over``: front-to-back premultiplied over, in recorded draw order, matching
      the model in the archived GPU sandbox; not a reconstruction of host OIT.

    Neither includes real 3D depth weighting, lighting, or perspective. RGB can
    exceed one (HDR) and must not be clipped until a background is composed.
    """
    if mode not in ("weighted", "over"):
        raise ValueError("mode must be 'weighted' or 'over'")
    W, H = int(screen_w), int(screen_h)
    rgb = np.zeros((H, W, 3), np.float32)
    trans = np.ones((H, W), np.float32)
    wsum = np.zeros((H, W), np.float32)
    for ys, xs, tex, alpha in _fragments(atlas, quads, W, H):
        a = np.clip(tex[:, 3] * alpha, 0.0, 1.0)
        emission = tex[:, :3] * alpha
        if mode == "weighted":
            rgb[ys, xs] += emission
            wsum[ys, xs] += a
        else:
            rgb[ys, xs] += emission * trans[ys, xs, None]
        trans[ys, xs] *= 1.0 - a
    opacity = 1.0 - trans
    if mode == "weighted":
        nz = wsum > 1e-8
        rgb[nz] *= (opacity[nz] / wsum[nz])[:, None]
    return np.concatenate((rgb, opacity[..., None]), axis=-1)
