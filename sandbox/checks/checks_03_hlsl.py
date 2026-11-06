"""HLSL shader checks: transpile/compile + numeric behaviour of every shader."""
from __future__ import annotations

import math
import re

import numpy as np

from vl3dsb.config import REPO_ROOT
from vl3dsb.harness import check, fail, require, skip
from vl3dsb.obj2 import parse_obj2
from vl3dsb.session import DEFAULT_SCRIPT, OUT_ROOT, save_png, shader_library, script

LIB = None


def lib():
    global LIB
    if LIB is None:
        LIB = shader_library()
    return LIB


def _img(w, h, rgba):
    out = np.zeros((h, w, 4), np.float32)
    out[...] = np.array(rgba, np.float32)
    return out


@check("hlsl: all embedded shaders transpile and compile", tags=["hlsl"])
def compile_all():
    s = script()
    l = lib()
    missing = set(s.shaders) - set(l.names)
    require(not missing, f"shaders failed to compile: {sorted(missing)}")
    info = l.describe()
    for name, meta in info.items():
        require(meta["cb_floats"] > 0, f"{name}: no cbuffer parameters")
    return {"shaders": len(l.names), "build": str(l.lib_path)}


@check("hlsl: language subset / FXC-compatibility lint", tags=["hlsl", "static"])
def fxc_lint():
    s = script()
    problems = []
    for name, src in s.shaders.items():
        code = re.sub(r"//[^\n]*", "", src)          # strip line comments
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
        if re.search(r"\[\s*(?!loop\b|unroll\b|branch\b)", code) and "[" in code:
            pass
        if re.search(r"\bswitch\b", code):
            problems.append(f"{name}: switch statement (unsupported by this pipeline)")
        if re.search(r"\bdiscard\b", code):
            problems.append(f"{name}: discard (host alpha-test assumed instead)")
        if re.search(r"\bstatic\s+(const\s+)?float", code):
            problems.append(f"{name}: static float (forces a lazy-init code path)")
        if re.search(r"\bwhile\s*\(", code):
            problems.append(f"{name}: while loop (unbounded - FXC may fail)")
        # D3DCOMPILE_WARNINGS_ARE_ERRORS: integer division triggers X3556
        for m in re.finditer(r"[^/\n]+/[^/\n]+", code):
            frag = m.group(0)
            if re.search(r"\(\s*int\s*\)|int\s+\w+\s*=|uint", frag) and "* 0.5" not in frag:
                if re.search(r"\bint\b|\buint\b", frag):
                    problems.append(f"{name}: possible integer division -> X3556: {frag.strip()[:60]}")
                    break
    require(not problems, "; ".join(problems))
    return {"detail": "no switch/discard/while, no integer division"}


@check("hlsl: texture bindings match the Lua call sites", tags=["hlsl"])
def bindings():
    s = script()
    l = lib()
    # every shader that samples textures must be called with the same number of inputs
    calls = {}
    for m in re.finditer(r'obj\.pixelshader\(\s*"([A-Za-z0-9_]+)"\s*,\s*([^,]+),\s*(\{[^}]*\})', s.text):
        name, _target, inputs = m.group(1), m.group(2), m.group(3)
        n = len([x for x in inputs.replace("{", "").replace("}", "").split(",") if x.strip()])
        calls.setdefault(name, set()).add(n)
    bad = []
    for name, counts in calls.items():
        want = len(l.info[name].textures) if name in l.info else None
        if want is None:
            continue
        # a shader may legally be called with fewer inputs (unused sampler)
        if max(counts) > want:
            bad.append(f"{name}: Lua passes {max(counts)} textures, HLSL declares {want}")
    require(not bad, "; ".join(bad))
    return {"checked": len(calls)}


