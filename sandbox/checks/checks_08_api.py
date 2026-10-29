"""
Host-API contract checks: official docs (docs.aviutl2.jp/lua) vs. the script vs.
the sandbox's emulated host.

The rest of the suite proves *behaviour*; these checks prove that the thing the
behaviour is measured against still matches the manual:

  * every `obj.*` the script calls is documented (`vl3dsb.apidoc`) and emulated
    (`vl3dsb.host`),
  * argument counts / literal enum keys stay inside the documented signatures,
  * the emulated `obj` table exposes exactly the documented field set,
  * the fake returns the documented *number of values* (this is where
    `obj.getinfo("image_max")` -- two values, not one -- used to be wrong),
  * `obj.copybuffer` exists by default (it is part of the API) and both buffer
    paths still produce the same frame.

Each rule is verified twice: once against the real script and once against a
seeded-bad snippet, so a silently disabled rule cannot pass.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from vl3dsb import api_lint, apidoc, assets, session
from vl3dsb.harness import check, fail, require
from vl3dsb.host import LUA_PRELUDE, Host, HostConfig, Layer
from vl3dsb.obj2 import parse_obj2
from vl3dsb.session import DEFAULT_SCRIPT, run_frame, standard_scene

BASE = {"src_layer": 1, "intensity": 180.0, "slices": 20.0}


def _render(params=None, *, copybuffer=True, w=160, h=96):
    p = dict(BASE)
    p.update(params or {})
    return run_frame(standard_scene(128, 128), p, width=w, height=h,
                     layer_index=4, copybuffer=copybuffer)


def _host() -> Host:
    sc = parse_obj2(DEFAULT_SCRIPT)
    return Host(sc, [Layer(index=1, image=assets.stained_window(32, 32))],
                HostConfig(screen_w=64, screen_h=36))


def _lua(host: Host, code: str):
    """Evaluate a Lua snippet that may use `local` (lupa.eval only takes expressions)."""
    host.lua.execute("__probe = (function() %s end)()" % code)
    return host.lua.globals()["__probe"]


# ---------------------------------------------------------------------------
# static: the surface the script uses
# ---------------------------------------------------------------------------
@check("api: every obj.* call is documented and emulated by the host", tags=["static", "api"])
def surface_documented():
    sc = session.script()
    rep = api_lint.lint(sc.text, set(sc.shaders))
    require(rep.ok, "API surface drift:\n  " + "\n  ".join(rep.issues[:12]))

    from vl3dsb import luasurface
    surf = luasurface.scan(sc.text)
    missing = []
    for name in surf.names:
        emulated = hasattr(Host, "_api_" + name) or ("function obj.%s(" % name) in LUA_PRELUDE
        if not emulated:
            missing.append(name)
    require(not missing, f"host does not emulate: {missing}")
    return {"calls": rep.stats["calls"], "functions": rep.stats["functions"],
            "notes": len(rep.notes), "note_sample": rep.notes[:3]}


@check("api: argument counts and enum keys match the documented signatures", tags=["static", "api"])
def arity_and_keys():
    sc = session.script()
    from vl3dsb import luasurface
    surf = luasurface.scan(sc.text)
    rows: Dict[str, str] = {}
    for name in surf.names:
        doc = apidoc.FUNCS[name]
        counts = sorted({c.n_args for c in surf.calls_of(name)})
        bad = [n for n in counts if not doc.accepts(n)]
        require(not bad, f"obj.{name}{{...}} called with {bad}, docs allow {doc.signature()}")
        rows[name] = f"{counts} of {doc.signature()}"

    # the documented enumerations must be *used* legally, not merely parsed
    checks = {
        "setoption": (0, apidoc.OPTION_KEYS_SET),
        "getoption": (0, apidoc.OPTION_KEYS_GET),
        "load": (0, apidoc.LOAD_KINDS),
    }
    for fn, (idx, allowed) in checks.items():
        for c in surf.calls_of(fn):
            key = c.literal(idx)
            if key is not None:
                require(key in allowed, f"L{c.line}: obj.{fn}({key!r}) not in the documented set")
    for fn in ("clearbuffer", "getpixeldata", "putpixeldata"):
        for c in surf.calls_of(fn):
            key = c.literal(0)
            if key is not None:
                require(apidoc.is_buffer_target(key), f"L{c.line}: {fn} buffer {key!r} undocumented")
    for c in surf.calls_of("copybuffer"):
        for idx in (0, 1):
            key = c.literal(idx)
            if key is not None:
                require(apidoc.is_buffer_source(key), f"L{c.line}: copybuffer buffer {key!r} undocumented")
    return {"functions": len(rows), "sample": dict(list(rows.items())[:4])}


@check("api: API linter catches seeded drift (rule self-test)", tags=["static", "api"])
def linter_bites():
    bad = '''
obj.nope(1)
obj.getinfo("clock", 1, 2)
obj.setoption("nonsense", 1)
obj.load("movie.frame.extra")
obj.getinfo("weird_key")
obj.pixelshader("no_such", "object", {"a"}, {}, "bogus")
obj.setfont("x", 10, 9)
obj.drawpoly({{0,0,0,0,0}}, 5)
print(obj.mystery_field)
obj.x = 5
obj.clearbuffer("cache:ok", 2, 2)
obj.copybuffer("object", "cache:ok")
'''
    rep = api_lint.lint(bad)
    require(len(rep.issues) >= 10, f"linter only found {len(rep.issues)} of 10 seeded issues:\n"
                                   + "\n".join(rep.issues))
    must = ["obj.nope", "takes 1..2 args", "nonsense", "movie.frame.extra",
            "weird_key", "bogus", "outside 0..6", "vertex_num 5",
            "mystery_field", "read-only"]
    for needle in must:
        require(any(needle in i for i in rep.issues), f"linter missed {needle!r}")
    # legal lines in the same snippet must NOT be reported
    require(not any("cache:ok" in i for i in rep.issues), "legal cache: buffer reported as an issue")
    return {"seeded": 12, "reported": len(rep.issues)}


# ---------------------------------------------------------------------------
# static: the emulated obj table
# ---------------------------------------------------------------------------
@check("api: the emulated host exposes the documented obj.* field table", tags=["static", "api"])
def field_table():
    host = _host()
    keys = _lua(host, "local t = {}; for k in pairs(obj) do t[#t+1] = k end; return table.concat(t, ',')")
    names = {k for k in str(keys).split(",") if k}
    funcs = {n for n in apidoc.FUNCS if n in names}
    fields = names - funcs
    missing = sorted(set(apidoc.FIELDS) - fields)
    extra = sorted(fields - set(apidoc.FIELDS))
    require(not missing, f"documented fields missing from the fake: {missing}")
    require(not extra, f"undocumented fields exposed by the fake: {extra}")
    return {"fields": len(fields), "functions": len(funcs)}


# ---------------------------------------------------------------------------
# runtime: return arity / accepted call shapes
# ---------------------------------------------------------------------------
@check("api: the fake returns the documented values (arity + enum probe)", tags=["runtime", "api"])
def return_contract():
    host = _host()
    host._alloc("object", 4, 4)
    buf = host.buffers["object"]
    buf[:, :] = (32, 64, 128, 128)          # premultiplied RGBA

    problems: List[str] = []

    def want(label, got, expect):
        if got != expect:
            problems.append(f"{label}: {got!r} != {expect!r}")

    want("getinfo('image_max') arity",
         _lua(host, "return select('#', obj.getinfo('image_max'))"), 2)
    want("getinfo('image_max') types",
         _lua(host, "local a,b = obj.getinfo('image_max'); return type(a)..'/'..type(b)"), "number/number")
    want("getinfo('clock') type", _lua(host, "return type(obj.getinfo('clock'))"), "number")
    want("getpixel() arity", _lua(host, "return select('#', obj.getpixel())"), 2)
    want("getpixel rgb arity",
         _lua(host, "return select('#', obj.getpixel(0,0,'rgb'))"), 4)
    want("getpixel col arity",
         _lua(host, "return select('#', obj.getpixel(0,0,'col'))"), 2)
    want("getpixel yc arity",
         _lua(host, "return select('#', obj.getpixel(0,0,'yc'))"), 4)
    want("getpixel rgb value",
         _lua(host, "local r,g,b,a = obj.getpixel(0,0,'rgb'); return string.format('%d,%d,%d,%d', r,g,b,a)"),
         "64,128,255,128")
    want("copybuffer type", _lua(host, "return type(obj.copybuffer)"), "function")
    want("copybuffer return",
         _lua(host, "return type(obj.copybuffer('cache:probe','object'))"), "boolean")
    want("setanchor return", _lua(host, "return type(obj.setanchor('light_x,light_y', 0))"), "number")
    want("load(figure) return", _lua(host, "return type(obj.load('figure','四角形',0xffffff,100))"), "boolean")

    # documented shapes that must not raise
    _lua(host, "return obj.setfont('Arial', 20, 3, 0xffffff, 0x111122, true, false, 1.5, 2.0)")
    want("setfont type is the 3rd argument", host.font.get("type"), 3)
    want("setfont bold", host.font.get("bold"), True)
    _lua(host, "return obj.draw(0,0,0,1.0,1.0,10,20,30)")
    want("draw accepts 8 args (rx,ry,rz)", host.draw_calls[-1].get("rx"), 10.0)
    _lua(host, "return obj.drawpoly({{0,0,0,0,0},{8,0,0,1,0},{8,8,0,1,1},{0,8,0,0,1}}, 4, 0.5)")
    want("drawpoly vertex-table form", host.quads[-1]["alpha"], 0.5)
    _lua(host, "return obj.drawpoly({0,0,0,0,0, 8,0,0,1,0, 8,8,0,1,1, 0,8,0,0,1}, 0.25)")
    want("drawpoly flat form", host.quads[-1]["alpha"], 0.25)
    n_before = len(host.quads)
    ok = _lua(host, "return obj.drawpoly({{0,0,0,0,0},{1,0,0,1,0,1},{1,1,0,1,1},{0,1,0,0,1}}, 3, 0.5)")
    want("drawpoly rejects mixed tuple widths", ok, False)
    want("drawpoly mixed leaves no geometry", len(host.quads), n_before)

    require(not problems, "documented contract violated:\n  " + "\n  ".join(problems))
    return {"probes": 19}


@check("api: obj.getpixel and obj.getpixeldata agree on the same buffer", tags=["runtime", "api"])
def pixel_paths_agree():
    host = _host()
    host._alloc("object", 3, 2)
    buf = host.buffers["object"]
    buf[1, 2] = (10, 20, 30, 255)          # opaque, so premultiplication is a no-op

    got = _lua(host, "local r,g,b,a = obj.getpixel(2,1,'rgb'); return string.format('%d,%d,%d,%d',r,g,b,a)")
    require(str(got) == "10,20,30,255", f"obj.getpixel disagrees with the buffer: {got}")

    addr = _lua(host, "local p,w,h = obj.getpixeldata('object','rgba'); return p")
    require(addr is not None, "getpixeldata returned no pointer")
    # read the same texel straight through the FFI pointer the script would get
    px = _lua(host, "local p,w,h = obj.getpixeldata('object','rgba'); "
                    "local b = p + (1*w + 2)*4; "
                    "return string.format('%d,%d,%d,%d', b[0], b[1], b[2], b[3])")
    require(str(px) == "10,20,30,255", f"FFI pointer disagrees with obj.getpixel: {px}")
    return {"texel": str(px)}


@check("api: obj.copybuffer is documented+on, and both buffer paths agree", tags=["runtime", "api"])
def copybuffer_paths():
    require(HostConfig().copybuffer_supported, "obj.copybuffer is documented; the fake must enable it")
    a = _render()
    b = _render(copybuffer=False)
    require(not a.host.errors, f"copybuffer path raised: {a.host.errors[:1]}")
    require(not b.host.errors, f"fallback path raised: {b.host.errors[:1]}")
    require(a.volume is not None and b.volume is not None, "a path produced no geometry")
    require(a.alpha.shape == b.alpha.shape, f"frame size differs: {a.alpha.shape} vs {b.alpha.shape}")
    diff = int(np.abs(a.alpha.astype(int) - b.alpha.astype(int)).max())
    require(diff <= 2, f"the two documented buffer paths disagree (max alpha diff {diff})")
    used = "copybuffer" in "".join(a.log) + "".join(a.host.prints)
    return {"max_alpha_diff": diff, "alpha_mean": round(a.stats()["alpha_mean"], 5),
            "copybuffer_mentioned": used}
