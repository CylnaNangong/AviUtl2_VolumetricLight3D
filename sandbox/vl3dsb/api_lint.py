"""
Linter for the host-API surface of an AviUtl2 Lua script.

Combines :mod:`vl3dsb.luasurface` (what the script actually calls) with
:mod:`vl3dsb.apidoc` (what the official manual promises) and reports drift in
either direction:

  * a call to a function the manual does not document,
  * an argument count outside the documented signature,
  * a literal key / enum value that is not in the documented set,
  * a read of an `obj.*` field the manual does not list,
  * (optional) a `obj.pixelshader("name")` without a matching shader block.

Every rule is *opt-in strict*: an undocumented key is accepted when the call is
guarded (`pcall` or a `type(...)` check within a few lines), because that is
exactly how a script has to probe host extensions.  Guarded keys are reported
as notes, not issues, so the check stays green on forward-compatible scripts
while still failing on real mistakes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import apidoc, luasurface

_NUM_RE = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


@dataclass
class SurfaceReport:
    issues: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues


def _is_table(expr: str) -> bool:
    return expr.strip().startswith("{")


def _is_number(expr: str) -> bool:
    s = expr.strip()
    return bool(_NUM_RE.match(s)) or bool(re.fullmatch(r"0[xX][0-9a-fA-F]+", s))


def _guarded(lines: List[str], line_no: int, span: int = 3) -> bool:
    """True when a pcall / type() check surrounds the call (compat probing)."""
    lo = max(0, line_no - 1 - span)
    hi = min(len(lines), line_no + span)
    window = "\n".join(lines[lo:hi])
    return ("pcall" in window) or ("type(" in window)


def lint(text: str, shader_names: Optional[Set[str]] = None) -> SurfaceReport:
    surf = luasurface.scan(text)
    lines = text.splitlines()
    rep = SurfaceReport()
    called = set(surf.names)

    for c in surf.calls:
        doc = apidoc.FUNCS.get(c.name)
        if doc is None:
            rep.issues.append(f"L{c.line}: obj.{c.name}() is not a documented function")
            continue
        if not doc.accepts(c.n_args):
            rep.issues.append(
                f"L{c.line}: obj.{c.name}() takes {doc.signature()} "
                f"(docs), script passes {c.n_args}")
        key = c.key
        guarded = _guarded(lines, c.line)

        # --- per-function key / enum validation -------------------------
        if c.name == "getinfo" and key is not None and key not in apidoc.INFO_KEYS:
            (rep.notes if guarded else rep.issues).append(
                f"L{c.line}: obj.getinfo({key!r}) is not a documented key"
                + (" (guarded)" if guarded else ""))
        elif c.name == "getoption" and key is not None and key not in apidoc.OPTION_KEYS_GET:
            (rep.notes if guarded else rep.issues).append(
                f"L{c.line}: obj.getoption({key!r}) is not a documented key")
        elif c.name == "setoption" and key is not None and key not in apidoc.OPTION_KEYS_SET:
            rep.issues.append(f"L{c.line}: obj.setoption({key!r}) is not a documented option")
        elif c.name == "load" and key is not None and key not in apidoc.LOAD_KINDS:
            rep.issues.append(f"L{c.line}: obj.load({key!r}) is not a documented kind")
        elif c.name in ("clearbuffer", "copybuffer", "getpixeldata") and key is not None:
            ok = apidoc.is_buffer_source(key) if c.name == "copybuffer" else apidoc.is_buffer_target(key)
            if not ok:
                rep.issues.append(f"L{c.line}: obj.{c.name}({key!r}) is not a documented buffer")
        elif c.name == "putpixeldata" and key is not None and not apidoc.is_buffer_target(key):
            rep.issues.append(f"L{c.line}: obj.putpixeldata({key!r}) is not a documented buffer")
        elif c.name == "pixelshader":
            if shader_names is not None and key is not None and key not in shader_names:
                rep.issues.append(f"L{c.line}: obj.pixelshader({key!r}) has no shader block")
            if c.n_args >= 5:
                blend = c.literal(4)
                if blend is not None and blend not in apidoc.SHADER_BLENDS:
                    rep.issues.append(f"L{c.line}: pixelshader blend {blend!r} is not documented")
            if c.n_args >= 6:
                samp = c.literal(5)
                if samp is not None and samp not in apidoc.SAMPLERS:
                    rep.issues.append(f"L{c.line}: pixelshader sampler {samp!r} is not documented")
            if c.n_args >= 3 and c.literal(1) is not None and not apidoc.is_buffer_target(c.literal(1)):
                rep.issues.append(f"L{c.line}: pixelshader target {c.literal(1)!r} is not a buffer")
        elif c.name == "setfont" and c.n_args >= 3 and _is_number(c.args[2]):
            t = int(float(c.args[2]))
            if not 0 <= t <= 6:
                rep.issues.append(f"L{c.line}: setfont decoration type {t} outside 0..6")
        elif c.name == "drawpoly":
            if _is_table(c.args[0]):
                first = c.args[0].strip()
                if first.startswith("{ {"):
                    rep.notes.append(f"L{c.line}: drawpoly vertex-table form (checked at runtime)")
            if c.n_args >= 2 and _is_number(c.args[1]):
                n = int(float(c.args[1]))
                if n not in (3, 4):
                    rep.issues.append(f"L{c.line}: drawpoly vertex_num {n} is not 3/4")

    # --- obj.* field reads ---------------------------------------------
    for name, lns in sorted(surf.fields.items()):
        if name in called:
            continue                      # `type(obj.load)` etc. -- a function handle
        if name in apidoc.FIELDS:
            continue
        seen_lines: Set[int] = set()
        for ln in lns:
            if ln in seen_lines:
                continue
            seen_lines.add(ln)
            where = f"L{ln}: obj.{name}"
            if _guarded(lines, ln):
                rep.notes.append(where + " is not a documented field (guarded)")
            else:
                rep.issues.append(where + " is not a documented field")

    # --- writes to read-only fields ------------------------------------
    for name, lns in sorted(surf.writes.items()):
        if name in apidoc.FIELDS and apidoc.FIELDS[name]:
            rep.issues.append(f"L{lns[0]}: obj.{name} is documented as read-only")

    rep.stats = {
        "calls": len(surf.calls),
        "functions": len(called),
        "fields": len([n for n in surf.fields if n not in called]),
        "documented_functions": len(apidoc.FUNCS),
    }
    return rep


__all__ = ["SurfaceReport", "lint"]