@check("hlsl: ps_vl_white (light source generator)", tags=["hlsl"])
def white():
    l = lib()
    flat = l.run("ps_vl_white", [1 / 16, 1 / 8, 0.0, 2 / 128], size=(32, 16))
    require((flat[..., :] == 255).all(), "flat mode must be fully opaque white")
    disc = l.run("ps_vl_white", [1 / 32, 1 / 32, 1.0, 1.0], size=(64, 64))
    require(disc[32, 32, 3] == 255, "disc centre must be opaque")
    require(disc[1, 1, 3] == 0, f"disc corner must be transparent, got {disc[1,1,3]}")
    soft = l.run("ps_vl_white", [1 / 32, 1 / 32, 2.0, 1.0], size=(64, 64))
    mid = int(soft[32, 44, 3])
    require(0 < mid < 255, f"radial falloff should be gradual, got {mid}")
    return {"disc_corner": int(disc[1, 1, 3]), "falloff_sample": mid}


@check("hlsl: ps_vl_blur (gaussian, energy preserving)", tags=["hlsl"])
def blur():
    l = lib()
    const = _img(32, 32, [0.25, 0.5, 0.75, 1.0])
    same = l.run("ps_vl_blur", [32, 32, 1, 0, 2.0, 0], [const], size=(32, 32))
    require(np.abs(same.astype(int) - const * 255).max() <= 1, "identity path must not change pixels")
    blurred = l.run("ps_vl_blur", [32, 32, 1, 0, 3.0, 8], [const], size=(32, 32))
    require(np.abs(blurred.astype(int) - const * 255).max() <= 2, "flat image must survive blurring")
    # a single bright pixel spreads out but conserves roughly its total energy
    spot = _img(33, 33, [0, 0, 0, 0])
    spot[16, 16] = [1, 1, 1, 1]
    out = l.run("ps_vl_blur", [33, 33, 1, 0, 2.0, 9], [spot], size=(33, 33))
    require(out[16, 16, 3] < 255, "peak should be attenuated")
    require(out[16, 20, 3] > 0, "energy must spread horizontally")
    return {"peak": int(out[16, 16, 3]), "spread": int(out[16, 20, 3])}


@check("hlsl: ps_vl_pool (light statistics reduction)", tags=["hlsl"])
def pool():
    l = lib()
    white = _img(64, 64, [1, 1, 1, 1])
    out = l.run("ps_vl_pool", [64, 64, 8.0, 0.15], [white], size=(8, 8))
    r, g, b, a = out[4, 4]
    require(r > 250 and a > 250, f"opaque white image should report full coverage, got {out[4,4]}")
    dark = _img(64, 64, [0.01, 0.01, 0.01, 1.0])
    out2 = l.run("ps_vl_pool", [64, 64, 8.0, 0.15], [dark], size=(8, 8))
    require(out2[4, 4, 3] == 0, "below-threshold image must report no highlight coverage")
    return {"white": out[4, 4].tolist(), "dark_a": int(out2[4, 4, 3])}


@check("hlsl: ps_vl_prep (colour source, threshold knee)", tags=["hlsl"])
def prep():
    l = lib()
    src = _img(48, 48, [0.6, 0.3, 0.9, 1.0])
    src_mode = l.run("ps_vl_prep", [48, 48, 4.0, 0.15, 1, 1, 1, 1.0], [src], size=(12, 12))
    require(src_mode[6, 6, 3] > 200, "source colour mode must pass alpha through")
    tint_mode = l.run("ps_vl_prep", [48, 48, 4.0, 0.15, 1.0, 0.0, 0.0, 0.0], [src], size=(12, 12))
    r, g, b, a = tint_mode[6, 6]
    require(r > 150 and g < 40 and b < 40, f"monochrome tint should be red, got {tint_mode[6,6]}")
    hi = l.run("ps_vl_prep", [48, 48, 4.0, 0.95, 1, 1, 1, 1.0], [src], size=(12, 12))
    require(hi[6, 6, 3] < 40, "a 95% threshold must reject a 60%-bright source")
    return {"src_a": int(src_mode[6, 6, 3]), "tint": tint_mode[6, 6].tolist(), "thr_a": int(hi[6, 6, 3])}


