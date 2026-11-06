"""Regression checks for v3.14 beam brightness & 3D Shader #118 OIT pipeline."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import numpy as np

from vl3dsb import assets
from vl3dsb.aup2 import load_aup2_scene, map_aup2_params_to_obj2
from vl3dsb.compositor3d import composite_scene_3d
from vl3dsb.harness import check, require
from vl3dsb.host import Host, HostConfig, Layer
from vl3dsb.config import REPO_ROOT
from vl3dsb.session import native, run_frame, script, shader_library, standard_scene

_P12_Z = 12 * 4 + 2   # min_opt_a
_P25_W = 25 * 4 + 3   # spot_ray_env_mode
_P26_W = 26 * 4 + 3   # wboit_ka


@check("brightness: non-spot optical path uses constant OIT token (poly_alpha=0.27, min_a=1/255, ka=0)",
       tags=["brightness", "core"])
def non_spot_constant_token():
    layers = [Layer(index=1, image=assets.stained_window(128, 128), z=400.0)]
    cam = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0, "rz": 0.0, "d": 1024.0}
    for inten in (60.0, 100.0, 260.0):
        r = run_frame(
            layers,
            {"src_layer": 1.0, "intensity": inten, "slices": 24.0, "auto_slice": 0.0, "show_info": 0.0},
            width=320, height=180, camera=cam, camera_mode=1, rasterize_2d=False,
        )
        require(not r.host.errors, f"host errors at intensity={inten}: {r.host.errors}")
        tr = r.host.shader_traces["beamatlas"]["params"]
        min_a = float(tr[_P12_Z])
        ka = float(tr[_P26_W])
        spot_env = float(tr[_P25_W])
        beam_q = [q for q in r.host.quads if q["alpha"] < 0.5]
        require(beam_q, "no volumetric slice quads emitted")
        pa = float(beam_q[0]["alpha"])
        a118 = pa * min_a
        require(abs(pa - 0.27) < 1e-6, f"expected poly_alpha=0.27, got {pa}")
        require(abs(min_a - 1.0 / 255.0) < 1e-6, f"expected min_opt_a=1/255, got {min_a}")
        require(abs(ka) < 1e-6, f"expected wboit_ka=0.0, got {ka}")
        require(spot_env < 0.5, f"expected non-spot path (spot_env=0), got {spot_env}")
        # Must pass Shader #118 >0.001 cutoff indoors while discarding 0.75x zero-neighbor borders
        require(a118 > 0.001, f"interior a118={a118:.6f} fails >0.001 cutoff")
        require(0.75 * a118 <= 0.001, f"zero-neighbor bilinear 0.75*a118={0.75*a118:.6f} leaks past 0.001")
    return {"poly_alpha": 0.27, "min_opt_a": round(1.0 / 255.0, 6), "a118": round(0.27 / 255.0, 6)}


@check("brightness: virtual camera spotlight path preserves spot_env solver untouched",
       tags=["brightness", "core"])
def virtual_spot_preserved():
    layers = standard_scene(96, 54, with_occluder=False, light_size=64)
    params = {"src_layer": 0.0, "cam_light_mode": 1.0, "intensity": 180.0,
              "res_mode": 2.0, "auto_slice": False, "slices": 16.0, "log_mode": False, "show_info": False}
    sc = script()

    def _factory(c: HostConfig) -> Host:
        h = Host(sc, layers, c, shader_lib=shader_library(), params=params, native=native())
        h.lua.execute("""_G.VL3D_TEST_CAM_LIGHTS = {
            count=1, unlit=0, amb_r=0, amb_g=0, amb_b=0, peak_gain=1,
            suppressed=false, primary={ltype=5, dx=0, dy=0, dz=-1,
              r=1, g=1, b=1, lum=1, px=0, py=0, pz=-800, cos_ang=0.7}
        }""")
        return h

    r = run_frame(layers, params, width=96, height=54, layer_index=4,
                  camera_mode=1, host_factory=_factory, rasterize_2d=False)
    require(not r.host.errors, f"virtual spot raised: {r.host.errors}")
    tr = r.host.shader_traces["beamatlas"]["params"]
    require(float(tr[_P25_W]) > 0.5, f"expected spot_env=1, got {tr[_P25_W]}")
    require(abs(float(tr[_P26_W])) < 1e-6, f"expected wboit_ka=0 on spot, got {tr[_P26_W]}")
    require(abs(float(tr[_P12_Z]) - 4.0 / 255.0) < 1e-6, f"expected spot min_opt_a=4/255, got {tr[_P12_Z]}")
    beam_q = [q for q in r.host.quads if q["alpha"] < 0.5]
    require(beam_q and abs(float(beam_q[0]["alpha"]) - 0.064125) < 1e-6, "unexpected spot poly_alpha")
    return {"spot_env": float(tr[_P25_W]), "poly_alpha": 0.064125, "min_opt_a": round(4.0 / 255.0, 6)}


@check("brightness: 3D Shader #118 OIT compositor + .aup2 loader restore pre-v3.08 beam brightness",
       tags=["brightness", "raster"])
def compositor3d_and_aup2_brightness():
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        win_rgba = assets.stained_window(192, 144)
        occ_rgba = assets.silhouette(80, 120)
        Image.fromarray(win_rgba, "RGBA").save(td_p / "win.png")
        Image.fromarray(occ_rgba, "RGBA").save(td_p / "occ.png")

        aup2_txt = """[scene.0]
