"""Regression checks for software-rendering assumptions and shader literals."""
from __future__ import annotations

import re

import numpy as np

from vl3dsb.config import REPO_ROOT
from vl3dsb.harness import check, require
from vl3dsb.hlsl.executor import ShaderLibrary
from vl3dsb.host import Host, HostConfig
from vl3dsb.raster import DISCARD_W, rasterize_emission
from vl3dsb.session import script


def _quad(alpha, atlas=None):
    return {"verts": [[-1, -1, 0, .5, .5], [1, -1, 0, .5, .5],
                      [1, 1, 0, .5, .5], [-1, 1, 0, .5, .5]],
            "alpha": alpha, "atlas": atlas}


@check("raster: low-alpha cutoff and float OIT assumptions", tags=["runtime", "core"])
def low_alpha():
    atlas = np.full((2, 2, 4), [128, 0, 0, 1], np.uint8)
    # 1/255 * 0.2 < 0.001; 1/255 * 0.27 > 0.001. Test both
    # sides rather than assuming the host's equality boundary.
    below = rasterize_emission(None, [_quad(0.20, atlas)], 2, 2)
    require(not np.any(below), "fragment below the assumed cutoff survived")
    above = rasterize_emission(None, [_quad(0.27, atlas)], 2, 2)
    expected_a = 0.27 / 255.0
    expected_r = 128 / 255.0 * 0.27
    require(np.allclose(above[..., 3], expected_a, atol=2e-7),
            f"unexpected opacity, including the shared triangle edge: {above[..., 3]}")
    require(np.allclose(above[..., 0], expected_r, atol=2e-6),
            f"a single emissive fragment should resolve to tex.rgb*drawpoly_alpha: {above[..., 0]}")
    require(expected_a > DISCARD_W, "bad fixture: above-cutoff sample is not above threshold")
    # Two equal slices: verify the equal-depth WBOIT formula and no duplicate
    # fragments along the triangulation diagonal.
    twice = rasterize_emission(None, [_quad(0.27, atlas), _quad(0.27, atlas)], 2, 2)
    opacity = 1.0 - (1.0 - expected_a) ** 2
    expected = opacity * (128 / 255.0) * 255.0
    require(np.allclose(twice[..., 3], opacity, atol=2e-7), "wrong two-slice reveal")
    require(np.allclose(twice[..., 0], expected, atol=2e-5), "wrong two-slice weighted emission")
    over = rasterize_emission(None, [_quad(0.27, atlas), _quad(0.27, atlas)], 2, 2, mode="over")
    require(np.allclose(over[..., 0], expected_r * (2 - expected_a), atol=2e-6), "wrong over emission")
    return {"discard_assumption": DISCARD_W, "one_slice_alpha": expected_a,
            "one_slice_red": round(expected_r, 5)}


@check("raster: each drawpoly uses its own object-buffer snapshot", tags=["runtime", "core"])
def draw_snapshot():
    host = Host(script(), [], HostConfig(screen_w=2, screen_h=2), shader_lib=None)
    require(host._alloc("object", 2, 2), "could not allocate first buffer")
    host.buffers["object"][:] = [128, 0, 0, 255]
    call = ("obj.drawpoly({{-1,-1,0,.5,.5},{1,-1,0,.5,.5},"
            "{1,1,0,.5,.5},{-1,1,0,.5,.5}},4,0.5)")
    host.lua.execute(call)
    host.buffers["object"][:] = [0, 128, 0, 255]
    host.lua.execute(call)
    host.buffers["object"][:] = [0, 0, 128, 255]
    require(np.array_equal(host.quads[0]["atlas"][0, 0], [128, 0, 0, 255]),
            "first draw lost its red atlas")
    require(np.array_equal(host.quads[1]["atlas"][0, 0], [0, 128, 0, 255]),
            "second draw lost its green atlas")
    out = rasterize_emission(None, host.quads, 2, 2)
    require(out[0, 0, 0] > 0 and out[0, 0, 1] > 0 and out[0, 0, 2] == 0,
            f"snapshots were not both sampled: {out[0, 0]}")
    return {"draws": 2, "snapshot_count": 2}