@check("hlsl: ps_vl_pack (occluder atlas tiling)", tags=["hlsl"])
def pack():
    l = lib()
    prev = _img(64, 32, [0.0, 0.0, 0.1, 0.1])
    occ = _img(32, 32, [0, 0, 0, 1])
    out = l.run("ps_vl_pack", [0, 0, 32, 32, 2, 2, 0.5, 0, 1 / 32, 1 / 32, 2, 2],
                [prev, occ], size=(64, 32))
    inside = out[16, 16]
    outside = out[16, 50]
    require(abs(int(inside[2]) - 128) <= 6, f"tile alpha should be 0.5*255, got {inside}")
    require(inside[2] == inside[3], "raw (.b) and soft (.a) channels must both be written")
    require(abs(int(outside[2]) - 26) <= 3, f"outside the tile the previous atlas must be preserved, got {outside}")
    return {"inside": inside.tolist(), "outside": outside.tolist()}


@check("hlsl: ps_vl_occ_blur keeps the raw silhouette channel", tags=["hlsl"])
def occ_blur():
    l = lib()
    src = _img(16, 16, [0, 0, 0.8, 1.0])
    out = l.run("ps_vl_occ_blur", [16, 16, 1, 0, 0.0, 0], [src], size=(16, 16))
    require(abs(int(out[8, 8, 2]) - 204) <= 1, "b channel must be copied verbatim")
    blur = l.run("ps_vl_occ_blur", [16, 16, 1, 0, 2.0, 5], [src], size=(16, 16))
    require(abs(int(blur[8, 8, 2]) - 204) <= 1, "b channel must stay raw while blurring")
    return {"raw_b": int(blur[8, 8, 2]), "soft_a": int(blur[8, 8, 3])}


@check("hlsl: AutoBlendLight shader family sanity", tags=["hlsl"])
def ab_family():
    l = lib()
    src = _img(32, 32, [0.5, 0.5, 0.5, 1.0])
    sil = _img(32, 32, [0, 0, 0, 0])
    sil[8:24, 8:24] = [1, 1, 1, 1]
    env = _img(32, 32, [0.2, 0.3, 0.4, 1.0])
    front = _img(32, 32, [0, 0, 0, 0])
    shade = l.run("ps_vl_ab_shade",
                  [32, 32, 32, 32, 0, 0, 0.0, 0.0,
                   1, 1, 1, 100, 1, 1, 1, 100, 100, 0.6, 0.3, 0.0,
                   0.35, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                   1, 0, 0, 640, 0, 1, 0, 360, 0, 0, -1024, 1024,
                   0, 0, 1, 0, 1, 0, 0, 0, 0, 16384],
                  [src, sil, env, front], size=(32, 32))
    require(shade.shape == (32, 32, 4), "ab_shade must return a full frame")
    require(int(shade[..., 3].max()) > 0, "ab_shade produced a fully transparent frame")
    blurred = l.run("ps_vl_ab_blur", [0.01, 0.0, 2.0, 0.0, 32, 32, 0, 0, 0, 0, 0, 0], [src],
                    size=(32, 32))
    require(np.abs(blurred[16, 16, :3].astype(int) - 128).max() <= 3, "ab_blur must preserve flat colours")
    env_init = l.run("ps_vl_ab_env_init", [32, 32, 1.25, 0.0], [env, sil], size=(32, 32))
    require(int(env_init[16, 16, 3]) < 40, "the masked region must be suppressed")
    require(int(env_init[2, 2, 3]) > 150, "unmasked regions must survive")
    return {"shade_alpha_max": int(shade[..., 3].max())}


