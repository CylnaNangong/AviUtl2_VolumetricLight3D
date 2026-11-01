"""3D Camera Control + Depth Buffer + AviUtl2 Shader #118 OIT compositor.

Compiled to native C++17 with OpenMP (`-O3 -fopenmp`) so full-resolution
`2048x1080` scenes with `6501x4751` OpenGlow backgrounds and `160` volumetric
beam slices (`3584x3584` atlases) composite in well under a second.

Supports multiple OIT resolve models for controlled verification:
  * ``"wboit_exp"``     -- Shader #118 WBOIT with exponential depth weight
                           ``w(zc) = exp(-zc / z_scale)`` (default ``z_scale=1024``):
                           ``Out = Bg * Reveal + (1 - Reveal) * sum(w*r4.rgb) / sum(w*r4.w)``
  * ``"wboit_pow2"``    -- Shader #118 WBOIT with inverse-square depth weight
                           ``w(zc) = clamp((1024 / max(zc, 1))^2, 1e-4, 3e4)``
  * ``"wboit_hw118"``   -- Calibrated live AviUtl2 v2.1.16 Shader #118 WBOIT:
                           non-normalized ``r4.rgb = tex.rgb * alpha * gain`` with
                           steep foreground depth dominance ``(1024 / max(zc, 16))^8``
  * ``"wboit_uniform"`` -- Equal-depth-weight WBOIT (``w(zc) = 1``)
  * ``"over"``          -- Strict back-to-front premultiplied Porter-Duff over
  * ``"pure_additive"`` -- Unattenuated additive ``Bg + sum(r4.rgb)`` (reference upper bound)
"""
from __future__ import annotations

import ctypes
import hashlib
import math
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

_WORK_DIR = Path("/tmp/vl3dsb_comp3d")