@check("hlsl: hexadecimal integer literals execute with correct type and value", tags=["hlsl", "core"])
def hex_literals():
    shader = '''float4 hexprobe(float4 pos : SV_Position) : SV_Target {
        uint a = 0x5bd1u;
        uint b = 0x1b87u;
        uint x = (a ^ b) & 255u;
        int y = 0x1f;
        int z = 0xfe;
        return float4(float(x) / 255.0, float(y) / 255.0, float(z) / 255.0, 1.0);
    }'''
    lib = ShaderLibrary({"hexprobe": shader})
    px = lib.run("hexprobe", [], size=(1, 1))[0, 0].tolist()
    require(px == [86, 31, 254, 255], f"incorrect hexadecimal output: {px}")
    return {"rgba": px}


# ---------------------------------------------------------------------------
# prox 编码：Lua / C++ / 两份 HLSL / 内嵌 CS 一共五处副本，数值必须完全一致
# ---------------------------------------------------------------------------
_CONSTS_LUA = REPO_ROOT / "src" / "VolumetricLight3D" / "modules" / "00a_consts.lua"
_PROX_HDR = REPO_ROOT / "src" / "VL3DBridge" / "src" / "vl3d_bridge_prox.h"
_PROX_CPP = REPO_ROOT / "src" / "VL3DBridge" / "src" / "vl3d_bridge_rast.cpp"
_PROX_CS = REPO_ROOT / "src" / "VL3DBridge" / "src" / "vl3d_bridge_shaders.h"
_PROX_HLSL = [REPO_ROOT / "src" / "VolumetricLight3D" / "shaders" / name
              for name in ("beamatlas.hlsl", "ps_vl_lfpack.hlsl")]
_LUA_PROX = REPO_ROOT / "src" / "VolumetricLight3D" / "modules" / "12_bridge_raster.lua"

# 逻辑名 → 各语言里的标识符
_PROX_NAMES = {
    "STEP": ("kStep", "VL_PROX_STEP"),
    "BASE_MESH": ("kBaseMesh", "VL_PROX_BASE_MESH"),
    "BASE_CHAR": ("kBaseChar", "VL_PROX_BASE_CHAR"),
    "FLAG": ("kFlag", "VL_PROX_FLAG"),
    "CHAN": ("kChan", "VL_PROX_CHAN"),
    "OFF_R": ("kOffR", "VL_PROX_OFF_R"),
    "OFF_G": ("kOffG", "VL_PROX_OFF_G"),
    "OFF_B": ("kOffB", "VL_PROX_OFF_B"),
}


def _lua_prox() -> dict:
    from vl3dsb.consts import lua_consts

    table = lua_consts()["PROX"]
    return {k: float(table[k]) for k in _PROX_NAMES}


def _grab(path, ident: str, pattern: str) -> float:
    require(path.exists(), f"missing {path}")
    m = re.search(pattern.format(ident=re.escape(ident)), path.read_text(encoding="utf-8"))
    require(m is not None, f"{ident} not found in {path.name}")
    return float(m.group(1))


def _cpp_prox() -> dict:
    # constexpr float kStep = 0.48f;   また static_assert のため、末尾の f を落とす
    return {k: _grab(_PROX_HDR, names[0], r"constexpr\s+float\s+{ident}\s*=\s*([-0-9.eE+]+)f?\s*;")
            for k, names in _PROX_NAMES.items()}


def _hlsl_prox(path) -> dict:
    """HLSL 侧只声明自己用得到的那几个常量（解码侧不需要 CHAN / OFF_*）。"""
    require(path.exists(), f"missing {path}")
    text = path.read_text(encoding="utf-8")
    out = {}
    for k, (_, ident) in _PROX_NAMES.items():
        # HLSL 侧用 `const float`（不用 static：FXC 会走 lazy-init 代码路径，见 checks_03）
        m = re.search(rf"(?:static\s+)?const\s+float\s+{re.escape(ident)}\s*=\s*([-0-9.eE+]+)\s*;",
                      text)
        if m is not None:
            out[k] = float(m.group(1))
    for need in ("STEP", "BASE_MESH", "BASE_CHAR", "FLAG"):
        require(need in out, f"{path.name} does not declare {_PROX_NAMES[need][1]}")
    return out