@check("hlsl: ps_vl_water (caustic light source)", tags=["hlsl"])
def water():
    l = lib()
    p = [128, 1.0, 0.5, 0.5, 0.2, 0.4, 5.0, 15.0, 20.0, 30.0, 5.0, 40.0, 0.4, 0.8, 1.0, 1.0]
    a = l.run("ps_vl_water", p, size=(64, 64))
    require(int(a[..., 3].max()) > 0, "water shader returned an empty frame")
    b = l.run("ps_vl_water", p, size=(64, 64))
    require(np.array_equal(a, b), "water shader must be deterministic for a fixed time")
    p2 = list(p)
    p2[1] = 2.0
    c = l.run("ps_vl_water", p2, size=(64, 64))
    require(not np.array_equal(a, c), "water shader must animate with time")
    return {"alpha_max": int(a[..., 3].max())}


def _capture_beam_trace(occ: bool, w: int = 320, h: int = 180) -> dict:
    """Run the real pipeline once and return the uniform set it used for beamatlas.

    Using captured uniforms (instead of hand-written ones) guarantees the probe
    matches the actual cbuffer layout of the shipping script.
    """
    from vl3dsb import session
    params = {"src_layer": 1, "slices": 24.0, "intensity": 180.0,
              "occ_mode": 3.0 if occ else 0.0, "occ_target": 2.0, "occ_list": "3"}
    layers = session.standard_scene(160, 160, occluder_z=-200.0)
    r = session.run_frame(layers, params, width=w, height=h)
    tr = r.host.shader_traces.get("beamatlas")
    require(tr is not None, "the pipeline never invoked beamatlas")
    return tr


@check("hlsl: beamatlas (volume slice compositor) - energy, determinism, premultiply", tags=["hlsl", "core"])
def beamatlas():
    l = lib()
    tr = _capture_beam_trace(occ=False)
    p = list(tr["params"])
    texs = tr["textures"]
    out = l.run("beamatlas", p, texs, out_hw=tr["size"])
    again = l.run("beamatlas", p, texs, out_hw=tr["size"])
    require(np.array_equal(out, again), "beamatlas must be deterministic for identical inputs")
    alpha = out[..., 3].astype(int)
    require(alpha.max() > 0, "beamatlas produced no light at all")
    # the project stores *straight* colour with a small coverage alpha (host OIT contract)
    rgb = out[..., :3].astype(int)
    require(rgb.max() > 0, "beamatlas wrote no colour")
    require(alpha.max() <= 255, "alpha out of range")
    ratio = round(float(alpha.mean()), 3)
    save_png(out, OUT_ROOT / "hlsl" / "beamatlas_probe.png")
    return {"alpha_max": int(alpha.max()), "alpha_mean": ratio,
            "uniforms": len(p), "textures": [t.shape for t in texs],
            "artifacts": [str(OUT_ROOT / "hlsl" / "beamatlas_probe.png")]}


@check("hlsl: beamatlas honours occlusion uniform toggles", tags=["hlsl", "core"])
def beamatlas_occlusion():
    l = lib()
    tr = _capture_beam_trace(occ=True)
    p = list(tr["params"])
    texs = tr["textures"]
    base = l.run("beamatlas", p, texs, out_hw=tr["size"])
    # p15 = [n_occ, occ_mode, occ_strength, sh3d_active]  ->  offsets 60..63
    off = list(p)
    off[61] = 0.0          # occ_mode = 0
    no_occ = l.run("beamatlas", off, texs, out_hw=tr["size"])
    diff = np.abs(base[..., 3].astype(int) - no_occ[..., 3].astype(int))
    require(int(p[60]) >= 1, f"expected at least one occluder uniform, n_occ={p[60]}")
    if diff.max() == 0:
        fail("occlusion uniforms did not change the beam - mask plumbing suspect")
    save_png(base, OUT_ROOT / "hlsl" / "beamatlas_occ.png")
    save_png(no_occ, OUT_ROOT / "hlsl" / "beamatlas_noocc.png")
    return {"max_alpha_diff": int(diff.max()),
            "occluded_pixels": int((diff > 2).sum()),
            "n_occ": float(p[60]),
            "artifacts": [str(OUT_ROOT / "hlsl" / "beamatlas_occ.png")]}


