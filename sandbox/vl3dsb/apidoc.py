"""
The documented AviUtl2 Lua host API (`obj.*`), transcribed from the official
manual so the sandbox can *lint* its own emulation against it.

Source: https://docs.aviutl2.jp/lua/  (AviUtl ExEdit2, checked 2026-10-29)
        https://docs.aviutl2.jp/lua/ § 設定項目 (property comments)
        https://docs.aviutl2.jp/lua/ § 変数 (obj field table)

Why a hand written table instead of scraping at test time:
  * the CI runners are offline,
  * the checks need stable *rules* (argument ranges, key sets, return arity),
  * any manual update is visible in the diff and reviewed like code.

`FUNCS` covers every `obj.<name>` the manual documents (including the ones this
project never calls) so the "surface used by the script" check can distinguish
"documented but unused" from "not part of the API".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

DOC_URL = "https://docs.aviutl2.jp/lua/"
DOC_CHECKED = "2026-10-29"


@dataclass(frozen=True)
class FuncDoc:
    name: str
    min_args: int
    max_args: Optional[int]          # None -> variadic
    returns: str = ""                # human readable, for failure messages
    note: str = ""

    def accepts(self, n: int) -> bool:
        if n < self.min_args:
            return False
        return self.max_args is None or n <= self.max_args

    def signature(self) -> str:
        hi = "..." if self.max_args is None else str(self.max_args)
        return f"{self.min_args}..{hi} args"


# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------
FUNCS: Dict[str, FuncDoc] = {f.name: f for f in [
    # --- general ---------------------------------------------------------
    FuncDoc("getvalue", 1, 4, "value|nil",
            "obj.getvalue(target[,t,section]) / (effect,item[,t,s]) / (layer,effect,item[,t,s])"),
    FuncDoc("getinfo", 1, 2, "varies", "obj.getinfo(name,...)"),
    FuncDoc("getoption", 1, 2, "varies", "obj.getoption(name,...)"),
    FuncDoc("setoption", 2, 4, "-", "obj.setoption(name,value) (+ drawtarget w,h)"),
    FuncDoc("effect", 0, None, "-", "obj.effect([name,p1,v1,...])"),
    FuncDoc("getpoint", 1, 3, "varies", "trackbar-move scripts only"),
    FuncDoc("module", 1, 1, "table|nil", "obj.module(name) -> function table"),
    FuncDoc("data", 1, 1, "ptr,size", "obj.data(name)"),
    FuncDoc("mes", 1, None, "-", "text objects only"),
    FuncDoc("multiobject", 2, 2, "-", "obj.multiobject(num,func)"),
    FuncDoc("interpolation", 8, 13, "x[,y[,z]]", "spline interpolation helper"),
    # --- drawing ---------------------------------------------------------
    FuncDoc("draw", 0, 8, "-", "obj.draw([ox,oy,oz,zoom,alpha,rx,ry,rz])"),
    FuncDoc("drawpoly", 1, 3, "-",
            "obj.drawpoly(verts[,vertex_num,alpha]) / obj.drawpoly(flat[,alpha])"),
    FuncDoc("load", 1, 6, "bool",
            "obj.load(kind,...) -- movie|image|text|figure|framebuffer|tempbuffer|layer|before"),
    FuncDoc("setfont", 2, 9, "-",
            "obj.setfont(name,size[,type 0-6,col1,col2,bold,italic,charspacing,linespacing])"),
    FuncDoc("getfont", 0, 0, "table", "obj.getfont()"),
    FuncDoc("setanchor", 2, None, "int", "obj.setanchor(name,num[,option,...])"),
    # --- buffers ---------------------------------------------------------
    FuncDoc("clearbuffer", 1, 4, "-", "obj.clearbuffer(target[,color]) / (target,w,h[,color])"),
    FuncDoc("copybuffer", 2, 2, "bool", "obj.copybuffer(dst,src)"),
    FuncDoc("getpixel", 0, 3, "(w,h)|col,alpha|rgba|ycbcra", "obj.getpixel([x,y[,type]])"),
    FuncDoc("putpixel", 4, 6, "-", "obj.putpixel(x,y,...)"),
    FuncDoc("copypixel", 4, 4, "-", "obj.copypixel(dx,dy,sx,sy)"),
    FuncDoc("pixeloption", 2, 2, "-", "obj.pixeloption(name,value)"),
    FuncDoc("getpixeldata", 1, 2, "ptr,w,h", "obj.getpixeldata(target[,format])"),
    FuncDoc("putpixeldata", 4, 5, "-", "obj.putpixeldata(target,data,w,h[,format])"),
    # --- audio / shaders -------------------------------------------------
    FuncDoc("getaudio", 3, 4, "n,rate[,table]", "obj.getaudio(buf,file,type,size)"),
    FuncDoc("pixelshader", 3, 7, "-",
            "obj.pixelshader(name,target,{res}[,{const},blend,sampler])"),
    FuncDoc("computeshader", 3, 8, "-",
            "obj.computeshader(name,{target},{res}[,{const},cx,cy,cz,sampler])"),
    # --- misc helpers ----------------------------------------------------
    FuncDoc("rand", 2, 4, "number", "obj.rand(st,ed[,seed,frame])"),
    FuncDoc("rand1", 0, 2, "number", "obj.rand1([seed,frame])"),
]}

# ---------------------------------------------------------------------------
# documented literal keys / enumerations (first argument unless noted)
# ---------------------------------------------------------------------------
INFO_KEYS: FrozenSet[str] = frozenset({
    "script_path", "filter", "origin_scene_id", "reference_frame", "rendering_id",
    "saving", "image_max", "frame_max", "layer_max", "bpm", "bpm_list",
    "clock", "script_time", "version",
})

OPTION_KEYS_GET: FrozenSet[str] = frozenset({
    "track_mode", "section_num", "script_name", "gui", "group_info", "camera_mode",
    "camera_param", "camera_focus", "multi_object", "blend", "culling", "billboard",
    "drawtarget", "draw_state", "enable_group", "enable_camera",
    "clipping_object", "clipping_upper_object",
})

OPTION_KEYS_SET: FrozenSet[str] = frozenset({
    "culling", "billboard", "blend", "drawtarget", "draw_state", "focus_mode",
    "camera_param", "camera_focus", "sampler",
})

LOAD_KINDS: FrozenSet[str] = frozenset({
    "movie", "movie.frame", "movie.info", "image", "text", "text.layout",
    "textlayout", "figure", "framebuffer", "tempbuffer", "layer", "before",
})

BUFFER_TARGETS: FrozenSet[str] = frozenset({"object", "tempbuffer", "framebuffer"})
PIXEL_FORMATS: FrozenSet[str] = frozenset({"rgba", "bgra"})
PIXEL_TYPES: FrozenSet[str] = frozenset({"col", "rgb", "yc"})
PIXEL_SOURCES: FrozenSet[str] = frozenset({"object", "framebuffer"})
PIXEL_BLENDS: FrozenSet[str] = frozenset({"", "replace", "0", "1", "2", "3"})
SHADER_BLENDS: FrozenSet[str] = frozenset({"copy", "mask", "draw", "add"})
SAMPLERS: FrozenSet[str] = frozenset({"clip", "clamp", "loop", "mirror", "dot"})
SETOPTION_BLENDS: FrozenSet[str] = frozenset({
    "none", "add", "sub", "mul", "screen", "overlay", "light", "dark",
    "brightness", "chroma", "shadow", "light_dark", "diff",
    "alpha_add", "alpha_max", "alpha_sub", "alpha_add2", "rgba_add",
})
# `obj.getvalue("layer7.x")` / `obj.getvalue("layer"..n..".y")` forms
LAYER_VALUE_FIELDS: FrozenSet[str] = frozenset({
    "x", "y", "z", "rx", "ry", "rz", "cx", "cy", "cz", "sx", "sy", "sz",
    "zoom", "aspect", "alpha", "time", "frame_s", "frame_e", "pos", "angle",
    "center", "scale",
})
GETVALUE_FIELDS: FrozenSet[str] = frozenset(LAYER_VALUE_FIELDS | {
    "layer", "time", "frame_s", "frame_e", "scenechange",
})


def is_buffer_target(key: str) -> bool:
    """`object` / `tempbuffer` / `framebuffer` / `cache:NAME` (docs § 画像バッファ)."""
    return key in BUFFER_TARGETS or key.startswith("cache:")


def is_buffer_source(key: str) -> bool:
    """Sources additionally allow `image:PATH` (obj.copybuffer) and `random`."""
    return is_buffer_target(key) or key.startswith("image:") or key == "random"


# ---------------------------------------------------------------------------
# obj.* field table (docs § 変数)
# ---------------------------------------------------------------------------
# name -> readonly
FIELDS: Dict[str, bool] = {
    "ox": False, "oy": False, "oz": False,
    "rx": False, "ry": False, "rz": False,
    "cx": False, "cy": False, "cz": False,
    "sx": False, "sy": False, "sz": False,
    "zoom": False, "aspect": False, "alpha": False,
    "x": True, "y": True, "z": True,
    "w": True, "h": True,
    "screen_w": True, "screen_h": True, "framerate": True,
    "frame": True, "time": True, "totalframe": True, "totaltime": True,
    "layer": True, "index": True, "num": True, "id": True, "effect_id": True,
    "frame_s": True, "frame_e": True, "effect_layer": True,
    "originframe": True, "scene_id": True,
}

# ---------------------------------------------------------------------------
# Return arity of the calls the sandbox probes at runtime
# ---------------------------------------------------------------------------
# name -> {literal key: expected values} (None = "docs do not specify")
RETURNS: Dict[str, Dict[str, Optional[int]]] = {
    "getinfo": {"image_max": 2, "bpm": 3, "bpm_list": 1, "clock": 1,
                "script_time": 1, "version": 1, "script_path": 1, "filter": 1,
                "saving": 1, "frame_max": 1, "layer_max": 1, "rendering_id": 1,
                "origin_scene_id": 1, "reference_frame": 1},
    "getoption": {"gui": 1, "multi_object": 1, "camera_mode": 1, "blend": 1,
                  "culling": 1, "billboard": 1, "drawtarget": 1, "draw_state": 1,
                  "section_num": 1, "camera_param": 1, "camera_focus": 1},
}

__all__ = [
    "DOC_URL", "DOC_CHECKED", "FuncDoc", "FUNCS", "INFO_KEYS", "OPTION_KEYS_GET",
    "OPTION_KEYS_SET", "LOAD_KINDS", "BUFFER_TARGETS", "PIXEL_FORMATS",
    "PIXEL_TYPES", "PIXEL_SOURCES", "PIXEL_BLENDS", "SHADER_BLENDS", "SAMPLERS",
    "SETOPTION_BLENDS", "LAYER_VALUE_FIELDS", "GETVALUE_FIELDS",
    "is_buffer_target", "is_buffer_source", "FIELDS", "RETURNS",
]
