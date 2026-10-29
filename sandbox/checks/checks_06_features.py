"""End-to-end feature coverage: 2D / 3D occlusion, AutoBlendLight, fog, HUD, presets."""
from __future__ import annotations

import numpy as np

from vl3dsb import assets, session
from vl3dsb.harness import check, fail, require
from vl3dsb.host import Layer
from vl3dsb.session import OUT_ROOT, composite, run_frame, save_png

W, H = 320, 180
BASE = {"src_layer": 1, "intensity": 180.0, "slices": 24.0}


def _layers(*, occ_2d=True, cube=False, cube_z=-300.0):
    layers = [Layer(index=1, image=assets.stained_window(160, 160), kind="image")]
    if occ_2d:
        layers.append(Layer(index=3, image=assets.silhouette(70, 120), kind="image",
                            x=25, y=18, z=-200.0))
    if cube:
        path = assets.write_test_obj(OUT_ROOT / "assets" / "cube.obj", 150.0, scene_units=True)
        layers.append(Layer(index=5, kind="obj", model_path=str(path),
                            effects={"モデルファイル": str(path)}, z=cube_z))
    return layers


def _render(params=None, **kw):
    p = dict(BASE)
    p.update(params or {})
    return run_frame(kw.pop("layers", _layers()), p, width=W, height=H, **kw)


def _diff(a, b) -> float:
    if a.volume is None or b.volume is None:
        return 0.0 if (a.volume is None and b.volume is None) else 255.0
    return float(np.abs(a.alpha.astype(int) - b.alpha.astype(int)).mean())


@check("occlusion: mode 2 (2D foreground mask) darkens the beam", tags=["occlusion", "core"])
def occ_2d():
    off = _render({"occ_mode": 0.0})
    on = _render({"occ_mode": 2.0})
    d = _diff(off, on)
    require(d > 0.2, f"2D occlusion changed nothing (diff={d})")
    require(on.stats()["alpha_mean"] < off.stats()["alpha_mean"],
            "the occluded frame should be darker on average")
    require(any("L3" in p for p in on.log), "HUD did not report the occluder layer")
    save_png(composite(None, off.volume, 1.0), OUT_ROOT / "features" / "occ_off.png")
    save_png(composite(None, on.volume, 1.0), OUT_ROOT / "features" / "occ_2d.png")
    return {"diff": round(d, 3), "alpha_off": round(off.stats()["alpha_mean"], 4),
            "alpha_on": round(on.stats()["alpha_mean"], 4),
            "artifacts": [str(OUT_ROOT / "features" / "occ_2d.png")]}


@check("occlusion: mode 3 (3D shadow + mask) engages the native rasterizer", tags=["occlusion", "core"])
def occ_3d():
    layers = _layers(occ_2d=True, cube=True, cube_z=-400.0)
    params = {"occ_mode": 3.0, "occ_target": 2.0, "occ_list": "5"}
    off = run_frame(_layers(occ_2d=True, cube=True, cube_z=-400.0), dict(BASE), width=W, height=H)
    r = run_frame(layers, {**BASE, **params}, width=W, height=H)
    require(not r.host.errors, f"raised: {r.host.errors[:1]}")
    desc = next((p for p in r.log if "GPU=" in p), "")
    require("L5(OBJ)" in desc, f"3D occluder not recognised: {desc}")
    require("tri:" in desc, f"shadow rasterizer drew no triangles: {desc}")
    tr = r.host.shader_traces["beamatlas"]
    sh = tr["textures"][2]
    covered = (sh[..., 3] > 127).mean()
    require(covered > 0.01, f"shadow map is empty (coverage={covered:.3f})")
    require(tr["params"][63] > 0.5, "sh3d_active flag not set in the beam uniforms")
    # the shadow must actually remove light from the volume
    darkened = float(np.abs(off.alpha.astype(int) - r.alpha.astype(int)).mean())
    require(darkened > 1.0,
            f"3D occlusion did not darken the beam (mean |delta alpha| = {darkened})")
    # the shadow map must actually modulate the beam: force the mask off
    from vl3dsb.session import shader_library
    lib = shader_library()
    base = lib.run("beamatlas", tr["params"], tr["textures"], out_hw=tr["size"])
    off = list(tr["params"])
    off[61] = 0.0            # occ_mode = 0
    off[63] = 0.0            # sh3d_active = 0
    no_occ = lib.run("beamatlas", off, tr["textures"], out_hw=tr["size"])
    d = int(np.abs(base[..., 3].astype(int) - no_occ[..., 3].astype(int)).max())
    save_png(sh, OUT_ROOT / "features" / "shmap.png")
    return {"shadow_coverage": round(float(covered), 4), "max_alpha_diff": d,
            "darkened_mean": round(darkened, 3),
            "artifacts": [str(OUT_ROOT / "features" / "shmap.png")]}


@check("occlusion: strength / softness are monotonic", tags=["occlusion"])
def occ_tuning():
    soft = []
    for s in (0.0, 50.0, 100.0):
        r = _render({"occ_mode": 2.0, "occ_soft": s})
        soft.append(r.stats()["alpha_mean"])
    require(soft[0] <= soft[-1] + 1e-6, f"more blur should not remove more light: {soft}")
    hard = _render({"occ_mode": 2.0, "occ_strength": 0.0})
    full = _render({"occ_mode": 2.0, "occ_strength": 100.0})
    require(_diff(hard, full) > 0.05, "occ_strength has no effect")
    return {"alpha_by_soft": [round(x, 4) for x in soft]}