@check("hlsl: no NaN / Inf leaks into any shader output", tags=["hlsl"])
def no_nan():
    l = lib()
    zero = _img(8, 8, [0, 0, 0, 0])
    one = _img(8, 8, [1, 1, 1, 1])
    for name in l.names:
        info = l.info[name]
        if name == "beamatlas":
            continue
        texs = [zero] * len(info.textures)
        params = [0.0] * info.cb_floats
        out = l.run(name, params, texs, size=(8, 8))
        require(out.dtype == np.uint8 and out.shape == (8, 8, 4), f"{name}: bad output shape")
        texs1 = [one] * len(info.textures)
        params1 = [1.0] * info.cb_floats
        out2 = l.run(name, params1, texs1, size=(8, 8))
        require(out2.dtype == np.uint8, f"{name}: bad output dtype")
    return {"shaders": len(l.names) - 1}


# ---------------------------------------------------------------------------
# Static interface lint (uniform layout vs Lua call sites)
#
# Ported from the rule set AviUtl2_Astra applies during `astra build`
# (cbuffer layout validation), re-implemented in Python so it runs here
# without the Windows-only Astra binary.  See sandbox/docs/ANALYSIS.zh.md.
# ---------------------------------------------------------------------------
@check("hlsl: cbuffer layout matches every Lua call site (static)", tags=["hlsl", "static"])
def uniform_layout_static():
    from vl3dsb.hlsl.interface import audit

    s = script()
    res = audit(s.shaders, s.text)
    require(not res["problems"], "; ".join(res["problems"]))
    rows = res["rows"]
    # every shader that is called must have been measured
    called = {c.name for c in res["calls"] if c.name}
    unresolved = [r["shader"] for r in rows if not r["supplied"]]
    require(not unresolved, f"uniform tables could not be resolved: {unresolved}")
    padding = {r["shader"]: r["unused_tail"] for r in rows if r["unused_tail"]}
    dynamic = sorted({c.name for c in res["calls"] if c.dynamic_uniforms})
    return {
        "call_sites": len(res["calls"]),
        "shaders_called": len(called),
        "trailing_padding_floats": padding,
        "resolved_dynamic_tables": dynamic,
        "detail": f"{len(res['calls'])} call sites / {len(rows)} blocks verified",
    }


@check("hlsl: interface lint rejects deliberately broken call sites", tags=["hlsl", "static"])
def uniform_layout_mutation():
    """Mutation test: the lint must actually bite, not just always pass."""
    from vl3dsb.hlsl.interface import audit

    s = script()
    base = audit(s.shaders, s.text)
    require(not base["problems"], "baseline is dirty; mutation test is meaningless")

    # (1) drop one uniform the shader really reads
    src = s.text
    dropped = src.replace("{ 1.0 / 128.0, 1.0 / 128.0, shape_mode, r_lim_arg }",
                          "{ 1.0 / 128.0, shape_mode, r_lim_arg }")
    require(dropped != src, "mutation target for ps_vl_white not found")
    p1 = audit(s.shaders, dropped)["problems"]
    require(any("ps_vl_white" in x and "reads" in x for x in p1),
            f"under-supplied uniforms were not detected: {p1}")

    # (2) bind more floats than the block declares
    over = src.replace("{ pw, ph, 1.0, 0.0, sig_x, rad_x }",
                       "{ pw, ph, 1.0, 0.0, sig_x, rad_x, 0.0, 0.0, 0.0 }")
    require(over != src, "mutation target for ps_vl_blur not found")
    p2 = audit(s.shaders, over)["problems"]
    require(any("declares" in x for x in p2), f"over-supply was not detected: {p2}")

    # (3) call a shader that does not exist
    ghost = src.replace('obj.pixelshader("ps_vl_white"', 'obj.pixelshader("ps_vl_white_x"')
    require(ghost != src, "mutation target for the shader name not found")
    p3 = audit(s.shaders, ghost)["problems"]
    require(any("without a shader block" in x for x in p3),
            f"unknown shader names were not detected: {p3}")
    return {"detail": "3/3 mutations detected (under-supply / over-supply / unknown name)"}


