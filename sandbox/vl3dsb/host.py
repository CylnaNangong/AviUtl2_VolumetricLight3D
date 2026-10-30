"""
AviUtl ExEdit2 script-host emulator (LuaJIT + Python).

The `.obj2` custom object script is executed verbatim inside LuaJIT 2.1
(`lupa.luajit21`, which provides the real `ffi` module).  Everything the script
can reach through the host is emulated here:

  obj.getvalue / getinfo / getoption / setoption / module
  obj.load("layer" | "text") / getpixel
  RGBA image caches (object / framebuffer / tempbuffer / cache:*)
  obj.getpixeldata / putpixeldata (real FFI pointers into numpy buffers)
  obj.pixelshader (executed by the CPU HLSL executor)
  obj.drawpoly / obj.draw (recorded and software-rasterised)
  obj.node-wide helpers used by the script's own fallbacks

Aim: exercise script logic (parameter handling, caching, occlusion decisions,
error paths). Host rendering is only approximated: raster.py provides a legacy
RGBA8 preview and two explicit floating-point models, none of which implements
AviUtl2's actual 3D compositor, lighting, or depth weighting.
"""
from __future__ import annotations

import math
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .obj2 import Obj2Script, parse_obj2
from .hlsl.executor import ShaderLibrary, extract_shaders


# ---------------------------------------------------------------------------
# Scene description
# ---------------------------------------------------------------------------
@dataclass
class Layer:
    index: int
    image: Optional[np.ndarray] = None      # (h,w,4) uint8, PREMULTIPLIED
    kind: str = "image"                     # image | text | obj | vrm | water | camera | empty
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    zoom: float = 100.0                     # percent, as AviUtl2 reports it
    alpha: float = 1.0
    # AviUtl2 has no per-axis scale / centre items; the script probes them and
    # falls back to zoom+aspect when they are nil.  Set them only to emulate a
    # host that does expose them.
    sx: Optional[float] = None
    sy: Optional[float] = None
    sz: Optional[float] = None
    cx: Optional[float] = None
    cy: Optional[float] = None
    cz: Optional[float] = None
    effects: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    model_path: str = ""
    coord_adj: int = 0
    water_color: int = -1
    start: int = 0
    end: int = 10 ** 9
    enabled: bool = True
    fx_name: str = ""
    raw_image: Optional[np.ndarray] = None  # (h,w,4) uint8 when effect=False differs from image

    @property
    def w(self) -> int:
        return 0 if self.image is None else self.image.shape[1]

    @property
    def h(self) -> int:
        return 0 if self.image is None else self.image.shape[0]


@dataclass
class HostConfig:
    screen_w: int = 640
    screen_h: int = 360
    cache_pixels: int = 16_000_000       # AviUtl2 TemporaryImageCacheSize default
    max_buffer_dim: int = 8192
    gui: bool = True                     # obj.getoption("gui"): False while exporting
    camera_mode: int = 0                 # 0 = 2D, 1 = 3D camera, 2 = edit camera
    camera: Optional[Dict[str, float]] = None
    bridge: bool = True                  # emulate VL3DBridge.aux2 (C++ CPU rasterizer)
    project_path: str = ""
    script_path: str = ""
    fake_clock: Optional[float] = None   # deterministic vl_wall_time_sec()
    inject_test_hooks: bool = True       # expose _G.VL3D_TEST_* hooks the script documents
    restricted_env: bool = False         # emulate the official script sandbox (os == nil, io == nil)
    # `obj.copybuffer(dst,src)` IS part of the documented API
    # (https://docs.aviutl2.jp/lua/ § obj.copybuffer), so it is on by default;
    # set it to False to emulate hosts/scripts without it (the script's
    # `type(obj.copybuffer) ~= "function"` guard then takes the obj.load path).
    copybuffer_supported: bool = True
    image_max: Tuple[int, int] = (1920, 1080)
    version: str = "2.1.15"             # obj.getinfo("version")