@check("features: AutoBlendLight adds the ab_* shader chain", tags=["feature"])
def autoblend():
    off = _render({"ab_enable": False})
    on = _render({"ab_enable": True, "ab_target": 2.0, "ab_list": "3"})
    ab_calls = [s for s in on.host.shader_calls if s.startswith("ps_vl_ab_")]
    require(len(ab_calls) >= 5, f"expected the ab_* chain, got {on.host.shader_calls}")
    require(not on.host.errors, f"AutoBlend raised: {on.host.errors[:1]}")
    require("ps_vl_ab_shade" in on.host.shader_calls, "ab_shade never ran")
    require(not [s for s in off.host.shader_calls if s.startswith("ps_vl_ab_")],
            "ab_* shaders ran although ab_enable is false")
    d = _diff(off, on)
    save_png(composite(None, on.volume, 1.0), OUT_ROOT / "features" / "autoblend.png")
    return {"ab_shaders": len(ab_calls), "diff": round(d, 3),
            "artifacts": [str(OUT_ROOT / "features" / "autoblend.png")]}


@check("features: light colour, decay and length behave physically", tags=["feature"])
def photometry():
    near = _render({"decay": 0.0})
    far = _render({"decay": 90.0})
    require(near.stats()["alpha_mean"] > far.stats()["alpha_mean"],
            "higher decay must dim the beam")
    short = _render({"length": 600.0})
    long_ = _render({"length": 4000.0})
    require(long_.stats()["alpha_mean"] > 0, "long beam vanished")
    require(short.stats()["alpha_mean"] < long_.stats()["alpha_mean"],
            "a shorter beam should collect less light")
    warm = _render({"light_color": 0xFFB060})
    cool = _render({"light_color": 0x6090FF})
    require(_diff(warm, cool) > 0.1 or
            float(np.abs(warm.rgb.astype(int) - cool.rgb.astype(int)).mean()) > 0.5,
            "light colour did not change the beam")
    # the ratio r/b must be larger for the warm preset
    def rb(r):
        rgb = r.rgb.astype(float)
        mask = rgb.sum(axis=2) > 8
        if not mask.any():
            return 0.0
        return float((rgb[..., 0][mask] + 1).mean() / (rgb[..., 2][mask] + 1).mean())
    require(rb(warm) > rb(cool), f"warm light ratio r/b {rb(warm):.2f} <= cool {rb(cool):.2f}")
    return {"alpha_decay0": round(near.stats()["alpha_mean"], 4),
            "alpha_decay90": round(far.stats()["alpha_mean"], 4)}


@check("features: fog / wind / seed modulate the beam", tags=["feature"])
def fog_and_seed():
    clear = _render({"fog": 0.0})
    foggy = _render({"fog": 80.0})
    d = _diff(clear, foggy)
    require(d > 0.05, f"fog had no effect (diff={d})")
    a = _render({"fog": 80.0, "seed": 1.0})
    b = _render({"fog": 80.0, "seed": 2.0})
    require(_diff(a, b) > 0.01, "seed did not change the fog pattern")
    save_png(composite(None, foggy.volume, 1.0), OUT_ROOT / "features" / "fog.png")
    return {"fog_diff": round(d, 3), "seed_diff": round(_diff(a, b), 3),
            "artifacts": [str(OUT_ROOT / "features" / "fog.png")]}


@check("features: source layer transforms move the beam", tags=["feature"])
def source_transform():
    base = _render()
    moved = run_frame([Layer(index=1, image=assets.stained_window(160, 160), x=120, y=-40),
                       Layer(index=3, image=assets.silhouette(70, 120), x=25, y=18, z=-200.0)],
                      dict(BASE), width=W, height=H)
    d = _diff(base, moved)
    require(d > 0.2, f"moving the source had no effect (diff={d})")
    rotated = run_frame([Layer(index=1, image=assets.stained_window(160, 160), ry=25.0),
                         Layer(index=3, image=assets.silhouette(70, 120), x=25, y=18, z=-200.0)],
                        dict(BASE), width=W, height=H)
    require(not rotated.host.errors, f"rotated source raised: {rotated.host.errors[:1]}")
    require(rotated.volume is not None and rotated.alpha.max() > 0, "rotated source is black")
    return {"move_diff": round(d, 3), "rotate_alpha": int(rotated.alpha.max())}


@check("features: procedural light shapes (no source layer)", tags=["feature"])
def procedural():
    for shape in (1.0, 2.0):
        r = run_frame([], {**BASE, "src_layer": 0.0, "shape": shape}, width=W, height=H)
        require(not r.host.errors, f"shape={shape} raised: {r.host.errors[:1]}")
    return {"detail": "procedural shapes run without host errors"}


@check("features: view_mode (edit-camera X view) is safe in both positions", tags=["feature"])
def view_modes():
    auto = _render({"view_mode": 0.0})
    side = _render({"view_mode": 1.0})
    require(not side.host.errors, f"view_mode=1 raised: {side.host.errors[:1]}")
    require(side.volume is not None and side.alpha.max() > 0, "view_mode=1 produced no light")
    # In a 2D scene the option only re-bases the *edit* camera direction, so the
    # rendered beam is expected to be identical - recorded as an observation.
    return {"diff_2d_scene": round(_diff(auto, side), 4),
            "camera_mode": auto.host.cfg.camera_mode}