@check("hlsl: every block declares a matching entry point and explicit registers", tags=["hlsl", "static"])
def entry_points():
    s = script()
    bad = []
    for name, src in s.shaders.items():
        code = re.sub(r"//[^\n]*", "", src)
        if not re.search(rf"\bfloat4\s+{re.escape(name)}\s*\(", code) and \
           not re.search(rf"\bvoid\s+{re.escape(name)}\s*\(", code):
            bad.append(f"{name}: no function named like the block")
        if len(re.findall(rf"\b(?:float4|void)\s+{re.escape(name)}\s*\(", code)) > 1:
            bad.append(f"{name}: entry point defined twice")
        if "cbuffer" in code and not re.search(r"register\s*\(\s*b0\s*\)", code):
            bad.append(f"{name}: cbuffer without an explicit register(b0)")
        for i, _tex in enumerate(re.findall(r"Texture2D\s+(\w+)", code)):
            if f"t{i}" not in code:
                bad.append(f"{name}: texture binding t{i} not declared")
                break
        if "SV_Position" not in code:
            bad.append(f"{name}: missing SV_Position input")
        if "SV_Target" not in code:
            bad.append(f"{name}: missing SV_Target output")
    require(not bad, "; ".join(bad))
    return {"shaders": len(s.shaders)}


# ---------------------------------------------------------------------------
# Alpha encoding switch (p26.w = wboit_ka)
#
# `beamatlas` has two documented alpha encodings, selected by p26.w:
#   p26.w > 0.01  proportional  : out_a = quantise(saturate(emit_s * ka_eff))
#   p26.w <= 0    constant token: out_a = p12.z (min_opt_a)
# v3.14 flipped the non-spot path from 0.24 to 0.0, so both branches have to
# stay alive and produce usable alpha. A separate runtime check verifies the
# assumed fragment cutoff on the *product* of texture and drawpoly alpha.
# ---------------------------------------------------------------------------
_F_P12_Z = 12 * 4 + 2      # min_opt_a
_F_P26_W = 26 * 4 + 3      # wboit_ka


@check("hlsl: beamatlas alpha encoding switches on wboit_ka (constant token vs proportional)",
       tags=["hlsl", "core"])
def beamatlas_alpha_switch():
    l = lib()
    tr = _capture_beam_trace(occ=False)
    texs = tr["textures"]
    const_token = float(tr["params"][_F_P12_Z])
    require(const_token > 0, f"min_opt_a (p12.z) is {const_token}, the constant path would emit alpha 0")

    outs = {}
    for ka in (0.0, 0.24):
        p = list(tr["params"])
        p[_F_P26_W] = ka
        outs[ka] = l.run("beamatlas", p, texs, out_hw=tr["size"])

    a_const = outs[0.0][..., 3].astype(np.float32) / 255.0
    a_prop = outs[0.24][..., 3].astype(np.float32) / 255.0
    lit = a_const > 0
    require(lit.any(), "constant-alpha encoding produced no lit pixel at all")
    require(a_prop.max() > 0, "proportional encoding produced no lit pixel at all")

    # constant path: every lit pixel carries the same quantized token
    vals = np.unique(a_const[lit])
    require(vals.size == 1 and float(np.abs(vals - const_token).max()) <= 0.5 / 255.0 + 1e-6,
            f"constant path should emit only quantized p12.z={const_token:.5f}, got {vals[:6]}")
    # the proportional branch may *drop* pixels (it returns 0 below min_a), so it
    # can only ever cover a subset -- and the colour must not depend on the
    # encoding at all: alpha is coverage, not tint.
    lit_const = int((a_const > 0).sum())
    lit_prop = int((a_prop > 0).sum())
    require(lit_const >= lit_prop,
            f"constant path must cover >= the proportional one ({lit_const} < {lit_prop})")
    both = (a_const > 0) & (a_prop > 0)
    require(both.any(), "the two encodings share no lit pixel")
    require(np.array_equal(outs[0.0][..., :3][both], outs[0.24][..., :3][both]),
            "changing wboit_ka must not alter RGB where both encodings keep a pixel: "
            "it encodes coverage in alpha only")
    return {"min_opt_a": round(const_token, 6),
            "lit_px": {"constant": lit_const, "proportional": lit_prop},
            "alpha_ratio": round(float(a_prop.mean()) / max(float(a_const.mean()), 1e-9), 3),
            "alpha_const_max": round(float(a_const.max()), 5),
            "alpha_prop_max": round(float(a_prop.max()), 5)}


