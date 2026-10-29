"""
Static interface analysis for the embedded HLSL blocks.

Two questions are answered here, both **without running anything**:

  * what does each `--[[pixelshader@NAME: ... ]]` block *declare*
    (`Texture2D` slots, `cbuffer` layout in floats), and
  * what does each `obj.pixelshader(NAME, dst, {inputs}, {params}, blend, sampler)`
    call site actually *supply* (input count, uniform count).

Comparing the two catches the classic volumetric-light failure mode "the shader
reads `p1.y` but Lua only uploads six floats" at lint time instead of on a
user's GPU.  The rule set follows the one AviUtl2_Astra enforces in its build
step (uniform-layout validation), but is re-implemented here in Python so it
runs on Linux CI without the Astra toolchain.

The distinction that makes the check useful:

    declared  = 4 * sum(field.count)             # total floats in the cbuffer
    required  = offset(last used component) + 1  # what the shader body reads
    supplied  = number of floats in the Lua table

    supplied < required -> FAIL   (shader reads garbage)
    required <= supplied <= declared -> PASS   (tail is unused padding)
    supplied > declared -> FAIL   (Lua packs more than the block holds)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Lua side
# ---------------------------------------------------------------------------
CALL_RE = re.compile(r"obj\.(pixelshader|computeshader)\s*\(")
_QUOTES = "\"'"
_PAIRS = {"(": ")", "{": "}", "[": "]"}


@dataclass
class CallSite:
    line: int
    fn: str
    name: Optional[str]          # shader id when the first argument is a literal
    n_tex: Optional[int]         # number of entries in the source-buffer table
    n_params: Optional[int]      # number of floats in the uniform table
    blend: Optional[str] = None
    sampler: Optional[str] = None
    dynamic_uniforms: bool = False

    def __str__(self) -> str:
        return (f"L{self.line} {self.fn}({self.name or '?'}) "
                f"tex={self.n_tex} floats={self.n_params}")


def _scan_to_close(text: str, start: int) -> int:
    """Return the index of the bracket that closes the one opened at `start`."""
    opener = text[start]
    closer = _PAIRS[opener]
    depth = 0
    i, n = start, len(text)
    quote: Optional[str] = None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in _QUOTES:
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced brackets")


def split_top(text: str, sep: str = ",") -> List[str]:
    """Split a Lua expression on `sep` at nesting depth zero (quotes aware)."""
    out: List[str] = []
    depth = 0
    quote: Optional[str] = None
    cur: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < n:
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in _QUOTES:
            quote = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if cur or out:
        out.append("".join(cur))
    return [x.strip() for x in out]


def _lua_string(expr: str) -> Optional[str]:
    m = re.fullmatch(r"""["']([^"']*)["']""", expr.strip())
    return m.group(1) if m else None


def _table_len(expr: str) -> Optional[int]:
    expr = expr.strip()
    if not (expr.startswith("{") and expr.endswith("}")):
        return None
    inner = expr[1:-1].strip()
    if not inner:
        return 0
    return len([x for x in split_top(inner) if x])


def iter_calls(lua_text: str) -> List[CallSite]:
    """Every `obj.pixelshader` / `obj.computeshader` call site in the script."""
    calls: List[CallSite] = []
    for m in CALL_RE.finditer(lua_text):
        open_paren = m.end() - 1
        end = _scan_to_close(lua_text, open_paren)
        args = split_top(lua_text[open_paren + 1:end])
        line = lua_text.count("\n", 0, m.start()) + 1
        site = CallSite(line=line, fn=m.group(1), name=_lua_string(args[0]) if args else None,
                        n_tex=None, n_params=None)
        if len(args) > 2:
            site.n_tex = _table_len(args[2])
        if len(args) > 3:
            site.n_params = _table_len(args[3])
            site.dynamic_uniforms = site.n_params is None
        if len(args) > 4:
            site.blend = _lua_string(args[4]) or args[4].strip()
        if len(args) > 5:
            site.sampler = _lua_string(args[5]) or args[5].strip()
        calls.append(site)
    return calls


# ---------------------------------------------------------------------------
# HLSL side
# ---------------------------------------------------------------------------
def strip_hlsl_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


@dataclass
class CBufferField:
    name: str
    count: int = 1
    offset: int = 0            # first float index of this field

    @property
    def floats(self) -> int:
        return 4 * self.count


@dataclass
class ShaderLayout:
    name: str
    textures: List[str] = field(default_factory=list)
    fields: List[CBufferField] = field(default_factory=list)
    used: Dict[str, int] = field(default_factory=dict)   # field -> floats read
    entry_points: List[str] = field(default_factory=list)

    @property
    def declared(self) -> int:
        return sum(f.floats for f in self.fields)

    @property
    def required(self) -> int:
        need = 0
        for f in self.fields:
            comps = self.used.get(f.name, 0)
            if comps:
                need = max(need, f.offset + comps)
        return need

    @property
    def unused_tail(self) -> int:
        return self.declared - self.required

    def describe(self) -> Dict[str, object]:
        return {"textures": self.textures, "declared": self.declared,
                "required": self.required, "unused_tail": self.unused_tail,
                "fields": [f.name for f in self.fields],
                "unused_fields": [f.name for f in self.fields if not self.used.get(f.name)]}


_SWIZZLE_COMP = {c: j for grp in ("xyzw", "rgba", "stpq")
                 for j, c in enumerate(grp)}
_REGISTER_RE = re.compile(r"cbuffer\s+([A-Za-z_]\w*)\s*(?::\s*register\s*\(\s*b\d+\s*\))?\s*\{")
_TEX_RE = re.compile(r"Texture2D\s+([A-Za-z_]\w*)\s*(?::|;)")
_FUNC_RE = re.compile(r"\b(?:float[1-4]?|void|int[1-4]?|uint[1-4]?)\s+([A-Za-z_]\w*)\s*\(")


def analyze(name: str, src: str) -> ShaderLayout:
    """Parse one embedded shader block into a `ShaderLayout`."""
    body = strip_hlsl_comments(src)
    layout = ShaderLayout(name=name, textures=_TEX_RE.findall(body))

    # -- cbuffer ------------------------------------------------------------
    # `decl_span` marks the declaration blocks so the "which components are
    # read" pass below does not count `float4 p1;` itself as a read of `p1`.
    decl_span: List[tuple] = []
    for m in _REGISTER_RE.finditer(body):
        braces = body.index("{", m.start())
        end = _scan_to_close(body, braces)
        decl_span.append((m.start(), end + 1))
        inner = body[braces + 1:end]
        for decl in split_top(inner, ";"):
            decl = decl.strip()
            if not decl:
                continue
            dm = re.match(r"float4\s+([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*\])?$", decl)
            if not dm:
                # scalar / matrix members are legal HLSL but the transpiler
                # (and therefore this lint) only supports float4[n]; none of the
                # 14 blocks in this script uses them.
                continue
            count = int(dm.group(2) or 1)
            offset = layout.declared
            layout.fields.append(CBufferField(name=dm.group(1), count=count, offset=offset))

    # -- which components of each field does the body actually read? --------
    code = body
    for a, b in decl_span:            # mask `cbuffer Foo { float4 p0; };`
        code = code[:a] + " " * (b - a) + code[b:]
    for f in layout.fields:
        need = 0
        # an indexed access (`occ[i]`, `occ[3]`) may touch any element of the
        # array, so the whole field has to be counted
        if re.search(r"\b" + re.escape(f.name) + r"\s*\[", code):
            need = f.floats
        else:
            swz = re.compile(r"\b" + re.escape(f.name) + r"\s*\.\s*([xyzwrgba]{1,4})\b")
            for sm in swz.finditer(code):
                for c in sm.group(1):
                    need = max(need, _SWIZZLE_COMP[c] + 1)
            # a bare reference (whole float4 passed around) counts as all 4;
            # a swizzled one does not (`p1.x` must not count as `p1`)
            if not need and re.search(r"\b" + re.escape(f.name) + r"\b(?![.\[])", code):
                need = f.floats
        layout.used[f.name] = need

    # -- entry points -------------------------------------------------------
    layout.entry_points = [fn for fn in _FUNC_RE.findall(body) if fn == name]
    return layout


def array_fields(layout: ShaderLayout) -> Dict[str, int]:
    """{field: floats} for the `float4 name[N]` members (N > 1)."""
    return {f.name: f.floats for f in layout.fields if f.count > 1}


def analyze_all(shaders: Dict[str, str]) -> Dict[str, ShaderLayout]:
    return {n: analyze(n, s) for n, s in shaders.items()}


def audit(shaders: Dict[str, str], lua_text: str) -> Dict[str, object]:
    """Cross-check every call site against every declared shader block."""
    layouts = analyze_all(shaders)
    calls = iter_calls(lua_text)
    problems: List[str] = []
    notes: List[str] = []
    rows: List[Dict[str, object]] = []

    unknown = sorted({c.name for c in calls if c.name and c.name not in layouts})
    if unknown:
        problems.append(f"pixelshader() calls without a shader block: {unknown}")

    by_name: Dict[str, List[CallSite]] = {}
    for c in calls:
        if c.name in layouts:
            by_name.setdefault(c.name, []).append(c)

    for name, layout in sorted(layouts.items()):
        sites = by_name.get(name, [])
        if not sites:
            notes.append(f"{name}: defined but never called")
            continue
        supplied_seen = set()
        for c in sites:
            supplied = c.n_params
            if supplied is None:      # table built from a literal + append loop
                supplied = resolve_uniforms(lua_text, c, layout)
                c.n_params = supplied
                c.dynamic_uniforms = True
            if supplied is not None:
                supplied_seen.add(supplied)
                if supplied > layout.declared:
                    problems.append(
                        f"{name} @L{c.line}: Lua passes {supplied} floats, "
                        f"cbuffer declares {layout.declared}")
                elif supplied < layout.required:
                    problems.append(
                        f"{name} @L{c.line}: Lua passes {supplied} floats, "
                        f"shader reads {layout.required} (cbuffer {layout.declared})")
            elif c.dynamic_uniforms:
                notes.append(f"{name} @L{c.line}: uniforms cannot be resolved statically")
            if c.n_tex is not None and c.n_tex > len(layout.textures):
                problems.append(
                    f"{name} @L{c.line}: Lua binds {c.n_tex} textures, "
                    f"HLSL declares {len(layout.textures)}")
        rows.append({
            "shader": name,
            "calls": len(sites),
            "supplied": sorted(supplied_seen),
            "required": layout.required,
            "declared": layout.declared,
            "unused_tail": layout.unused_tail,
        })

    return {"layouts": layouts, "calls": calls, "rows": rows,
            "problems": problems, "notes": notes}


# ---------------------------------------------------------------------------
# Dynamic uniform tables
# ---------------------------------------------------------------------------
# `beamatlas` is called with a table that is built in two steps: a literal of
# fixed constants plus a loop that appends `MAX_OCC * 16` floats from the 2D
# occluder slab.  That is the one call site where the linter has to follow data
# flow, so it gets a (deliberately small) resolver.
_APPEND_LOOP_RE = r"for\s+{var}\s*=\s*1\s*,\s*([^d]+?)\s+do"
_CONST_RE = r"local\s+{name}\s*=\s*(\d+)"


def lua_int_constant(lua_text: str, name: str) -> Optional[int]:
    m = re.search(_CONST_RE.format(name=re.escape(name)), lua_text)
    return int(m.group(1)) if m else None


def _eval_count(expr: str, lua_text: str) -> Optional[int]:
    expr = expr.strip()
    if expr.isdigit():
        return int(expr)
    m = re.fullmatch(r"([A-Za-z_]\w*)\s*\*\s*(\d+)", expr)
    if m:
        base = lua_int_constant(lua_text, m.group(1))
        return None if base is None else base * int(m.group(2))
    return None


def table_literal_len(lua_text: str, var: str) -> Optional[int]:
    """Number of top-level entries in the first `local <var> = { ... }` literal."""
    m = re.search(r"local\s+" + re.escape(var) + r"\s*=\s*\{", lua_text)
    if not m:
        return None
    try:
        end = _scan_to_close(lua_text, m.end() - 1)
    except ValueError:
        return None
    return _table_len("{" + lua_text[m.end():end] + "}")


def appended_len(lua_text: str, var: str) -> Optional[int]:
    """Floats appended to `<var>` inside a `for <i> = 1, <count> do` loop."""
    am = re.search(re.escape(var) + r"\s*\[\s*[A-Za-z_]\w*\s*\+\s*([A-Za-z_]\w*)\s*\]\s*=", lua_text)
    if not am:
        return 0
    idx = am.group(1)
    lm = re.search(_APPEND_LOOP_RE.format(var=re.escape(idx)), lua_text[:am.start()])
    if not lm:
        return None
    return _eval_count(lm.group(1), lua_text)


def resolve_uniforms(lua_text: str, call: CallSite, layout: "ShaderLayout") -> Optional[int]:
    """Total float count for a call site whose uniform table is a variable.

    Returns None when the pattern is not recognised (the caller should then
    treat the site as unverifiable rather than failing it).
    """
    if call.n_params is not None:
        return call.n_params
    # which identifier was passed as the uniform table?
    m = CALL_RE.search(lua_text, max(0, _line_offset(lua_text, call.line) - 1))
    if not m:
        return None
    open_paren = m.end() - 1
    args = split_top(lua_text[open_paren + 1:_scan_to_close(lua_text, open_paren)])
    if len(args) < 4:
        return None
    var = args[3].strip()
    if not re.fullmatch(r"[A-Za-z_]\w*", var):
        return None
    lit = table_literal_len(lua_text, var)
    if lit is None:
        return None
    extra = appended_len(lua_text, var)
    if extra is None:
        return None
    return lit + extra


def _line_offset(text: str, line: int) -> int:
    parts = text.splitlines(keepends=True)
    return sum(len(p) for p in parts[:max(0, line - 1)])