_CPP_SRC = r"""
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

struct QuadSpec {
    // 4 vertices in camera space: (xc, yc, zc, u, v), u/v in pixel coords [0..tw, 0..th]
    float v[4][5];
    float alpha;          // effective poly_alpha * obj_alpha
    float gain_r, gain_g, gain_b;
    int32_t shader118;    // 1 = blend="none" Shader #118 (discard a118 <= 0.001), 0 = normal card
    int32_t sampler_dot;  // 1 = nearest, 0 = bilinear clamp
    int32_t tex_id;       // index into tex_ptrs / tex_wh
    int32_t order_idx;    // submission order
};

static inline void sample_tex(
    const uint8_t* tex, int tw, int th, float u_px, float v_px, int nearest,
    float& r, float& g, float& b, float& a
) {
    if (nearest) {
        int ix = std::clamp((int)std::floor(u_px), 0, tw - 1);
        int iy = std::clamp((int)std::floor(v_px), 0, th - 1);
        const uint8_t* p = tex + ((size_t)iy * tw + ix) * 4;
        const float inv = 1.0f / 255.0f;
        r = p[0] * inv; g = p[1] * inv; b = p[2] * inv; a = p[3] * inv;
        return;
    }
    float fx = std::clamp(u_px - 0.5f, 0.0f, (float)(tw - 1));
    float fy = std::clamp(v_px - 0.5f, 0.0f, (float)(th - 1));
    int x0 = (int)std::floor(fx);
    int y0 = (int)std::floor(fy);
    int x1 = std::min(x0 + 1, tw - 1);
    int y1 = std::min(y0 + 1, th - 1);
    float tx = fx - x0;
    float ty = fy - y0;
    const uint8_t* p00 = tex + ((size_t)y0 * tw + x0) * 4;
    const uint8_t* p10 = tex + ((size_t)y0 * tw + x1) * 4;
    const uint8_t* p01 = tex + ((size_t)y1 * tw + x0) * 4;
    const uint8_t* p11 = tex + ((size_t)y1 * tw + x1) * 4;
    float w00 = (1.0f - tx) * (1.0f - ty) * (1.0f / 255.0f);
    float w10 = tx * (1.0f - ty) * (1.0f / 255.0f);
    float w01 = (1.0f - tx) * ty * (1.0f / 255.0f);
    float w11 = tx * ty * (1.0f / 255.0f);
    r = p00[0] * w00 + p10[0] * w10 + p01[0] * w01 + p11[0] * w11;
    g = p00[1] * w00 + p10[1] * w10 + p01[1] * w01 + p11[1] * w11;
    b = p00[2] * w00 + p10[2] * w10 + p01[2] * w01 + p11[2] * w11;
    a = p00[3] * w00 + p10[3] * w10 + p01[3] * w01 + p11[3] * w11;
}

struct TriCam {
    float sx[3], sy[3], inv_z[3], u_oz[3], v_oz[3];
    float min_x, max_x, min_y, max_y;
    float mean_zc;
    int quad_idx;
};

static void clip_and_emit_tri(
    const float v0[5], const float v1[5], const float v2[5],
    float sw, float sh, float focal_d, int quad_idx,
    std::vector<TriCam>& out
) {
    const float z_near = 1.0f;
    float poly[8][5];
    poly[0][0]=v0[0]; poly[0][1]=v0[1]; poly[0][2]=v0[2]; poly[0][3]=v0[3]; poly[0][4]=v0[4];
    poly[1][0]=v1[0]; poly[1][1]=v1[1]; poly[1][2]=v1[2]; poly[1][3]=v1[3]; poly[1][4]=v1[4];
    poly[2][0]=v2[0]; poly[2][1]=v2[1]; poly[2][2]=v2[2]; poly[2][3]=v2[3]; poly[2][4]=v2[4];
    int n = 3;

    // Sutherland-Hodgman clip against zc >= z_near
    float clipped[8][5];
    int cn = 0;
    for (int i = 0; i < n; ++i) {
        const float* A = poly[i];
        const float* B = poly[(i + 1) % n];
        bool inA = A[2] >= z_near;
        bool inB = B[2] >= z_near;
        if (inA && inB) {
            std::memcpy(clipped[cn++], B, sizeof(float) * 5);
        } else if (inA && !inB) {
            float t = (z_near - A[2]) / (B[2] - A[2]);
            for (int k = 0; k < 5; ++k) clipped[cn][k] = A[k] + t * (B[k] - A[k]);
            clipped[cn][2] = z_near;
            cn++;
        } else if (!inA && inB) {
            float t = (z_near - A[2]) / (B[2] - A[2]);
            for (int k = 0; k < 5; ++k) clipped[cn][k] = A[k] + t * (B[k] - A[k]);
            clipped[cn][2] = z_near;
            cn++;
            std::memcpy(clipped[cn++], B, sizeof(float) * 5);
        }
    }
    if (cn < 3) return;

    float hx = sw * 0.5f, hy = sh * 0.5f;
    for (int i = 1; i + 1 < cn; ++i) {
        const float* tv[3] = { clipped[0], clipped[i], clipped[i + 1] };
        TriCam T;
        T.quad_idx = quad_idx;
        T.min_x = 1e30f; T.max_x = -1e30f;
        T.min_y = 1e30f; T.max_y = -1e30f;
        T.mean_zc = (tv[0][2] + tv[1][2] + tv[2][2]) * (1.0f / 3.0f);
        for (int k = 0; k < 3; ++k) {
            float iz = 1.0f / tv[k][2];
            float sx = hx + tv[k][0] * focal_d * iz;
            float sy = hy + tv[k][1] * focal_d * iz;
            T.sx[k] = sx;
            T.sy[k] = sy;
            T.inv_z[k] = iz;
            T.u_oz[k] = tv[k][3] * iz;
            T.v_oz[k] = tv[k][4] * iz;
            T.min_x = std::min(T.min_x, sx);
            T.max_x = std::max(T.max_x, sx);
            T.min_y = std::min(T.min_y, sy);
            T.max_y = std::max(T.max_y, sy);
        }
        if (T.max_x < 0.0f || T.min_x >= sw || T.max_y < 0.0f || T.min_y >= sh) continue;
        out.push_back(T);
    }
}

extern "C" void vl3dsb_composite3d(
    int W, int H, float focal_d,
    int n_quads, const QuadSpec* quads,
    int n_tex, const uint8_t* const* tex_ptrs, const int32_t* tex_wh,
    int oit_mode, float z_scale,
    float* out_rgba   // (H * W * 4) float32 linear/display [0..inf)
) {
    std::vector<TriCam> tris;
    tris.reserve((size_t)n_quads * 2);
    for (int qi = 0; qi < n_quads; ++qi) {
        const QuadSpec& q = quads[qi];
        if (q.alpha <= 1e-7f || q.tex_id < 0 || q.tex_id >= n_tex) continue;
        clip_and_emit_tri(q.v[0], q.v[1], q.v[2], (float)W, (float)H, focal_d, qi, tris);
        clip_and_emit_tri(q.v[0], q.v[2], q.v[3], (float)W, (float)H, focal_d, qi, tris);
    }

    // For back-to-front modes (opaque card background or "over" mode), sort far-to-near
    std::vector<int> order(tris.size());
    for (size_t i = 0; i < tris.size(); ++i) order[i] = (int)i;
    std::stable_sort(order.begin(), order.end(), [&](int a, int b) {
        const TriCam& ta = tris[a];
        const TriCam& tb = tris[b];
        if (std::abs(ta.mean_zc - tb.mean_zc) > 1e-3f)
            return ta.mean_zc > tb.mean_zc;
        return quads[ta.quad_idx].order_idx < quads[tb.quad_idx].order_idx;
    });

    // Tile-bin triangles into 32x32 screen tiles so rows/tiles run lock-free in OpenMP!
    const int TILE = 32;
    const int TX = (W + TILE - 1) / TILE;
    const int TY = (H + TILE - 1) / TILE;
    std::vector<std::vector<int>> bins((size_t)(TX * TY));
    for (int ord_i = 0; ord_i < (int)order.size(); ++ord_i) {
        int ti = order[ord_i];
        const TriCam& T = tris[ti];
        int bx0 = std::max(0, (int)std::floor(T.min_x) / TILE);
        int bx1 = std::min(TX - 1, (int)std::floor(T.max_x) / TILE);
        int by0 = std::max(0, (int)std::floor(T.min_y) / TILE);
        int by1 = std::min(TY - 1, (int)std::floor(T.max_y) / TILE);
        for (int by = by0; by <= by1; ++by) {
            for (int bx = bx0; bx <= bx1; ++bx) {
                bins[(size_t)by * TX + bx].push_back(ti);
            }
        }
    }

    #pragma omp parallel for schedule(dynamic, 1)
    for (int tile_id = 0; tile_id < TX * TY; ++tile_id) {
        int bx = tile_id % TX;
        int by = tile_id / TX;
        int x_start = bx * TILE;
        int y_start = by * TILE;
        int tw_tile = std::min(TILE, W - x_start);
        int th_tile = std::min(TILE, H - y_start);
        const auto& bin = bins[tile_id];

        float bg_r[TILE * TILE] = {0};
        float bg_g[TILE * TILE] = {0};
        float bg_b[TILE * TILE] = {0};
        float bg_a[TILE * TILE] = {0};
        float z_op[TILE * TILE];
        for (int i = 0; i < tw_tile * th_tile; ++i) z_op[i] = 1e30f;

        // Pass 1: Opaque pixels (and non-shader118 scene cards drawn back-to-front into bg + z_opaque)
        // Wait: non-shader118 scene cards (like Window at z=2050 and Character at z=1220) have
        // opaque interior (a >= 0.999 -> writes z_op) and anti-aliased fringe.
        // AutoBlend character quad IS shader118=1 at z=1219.95 (< 1220.0): its opaque pixels
        // (r4.w >= 0.999) overwrite z_op and bg!
        // To ensure front-most opaque wins regardless of order, pass 1 checks zc <= z_op[p] for opaque,
        // and for non-shader118 semi-transparent card fringes blends back-to-front when zc <= z_op[p].
        // Wait: if we process opaque hits FIRST (or front-to-back / z-tested), an opaque pixel at z=1219.95
        // must beat z=1220.0 and z=2050.0! Since `bin` is sorted far-to-near (2050 -> 1220 -> 1219.95),
        // `zc <= z_op[p]` is TRUE for 2050 (writes 2050), TRUE for 1220 (overwrites 1220), and TRUE for
        // 1219.95 (overwrites 1219.95)! That handles BOTH opaque z-buffering AND back-to-front card fringes!
        for (int ti : bin) {
            const TriCam& T = tris[ti];
            const QuadSpec& q = quads[T.quad_idx];
            // Only scene cards (shader118 == 0) or potentially opaque shader118 quads (alpha >= 0.999) can hit Pass 1
            if (q.shader118 == 2 || (q.shader118 == 1 && q.alpha < 0.999f)) continue;

            float x0 = T.sx[0], y0 = T.sy[0];
            float x1 = T.sx[1], y1 = T.sy[1];
            float x2 = T.sx[2], y2 = T.sy[2];
            float denom = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
            if (std::abs(denom) < 1e-8f) continue;
            float inv_den = 1.0f / denom;

            int px0 = std::max(x_start, (int)std::floor(T.min_x));
            int px1 = std::min(x_start + tw_tile - 1, (int)std::floor(T.max_x));
            int py0 = std::max(y_start, (int)std::floor(T.min_y));
            int py1 = std::min(y_start + th_tile - 1, (int)std::floor(T.max_y));

            const uint8_t* tex = tex_ptrs[q.tex_id];
            int tex_w = tex_wh[q.tex_id * 2 + 0];
            int tex_h = tex_wh[q.tex_id * 2 + 1];

            for (int py = py0; py <= py1; ++py) {
                float fy = (float)py + 0.5f;
                for (int px = px0; px <= px1; ++px) {
                    float fx = (float)px + 0.5f;
                    float w1 = ((fx - x0) * (y2 - y0) - (x2 - x0) * (fy - y0)) * inv_den;
                    float w2 = ((x1 - x0) * (fy - y0) - (fx - x0) * (y1 - y0)) * inv_den;
                    float w0 = 1.0f - w1 - w2;
                    if (w0 < -1e-5f || w1 < -1e-5f || w2 < -1e-5f) continue;

                    float iz = w0 * T.inv_z[0] + w1 * T.inv_z[1] + w2 * T.inv_z[2];
                    if (iz <= 1e-9f) continue;
                    float zc = 1.0f / iz;
                    int lp = (py - y_start) * tw_tile + (px - x_start);
                    if (zc > z_op[lp]) continue;

                    float u_px = (w0 * T.u_oz[0] + w1 * T.u_oz[1] + w2 * T.u_oz[2]) * zc;
                    float v_px = (w0 * T.v_oz[0] + w1 * T.v_oz[1] + w2 * T.v_oz[2]) * zc;
                    float tr, tg, tb, ta;
                    sample_tex(tex, tex_w, tex_h, u_px, v_px, q.sampler_dot, tr, tg, tb, ta);

                    float out_a = ta * q.alpha;
                    if (q.shader118) {
                        if (out_a < 0.999f) continue; // transparent shader118 pixels go to Pass 2 OIT!
                        bg_r[lp] = tr * q.alpha * q.gain_r;
                        bg_g[lp] = tg * q.alpha * q.gain_g;
                        bg_b[lp] = tb * q.alpha * q.gain_b;
                        bg_a[lp] = 1.0f;
                        z_op[lp] = zc;
                    } else {
                        if (out_a <= 1e-6f && (tr + tg + tb) <= 1e-6f) continue;
                        float sr = tr * q.alpha;
                        float sg = tg * q.alpha;
                        float sb = tb * q.alpha;
                        if (out_a >= 0.999f) {
                            bg_r[lp] = sr; bg_g[lp] = sg; bg_b[lp] = sb; bg_a[lp] = 1.0f;
                            z_op[lp] = zc;
                        } else {
                            float inv_a = 1.0f - out_a;
                            bg_r[lp] = sr + bg_r[lp] * inv_a;
                            bg_g[lp] = sg + bg_g[lp] * inv_a;
                            bg_b[lp] = sb + bg_b[lp] * inv_a;
                            bg_a[lp] = out_a + bg_a[lp] * inv_a;
                        }
                    }
                }
            }
        }

        // Pass 2: Transparent Shader #118 OIT pass + hardware additive (shader118 == 2) pass
        float acc_r[TILE * TILE] = {0};
        float acc_g[TILE * TILE] = {0};
        float acc_b[TILE * TILE] = {0};
        float acc_w[TILE * TILE] = {0};
        float add_r[TILE * TILE] = {0};
        float add_g[TILE * TILE] = {0};
        float add_b[TILE * TILE] = {0};
        float reveal[TILE * TILE];
        for (int i = 0; i < tw_tile * th_tile; ++i) reveal[i] = 1.0f;

        for (int ti : bin) {
            const TriCam& T = tris[ti];
            const QuadSpec& q = quads[T.quad_idx];
            if (!q.shader118) continue;

            float x0 = T.sx[0], y0 = T.sy[0];
            float x1 = T.sx[1], y1 = T.sy[1];
            float x2 = T.sx[2], y2 = T.sy[2];
            float denom = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
            if (std::abs(denom) < 1e-8f) continue;
            float inv_den = 1.0f / denom;

            int px0 = std::max(x_start, (int)std::floor(T.min_x));
            int px1 = std::min(x_start + tw_tile - 1, (int)std::floor(T.max_x));
            int py0 = std::max(y_start, (int)std::floor(T.min_y));
            int py1 = std::min(y_start + th_tile - 1, (int)std::floor(T.max_y));

            const uint8_t* tex = tex_ptrs[q.tex_id];
            int tex_w = tex_wh[q.tex_id * 2 + 0];
            int tex_h = tex_wh[q.tex_id * 2 + 1];

            for (int py = py0; py <= py1; ++py) {
                float fy = (float)py + 0.5f;
                for (int px = px0; px <= px1; ++px) {
                    float fx = (float)px + 0.5f;
                    float w1 = ((fx - x0) * (y2 - y0) - (x2 - x0) * (fy - y0)) * inv_den;
                    float w2 = ((x1 - x0) * (fy - y0) - (fx - x0) * (y1 - y0)) * inv_den;
                    float w0 = 1.0f - w1 - w2;
                    if (w0 < -1e-5f || w1 < -1e-5f || w2 < -1e-5f) continue;

                    float iz = w0 * T.inv_z[0] + w1 * T.inv_z[1] + w2 * T.inv_z[2];
                    if (iz <= 1e-9f) continue;
                    float zc = 1.0f / iz;
                    int lp = (py - y_start) * tw_tile + (px - x_start);
                    if (zc > z_op[lp]) continue;

                    float u_px = (w0 * T.u_oz[0] + w1 * T.u_oz[1] + w2 * T.u_oz[2]) * zc;
                    float v_px = (w0 * T.v_oz[0] + w1 * T.v_oz[1] + w2 * T.v_oz[2]) * zc;
                    float tr, tg, tb, ta;
                    sample_tex(tex, tex_w, tex_h, u_px, v_px, q.sampler_dot, tr, tg, tb, ta);

                    if (q.shader118 == 2) {
                        add_r[lp] += tr * q.alpha * q.gain_r;
                        add_g[lp] += tg * q.alpha * q.gain_g;
                        add_b[lp] += tb * q.alpha * q.gain_b;
                        continue;
                    }

                    float a118 = ta * q.alpha;
                    if (a118 <= 0.001f || a118 >= 0.999f) continue; // Shader #118 alpha discard / opaque handled in Pass 1

                    float r4_r = tr * q.alpha * q.gain_r;
                    float r4_g = tg * q.alpha * q.gain_g;
                    float r4_b = tb * q.alpha * q.gain_b;

                    if (oit_mode == 3) {
                        // Back-to-front Porter-Duff premultiplied over
                        float inv_a = 1.0f - a118;
                        bg_r[lp] = r4_r + bg_r[lp] * inv_a;
                        bg_g[lp] = r4_g + bg_g[lp] * inv_a;
                        bg_b[lp] = r4_b + bg_b[lp] * inv_a;
                        bg_a[lp] = a118 + bg_a[lp] * inv_a;
                    } else if (oit_mode == 4) {
                        // Pure additive reference
                        acc_r[lp] += r4_r;
                        acc_g[lp] += r4_g;
                        acc_b[lp] += r4_b;
                    } else {
                        float wz = 1.0f;
                        if (oit_mode == 0) {
                            wz = std::clamp(std::exp(-zc / std::max(z_scale, 1.0f)), 1e-5f, 1e4f);
                        } else if (oit_mode == 1) {
                            float zn = 1024.0f / std::max(zc, 1.0f);
                            wz = std::clamp(zn * zn, 1e-4f, 3e4f);
                        } else if (oit_mode == 5) {
                            float zn = 1024.0f / std::max(zc, 16.0f);
                            float zn2 = zn * zn;
                            float zn4 = zn2 * zn2;
                            wz = std::clamp(zn4 * zn4 * std::pow(a118, 0.25f), 1e-6f, 1e7f);
                        }
                        acc_r[lp] += r4_r * wz;
                        acc_g[lp] += r4_g * wz;
                        acc_b[lp] += r4_b * wz;
                        acc_w[lp] += a118 * wz;
                        reveal[lp] *= (1.0f - std::clamp(a118, 0.0f, 1.0f));
                    }
                }
            }
        }

        // Resolve tile to out_rgba
        for (int ly = 0; ly < th_tile; ++ly) {
            int py = y_start + ly;
            for (int lx = 0; lx < tw_tile; ++lx) {
                int px = x_start + lx;
                int lp = ly * tw_tile + lx;
                float fr = bg_r[lp], fg = bg_g[lp], fb = bg_b[lp], fa = bg_a[lp];
                if (oit_mode == 4) {
                    fr += acc_r[lp]; fg += acc_g[lp]; fb += acc_b[lp];
                } else if (oit_mode != 3 && acc_w[lp] > 1e-9f) {
                    float rev = reveal[lp];
                    float omr = 1.0f - rev;
                    float inv_w = omr / acc_w[lp];
                    fr = fr * rev + acc_r[lp] * inv_w;
                    fg = fg * rev + acc_g[lp] * inv_w;
                    fb = fb * rev + acc_b[lp] * inv_w;
                    fa = 1.0f - (1.0f - fa) * rev;
                }
                fr += add_r[lp];
                fg += add_g[lp];
                fb += add_b[lp];
                size_t dst = ((size_t)py * W + px) * 4;
                out_rgba[dst + 0] = fr;
                out_rgba[dst + 1] = fg;
                out_rgba[dst + 2] = fb;
                out_rgba[dst + 3] = fa;
            }
        }
    }
}
"""