# 编解码代码窗口：窗口内不许再出现裸字面量（声明行在窗口外）
_CODEC_WINDOW = {
    "beamatlas.hlsl": ("float raw = (c.r + c.g + c.b)", "z_val = (1.0 - prox) * max_z;"),
    "ps_vl_lfpack.hlsl": ("float raw = (cm.r + cm.g + cm.b)", "float z_3d = (1.0 - prox) * max_z;"),
    "vl3d_bridge_shaders.h": ("float code = pr;", "g_rgba_buf[idx] ="),
    "vl3d_bridge_rast.cpp": ("float code = is_camdepth", "rgba[di + 2]"),
}


def _codec_window(path, text: str) -> str:
    anchors = _CODEC_WINDOW.get(path.name)
    require(anchors is not None, f"no codec window registered for {path.name}")
    start = text.find(anchors[0])
    end = text.find(anchors[1], start)
    require(start >= 0 and end > start, f"codec window anchors not found in {path.name}")
    return text[start:end]


@check("raster: prox depth codec is identical in Lua / C++ / HLSL and round-trips",
       tags=["runtime", "contract", "core"])
def prox_codec_single_source():
    """camocc 深度用「三通道移位拷贝」写进 RGBA8，五处实现必须逐位一致。

    这个用例把 §8.4(6) 的口头契约变成可执行断言：
      * 五处常量必须相等（任一处漂移 → 遮挡距离整体偏移且不报错）；
      * 编解码体内不许再有裸字面量 0.48 / 0.51 / 0.01；
      * 编码 → 8bit 量化 → 解码的往返误差 ≤ 1/255（协议本身能还原）。
    """
    lua = _lua_prox()
    cpp = _cpp_prox()
    sources = {"C++ header": cpp}
    for path in _PROX_HLSL:
        sources[path.name] = _hlsl_prox(path)
    sources[_PROX_CS.name] = _hlsl_prox(_PROX_CS)

    require(set(cpp) == set(_PROX_NAMES), f"C++ header is missing prox constants: "
                                         f"{sorted(set(_PROX_NAMES) - set(cpp))}")
    for k, val in lua.items():
        for label, table in sources.items():
            if k not in table:
                continue        # HLSL 解码侧只声明自己用得到的常量
            require(abs(table[k] - val) < 1e-9,
                    f"prox.{k} drifted in {label}: {table[k]!r} != Lua {val!r}")

    # 编解码窗口里不许再有裸字面量；同时必须真的引用了命名常量
    for path in _PROX_HLSL + [_PROX_CS, _PROX_CPP]:
        window = _codec_window(path, path.read_text(encoding="utf-8"))
        for lit in ("0.48", "0.51", "0.01", "3.0"):
            require(lit not in window,
                    f"{path.name}: naked {lit} left inside the prox codec body")
        require("VL_PROX_" in window if path.suffix == ".hlsl" or path.name == _PROX_CS.name
                else "prox::k" in window,
                f"{path.name}: prox codec body does not use the named constants")
    lua_text = _LUA_PROX.read_text(encoding="utf-8")
    for lit in ("* 0.48", "+ 0.51", "+ 0.01", "* 3.0"):
        require(lit not in lua_text, f"12_bridge_raster.lua: naked {lit!r} left in the prox codec")
    require(lua_text.count("K.PROX.") >= 8,
            "12_bridge_raster.lua no longer reads the prox codec from K.PROX")

    # 往返：code → 三通道 8bit → raw=(r+g+b)/3 → 解回 prox
    step, mesh, char, flag = lua["STEP"], lua["BASE_MESH"], lua["BASE_CHAR"], lua["FLAG"]
    worst = 0.0
    tested = 0
    for base in (mesh, char):
        for i in range(0, 65):
            v = i / 64.0
            code = v * step + base
            chans = []
            for off in (lua["OFF_R"], lua["OFF_G"], lua["OFF_B"]):
                x = min(1.0, max(0.0, code * lua["CHAN"] - off))
                chans.append(round(x * 255.0) / 255.0)   # 8bit 量化
            raw = sum(chans) / 3.0
            dec = (raw - char) / step if raw > flag else (raw - mesh) / step
            worst = max(worst, abs(dec - v))
            tested += 1
    require(worst <= 1.0 / 255.0,
            f"prox round-trip error {worst:.6f} exceeds 8bit quantization (1/255)")

    return {"step": step, "flag": flag, "copies": ["lua", "c++", *sources.keys()],
            "roundtrip_max_err": round(worst, 6), "roundtrip_samples": tested}
