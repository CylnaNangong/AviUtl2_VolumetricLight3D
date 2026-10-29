"""
Software replacement for AviUtl2's 3D `obj.drawpoly()` compositor.

The script emits one textured quad per volume slice (optionally subdivided into
a knot grid for point/spot lights) and relies on AviUtl2's Weight-Blended OIT
pass.  According to the script's own reverse-engineering notes the host does:

    discard when tex.a * vertex_alpha < 0.001
    Out = (1 - Reveal) * sum(rgb * w(z)) / sum(tex.a * w(z))

i.e. the texture carries a *straight* colour and alpha is only a coverage /
weight.  The sandbox therefore accumulates

    w      = tex.a * vertex_alpha        (per fragment weight)
    rgb_acc += tex.rgb * w ; w_acc += w
    Reveal *= (1 - w)

and resolves rgb = rgb_acc / w_acc, a = 1 - Reveal.  That reproduces colour
ratios, silhouettes and slice ordering faithfully; absolute brightness and the
exact depth weight w(z) are approximated (documented limitation).
"""
from __future__ import annotations

import numpy as np


def _sample_bilinear(tex: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Sample RGBA8 texture at normalised uv (clamped) -> float32 (...,4) in 0..1."""
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
    t = tex.astype(np.float32) / 255.0
    c00 = t[y0c, x0c]
    c10 = t[y0c, x1c]
    c01 = t[y1c, x0c]
    c11 = t[y1c, x1c]
    wx = fx[..., None]
    wy = fy[..., None]
    top = c00 * (1 - wx) + c10 * wx
    bot = c01 * (1 - wx) + c11 * wx
    return top * (1 - wy) + bot * wy


def rasterize_quads(atlas: np.ndarray, quads, screen_w: int, screen_h: int,
                    normalize: bool = False) -> np.ndarray:
    """Rasterise the recorded drawpoly quads.

    quads: [{"verts": [[x,y,z,u,v,(nx,ny,nz)], ...4], "alpha": float}, ...]
    Returns straight-alpha RGBA8 (h,w,4).
    """
    W, H = int(screen_w), int(screen_h)
    acc_rgb = np.zeros((H, W, 3), np.float32)
    reveal = np.ones((H, W), np.float32)
    wsum = np.zeros((H, W), np.float32)
    cx, cy = W * 0.5, H * 0.5

    for quad in quads:
        verts = quad["verts"]
        if len(verts) != 4:
            continue
        alpha = float(quad.get("alpha", 1.0))
        if alpha <= 0.0:
            continue
        pts = np.array([[cx + v[0], cy + v[1]] for v in verts], np.float64)
        uvs = np.array([[v[3], v[4]] for v in verts], np.float64)
        tris = [(0, 1, 2), (0, 2, 3)]
        for (ia, ib, ic) in tris:
            x0, y0 = pts[ia]
            x1, y1 = pts[ib]
            x2, y2 = pts[ic]
            area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(area) < 1e-9:
                continue
            minx = int(max(0, np.floor(min(x0, x1, x2))))
            maxx = int(min(W - 1, np.ceil(max(x0, x1, x2))))
            miny = int(max(0, np.floor(min(y0, y1, y2))))
            maxy = int(min(H - 1, np.ceil(max(y0, y1, y2))))
            if maxx < minx or maxy < miny:
                continue
            ys, xs = np.mgrid[miny:maxy + 1, minx:maxx + 1]
            px = xs + 0.5
            py = ys + 0.5
            inv = 1.0 / area
            w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) * inv
            w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) * inv
            w2 = 1.0 - w0 - w1
            mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not mask.any():
                continue
            u = w0 * uvs[ia, 0] + w1 * uvs[ib, 0] + w2 * uvs[ic, 0]
            v = w0 * uvs[ia, 1] + w1 * uvs[ib, 1] + w2 * uvs[ic, 1]
            tex = _sample_bilinear(atlas, u[mask], v[mask])
            w = tex[:, 3] * alpha
            keep = w > 1.0 / 255.0
            if not keep.any():
                continue
            ys_k = ys[mask][keep]
            xs_k = xs[mask][keep]
            w_k = w[keep]
            rgb_k = tex[keep, :3]
            acc_rgb[ys_k, xs_k] += rgb_k * w_k[:, None]
            wsum[ys_k, xs_k] += w_k
            reveal[ys_k, xs_k] *= (1.0 - np.clip(w_k, 0.0, 1.0))

    out = np.zeros((H, W, 4), np.float32)
    nz = wsum > 1e-6
    if normalize:
        out[..., :3][nz] = acc_rgb[nz] / wsum[nz][:, None]
    else:
        # WBOIT: colour is the alpha-weighted average, alpha the accumulated coverage
        out[..., :3][nz] = acc_rgb[nz] / wsum[nz][:, None]
        out[..., 3] = 1.0 - reveal
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)