class _QuadSpecC(ctypes.Structure):
    _fields_ = [
        ("v", (ctypes.c_float * 5) * 4),
        ("alpha", ctypes.c_float),
        ("gain_r", ctypes.c_float),
        ("gain_g", ctypes.c_float),
        ("gain_b", ctypes.c_float),
        ("shader118", ctypes.c_int32),
        ("sampler_dot", ctypes.c_int32),
        ("tex_id", ctypes.c_int32),
        ("order_idx", ctypes.c_int32),
    ]


_LIB_CACHE: Optional[ctypes.CDLL] = None


def _get_lib() -> ctypes.CDLL:
    global _LIB_CACHE
    if _LIB_CACHE is not None:
        return _LIB_CACHE
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(_CPP_SRC.encode("utf-8")).hexdigest()[:16]
    so_path = _WORK_DIR / f"libcomp3d_{h}.so"
    if not so_path.exists():
        cpp_path = _WORK_DIR / f"comp3d_{h}.cpp"
        cpp_path.write_text(_CPP_SRC, encoding="utf-8")
        subprocess.run(
            ["g++", "-std=c++17", "-O3", "-fopenmp", "-fPIC", "-shared", str(cpp_path), "-o", str(so_path)],
            check=True,
        )
    lib = ctypes.CDLL(str(so_path))
    lib.vl3dsb_composite3d.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_float,
        ctypes.c_int, ctypes.POINTER(_QuadSpecC),
        ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int, ctypes.c_float,
        ctypes.POINTER(ctypes.c_float),
    ]
    lib.vl3dsb_composite3d.restype = None
    _LIB_CACHE = lib
    return lib


