"""End-to-end verification of VL3DLF v3 shared 3D light field (VolumetricLight3D v3.16 x Snow3D v1.23)."""
from __future__ import annotations

import base64
import math
import re
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from vl3dsb import api_lint, assets
from vl3dsb.config import REPO_ROOT
from vl3dsb.harness import check, require
from vl3dsb.hlsl.executor import ShaderLibrary
from vl3dsb.host import Host, HostConfig, Layer
from vl3dsb.obj2 import Obj2Script
from vl3dsb.session import native, script as load_script, shader_library

SNOW_ROOT = REPO_ROOT / "integrations" / "snow3d"
SNOW_OBJ2 = SNOW_ROOT / "Script" / "雪（3D）.obj2"
SNOW_LANG_DIR = SNOW_ROOT / "Language"


def _parse_aul2(path: Path) -> Dict[str, Dict[str, str]]:
    sections: Dict[str, Dict[str, str]] = {}
    cur: Optional[str] = None
    for raw_ln in path.read_text(encoding="utf-8-sig").splitlines():
        ln = raw_ln.strip()
        if not ln or ln.startswith(";"):
            continue
        if ln.startswith("[") and ln.endswith("]"):
            cur = ln[1:-1].strip()
            sections.setdefault(cur, {})
        elif cur is not None and "=" in raw_ln:
            k, v = raw_ln.split("=", 1)
            sections[cur][k.strip()] = v.strip()
    return sections

def snow_script() -> Obj2Script:
    return load_script(SNOW_OBJ2)


def snow_shaders() -> ShaderLibrary:
    return shader_library(SNOW_OBJ2)


def _solid_layer(idx: int, w: int, h: int, rgba: Tuple[int, int, int, int],
                 x: float = 0.0, y: float = 0.0, z: float = 0.0,
                 rx: float = 0.0, ry: float = 0.0, rz: float = 0.0,
                 fx_name: str = "") -> Layer:
    img = np.empty((h, w, 4), dtype=np.uint8)
    img[..., 0] = rgba[0]
    img[..., 1] = rgba[1]
    img[..., 2] = rgba[2]
    img[..., 3] = rgba[3]
    return Layer(index=idx, image=img, x=x, y=y, z=z, rx=rx, ry=ry, rz=rz, fx_name=fx_name)