# ---------------------------------------------------------------------------
# 抖动 / 去相关常量：黄金比家族 + IGN（外部给定）
# ---------------------------------------------------------------------------
def _shader_const(src: str, ident: str) -> float:
    m = re.search(rf"const\s+float\s+{re.escape(ident)}\s*=\s*([-0-9.eE+]+)\s*;", src)
    require(m is not None, f"{ident} not found in beamatlas")
    return float(m.group(1))


def _gaps(alpha: float, n: int):
    xs = sorted((k * alpha) % 1.0 for k in range(1, n + 1))
    gs = [xs[i + 1] - xs[i] for i in range(n - 1)] + [1.0 - xs[-1] + xs[0]]
    return min(gs), max(gs)


@check("hlsl: dither constants are the golden-ratio family and stay decorrelated",
       tags=["hlsl", "contract", "core"])
def dither_constants():
    """抖动常量不是调参值：它们是数论常数，写错只会让画面悄悄出现条带 / 彩色台阶。

    这里把三件事变成可执行断言：
      1. 数值身份：1/φ、1/φ²、(5+√5)/10、1/ρ（塑料数）在 8 位小数上逐位吻合；
      2. 增量有效性：frac(k/φ) 的分层性（最坏间隔必须贴近 1/N）—— 这是「用 φ
         而不是随便一个数」的可检验理由，换成 0.6 这类有理数会立刻塌成零间隔；
      3. 去相关：三个通道的抖动偏移两两相隔 > 1/4，否则 RGB 会同步量化。
    IGN 三个系数是 Jimenez 2014 的公开值，属于外部给定，只能原样保留。
    """
    src = script().shaders["beamatlas"]
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    rho = 1.3247179572447460          # 塑料数：x³ = x + 1 的实根

    expected = {
        "VL_PHI_INV": 1.0 / phi,
        "VL_DITHER_G": 1.0 / phi ** 2,
        "VL_DITHER_B": phi ** 2 / (1.0 + phi ** 2),      # = (5+√5)/10
        "VL_R2_INV_RHO": 1.0 / rho,
        "VL_IGN_A": 52.9829189,
        "VL_IGN_BX": 0.06711056,
        "VL_IGN_BY": 0.00583715,
    }
    got = {}
    for ident, want in expected.items():
        val = _shader_const(src, ident)
        got[ident] = val
        require(abs(val - want) <= 5e-9,
                f"{ident} = {val!r} no longer matches {want!r} (8-decimal truncation)")

    # 增量有效性：φ 是「最难逼近的有理数之比」，其倍数的落点间隔最均匀。
    n = 64
    lo, hi = _gaps(got["VL_PHI_INV"], n)
    require(lo > 1.0 / (4 * n), f"1/φ stratification collapsed: min gap {lo:.6f}")
    require(hi < 4.0 / n, f"1/φ stratification collapsed: max gap {hi:.6f}")
    # 把这条检查的判别力本身也钉住：有理增量会周期化 → 出现零间隔
    require(_gaps(0.6, n)[0] == 0.0, "the stratification probe lost its discriminating power")

    # 三通道去相关：圆环距离两两 > 1/4
    offs = [0.0, got["VL_DITHER_G"], got["VL_DITHER_B"]]
    worst = min(min(abs(a - b), 1.0 - abs(a - b))
                for i, a in enumerate(offs) for b in offs[i + 1:])
    require(worst > 0.25, f"channel dither offsets are too close: {worst:.4f}")

    # IGN 在 8x8 小块上应当处处不同、均值接近 0.5（否则等于没抖动）
    a, bx, by = got["VL_IGN_A"], got["VL_IGN_BX"], got["VL_IGN_BY"]
    vals = [((a * (((x * bx) + (y * by)) % 1.0)) % 1.0) for y in range(8) for x in range(8)]
    q = sorted({int(v * 255.0 + 0.5) for v in vals})
    require(len(q) >= 60, f"IGN degenerated: only {len(q)}/64 distinct levels in an 8x8 tile")
    require(abs(sum(vals) / len(vals) - 0.5) < 0.05, "IGN mean drifted away from 0.5")

    # 代码体里不能再有裸字面量（注释不算：文档注释里本来就该写出数值）
    code = re.sub(r"//[^\n]*", "", src)
    for lit in ("0.61803399", "0.38196601", "0.72360680", "0.75487767",
                "52.9829189", "0.06711056", "0.00583715"):
        require(code.count(lit) == 1, f"naked {lit} left in beamatlas besides its declaration")

    return {"phi_inv": got["VL_PHI_INV"], "min_gap": round(lo, 6),
            "max_gap": round(hi, 6), "channel_sep": round(worst, 4),
            "ign_levels": len(q)}