def rot_xyz(pts: np.ndarray, rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """AviUtl2 intrinsic X -> Y -> Z rotation in 3D space (Y-down, Z-forward)."""
    p = np.asarray(pts, dtype=np.float64)
    if abs(rx_deg) > 1e-7:
        a = math.radians(rx_deg)
        c, s = math.cos(a), math.sin(a)
        y, z = p[..., 1] * c - p[..., 2] * s, p[..., 1] * s + p[..., 2] * c
        p = np.stack([p[..., 0], y, z], axis=-1)
    if abs(ry_deg) > 1e-7:
        a = math.radians(ry_deg)
        c, s = math.cos(a), math.sin(a)
        x, z = p[..., 0] * c + p[..., 2] * s, -p[..., 0] * s + p[..., 2] * c
        p = np.stack([x, p[..., 1], z], axis=-1)
    if abs(rz_deg) > 1e-7:
        a = math.radians(rz_deg)
        c, s = math.cos(a), math.sin(a)
        x, y = p[..., 0] * c - p[..., 1] * s, p[..., 0] * s + p[..., 1] * c
        p = np.stack([x, y, p[..., 2]], axis=-1)
    return p


def camera_basis(cam: Optional[Dict[str, float]]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """Return ``(cam_pos, r_ax, d_ax, f_ax, focal_d)`` matching ``VolumetricLight3D``."""
    cam_pos = np.array([0.0, 0.0, -1024.0], dtype=np.float64)
    focal_d = 1024.0
    f_ax = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    r_ax = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    d_ax = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    if not cam:
        return cam_pos, r_ax, d_ax, f_ax, focal_d

    cam_pos = np.array([
        float(cam.get("x", 0.0)),
        float(cam.get("y", 0.0)),
        float(cam.get("z", -1024.0)),
    ], dtype=np.float64)
    if float(cam.get("d", 0.0)) > 1.0:
        focal_d = float(cam["d"])

    tgt = np.array([
        float(cam.get("tx", 0.0)),
        float(cam.get("ty", 0.0)),
        float(cam.get("tz", 0.0)),
    ], dtype=np.float64)
    dv = tgt - cam_pos
    vl = float(np.linalg.norm(dv))
    if vl > 1e-6:
        f_ax = dv / vl

    up = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    if "ux" in cam:
        u_try = np.array([float(cam["ux"]), float(cam["uy"]), float(cam["uz"])], dtype=np.float64)
        if float(np.dot(u_try, u_try)) >= 1e-9:
            up = u_try

    r_raw = np.cross(f_ax, up)
    rl = float(np.linalg.norm(r_raw))
    r_ax = (r_raw / rl) if rl > 1e-6 else np.array([1.0, 0.0, 0.0], dtype=np.float64)
    d_raw = np.cross(f_ax, r_ax)
    dl = float(np.linalg.norm(d_raw))
    d_ax = (d_raw / dl) if dl > 1e-6 else np.array([0.0, 1.0, 0.0], dtype=np.float64)

    rz = float(cam.get("rz", 0.0))
    if abs(rz) > 1e-5:
        cr, sr = math.cos(math.radians(rz)), math.sin(math.radians(rz))
        r_ax, d_ax = r_ax * cr - d_ax * sr, r_ax * sr + d_ax * cr
    return cam_pos, r_ax, d_ax, f_ax, focal_d


OIT_MODES = {
    "wboit_exp": 0,
    "wboit_pow2": 1,
    "wboit_uniform": 2,
    "over": 3,
    "pure_additive": 4,
    "wboit_hw118": 5,
}


def _eval_host_light_quad(
    pw: np.ndarray,
    verts: Sequence[Sequence[float]],
    tf: Dict[str, Any],
    cl: Dict[str, float],
) -> Tuple[float, float, float]:
    unlit = float(cl.get("unlit", 0.0))
    amb = np.array([
        float(cl.get("amb_r", 0.0)),
        float(cl.get("amb_g", 0.0)),
        float(cl.get("amb_b", 0.0)),
    ], dtype=np.float64)
    lum = float(cl.get("lum", 1.0))
    cl_rgb = np.array([
        float(cl.get("r", 1.0)) * lum,
        float(cl.get("g", 1.0)) * lum,
        float(cl.get("b", 1.0)) * lum,
    ], dtype=np.float64)
    pc = pw.mean(axis=0)
    if len(verts[0]) >= 8:
        vn_loc = np.array([[float(v[5]), float(v[6]), float(v[7])] for v in verts[:4]], dtype=np.float64).mean(axis=0)
        nw = rot_xyz(vn_loc, float(tf.get("rx", 0.0)), float(tf.get("ry", 0.0)), float(tf.get("rz", 0.0)))
    else:
        nw = -np.cross(pw[1] - pw[0], pw[2] - pw[0])
    nl = float(np.linalg.norm(nw))
    nw = (nw / nl) if nl > 1e-8 else np.array([0.0, 0.0, -1.0], dtype=np.float64)

    ltype = int(round(float(cl.get("ltype", 5))))
    cone = 1.0
    if ltype >= 3:
        lp = np.array([float(cl.get("px", 0.0)), float(cl.get("py", 0.0)), float(cl.get("pz", 0.0))], dtype=np.float64)
        lv = lp - pc
        dist = float(np.linalg.norm(lv))
        lw = (lv / dist) if dist > 1e-6 else np.array([0.0, 0.0, 1.0], dtype=np.float64)
        if ltype >= 4:
            ray_d = -np.array([float(cl.get("dx", 0.0)), float(cl.get("dy", 0.0)), float(cl.get("dz", 1.0))], dtype=np.float64)
            rn = float(np.linalg.norm(ray_d))
            ray_d = (ray_d / rn) if rn > 1e-6 else np.array([0.0, 0.0, -1.0], dtype=np.float64)
            cos_sp = float(np.dot(-lw, ray_d))
            cos_out = float(cl.get("cos_ang", math.cos(math.radians(30.0))))
            cos_in = float(cl.get("cos_in", min(0.9999, cos_out + (1.0 - cos_out) * 0.35)))
            if cos_sp <= cos_out:
                cone = 0.0
            elif cos_sp < cos_in:
                t = (cos_sp - cos_out) / max(1e-6, cos_in - cos_out)
                cone = t * t * (3.0 - 2.0 * t)
    else:
        lw = np.array([float(cl.get("dx", 0.0)), float(cl.get("dy", 0.0)), float(cl.get("dz", 1.0))], dtype=np.float64)
        ln = float(np.linalg.norm(lw))
        lw = (lw / ln) if ln > 1e-6 else np.array([0.0, 0.0, 1.0], dtype=np.float64)

    ndotl = max(0.0, float(np.dot(nw, lw)))
    h = unlit + amb + cl_rgb * (cone * ndotl)
    return float(h[0]), float(h[1]), float(h[2])


def composite_scene_3d(
    width: int,
    height: int,
    camera: Optional[Dict[str, float]],
    scene_layers: Sequence[Any],
    vl3d_quads: Sequence[Dict[str, Any]],
    *,
    oit_mode: str = "wboit_exp",
    z_scale: float = 1024.0,
    host_light: Tuple[float, float, float] = (1.0, 1.0, 1.0),
    camera_light: Optional[Dict[str, float]] = None,
    include_autoblend: bool = True,
) -> np.ndarray:
    """Composite 3D scene card layers + ``VolumetricLight3D`` quads to float32 RGBA ``(H, W, 4)``."""
    if oit_mode not in OIT_MODES:
        raise ValueError(f"Unknown oit_mode={oit_mode!r}; expected one of {sorted(OIT_MODES)}")

    cam_pos, r_ax, d_ax, f_ax, focal_d = camera_basis(camera)

    tex_keepalive: List[np.ndarray] = []
    tex_id_by_ptr: Dict[int, int] = {}
    tex_ptrs: List[int] = []
    tex_wh: List[int] = []

    def _reg_tex(arr: np.ndarray) -> int:
        c_arr = np.ascontiguousarray(arr, dtype=np.uint8)
        # Deduplicate shared atlas references across quads in the same batch
        key = id(arr) if arr.flags["C_CONTIGUOUS"] and arr.dtype == np.uint8 else id(c_arr)
        if key in tex_id_by_ptr:
            return tex_id_by_ptr[key]
        use = arr if (arr.flags["C_CONTIGUOUS"] and arr.dtype == np.uint8) else c_arr
        tid = len(tex_ptrs)
        tex_keepalive.append(use)
        tex_ptrs.append(int(use.ctypes.data))
        tex_wh.extend([int(use.shape[1]), int(use.shape[0])])
        tex_id_by_ptr[key] = tid
        return tid

    c_quads: List[_QuadSpecC] = []
    order_counter = 0

    def _world_to_cam(pw: np.ndarray) -> np.ndarray:
        dv = pw - cam_pos[None, :]
        return np.stack([dv @ r_ax, dv @ d_ax, dv @ f_ax], axis=-1)

    # 1. Scene 2D-in-3D card layers (e.g. stained glass window, foreground character)
    for lay in scene_layers:
        if not getattr(lay, "enabled", True) or getattr(lay, "image", None) is None:
            continue
        img = lay.image
        ih, iw = img.shape[:2]
        tid = _reg_tex(img)
        zs = float(getattr(lay, "zoom", 100.0) or 100.0) / 100.0
        lay_sx = getattr(lay, "sx", None)
        lay_sy = getattr(lay, "sy", None)
        sx = float(lay_sx) if lay_sx is not None else zs
        sy = float(lay_sy) if lay_sy is not None else zs
        hx, hy = 0.5 * iw * sx, 0.5 * ih * sy
        cx = float(getattr(lay, "cx", None) or 0.0)
        cy = float(getattr(lay, "cy", None) or 0.0)
        cz = float(getattr(lay, "cz", None) or 0.0)
        qloc = np.array([
            [-hx - cx * sx, -hy - cy * sy, -cz],
            [ hx - cx * sx, -hy - cy * sy, -cz],
            [ hx - cx * sx,  hy - cy * sy, -cz],
            [-hx - cx * sx,  hy - cy * sy, -cz],
        ], dtype=np.float64)
        qw = rot_xyz(qloc, float(getattr(lay, "rx", 0.0)), float(getattr(lay, "ry", 0.0)), float(getattr(lay, "rz", 0.0)))
        qw += np.array([float(getattr(lay, "x", 0.0)), float(getattr(lay, "y", 0.0)), float(getattr(lay, "z", 0.0))])
        qcam = _world_to_cam(qw)
        uv_px = [(0.0, 0.0), (float(iw), 0.0), (float(iw), float(ih)), (0.0, float(ih))]

        qs = _QuadSpecC()
        for k in range(4):
            qs.v[k][0] = float(qcam[k, 0])
            qs.v[k][1] = float(qcam[k, 1])
            qs.v[k][2] = float(qcam[k, 2])
            qs.v[k][3] = uv_px[k][0]
            qs.v[k][4] = uv_px[k][1]
        qs.alpha = float(getattr(lay, "alpha", 1.0))
        qs.gain_r, qs.gain_g, qs.gain_b = 1.0, 1.0, 1.0
        qs.shader118 = 0
        qs.sampler_dot = 0
        qs.tex_id = tid
        qs.order_idx = order_counter
        order_counter += 1
        c_quads.append(qs)

    # 2. VolumetricLight3D drawpoly quads (AutoBlend foreground quad + volumetric slice quads)
    for q in vl3d_quads:
        atlas = q.get("atlas")
        verts = q.get("verts")
        if atlas is None or not verts or len(verts) < 4:
            continue
        if q.get("drawtarget", "framebuffer") != "framebuffer":
            continue
        # AutoBlend quads are 4-element or 5-element vertices without normal vectors and alpha ~ T.alpha (>=0.5)
        is_ab_quad = len(verts[0]) == 5 and float(q.get("alpha", 0.0)) >= 0.5 and atlas.shape[0] != atlas.shape[1]
        if is_ab_quad and not include_autoblend:
            continue

        ih, iw = atlas.shape[:2]
        tid = _reg_tex(atlas)
        tf = q.get("obj_tf") or {}
        zoom = float(tf.get("zoom", 1.0))
        cx, cy, cz = float(tf.get("cx", 0.0)), float(tf.get("cy", 0.0)), float(tf.get("cz", 0.0))
        pts = np.array([[v[0] * zoom - cx, v[1] * zoom - cy, v[2] * zoom - cz] for v in verts[:4]], dtype=np.float64)
        pw = rot_xyz(pts, float(tf.get("rx", 0.0)), float(tf.get("ry", 0.0)), float(tf.get("rz", 0.0)))
        pw += np.array([
            float(tf.get("x", 0.0)) + float(tf.get("ox", 0.0)),
            float(tf.get("y", 0.0)) + float(tf.get("oy", 0.0)),
            float(tf.get("z", 0.0)) + float(tf.get("oz", 0.0)),
        ], dtype=np.float64)
        qcam = _world_to_cam(pw)

        us = [float(v[3]) for v in verts[:4]]
        vs = [float(v[4]) for v in verts[:4]]
        if max(abs(u) for u in us) <= 1.5 and max(abs(v) for v in vs) <= 1.5:
            us = [u * iw for u in us]
            vs = [v * ih for v in vs]

        blend = str(q.get("blend", "none"))
        qs = _QuadSpecC()
        for k in range(4):
            qs.v[k][0] = float(qcam[k, 0])
            qs.v[k][1] = float(qcam[k, 1])
            qs.v[k][2] = float(qcam[k, 2])
            qs.v[k][3] = us[k]
            qs.v[k][4] = vs[k]
        qs.alpha = float(q.get("alpha", 1.0))
        if camera_light is not None:
            hr, hg, hb = _eval_host_light_quad(pw, verts, tf, camera_light)
        elif blend == "none":
            hr, hg, hb = float(host_light[0]), float(host_light[1]), float(host_light[2])
        else:
            hr, hg, hb = 1.0, 1.0, 1.0
        qs.gain_r, qs.gain_g, qs.gain_b = hr, hg, hb
        if blend in ("none", "add"):
            # Verified on live AviUtl2 v2.1.16 (0x1404404b0): in 3D Camera mode,
            # obj.setoption("blend", "add") inside an object script still routes
            # framebuffer drawpoly through Shader #118 WBOIT + host 3D lighting.
            qs.shader118 = 1
        else:
            qs.shader118 = 0
        qs.sampler_dot = 1 if q.get("sampler") == "dot" else 0
        qs.tex_id = tid
        qs.order_idx = order_counter
        order_counter += 1
        c_quads.append(qs)

    out = np.zeros((height, width, 4), dtype=np.float32)
    if not c_quads:
        return out

    lib = _get_lib()
    q_arr = (_QuadSpecC * len(c_quads))(*c_quads)
    p_arr = (ctypes.c_void_p * len(tex_ptrs))(*tex_ptrs)
    wh_arr = (ctypes.c_int32 * len(tex_wh))(*tex_wh)
    lib.vl3dsb_composite3d(
        int(width), int(height), float(focal_d),
        len(c_quads), q_arr,
        len(tex_ptrs), p_arr, wh_arr,
        int(OIT_MODES[oit_mode]), float(z_scale),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
    )
    return out