# ---------------------------------------------------------------------------
# Lua prelude: builds a *Lua* `obj` table (so type(obj.load) == "function"),
# forwards everything to the Python Host.call dispatcher.
# ---------------------------------------------------------------------------
LUA_PRELUDE = r"""
local host_call = host_call
obj = {}
local function H(name, ...) return host_call(name, ...) end

function obj.getvalue(...) return H("getvalue", ...) end
function obj.getinfo(...) return H("getinfo", ...) end
function obj.getoption(...) return H("getoption", ...) end
function obj.setoption(...) return H("setoption", ...) end
function obj.setfont(...) return H("setfont", ...) end
function obj.setanchor(...) return H("setanchor", ...) end
function obj.load(...) return H("load", ...) end
function obj.getpixel(...) return H("getpixel", ...) end
function obj.copybuffer(...) return H("copybuffer", ...) end
function obj.clearbuffer(...) return H("clearbuffer", ...) end
function obj.pixelshader(...) return H("pixelshader", ...) end
function obj.drawpoly(...) return H("drawpoly", ...) end
function obj.draw(...) return H("draw", ...) end

local ok_ffi, ffi = pcall(require, "ffi")   -- same expression the script uses (AviUtl2 ships LuaJIT)
function obj.getpixeldata(name, fmt)
    local a, w, h = H("getpixeldata", name, fmt)
    if a == nil or a == 0 then return nil, 0, 0 end
    if not ok_ffi then return nil, 0, 0 end
    return ffi.cast("uint8_t*", a), w, h
end
function obj.putpixeldata(name, ptr, w, h, fmt)
    local addr = 0
    if ok_ffi and ptr ~= nil then addr = tonumber(ffi.cast("uintptr_t", ptr)) end
    return H("putpixeldata", name, addr, w, h, fmt)
end

local function __vl3d_ptr(p)
    if p == nil then return 0 end
    if type(p) == "number" then return p end
    if ok_ffi then
        local ok, v = pcall(function() return tonumber(ffi.cast("uintptr_t", p)) end)
        if ok and v ~= nil then return v end
    end
    return 0
end

-- bridge module table (type(br.resolve_layer) == "function")
local br_tbl = nil
if host_call("bridge_available") then
    br_tbl = {}
    local function B(name) return function(...) return H(name, ...) end end
    br_tbl.resolve_layer        = B("br_resolve_layer")
    br_tbl.get_water_color      = B("br_get_water_color")
    br_tbl.get_edit_camera      = B("br_get_edit_camera")
    br_tbl.get_scene_camera     = B("br_get_scene_camera")
    br_tbl.get_camera_lights    = B("br_get_camera_lights")
    br_tbl.get_status           = B("br_get_status")
    br_tbl.wall_time_sec        = B("br_wall_time_sec")
    br_tbl.has_mesh             = B("br_has_mesh")
    br_tbl.upload_mesh          = B("br_upload_mesh")
    br_tbl.rasterize_shadow     = function(ptr, res, params, insts)
        return H("br_rasterize_shadow", __vl3d_ptr(ptr), res, params, insts)
    end
    br_tbl.rasterize_camdepth   = function(ptr, cw, ch, params, insts)
        return H("br_rasterize_camdepth", __vl3d_ptr(ptr), cw, ch, params, insts)
    end
end
function obj.module(name)
    if name == "VL3DBridge" then return br_tbl end
    return nil
end

function __vl3d_sync(fields)
    for k, v in pairs(fields) do obj[k] = v end
end

function __vl3d_dir_listing(dir, pattern)
    local rows = host_call("dir_listing", dir, pattern)
    local out = {}
    if type(rows) ~= "table" then return out end
    for i = 1, #rows do
        local r = rows[i]
        out[#out + 1] = { path = r[1], name = r[2], mtime = r[3] }
    end
    return out
end
"""


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------
class Host:
    def __init__(self, script: Obj2Script, layers: Sequence[Layer], config: Optional[HostConfig] = None,
                 shader_lib: Optional[ShaderLibrary] = None, params: Optional[Dict[str, Any]] = None,
                 native=None):
        self.script = script
        self.layers: List[Layer] = list(layers)
        self.cfg = config or HostConfig()
        self.params = dict(script.defaults())
        if params:
            self.params.update(params)
        self.buffers: Dict[str, np.ndarray] = {}
        self.peak_cache_pixels = 0
        self.prints: List[str] = []
        self.draw_calls: List[dict] = []
        self.quads: List[dict] = []
        self.setoptions: Dict[str, Any] = {}
        self.pixel_uploads = 0
        self.pixel_downloads = 0
        self.shader_calls: List[str] = []
        self.pixelshader_failures = 0
        self.shader_param_mismatches: List[dict] = []
        # last successful call per shader (uniforms + texture snapshots) - used by
        # the shader checks to replay real uniform sets outside the full pipeline
        self.shader_traces: Dict[str, dict] = {}
        self.frame = 0
        self.frame_s = 0
        self.frame_e = 10 ** 9
        self.scene_id = 0
        self.time = 0.0
        self.layer_index = 0
        self.text_image: Optional[np.ndarray] = None
        # obj.setfont(name,size[,type,col1,col2,bold,italic,charspacing,linespacing])
        self.font = {"name": "", "size": 18, "type": 0, "col1": 0xFFFFFF,
                     "col2": 0x000000, "bold": False, "italic": False,
                     "charspacing": 0.0, "linespacing": 0.0}
        self._start = time.time()
        self.native = native
        self._shader_lib = shader_lib
        self._shader_cache: Dict[str, ShaderLibrary] = {}
        self.errors: List[str] = []
        self._create_runtime()

    # -- runtime -------------------------------------------------------------
    def _create_runtime(self):
        from lupa.luajit21 import LuaRuntime
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g["host_call"] = self.call
        g["print"] = self._lua_print
        self.lua.execute(LUA_PRELUDE)
        g["_VL3D_DIR_LISTING_HOOK"] = True
        for k, v in self.params.items():
            g[k] = v
        if self.cfg.inject_test_hooks:
            g["VL3D_TEST_DIR_LISTING"] = self.lua.eval("__vl3d_dir_listing")
            if self.cfg.fake_clock is not None:
                fake = float(self.cfg.fake_clock)
                self.lua.execute(
                    "_G.VL3D_TEST_BRIDGE = { wall_time_sec = function() return %r end }" % fake)
        if self.cfg.restricted_env:
            self.lua.execute("os = nil; io = nil; debug = nil")
        # deterministic-ish time for animations
        self.sync_fields()

    def _lua_print(self, *args):
        msg = "\t".join(str(a) for a in args)
        self.prints.append(msg)

    def _obj_span(self) -> int:
        """Object length in frames (`obj.totalframe`).

        `session.run_frame` uses `frame_e = 10**9` as "unbounded"; a real
        AviUtl2 object always has a finite length, so the sentinel maps to a
        deterministic 10 s @ 30 fps instead of leaking a huge number into the
        script.
        """
        span = int(self.frame_e - self.frame_s)
        return 300 if span >= 10 ** 8 else max(0, span)

    def sync_fields(self):
        _obj = self.buf("object")
        obj_w, obj_h = (0, 0) if _obj is None else (_obj.shape[1], _obj.shape[0])
        fields = {
            "frame": self.frame,
            "frame_s": self.frame_s,
            "frame_e": self.frame_e,
            "scene_id": self.scene_id,
            "time": self.time,
            "layer": self.layer_index,
            "x": 0.0, "y": 0.0, "z": 0.0,
            "rx": 0.0, "ry": 0.0, "rz": 0.0,
            "zoom": 1.0, "alpha": 1.0,
            "ox": 0.0, "oy": 0.0, "oz": 0.0,
            "cx": 0.0, "cy": 0.0,
            "screen_w": float(self.cfg.screen_w),
            "screen_h": float(self.cfg.screen_h),
            # remaining documented read-only fields (docs § 変数)
            "aspect": 0.0, "sx": 1.0, "sy": 1.0, "sz": 1.0, "cz": 0.0,
            "w": float(obj_w), "h": float(obj_h),
            "framerate": 30.0,
            "totalframe": float(self._obj_span()),
            "totaltime": float(self._obj_span()) / 30.0,
            "index": 1.0, "num": 1.0,
            "id": float(self.layer_index * 1000 + 1),
            "effect_id": float(self.layer_index * 1000 + 1),
            "effect_layer": float(self.layer_index),
            "originframe": 0.0,
        }
        self.lua.eval("__vl3d_sync")(self.lua.table_from(fields))

    # -- buffers -------------------------------------------------------------
    def _norm_name(self, name: str) -> str:
        return str(name)

    def _alloc(self, name: str, w: int, h: int, clear: bool = True) -> bool:
        name = self._norm_name(name)
        w, h = int(w), int(h)
        if w <= 0 or h <= 0:
            return False
        if w > self.cfg.max_buffer_dim or h > self.cfg.max_buffer_dim:
            return False
        # AviUtl2's TemporaryImageCacheSize pool tracks cache:* and tempbuffer;
        # the active "object" and "framebuffer" render targets live outside it.
        is_temp_cache = name not in ("object", "framebuffer")
        other = sum(
            b.shape[0] * b.shape[1]
            for n, b in self.buffers.items()
            if n != name and n not in ("object", "framebuffer")
        )
        if is_temp_cache and other + w * h > self.cfg.cache_pixels:
            return False
        img = np.zeros((h, w, 4), np.uint8) if clear else np.zeros((h, w, 4), np.uint8)
        self.buffers[name] = img
        if is_temp_cache:
            self.peak_cache_pixels = max(self.peak_cache_pixels, other + w * h)
        return True

    def buf(self, name: str) -> Optional[np.ndarray]:
        return self.buffers.get(self._norm_name(name))

    # -- shader --------------------------------------------------------------
    def shader_lib(self) -> Optional[ShaderLibrary]:
        if self._shader_lib is not None:
            return self._shader_lib
        return None

    def _run_shader(self, name: str, target: str, inputs: List[str], params: Sequence[Any],
                    sampler: str) -> bool:
        lib = self.shader_lib()
        if lib is None:
            self.pixelshader_failures += 1
            return False
        dst = self.buf(target)
        if dst is None:
            self.pixelshader_failures += 1
            return False
        info = lib.info.get(name)
        if info is None:
            self.pixelshader_failures += 1
            self.prints.append(f"@error unknown pixelshader {name}")
            return False
        texs = []
        for src in (info.textures if not inputs else inputs):
            b = self.buf(src) if isinstance(src, str) else src
            if b is None:
                b = np.zeros((1, 1, 4), np.uint8)
            texs.append(np.array(b, copy=True))  # snapshot (no aliasing)
        # pad/truncate to the number of textures the shader declares
        want = len(info.textures)
        texs = (texs + [np.zeros((1, 1, 4), np.uint8)] * want)[:want]
        if info.cb_floats and len(params) != info.cb_floats:
            self.shader_param_mismatches.append(
                {"shader": name, "lua_floats": len(params), "hlsl_floats": info.cb_floats})
        filt, addr = (0, 0) if str(sampler).lower() in ("point", "clip_point") else (1, 0)
        try:
            out = lib.run(name, params, texs, out_hw=(dst.shape[0], dst.shape[1]),
                          filter=filt, address=addr)
        except Exception as exc:  # pragma: no cover - defensive
            self.pixelshader_failures += 1
            self.prints.append(f"@error pixelshader {name}: {exc}")
            return False
        self.buffers[self._norm_name(target)] = out
        self.shader_calls.append(name)
        self.shader_traces[name] = {
            "params": [float(x) for x in params],
            "textures": [np.array(t, copy=True) for t in texs],
            "target": target,
            "size": (out.shape[1], out.shape[0]),
        }
        return True

    # -- dispatcher ----------------------------------------------------------
    def call(self, fn: str, *args):
        try:
            return getattr(self, "_api_" + fn)(*args)
        except Exception as exc:
            self.errors.append(f"{fn}{args[:2]}: {exc}\n{traceback.format_exc(limit=3)}")
            raise

    # ---- obj.getvalue ------------------------------------------------------
    _ITEM_ALIASES = {
        "文字揃え": {"文字揃え", "文字对齐", "Text Align", "Textausrichtung"},
        "文字装飾": {"文字装飾", "文字装饰", "Text Deco"},
        "サイズ": {"サイズ", "大小", "Size", "Größe"},
        "ファイル": {"ファイル", "文件", "File"},
        "モデルファイル": {"モデルファイル", "模型文件", "Model File"},
    }

    def _fx_item(self, layer: Layer, fx_name: str, item: str):
        names = {fx_name}
        for canon, alts in self._ITEM_ALIASES.items():
            if item in alts or item == canon:
                names.add(item)
                item_alts = alts
                break
        else:
            item_alts = {item}
        for fx, items in layer.effects.items():
            if fx_name and fx != fx_name:
                continue
            for k, v in items.items():
                if k in item_alts:
                    return v
        return None

    def _api_getvalue(self, *args):
        if not args:
            return None
        first = args[0]
        if isinstance(first, float) and float(first).is_integer():
            first = int(first)
        if isinstance(first, (int,)) and len(args) >= 2:
            layer = self._layer(first)
            if layer is None:
                return None
            if len(args) >= 3:
                return self._fx_item(layer, str(args[1]), str(args[2]))
            return self._fx_item(layer, "", str(args[1]))
        if isinstance(first, bytes):
            first = first.decode("utf-8", "replace")
        key = str(first)
        if key.startswith("layer"):
            rest = key[len("layer"):]
            parts = rest.split(".")
            try:
                idx = int(parts[0])
            except ValueError:
                return None
            layer = self._layer(idx)
            if layer is None:
                return False
            if len(parts) == 1:
                return True
            item = parts[1]
            vals = {
                "x": layer.x, "y": layer.y, "z": layer.z,
                "rx": layer.rx, "ry": layer.ry, "rz": layer.rz,
                "zoom": layer.zoom, "aspect": 0.0,
                "alpha": layer.alpha,
                "time": self.time,
                # only reported when the scene builder actually sets them
                "sx": layer.sx, "sy": layer.sy, "sz": layer.sz,
                "cx": layer.cx, "cy": layer.cy, "cz": layer.cz,
            }
            return vals.get(item)
        if key == "frame_s":
            return float(self.frame_s)
        if key == "time":
            return self.time
        if key == "layer":
            return float(self.layer_index)
        return None

    def _layer(self, idx: int, scene_frame: Optional[int] = None) -> Optional[Layer]:
        # Layer start/end describe the scene timeline, whereas obj.frame is
        # relative to this effect's start. Bridge lookups can request a
        # different explicit scene frame; regular obj.load/getvalue use now.
        frame = self.frame_s + self.frame if scene_frame is None else int(scene_frame)
        for lay in self.layers:
            if lay.index == idx and lay.enabled and lay.start <= frame <= lay.end:
                return lay
        return None

    def _api_getinfo(self, key):
        """obj.getinfo(name) -- keys per https://docs.aviutl2.jp/lua/ § obj.getinfo."""
        if isinstance(key, bytes):
            key = key.decode()
        if key in ("script_path", "project_path"):
            # project_path is not part of the documented key list; it is kept
            # because the script probes it defensively.
            return (self.cfg.script_path or str(self.script.path)) if key == "script_path" \
                else (self.cfg.project_path or None)
        if key == "image_max":
            # docs: max_x, max_y = obj.getinfo("image_max")  (TWO values)
            mx, my = self.cfg.image_max
            return int(mx), int(my)
        if key == "clock":
            # docs: seconds since the application started (performance counter)
            return float(self.cfg.fake_clock) if self.cfg.fake_clock is not None \
                else float(1.0 + self.time)
        if key == "script_time":
            return 0.0
        if key == "version":
            return self.cfg.version
        if key in ("filter", "saving", "reference_frame"):
            return False
        if key == "bpm":
            return 120.0, 4.0, 0.0
        if key == "bpm_list":
            return self.lua.table_from([self.lua.table_from(
                {"tempo": 120.0, "beat": 4.0, "start": 0.0, "offset": 0.0})])
        if key == "frame_max":
            finite = [lay.end for lay in self.layers if lay.end < 10 ** 8]
            return max(finite) if finite else int(self.frame_s + self._obj_span())
        if key == "layer_max":
            return max((lay.index for lay in self.layers), default=0)
        if key in ("origin_scene_id", "rendering_id"):
            return int(self.scene_id)
        return None

    def _api_getoption(self, key):
        if isinstance(key, bytes):
            key = key.decode()
        if key == "gui":
            return bool(self.cfg.gui)
        if key == "camera_mode":
            return float(self.cfg.camera_mode)
        if key == "camera_param":
            if self.cfg.camera is None:
                return None
            return self.lua.table_from(self.cfg.camera)
        return None

    def _api_setoption(self, key, value=None):
        if isinstance(key, bytes):
            key = key.decode()
        self.setoptions[str(key)] = value
        return True

    def _api_setfont(self, name="", size=18, type=0, col1=0xFFFFFF, col2=0x000000,  # noqa: A002
                     bold=None, italic=None, charspacing=None, linespacing=None):
        """obj.setfont(name,size[,type 0-6,col1,col2,bold,italic,charspacing,linespacing]).

        The third argument is the *decoration type* documented as 0..6 -- in an
        earlier version of this fake it was mislabelled "weight" (there is no
        weight parameter in the AviUtl2 API).
        """
        self.font = {
            "name": str(name), "size": int(size), "type": int(type),
            "col1": int(col1), "col2": int(col2),
            "bold": bool(bold), "italic": bool(italic),
            "charspacing": float(charspacing or 0.0),
            "linespacing": float(linespacing or 0.0),
        }
        return True

    def _api_setanchor(self, name, num=0, *options):
        """obj.setanchor(name,num[,option,...]) -> number of anchor points."""
        if isinstance(name, bytes):
            name = name.decode()
        try:
            return int(num)
        except (TypeError, ValueError):
            return 0

    # ---- buffers -----------------------------------------------------------
    def _api_clearbuffer(self, name, w=1, h=1):
        return bool(self._alloc(str(name), w, h))

    def _api_copybuffer(self, dst, src):
        s = self.buf(str(src))
        if s is None:
            return False
        if not self.cfg.copybuffer_supported:
            return False
        d = self.buf(str(dst))
        if d is None or d.shape != s.shape:
            if not self._alloc(str(dst), s.shape[1], s.shape[0]):
                return False
        self.buffers[self._norm_name(str(dst))] = np.array(s, copy=True)
        return True

    def _api_getpixel(self, x=None, y=None, type=None):  # noqa: A002
        """obj.getpixel([x,y[,type]]) -- docs § obj.getpixel.

        No arguments returns the object size (w,h).  With coordinates it
        returns `col,alpha` / `r,g,b,a` / `y,cb,cr,a` depending on the type
        (`"col"` default).  Buffers are premultiplied internally, so the RGB
        channels are un-premultiplied before they are reported.
        """
        b = self.buf("object")
        kind = str(type or "col")
        if b is None:
            if x is None:
                return 0, 0
            return (0, 0.0) if kind == "col" else (0, 0, 0, 0)
        if x is None or y is None:
            return b.shape[1], b.shape[0]
        px, py = int(x), int(y)
        if not (0 <= px < b.shape[1] and 0 <= py < b.shape[0]):
            return (0, 0.0) if kind == "col" else (0, 0, 0, 0)
        r8, g8, b8, a8 = (int(v) for v in b[py, px])
        alpha = a8 / 255.0
        if a8:
            r8 = min(255, int(round(r8 * 255.0 / a8)))
            g8 = min(255, int(round(g8 * 255.0 / a8)))
            b8 = min(255, int(round(b8 * 255.0 / a8)))
        if kind == "rgb":
            return r8, g8, b8, a8
        if kind == "yc":
            yy = 0.299 * r8 + 0.587 * g8 + 0.114 * b8
            cb = 128.0 - 0.168736 * r8 - 0.331264 * g8 + 0.5 * b8
            cr = 128.0 + 0.5 * r8 - 0.418688 * g8 - 0.081312 * b8
            return float(yy), float(cb), float(cr), a8
        return (r8 << 16) | (g8 << 8) | b8, alpha

    def _api_getpixeldata(self, name="object", fmt="rgba"):
        b = self.buf(str(name))
        if b is None or b.size == 0:
            return None, 0, 0
        self.pixel_downloads += 1
        return int(b.ctypes.data), b.shape[1], b.shape[0]

    def _api_putpixeldata(self, name, addr, w=0, h=0, fmt="rgba"):
        b = self.buf(str(name))
        if b is None:
            return False
        if int(w) and int(h) and (b.shape[1] != int(w) or b.shape[0] != int(h)):
            return False
        self.pixel_uploads += 1
        return True

    def _api_load(self, kind, arg=None, effect=None, size=None):
        """obj.load(kind,...) -- docs § obj.load.

        Handles the documented call shapes: `("text",txt)`,
        `("layer",n)`, `("text.layout",txt,col,size?)`-style extra arguments are
        accepted (never a Python TypeError) and the kinds this sandbox does not
        emulate return `False`, exactly like a host that cannot load them.
        """
        kind = kind.decode() if isinstance(kind, bytes) else str(kind)
        if kind == "text":
            self._render_text(str(arg))
            return True
        if kind != "layer":
            return False
        idx = int(arg)
        layer = self._layer(idx)
        if layer is None or (layer.image is None and layer.raw_image is None):
            return False
        if not bool(effect) and layer.raw_image is not None:
            img = layer.raw_image
        else:
            img = layer.image if layer.image is not None else layer.raw_image
        if layer.fx_name and bool(effect) and self._fx_bug(layer):
            alpha = np.full(img.shape[:2], 255, np.uint8)
            img = np.concatenate([img[..., :3], alpha[..., None]], axis=-1)
        self.buffers["object"] = np.array(img, copy=True)
        return True

    def _fx_bug(self, layer: Layer) -> bool:
        """Simulate the documented obj.load(..., true) 'opaque rectangle' host bug."""
        return bool(layer.effects.get("__fx_opaque_bug__", {}).get("on", False))

    def _render_text(self, text: str):
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.load_default()
        lines = text.split("\n") or [""]
        line_h = 12
        w = max(8, max(len(ln) for ln in lines) * 6 + 4)
        h = max(8, line_h * len(lines) + 4)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        col = self.font.get("col1", 0xFFFFFF)
        rgb = ((col >> 16) & 255, (col >> 8) & 255, col & 255, 255)
        for i, ln in enumerate(lines):
            dr.text((2, 2 + i * line_h), ln, fill=rgb, font=font)
        arr = np.asarray(im, np.uint8).copy()
        # premultiply (AviUtl2 buffers are premultiplied RGBA)
        a = arr[..., 3:4].astype(np.float32) / 255.0
        arr[..., :3] = np.clip(arr[..., :3].astype(np.float32) * a, 0, 255).astype(np.uint8)
        self.text_image = arr
        self.buffers["object"] = np.array(arr, copy=True)
        self.text_content = text

    # ---- drawing -----------------------------------------------------------
    def _api_drawpoly(self, verts, second=None, third=None):
        """obj.drawpoly(...) -- both documented shapes.

        * `obj.drawpoly({x,y,z,u,v}, ..., vertex_num, alpha)`  (vertex tables)
        * `obj.drawpoly(flat_numbers[, alpha])`                 (flat 20 floats)

        `vertex_num` is 4 (quads, default) or 3 (triangles); mixing tuple
        lengths in one call is rejected by the host (docs: 異なる形式を混在).
        """
        try:
            count = len(verts)
        except TypeError:
            return False
        if count == 0:
            return True
        first = verts[1]
        prims = []
        if isinstance(first, (int, float)):
            # flat form: groups of 5 floats (x,y,z,u,v) per vertex, 4 per quad
            alpha = float(second) if isinstance(second, (int, float)) else 1.0
            flat = []
            for i in range(1, count + 1):
                v = verts[i]
                if not isinstance(v, (int, float)):
                    self.errors.append("drawpoly: mixed flat/table vertex data")
                    return False
                flat.append(float(v))
            per_vert = 5
            n_vert = len(flat) // per_vert
            if n_vert < 4:
                self.errors.append(f"drawpoly: flat form needs 4 vertices, got {n_vert}")
                return False
            for base in range(0, n_vert - 3, 4):
                prims.append([flat[(base + k) * per_vert:(base + k + 1) * per_vert]
                              for k in range(4)])
        else:
            n = int(second) if isinstance(second, (int, float)) else 4
            alpha = float(third) if isinstance(third, (int, float)) else 1.0
            if n not in (3, 4):
                self.errors.append(f"drawpoly: vertex_num must be 3 or 4, got {n}")
                return False
            widths = set()
            i = 1
            while i + n - 1 <= count:
                prim = []
                for k in range(n):
                    v = verts[i + k]
                    try:
                        row = [float(v[j]) for j in range(1, len(v) + 1)]
                    except TypeError:
                        self.errors.append("drawpoly: mixed flat/table vertex data")
                        return False
                    widths.add(len(row))
                    prim.append(row)
                prims.append(prim)
                i += n
            if len(widths) > 1:
                self.errors.append(f"drawpoly: mixed vertex tuple widths {sorted(widths)}")
                return False
        lua_obj = self.lua.globals().obj
        def _of(k: str, d: float = 0.0) -> float:
            try:
                v = lua_obj[k]
                return float(v) if isinstance(v, (int, float)) else d
            except Exception:
                return d
        obj_alpha = _of("alpha", 1.0)
        eff_alpha = max(0.0, min(1.0, float(alpha) * obj_alpha))
        obj_tf = {
            "x": _of("x"), "y": _of("y"), "z": _of("z"),
            "ox": _of("ox"), "oy": _of("oy"), "oz": _of("oz"),
            "rx": _of("rx"), "ry": _of("ry"), "rz": _of("rz"),
            "cx": _of("cx"), "cy": _of("cy"), "cz": _of("cz"),
            "zoom": _of("zoom", 1.0),
        }
        blend = str(self.setoptions.get("blend", "none"))
        billboard = int(self.setoptions.get("billboard", 0) or 0)
        sampler = str(self.setoptions.get("sampler", "clamp"))
        drawtarget = str(self.setoptions.get("drawtarget", "framebuffer"))
        self.draw_calls.append({"type": "poly", "n": len(prims), "alpha": eff_alpha, "blend": blend})
        # Each draw call uses the object image as it exists *now*. A later
        # shader dispatch may replace or clear it for the next 64-slice atlas.
        # Snapshot once per call (shared by all its quads), rather than using
        # the last atlas for every recorded slice at the end of the frame.
        texture = self.buf("object")
        atlas = None if texture is None else np.array(texture, copy=True)
        self.quads.extend([{
            "verts": q,
            "alpha": eff_alpha,
            "poly_alpha": float(alpha),
            "obj_alpha": obj_alpha,
            "obj_tf": obj_tf,
            "blend": blend,
            "billboard": billboard,
            "sampler": sampler,
            "drawtarget": drawtarget,
            "atlas": atlas,
        } for q in prims])
        return True

    def _api_draw(self, x=0, y=0, z=0, zoom=1.0, alpha=1.0, rx=0.0, ry=0.0, rz=0.0):
        """obj.draw([ox,oy,oz,zoom,alpha,rx,ry,rz]) -- docs § obj.draw."""
        self.draw_calls.append({"type": "text", "x": float(x), "y": float(y), "z": float(z),
                                "zoom": float(zoom), "alpha": float(alpha),
                                "rx": float(rx), "ry": float(ry), "rz": float(rz),
                                "text": getattr(self, "text_content", "")})
        return True

    # ---- bridge ------------------------------------------------------------
    def _api_bridge_available(self):
        return bool(self.cfg.bridge and self.native is not None)

    def _api_br_resolve_layer(self, layer_idx, frame=0):
        lay = self._layer(int(layer_idx), scene_frame=int(frame))
        if lay is None:
            return ("EMPTY", "", "", 0, self.scene_id, -1, "BRIDGE", False,
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        if lay.kind == "obj":
            status = "OBJ"
        elif lay.kind == "vrm":
            status = "VRM"
        elif lay.kind == "water":
            status = "WATER"
        else:
            status = "OTHER"
        is_txt = lay.kind == "text"
        align = lay.effects.get("テキスト", {})
        return (status, lay.fx_name or ("モデルファイル" if lay.kind == "obj" else ""),
                lay.model_path, lay.coord_adj, self.scene_id, lay.water_color,
                "BRIDGE", is_txt,
                float(align.get("文字揃え_y", 0.0)), float(align.get("文字揃え_x", 0.0)),
                float(align.get("pad_l", 0.0)), float(align.get("pad_r", 0.0)),
                float(align.get("pad_t", 0.0)), float(align.get("pad_b", 0.0)))

    def _api_br_get_water_color(self, layer_idx, frame=0):
        lay = self._layer(int(layer_idx), scene_frame=int(frame))
        return lay.water_color if lay is not None else -1

    def _api_br_get_edit_camera(self):
        cam = self.cfg.camera or {}
        return (True, cam.get("x", 0.0), cam.get("y", 0.0), cam.get("z", -1024.0),
                cam.get("tx", 0.0), cam.get("ty", 0.0), cam.get("tz", 0.0),
                0.0, -1.0, 0.0, 0.0, cam.get("d", 1024.0))

    def _api_br_get_scene_camera(self, max_layer=100, frame=0):
        cam = self.cfg.camera
        if cam is None:
            return None
        return self.lua.table_from(cam)

    def _api_br_get_camera_lights(self):
        return (0, 1.0, 0.0, 0.0, 0.0, 1.0, False, 0, 0.0, 0.0, 1.0,
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def _api_br_get_status(self):
        return self.lua.table_from({"gpu": "CPP_CPU", "sdk_ready": True, "mode": "SANDBOX"})

    def _api_br_wall_time_sec(self):
        if self.cfg.fake_clock is not None:
            return float(self.cfg.fake_clock)
        return time.time() - self._start

    def _api_br_has_mesh(self, key, rev=""):
        if self.native is None:
            return 0
        return int(self.native.has_mesh(str(key), str(rev)))

    def _api_br_upload_mesh(self, key, rev, nv, nt, vb64, tb64):
        if self.native is None:
            return 0
        return int(self.native.upload_mesh_b64(str(key), str(rev), int(nv), int(nt),
                                              _as_bytes(vb64), _as_bytes(tb64)))

    def _to_list(self, arr) -> List[float]:
        if arr is None:
            return []
        if isinstance(arr, (list, tuple)):
            return [float(x) for x in arr]
        out = []
        try:
            n = len(arr)
        except Exception:
            return []
        for i in range(1, n + 1):
            out.append(float(arr[i]))
        return out

    def _api_br_rasterize_shadow(self, dst_ptr, sh_res, params, insts):
        if self.native is None:
            return False, 0, False, "NONE"
        p = self._to_list(params)
        ins = self._to_list(insts)
        n_inst = len(ins) // 14
        ok, tris, any_px = self.native.rasterize_shadow(
            int(dst_ptr), int(sh_res), p, ins[: n_inst * 14], n_inst)
        return bool(ok), int(tris), bool(any_px), "CPP_CPU"

    def _api_br_rasterize_camdepth(self, dst_ptr, cm_w, cm_h, params, insts):
        if self.native is None:
            return False, 0, False
        p = self._to_list(params)
        ins = self._to_list(insts)
        n_inst = len(ins) // 14
        ok, tris, any_px = self.native.rasterize_camdepth(
            int(dst_ptr), int(cm_w), int(cm_h), p, ins[: n_inst * 14], n_inst)
        return bool(ok), int(tris), bool(any_px)

    def _api_dir_listing(self, dir_path, pattern="*"):
        import fnmatch
        import os
        d = str(dir_path).rstrip("/\\")
        if not d:
            return []
        out = []
        try:
            for name in os.listdir(d):
                full = os.path.join(d, name)
                if not os.path.isfile(full):
                    continue
                if pattern and pattern != "*" and not fnmatch.fnmatch(name.lower(), str(pattern).lower()):
                    continue
                try:
                    mtime = int(os.path.getmtime(full))
                except OSError:
                    mtime = 0
                out.append((full, name, mtime))
        except OSError:
            return []
        return out

    # ---- pixelshader -------------------------------------------------------
    def _api_pixelshader(self, name, target, inputs, params, blend=None, sampler=None):
        if isinstance(name, bytes):
            name = name.decode()
        target = target.decode() if isinstance(target, bytes) else str(target)
        srcs = self._to_list_of_names(inputs)
        p = self._to_list(params) if params is not None else []
        return bool(self._run_shader(str(name), target, srcs, p,
                                     sampler.decode() if isinstance(sampler, bytes) else str(sampler or "")))

    def _to_list_of_names(self, inputs):
        if inputs is None:
            return []
        out = []
        try:
            n = len(inputs)
        except TypeError:
            return []
        for i in range(1, n + 1):
            v = inputs[i]
            out.append(v.decode() if isinstance(v, bytes) else str(v))
        return out

    # ---- execution ---------------------------------------------------------
    def run_frame(self, frame: int, time_s: Optional[float] = None):
        self.frame = int(frame)
        self.time = float(time_s if time_s is not None else frame / 30.0)
        # Preserve the effect's configured scene start; obj.frame is relative.
        self.quads = []
        self.draw_calls = []
        self.sync_fields()
        try:
            self.lua.execute(self.script.text)
            ok = True
        except Exception as exc:
            ok = False
            self.errors.append(f"Lua error: {exc}")
            detail = ""
            try:
                detail = self.lua.eval("debug.traceback()")
            except Exception:
                pass
            self.errors.append(str(detail)[:4000])
        return ok

    # ---- output ------------------------------------------------------------
    def volume_layer(self) -> Optional[np.ndarray]:
        """Rasterise all recorded quads (the volume light) into RGBA uint8."""
        from .raster import rasterize_quads
        if not self.quads:
            return None
        atlas = self.buf("object")
        if atlas is None and not any(q.get("atlas") is not None for q in self.quads):
            return None
        return rasterize_quads(atlas, self.quads, self.cfg.screen_w, self.cfg.screen_h)


def _as_bytes(v) -> bytes:
    if isinstance(v, bytes):
        return v
    if isinstance(v, str):
        return v.encode()
    return bytes(v)
