#!/usr/bin/env python3
"""Compare user-supplied v3.00–v3.06 .obj2 scripts in a controlled CPU sandbox.

The inputs may retain their uploaded .txt suffix; no files are modified. The
CPU shader/raster models are not AviUtl2, D3D11, a real .aup2 project or real
host OIT. A textured framebuffer and reduced source/foreground images are
explicitly seeded so the AutoBlend comparison does not silently use black.

Example (replace all three paths):
  python3 sandbox/compare_original_versions.py --versions-dir /path/to/files \
    --window /path/to/window.jpg --character /path/to/character.png --hires

Without image options it uses procedural fixtures. --hires additionally runs
actual v3.01/v3.02 pool/prep HLSL on a 6501x4751 fixture and an extreme 16K
stripe fixture; it requires substantial memory. If the supplied window has the
expected 6251x4501 dimensions, --hires adds an explicitly synthetic 125-pixel
transparent border on all sides; this is NOT a claim about the host's buffer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
from PIL import Image

import compare_brightness as compare
from vl3dsb import assets, session
from vl3dsb.hlsl.executor import ShaderLibrary
from vl3dsb.hlsl.transpile import strip_comments, tokenize
from vl3dsb.host import Host, Layer
from vl3dsb.raster import rasterize_emission

VERSIONS = ("3.00", "3.01", "3.02", "3.03_r0_strict_i18n", "3.03", "3.04", "3.05", "3.06")
W, H = 256, 135
PARAMS = {
    "src_layer": 2, "intensity": 100.0, "threshold": 15.0, "res_mode": 0,
    "auto_slice": True, "occ_mode": 1, "occ_target": 2, "occ_list": "3",
    "ab_enable": True, "ab_target": 1, "ab_list": "3", "log_mode": True,
    "occ_soft": 8.0, "occ_strength": 100.0, "ab_bright": 68.0,
    "ab_color_mode": 2, "ab_style": 0, "ab_strength": 100.0,
    "ab_diffusion": 35.0, "ab_bias": -60.0, "ab_color_mix": 131.0,
    "ab_saturation": 100.0, "ab_luma_link": True, "ab_env_blend": 48.0,
    "ab_env_blur": 160.0,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_versions(directory: Path) -> dict[str, Path]:
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".obj2")]
    paths = {}
    for version in VERSIONS:
        pattern = re.compile(r"_v" + re.escape(version) + r"$", re.I)
        candidates = [p for p in files if pattern.search(p.stem)]
        require(len(candidates) == 1, f"expected one _v{version}.txt/.obj2 in {directory}, got {candidates}")
        path = candidates[0].resolve()
        expected = version[:4]
        sc = session.script(path)
        require(sc.version == "v" + expected and len(sc.shaders) == 14,
                f"unexpected header or shader count for {path.name}: {sc.information}, {len(sc.shaders)}")
        paths[version] = path
    return paths


def shader_tokens(src: str):
    return [(tok.kind, tok.text) for tok in tokenize(src)]


def static_comparison(paths):
    scripts = {v: session.script(p) for v, p in paths.items()}
    info = {v: {"filename": paths[v].name, "bytes": paths[v].stat().st_size,
                "sha256": digest(paths[v].read_bytes()), "information": scripts[v].information,
                "ui_parameters": len(scripts[v].params), "shader_blocks": len(scripts[v].shaders)}
            for v in VERSIONS}
    pairs = []
    for a, b in zip(VERSIONS, VERSIONS[1:]):
        sa, sb = scripts[a], scripts[b]
        ka, kb = set(sa.shaders), set(sb.shaders)
        common = sorted(ka & kb)
        tokens = [name for name in common if shader_tokens(sa.shaders[name]) != shader_tokens(sb.shaders[name])]
        raw = [name for name in common if sa.shaders[name] != sb.shaders[name]]
        # Unlike the tokenizer, this does not strip [loop]/[unroll] attributes.
        without_comments = [name for name in common if strip_comments(sa.shaders[name]).split() !=
                            strip_comments(sb.shaders[name]).split()]
        pa = {p.name: (p.kind, p.raw, p.default) for p in sa.params}
        pb = {p.name: (p.kind, p.raw, p.default) for p in sb.params}
        pairs.append({"from": a, "to": b, "hlsl_token_changes": tokens,
                      "hlsl_noncomment_changes": without_comments, "hlsl_raw_changes": raw,
                      "hlsl_added": sorted(kb - ka), "hlsl_removed": sorted(ka - kb),
                      "ui_parameter_declaration_changes": [k for k in sorted(set(pa) | set(pb))
                                                          if pa.get(k) != pb.get(k)]})
    for v in VERSIONS:
        require(strip_comments(scripts["3.01"].shaders["beamatlas"]).split() ==
                strip_comments(scripts[v].shaders["beamatlas"]).split(),
                f"beam shader non-comment difference in {v}")
        require({p.name: (p.kind, p.raw, p.default) for p in scripts[v].params} ==
                {p.name: (p.kind, p.raw, p.default) for p in scripts["3.01"].params},
                f"UI parameter declaration differs in {v}")
    # These signatures make the following factor formulas traceable to the
    # actual original Lua sources, not just a forced shader parameter.
    require("local kf = math.min(32, math.max(1, math.ceil(math.sqrt((lw * lh) / 4096.0))))"
            in scripts["3.01"].text, "v3.01 pool formula not found")
    require("local kf = math.max(1, math.ceil(math.max(lw, lh) / 256.0), math.min(32, math.ceil(math.sqrt((lw * lh) / 4096.0))))"
            in scripts["3.02"].text, "v3.02 pool formula not found")
    require("local kf_p = math.min(16, math.max(1, math.ceil(math.max(VL_BF.lw, VL_BF.lh) / 640.0)))"
            in scripts["3.01"].text, "v3.01 prep formula not found")
    require("local kf_p = math.max(1, math.ceil(math.max(VL_BF.lw, VL_BF.lh) / 640.0))"
            in scripts["3.02"].text, "v3.02 prep formula not found")
    return {"versions": info, "adjacent_changes": pairs,
            "beam_shader_noncomment_identical_all": True,
            "ui_parameter_declarations_identical_all": True}


def factors(w: int, h: int):
    base = math.ceil(math.sqrt(w * h / 4096.0))
    old_pool = min(32, max(1, base))
    new_pool = max(1, math.ceil(max(w, h) / 256.0), min(32, base))
    old_prep = min(16, max(1, math.ceil(max(w, h) / 640.0)))
    new_prep = max(1, math.ceil(max(w, h) / 640.0))
    return {"v3.01": {"pool": old_pool, "prep": old_prep},
            "v3.02": {"pool": new_pool, "prep": new_prep}}


class TrackingHost(Host):
    def __init__(self, *args, **kwargs):
        self.resolve_queries = []
        super().__init__(*args, **kwargs)

    def _api_br_resolve_layer(self, layer_idx, frame=0):
        self.resolve_queries.append([int(layer_idx), int(frame)])
        return super()._api_br_resolve_layer(layer_idx, frame)


def run_one(path, layers, params, background, *, frame=2, frame_s=0, scene_id=0,
            camera_mode=0, host_class=TrackingHost):
    def factory(cfg):
        host = host_class(session.script(path), layers, cfg, shader_lib=session.shader_library(path),
                            params=params, native=session.native())
        host.buffers["framebuffer"] = np.array(background, copy=True)
        return host
    result = session.run_frame(layers, params, width=W, height=H, frame=frame,
                               frame_s=frame_s, scene_id=scene_id, layer_index=6,
                               camera_mode=camera_mode, script_path=path, host_factory=factory,
                               extra_cfg={"restricted_env": True})
    host = result.host
    require(not host.errors and result.volume is not None and "beamatlas" in host.shader_traces,
            f"{path.name}: no valid beam; errors={host.errors}, log={result.log}")
    require(host.pixelshader_failures == 0 and not any("@error" in line for line in result.log),
            f"{path.name}: shader/script error: {result.log}")
    beam_trace = host.shader_traces["beamatlas"]
    beam_size = (beam_trace["size"][1], beam_trace["size"][0])
    beam = [q for q in host.quads if q.get("atlas") is not None and q["atlas"].shape[:2] == beam_size]
    require(beam, f"{path.name}: no beam geometry")
    emission = rasterize_emission(None, beam, W, H, mode="weighted")
    atlases = compare._distinct_atlases(beam)
    ab = None
    if "ps_vl_ab_shade" in host.shader_traces:
        trace = host.shader_traces["ps_vl_ab_shade"]
        ab = session.shader_library(path).run("ps_vl_ab_shade", trace["params"], trace["textures"],
                                               size=trace["size"])
    geometry = hashlib.sha256()
    for q in beam:
        geometry.update(np.asarray(q["verts"], dtype=np.float64).tobytes())
        geometry.update(np.asarray([q["alpha"]], dtype=np.float64).tobytes())
    row = {
        "status": next((line for line in result.log if "GPU=" in line), ""),
        "resolve_queries": host.resolve_queries,
        "scene_camera_queries": getattr(host, "camera_queries", []),
        "injected_blocked_pixeldata_reads": getattr(host, "blocked_pixeldata_reads", 0),
        "beam_uniform_p10": beam_trace["params"][40:44],
        "beam_quad_count": len(beam), "beam_atlas_hw": list(beam_size),
        "beam_atlas_sha256": [digest(a.tobytes()) for a in atlases],
        "beam_geometry_sha256": geometry.hexdigest(),
        "assumed_beam_luma": float(np.mean(emission[..., :3] @ compare.LUMA)),
        "preview_sha256": digest(result.volume.tobytes()),
        "ab_shader_sha256": None if ab is None else digest(ab.tobytes()),
        "ab_shader_hw": None if ab is None else list(ab.shape[:2]),
        "pool_factor": host.shader_traces.get("ps_vl_pool", {}).get("params", [None, None, None])[2],
        "prep_factor": host.shader_traces.get("ps_vl_prep", {}).get("params", [None, None, None])[2],
    }
    return row, ab


def fixture_matrices(paths, window, character, background):
    scenarios = {
        "no_occ_no_ab": {"occ_mode": 0, "ab_enable": False},
        "m1_with_ab": {},
        "m2_no_ab": {"occ_mode": 2, "ab_enable": False},
    }
    matrices, ab_images = {}, {}
    for scenario, delta in scenarios.items():
        params = {**PARAMS, **delta}
        rows = {}
        for ver in VERSIONS:
            layers = [Layer(index=2, image=window),
                      Layer(index=3, image=character, x=15, y=10, z=-200)]
            row, ab = run_one(paths[ver], layers, params, background)
            rows[ver] = row
            if scenario == "m1_with_ab":
                require(ab is not None and "+AB(L3)" in row["status"], f"{ver}: AB not exercised")
                ab_images[ver] = ab
        base = rows["3.01"]
        matrices[scenario] = {
            "params": params, "versions": rows,
            "v301_to_v306_beam_atlases_identical": base["beam_atlas_sha256"] == rows["3.06"]["beam_atlas_sha256"],
            "v301_to_v306_beam_geometry_identical": base["beam_geometry_sha256"] == rows["3.06"]["beam_geometry_sha256"],
            "v301_to_v306_preview_identical": base["preview_sha256"] == rows["3.06"]["preview_sha256"],
            "v301_to_v306_ab_shader_identical": (base["ab_shader_sha256"] == rows["3.06"]["ab_shader_sha256"]
                                                   if scenario == "m1_with_ab" else None),
        }
        print(f"{scenario}: " + "; ".join(
            f"v{ver}={row['assumed_beam_luma']:.8f}/AB:{str(row['ab_shader_sha256'])[:8]}"
            for ver, row in rows.items()), flush=True)
    require(ab_images["3.00"].shape == ab_images["3.01"].shape, "unexpected AB comparison size")
    d = np.abs(ab_images["3.00"].astype(np.int16) - ab_images["3.01"].astype(np.int16))
    ab_change_300_301 = {"shape": list(d.shape), "max_abs_0_255": int(d.max()),
                         "mean_abs_per_channel_0_255": float(d.mean()),
                         "changed_pixel_count": int(np.any(d > 0, axis=-1).sum())}
    return matrices, ab_change_300_301


def autoblend_temp_failure(paths, window, character, background):
    # Inject only tempbuffer <- framebuffer failure. v3.02 first samples the
    # framebuffer directly; v3.01 takes its own fallback. This emulates a
    # failure, not evidence that it occurred in the user's host.
    class TempCopyFailureHost(TrackingHost):
        def _api_copybuffer(self, dst, src):
            if str(dst) == "tempbuffer" and str(src) == "framebuffer":
                return False
            return super()._api_copybuffer(dst, src)

    rows, images = {}, {}
    for ver in ("3.01", "3.02", "3.06"):
        layers = [Layer(index=2, image=window),
                  Layer(index=3, image=character, x=15, y=10, z=-200)]
        rows[ver], images[ver] = run_one(paths[ver], layers, PARAMS, background,
                                        host_class=TempCopyFailureHost)
        require(images[ver] is not None, f"v{ver}: AB path was not exercised")
    require(images["3.01"].shape == images["3.02"].shape, "unexpected AB output sizes")
    diff = np.abs(images["3.01"].astype(np.int16) - images["3.02"].astype(np.int16))
    result = {"versions": rows, "ab_output_equal": bool(np.array_equal(images["3.01"], images["3.02"])),
              "ab_mean_absolute_channel_diff_0_255": float(diff.mean()),
              "ab_max_absolute_channel_diff_0_255": int(diff.max()),
              "ab_changed_pixels": int(np.any(diff > 0, axis=-1).sum())}
    print(f"injected temp-copy failure: v3.01/v3.02 AB identical={result['ab_output_equal']}, "
          f"beam atlases identical={rows['3.01']['beam_atlas_sha256'] == rows['3.02']['beam_atlas_sha256']}",
          flush=True)
    return result


def candidate_autoblend(reference, candidate, window, character, background):
    rows, images = {}, {}
    for name, path in (("v3.06", reference), ("candidate", candidate)):
        layers = [Layer(index=2, image=window),
                  Layer(index=3, image=character, x=15, y=10, z=-200)]
        rows[name], images[name] = run_one(path, layers, PARAMS, background)
        require(images[name] is not None, f"{name}: AB path was not exercised")
    require(images["v3.06"].shape == images["candidate"].shape, "candidate AB output sizes differ")
    diff = np.abs(images["v3.06"].astype(np.int16) - images["candidate"].astype(np.int16))
    result = {"candidate_sha256": digest(candidate.read_bytes()), "versions": rows,
              "ab_output_equal": bool(np.array_equal(images["v3.06"], images["candidate"])),
              "ab_mean_absolute_channel_diff_0_255": float(diff.mean()),
              "ab_max_absolute_channel_diff_0_255": int(diff.max())}
    print(f"v3.06/candidate with seeded framebuffer: AB identical={result['ab_output_equal']}", flush=True)
    return result


def delayed_frame(paths, window, character, background):
    params = {**PARAMS, "occ_mode": 3}
    rows = {}
    for ver in ("3.04", "3.05", "3.06"):
        layers = [Layer(index=2, image=window, start=619, end=1000),
                  Layer(index=3, image=character, x=15, y=10, z=-200, start=619, end=1000)]
        rows[ver], _ = run_one(paths[ver], layers, params, background, frame=146, frame_s=619, scene_id=1)
    require([3, 146] in rows["3.04"]["resolve_queries"] and "0hit" in rows["3.04"]["status"] and
            rows["3.04"]["ab_shader_sha256"] is None, "v3.04 delayed-frame failure was not exercised")
    for ver in ("3.05", "3.06"):
        require([3, 765] in rows[ver]["resolve_queries"] and "L3(m3)+AB(L3)" in rows[ver]["status"],
                f"v{ver}: delayed-frame target not detected")
    print("delayed-frame: v3.04=0hit/no AB at 146; v3.05/06=L3(m3)+AB(L3) at 765", flush=True)
    return rows


def scene_camera_fallback(paths, window, character, background):
    # This explicitly fabricated bridge models an unavailable edit camera and
    # a scene camera present only at the scene frame. It is not a real host or
    # proof that the user's project takes this fallback. No camera light is used.
    class CameraFallbackHost(TrackingHost):
        def __init__(self, *args, **kwargs):
            self.camera_queries = []
            super().__init__(*args, **kwargs)

        def _api_br_get_edit_camera(self):
            return (False, 0, 0, 0, 0, 0, 0, 0, -1, 0, 0, 1024)

        def _api_br_get_scene_camera(self, max_layer=100, frame=0):
            scene_frame = int(frame)
            self.camera_queries.append(scene_frame)
            if scene_frame < 619:
                return (False, 0, 0, 0, 0, 0, 0, 0, -1, 0, 0, 1024)
            return (True, 260, 0, -1024, 0, 0, 0, 0, -1, 0, 0, 1024)

    params = {**PARAMS, "occ_mode": 0, "ab_enable": False, "cam_light_mode": 0,
              "res_mode": 2, "auto_slice": False, "slices": 24.0}
    rows = {}
    for ver in ("3.04", "3.05", "3.06"):
        layers = [Layer(index=2, image=window, start=619, end=1000),
                  Layer(index=3, image=character, x=15, y=10, z=-200, start=619, end=1000)]
        rows[ver], _ = run_one(paths[ver], layers, params, background, frame=146, frame_s=619,
                               scene_id=1, camera_mode=2, host_class=CameraFallbackHost)
    require(rows["3.04"]["scene_camera_queries"] == [146] and
            rows["3.05"]["scene_camera_queries"] == [765], "camera query frame mismatch")
    require(rows["3.04"]["beam_uniform_p10"][0] == 0 and
            rows["3.05"]["beam_uniform_p10"][0] == 260, "camera fallback not exercised")
    print("edit-camera fallback: v3.04 scene query=146; v3.05/06=765, no camera light", flush=True)
    return rows


def opaque_layer(paths, window, character, background):
    cases = []
    for mode in (1, 2):
        for injected in (False, True):
            params = {**PARAMS, "occ_mode": mode, "ab_enable": False}
            layers = [Layer(index=2, image=window),
                      Layer(index=3, image=character, x=15, y=10, z=-200,
                            fx_name="synthetic effect", effects={"__fx_opaque_bug__": {"on": injected}})]
            versions = {}
            for ver in ("3.05", "3.06"):
                versions[ver], _ = run_one(paths[ver], layers, params, background)
            if injected:
                require("L3*" in versions["3.06"]["status"], "v3.06 raw fallback did not fire")
            else:
                require(versions["3.05"]["preview_sha256"] == versions["3.06"]["preview_sha256"],
                        f"healthy v3.05/v3.06 fixture differs in mode {mode}")
            cases.append({"mode": mode, "injected_effect_opaque_rectangle": injected, "versions": versions})
            print(f"opaque test mode={mode} injected={injected}: "
                  f"v3.05={versions['3.05']['assumed_beam_luma']:.8f}; "
                  f"v3.06={versions['3.06']['assumed_beam_luma']:.8f}", flush=True)

    # Controls: a truly opaque source must not be 'corrected'; a simulated
    # unreadable pixel buffer must make the audit inconclusive. The latter
    # disables getpixeldata only after loading the target layer with effects,
    # leaving the separate light-pool readback usable.
    class UnreadableAuditHost(TrackingHost):
        def _api_load(self, kind, arg=None, effect=None, size=None):
            self.occluder_fx_loaded = kind == "layer" and arg == 3 and bool(effect)
            return super()._api_load(kind, arg, effect, size)

        def _api_getpixeldata(self, name="object", fmt="rgba"):
            if getattr(self, "occluder_fx_loaded", False) and name == "object":
                self.blocked_pixeldata_reads = getattr(self, "blocked_pixeldata_reads", 0) + 1
                return None, 0, 0
            return super()._api_getpixeldata(name, fmt)

    for condition in ("genuinely_opaque", "audit_pixeldata_unavailable"):
        fg = np.array(character, copy=True)
        injected = condition == "audit_pixeldata_unavailable"
        if not injected:
            fg[..., 3] = 255
        versions = {}
        for ver in ("3.05", "3.06"):
            layers = [Layer(index=2, image=window),
                      Layer(index=3, image=fg, x=15, y=10, z=-200,
                            fx_name="synthetic effect", effects={"__fx_opaque_bug__": {"on": injected}})]
            versions[ver], _ = run_one(paths[ver], layers,
                                       {**PARAMS, "occ_mode": 2, "ab_enable": False}, background,
                                       host_class=UnreadableAuditHost if injected else TrackingHost)
        status = versions["3.06"]["status"]
        if injected:
            require(versions["3.06"]["injected_blocked_pixeldata_reads"] > 0,
                    "audit pixeldata failure was not actually exercised")
        require("L3*" not in status, f"v3.06 unexpectedly fell back in {condition}")
        require((",opq" in status) == (condition == "genuinely_opaque"),
                f"unexpected audit status in {condition}: {status}")
        require(versions["3.05"]["preview_sha256"] == versions["3.06"]["preview_sha256"],
                f"v3.05/v3.06 control differs: {condition}")
        cases.append({"mode": 2, "injected_effect_opaque_rectangle": injected,
                      "control": condition, "versions": versions})
        print(f"opaque control {condition}: v3.05/v3.06 preview identical, "
              f"v3.06 opq={',opq' in status}, raw_fallback=False", flush=True)
    return cases


def shader_result(lib, shader, texture, factor):
    h, w = texture.shape[:2]
    values = [w, h, factor, 0.15]
    if shader == "ps_vl_prep":
        values += [1, 1, 1, 1]
    dim_min = 2 if shader == "ps_vl_prep" else 1
    out_hw = (max(dim_min, math.ceil(h / factor)), max(dim_min, math.ceil(w / factor)))
    return lib.run(shader, values, [texture], out_hw=out_hw)


def large_fixtures(paths, window_path):
    libraries = {ver: ShaderLibrary({name: session.script(paths[ver]).shaders[name]
                                    for name in ("ps_vl_pool", "ps_vl_prep")})
                 for ver in ("3.01", "3.02")}
    if window_path:
        with Image.open(window_path) as im:
            original_size = list(im.size)
            require(im.size == (6251, 4501), "--hires window must be 6251x4501 or omitted")
            source = np.asarray(im.convert("RGBA"))
        texture = np.zeros((4751, 6501, 4), np.uint8)
        texture[125:4626, 125:6376] = source
        del source
        description = {"kind": "user window with synthetic transparent 125px border",
                       "original_size": original_size, "padded_size": [6501, 4751]}
    else:
        texture = np.empty((4751, 6501, 4), np.uint8)
        texture[..., 0] = 180
        texture[..., 1] = 80
        texture[..., 2] = 210
        texture[..., 3] = 255
        texture[:, ::20, :3] = 20
        description = {"kind": "synthetic vertical stripes", "padded_size": [6501, 4751]}
    f = factors(6501, 4751)
    require(f["v3.01"] == f["v3.02"] == {"pool": 32, "prep": 11}, "6501x4751 factor mismatch")
    large = {}
    for stage in ("pool", "prep"):
        name = "ps_vl_" + stage
        old = shader_result(libraries["3.01"], name, texture, f["v3.01"][stage])
        new = shader_result(libraries["3.02"], name, texture, f["v3.02"][stage])
        require(np.array_equal(old, new), f"6501x4751 {stage} output differs")
        large[stage] = {"factor": f["v3.01"][stage], "output_hw": list(old.shape[:2]),
                        "equal_rgba8": True, "sha256": digest(old.tobytes())}
        print(f"full-size {name}: factor {f['v3.01'][stage]}, v3.01/v3.02 RGBA8 equal, {old.shape}", flush=True)
    del texture

    stripes = np.zeros((8, 16384, 4), np.uint8)
    stripes[..., 3] = 255
    stripes[:, ::2, :3] = 255
    fs = factors(16384, 8)
    extreme = {}
    for stage in ("pool", "prep"):
        name = "ps_vl_" + stage
        a = shader_result(libraries["3.01"], name, stripes, fs["v3.01"][stage])
        b = shader_result(libraries["3.02"], name, stripes, fs["v3.02"][stage])
        extreme[stage] = {"v3.01_factor": fs["v3.01"][stage], "v3.02_factor": fs["v3.02"][stage],
                          "v3.01_output_hw": list(a.shape[:2]), "v3.02_output_hw": list(b.shape[:2]),
                          "v3.01_first_rgba": a[0, 0].tolist(), "v3.02_first_rgba": b[0, 0].tolist(),
                          "v3.01_mean_red_including_padding": float(a[..., 0].mean()),
                          "v3.02_mean_red_including_padding": float(b[..., 0].mean())}
    return {"6501x4751": {"source": description, "factors": f, "shaders": large},
            "16384x8_stripes": {"source": "synthetic alternating white/black columns",
                               "factors": fs, "shaders": extreme}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--versions-dir", type=Path, required=True)
    ap.add_argument("--window", type=Path)
    ap.add_argument("--character", type=Path)
    ap.add_argument("--candidate", type=Path, help="optional script to compare with v3.06 using the synthetic framebuffer")
    ap.add_argument("--hires", action="store_true")
    ap.add_argument("--json", type=Path, default=session.OUT_ROOT / "history-originals" / "comparison.json")
    args = ap.parse_args()
    if bool(args.window) != bool(args.character):
        ap.error("--window and --character must be supplied together")
    paths = find_versions(args.versions_dir)
    inputs = {p.resolve() for p in paths.values()}
    if args.window:
        inputs.update((args.window.resolve(), args.character.resolve()))
    if args.candidate:
        if not args.candidate.is_file():
            ap.error(f"candidate script does not exist: {args.candidate}")
        inputs.add(args.candidate.resolve())
    if args.json.expanduser().resolve() in inputs:
        ap.error("--json must not overwrite an input file")
    if args.window:
        window = compare._load_asset(args.window, 220)
        character = compare._load_asset(args.character, 120)
        asset_info = {"kind": "user images reduced for scene", "window_sha256": digest(args.window.read_bytes()),
                      "character_sha256": digest(args.character.read_bytes())}
    else:
        window = assets.stained_window(220, 158)
        character = assets.silhouette(120, 120)
        asset_info = {"kind": "procedural fixture"}
    background = np.array(Image.fromarray(window).resize((W, H), Image.Resampling.LANCZOS), dtype=np.uint8)
    background[..., 3] = 255
    asset_info.update({"window_rgba_shape": list(window.shape), "character_rgba_shape": list(character.shape),
                       "framebuffer": "resized light image, opaque; synthetic, not the original project"})
    out: dict[str, Any] = {"warning": "Synthetic CPU simulation, not actual AviUtl2/D3D11 output.",
                           "static": static_comparison(paths), "assets": asset_info,
                           "factors_at_hud_size": factors(6501, 4751)}
    out["matrix"], out["ab_300_vs_301"] = fixture_matrices(paths, window, character, background)
    out["autoblend_temp_copy_failure"] = autoblend_temp_failure(paths, window, character, background)
    if args.candidate:
        out["v306_vs_candidate_seeded_framebuffer"] = candidate_autoblend(
            paths["3.06"], args.candidate, window, character, background)
    out["delayed_frame"] = delayed_frame(paths, window, character, background)
    out["scene_camera_fallback"] = scene_camera_fallback(paths, window, character, background)
    out["opaque_fallback"] = opaque_layer(paths, window, character, background)
    if args.hires:
        out["high_resolution"] = large_fixtures(paths, args.window)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"results: {args.json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