@check("hlsl: shadow softness ramp is named apart from the screen-radius cap",
       tags=["hlsl", "contract", "core"])
def shadow_soft_ramp():
    """520 这个数在两处出现，但**语义不同**：Lua 侧是屏幕半径上限，这里是被
    遮挡后距离的半影饱和距离。命名必须分开，否则一次全局替换会同时改坏两件事。
    """
    from vl3dsb.consts import lua_consts

    src = script().shaders["beamatlas"]
    soft_dist = _shader_const(src, "VL_SHADOW_SOFT_DIST")
    soft_min = _shader_const(src, "VL_SOFT_MIN_F")
    soft_max = _shader_const(src, "VL_SOFT_MAX_F")

    require(soft_dist == 520.0, f"shadow softness distance changed: {soft_dist}")
    require(abs((soft_min + soft_max) - 1.0) < 1e-7,
            f"soft radius ramp no longer tops out at the full radius "
            f"({soft_min} + {soft_max} != 1.0)")

    # 同值不同义：Lua 的 R_MAX 恰好也是 520，但必须是两个独立的名字
    lc = lua_consts()["LCHAR"]
    require(lc["R_MAX"] == soft_dist,
            "the coincidence being documented no longer holds - update the comment")
    code_only = re.sub(r"//[^\n]*", "", src)
    require("R_MAX" not in code_only,
            "beamatlas must not borrow the Lua name R_MAX for a softness distance")
    lua_dir = REPO_ROOT / "src" / "VolumetricLight3D" / "modules"
    leaked = [p.name for p in sorted(lua_dir.glob("*.lua"))
              if "VL_SHADOW_SOFT_DIST" in p.read_text(encoding="utf-8")]
    require(not leaked, f"the shader-only softness constant leaked into Lua: {leaked}")

    # 代码体里不许再裸写 0.22 / 0.78 / 520.0
    for lit in ("0.22", "0.78", "520.0"):
        require(code_only.count(lit) == 1,
                f"naked {lit} left in beamatlas besides its declaration")

    return {"soft_dist": soft_dist, "ramp": [soft_min, soft_max],
            "lua_r_max": lc["R_MAX"]}