scene=0
name=autoblend_synth
video.width=320
video.height=180
video.rate=30
video.scale=1
[0]
layer=0
scene=0
frame=0,60
[0.0]
effect.name=カメラ制御
X=-80.00,80.00,直線移動,0
Y=0.00
Z=-1024.00
目標レイヤー=1
視野角=0.00
[1]
layer=1
scene=0
frame=0,60
[1.0]
effect.name=画像ファイル
ファイル=C:\\test\\win.png
[1.1]
effect.name=標準描画
X=0.00
Y=0.00
Z=600.00
拡大率=220.00
透明度=0.00
[1.2]
effect.name=OpenGlow
強さ=9.0
拡散=20.0
減衰=2.0
しきい値=37.7
ガンマ=2.2
[2]
layer=2
scene=0
frame=0,60
[2.0]
effect.name=画像ファイル
ファイル=C:\\test\\occ.png
[2.1]
effect.name=標準描画
X=0.00
Y=30.00
Z=250.00
拡大率=120.00
透明度=0.00
[3]
layer=4
scene=0
frame=0,60
[3.0]
effect.name=VolumetricLight3D
光源レイヤー=2
光の出方=平行
光の中身=体積光
方向=90.0
手前角度=63.8
画面基準=1
長さ=900
減衰=55.0
強さ=120.0
しきい値=15.0
前景遮蔽=3D影のみ(物理遮光)
前景なじませを有効化=1
スライス数=32
自動動的精度=0
画面に状態表示=0
"""
        aup2_file = td_p / "test.aup2"
        aup2_file.write_text(aup2_txt, encoding="utf-8")

        sc_spec = load_aup2_scene(aup2_file, "autoblend_synth", frame=0, asset_dirs=[td_p])
        require(sc_spec.camera is not None and abs(sc_spec.camera["x"] - (-80.0)) < 1e-5, "camera parse mismatch")
        require(len(sc_spec.layers) == 2 and sc_spec.layers[0].raw_image is not None, "layer/OpenGlow parse mismatch")
        vl_item = sc_spec.vl3d_items[0]
        obj2_cur = script()
        params = map_aup2_params_to_obj2(vl_item["raw_kv"], obj2_cur)

        r_fixed = run_frame(
            sc_spec.layers, params,
            width=sc_spec.width, height=sc_spec.height, frame=0,
            layer_index=vl_item["ui_layer"], camera=sc_spec.camera, camera_mode=1,
            rasterize_2d=False,
        )
        comp_fixed = composite_scene_3d(sc_spec.width, sc_spec.height, sc_spec.camera, sc_spec.layers, r_fixed.host.quads, oit_mode="wboit_uniform")

        # Mutate temporary copy back to v3.08..v3.13 non-spot settings to verify regression gap
        norm_src = obj2_cur.text.replace("\r\n", "\n")
        needle = ("            VL_BF.poly_alpha = K.OIT.ALPHA_DEFAULT\n"
                  "            VL_BF.min_opt_a  = K.OIT.BASE_DEFAULT\n"
                  "            wboit_ka         = 0.0")
        require(needle in norm_src, "expected v3.14 non-spot block not found in script")
        reg_src = norm_src.replace(
            needle,
            "            VL_BF.poly_alpha = 0.045\n            VL_BF.min_opt_a  = 6.0 / 255.0\n            wboit_ka         = 0.24",
            1,
        )
        reg_path = td_p / "v313_regressed.obj2"
        reg_path.write_text(reg_src, encoding="utf-8")
        r_reg = run_frame(
            sc_spec.layers, params,
            width=sc_spec.width, height=sc_spec.height, frame=0,
            layer_index=vl_item["ui_layer"], camera=sc_spec.camera, camera_mode=1,
            script_path=reg_path, rasterize_2d=False,
        )
        comp_reg = composite_scene_3d(sc_spec.width, sc_spec.height, sc_spec.camera, sc_spec.layers, r_reg.host.quads, oit_mode="wboit_uniform")

        luma_fixed = float((comp_fixed[..., :3] @ np.array([0.2126, 0.7152, 0.0722])).mean() * 255.0)
        luma_reg = float((comp_reg[..., :3] @ np.array([0.2126, 0.7152, 0.0722])).mean() * 255.0)
        require(luma_fixed > luma_reg + 1.5,
                f"expected v3.14 to be materially brighter than v3.08..v3.13 settings: fixed={luma_fixed:.2f} vs reg={luma_reg:.2f}")
        return {"luma_v314": round(luma_fixed, 2), "luma_v308_v313": round(luma_reg, 2),
                "gain_255": round(luma_fixed - luma_reg, 2)}


# ---------------------------------------------------------------------------
# 常量表契约：OIT alpha 窗口必须单一来源，且落在宿主丢弃窗口内
# ---------------------------------------------------------------------------
_CONSTS_LUA = REPO_ROOT / "src" / "VolumetricLight3D" / "modules" / "00a_consts.lua"

# 允许在常量表里出现的表达式：数字、四则运算、括号
_SAFE_EXPR = re.compile(r"^[0-9eE+\-*/.() ]+$")


def _lua_number(expr: str) -> float:
    """把常量表里的算术表达式按 Lua 的 double 语义求值（只接受纯算术）。"""
    expr = expr.strip().rstrip(",").strip()
    if not _SAFE_EXPR.match(expr):
        raise AssertionError(f"unsupported expression in consts table: {expr!r}")
    return float(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - 纯算术白名单


def _consts() -> dict:
    require(_CONSTS_LUA.exists(), f"missing {_CONSTS_LUA}")
    text = _CONSTS_LUA.read_text(encoding="utf-8")
    out = {}
    for name in ("DISCARD", "EDGE_KILL_F", "MIN_WEIGHT", "BASE_SPOT",
                 "BASE_DEFAULT", "ALPHA_SPOT", "ALPHA_DEFAULT"):
        m = re.search(rf"^\s*{name}\s*=\s*([^\n]+?)\s*,?\s*(?:--.*)?$", text, re.M)
        require(m, f"K.OIT.{name} not found in 00a_consts.lua")
        out[name] = _lua_number(m.group(1))
    return out


@check("brightness: OIT alpha window is single-sourced and mirrors the host cutoff", tags=["brightness", "contract", "core"])
def alpha_window_single_source():
    """常量表是唯一来源；两条路径都必须落在 (DISCARD, DISCARD/0.75) 内。

    这个用例把 docs/CHANGELOG 里 v3.09/v3.11/v3.12 的散文约束变成可执行断言：
    改 K.OIT 的任何一个数，都会在这里立刻失败，而不是让画面悄悄变化。
    """
    k = _consts()
    require(abs(k["DISCARD"] - 0.001) < 1e-12, f"host discard threshold changed: {k['DISCARD']}")

    w_spot = k["ALPHA_SPOT"] * k["BASE_SPOT"]
    w_base = k["ALPHA_DEFAULT"] * k["BASE_DEFAULT"]

    # 派生式必须精确复现历史锚点（0.2565/255），不允许引入量化漂移
    require(w_spot == k["MIN_WEIGHT"], f"spot weight {w_spot!r} != anchor {k['MIN_WEIGHT']!r}")
    require(k["ALPHA_SPOT"] == 0.064125, f"spot poly_alpha drifted: {k['ALPHA_SPOT']!r}")

    # 合法窗口：(DISCARD, DISCARD/0.75)。上界来自 2x2 双线性里 3/4 权重来自零纹素。
    upper = k["DISCARD"] / 0.75
    for name, w in (("spot", w_spot), ("default", w_base)):
        require(k["DISCARD"] < w < upper,
                f"{name} weight {w:.9f} outside ({k['DISCARD']}, {upper:.9f})")
        require(0.75 * w < k["DISCARD"],
                f"{name}: 0.75*{w:.9f} = {0.75 * w:.9f} would survive the edge discard")

    # EDGE_KILL_F 是对同一锚点的另一种读法：丢弃覆盖率 f < EDGE_KILL_F 的插值像素
    f_cut = k["DISCARD"] / w_spot
    require(abs(f_cut - k["EDGE_KILL_F"]) < 1e-3,
            f"EDGE_KILL_F={k['EDGE_KILL_F']} no longer describes the anchor (cut at {f_cut:.5f})")

    # 生成物里不允许再有裸字面量：渲染片段必须只引用 K.*
    text = script().text.replace("\r\n", "\n")
    for ban in ("VL_BF.poly_alpha = 0.064125", "VL_BF.poly_alpha = 0.27",
                "VL_BF.min_opt_a  = 4.0 / 255.0", "VL_BF.min_opt_a  = 1.0 / 255.0"):
        require(ban not in text, f"naked literal still in the render path: {ban!r}")
    require(text.count("local K = {") == 1, "the K table must be defined exactly once")

    return {"poly_alpha_spot": k["ALPHA_SPOT"], "w_spot": round(w_spot, 9),
            "w_default": round(w_base, 9), "window": [k["DISCARD"], round(upper, 9)],
            "edge_cut_f": round(f_cut, 5)}


@check("brightness: L_char keeps its three estimators inside one envelope",
       tags=["brightness", "contract", "core"])
def lchar_envelope():
    """L_char 是亮度归一化的分母（k_gain_base = 强度/100 × CALIB / L_char）。

    历史上有三条互不相同的估算路径（屏幕空间一般路径 / 平行光固定值 / 点聚光
    角度换算）。本次不合并它们（合并会改变现有画面），而是：
      1. 把三套系数外置成 K.LCHAR，并把共用的屏幕半径上限 520 收成一处；
      2. 断言「固定值 195 落在另外两条的窗口内，且能被它们的输入复现」——
         这是「切换光源类型时亮度跳变有界」的可检验形态；
      3. 断言生成物里不再有裸写的 195.0 / 520.0 / 0.32。
    """
    import math

    from vl3dsb.consts import lua_consts

    lc = lua_consts()["LCHAR"]
    gain = lua_consts()["GAIN"]

    # 窗口自洽
    require(lc["MIN_FIXED"] < lc["PAR_FIXED"] < lc["MAX_FIXED"],
            f"PAR_FIXED {lc['PAR_FIXED']} left the screen-space window "
            f"({lc['MIN_FIXED']}, {lc['MAX_FIXED']})")
    require(lc["MIN_SPOT"] < lc["PAR_FIXED"] < lc["MAX_SPOT"],
            f"PAR_FIXED {lc['PAR_FIXED']} left the point/spot window "
            f"({lc['MIN_SPOT']}, {lc['MAX_SPOT']})")

    # 可达性：(a) 用 base_ratio∈[0,1] 能算出 195；(c) 用 half_angle 能算出 195
    ratio2 = (lc["PAR_FIXED"] / lc["R_MAX"] - lc["BASE"]) / lc["RATIO2"]
    require(0.0 <= ratio2 <= 1.0,
            f"PAR_FIXED is unreachable from the screen-space path (base_ratio²={ratio2:.3f})")
    tan_needed = (lc["PAR_FIXED"] / lc["SPOT_COEF"]) / lc["SPOT_GAIN"]
    require(math.atan(tan_needed) <= lc["ANG_MAX"],
            "PAR_FIXED is unreachable from the point/spot path (angle beyond ANG_MAX)")

    # R_MAX 必须真的会生效，否则「共用上限」是死代码
    require(lc["SPOT_GAIN"] * math.tan(lc["ANG_MAX"]) > lc["R_MAX"],
            "R_MAX never clamps the spot reference radius")

    require(gain["REF_INTENSITY"] == 100.0,
            f"intensity normalization basis changed: {gain['REF_INTENSITY']}")

    # 生成物：三处裸字面量必须消失，且都改为读 K.LCHAR
    text = script().text.replace("\r\n", "\n")
    for banned in ("VL_BF.L_char = 195.0",
                   "math.min(math.max(VL_BF.Hx, VL_BF.Hy), 520.0)",
                   "ref_r * 0.32)",
                   "(P.intensity / 100.0) * K.GAIN.CALIB"):
        require(banned not in text, f"naked literal still present: {banned!r}")
    require(text.count("K.LCHAR") >= 4,
            "the three L_char paths no longer read K.LCHAR")

    return {"par_fixed": lc["PAR_FIXED"], "r_max": lc["R_MAX"],
            "window_fixed": [lc["MIN_FIXED"], lc["MAX_FIXED"]],
            "window_spot": [lc["MIN_SPOT"], lc["MAX_SPOT"]],
            "reach_ratio2": round(ratio2, 4), "reach_angle_rad": round(math.atan(tan_needed), 4)}
