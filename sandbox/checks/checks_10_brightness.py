"""Regression checks for v3.14 beam brightness & 3D Shader #118 OIT pipeline."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from vl3dsb import assets
from vl3dsb.aup2 import load_aup2_scene, map_aup2_params_to_obj2
from vl3dsb.compositor3d import composite_scene_3d
from vl3dsb.harness import check, require
from vl3dsb.host import Host, HostConfig, Layer
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
        needle = "            VL_BF.poly_alpha = 0.27\n            VL_BF.min_opt_a  = 1.0 / 255.0\n            wboit_ka         = 0.0"
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
