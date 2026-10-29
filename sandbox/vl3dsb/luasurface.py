"""
Small, dependency-free scanner for the *host API surface* an AviUtl2 script uses.

The sandbox executes the real Lua body (lupa/LuaJIT), which proves that the
script runs against the emulated host -- but it cannot prove that the emulated
host still matches ``https://docs.aviutl2.jp/lua/``.  This module extracts the
static surface (`obj.foo(...)` call sites with their argument texts,
`obj.field` reads/writes and the string literals handed to the host) so the
checks can compare it against the documented signatures in
:mod:`vl3dsb.apidoc`.

The scanner is intentionally shallow: it only has to understand Lua's lexical
structure (comments, long strings, bracket nesting) well enough to split
argument lists.  It does not evaluate expressions -- argument *count* and
*literal* keys are all the API contract needs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_OBJ_RE = re.compile(r"(?<![\w.$])obj\s*\.\s*([A-Za-z_]\w*)")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


@dataclass
class Call:
    name: str
    args: List[str]
    line: int
    obj_word: str = "obj"          # `obj` (host table) - kept for future aliases

    @property
    def n_args(self) -> int:
        return len(self.args)

    def literal(self, idx: int) -> Optional[str]:
        """String literal passed at position ``idx`` (without quotes), else None."""
        if idx >= len(self.args):
            return None
        return _lua_string(self.args[idx])

    @property
    def key(self) -> Optional[str]:
        """First argument when it is a plain string literal (the usual API key)."""
        return self.literal(0)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"obj.{self.name}({', '.join(self.args)}) @L{self.line}"


@dataclass
class Surface:
    calls: List[Call] = field(default_factory=list)
    fields: Dict[str, List[int]] = field(default_factory=dict)        # read or write
    writes: Dict[str, List[int]] = field(default_factory=dict)        # obj.x = ...

    def calls_of(self, name: str) -> List[Call]:
        return [c for c in self.calls if c.name == name]

    @property
    def names(self) -> List[str]:
        return sorted({c.name for c in self.calls})

    def keys_of(self, name: str, idx: int = 0) -> Dict[str, List[int]]:
        """{literal key: [line, ...]} for one API function."""
        out: Dict[str, List[int]] = {}
        for c in self.calls_of(name):
            k = c.literal(idx)
            if k is not None:
                out.setdefault(k, []).append(c.line)
        return out


# ---------------------------------------------------------------------------
# lexical helpers
# ---------------------------------------------------------------------------
def _lua_string(expr: str) -> Optional[str]:
    """`\"text\"` / `'text'` -> text, else None."""
    s = expr.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        body = s[1:-1]
        if s[0] in body:          # `"layer" .. x` -- a concatenation, not a literal
            return None
        if "\\" not in body:
            return body
        # only the escapes that matter for keys
        return body.replace('\\"', '"').replace("\\\\", "\\").replace("\\'", "'")
    return None


def _looks_like_lua(text: str) -> bool:
    return bool(re.search(r"(?<![\w.$])obj\s*\.", text))


def mask_noncode(text: str) -> str:
    """Blank out comments and string literals, preserving offsets/line breaks.

    The result keeps the same length as ``text`` so indices stay valid; string
    contents are replaced by spaces (that is why literal keys are read from the
    *original* text, not from the mask).
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "-" and text.startswith("--", i):
            if text.startswith("--[", i):
                lvl = _long_bracket_level(text, i + 2)
                if lvl is not None:
                    end = _long_bracket_end(text, i + 2, lvl)
                    for k in range(i, min(end, n)):
                        if out[k] != "\n":
                            out[k] = " "
                    i = end
                    continue
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "[":
            lvl = _long_bracket_level(text, i)
            if lvl is not None:
                end = _long_bracket_end(text, i, lvl)
                for k in range(i, min(end, n)):
                    if out[k] != "\n":
                        out[k] = " "
                i = end
                continue
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == c:
                    j += 1
                    break
                if text[j] == "\n":       # unterminated: stop at EOL
                    break
                j += 1
            for k in range(i, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _long_bracket_level(text: str, i: int) -> Optional[int]:
    """`[==[` -> 2, `[[` -> 0, else None."""
    if i >= len(text) or text[i] != "[":
        return None
    j = i + 1
    lvl = 0
    while j < len(text) and text[j] == "=":
        lvl += 1
        j += 1
    if j < len(text) and text[j] == "[":
        return lvl
    return None


def _long_bracket_end(text: str, i: int, lvl: int) -> int:
    close = "]" + "=" * lvl + "]"
    j = text.find(close, i)
    return len(text) if j < 0 else j + len(close)


_PAIRS = {"(": ")", "[": "]", "{": "}"}


def match_bracket(masked: str, start: int) -> int:
    """Index just past the bracket that balances ``masked[start]``."""
    open_c = masked[start]
    close_c = _PAIRS[open_c]
    depth = 0
    for i in range(start, len(masked)):
        if masked[i] == open_c:
            depth += 1
        elif masked[i] == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
    return len(masked)


def split_args(masked: str, start: int, end: int, source: str = "") -> List[Tuple[int, int]]:
    """Split ``masked[start:end]`` (the inside of a call) at top-level commas.

    ``source`` is the *unmasked* text; it decides whether a span is empty,
    because a bare string literal is completely blank in the mask.
    """
    src = source or masked
    spans: List[Tuple[int, int]] = []
    depth = 0
    last = start
    for i in range(start, end):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((last, i))
            last = i + 1
    if src[last:end].strip():
        spans.append((last, end))
    return spans


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def scan(text: str) -> Surface:
    """Extract the `obj.*` surface of a Lua script (comments/strings ignored)."""
    masked = mask_noncode(text)
    surf = Surface()
    for m in _OBJ_RE.finditer(masked):
        name = m.group(1)
        after = m.end()
        while after < len(masked) and masked[after] in " \t":
            after += 1
        line = _line_of(masked, m.start())
        if after < len(masked) and masked[after] == "(":
            close = match_bracket(masked, after)
            spans = split_args(masked, after + 1, close - 1, source=text)
            args = [text[a:b].strip() for a, b in spans]
            surf.calls.append(Call(name=name, args=args, line=line))
            continue
        # field read / write
        surf.fields.setdefault(name, []).append(line)
        # `obj.x = value` (but not `==`, `<=`, `~=`)
        j = after
        while j < len(masked) and masked[j] in " \t":
            j += 1
        if j < len(masked) and masked[j] == "=":
            nxt = masked[j + 1:j + 2]
            if nxt != "=":
                surf.writes.setdefault(name, []).append(line)
    return surf


def call_sites_with_key(text: str, fn: str) -> Dict[str, List[Call]]:
    """{literal key: [Call, ...]} for e.g. ``fn="getinfo"``."""
    out: Dict[str, List[Call]] = {}
    for c in scan(text).calls_of(fn):
        k = c.key
        if k is not None:
            out.setdefault(k, []).append(c)
    return out


__all__ = ["Call", "Surface", "scan", "mask_noncode", "match_bracket",
           "split_args", "call_sites_with_key"]
