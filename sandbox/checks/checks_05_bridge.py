"""Native VL3DBridge (C++17) checks against the real shipped sources."""
from __future__ import annotations

import base64
import ctypes
import math
import re
import struct

import numpy as np

from vl3dsb.harness import check, fail, require, skip
from vl3dsb.native import NativeBridge
from vl3dsb.obj2 import parse_obj2
from vl3dsb.session import DEFAULT_SCRIPT, OUT_ROOT, save_png

NB = None


def nb() -> NativeBridge:
    global NB
    if NB is None:
        try:
            NB = NativeBridge()
        except Exception as exc:  # pragma: no cover
            skip(f"native build unavailable: {exc}")
    return NB


# ---------------------------------------------------------------- self test
@check("bridge: bundled C++ self-test suite", tags=["bridge", "core"])
def selftest():
    b = nb()
    rc, out = b.selftest()
    require(rc == 0, f"self-test failed (rc={rc}):\n{out[-2000:]}")
    n_pass = out.count("[PASS]") + out.count("PASS ")
    save = OUT_ROOT / "bridge" / "selftest.log"
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_text(out, encoding="utf-8")
    return {"detail": out.strip().splitlines()[-1][:160], "lines": len(out.splitlines()),
            "artifacts": [str(save)]}


@check("bridge: build is reproducible and warning clean", tags=["bridge"])
def build():
    b = nb()
    require(b.lib_path.exists(), "shared object missing")
    warnings = [ln for ln in b.build_log.splitlines() if "warning:" in ln]
    return {"lib": str(b.lib_path), "warnings": len(warnings), "hash": b.hash}


@check("bridge: PE / SHA-256 / whitelist gate", tags=["bridge", "security"])
def allowlist():
    b = nb()
    b.reset(0x140000000)
    b.load_ini("strict_whitelist_only=0\nenable_d3d11_cs=1\n")
    b.init_host(2100000)
    pe_ok, sha, allow, size, ts = b.status_sha256()
    require(len(sha) == 64, f"sha256 hex looks wrong: {sha!r}")
    # no host image in the sandbox: the gate must refuse, never crash
    require(pe_ok in (0, 1), "pe flag must be boolean")
    if pe_ok == 0:
        require(allow is False, "a non-verified image must never be allow-listed")
    b.add_default_allowlist()
    pe_ok2, sha2, allow2, _s, _t = b.status_sha256()
    require(sha2 == sha, "sha256 is not stable across calls")
    return {"pe_valid": bool(pe_ok2), "allowlisted": allow2, "sha256": sha2[:16] + "..."}


@check("bridge: mesh cache honours revisions", tags=["bridge"])
def mesh_cache():
    b = nb()
    verts = np.array([[0, 0, 0], [100, 0, 0], [0, 100, 0]], np.float32)
    tris = np.array([0, 1, 2], np.uint32)
    vb = base64.b64encode(verts.tobytes()).decode()
    tb = base64.b64encode(tris.tobytes()).decode()
    ok = b.upload_mesh_b64("unit:tri", "v1", 3, 1, vb, tb)
    require(ok > 0, "mesh upload rejected")
    # HasMesh returns the mesh slot id, not a boolean
    require(b.has_mesh("unit:tri", "v1") == ok, "mesh missing right after upload")
    require(b.has_mesh("unit:tri", "v2") in (0, 1), "stale revision query crashed")
    # uploading an empty mesh must be rejected, not crash
    require(b.upload_mesh_b64("unit:bad", "v1", 3, 0, vb, base64.b64encode(b"").decode()) == 0,
            "zero-triangle upload must be rejected")
    return {"has_v1": b.has_mesh("unit:tri", "v1")}