def _run_vl(layers: Sequence[Layer], params: Dict[str, Any], bus: Dict[str, bytes], *,
            layer_index: int = 3, scene_id: int = 0, frame: int = 0, frame_s: int = 0,
            camera: Optional[Dict[str, float]] = None, camera_mode: int = 1,
            edit_camera: Optional[Dict[str, float]] = None,
            cam_light_primary: Optional[Dict[str, Any]] = None,
            width: int = 320, height: int = 180) -> Host:
    cam = camera or {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
                     "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
    cfg = HostConfig(
        screen_w=width, screen_h=height,
        camera_mode=camera_mode, camera=cam,
        bridge=True, inject_test_hooks=bool(cam_light_primary or edit_camera),
    )
    h = Host(load_script(), layers, config=cfg, shader_lib=shader_library(),
             params=params, native=native(), shared_global=bus)
    if edit_camera is not None:
        h.lua.globals().VL3D_TEST_EDIT_CAM = h.lua.table_from(edit_camera)
    if cam_light_primary is not None:
        u_lv = float(cam_light_primary.get("unlit", 0.0))
        pk_lv = u_lv + float(cam_light_primary.get("lum", 1.0))
        h.lua.globals().VL3D_TEST_CAM_LIGHTS = h.lua.table_from({
            "count": 1, "unlit": u_lv,
            "amb_r": 0.0, "amb_g": 0.0, "amb_b": 0.0, "peak_gain": max(1.0, pk_lv),
            "suppressed": False,
            "primary": cam_light_primary,
        })
    h.layer_index = layer_index
    h.scene_id = scene_id
    h.frame_s = frame_s
    h.run_frame(frame, time_s=(frame_s + frame) / 30.0)
    require(not h.errors, f"VolumetricLight3D host errors: {h.errors}")
    return h


def _run_snow(layers: Sequence[Layer], params: Dict[str, Any], bus: Dict[str, bytes], *,
              layer_index: int = 5, scene_id: int = 0, frame: int = 0, frame_s: int = 0,
              camera: Optional[Dict[str, float]] = None, camera_mode: int = 1,
              disable_figure: bool = False,
              obj_xform: Optional[Dict[str, float]] = None,
              width: int = 320, height: int = 180) -> Host:
    cam = camera or {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
                     "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
    cfg = HostConfig(
        screen_w=width, screen_h=height,
        camera_mode=camera_mode, camera=cam,
        bridge=False, disable_figure=disable_figure,
    )
    h = Host(snow_script(), layers, config=cfg, shader_lib=snow_shaders(),
             params=params, shared_global=bus)
    h.layer_index = layer_index
    h.scene_id = scene_id
    h.frame_s = frame_s
    if obj_xform:
        h.obj_xform = {k: float(v) for k, v in obj_xform.items()}
    h.run_frame(frame, time_s=(frame_s + frame) / 30.0)
    require(not h.errors, f"Snow3D host errors: {h.errors}")
    return h


def _write_box_obj(path: Path, x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    verts = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    faces = [
        (1, 2, 3), (1, 3, 4),
        (5, 7, 6), (5, 8, 7),
        (1, 5, 6), (1, 6, 2),
        (4, 3, 7), (4, 7, 8),
        (1, 4, 8), (1, 8, 5),
        (2, 6, 7), (2, 7, 3),
    ]
    lines = ["# box mesh"]
    for vx, vy, vz in verts:
        lines.append(f"v {vx:.3f} {vy:.3f} {vz:.3f}")
    for a, b, c in faces:
        lines.append(f"f {a} {b} {c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _quad_center(q: dict) -> Tuple[float, float, float]:
    vs = q["verts"]
    n = len(vs)
    return (
        sum(v[0] for v in vs) / n,
        sum(v[1] for v in vs) / n,
        sum(v[2] for v in vs) / n,
    )


def _quad_tint_rgb(q: dict) -> Tuple[float, float, float]:
    atlas = q.get("atlas")
    if atlas is None:
        return (0.0, 0.0, 0.0)
    rgb = atlas[..., :3].astype(np.float32)
    pk = np.max(rgb, axis=-1)
    mask = pk > 24.0
    if not np.any(mask):
        return (0.0, 0.0, 0.0)
    denom = np.maximum(pk[mask], 1e-4)
    r = rgb[..., 0][mask] / denom
    g = rgb[..., 1][mask] / denom
    b = rgb[..., 2][mask] / denom
    return (float(np.mean(r)), float(np.mean(g)), float(np.mean(b)))


@check("lightfield: Snow3D v1.23 static spec, HLSL compile, API lint & 4 .aul2 packs", tags=["static", "lightfield"])
def snow3d_static_and_i18n():
    require(SNOW_OBJ2.is_file(), f"missing {SNOW_OBJ2}")
    raw = SNOW_OBJ2.read_bytes()
    require(b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""),
            "Snow3D .obj2 must use CRLF line endings")

    s = snow_script()
    require(s.information == "雪（3D）v1.23", f"unexpected Snow3D banner: {s.information!r}")
    groups = [g.name for g in s.groups if g.params]
    require(groups == ["雪", "運動", "領域", "寿命", "描画", "体積光連動"],
            f"unexpected Snow3D groups: {groups}")
    require(len(s.params) == 44, f"expected 44 Snow3D params, got {len(s.params)}")

    declared = {p.name for p in s.params}
    for tgt, expr in s.hide_rules:
        require(tgt in declared, f"unknown hide target {tgt}")
        require(expr == "vl_enable==0", f"unexpected hide rule {tgt}:{expr}")

    # Compile both embedded Snow3D pixel shaders via C++17 transpiler
    sh = snow_shaders()
    require(set(s.shaders.keys()) == {"ps_snow_shape_blur", "ps_snow_shape_tint"},
            f"unexpected Snow3D shaders: {list(s.shaders.keys())}")
    require(sh is not None, "Snow3D shader library failed to build")

    # Official AviUtl2 API lint on Snow3D
    rep = api_lint.lint(s.text, shader_names=set(s.shaders.keys()))
    require(not rep.issues, f"Snow3D API lint issues: {rep.issues}")

    # Verify all 4 .aul2 language packs cover every group, param, and tooltip
    aul_files = sorted(SNOW_LANG_DIR.glob("*.雪（3D）.aul2"))
    require(len(aul_files) == 4, f"expected 4 Snow3D .aul2 files, got {len(aul_files)}")
    jp_re = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
    for af in aul_files:
        ab = af.read_bytes()
        require(b"\r\n" in ab and b"\n" not in ab.replace(b"\r\n", b""), f"{af.name}: expected CRLF")
        pack = _parse_aul2(af)
        sec = pack.get("雪（3D）", {})
        tips = pack.get("Tips.雪（3D）", {})
        for gname in groups:
            require(gname in sec and sec[gname].strip(), f"{af.name}: missing group {gname}")
            if "简体中文" not in af.name:
                require(not jp_re.search(sec[gname]), f"{af.name}: untranslated kana in {gname}={sec[gname]!r}")
        for p in s.params:
            require(p.label in sec and sec[p.label].strip(), f"{af.name}: missing label {p.label}")
            require(p.label in tips and tips[p.label].strip(), f"{af.name}: missing tip {p.label}")
            if "简体中文" not in af.name:
                require(not jp_re.search(sec[p.label]), f"{af.name}: untranslated kana in {p.label}={sec[p.label]!r}")

    return {"params": len(s.params), "shaders": list(s.shaders.keys()), "aul2_packs": len(aul_files)}


@check("lightfield: standalone Snow3D v1.23 is bit-identical to uncoupled mode when no beam exists", tags=["runtime", "lightfield"])
def snow3d_standalone_noop():
    bus: Dict[str, bytes] = {}
    base_params = {"count": 120, "soft": 40, "follow": False, "world_fixed": False}
    h_on = _run_snow([], {**base_params, "vl_enable": True, "vl_shade": 45, "vl_strength": 150}, bus, frame=15)
    h_off = _run_snow([], {**base_params, "vl_enable": False, "vl_shade": 45, "vl_strength": 150}, bus, frame=15)

    require(len(h_on.quads) == len(h_off.quads) and len(h_on.quads) > 0,
            f"quad count mismatch: {len(h_on.quads)} vs {len(h_off.quads)}")
    require(all(q["blend"] == "none" for q in h_on.quads), "unexpected additive pass without emitter")
    for qa, qb in zip(h_on.quads, h_off.quads):
        require(abs(qa["alpha"] - qb["alpha"]) < 1e-7, f"alpha drift without emitter: {qa['alpha']} vs {qb['alpha']}")
    require(h_on.peak_cache_pixels < 1_000_000, f"unexpected cache usage: {h_on.peak_cache_pixels}")
    return {"quads": len(h_on.quads), "peak_cache_px": h_on.peak_cache_pixels}


@check("lightfield: beam illuminates inside snow, leaves outside snow unlit, and dims ambient", tags=["runtime", "lightfield"])
def beam_illuminates_inside_only():
    bus: Dict[str, bytes] = {}
    # Tight 140x140 bright warm window centered at (0, 0, 600), shooting parallel along -Z toward camera (-1024)
    src = _solid_layer(1, 140, 140, (255, 210, 120, 255), x=0.0, y=0.0, z=600.0)
    _run_vl(
        [src],
        {
            "src_layer": 1, "ray_mode": 0, "screen_dir": False,
            "direction": 0.0, "pitch": 90.0, "spread": 0.0, # pure -Z world ray (from z=600 toward z=-600)
            "length": 1200.0, "decay": 20.0, "intensity": 140.0, "threshold": 10.0,
            "occ_mode": 0,
        },
        bus, layer_index=3, frame=10,
    )
    require("VL3D_LF:0:3" in bus, "VolumetricLight3D did not publish slot VL3D_LF:0:3")
    raw_slot = bus["VL3D_LF:0:3"]
    require(raw_slot.startswith(b"VL3DB64:"), f"expected ASCII Base64 prefix, got {raw_slot[:12]!r}")
    require(b"\x00" not in raw_slot and all(32 <= b <= 126 for b in raw_slot),
            "published global string must be 100% printable 7-bit ASCII (NUL-safe across C++ c_str bridges)")
    blob = base64.b64decode(raw_slot[8:])
    magic, sid, lay, sframe, _rid, kind, flags, rw, sw = struct.unpack_from("<8s8i", blob, 0)
    require(magic == b"VL3DLF\x03\x00" and sid == 0 and lay == 3 and sframe == 10 and rw == 48,
            f"unexpected header: magic={magic!r} sid={sid} lay={lay} frame={sframe} rw={rw}")

    # Wide snow volume (1200x800x800 centered at 0,0,100) so some flakes are inside |x|<=70,|y|<=70 and most are outside
    snow_p = {
        "count": 320, "psize": 10.0, "soft": 25,
        "area_w": 1200.0, "area_h": 800.0, "area_d": 700.0,
        "area_x": 0.0, "area_y": 0.0, "area_z": 100.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 150.0, "vl_tint": 90.0, "vl_shade": 40.0,
    }
    h_snow = _run_snow([src], snow_p, bus, layer_index=5, frame=10)
    add_quads = [q for q in h_snow.quads if q["blend"] == "add"]
    base_quads = [q for q in h_snow.quads if q["blend"] != "add"]
    require(len(add_quads) >= 5, f"expected >=5 lit snowflakes inside shaft, got {len(add_quads)}")
    require(len(add_quads) < len(base_quads) * 0.35,
            f"too many lit snowflakes ({len(add_quads)}/{len(base_quads)}); outside-beam flakes leaked")

    for q in add_quads:
        cx, cy, cz = _quad_center(q)
        require(abs(cx) <= 92.0 and abs(cy) <= 92.0 and -610.0 <= cz <= 605.0,
                f"lit snowflake outside 3D beam shaft at ({cx:.1f}, {cy:.1f}, {cz:.1f})")

    # Disabling vl_enable removes all additive quads immediately
    h_disabled = _run_snow([src], {**snow_p, "vl_enable": False}, bus, layer_index=5, frame=10)
    require(not any(q["blend"] == "add" for q in h_disabled.quads),
            "vl_enable=False still emitted additive lit quads")
    return {"base_flakes": len(base_quads), "lit_flakes": len(add_quads), "payload_bytes": len(blob)}


@check("lightfield: TRUE 3D diagonal cast shadow extinguishes downstream snow (3D .obj & 2D silhouette)", tags=["runtime", "lightfield"])
def true_3d_cast_shadow_3d_and_2d():
    """Camera at (0,0,-1024) looks along +Z.
    Light window at (-400, 0, 600) shoots diagonally along U = (+0.7071, 0, -0.7071) (direction=0, pitch=45, spread=0):
      - Upstream snow box at (-220, 0, 420) (before blocker along ray): MUST BE LIT!
      - Blocker sits at (0, 0, 200) (screen center x = 0).
      - Downstream snow box at (+240, 0, -40) (after blocker along ray):
        Even though x=+240 is completely UNOCCLUDED from the camera's 2D view
        (blocker is at x=0!), it lies inside the 3D cast-shadow tunnel and MUST NOT BE LIT!
    """
    src = _solid_layer(1, 240, 240, (255, 240, 180, 255), x=-400.0, y=0.0, z=600.0)

    obj_path = assets.write_test_obj(
        REPO_ROOT / "sandbox" / "out" / "lf_blocker_box.obj",
        110.0, scene_units=True,
    )
    blocker_3d = Layer(
        index=2, kind="obj",
        model_path=str(obj_path),
        effects={"モデルファイル": str(obj_path)},
        x=0.0, y=0.0, z=200.0, zoom=100.0, alpha=1.0,
    )

    vl_base = {
        "src_layer": 1, "ray_mode": 0, "screen_dir": False,
        "direction": 0.0, "pitch": 45.0, "spread": 0.0,
        "length": 1300.0, "decay": 8.0, "intensity": 170.0, "threshold": 10.0,
        "occ_target": 1, "occ_strength": 100.0, "occ_soft": 3.0, "occ_3d_mode": 0,
    }

    snow_upstream = {
        "count": 80, "psize": 10.0, "soft": 20,
        "sway": 0.0, "spread": 0.0, "wind_x": 0.0,
        "area_w": 70.0, "area_h": 100.0, "area_d": 70.0,
        "area_x": -220.0, "area_y": 0.0, "area_z": 420.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 170.0, "vl_shade": 50.0,
    }
    snow_downstream = {
        **snow_upstream,
        "area_x": 240.0, "area_z": -40.0,
    }

    # 1) Unoccluded baseline (occ_mode = 0): BOTH upstream and downstream boxes are brightly lit
    bus_no_occ: Dict[str, bytes] = {}
    _run_vl([src, blocker_3d], {**vl_base, "occ_mode": 0}, bus_no_occ, layer_index=3, frame=5)
    up_unocc = [q for q in _run_snow([src, blocker_3d], snow_upstream, bus_no_occ, frame=5).quads if q["blend"] == "add"]
    dn_unocc = [q for q in _run_snow([src, blocker_3d], snow_downstream, bus_no_occ, frame=5).quads if q["blend"] == "add"]
    require(len(up_unocc) >= 20 and len(dn_unocc) >= 20,
            f"baseline unoccluded shaft failed to light both boxes: up={len(up_unocc)}, down={len(dn_unocc)}")

    # 2) 3D .obj Cast Shadow (occ_mode = 1): upstream STAYS LIT, downstream drops to ZERO!
    bus_3d: Dict[str, bytes] = {}
    _run_vl([src, blocker_3d], {**vl_base, "occ_mode": 1}, bus_3d, layer_index=3, frame=5)
    up_3d = [q for q in _run_snow([src, blocker_3d], snow_upstream, bus_3d, frame=5).quads if q["blend"] == "add"]
    dn_3d = [q for q in _run_snow([src, blocker_3d], snow_downstream, bus_3d, frame=5).quads if q["blend"] == "add"]
    require(len(up_3d) >= 20, f"upstream snow before 3D blocker should remain lit, got {len(up_3d)}")
    require(len(dn_3d) == 0, f"downstream snow inside 3D .obj cast shadow must be unlit, got {len(dn_3d)} lit quads")

    # 3) 2D Silhouette Cast Shadow (occ_mode = 1, 2D plane at (0, 0, 200)):
    blocker_2d = _solid_layer(2, 260, 280, (255, 255, 255, 255), x=0.0, y=0.0, z=200.0)
    bus_2d: Dict[str, bytes] = {}
    _run_vl([src, blocker_2d], {**vl_base, "occ_mode": 1}, bus_2d, layer_index=3, frame=5)
    up_2d = [q for q in _run_snow([src, blocker_2d], snow_upstream, bus_2d, frame=5).quads if q["blend"] == "add"]
    dn_2d = [q for q in _run_snow([src, blocker_2d], snow_downstream, bus_2d, frame=5).quads if q["blend"] == "add"]
    require(len(up_2d) >= 20, f"upstream snow before 2D blocker should remain lit, got {len(up_2d)}")
    require(len(dn_2d) == 0, f"downstream snow inside 2D cast shadow must be unlit, got {len(dn_2d)} lit quads")

    return {
        "unoccluded_up_down": (len(up_unocc), len(dn_unocc)),
        "occ3d_up_down": (len(up_3d), len(dn_3d)),
        "occ2d_up_down": (len(up_2d), len(dn_2d)),
    }


@check("lightfield: foreground holdout (occ_mode=2) masks snow behind character while keeping front snow lit", tags=["runtime", "lightfield"])
def camera_foreground_holdout():
    # Light window at z=700 shining toward camera (-Z), foreground character at z=150 (220x220 centered on screen)
    # Wait: under occ_mode=2 ("foreground mask only", no cast shadow!), light rays are NOT shadow-cast,
    # ONLY camera foreground mask applies: snow behind z=150 (at z=420) is masked, snow in front of z=150 (at z=-120) glows!
    src = _solid_layer(1, 280, 280, (140, 220, 255, 255), x=0.0, y=0.0, z=700.0)
    fg_char = _solid_layer(2, 260, 260, (40, 40, 50, 255), x=0.0, y=0.0, z=150.0)

    bus: Dict[str, bytes] = {}
    _run_vl(
        [src, fg_char],
        {
            "src_layer": 1, "ray_mode": 0, "screen_dir": False,
            "direction": 0.0, "pitch": 90.0, "spread": 0.0,
            "length": 1100.0, "decay": 10.0, "intensity": 160.0, "threshold": 10.0,
            "occ_mode": 2, "occ_target": 1, "occ_strength": 100.0,
        },
        bus, layer_index=3, frame=8,
    )

    snow_common = {
        "count": 70, "psize": 10.0, "soft": 20,
        "sway": 0.0, "spread": 0.0, "wind_x": 0.0,
        "area_w": 80.0, "area_h": 80.0, "area_d": 80.0,
        "area_x": 0.0, "area_y": 0.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 160.0, "vl_shade": 20.0,
    }
    behind_q = [q for q in _run_snow([src, fg_char], {**snow_common, "area_z": 420.0}, bus, frame=8).quads if q["blend"] == "add"]
    front_q  = [q for q in _run_snow([src, fg_char], {**snow_common, "area_z": -120.0}, bus, frame=8).quads if q["blend"] == "add"]

    require(len(behind_q) == 0, f"snow behind foreground character (z=420 > z_fg=150) should be masked, got {len(behind_q)}")
    require(len(front_q) >= 20, f"snow in front of character (z=-120 < z_fg=150) should glow, got {len(front_q)}")
    return {"behind_lit": len(behind_q), "front_lit": len(front_q)}


@check("lightfield: stained-glass multi-color panes, mullion dark gap & CPU fallback triangle mode", tags=["runtime", "lightfield"])
def stained_glass_and_cpu_fallback():
    # 240x160 stained glass at z=500:
    #   x in [0..74]:  Ruby Red (255, 25, 25)
    #   x in [75..164]: Dark opaque lead mullion (5, 5, 5) -> below threshold=25!
    #   x in [165..239]: Sapphire Blue (25, 80, 255)
    glass = np.zeros((160, 240, 4), dtype=np.uint8)
    glass[:, :75] = (255, 25, 25, 255)
    glass[:, 75:165] = (5, 5, 5, 255)
    glass[:, 165:] = (25, 80, 255, 255)
    src = Layer(index=1, image=glass, x=0.0, y=0.0, z=500.0)

    bus: Dict[str, bytes] = {}
    _run_vl(
        [src],
        {
            "src_layer": 1, "ray_mode": 0, "screen_dir": False,
            "direction": 0.0, "pitch": 90.0, "spread": 0.0,
            "length": 900.0, "decay": 15.0, "intensity": 170.0, "threshold": 25.0,
            "occ_mode": 0,
        },
        bus, layer_index=3, frame=12,
    )

    snow_box = {
        "count": 60, "psize": 10.0, "soft": 20,
        "sway": 0.0, "spread": 0.0, "wind_x": 0.0,
        "area_w": 24.0, "area_h": 90.0, "area_d": 120.0,
        "area_y": 0.0, "area_z": 200.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 170.0, "vl_tint": 100.0, "vl_shade": 30.0,
    }
    # Left pane center x = -85, Mullion gap center x = 0, Right pane center x = +85
    q_red  = [q for q in _run_snow([src], {**snow_box, "area_x": -85.0}, bus, frame=12).quads if q["blend"] == "add"]
    q_gap  = [q for q in _run_snow([src], {**snow_box, "area_x":   0.0, "area_w": 16.0}, bus, frame=12).quads if q["blend"] == "add"]
    q_blue = [q for q in _run_snow([src], {**snow_box, "area_x":  85.0}, bus, frame=12).quads if q["blend"] == "add"]

    require(len(q_red) >= 15 and len(q_blue) >= 15, f"expected lit red & blue panes, got {len(q_red)}, {len(q_blue)}")
    require(len(q_gap) == 0, f"snow behind dark stained-glass mullion should be unlit, got {len(q_gap)}")

    rr, rg, rb = _quad_tint_rgb(q_red[0])
    br, bg, bb = _quad_tint_rgb(q_blue[0])
    require(rr > 0.75 and rr > rb * 3.0, f"expected ruby-red snow tint, got ({rr:.2f},{rg:.2f},{rb:.2f})")
    require(bb > 0.75 and bb > br * 3.0, f"expected sapphire-blue snow tint, got ({br:.2f},{bg:.2f},{bb:.2f})")

    # Also verify CPU fallback circle mode (disable_figure=True, default white snow)
    h_cpu_red  = _run_snow([src], {**snow_box, "area_x": -85.0}, bus, frame=12, disable_figure=True)
    h_cpu_gap  = _run_snow([src], {**snow_box, "area_x":   0.0, "area_w": 16.0}, bus, frame=12, disable_figure=True)
    mean_r_lit = float(np.mean([q["verts"][0][3] for q in h_cpu_red.quads]))
    mean_b_lit = float(np.mean([q["verts"][0][5] for q in h_cpu_red.quads]))
    mean_r_gap = float(np.mean([q["verts"][0][3] for q in h_cpu_gap.quads]))
    require(mean_r_lit > mean_r_gap * 1.6 and mean_r_lit > mean_b_lit * 2.5,
            f"CPU fallback mode failed to tint/illuminate red shaft: lit=({mean_r_lit:.3f},{mean_b_lit:.3f}) gap={mean_r_gap:.3f}")

    return {"red_rgb": (round(rr, 3), round(rg, 3), round(rb, 3)),
            "blue_rgb": (round(br, 3), round(bg, 3), round(bb, 3)),
            "gap_lit": len(q_gap)}


@check("lightfield: diverging point-cone (ray_mode=1) & converging beam (ray_mode=2) + 3D fog", tags=["runtime", "lightfield"])
def point_diverging_and_converging_modes():
    # Window at z=500 (120x120 -> +/-60)
    src = _solid_layer(1, 120, 120, (255, 230, 160, 255), x=0.0, y=0.0, z=500.0)

    # 1) Diverging point source 300 behind window (light_z = 300 -> world z = 800):
    #    At z = -100 (distance 900 from S, 3x the window distance 300), cone half-width expands 3x from +/-60 to +/-180!
    #    So snow at x = +130, z = -100 is OUTSIDE +/-60 parallel shaft, YET INSIDE diverging cone!
    bus_div: Dict[str, bytes] = {}
    _run_vl(
        [src],
        {"src_layer": 1, "ray_mode": 1, "light_x": 0.0, "light_y": 0.0, "light_z": 300.0,
         "length": 1100.0, "decay": 10.0, "intensity": 170.0, "fog": 65.0, "occ_mode": 0},
        bus_div, layer_index=3, frame=6,
    )
    snow_flank = {
        "count": 60, "psize": 10.0, "soft": 20,
        "sway": 0.0, "spread": 0.0, "wind_x": 0.0,
        "area_w": 36.0, "area_h": 50.0, "area_d": 50.0,
        "area_x": 130.0, "area_y": 0.0, "area_z": -100.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 170.0,
    }
    q_div_in  = [q for q in _run_snow([src], snow_flank, bus_div, frame=6).quads if q["blend"] == "add"]
    q_div_out = [q for q in _run_snow([src], {**snow_flank, "area_x": 320.0}, bus_div, frame=6).quads if q["blend"] == "add"]
    require(len(q_div_in) >= 15 and len(q_div_out) == 0,
            f"diverging ray_mode=1 cone geometry mismatch: flank_in={len(q_div_in)}, far_out={len(q_div_out)}")

    # 2) Converging beam (ray_mode=2): window (240x240 -> +/-120 at z=600) converges 500 in front (light_z = 500 -> world z = 100):
    #    At z = 200 (20% from focus z=100 to window z=600), beam narrows to +/-24!
    src_wide = _solid_layer(1, 240, 240, (180, 240, 255, 255), x=0.0, y=0.0, z=600.0)
    bus_conv: Dict[str, bytes] = {}
    _run_vl(
        [src_wide],
        {"src_layer": 1, "ray_mode": 2, "light_x": 0.0, "light_y": 0.0, "light_z": 500.0,
         "length": 900.0, "decay": 10.0, "intensity": 160.0, "occ_mode": 0},
        bus_conv, layer_index=3, frame=6,
    )
    q_conv_core  = [q for q in _run_snow([src_wide], {**snow_flank, "area_x": 0.0, "area_z": 200.0, "area_w": 20.0}, bus_conv, frame=6).quads if q["blend"] == "add"]
    q_conv_flank = [q for q in _run_snow([src_wide], {**snow_flank, "area_x": 90.0, "area_z": 200.0, "area_w": 20.0}, bus_conv, frame=6).quads if q["blend"] == "add"]
    require(len(q_conv_core) >= 15 and len(q_conv_flank) == 0,
            f"converging ray_mode=2 cone geometry mismatch: core={len(q_conv_core)}, outside_pinch={len(q_conv_flank)}")

    return {"diverging_in_out": (len(q_div_in), len(q_div_out)),
            "converging_core_flank": (len(q_conv_core), len(q_conv_flank))}


@check("lightfield: Snow3D object rotation, multi-emitter superposition, spotlight, scene isolation & withdrawal", tags=["runtime", "lightfield"])
def transforms_multilight_isolation_and_withdrawal():
    # 1) Snow3D rotated rz = 90 deg: local (+200, 0, 200) rotates to world (0, +200, 200)!
    # Put narrow beam at world (0, +200, 500) shooting -Z.
    src_y = _solid_layer(1, 90, 90, (255, 180, 60, 255), x=0.0, y=200.0, z=500.0)
    bus: Dict[str, bytes] = {}
    _run_vl(
        [src_y],
        {"src_layer": 1, "ray_mode": 0, "screen_dir": False, "direction": 0.0, "pitch": 90.0, "spread": 0.0,
         "length": 700.0, "decay": 15.0, "intensity": 160.0, "occ_mode": 0},
        bus, layer_index=3, frame=4,
    )
    rot_snow_p = {
        "count": 50, "psize": 10.0, "soft": 20,
        "area_w": 50.0, "area_h": 50.0, "area_d": 80.0,
        "area_x": 200.0, "area_y": 0.0, "area_z": 250.0, # local +X=200 -> world +Y=200 under rz=90!
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 160.0,
    }
    q_unrot = [q for q in _run_snow([src_y], rot_snow_p, bus, frame=4).quads if q["blend"] == "add"]
    q_rot90 = [q for q in _run_snow([src_y], rot_snow_p, bus, frame=4, obj_xform={"rz": 90.0}).quads if q["blend"] == "add"]
    require(len(q_unrot) == 0 and len(q_rot90) >= 15,
            f"Snow3D object rotation rz=90 world mapping failed: unrot={len(q_unrot)}, rot90={len(q_rot90)}")

    # 2) Multi-emitter superposition: add a second VolumetricLight3D on Layer 4 at world (0, -200, 500)
    src_y2 = _solid_layer(2, 90, 90, (60, 220, 255, 255), x=0.0, y=-200.0, z=500.0)
    _run_vl(
        [src_y, src_y2],
        {"src_layer": 2, "ray_mode": 0, "screen_dir": False, "direction": 0.0, "pitch": 90.0, "spread": 0.0,
         "length": 700.0, "decay": 15.0, "intensity": 160.0, "occ_mode": 0},
        bus, layer_index=4, frame=4,
    )
    require(bus.get("VL3D_LF_IDX:0") == b"3,4", f"expected scene 0 index '3,4', got {bus.get('VL3D_LF_IDX:0')!r}")
    wide_snow = {
        "count": 200, "psize": 10.0, "soft": 20,
        "area_w": 80.0, "area_h": 560.0, "area_d": 120.0,
        "area_x": 0.0, "area_y": 0.0, "area_z": 250.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 160.0, "vl_tint": 100.0,
    }
    q_dual = [q for q in _run_snow([src_y, src_y2], wide_snow, bus, frame=4).quads if q["blend"] == "add"]
    pos_y = [q for q in q_dual if _quad_center(q)[1] > 120.0]
    neg_y = [q for q in q_dual if _quad_center(q)[1] < -120.0]
    require(len(pos_y) >= 5 and len(neg_y) >= 5,
            f"multi-emitter dual beams failed: +Y={len(pos_y)}, -Y={len(neg_y)}")

    # 3) Sub-scene isolation: Snow3D in scene_id=1 must NOT see scene_id=0's emitters!
    q_subscene = [q for q in _run_snow([src_y, src_y2], wide_snow, bus, scene_id=1, frame=4).quads if q["blend"] == "add"]
    require(len(q_subscene) == 0, f"scene_id=0 light leaked into sub-scene scene_id=1 ({len(q_subscene)} quads)")

    # 4) Staleness guard: seeking Snow3D to frame=20 without re-evaluating VL ignores frame=4 slots
    q_stale = [q for q in _run_snow([src_y, src_y2], wide_snow, bus, scene_id=0, frame=20).quads if q["blend"] == "add"]
    require(len(q_stale) == 0, f"stale light-field slots from frame=4 leaked onto frame=20 ({len(q_stale)} quads)")

    # 5) Immediate withdrawal on intensity=0 short-circuit:
    _run_vl([src_y, src_y2], {"src_layer": 1, "intensity": 0.0}, bus, layer_index=3, frame=4)
    _run_vl([src_y, src_y2], {"src_layer": 2, "intensity": 0.0}, bus, layer_index=4, frame=4)
    require(bus.get("VL3D_LF_IDX:0", b"") == b"", f"withdrawn index not empty: {bus.get('VL3D_LF_IDX:0')!r}")
    q_withdrawn = [q for q in _run_snow([src_y, src_y2], wide_snow, bus, frame=4).quads if q["blend"] == "add"]
    require(len(q_withdrawn) == 0, "withdrawn emitters still lit snowflakes")

    # 6) Camera 3D virtual spotlight + non-self-occlusion of Snow3D layer:
    bus_sp: Dict[str, bytes] = {}
    snow_layer_stub = _solid_layer(2, 64, 64, (255, 255, 255, 255), x=0.0, y=0.0, z=150.0, fx_name="雪（3D）")
    cam_spot = {
        "ltype": 5, "r": 1.0, "g": 0.9, "b": 0.7, "lum": 1.4,
        "px": 0.0, "py": 0.0, "pz": 600.0,
        "dx": 0.0, "dy": 0.0, "dz": 1.0,
        "cos_ang": math.cos(math.radians(28.0)),
    }
    h_vl_sp = _run_vl(
        [snow_layer_stub],
        {"src_layer": 0, "cam_light_mode": 1, "length": 900.0, "intensity": 150.0,
         "occ_mode": 3, "occ_target": 1, "log_mode": True},
        bus_sp, layer_index=3, frame=1, cam_light_primary=cam_spot,
    )
    log_joined = "\n".join(h_vl_sp.prints)
    require("L2" not in log_joined, f"Snow3D layer L2 was mistakenly captured as a 2D occluder: {log_joined}")
    q_spot = [q for q in _run_snow([], {**rot_snow_p, "area_x": 0.0, "area_z": 200.0}, bus_sp, frame=1).quads if q["blend"] == "add"]
    require(len(q_spot) >= 15, f"camera 3D spotlight failed to illuminate snowflakes, got {len(q_spot)}")

    return {
        "rot90_lit": len(q_rot90),
        "dual_pos_neg": (len(pos_y), len(neg_y)),
        "spot_lit": len(q_spot),
    }


@check("lightfield: 3D spotlight snow eliminates WBOIT dark soft-edge halo & backlit normal blackout", tags=["runtime", "lightfield"])
def spotlight_3d_wboit_soft_edge_and_backlit_normals():
    """Reproduces the live AviUtl2 v2.1.16 3D spotlight regression:
    1. Foreground unlit soft-edged snow overlapping a cyan 3D spotlight beam on a dark
       background must NOT hijack Shader #118 WBOIT Accum_w into a dark circular halo.
    2. Backlit snow inside the unoccluded 3D spotlight cone must emit 8-tuple normals
       facing the 3D light so host lighting H_host(wp, N) illuminates it even at unlit=0.
    3. Snow inside a 3D .obj cast shadow within the spotlight cone must emit light-opposing
       normals (max(0, N.L) = 0) and zero tinted glow quads so host spotlight cannot leak.
    4. `light_color` tinting on 2D sources must remain linear (no lc_rgb^2 double-multiply).
    """
    from vl3dsb.compositor3d import composite_scene_3d

    cam = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
           "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
    W, H = 240, 180

    # 1) Cyan 3D spotlight at z=+650 shooting toward camera (-Z) with length=820 (reaches down to z=-170).
    #    Foreground snow at (0, 0, -480) is OUTSIDE the beam reach (unlit dim gray, unlit=0.26)
    #    while projecting directly over the bright cyan shaft center on screen!
    cam_spot = {
        "ltype": 5, "r": 0.0, "g": 1.0, "b": 1.0, "lum": 1.35, "unlit": 0.26,
        "px": 0.0, "py": 0.0, "pz": 650.0,
        "dx": 0.0, "dy": 0.0, "dz": 1.0,
        "cos_ang": math.cos(math.radians(24.0)),
    }
    bus_fg: Dict[str, bytes] = {}
    h_vl = _run_vl(
        [],
        {"src_layer": 0, "cam_light_mode": 1, "length": 820.0, "decay": 10.0,
         "intensity": 200.0, "occ_mode": 0},
        bus_fg, layer_index=2, frame=3, camera=cam, cam_light_primary=cam_spot, width=W, height=H,
    )
    raw_fg = base64.b64decode(bus_fg["VL3D_LF:0:2"][8:])
    flags_fg = struct.unpack_from("<8s8i", raw_fg, 0)[6]
    require((flags_fg & (128 | 256 | 512)) == (128 | 256 | 512),
            f"expected has_cam_3d_light(128)+cam_light_is_pos(256)+has_ext_cam(512) in flags={flags_fg}")

    fg_snow_p = {
        "count": 1, "psize": 46.0, "size_var": 0.0, "soft": 100,
        "vel_x": 0.0, "vel_y": 0.0, "vel_z": 0.0, "gravity": 0.0,
        "sway": 0.0, "spread": 0.0, "spin": 0.0,
        "fade_in": 0.0, "fade_out": 0.0, "depth_fade": 0.0,
        "area_w": 10.0, "area_h": 10.0, "area_d": 10.0,
        "area_x": 0.0, "area_y": 0.0, "area_z": -480.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 140.0, "vl_shade": 25.0,
    }
    h_snow_fg = _run_snow([], fg_snow_p, bus_fg, frame=3, camera=cam, width=W, height=H)
    require(len(h_snow_fg.quads) == 1, f"expected 1 unlit foreground WBOIT snow quad, got {len(h_snow_fg.quads)}")
    require(len(h_snow_fg.quads[0]["verts"][0]) == 8,
            f"expected 3D spotlight snow base quad to use 8-tuple normals, got {len(h_snow_fg.quads[0]['verts'][0])}")

    img_beam = composite_scene_3d(W, H, cam, [], h_vl.quads, camera_light=cam_spot)
    img_fixed = composite_scene_3d(W, H, cam, [], list(h_vl.quads) + list(h_snow_fg.quads), camera_light=cam_spot)

    # Control: simulate pre-fix Snow3D (`vl_enable = False` -> raw Gaussian soft skirt in Shader #118 WBOIT)
    h_snow_legacy = _run_snow([], {**fg_snow_p, "vl_enable": False}, bus_fg, frame=3, camera=cam, width=W, height=H)
    img_legacy = composite_scene_3d(W, H, cam, [], list(h_vl.quads) + list(h_snow_legacy.quads), camera_light=cam_spot)

    legacy_drop_g = float(np.min(img_legacy[..., 1] - img_beam[..., 1]))
    fixed_drop_g = float(np.min(img_fixed[..., 1] - img_beam[..., 1]))
    fixed_drop_b = float(np.min(img_fixed[..., 2] - img_beam[..., 2]))
    center_boost_r = float(img_fixed[H // 2, W // 2, 0] - img_beam[H // 2, W // 2, 0])
    require(legacy_drop_g < -0.04,
            f"expected legacy WBOIT control to reproduce dark soft-edge halo (< -0.04), got {legacy_drop_g:.4f}")
    require(fixed_drop_g >= -0.008 and fixed_drop_b >= -0.008,
            f"foreground snow darkened 3D spotlight beam: dG={fixed_drop_g:.5f}, dB={fixed_drop_b:.5f}")
    require(center_boost_r > 0.02,
            f"foreground unlit snow core disappeared completely over beam: dR={center_boost_r:.4f}")

    # 2) Backlit lit snow at (0, 0, +250) inside spotlight cone with unlit=0.0:
    cam_spot_zero = {**cam_spot, "unlit": 0.0}
    bus_bk: Dict[str, bytes] = {}
    h_vl_bk = _run_vl(
        [],
        {"src_layer": 0, "cam_light_mode": 1, "length": 900.0, "decay": 8.0,
         "intensity": 180.0, "occ_mode": 0},
        bus_bk, layer_index=2, frame=3, camera=cam, cam_light_primary=cam_spot_zero, width=W, height=H,
    )
    bk_snow_p = {**fg_snow_p, "psize": 34.0, "area_z": 250.0, "vl_strength": 160.0, "vl_tint": 90.0}
    h_snow_bk = _run_snow([], bk_snow_p, bus_bk, frame=3, camera=cam, width=W, height=H)
    require(len(h_snow_bk.quads) >= 2, f"expected base + tinted glow quads for backlit lit snow, got {len(h_snow_bk.quads)}")
    for q in h_snow_bk.quads:
        vn = q["verts"][0][5:8]
        require(vn[2] > 0.99, f"backlit lit snow normal must face +Z spotlight (got {vn})")
    img_beam_bk = composite_scene_3d(W, H, cam, [], h_vl_bk.quads, camera_light=cam_spot_zero)
    img_snow_bk = composite_scene_3d(W, H, cam, [], list(h_vl_bk.quads) + list(h_snow_bk.quads), camera_light=cam_spot_zero)
    backlit_gain_g = float(np.max(img_snow_bk[..., 1] - img_beam_bk[..., 1]))
    backlit_drop_g = float(np.min(img_snow_bk[..., 1] - img_beam_bk[..., 1]))
    require(backlit_gain_g > 0.12,
            f"backlit snow inside 3D spotlight cone failed to illuminate at unlit=0: dG={backlit_gain_g:.4f}")
    require(backlit_drop_g >= -0.008,
            f"backlit lit snow created dark ring over 3D spotlight beam: dG={backlit_drop_g:.5f}")

    # 3) 3D .obj cast shadow inside spotlight cone:
    #    - At unlit=0.0 (100% spotlight brightness, zero ambient), occluded beam-overlapping snow is culled (0 black hole)
    #    - At unlit=0.35, occluded snow emits 1 WBOIT base quad with -N_light and 0 glow quads
    blocker_path = assets.write_test_obj(
        REPO_ROOT / "sandbox" / "out" / "lf_spot_blocker.obj",
        130.0, scene_units=True,
    )
    blocker_3d = Layer(
        index=1, kind="obj",
        model_path=str(blocker_path),
        effects={"モデルファイル": str(blocker_path)},
        x=0.0, y=0.0, z=430.0, zoom=100.0, alpha=1.0,
    )
    bus_occ0: Dict[str, bytes] = {}
    _run_vl(
        [blocker_3d],
        {"src_layer": 0, "cam_light_mode": 1, "length": 900.0, "decay": 8.0,
         "intensity": 180.0, "occ_mode": 3, "occ_target": 1, "occ_strength": 100.0},
        bus_occ0, layer_index=2, frame=3, camera=cam, cam_light_primary=cam_spot_zero, width=W, height=H,
    )
    h_snow_occ0 = _run_snow([blocker_3d], {**bk_snow_p, "area_z": 180.0}, bus_occ0, frame=3, camera=cam, width=W, height=H)
    require(len(h_snow_occ0.quads) == 0,
            f"unlit=0 occluded snow overlapping spotlight must be culled to prevent black silhouette, got {len(h_snow_occ0.quads)}")

    bus_occ: Dict[str, bytes] = {}
    _run_vl(
        [blocker_3d],
        {"src_layer": 0, "cam_light_mode": 1, "length": 900.0, "decay": 8.0,
         "intensity": 180.0, "occ_mode": 3, "occ_target": 1, "occ_strength": 100.0},
        bus_occ, layer_index=2, frame=3, camera=cam, cam_light_primary=cam_spot, width=W, height=H,
    )
    h_snow_occ = _run_snow([blocker_3d], {**bk_snow_p, "area_z": 180.0}, bus_occ, frame=3, camera=cam, width=W, height=H)
    require(len(h_snow_occ.quads) == 1,
            f"occluded snow inside 3D spotlight shadow (unlit=0.35) must emit 1 base + 0 glow quads, got {len(h_snow_occ.quads)}")
    vn_occ = h_snow_occ.quads[0]["verts"][0][5:8]
    require(vn_occ[2] < -0.99,
            f"occluded snow inside 3D spotlight shadow must oppose spotlight normal (got {vn_occ})")

    # 4) Verify 2D `light_color` tinting is linear (not squared `lc_rgb^2`):
    white_src = _solid_layer(1, 160, 160, (255, 255, 255, 255), x=0.0, y=0.0, z=500.0)
    bus_lc: Dict[str, bytes] = {}
    _run_vl(
        [white_src],
        {"src_layer": 1, "ray_mode": 0, "pitch": 90.0, "spread": 0.0,
         "light_color": 0x80ff40, "intensity": 100.0, "occ_mode": 0},
        bus_lc, layer_index=3, frame=1,
    )
    blob_lc = base64.b64decode(bus_lc["VL3D_LF:0:3"][8:])
    flags_lc = struct.unpack_from("<8s8i", blob_lc, 0)[6]
    hdr_lc = 320 if (flags_lc & 512) else 264
    mid_off = hdr_lc + (24 * 48 + 24) * 4
    r_b, g_b, b_b, _a_b = blob_lc[mid_off:mid_off + 4]
    ratio_rg = r_b / max(1.0, float(g_b))
    require(0.44 <= ratio_rg <= 0.56,
            f"expected linear light_color R/G ~ 0.502 (0x80/0xff), got {ratio_rg:.3f} (RGB={r_b},{g_b},{b_b})")

    return {
        "legacy_drop_g": round(legacy_drop_g, 4),
        "fixed_drop_g": round(fixed_drop_g, 5),
        "backlit_gain_g": round(backlit_gain_g, 4),
        "lc_ratio_rg": round(ratio_rg, 3),
    }


@check("lightfield: Edit Camera (camera_mode==2) live basis sync, fading snow & IDLE/WAIT log", tags=["e2e", "lightfield"])
def lightfield_edit_camera_and_status():
    from vl3dsb.compositor3d import composite_scene_3d

    W, H = 220, 160
    # Timeline camera sits straight ahead at (0, 0, -1024), while user orbits Edit Camera
    # (`camera_mode == 2`, `視点切り替え`) 90 degrees to (+1024, 0, 0) looking at (0, 0, 0)!
    tl_cam = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
              "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
    edit_cam = {"x": 1024.0, "y": 0.0, "z": 0.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
                "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}
    cam_spot = {
        "ltype": 5, "r": 0.0, "g": 1.0, "b": 1.0, "lum": 1.6, "unlit": 0.25,
        "px": -420.0, "py": 0.0, "pz": 0.0,
        "dx": -1.0, "dy": 0.0, "dz": 0.0,
        "cos_ang": math.cos(math.radians(24.0)),
    }

    bus: Dict[str, bytes] = {}
    # VL3D evaluates with the active Edit Camera (via VL3DBridge / VL3D_TEST_EDIT_CAM):
    h_vl = _run_vl(
        [],
        {"src_layer": 0, "cam_light_mode": 1, "length": 900.0, "decay": 8.0,
         "intensity": 180.0, "occ_mode": 0},
        bus, layer_index=2, frame=4, camera=tl_cam, edit_camera=edit_cam, camera_mode=2,
        cam_light_primary=cam_spot, width=W, height=H,
    )

    # Snow3D receives stale `tl_cam` from `obj.getoption("camera_param")` when `camera_mode == 2`,
    # and must sync its billboard basis & WBOIT ray probe to `edit_cam` from VL3DLF's 320B header!
    fg_edit_snow = {
        "count": 1, "psize": 48.0, "size_var": 0.0, "soft": 80,
        "vel_x": 0.0, "vel_y": 0.0, "vel_z": 0.0, "gravity": 0.0,
        "sway": 0.0, "spread": 0.0, "spin": 0.0,
        "fade_in": 0.0, "fade_out": 0.0, "depth_fade": 0.0,
        "area_w": 6.0, "area_h": 6.0, "area_d": 6.0,
        "area_x": 620.0, "area_y": 0.0, "area_z": 0.0,
        "follow": False, "world_fixed": False,
        "vl_enable": True, "vl_strength": 150.0, "vl_shade": 20.0, "log_mode": True,
    }
    h_snow_edit = _run_snow([], fg_edit_snow, bus, frame=4, camera=tl_cam, camera_mode=2, width=W, height=H)
    require(len(h_snow_edit.quads) == 1, f"expected 1 foreground quad in Edit Camera mode, got {len(h_snow_edit.quads)}")
    vs = h_snow_edit.quads[0]["verts"]
    span_x = max(v[0] for v in vs) - min(v[0] for v in vs)
    span_z = max(v[2] for v in vs) - min(v[2] for v in vs)
    require(span_z > 25.0 and span_x < 1.0,
            f"Edit Camera billboard failed to face +X camera (span_x={span_x:.2f}, span_z={span_z:.2f})")

    img_beam_ed = composite_scene_3d(W, H, edit_cam, [], h_vl.quads, camera_light=cam_spot)
    img_snow_ed = composite_scene_3d(W, H, edit_cam, [], list(h_vl.quads) + list(h_snow_edit.quads), camera_light=cam_spot)
    drop_ed = float(np.min(img_snow_ed[..., 1] - img_beam_ed[..., 1]))
    require(drop_ed >= -0.008,
            f"Edit Camera foreground snow left dark ring over 3D spotlight beam: dG={drop_ed:.5f}")

    # Fading particle (`obj.alpha = 0.04`) overlapping beam must skip Pass 1b instead of punching a dark hole:
    h_snow_fade = _run_snow(
        [], fg_edit_snow, bus,
        frame=4, camera=tl_cam, camera_mode=2, obj_xform={"alpha": 0.04}, width=W, height=H,
    )
    require(len(h_snow_fade.quads) == 0,
            f"fading 4% alpha snow over 3D beam must cull cleanly, got {len(h_snow_fade.quads)} quads")

    # Verify WAIT (bus empty) vs IDLE (VL3D withdrew with intensity=0) vs OFF status in log:
    bus_wait: Dict[str, bytes] = {}
    h_wait = _run_snow([], {**fg_edit_snow, "log_mode": True}, bus_wait, frame=1, camera=tl_cam, width=W, height=H)
    require(any("WAIT" in m for m in h_wait.prints), f"expected WAIT in Snow3D log when bus uninitialized: {h_wait.prints}")

    bus_idle: Dict[str, bytes] = {}
    _run_vl([], {"src_layer": 0, "cam_light_mode": 1, "intensity": 0.0}, bus_idle,
            layer_index=2, frame=1, camera=tl_cam, cam_light_primary=cam_spot, width=W, height=H)
    h_idle = _run_snow([], {**fg_edit_snow, "log_mode": True}, bus_idle, frame=1, camera=tl_cam, width=W, height=H)
    require(any("IDLE" in m for m in h_idle.prints), f"expected IDLE in Snow3D log when VL3D intensity=0: {h_idle.prints}")

    return {"edit_cam_span_z": round(span_z, 2), "edit_cam_drop_g": round(drop_ed, 5)}

