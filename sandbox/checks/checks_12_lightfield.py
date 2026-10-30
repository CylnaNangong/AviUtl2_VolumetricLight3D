"""
VL3D Light Field v1（体積光 → 他 3D スクリプトへの受光連動）の検証。

VolumetricLight3D は毎フレーム、公式のスクリプト間共有テーブル `global`
(docs.aviutl2.jp/lua § 変数) へ 1 光束 = 189 バイトの光場を書き出します。
雪（3D）などの受信側はそれを読んで粒子を発光させます。

ここで検証するのは 3 層:

  1. **公開側** — ペイロードの形式・幾何の自己整合性・フレーム刻印・
     共有インデックスの維持・`global` が無いホストでの無害な無効化。
  2. **受信ブロック** — `integrations/snow3d/lua/receiver.lua` を実際に
     LuaJIT で実行し、円錐の内外判定・距離減衰・終端フェード・色を確認。
  3. **統合** — `apply_lightfield.py` を適用した雪（3D）をサンドボックス
     ホストで実行し、光束内の粒子だけが加算発光パスへ回ることを確認
     （雪（3D）のリポジトリが見つからない環境では SKIP）。
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from vl3dsb import assets, session
from vl3dsb.harness import check, require, skip
from vl3dsb.host import Host, HostConfig, Layer
from vl3dsb.session import run_frame, script, shader_library

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RECEIVER_LUA = REPO / "integrations" / "snow3d" / "lua" / "receiver.lua"
PATCHER = REPO / "integrations" / "snow3d" / "apply_lightfield.py"

PAYLOAD_LEN = 189
BEAM_FLOATS = 26


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _light_source(w: int = 640, h: int = 360) -> np.ndarray:
    """明るい暖色の窓（光束色が有彩色になるよう R を強くする）。"""
    img = assets.stained_window(w, h).astype(np.float32) / 255.0
    img[..., 0] = np.clip(img[..., 0] * 1.15 + 0.10, 0.0, 1.0)
    return (img * 255.0 + 0.5).astype(np.uint8)


def _publish(params: Optional[Dict] = None, *, w: int = 640, h: int = 360,
             layers: Optional[List[Layer]] = None, layer_index: int = 4) -> Host:
    p = {"src_layer": 1, "intensity": 200.0, "slices": 16.0, "threshold": 0.0}
    p.update(params or {})
    ls = layers or [Layer(index=1, image=_light_source(w, h), kind="image")]
    res = run_frame(ls, p, width=w, height=h, layer_index=layer_index)
    return res.host


def _payload(host: Host, layer: int = 4) -> Optional[bytes]:
    hx = host.global_string(f"VL3D_LF:{layer}")
    return bytes.fromhex(hx) if hx else None


def _decode(b: bytes) -> Dict:
    frame, scene = struct.unpack_from("<II", b, 7)
    time_, = struct.unpack_from("<f", b, 15)
    layer, src_layer = struct.unpack_from("<HH", b, 19)
    cam = struct.unpack_from("<15f", b, 23)
    kind = b[83]
    vals = struct.unpack_from("<26f", b, 85)
    names = ("wx wy wz ax ay az rx ry rz dx dy dz apex_x apex_y apex_z "
             "r0 hx hy length kdec col_r col_g col_b lum power coverage").split()
    out = {"magic": b[0:5], "version": b[5], "flags": b[6], "frame": frame,
           "scene": scene, "time": time_, "layer": layer, "src_layer": src_layer,
           "cam": cam, "kind": kind}
    out.update(dict(zip(names, vals)))
    return out


def _snow3d_script() -> Optional[Path]:
    cand = os.environ.get("VL3DSB_SNOW3D", "").strip()
    roots: List[Path] = []
    if cand:
        roots.append(Path(cand).expanduser())
    roots += [REPO.parent / "AviUtl2_Snow3D", REPO.parent / "Snow3D",
              REPO / "AviUtl2_Snow3D"]
    for root in roots:
        if not root.is_dir():
            continue
        for name in ("雪（3D）.obj2", "雪(3D).obj2", "Snow3D.obj2"):
            p = root / "Script" / name
            if p.is_file():
                return p
    return None


# ---------------------------------------------------------------------------
# 1. 公開側
# ---------------------------------------------------------------------------
@check("lightfield: VolumetricLight3D publishes a well-formed field into global", tags=["lightfield"])
def publishes_field():
    host = _publish()
    idx = host.lua.eval('(function() local v = global["VL3D_LF_IDX"]; '
                        'return type(v) == "string" and v or "" end)()')
    require(str(idx).startswith("VL3LF1|"), f"index header malformed: {idx!r}")
    parts = str(idx).split("|")
    require(len(parts) == 3, f"index has {len(parts)} fields: {idx!r}")
    require(parts[2] == "4", f"index should list layer 4, got {parts[2]!r}")

    b = _payload(host)
    require(b is not None, "layer 4 published no payload")
    require(len(b) == PAYLOAD_LEN, f"payload is {len(b)} bytes (expected {PAYLOAD_LEN})")
    d = _decode(b)
    require(d["magic"] == b"VL3LF", f"bad magic {d['magic']!r}")
    require(d["version"] == 1, f"bad version {d['version']}")
    require(d["layer"] == 4 and d["src_layer"] == 1,
            f"layer/src mismatch: {d['layer']}/{d['src_layer']}")
    require(0 <= d["frame"] <= 10 ** 6, f"scene frame out of range: {d['frame']}")
    ax = np.array([d["ax"], d["ay"], d["az"]], float)
    require(abs(np.linalg.norm(ax) - 1.0) < 2e-3, f"axis is not unit length: {ax}")
    require(d["length"] > 1.0, f"beam length {d['length']}")
    require(d["hx"] > 0.5 and d["hy"] > 0.5, f"beam half extents {d['hx']}/{d['hy']}")
    require(d["power"] > 0.0, f"power {d['power']}")
    require(0.0 <= d["coverage"] <= 1.0, f"coverage {d['coverage']}")
    for k in ("col_r", "col_g", "col_b"):
        require(0.0 <= d[k] <= 1.0 + 1e-6, f"{k}={d[k]} outside 0..1")
    require(d["cam"][13] == 640 and d["cam"][14] == 360,
            f"screen size not published: {d['cam'][13:15]}")
    return {"bytes": len(b), "kind": d["kind"], "axis": [round(float(x), 3) for x in ax],
            "length": round(d["length"], 1), "power": round(d["power"], 3)}


@check("lightfield: beam geometry is self-consistent (apex/axis/plane)", tags=["lightfield"])
def geometry_self_consistent():
    out = {}
    for params, expect_kind in (({"spread": 0.0}, 0), ({"spread": 30.0}, 1)):
        host = _publish(params)
        d = _decode(_payload(host))
        require(d["kind"] == expect_kind,
                f"spread={params['spread']} expected kind {expect_kind}, got {d['kind']}")
        w = np.array([d["wx"], d["wy"], d["wz"]])
        apex = np.array([d["apex_x"], d["apex_y"], d["apex_z"]])
        ax = np.array([d["ax"], d["ay"], d["az"]])
        if d["kind"] == 0:
            # 平行光: apex フィールドは進行方向そのもの
            require(abs(np.linalg.norm(apex) - 1.0) < 2e-3,
                    f"parallel beam direction not unit: {apex}")
        else:
            # 放射: apex から進行方向へ r0 進むと光源面中心に一致する
            calc = apex + ax * d["r0"]
            require(np.linalg.norm(calc - w) < 1.5,
                    f"apex+axis*r0={calc} does not reach plane centre {w}")
            require(d["r0"] > 1.0, f"r0 too small: {d['r0']}")
        out[f"kind{d['kind']}"] = {"r0": round(d["r0"], 2), "length": round(d["length"], 1)}
    return out


@check("lightfield: shared index prunes stale slots and keeps live ones", tags=["lightfield"])
def index_prunes_stale():
    host = _publish()
    idx_before = str(host.lua.eval('global["VL3D_LF_IDX"]'))
    # 別レイヤーの古い光場（フレーム 0 のまま）を偽装して再公開させる
    host.lua.execute(
        'global["VL3D_LF:7"] = global["VL3D_LF:4"];'
        'global["VL3D_LF_IDX"] = "VL3LF1|99999|4,7,9"'
    )
    host.run_frame(60)
    idx = str(host.lua.eval('global["VL3D_LF_IDX"]'))
    require("7" not in idx.split("|")[2].split(","),
            f"stale slot 7 survived: {idx} (before: {idx_before})")
    require(idx.split("|")[2].split(",") == ["4"], f"index should only list layer 4: {idx}")

    # 有効な別レイヤーは残る
    host.lua.execute(
        'global["VL3D_LF:7"] = global["VL3D_LF:4"];'
        'global["VL3D_LF_IDX"] = "VL3LF1|60|4,7"'
    )
    host.run_frame(60)
    idx = str(host.lua.eval('global["VL3D_LF_IDX"]'))
    require("7" in idx.split("|")[2].split(","), f"live slot 7 was pruned: {idx}")
    return {"index": idx}


@check("lightfield: publisher is inert without `global` and when disabled", tags=["lightfield"])
def inert_without_global():
    # `global` が無いホスト（将来の仕様変更を模擬）
    host = _publish()
    host.lua.execute("global = nil")
    ok = host.run_frame(1)
    require(ok, f"frame failed without global: {host.errors[:2]}")
    require(not host.errors, f"errors raised: {host.errors[:2]}")

    # 明示無効化フラグ
    host2 = _publish()
    host2.lua.execute("_G.VL3D_TEST_LF_DISABLE = true")
    ok = host2.run_frame(1)
    require(ok, f"frame failed when disabled: {host2.errors[:2]}")
    return {"no_global": "ok", "disabled": "ok"}


@check("lightfield: pure-Lua float32 packer matches the ffi path byte for byte", tags=["lightfield"])
def packer_parity():
    a = _publish({"spread": 30.0, "intensity": 137.0})
    b = _publish({"spread": 30.0, "intensity": 137.0})
    require(a.global_string("VL3D_LF:4") == b.global_string("VL3D_LF:4"),
            "identical frames published different payloads")

    host = _publish({"spread": 30.0, "intensity": 137.0})
    ref = host.global_string("VL3D_LF:4")
    host.lua.execute("_G.VL3D_TEST_LF_FORCE_PURE_PACK = true")
    host.run_frame(0)
    pure = host.global_string("VL3D_LF:4")
    require(pure == ref, "pure-Lua packer disagrees with ffi packer")
    return {"bytes": len(ref) // 2}


# ---------------------------------------------------------------------------
# 2. 受信ブロック（Lua 実装そのもの）
# ---------------------------------------------------------------------------
def _receiver_lua(host: Host) -> None:
    src = RECEIVER_LUA.read_text(encoding="utf-8")
    host.lua.execute("as_bool = function(v, d) if v == nil then return d end "
                     "if type(v) == 'boolean' then return v end "
                     "if type(v) == 'number' then return v ~= 0 end return d end")
    for name, value in (("lf_enable", True), ("lf_strength", 120), ("lf_tint", 70),
                        ("lf_spread", 100)):
        host.lua.globals()[name] = value
    host.lua.globals()["obj"] = host.lua.table_from({"frame": 0.0, "frame_s": 0.0})
    # ブロックは `local SNOW_LF` なので、テスト用にグローバルへ 1 個だけ露出させる
    host.lua.execute("SNOW_LF = (function()\n" + src + "\nreturn SNOW_LF end)()")


def _synthetic_field() -> bytes:
    """+Z 向けの平行光束（原点から長さ 1000, 半径 200 の矩形）。"""
    t = [b"VL3LF", bytes([1, 0]), struct.pack("<I", 0), struct.pack("<I", 0),
         struct.pack("<f", 0.0), struct.pack("<H", 4), struct.pack("<H", 1)]
    t.append(struct.pack("<15f", 0, 0, -1024, 0, 0, 1, 1, 0, 0, 0, 1, 0, 1024, 1920, 1080))
    t.append(bytes([0, 0]))
    beam = (
        0.0, 0.0, 0.0,            # W    = 原点
        0.0, 0.0, 1.0,            # axis = +Z
        1.0, 0.0, 0.0,            # right
        0.0, 1.0, 0.0,            # down
        0.0, 0.0, 0.0,            # apex（平行光なので未使用）
        0.0,                      # r0
        200.0, 200.0,             # Hx, Hy
        1000.0,                   # L
        0.002,                    # kdec
        1.0, 0.6, 0.3,            # 光束色
        1.0,                      # lum
        1.0,                      # power
        1.0,                      # coverage
    )
    assert len(beam) == 26
    t.append(struct.pack("<26f", *beam))
    return b"".join(t)


@check("lightfield: receiver samples the beam analytically (inside/outside/decay)", tags=["lightfield"])
def receiver_sampling():
    host = Host(script(), [], HostConfig(screen_w=320, screen_h=180))
    _receiver_lua(host)
    payload = _synthetic_field()
    host.lua.globals()["__payload"] = payload.hex()
    host.lua.execute(
        'global["VL3D_LF:4"] = (__payload:gsub("%x%x", function(h) '
        'return string.char(tonumber(h, 16)) end))'
        'global["VL3D_LF_IDX"] = "VL3LF1|0|4"'
    )
    host.lua.execute("__n = SNOW_LF.begin(obj, 0)")
    n = int(host.lua.globals()["__n"])
    require(n == 1, f"receiver decoded {n} beams (expected 1)")

    sample = host.lua.eval("SNOW_LF.sample")
    # 中心（軸上）は最も明るい
    r, g, b, lit = sample(0.0, 0.0, 500.0, 5.0)
    require(lit > 0.3, f"beam centre should be lit, got {lit}")  # exp(-0.002*500) ~ 0.368
    require(abs(r - 1.0) < 0.02 and abs(g - 0.6) < 0.02 and abs(b - 0.3) < 0.02,
            f"beam colour wrong: {r},{g},{b}")
    # 軸から 400 離れると範囲外（半径 200）
    r2, g2, b2, lit2 = sample(400.0, 0.0, 500.0, 5.0)
    require(lit2 == 0.0, f"outside particle should be dark, got {lit2}")
    # 光源面より手前（t<0）は範囲外
    _, _, _, lit3 = sample(0.0, 0.0, -50.0, 5.0)
    require(lit3 == 0.0, f"particle behind the source plane should be dark, got {lit3}")
    # 距離減衰で軸方向に単調減少
    _, _, _, near = sample(0.0, 0.0, 100.0, 5.0)
    _, _, _, far = sample(0.0, 0.0, 900.0, 5.0)
    require(near > far > 0.0, f"decay is not monotonic: {near} / {far}")
    # 縁は滑らかに落ちる（ソフトエッジ）
    edge_values = [float(sample(190.0, 0.0, 300.0, 20.0)[3]),
                   float(sample(205.0, 0.0, 300.0, 20.0)[3]),
                   float(sample(230.0, 0.0, 300.0, 20.0)[3])]
    require(edge_values[0] > edge_values[-1],
            f"edge feather is not decreasing: {edge_values}")

    # 無効化すると 0
    host.lua.execute("lf_enable = false; SNOW_LF.begin(obj, 1)")
    _, _, _, off = host.lua.eval("SNOW_LF.sample")(0.0, 0.0, 500.0, 5.0)
    require(off == 0.0, f"disabled receiver still lit: {off}")
    return {"centre": round(float(lit), 3), "decay": [round(float(near), 3), round(float(far), 3)]}


@check("lightfield: receiver tolerates foreign, stale and truncated payloads", tags=["lightfield"])
def receiver_robustness():
    host = Host(script(), [], HostConfig(screen_w=320, screen_h=180))
    _receiver_lua(host)
    bad_cases = {
        "garbage": "not-a-lightfield",
        "wrong-magic": "XXXXX" + _synthetic_field()[5:].hex(),
        "truncated": _synthetic_field()[:40].hex(),
        "future-version": (bytes([0x56, 0x4C, 0x33, 0x4C, 0x46, 9]) + _synthetic_field()[6:]).hex(),
    }
    for name, hexs in bad_cases.items():
        host.lua.globals()["__payload"] = hexs
        host.lua.execute(
            'global["VL3D_LF:4"] = (__payload:gsub("%x%x", function(h) '
            'return string.char(tonumber(h, 16)) end))'
            'global["VL3D_LF_IDX"] = "VL3LF1|0|4"'
        )
        host.lua.execute("__n = SNOW_LF.begin(obj, 0)")
        require(int(host.lua.globals()["__n"]) == 0, f"{name} payload was accepted")
        _, _, _, lit = host.lua.eval("SNOW_LF.sample")(0.0, 0.0, 500.0, 5.0)
        require(lit == 0.0, f"{name} payload produced light")
    # 古すぎるフレームは無視
    old = bytearray(_synthetic_field())
    struct.pack_into("<I", old, 7, 900)
    host.lua.globals()["__payload"] = bytes(old).hex()
    host.lua.execute(
        'global["VL3D_LF:4"] = (__payload:gsub("%x%x", function(h) '
        'return string.char(tonumber(h, 16)) end))'
        'global["VL3D_LF_IDX"] = "VL3LF1|0|4"'
    )
    host.lua.execute("__n = SNOW_LF.begin(obj, 0)")
    require(int(host.lua.globals()["__n"]) == 0, "stale payload was accepted")
    return {"cases": list(bad_cases) + ["stale"]}


# ---------------------------------------------------------------------------
# 3. 雪（3D）への適用（リポジトリが見つかる環境のみ）
# ---------------------------------------------------------------------------
def _isolated_snow_copy(tmp: Path) -> Path:
    src = _snow3d_script()
    if src is None:
        skip("雪（3D）のリポジトリが見つかりません（VL3DSB_SNOW3D で指定できます）")
    dst = tmp / "Script" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return dst


@check("lightfield: patcher is idempotent and refuses foreign scripts", tags=["lightfield"])
def patcher_behaviour():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_snow_copy(Path(td))
        r1 = subprocess.run([sys.executable, str(PATCHER), "--script", str(path),
                             "--no-backup"], capture_output=True, text=True)
        require(r1.returncode == 0, f"first patch failed: {r1.stdout}{r1.stderr}")
        once = path.read_bytes()
        r2 = subprocess.run([sys.executable, str(PATCHER), "--script", str(path),
                             "--no-backup"], capture_output=True, text=True)
        require(r2.returncode == 0, f"second patch failed: {r2.stdout}{r2.stderr}")
        require(path.read_bytes() == once, "patcher is not idempotent")
        require(once.count(b"VL3D Light Field v1") < 12, "payload got duplicated")

        foreign = Path(td) / "foreign.obj2"
        foreign.write_bytes(b"--information:other script v1.00\r\nlocal x = 1\r\n")
        r3 = subprocess.run([sys.executable, str(PATCHER), "--script", str(foreign),
                             "--no-backup"], capture_output=True, text=True)
        require(r3.returncode == 2, f"patcher accepted a foreign script (rc={r3.returncode})")
        return {"idempotent": True, "foreign_rc": r3.returncode}


@check("lightfield: patched 雪（3D）lights only the particles inside the beam", tags=["lightfield", "integration"])
def snow3d_integration():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_snow_copy(Path(td))
        r = subprocess.run([sys.executable, str(PATCHER), "--script", str(path),
                            "--no-backup"], capture_output=True, text=True)
        require(r.returncode == 0, f"patch failed: {r.stdout}{r.stderr}")

        sc = session.script(path)
        names = {p.name for p in sc.params}
        require({"lf_enable", "lf_strength", "lf_tint", "lf_spread"} <= names,
                f"new parameters missing: {sorted(names)}")

        cam = {"x": 0.0, "y": 0.0, "z": -1024.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
               "ux": 0.0, "uy": -1.0, "uz": 0.0, "rz": 0.0, "d": 1024.0}

        def run(params, publish=True):
            gsrc = _publish({"spread": 60.0, "length": 4000.0, "intensity": 260.0})
            cfg = HostConfig(screen_w=640, screen_h=360, camera_mode=1, camera=cam)
            h = Host(sc, [], cfg, shader_lib=shader_library(path), params=params)
            if publish:
                h.import_global(gsrc.export_global())
            h.layer_index = 3
            ok = h.run_frame(0)
            require(ok, f"snow frame failed: {h.errors[:2]}")
            return h

        h_on = run({"lf_enable": True, "count": 1200.0, "lf_strength": 200.0})
        h_off = run({"lf_enable": False, "count": 1200.0, "lf_strength": 200.0})

        logs = [p for p in h_on.prints if "体積光連動" in p]
        require(logs, f"Snow3D never reported lit particles: {h_on.prints[:3]}")
        lit = int(logs[0].split("受光粒子 ")[1].split(" /")[0])
        require(lit > 0, f"no particle was lit by the beam ({logs[0]})")
        require(len(h_on.quads) > len(h_off.quads),
                f"lit pass drew nothing: {len(h_on.quads)} vs {len(h_off.quads)}")
        require(h_on.setoptions.get("blend") == "none",
                f"blend mode was not restored: {h_on.setoptions.get('blend')!r}")

        # 受光クアッドは必ず解析円錐の内側（2 実装の座標系一致を確認）
        extra = len(h_on.quads) - len(h_off.quads)
        add_quads = [q for q in h_on.quads if q.get("blend") == "add"][-max(extra, 1):] if extra > 0 else []
        d = _decode(_payload(_publish({"spread": 60.0, "length": 4000.0,
                                       "intensity": 260.0}), layer=4))
        W = np.array([d["wx"], d["wy"], d["wz"]])
        axis = np.array([d["ax"], d["ay"], d["az"]])
        right = np.array([d["rx"], d["ry"], d["rz"]])
        down = np.array([d["dx"], d["dy"], d["dz"]])
        apex = np.array([d["apex_x"], d["apex_y"], d["apex_z"]])
        outside = 0
        for q in add_quads:
            v = np.array([tuple(x)[:3] for x in q["verts"]], float).reshape(-1, 3)
            c = v.mean(axis=0)
            rel = c - W
            t = float(rel @ axis)
            ex, ey = d["hx"], d["hy"]
            if d["kind"] == 1:
                k = (d["r0"] + t) / d["r0"]
            elif d["kind"] == 2:
                k = (d["r0"] - t) / d["r0"]
            else:
                k = 1.0
            qn = np.hypot(float(rel @ right) / (ex * k), float(rel @ down) / (ey * k))
            if qn > 1.35:
                outside += 1
        require(outside == 0,
                f"{outside}/{len(add_quads)} lit quads lie outside the analytic cone")

        # 光束を出さなければ従来どおり（描画数が一致）
        h_no = run({"lf_enable": True, "count": 1200.0}, publish=False)
        require(len(h_no.quads) == len(h_off.quads),
                f"without a published beam the output changed: "
                f"{len(h_no.quads)} vs {len(h_off.quads)}")
        return {"lit_particles": lit, "quads_on": len(h_on.quads),
                "quads_off": len(h_off.quads), "add_quads_checked": len(add_quads)}


@check("lightfield: integration blocks only use documented obj.* API", tags=["lightfield", "static"])
def integration_api_surface():
    """补丁插入的每个 Lua 片段都必须落在官方文档的 API 面上。"""
    from vl3dsb import api_lint

    shaders = {"ps_snow_shape_tint"}
    stats = {}
    for f in sorted((REPO / "integrations" / "snow3d" / "lua").glob("*.lua")):
        rep = api_lint.lint(f.read_text(encoding="utf-8"), shaders)
        require(not rep.issues, f"{f.name}: {rep.issues}")
        stats[f.name] = rep.stats.get("calls", 0)
    require(stats.get("lit_pass.lua", 0) > 0, "lit_pass.lua was not scanned")

    # 新着色器的入口点声明与 HLSL 侧一致
    hlsl = (REPO / "integrations" / "snow3d" / "lua" / "tint_shader.hlsl").read_text(encoding="utf-8")
    require("ps_snow_shape_tint" in hlsl, "tint shader entry point missing")
    require("Texture2D" in hlsl and ("SamplerState" in hlsl or ".Load(" in hlsl),
            "tint shader neither samples nor loads its source texture")
    require("SV_Position" in hlsl and "SV_Target" in hlsl, "tint shader lacks stage semantics")
    return stats
