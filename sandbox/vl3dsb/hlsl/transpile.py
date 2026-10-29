"""
HLSL -> C++ transpiler for the VolumetricLight3D pixel shaders.

Only the language subset used by Script/VolumetricLight3D.obj2 is supported
(float/int/uint vectors, swizzles, cbuffers, Texture2D.Load / SampleLevel,
if/for/break/continue/return, out parameters).  Anything outside that subset
raises `HlslError` with a line number instead of silently mis-compiling.

The generated C++ is compiled once per script hash by `executor.py` and called
through ctypes, which lets the sandbox execute the real shader math on a CPU
without Direct3D 11 / Vulkan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


class HlslError(Exception):
    pass


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------
TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>0[xX][0-9a-fA-F]+[uU]?|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[uUfF]?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><<=|>>=|<<|>>|\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|==|!=|<=|>=|&&|\|\||[-+*/%&|^~!<>=?:.,;(){}\[\]])
    """,
    re.X,
)

KEYWORDS = {
    "if", "else", "for", "while", "return", "break", "continue", "true", "false",
    "cbuffer", "register", "Texture2D", "SamplerState", "out", "in", "inout",
    "void", "bool", "int", "uint", "float",
}

BUILTINS = {
    "min", "max", "clamp", "saturate", "lerp", "smoothstep", "dot", "length",
    "normalize", "pow", "exp", "sin", "cos", "acos", "atan2", "sqrt", "rsqrt",
    "abs", "floor", "ceil", "frac", "fmod", "sign",
}

TYPE_RE = re.compile(r"^(float|int|uint|bool)([2-4])?$")


def strip_comments(src: str) -> str:
    out = []
    i, n = 0, len(src)
    in_line = in_block = False
    while i < n:
        c = src[i]
        if in_line:
            if c == "\n":
                in_line = False
                out.append(c)
            else:
                out.append(" ")
            i += 1
            continue
        if in_block:
            if c == "*" and i + 1 < n and src[i + 1] == "/":
                in_block = False
                out.append("  ")
                i += 2
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            in_line = True
            out.append("  ")
            i += 2
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            in_block = True
            out.append("  ")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_attributes(src: str) -> str:
    """Remove [loop] / [unroll] / [branch] / [fastopt] hint lines."""
    return re.sub(r"^\s*\[(?:loop|unroll|branch|fastopt|allow_uav_condition|call|forcecase)\]\s*$",
                  "", src, flags=re.M)


@dataclass
class Tok:
    kind: str
    text: str
    line: int
    col: int