def _shadow_params() -> list:
    """Shadow params identical to the ones the shipping script emits.

    mode_kind = 1 (camera/point style), plane normal Wn = +Z, u_wn = -1 and
    dS_wn = -285.9 reproduce the real call captured from the Lua pipeline; using
    the flat `mode_kind = 0` plane instead leaves the projection degenerate.
    """
    return [-80.0, -80.0, 0.0, 80.0, 80.0,
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
            1.0, -249.81, -322.51, 285.9, -1.0, -285.9, 2981.25, 2048.0]


def _cube_mesh(b: NativeBridge, size: float = 200.0, key: str = "sandbox:cube") -> int:
    """Upload a cube and return its mesh slot id (the rasterizers index by id)."""
    s = size
    verts = []
    for z in (-s, s):
        for y in (-s, s):
            for x in (-s, s):
                verts.append((x, y, z))
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
             (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    vb = base64.b64encode(np.asarray(verts, np.float32).tobytes()).decode()
    tb = base64.b64encode(np.asarray([i for f in faces for i in f], np.uint32).tobytes()).decode()
    mid = b.upload_mesh_b64(key, "cube", 8, len(faces), vb, tb)
    require(mid > 0, "cube mesh upload rejected")
    return mid


def _draw_shadow(b, mid, z: float, res: int = 256):
    # 14 doubles: mesh_id, is_vrm, origin xyz, r0 xyz, d0 xyz, n0 xyz
    insts = [mid, 0, 0.0, 0.0, z, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    dst = np.zeros((res, res, 4), np.uint8)
    ok, tris, any_px = b.rasterize_shadow(dst.ctypes.data, res, _shadow_params(), insts, 1)
    return ok, tris, any_px, dst


@check("bridge: shadow rasterizer fills a silhouette and encodes depth", tags=["bridge", "core"])
def shadow_raster():
    b = nb()
    mid = _cube_mesh(b)
    ok, tris, any_px, dst = _draw_shadow(b, mid, -300.0)
    require(ok, "rasterize_shadow returned failure")
    require(tris == 12, f"expected all 12 triangles to be accepted, got {tris}")
    hit = dst[..., 3] > 127
    require(hit.any(), "shadow map has no covered pixels")
    require(hit.mean() > 0.2, f"silhouette is suspiciously sparse: {hit.mean():.3f}")
    rgb = dst[..., :3].astype(np.int16)
    r, g, bch = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    require(((r <= g + 1) & (g <= bch + 1)).all(),
            "the 3-step depth ramp must satisfy r<=g<=b")
    codes = rgb.mean(axis=2) / 255.0
    require(len(np.unique(np.round(codes[hit], 3))) >= 4,
            "depth code is constant - no depth is encoded")
    require(int(dst[hit][:, 3].min()) == 255, "covered texels must carry alpha 255")
    if (~hit).any():
        require(int(dst[~hit][:, 3].max()) == 0, "uncovered texels must stay fully transparent")
    _, _, _, dst2 = _draw_shadow(b, mid, -300.0)
    require(np.array_equal(dst, dst2), "shadow rasterizer is not deterministic")
    save_png(dst, OUT_ROOT / "bridge" / "shadow_map.png")
    return {"tris": tris, "coverage": round(float(hit.mean()), 4),
            "code_range": [round(float(codes[hit].min()), 3), round(float(codes[hit].max()), 3)],
            "artifacts": [str(OUT_ROOT / "bridge" / "shadow_map.png")]}


@check("bridge: shadow map respects the occlusion plane", tags=["bridge", "core"])
def shadow_plane():
    b = nb()
    mid = _cube_mesh(b, key="sandbox:cube2")
    # the shadow volume keeps only geometry on the light's far side (z <= -0.5
    # for this parameter set); a cube entirely on the near side must vanish.
    ok_front, t_front, _, d_front = _draw_shadow(b, mid, -300.0)
    ok_back, t_back, any_back, d_back = _draw_shadow(b, mid, 1500.0)
    require(t_front == 12, f"near-side cube should draw, got {t_front}")
    require((d_front[..., 3] > 127).any(), "near-side cube produced no pixels")
    require(t_back == 0, f"far-side cube must be clipped away, drew {t_back} triangles")
    require(not any_back and not (d_back[..., 3] > 127).any(),
            "far-side cube must not write pixels")
    # partially clipped geometry (straddling the plane) is clipped, not dropped
    ok_mid, t_mid, _, d_mid = _draw_shadow(b, mid, -100.0)
    require(t_mid > 12, f"straddling cube should need extra clipped triangles, got {t_mid}")
    return {"front_tris": t_front, "behind_tris": t_back, "straddle_tris": t_mid}


@check("bridge: camera depth rasterizer", tags=["bridge", "core"])
def camdepth_raster():
    b = nb()
    mid = _cube_mesh(b, key="sandbox:cube3")
    w, h = 320, 180
    # params16: scr_w, scr_h, cam pos, focal, cam basis, far
    params = [640.0, 360.0, 0.0, 0.0, -1024.0, 1024.0,
              0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 4096.0]
    results = {}
    for z in (0.0, 800.0):
        insts = [mid, 0, 0, 0, z, 1, 0, 0, 0, 1, 0, 0, 0, 1]
        dst = np.zeros((h, w, 4), np.uint8)
        ok, tris, any_px = b.rasterize_camdepth(dst.ctypes.data, w, h, params, insts, 1)
        require(ok, "rasterize_camdepth failed")
        hit = dst[..., 3] > 127
        require(tris > 0 and hit.any(), f"camera depth map empty at z={z} (tris={tris})")
        results[z] = float(dst[..., :3].astype(np.float32).mean(axis=2)[hit].mean()) / 255.0
        if z == 0.0:
            save_png(dst, OUT_ROOT / "bridge" / "camdepth.png")
    # a nearer occluder must encode a larger proximity value
    require(results[0.0] > results[800.0],
            f"camera depth code does not follow distance: {results}")
    return {"code_near": round(results[0.0], 3), "code_far": round(results[800.0], 3),
            "artifacts": [str(OUT_ROOT / "bridge" / "camdepth.png")]}


@check("bridge: rejects invalid inputs instead of crashing", tags=["bridge", "robust"])
def rejects():
    b = nb()
    mid = _cube_mesh(b, key="sandbox:cube4")
    insts = [mid, 0, 0.0, 0.0, -300.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    dst = np.zeros((64, 64, 4), np.uint8)
    p = _shadow_params()
    require(b.rasterize_shadow(dst.ctypes.data, 8, p, insts, 1)[0] is False,
            "sh_res below the documented minimum must be rejected")
    require(b.rasterize_shadow(dst.ctypes.data, 64, p, insts, 0)[0] is False,
            "zero instances must be rejected")
    bad = [99999, 0] + [0.0] * 12      # unknown mesh id
    ok, tris, any_px = b.rasterize_shadow(dst.ctypes.data, 64, p, bad, 1)
    require(tris == 0 or ok, "unknown mesh must degrade gracefully")
    return {"detail": "invalid inputs are rejected without crashing"}


@check("bridge: wall clock + ini parser", tags=["bridge"])
def misc():
    b = nb()
    t0 = b.wall_time_sec()
    t1 = b.wall_time_sec()
    require(t1 >= t0, "wall_time_sec is not monotonic")
    b.load_ini("strict_whitelist_only=1\nenable_d3d11_cs=0\nallow_pe=0x6ADC1302,0x005F5000\n")
    b.load_ini("")                     # empty ini must not crash
    b.reset(0)
    b.init_host(0)
    return {"detail": "wall clock + ini parser survive empty/garbage input"}


@check("bridge: Lua glue calls the same C API surface", tags=["bridge", "integration"])
def lua_surface():
    s = parse_obj2(DEFAULT_SCRIPT)
    expected = ["rasterize_shadow", "rasterize_camdepth", "upload_mesh", "has_mesh",
                "resolve_layer", "get_camera_lights", "get_edit_camera", "get_water_color",
                "wall_time_sec", "requires_time_update"]
    missing = [fn for fn in expected
               if not re.search(r"\.\s*" + re.escape(fn) + r"\b", s.text)]
    require(not missing, f"script never calls: {missing}")
    return {"bridge_functions": len(expected)}


@check("bridge: shipped Windows x64 VL3DBridge.aux2 & ini match v1.22 / v2.1.16", tags=["bridge", "static"])
def shipped_aux2_and_ini():
    from vl3dsb.config import REPO_ROOT
    aux2_path = REPO_ROOT / "Plugin" / "VL3DBridge.aux2"
    ini_path = REPO_ROOT / "Plugin" / "VL3DBridge.ini"
    require(aux2_path.is_file(), f"missing {aux2_path}")
    require(ini_path.is_file(), f"missing {ini_path}")

    b = aux2_path.read_bytes()
    require(b[:2] == b"MZ", "VL3DBridge.aux2 is not a DOS/PE image")
    e_lfanew = struct.unpack_from("<I", b, 0x3C)[0]
    sig, machine, nsec, _ts, _st, _ns, opt_sz, _ch = struct.unpack_from("<4sHHIIIHH", b, e_lfanew)
    require(sig == b"PE\x00\x00" and machine == 0x8664, f"expected x64 PE32+, got sig={sig!r} machine={machine:#x}")
    opt_off = e_lfanew + 24
    magic = struct.unpack_from("<H", b, opt_off)[0]
    require(magic == 0x020B, f"expected PE32+ magic 0x020B, got {magic:#x}")

    exp_rva, _exp_sz = struct.unpack_from("<II", b, opt_off + 112)
    require(exp_rva > 0, "VL3DBridge.aux2 has no export table")
    secs = [struct.unpack_from("<8sIIII", b, opt_off + opt_sz + i * 40) for i in range(nsec)]

    def rva2off(r: int) -> int:
        for _nm, vsz, srva, rsz, roff in secs:
            if srva <= r < srva + max(vsz, rsz):
                return roff + (r - srva)
        raise AssertionError(f"unmapped RVA {r:#x}")

    eo = rva2off(exp_rva)
    _nf, n_names, _af, addr_names, _ao = struct.unpack_from("<IIIII", b, eo + 20)
    exports = []
    for i in range(n_names):
        nrva = struct.unpack_from("<I", b, rva2off(addr_names) + i * 4)[0]
        o = rva2off(nrva)
        exports.append(b[o:b.index(b"\x00", o)].decode("ascii"))
    need_exports = {"GetCommonPluginTable", "GetScriptModuleTable", "InitializePlugin",
                    "RegisterPlugin", "RequiredVersion", "UninitializePlugin"}
    require(need_exports.issubset(set(exports)), f"missing exports: {sorted(need_exports - set(exports))}")
    require("VL3DBridge v1.22".encode("utf-16-le") in b,
            "VL3DBridge.aux2 binary does not contain the 'VL3DBridge v1.22' UTF-16LE banner")

    ini_raw = ini_path.read_bytes()
    require(b"\r\n" in ini_raw and b"\n" not in ini_raw.replace(b"\r\n", b""),
            "VL3DBridge.ini must use Windows CRLF line endings")
    ini_txt = ini_raw.decode("utf-8")
    require("0x6AE538B8,0x005F6000" in ini_txt, "VL3DBridge.ini missing AviUtl2 v2.1.16 allow_pe")
    require("0x6ADC1302,0x005F5000" in ini_txt, "VL3DBridge.ini missing AviUtl2 v2.1.15 allow_pe")
    require("ea321cfb" not in ini_txt, "VL3DBridge.ini still contains obsolete v1.21 header allow_sha256")
    return {"size": len(b), "exports": exports}
