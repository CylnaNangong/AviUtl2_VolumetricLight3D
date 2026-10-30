"""Parser and scene loader for AviUtl ExEdit2 project files (``.aup2``).

Extracts sub-compositions (``[scene.N]``), evaluates animated trackbars at any
frame, resolves local test assets by basename, emulates ``OpenGlow`` (linear-add
multi-scale inverse-square HDR glow with canvas expansion), and maps localized
UI parameter names & ``--select`` option labels onto ``.obj2`` Lua parameter
variables so unmodified ``.obj2`` scripts can be driven directly from real
``.aup2`` projects inside the sandbox.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter

from .host import Host, HostConfig, Layer
from .obj2 import Obj2Script, parse_obj2


@dataclass
class Aup2SceneSpec:
    scene_id: int
    name: str
    width: int
    height: int
    fps: float
    camera: Optional[Dict[str, float]]
    camera_lights: List[Dict[str, Any]]
    layers: List[Layer]
    vl3d_items: List[Dict[str, Any]]  # all VolumetricLight3D / 体積光（3D） items in scene


def _eval_track(raw: str, rel_frame: float, span_frames: float) -> Optional[float]:
    s = raw.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    try:
        v0 = float(parts[0])
    except ValueError:
        return None
    if len(parts) == 1:
        return v0
    try:
        v1 = float(parts[1])
    except ValueError:
        return v0
    t = 0.0 if span_frames <= 0 else max(0.0, min(1.0, rel_frame / span_frames))
    mode = parts[2] if len(parts) >= 3 else "直線移動"
    if "加減速" in mode or "緩急" in mode:
        t = t * t * (3.0 - 2.0 * t)
    return v0 + (v1 - v0) * t


def apply_openglow_rgba(
    raw_straight_rgba: np.ndarray,
    *,
    strength: float = 9.0,
    spread: float = 100.0,
    falloff: float = 2.0,
    threshold_pct: float = 37.7,
    gamma: float = 2.2,
    fixed_size: bool = False,
) -> np.ndarray:
    """Approximate AviUtl2 ``OpenGlow`` (``リニア加算``) on an RGBA8 image.

    Strip-processed so 30MP+ assets (e.g. ``6501x4751``) run under ~180MB peak RAM.
    """
    h, w = raw_straight_rgba.shape[:2]
    pad = 0 if fixed_size else max(0, int(round(spread * 1.25)))
    oh, ow = h + 2 * pad, w + 2 * pad
    g = max(0.1, float(gamma))
    inv_g = 1.0 / g
    thr = float(threshold_pct) / 100.0
    knee = max(0.04, thr * 0.25)
    inv_2knee = 1.0 / max(1e-5, 2.0 * knee)

    # 1. Build 1/4-res bright-pass pyramid input directly by 4x4 strided averaging
    ds = 4
    sw, sh = max(1, ow // ds), max(1, oh // ds)
    small = np.zeros((sh, sw, 3), dtype=np.float32)
    for dy_s in range(ds):
        for dx_s in range(ds):
            oy_idx = np.arange(sh) * ds + dy_s - pad
            ox_idx = np.arange(sw) * ds + dx_s - pad
            vy = (oy_idx >= 0) & (oy_idx < h)
            vx = (ox_idx >= 0) & (ox_idx < w)
            sub = raw_straight_rgba[oy_idx[vy]][:, ox_idx[vx]]
            rgb_s = sub[..., :3].astype(np.float32) * (1.0 / 255.0)
            al = sub[..., 3].astype(np.float32) * (1.0 / 255.0)
            luma_s = 0.2126 * rgb_s[..., 0] + 0.7152 * rgb_s[..., 1] + 0.0722 * rgb_s[..., 2]
            gate = np.clip((luma_s - thr + knee) * inv_2knee, 0.0, 1.0)
            gate = gate * gate * (3.0 - 2.0 * gate) * al
            b_lin = np.power(np.clip(rgb_s, 0.0, 1.0), g) * gate[..., None]
            small[np.ix_(vy, vx)] += b_lin * (1.0 / float(ds * ds))

    def _gauss2d(arr: np.ndarray, sigma: float) -> np.ndarray:
        r = max(1, int(math.ceil(sigma * 3.0)))
        xs = np.arange(-r, r + 1, dtype=np.float32)
        k = np.exp(-0.5 * (xs / max(sigma, 1e-3)) ** 2)
        k /= k.sum()
        ph = np.pad(arr, ((0, 0), (r, r), (0, 0)), mode="constant")
        tmp = np.zeros_like(arr)
        for i in range(len(k)):
            tmp += k[i] * ph[:, i:i + arr.shape[1], :]
        pv = np.pad(tmp, ((r, r), (0, 0), (0, 0)), mode="constant")
        res = np.zeros_like(arr)
        for i in range(len(k)):
            res += k[i] * pv[i:i + arr.shape[0], :, :]
        return res

    radii_src = [2.0, 5.0, 11.0, 24.0, 52.0, 105.0]
    scale_fac = max(0.05, float(spread) / 100.0)
    bloom_small = np.zeros_like(small)
    w_sum = 0.0
    p_fall = max(0.2, float(falloff))
    for i, r0 in enumerate(radii_src):
        r_px = r0 * scale_fac
        w_oct = 1.0 / math.pow(1.0 + i, p_fall * 0.55)
        w_sum += w_oct
        bloom_small += w_oct * _gauss2d(small, max(0.4, r_px / ds))

    if w_sum > 0:
        # OpenGlow's 強さ trackbar is in percent (e.g. 9.0 -> 9% = 0.09 linear add gain)
        bloom_small *= (float(strength) * 0.01) / w_sum

    # 2. Compose output (oh, ow, 4) uint8 in row strips using bilinear upsampling of bloom_small
    strip_h = 256
    out = np.zeros((oh, ow, 4), dtype=np.uint8)
    xs_f = np.clip((np.arange(ow, dtype=np.float32) + 0.5) / ds - 0.5, 0.0, sw - 1.0)
    ix0 = np.floor(xs_f).astype(np.int32)
    ix1 = np.minimum(ix0 + 1, sw - 1)
    wx = (xs_f - ix0)[:, None]

    for oy0 in range(0, oh, strip_h):
        oy1 = min(oh, oy0 + strip_h)
        bh = oy1 - oy0
        ys_f = np.clip((np.arange(oy0, oy1, dtype=np.float32) + 0.5) / ds - 0.5, 0.0, sh - 1.0)
        iy0 = np.floor(ys_f).astype(np.int32)
        iy1 = np.minimum(iy0 + 1, sh - 1)
        wy = (ys_f - iy0)[:, None, None]

        r0 = (1.0 - wx) * bloom_small[iy0][:, ix0] + wx * bloom_small[iy0][:, ix1]
        r1 = (1.0 - wx) * bloom_small[iy1][:, ix0] + wx * bloom_small[iy1][:, ix1]
        b_strip = (1.0 - wy) * r0 + wy * r1  # (bh, ow, 3)
        a_strip = np.zeros((bh, ow), dtype=np.float32)

        # Add linear base image where (oy in [pad, pad+h))
        sy0 = max(oy0, pad)
        sy1 = min(oy1, pad + h)
        if sy1 > sy0:
            raw_sub = raw_straight_rgba[sy0 - pad:sy1 - pad]
            rgb_s = raw_sub[..., :3].astype(np.float32) * (1.0 / 255.0)
            al = raw_sub[..., 3].astype(np.float32) * (1.0 / 255.0)
            base_l = np.power(np.clip(rgb_s, 0.0, 1.0), g) * al[..., None]
            b_strip[sy0 - oy0:sy1 - oy0, pad:pad + w] += base_l
            a_strip[sy0 - oy0:sy1 - oy0, pad:pad + w] = al

        out_srgb = np.power(np.clip(b_strip, 0.0, 1.0), inv_g)
        glow_lum = np.max(out_srgb, axis=-1)
        out_a = np.clip(a_strip + (1.0 - a_strip) * np.clip(glow_lum, 0.0, 1.0), 0.0, 1.0)
        out[oy0:oy1, :, :3] = np.clip(np.round(out_srgb * 255.0), 0, 255).astype(np.uint8)
        out[oy0:oy1, :, 3] = np.clip(np.round(out_a * 255.0), 0, 255).astype(np.uint8)

    return out


def _premul_rgba8(straight_rgba: np.ndarray) -> np.ndarray:
    out = np.empty_like(straight_rgba, dtype=np.uint8)
    a = straight_rgba[..., 3:4].astype(np.float32) / 255.0
    out[..., :3] = np.clip(np.round(straight_rgba[..., :3].astype(np.float32) * a), 0, 255).astype(np.uint8)
    out[..., 3] = straight_rgba[..., 3]
    return out


_ASSET_CACHE: Dict[Tuple[str, Tuple[Any, ...]], np.ndarray] = {}


def _find_asset(raw_path: str, asset_dirs: Sequence[Path]) -> Optional[Path]:
    norm = raw_path.replace("\\", "/")
    base = os.path.basename(norm)
    for d in asset_dirs:
        cand = Path(d) / base
        if cand.is_file():
            return cand
    p = Path(norm)
    return p if p.is_file() else None


def parse_aup2_sections(aup2_path: str | Path) -> List[Tuple[str, Dict[str, str]]]:
    text = Path(aup2_path).read_text(encoding="utf-8-sig", errors="replace")
    sections: List[Tuple[str, Dict[str, str]]] = []
    cur_hdr: Optional[str] = None
    cur_kv: Dict[str, str] = {}
    for raw_ln in text.splitlines():
        ln = raw_ln.strip()
        if not ln:
            continue
        if ln.startswith("[") and ln.endswith("]"):
            if cur_hdr is not None:
                sections.append((cur_hdr, cur_kv))
            cur_hdr = ln[1:-1]
            cur_kv = {}
        elif "=" in ln and cur_hdr is not None:
            k, v = ln.split("=", 1)
            cur_kv[k.strip()] = v.strip()
    if cur_hdr is not None:
        sections.append((cur_hdr, cur_kv))
    return sections


def map_aup2_params_to_obj2(raw_kv: Dict[str, str], obj2: Obj2Script, rel_frame: float = 0.0, span: float = 1.0) -> Dict[str, Any]:
    """Translate ``.aup2`` localized UI keys/values into ``obj2`` Lua variable dict."""
    by_label = {p.label: p for p in obj2.params if p.label}
    by_name = {p.name: p for p in obj2.params}
    out: Dict[str, Any] = {}
    for k, v in raw_kv.items():
        if k.startswith("effect.") or k.endswith(".hide"):
            continue
        p = by_label.get(k) or by_name.get(k)
        if p is None:
            continue
        if p.kind == "track":
            fv = _eval_track(v, rel_frame, span)
            if fv is not None:
                out[p.name] = fv
        elif p.kind == "check":
            fv = _eval_track(v, rel_frame, span)
            if fv is not None:
                out[p.name] = 1.0 if fv >= 0.5 else 0.0
        elif p.kind == "select":
            matched = None
            for opt_lbl, opt_val in p.options:
                if opt_lbl == v:
                    matched = opt_val
                    break
            if matched is None:
                fv = _eval_track(v, rel_frame, span)
                if fv is not None:
                    matched = fv
            if matched is not None:
                out[p.name] = float(matched)
        elif p.kind == "color":
            s = v.strip().lstrip("#")
            if not s:
                out[p.name] = None
            else:
                try:
                    out[p.name] = int(s, 16)
                except ValueError:
                    pass
        elif p.kind in ("string", "file"):
            out[p.name] = v
    return out


def load_aup2_scene(
    aup2_path: str | Path,
    scene_name_or_id: str | int,
    *,
    frame: int = 0,
    asset_dirs: Sequence[str | Path] = (),
    apply_openglow: bool = True,
) -> Aup2SceneSpec:
    """Load one ``[scene.N]`` from an ``.aup2`` file at timeline ``frame``."""
    sections = parse_aup2_sections(aup2_path)
    dirs = [Path(d) for d in asset_dirs]

    # Locate scene header
    target_scene_id: Optional[int] = None
    scene_meta: Dict[str, str] = {}
    for hdr, kv in sections:
        if hdr.startswith("scene."):
            sid = int(kv.get("scene", hdr.split(".")[1]))
            sname = kv.get("name", "")
            if (isinstance(scene_name_or_id, int) and sid == scene_name_or_id) or (str(scene_name_or_id) in (sname, str(sid))):
                target_scene_id = sid
                scene_meta = kv
                break
    if target_scene_id is None:
        raise ValueError(f"Scene {scene_name_or_id!r} not found in {aup2_path}")

    width = int(scene_meta.get("video.width", 1920))
    height = int(scene_meta.get("video.height", 1080))
    fps = float(scene_meta.get("video.rate", 30.0)) / max(1.0, float(scene_meta.get("video.scale", 1.0)))

    # Hidden UI layers in this scene (note: .aup2 layer=N is 0-based; UI layer is N+1)
    hidden_layers_0: set[int] = set()
    for hdr, kv in sections:
        if hdr.startswith("layer.") and int(kv.get("scene", -1)) == target_scene_id:
            if int(kv.get("hide", 0)) != 0:
                hidden_layers_0.add(int(kv["layer"]))

    # Group [item] and [item.sub] sections belonging to target_scene_id
    items_by_id: Dict[str, Dict[str, Any]] = {}
    for hdr, kv in sections:
        if re.fullmatch(r"\d+", hdr):
            if int(kv.get("scene", -1)) == target_scene_id:
                f_parts = [int(x) for x in kv.get("frame", "0,0").split(",")]
                items_by_id[hdr] = {
                    "id": int(hdr),
                    "layer0": int(kv.get("layer", 0)),
                    "start": f_parts[0],
                    "end": f_parts[1] if len(f_parts) > 1 else f_parts[0],
                    "subs": [],
                }
        elif re.fullmatch(r"\d+\.\d+", hdr):
            parent = hdr.split(".")[0]
            if parent in items_by_id:
                items_by_id[parent]["subs"].append(kv)

    camera: Optional[Dict[str, float]] = None
    camera_raw_target_ui_layer: int = 0
    camera_lights: List[Dict[str, Any]] = []
    layers_by_ui: Dict[int, Layer] = {}
    vl3d_items: List[Dict[str, Any]] = []

    for item in sorted(items_by_id.values(), key=lambda x: (x["layer0"], x["start"])):
        s_f, e_f = item["start"], item["end"]
        if not (s_f <= frame <= e_f):
            continue
        rel_f = float(frame - s_f)
        span_f = float(max(1, e_f - s_f))
        ui_layer = item["layer0"] + 1
        is_enabled = item["layer0"] not in hidden_layers_0
        subs = item["subs"]
        if not subs:
            continue

        fx0 = subs[0].get("effect.name", "")
        if fx0 == "カメラ制御" and is_enabled:
            fov_deg = _eval_track(subs[0].get("視野角", "0"), rel_f, span_f) or 0.0
            if abs(fov_deg) > 1e-4:
                focal_d = (height * 0.5) / math.tan(math.radians(max(1.0, min(170.0, 60.0 + fov_deg)) * 0.5))
            else:
                focal_d = 1024.0
            camera = {
                "x": _eval_track(subs[0].get("X", "0"), rel_f, span_f) or 0.0,
                "y": _eval_track(subs[0].get("Y", "0"), rel_f, span_f) or 0.0,
                "z": _eval_track(subs[0].get("Z", "-1024"), rel_f, span_f) or -1024.0,
                "tx": _eval_track(subs[0].get("目標X", "0"), rel_f, span_f) or 0.0,
                "ty": _eval_track(subs[0].get("目標Y", "0"), rel_f, span_f) or 0.0,
                "tz": _eval_track(subs[0].get("目標Z", "0"), rel_f, span_f) or 0.0,
                "rz": _eval_track(subs[0].get("傾き", "0"), rel_f, span_f) or 0.0,
                "d": focal_d,
            }
            camera_raw_target_ui_layer = int(_eval_track(subs[0].get("目標レイヤー", "0"), rel_f, span_f) or 0)
            for sub in subs[1:]:
                if int(sub.get("effect.disable", "0")) != 0:
                    continue
                fn = sub.get("effect.name", "")
                if "光源(カメラ制御)" in fn or "ライト" in fn:
                    camera_lights.append(dict(sub))
            continue

        if fx0 in ("VolumetricLight3D", "体積光（3D）"):
            draw_sub = next((s for s in subs[1:] if s.get("effect.name") == "標準描画"), {})
            vl3d_items.append({
                "ui_layer": ui_layer,
                "enabled": is_enabled,
                "effect_name": fx0,
                "raw_kv": dict(subs[0]),
                "draw_kv": dict(draw_sub),
                "rel_frame": rel_f,
                "span_frames": span_f,
                "start": s_f,
                "end": e_f,
            })
            continue

        # Standard scene object layer (画像ファイル, 図形, テキスト, モデルファイル, VRM...)
        draw_sub = next((s for s in subs if s.get("effect.name") in ("標準描画", "映像再生")), {})
        x = _eval_track(draw_sub.get("X", "0"), rel_f, span_f) or 0.0
        y = _eval_track(draw_sub.get("Y", "0"), rel_f, span_f) or 0.0
        z = _eval_track(draw_sub.get("Z", "0"), rel_f, span_f) or 0.0
        cx = _eval_track(draw_sub.get("中心X", "0"), rel_f, span_f) or 0.0
        cy = _eval_track(draw_sub.get("中心Y", "0"), rel_f, span_f) or 0.0
        cz = _eval_track(draw_sub.get("中心Z", "0"), rel_f, span_f) or 0.0
        rx = _eval_track(draw_sub.get("X軸回転", "0"), rel_f, span_f) or 0.0
        ry = _eval_track(draw_sub.get("Y軸回転", "0"), rel_f, span_f) or 0.0
        rz = _eval_track(draw_sub.get("Z軸回転", "0"), rel_f, span_f) or 0.0
        zoom = _eval_track(draw_sub.get("拡大率", "100"), rel_f, span_f) or 100.0
        aspect = _eval_track(draw_sub.get("縦横比", "0"), rel_f, span_f) or 0.0
        transp = _eval_track(draw_sub.get("透明度", "0"), rel_f, span_f) or 0.0
        alpha = max(0.0, min(1.0, 1.0 - transp / 100.0))
        sx_opt = (zoom / 100.0) * min(1.0, 1.0 - aspect / 100.0) if abs(aspect) > 1e-6 else None
        sy_opt = (zoom / 100.0) * min(1.0, 1.0 + aspect / 100.0) if abs(aspect) > 1e-6 else None

        raw_img_premul: Optional[np.ndarray] = None
        fx_img_premul: Optional[np.ndarray] = None
        effects_dict: Dict[str, Dict[str, Any]] = {}
        for sub in subs:
            if int(sub.get("effect.disable", "0")) != 0:
                continue
            sn = sub.get("effect.name", "")
            if sn:
                effects_dict[sn] = {
                    k: (_eval_track(v, rel_f, span_f) if _eval_track(v, rel_f, span_f) is not None else v)
                    for k, v in sub.items()
                    if not k.startswith("effect.") and not k.endswith(".hide")
                }

        if fx0 == "画像ファイル":
            fpath = _find_asset(subs[0].get("ファイル", ""), dirs)
            if fpath is not None:
                raw_key = (str(fpath), ("raw",))
                if raw_key not in _ASSET_CACHE:
                    straight = np.asarray(Image.open(fpath).convert("RGBA"), dtype=np.uint8)
                    _ASSET_CACHE[raw_key] = straight
                straight = _ASSET_CACHE[raw_key]
                premul_key = (str(fpath), ("premul",))
                if premul_key not in _ASSET_CACHE:
                    _ASSET_CACHE[premul_key] = _premul_rgba8(straight)
                raw_img_premul = _ASSET_CACHE[premul_key]
                fx_img_premul = raw_img_premul

                og_sub = next(
                    (s for s in subs[1:] if s.get("effect.name") == "OpenGlow" and int(s.get("effect.disable", "0")) == 0),
                    None,
                )
                if apply_openglow and og_sub is not None:
                    og_params = (
                        round(_eval_track(og_sub.get("強さ", "9.0"), rel_f, span_f) or 9.0, 3),
                        round(_eval_track(og_sub.get("拡散", "100.0"), rel_f, span_f) or 100.0, 3),
                        round(_eval_track(og_sub.get("減衰", "2.0"), rel_f, span_f) or 2.0, 3),
                        round(_eval_track(og_sub.get("しきい値", "37.7"), rel_f, span_f) or 37.7, 3),
                        round(_eval_track(og_sub.get("ガンマ", "2.2"), rel_f, span_f) or 2.2, 3),
                        int(_eval_track(og_sub.get("サイズを固定", og_sub.get("サイズ固定", "0")), rel_f, span_f) or 0),
                    )
                    og_key = (str(fpath), ("openglow",) + og_params)
                    if og_key not in _ASSET_CACHE:
                        _ASSET_CACHE[og_key] = apply_openglow_rgba(
                            straight,
                            strength=og_params[0],
                            spread=og_params[1],
                            falloff=og_params[2],
                            threshold_pct=og_params[3],
                            gamma=og_params[4],
                            fixed_size=bool(og_params[5]),
                        )
                    fx_img_premul = _ASSET_CACHE[og_key]

        layers_by_ui[ui_layer] = Layer(
            index=ui_layer,
            image=fx_img_premul,
            raw_image=raw_img_premul,
            x=x, y=y, z=z,
            rx=rx, ry=ry, rz=rz,
            cx=cx, cy=cy, cz=cz,
            zoom=zoom, alpha=alpha,
            sx=sx_opt, sy=sy_opt,
            effects=effects_dict,
            start=s_f, end=e_f,
            enabled=is_enabled,
            fx_name=fx0,
        )

    # Resolve カメラ制御 目標レイヤー (1-based UI layer index; if pointing to camera layer itself, tx/ty/tz = cam x/y/z)
    if camera is not None and camera_raw_target_ui_layer > 0:
        if camera_raw_target_ui_layer in layers_by_ui:
            tlay = layers_by_ui[camera_raw_target_ui_layer]
            camera["tx"], camera["ty"], camera["tz"] = tlay.x, tlay.y, tlay.z
        else:
            # Target layer is the camera layer itself (e.g. 目標レイヤー=1 when camera is on UI layer 1)
            camera["tx"], camera["ty"], camera["tz"] = camera["x"], camera["y"], camera["z"]

    return Aup2SceneSpec(
        scene_id=target_scene_id,
        name=scene_meta.get("name", ""),
        width=width,
        height=height,
        fps=fps,
        camera=camera,
        camera_lights=camera_lights,
        layers=[layers_by_ui[k] for k in sorted(layers_by_ui)],
        vl3d_items=vl3d_items,
    )