def tokenize(src: str) -> List[Tok]:
    src = strip_attributes(strip_comments(src))
    toks: List[Tok] = []
    pos = 0
    line = 1
    line_start = 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise HlslError(f"lexer: unexpected character {src[pos]!r} at line {line}")
        text = m.group(0)
        if m.lastgroup != "ws":
            kind = m.lastgroup or ""
            toks.append(Tok(kind, text, line, pos - line_start))
        nl = text.count("\n")
        if nl:
            line += nl
            line_start = m.end()
        pos = m.end()
    toks.append(Tok("eof", "", line, 0))
    return toks


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
# type = ("f"|"i"|"u"|"b"|"void"|"tex"|"smp", components)
class Parser:
    def __init__(self, toks: List[Tok], name: str):
        self.t = toks
        self.i = 0
        self.name = name

    # -- helpers --
    def peek(self, k: int = 0) -> Tok:
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self) -> Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, text: str) -> bool:
        return self.peek().text == text

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> Tok:
        tok = self.peek()
        if tok.text != text:
            raise HlslError(f"{self.name}: expected {text!r} but found {tok.text!r} at line {tok.line}")
        self.i += 1
        return tok

    def ident(self) -> str:
        tok = self.peek()
        if tok.kind != "id":
            raise HlslError(f"{self.name}: expected identifier, found {tok.text!r} at line {tok.line}")
        self.i += 1
        return tok.text

    # -- types --
    def is_type_start(self, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok.kind == "id" and (TYPE_RE.match(tok.text) is not None or tok.text == "void")

    def parse_type(self) -> Tuple[str, int]:
        tok = self.peek()
        m = TYPE_RE.match(tok.text)
        if m:
            base = {"float": "f", "int": "i", "uint": "u", "bool": "b"}[m.group(1)]
            comps = int(m.group(2) or 1)
            self.i += 1
            return base, comps
        if tok.text == "void":
            self.i += 1
            return "void", 0
        raise HlslError(f"{self.name}: unsupported type {tok.text!r} at line {tok.line}")

    # -- program --
    def parse_program(self) -> Dict:
        textures: List[str] = []
        samplers: List[str] = []
        cbuffers: List[Dict] = []
        funcs: List[Dict] = []
        while self.peek().kind != "eof":
            tok = self.peek()
            if tok.text == "Texture2D":
                self.next()
                nm = self.ident()
                if self.accept(":"):
                    self.expect("register")
                    self.expect("(")
                    self.next()
                    self.expect(")")
                self.expect(";")
                textures.append(nm)
                continue
            if tok.text == "SamplerState":
                self.next()
                nm = self.ident()
                if self.accept(":"):
                    self.expect("register")
                    self.expect("(")
                    self.next()
                    self.expect(")")
                self.expect(";")
                samplers.append(nm)
                continue
            if tok.text == "cbuffer":
                self.next()
                nm = self.ident()
                if self.accept(":"):
                    self.expect("register")
                    self.expect("(")
                    self.next()
                    self.expect(")")
                self.expect("{")
                items = []
                while not self.at("}"):
                    base, comps = self.parse_type()
                    if comps != 4 or base != "f":
                        raise HlslError(f"{self.name}: cbuffer field must be float4 ({nm})")
                    fname = self.ident()
                    count = None
                    if self.accept("["):
                        tok = self.next()
                        if tok.kind != "num":
                            raise HlslError(f"{self.name}: cbuffer array size must be a literal")
                        count = int(float(tok.text.rstrip("uUfF")))
                        self.expect("]")
                    self.expect(";")
                    items.append({"name": fname, "count": count})
                self.expect("}")
                self.accept(";")
                cbuffers.append({"name": nm, "fields": items})
                continue
            # function definition
            ret = self.parse_type()
            fname = self.ident()
            self.expect("(")
            params = []
            if not self.at(")"):
                while True:
                    is_out = False
                    if self.peek().text in ("out", "inout", "in"):
                        is_out = self.next().text in ("out", "inout")
                    pty = self.parse_type()
                    pnm = None
                    if self.peek().kind == "id" and self.peek().text not in ("out",):
                        pnm = self.ident()
                    if self.accept(":"):
                        self.next()
                    params.append({"type": pty, "name": pnm, "out": is_out})
                    if not self.accept(","):
                        break
            self.expect(")")
            if self.accept(":"):
                self.next()  # SV_Target
            body = self.parse_block()
            funcs.append({"ret": ret, "name": fname, "params": params, "body": body,
                          "line": tok.line})
        if not textures and not funcs:
            raise HlslError(f"{self.name}: no shader code found")
        return {"textures": textures, "samplers": samplers, "cbuffers": cbuffers, "funcs": funcs}

    # -- statements --
    def parse_block(self) -> Tuple:
        self.expect("{")
        stmts = []
        while not self.at("}"):
            if self.peek().kind == "eof":
                raise HlslError(f"{self.name}: unexpected EOF inside block")
            stmts.append(self.parse_stmt())
        self.expect("}")
        return ("block", stmts)

    def parse_stmt(self) -> Tuple:
        tok = self.peek()
        line = tok.line
        if tok.text == "{":
            return self.parse_block()
        if tok.text == ";":
            self.next()
            return ("block", [])
        if tok.text == "if":
            self.next()
            self.expect("(")
            cond = self.parse_expr()
            self.expect(")")
            then = self.parse_stmt()
            other = None
            if self.accept("else"):
                other = self.parse_stmt()
            return ("if", cond, then, other, line)
        if tok.text == "for":
            self.next()
            self.expect("(")
            init = None
            if not self.at(";"):
                if self.is_type_start():
                    init = self.parse_decl(expect_semicolon=False)
                else:
                    init = ("expr", self.parse_expr(), line)
            self.expect(";")
            cond = None if self.at(";") else self.parse_expr()
            self.expect(";")
            step = None if self.at(")") else self.parse_expr()
            self.expect(")")
            body = self.parse_stmt()
            return ("for", init, cond, step, body, line)
        if tok.text == "return":
            self.next()
            e = None if self.at(";") else self.parse_expr()
            self.expect(";")
            return ("return", e, line)
        if tok.text == "break":
            self.next()
            self.expect(";")
            return ("break",)
        if tok.text == "continue":
            self.next()
            self.expect(";")
            return ("continue",)
        if self.is_type_start():
            return self.parse_decl(expect_semicolon=True)
        e = self.parse_expr()
        self.expect(";")
        return ("expr", e, line)

    def parse_decl(self, expect_semicolon: bool) -> Tuple:
        base, comps = self.parse_type()
        items = []
        while True:
            nm = self.ident()
            count = None
            if self.accept("["):
                count = self.parse_expr()
                self.expect("]")
            init = None
            if self.accept("="):
                init = self.parse_expr()
            items.append((nm, count, init))
            if not self.accept(","):
                break
        if expect_semicolon:
            self.expect(";")
        return ("decl", (base, comps), items)

    # -- expressions --
    ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}

    def parse_expr(self) -> Tuple:
        return self.parse_assign()

    def parse_assign(self) -> Tuple:
        lhs = self.parse_ternary()
        tok = self.peek()
        if tok.text in self.ASSIGN_OPS:
            self.next()
            rhs = self.parse_assign()
            return ("assign", tok.text, lhs, rhs)
        return lhs

    def parse_ternary(self) -> Tuple:
        cond = self.parse_binary(0)
        if self.accept("?"):
            a = self.parse_assign()
            self.expect(":")
            b = self.parse_assign()
            return ("tern", cond, a, b)
        return cond

    PRECEDENCE = [
        ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
        ["<", ">", "<=", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
    ]

    def parse_binary(self, level: int) -> Tuple:
        if level >= len(self.PRECEDENCE):
            return self.parse_unary()
        lhs = self.parse_binary(level + 1)
        while self.peek().text in self.PRECEDENCE[level]:
            op = self.next().text
            rhs = self.parse_binary(level + 1)
            lhs = ("bin", op, lhs, rhs)
        return lhs

    def parse_unary(self) -> Tuple:
        tok = self.peek()
        if tok.text in ("-", "+", "!", "~"):
            self.next()
            return ("unary", tok.text, self.parse_unary())
        if tok.text in ("++", "--"):
            self.next()
            return ("preinc", tok.text, self.parse_unary())
        if tok.text == "(" and self.is_type_start(1) and self.peek(2).text == ")":
            self.next()
            ty = self.parse_type()
            self.expect(")")
            return ("cast", ty, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self) -> Tuple:
        e = self.parse_primary()
        while True:
            tok = self.peek()
            if tok.text == ".":
                self.next()
                e = ("member", e, self.ident())
            elif tok.text == "[":
                self.next()
                idx = self.parse_expr()
                self.expect("]")
                e = ("index", e, idx)
            elif tok.text == "(":
                self.next()
                args = []
                if not self.at(")"):
                    while True:
                        args.append(self.parse_assign())
                        if not self.accept(","):
                            break
                self.expect(")")
                e = ("call", e, args, tok.line)
            elif tok.text in ("++", "--"):
                self.next()
                e = ("postinc", tok.text, e)
            else:
                break
        return e

    def parse_primary(self) -> Tuple:
        tok = self.peek()
        if tok.kind == "num":
            self.next()
            return ("num", tok.text, tok.line)
        if tok.text in ("true", "false"):
            self.next()
            return ("bool", tok.text == "true")
        if tok.kind == "id":
            self.next()
            return ("id", tok.text, tok.line)
        if tok.text == "(":
            self.next()
            e = self.parse_expr()
            self.expect(")")
            return e
        raise HlslError(f"{self.name}: unexpected token {tok.text!r} at line {tok.line}")


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------
@dataclass
class FuncSig:
    ret: Tuple[str, int]
    params: List[Dict]


SCALAR_CTYPE = {"f": "float", "i": "int32_t", "u": "uint32_t", "b": "bool"}
VEC_CTYPE = {"f": "V", "i": "IV", "u": "UV"}


def ctype(t: Tuple[str, int]) -> str:
    base, comps = t
    if comps == 0:
        return "void"
    if comps > 1:
        return VEC_CTYPE[base]
    return SCALAR_CTYPE[base]


def ty_key(t: Tuple[str, int]) -> str:
    base, comps = t
    if comps > 1:
        return VEC_CTYPE[base]
    return SCALAR_CTYPE[base]


class CodeGen:
    def __init__(self, name: str, program: Dict):
        self.name = name
        self.program = program
        self.sigs: Dict[str, FuncSig] = {}
        for f in program["funcs"]:
            self.sigs[f["name"]] = FuncSig(f["ret"], f["params"])
        self.scopes: List[Dict[str, Tuple[str, int]]] = []
        # program-level symbols (textures, samplers, cbuffer fields)
        self.globals: Dict[str, Tuple[str, int]] = {}
        for t in program["textures"]:
            self.globals[t] = ("tex", 1)
        for t in program["samplers"]:
            self.globals[t] = ("smp", 1)
        for cb in program["cbuffers"]:
            for f in cb["fields"]:
                self.globals[f["name"]] = ("f", 4)
        self.out: List[str] = []
        self.indent = 1
        self.entry = None

    # -- scope helpers --
    def push(self):
        self.scopes.append({})

    def pop(self):
        self.scopes.pop()

    def declare(self, name: str, t: Tuple[str, int]):
        if not self.scopes:
            raise HlslError(f"{self.name}: declaration outside scope: {name}")
        self.scopes[-1][name] = t

    def lookup(self, name: str) -> Optional[Tuple[str, int]]:
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return self.globals.get(name)

    def line(self, text: str):
        self.out.append("    " * self.indent + text)

    # -- type helpers --
    def expr_type(self, e) -> Tuple[str, int]:
        k = e[0]
        if k == "num":
            txt: str = e[1]
            low = txt.lower()
            if low.startswith("0x"):
                # Hex digits e/f are not exponent/float suffixes.
                val = int(low.rstrip("u"), 16)
                return ("u", 1) if low.endswith("u") or val > 0x7fffffff else ("i", 1)
            if "u" in low and not low.endswith("f"):
                return ("u", 1)
            if any(c in txt for c in ".eE") or txt.lower().endswith("f"):
                return ("f", 1)
            return ("i", 1)
        if k == "bool":
            return ("b", 1)
        if k == "id":
            name = e[1]
            t = self.lookup(name)
            if t is not None:
                return t
            if name in ("float", "int", "uint", "bool"):
                return ({"float": "f", "int": "i", "uint": "u", "bool": "b"}[name], 1)
            raise HlslError(f"{self.name}: unknown identifier {name!r} (line {e[2]})")
        if k == "cast":
            return e[1]
        if k == "member":
            base_t = self.expr_type(e[1])
            sw = e[2]
            if base_t[1] <= 1:
                raise HlslError(f"{self.name}: swizzle on scalar: .{sw}")
            return (base_t[0], len(sw))
        if k == "index":
            base_t = self.expr_type(e[1])
            return base_t
        if k == "unary":
            op = e[1]
            if op == "!":
                return ("b", 1)
            return self.expr_type(e[2])
        if k == "tern":
            ta = self.expr_type(e[2])
            tb = self.expr_type(e[3])
            return self.common_type(ta, tb)
        if k == "bin":
            op = e[1]
            if op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return ("b", 1)
            ta = self.expr_type(e[2])
            tb = self.expr_type(e[3])
            if op in ("&", "|", "^", "<<", ">>"):
                if ta[1] > 1 or tb[1] > 1:
                    return ("u" if "u" in (ta[0], tb[0]) else "i", max(ta[1], tb[1]))
                return ("u" if "u" in (ta[0], tb[0]) else "i", 1)
            return self.common_type(ta, tb)
        if k == "assign":
            return self.expr_type(e[2])
        if k == "call":
            callee = e[1]
            if callee[0] == "id" and TYPE_RE.match(callee[1]):
                m = TYPE_RE.match(callee[1])
                base = {"float": "f", "int": "i", "uint": "u", "bool": "b"}[m.group(1)]
                return (base, int(m.group(2) or 1))
            if callee[0] == "id" and callee[1] in self.sigs:
                return self.sigs[callee[1]].ret
            if callee[0] == "member" and callee[2] in ("Load", "SampleLevel", "Sample"):
                return ("f", 4)
            if callee[0] == "id" and callee[1] in BUILTINS:
                nm = callee[1]
                tys = [self.expr_type(a) for a in e[2]]
                if nm in ("dot", "length"):
                    return ("f", 1)
                if nm == "normalize":
                    return ("f", 4)
                cty = ("f", 1)
                for t in tys:
                    cty = self.common_type(cty, t)
                if nm in ("min", "max", "abs") and cty[0] in ("i", "u"):
                    return (cty[0], cty[1])
                return cty
            raise HlslError(f"{self.name}: cannot infer return type of {callee}")
        if k in ("postinc", "preinc"):
            return self.expr_type(e[2])
        raise HlslError(f"{self.name}: cannot infer type of node {k}")

    @staticmethod
    def common_type(a: Tuple[str, int], b: Tuple[str, int]) -> Tuple[str, int]:
        if a == b:
            return a
        comps = max(a[1], b[1])
        bases = {a[0], b[0]}
        if "f" in bases:
            base = "f"
        elif "i" in bases:
            base = "i"
        elif "u" in bases:
            base = "u"
        else:
            base = "b"
        if comps == 1 and (a[1] > 1 or b[1] > 1):
            comps = max(a[1], b[1])
        return (base, comps)

    # -- conversion emission --
    def conv(self, code: str, src: Tuple[str, int], dst: Tuple[str, int]) -> str:
        if src == dst:
            return code
        sd, dd = src, dst
        if dd == ("f", 1):
            return f"tof({code})"
        if dd == ("i", 1):
            return f"toi({code})"
        if dd == ("u", 1):
            return f"tou({code})"
        if dd == ("b", 1):
            return f"(({code}) != 0)"
        if dd[1] > 1:
            fn = {"f": "toV", "i": "toIV", "u": "toUV"}[dd[0]]
            if sd[1] == 1:
                if sd[0] == "b":
                    return f"{fn}({code} ? 1.f : 0.f)"
                return f"{fn}({code})"
            return f"{fn}({code})"
        raise HlslError(f"{self.name}: unsupported conversion {src} -> {dst}")

    # -- number literals --
    def num_literal(self, txt: str, want: Tuple[str, int]) -> str:
        low = txt.lower()
        if low.startswith("0x"):
            # HLSL hex literals (e.g. the PCG hash constants `0x5bd1u`)
            val = int(txt.rstrip("uU"), 16)
            if want[1] > 1:
                return f"{VEC_CTYPE[want[0]]}({self.num_literal(txt, (want[0], 1))})"
            return {"f": f"{float(val)}f", "u": f"{val}u", "i": str(val),
                    "b": "(true)" if val else "(false)"}[want[0]]
        if want[1] > 1:
            return f"{VEC_CTYPE[want[0]]}({self.num_literal(txt, (want[0], 1))})"
        return self._num_literal_decimal(txt, low, want)

    def _num_literal_decimal(self, txt: str, low: str, want: Tuple[str, int]) -> str:
        is_float_lit = any(c in txt for c in ".eE") or low.endswith("f")
        if want[0] == "f":
            if is_float_lit:
                return txt if low.endswith("f") else txt + "f"
            return txt + ".0f"
        if want[0] == "u":
            return txt.rstrip("uU") + "u"
        if want[0] == "i":
            return txt.rstrip("uU")
        if want[0] == "b":
            return "(true)" if txt not in ("0", "0.0") else "(false)"
        raise HlslError(f"{self.name}: bad literal {txt}")

    # -- expression emission --
    def emit(self, e, want: Optional[Tuple[str, int]] = None) -> str:
        k = e[0]
        ty = self.expr_type(e)
        if k == "num":
            code = self.num_literal(e[1], ty)
            return self.conv(code, ty, want) if want and want != ty else code
        if k == "bool":
            code = "true" if e[1] else "false"
            return self.conv(code, ("b", 1), want) if want else code
        if k == "id":
            code = e[1]
            return self.conv(code, ty, want) if want and want != ty else code
        if k == "member":
            obj = self.emit(e[1])
            sw = e[2]
            comp_idx = {"x": 0, "y": 1, "z": 2, "w": 3,
                        "r": 0, "g": 1, "b": 2, "a": 3}
            if any(c not in comp_idx for c in sw):
                raise HlslError(f"{self.name}: unsupported swizzle .{sw}")
            idxs = [comp_idx[c] for c in sw]
            if len(idxs) == 1:
                code = f"({obj}).{'xyzw'[idxs[0]]}"
            else:
                base = self.expr_type(e[1])
                prefix = {"f": "swz", "i": "iswz", "u": "uswz"}[base[0]]
                if len(idxs) == 2:
                    code = f"{prefix}2({obj}, {idxs[0]}, {idxs[1]})"
                elif len(idxs) == 3:
                    code = f"{prefix}3({obj}, {idxs[0]}, {idxs[1]}, {idxs[2]})"
                else:
                    code = f"swz4({obj}, {idxs[0]}, {idxs[1]}, {idxs[2]}, {idxs[3]})"
            return self.conv(code, ty, want) if want and want != ty else code
        if k == "index":
            code = f"({self.emit(e[1])})[{self.emit(e[2], ('i', 1))}]"
            return self.conv(code, ty, want) if want and want != ty else code
        if k == "cast":
            src = self.expr_type(e[2])
            code = self.emit(e[2])
            return self.conv(code, src, e[1])
        if k == "unary":
            op, val = e[1], self.emit(e[2])
            if op == "+":
                return val
            if op == "!":
                code = f"(!({val}))"
            else:
                code = f"({op}({val}))"
            return self.conv(code, ty, want) if want and want != ty else code
        if k in ("preinc", "postinc"):
            op, val = e[1], self.emit(e[2])
            return f"({op}{val})" if k == "preinc" else f"({val}{op})"
        if k == "tern":
            tc = self.expr_type(e[2])
            tb = self.expr_type(e[3])
            cty = self.common_type(tc, tb)
            a = self.emit(e[2], cty)
            b = self.emit(e[3], cty)
            code = f"(({self.emit(e[1])}) ? ({a}) : ({b}))"
            return self.conv(code, cty, want) if want and want != cty else code
        if k == "bin":
            return self.emit_bin(e, want)
        if k == "assign":
            return self.emit_assign(e)
        if k == "call":
            return self.emit_call(e, want)
        raise HlslError(f"{self.name}: cannot emit node {k}")

    def emit_bin(self, e, want=None) -> str:
        op = e[1]
        ta, tb = self.expr_type(e[2]), self.expr_type(e[3])
        if op in ("==", "!=", "<", ">", "<=", ">="):
            a = self.emit(e[2])
            b = self.emit(e[3])
            code = f"(({a}) {op} ({b}))"
            return code
        if op in ("&&", "||"):
            return f"(({self.emit(e[2])}) {op} ({self.emit(e[3])}))"
        if op in ("&", "|", "^", "<<", ">>"):
            cty = self.common_type(ta, tb)
            if cty[0] not in ("i", "u"):
                cty = ("i" if cty[0] != "u" else "u", cty[1])
            a = self.emit(e[2], cty)
            b = self.emit(e[3], cty)
            return f"(({a}) {op} ({b}))"
        cty = self.common_type(ta, tb)
        a = self.emit(e[2], cty)
        b = self.emit(e[3], cty)
        if op == "%":
            if cty[0] == "f":
                code = f"fmod({a}, {b})"
            else:
                code = f"(({a}) % ({b}))"
        else:
            code = f"(({a}) {op} ({b}))"
        return self.conv(code, cty, want) if want and want != cty else code

    def emit_assign(self, e) -> str:
        op, target, value = e[1], e[2], e[3]
        tgt = self.emit(target)
        ty = self.expr_type(target)
        val = self.emit(value, ty)
        return f"{tgt} {op} {val}"

    def emit_call(self, e, want=None) -> str:
        callee, args, line = e[1], e[2], e[3]
        if callee[0] == "id":
            nm = callee[1]
            m = TYPE_RE.match(nm)
            if m:
                base = {"float": "f", "int": "i", "uint": "u", "bool": "b"}[m.group(1)]
                comps = int(m.group(2) or 1)
                return self.emit_ctor((base, comps), args, want)
            if nm in self.sigs:
                sig = self.sigs[nm]
                parts = []
                for i, a in enumerate(args):
                    if i < len(sig.params):
                        p = sig.params[i]
                        if p["out"]:
                            parts.append(self.emit(a))
                        else:
                            parts.append(self.emit(a, p["type"]))
                    else:
                        parts.append(self.emit(a))
                code = f"{nm}({', '.join(parts)})"
                rty = sig.ret
                if want and rty != want:
                    code = self.conv(code, rty, want)
                return code
            if nm in ("min", "max", "clamp", "saturate", "lerp", "smoothstep", "dot",
                      "length", "normalize", "pow", "exp", "sin", "cos", "acos", "atan2",
                      "sqrt", "rsqrt", "abs", "floor", "ceil", "frac", "fmod", "sign"):
                # component-wise helpers: decide on a common vector width
                tys = [self.expr_type(a) for a in args]
                vec = [t for t in tys if t[1] > 1]
                if vec:
                    cty = vec[0]
                    for t in vec[1:]:
                        cty = self.common_type(cty, t)
                    cty = (cty[0], max(t[1] for t in tys if t[1] > 1))
                    if nm in ("dot", "length", "normalize"):
                        a = self.emit(args[0])
                        b = self.emit(args[1]) if len(args) > 1 else None
                        if nm == "dot":
                            code = f"dot({a}, {b})"
                        elif nm == "length":
                            code = f"length({a})"
                        else:
                            code = f"normalize({a})"
                        return code
                    parts = []
                    for a, t in zip(args, tys):
                        if nm == "clamp" and len(args) == 3:
                            parts.append(self.emit(a, cty))
                        elif nm == "saturate":
                            parts.append(self.emit(a, cty))
                        elif t[1] > 1:
                            parts.append(self.emit(a, cty))
                        elif nm in ("lerp", "smoothstep") and len(parts) == len(args) - 1:
                            parts.append(self.emit(a, ("f", 1)))
                        else:
                            parts.append(self.emit(a, ("f", 1)))
                    code = f"{nm}({', '.join(parts)})"
                    ret_ty = cty if nm not in ("dot", "length") else ("f", 1)
                    if nm == "clamp" and len(args) == 3 and tys[1][1] == 1 and tys[2][1] == 1:
                        code = f"clamp({parts[0]}, {parts[1]}, {parts[2]})"
                    if want and ret_ty != want:
                        code = self.conv(code, ret_ty, want)
                    return code
                # all-scalar intrinsic
                if nm in ("dot", "length", "normalize", "smoothstep", "lerp", "sign"):
                    pass
                cty = tys[0] if tys else ("f", 1)
                for t in tys[1:]:
                    cty = self.common_type(cty, t)
                parts = [self.emit(a, cty) for a in args]
                code = f"{nm}({', '.join(parts)})"
                if want and cty != want:
                    code = self.conv(code, cty, want)
                return code
            raise HlslError(f"{self.name}: unsupported function call {nm}() at line {line}")
        if callee[0] == "member":
            obj = self.emit(callee[1])
            mname = callee[2]
            if mname == "Load":
                args_s = [self.emit(a, ("i", 4)) for a in args]
                return f"({obj}).Load({', '.join(args_s)})"
            if mname in ("SampleLevel", "Sample"):
                smp = self.emit(args[0])
                uv = self.emit(args[1], ("f", 4))
                lod = self.emit(args[2], ("f", 1)) if len(args) > 2 else "0.f"
                return f"({obj}).SampleLevel({smp}, {uv}, {lod})"
            raise HlslError(f"{self.name}: unsupported texture method .{mname}()")
        raise HlslError(f"{self.name}: indirect call at line {line}")

    def emit_ctor(self, ty: Tuple[str, int], args, want=None) -> str:
        base, comps = ty
        if comps == 1:
            if len(args) != 1:
                raise HlslError(f"{self.name}: scalar constructor with {len(args)} args")
            return self.conv(self.emit(args[0]), self.expr_type(args[0]), ty)
        cty = (base, comps)
        cname = VEC_CTYPE[base]
        tys = [self.expr_type(a) for a in args]
        if len(args) == 1:
            code = self.conv(self.emit(args[0]), tys[0], cty)
            return self.conv(code, cty, want) if want and want != cty else code
        if len(args) == 2 and tys[0][1] > 1:
            first = self.emit(args[0], (base, max(tys[0][1], 2)))
            second = self.emit(args[1], (base, 1))
            code = f"{cname}({first}, {second})"
            return self.conv(code, cty, want) if want and want != cty else code
        if len(args) == comps:
            parts = [self.emit(a, (base, 1)) for a in args]
            code = f"{cname}({', '.join(parts)})"
            return self.conv(code, cty, want) if want and want != cty else code
        if len(args) == 2 and tys[0][1] == 1 and tys[1][1] == 1:
            parts = [self.emit(a, (base, 1)) for a in args]
            code = f"{cname}({', '.join(parts)})"
            return self.conv(code, cty, want) if want and want != cty else code
        raise HlslError(f"{self.name}: unsupported {cname} constructor with {len(args)} args")

    # -- statements --
    def emit_decl(self, node) -> str:
        (base, comps), items = node[1], node[2]
        t = (base, comps)
        ct = ctype(t)
        if comps > 1:
            init0 = f"{ct}({ {'f':'0.f','i':'0','u':'0u'}[base] })"
        else:
            init0 = {"f": "0.f", "i": "0", "u": "0u", "b": "false"}[base]
        parts = []
        for nm, count, init in items:
            if count is not None:
                raise HlslError(f"{self.name}: local arrays are not supported ({nm})")
            self.declare(nm, t)
            if init is not None:
                parts.append(f"{nm} = {self.emit(init, t)}")
            else:
                parts.append(f"{nm} = {init0}")
        return f"{ct} " + ", ".join(parts) + ";"

    def emit_stmt(self, node):
        k = node[0]
        if k == "block":
            self.line("{")
            self.indent += 1
            self.push()
            for s in node[1]:
                self.emit_stmt(s)
            self.pop()
            self.indent -= 1
            self.line("}")
            return
        if k == "decl":
            self.line(self.emit_decl(node))
            return
        if k == "expr":
            ty = self.expr_type(node[1])
            code = self.emit(node[1])
            if ty[1] > 1 or ty[0] == "b":
                code = f"(void)({code})" if ty[0] != "b" else f"(void)({code})"
            self.line(code + ";")
            return
        if k == "if":
            self.line(f"if ({self.emit(node[1])})")
            self.emit_embedded(node[2])
            if node[3] is not None:
                self.line("else")
                self.emit_embedded(node[3])
            return
        if k == "for":
            init, cond, step, body = node[1], node[2], node[3], node[4]
            self.push()
            if init is None:
                init_s = ""
            elif init[0] == "decl":
                init_s = self.emit_decl(init)[:-1]
            else:
                init_s = self.emit(init[1])
            cond_s = self.emit(cond) if cond is not None else ""
            step_s = self.emit(step) if step is not None else ""
            self.line(f"for ({init_s}; {cond_s}; {step_s})")
            self.emit_embedded(body)
            self.pop()
            return
        if k == "return":
            if node[1] is None:
                self.line("return;")
            else:
                want = self.current_ret
                self.line(f"return {self.emit(node[1], want)};")
            return
        if k == "break":
            self.line("break;")
            return
        if k == "continue":
            self.line("continue;")
            return
        raise HlslError(f"{self.name}: unsupported statement {k}")

    def emit_embedded(self, node):
        if node[0] == "block":
            self.emit_stmt(node)
        else:
            self.line("{")
            self.indent += 1
            self.push()
            self.emit_stmt(node)
            self.pop()
            self.indent -= 1
            self.line("}")

    # -- top level --
    def generate(self) -> str:
        prog = self.program
        lines: List[str] = []
        lines.append(f"namespace sh_{self.name} {{")
        lines.append("using namespace hlsl;")
        for tex in prog["textures"]:
            lines.append(f"static Tex {tex};")
        for smp in prog["samplers"]:
            lines.append(f"static Smp {smp};")
        # cbuffer
        self.cb_fields = []
        if prog["cbuffers"]:
            if len(prog["cbuffers"]) > 1:
                raise HlslError(f"{self.name}: multiple cbuffers unsupported")
            cb = prog["cbuffers"][0]
            lines.append("struct CBC {")
            for f in cb["fields"]:
                if f["count"]:
                    lines.append(f"    V {f['name']}[{f['count']}];")
                else:
                    lines.append(f"    V {f['name']};")
            lines.append("};")
            lines.append("static CBC cb;")
            for f in cb["fields"]:
                if f["count"]:
                    lines.append(f"static V (&{f['name']})[{f['count']}] = cb.{f['name']};")
                else:
                    lines.append(f"static V& {f['name']} = cb.{f['name']};")
            self.cb_fields = cb["fields"]
        lines.append("")
        # function prototypes then bodies
        for f in prog["funcs"]:
            ret = ctype(f["ret"])
            params = []
            for p in f["params"]:
                ct = ctype(p["type"])
                if p["out"]:
                    params.append(f"{ct}& {p['name']}")
                else:
                    params.append(f"{ct} {p['name']}")
            lines.append(f"static {ret} {f['name']}({', '.join(params)});")
        lines.append("")
        for f in prog["funcs"]:
            ret = ctype(f["ret"])
            params = []
            for p in f["params"]:
                ct = ctype(p["type"])
                if p["out"]:
                    params.append(f"{ct}& {p['name']}")
                else:
                    params.append(f"{ct} {p['name']}")
            self.out = []
            self.indent = 1
            self.scopes = []
            self.push()
            for p in f["params"]:
                if p["name"]:
                    self.declare(p["name"], p["type"])
            self.current_ret = f["ret"]
            self.line("{")
            self.indent = 2
            self.emit_stmt(f["body"])
            self.indent = 1
            self.line("}")
            body = "\n".join(self.out)
            lines.append(f"static {ret} {f['name']}({', '.join(params)})")
            lines.append(body)
            lines.append("")
        # entry point detection
        entry_fn = None
        for f in prog["funcs"]:
            if f["ret"] == ("f", 4) and len(f["params"]) == 1:
                entry_fn = f
                if f["name"] == self.name:
                    break
        if entry_fn is None:
            raise HlslError(f"{self.name}: no 'float4 name(float4 pos : SV_Position)' entry point")
        # per-shader run wrapper
        lines.append("int run(int w, int h, const float* p, int np, const vl_tex* texs, int nt, unsigned char* out) {")
        idx = 0
        for tex in prog["textures"]:
            lines.append(f"    {tex}.w = (idx_tex < nt) ? texs[idx_tex].w : 0;")
            lines.append(f"    {tex}.h = (idx_tex < nt) ? texs[idx_tex].h : 0;")
            lines.append(f"    {tex}.px = (idx_tex < nt) ? reinterpret_cast<const V*>(texs[idx_tex].px) : nullptr;")
            lines.append("    ++idx_tex;")
        for smp in prog["samplers"]:
            lines.append(f"    {smp}.filter = g_filter; {smp}.address = g_address;")
        if self.cb_fields:
            fi = 0
            for f in self.cb_fields:
                if f["count"]:
                    lines.append(f"    for (int i = 0; i < {f['count']}; ++i) {{")
                    lines.append(f"        cb.{f['name']}[i] = V(")
                    lines.append("            (idx_cb + 0 < np) ? p[idx_cb + 0] : 0.f,")
                    lines.append("            (idx_cb + 1 < np) ? p[idx_cb + 1] : 0.f,")
                    lines.append("            (idx_cb + 2 < np) ? p[idx_cb + 2] : 0.f,")
                    lines.append("            (idx_cb + 3 < np) ? p[idx_cb + 3] : 0.f);")
                    lines.append("        idx_cb += 4;")
                    lines.append("    }")
                else:
                    lines.append(f"    cb.{f['name']} = V(")
                    lines.append("        (idx_cb + 0 < np) ? p[idx_cb + 0] : 0.f,")
                    lines.append("        (idx_cb + 1 < np) ? p[idx_cb + 1] : 0.f,")
                    lines.append("        (idx_cb + 2 < np) ? p[idx_cb + 2] : 0.f,")
                    lines.append("        (idx_cb + 3 < np) ? p[idx_cb + 3] : 0.f);")
                    lines.append("    idx_cb += 4;")
        lines.append("    for (int y = 0; y < h; ++y) {")
        lines.append("        for (int x = 0; x < w; ++x) {")
        lines.append(f"            V pos((float)x + 0.5f, (float)y + 0.5f, 0.f, 1.f);")
        lines.append(f"            V c = {entry_fn['name']}(pos);")
        lines.append("            size_t o = ((size_t)y * (size_t)w + (size_t)x) * 4u;")
        lines.append("            out[o + 0] = (unsigned char)(saturate(c.x) * 255.f + 0.5f);")
        lines.append("            out[o + 1] = (unsigned char)(saturate(c.y) * 255.f + 0.5f);")
        lines.append("            out[o + 2] = (unsigned char)(saturate(c.z) * 255.f + 0.5f);")
        lines.append("            out[o + 3] = (unsigned char)(saturate(c.w) * 255.f + 0.5f);")
        lines.append("        }")
        lines.append("    }")
        lines.append("    return 0;")
        lines.append("}")
        lines.append(f"}}  // namespace sh_{self.name}")
        # fix indices
        header = "\n".join(lines)
        header = header.replace("int run(int w, int h, const float* p, int np, const vl_tex* texs, int nt, unsigned char* out) {",
                                "int run(int w, int h, const float* p, int np, const vl_tex* texs, int nt, unsigned char* out) {\n"
                                "    int idx_cb = 0; int idx_tex = 0;")
        return header


def transpile(name: str, source: str) -> str:
    """Transpile one HLSL shader block into a C++ namespace implementing `run`."""
    p = Parser(tokenize(source), name)
    prog = p.parse_program()
    cg = CodeGen(name, prog)
    return cg.generate()
