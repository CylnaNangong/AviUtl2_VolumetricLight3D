"""
Parser for AviUtl ExEdit2 custom object scripts (.obj2) and language packs
(.aul2).

Only the declarative parts are parsed (metadata, parameter/group declarations,
visibility conditions, HLSL blocks); the Lua body is executed verbatim by the
sandbox host.  This is enough to

  * discover every parameter + default value (the host injects them as globals),
  * lint `--hide@key:` expressions against declared keys,
  * cross-check the .aul2 language packs against the script's UI strings,
  * extract the embedded HLSL pixel shaders for the CPU shader executor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PARAM_RE = re.compile(r"^--(?P<kind>group|track|select|check|string|color|file)@?(?P<name>[^:]*):?(?P<rest>.*)$")
HIDE_RE = re.compile(r"^--hide@(?P<name>[^:]+):(?P<expr>.*)$")
SHADER_RE = re.compile(r"--\[\[pixelshader@([A-Za-z0-9_]+):(.*?)\]\]", re.S)


@dataclass
class Param:
    name: str
    kind: str
    label: str = ""
    default: object = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: List[Tuple[str, float]] = field(default_factory=list)  # (label, value)
    raw: str = ""


@dataclass
class Group:
    name: str
    open: bool = False
    params: List[Param] = field(default_factory=list)


@dataclass
class Obj2Script:
    path: Path
    text: str
    information: str
    groups: List[Group]
    hides: Dict[str, str]                    # last rule per target (host may keep only one)
    hide_rules: List[Tuple[str, str]]        # every rule in source order
    shaders: Dict[str, str]
    version: Optional[str]

    # -- convenience ---------------------------------------------------------
    @property
    def params(self) -> List[Param]:
        out: List[Param] = []
        for g in self.groups:
            out.extend(g.params)
        return out

    def param(self, name: str) -> Optional[Param]:
        for p in self.params:
            if p.name == name:
                return p
        return None

    def defaults(self) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for p in self.params:
            if p.default is not None:
                out[p.name] = p.default
        return out

    def ui_strings(self) -> List[str]:
        """Every Japanese label the script shows in the UI (for .aul2 checks)."""
        out: List[str] = []
        for g in self.groups:
            if g.name:
                out.append(g.name)
            for p in g.params:
                if p.label:
                    out.append(p.label)
                for lbl, _v in p.options:
                    if lbl:
                        out.append(lbl)
        return out


def _num(s: str) -> Optional[float]:
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_obj2(path: str | Path) -> Obj2Script:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    information = ""
    m = re.match(r"--information:(.*)", text)
    if m:
        information = m.group(1).strip()

    groups: List[Group] = []
    hides: Dict[str, str] = {}
    hide_rules: List[Tuple[str, str]] = []
    cur: Optional[Group] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("--"):
            continue
        hm = HIDE_RE.match(line)
        if hm:
            rule = (hm.group("name"), hm.group("expr").strip())
            hide_rules.append(rule)
            hides[rule[0]] = rule[1]
            continue
        if line.startswith("--group"):
            rest = line[len("--group"):]
            if rest.startswith(":"):
                body = rest[1:]
                if "," in body:
                    name, flag = body.rsplit(",", 1)
                    g = Group(name=name.strip(), open=flag.strip().lower() == "true")
                else:
                    g = Group(name=body.strip())
                groups.append(g)
                cur = g
            else:
                cur = None  # closes the current group
            continue
        m = re.match(r"^--(track|select|check|string|color|file)@([^:]+):(.*)$", line)
        if m and cur is not None:
            kind, name, rest = m.group(1), m.group(2), m.group(3)
            p = Param(name=name, kind=kind, raw=rest)
            if kind == "track":
                parts = rest.split(",")
                if len(parts) >= 4:
                    p.label = parts[0].strip()
                    p.min, p.max = _num(parts[1]), _num(parts[2])
                    p.default = _num(parts[3])
                    p.step = _num(parts[4]) if len(parts) > 4 else None
            elif kind == "select":
                # label=defaultIndex,opt=idx,opt=idx...
                head, *opts = rest.split(",")
                if "=" in head:
                    p.label, dflt = head.rsplit("=", 1)
                    p.label = p.label.strip()
                    p.default = _num(dflt)
                else:
                    p.label = head.strip()
                for o in opts:
                    if "=" in o:
                        lbl, val = o.rsplit("=", 1)
                        p.options.append((lbl.strip(), _num(val) or 0.0))
            elif kind == "check":
                parts = rest.split(",")
                p.label = parts[0].strip() if parts else ""
                if len(parts) > 1:
                    p.default = parts[1].strip().lower() in ("true", "1")
                else:
                    p.default = False
            elif kind == "string":
                parts = rest.split(",", 1)
                p.label = parts[0].strip()
                p.default = parts[1].strip() if len(parts) > 1 else ""
            elif kind == "color":
                parts = rest.split(",", 1)
                p.label = parts[0].strip()
                p.default = None
            elif kind == "file":
                parts = rest.split(",", 1)
                p.label = parts[0].strip()
                p.default = parts[1].strip() if len(parts) > 1 else ""
            cur.params.append(p)
            continue

    shaders = {m.group(1): m.group(2) for m in SHADER_RE.finditer(text)}
    vm = re.search(r"v(\d+\.\d+)", information)
    return Obj2Script(
        path=path, text=text, information=information, groups=groups,
        hides=hides, hide_rules=hide_rules, shaders=shaders,
        version=("v" + vm.group(1)) if vm else None,
    )


# ---------------------------------------------------------------------------
# .aul2 language packs
# ---------------------------------------------------------------------------
def parse_aul2(path: str | Path) -> Dict[str, Dict[str, str]]:
    """Parse an AviUtl2 language pack into {section: {japanese: translation}}."""
    path = Path(path)
    sections: Dict[str, Dict[str, str]] = {}
    cur = None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
            sections.setdefault(cur, {})
            continue
        if cur is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        sections[cur][k.strip()] = v.strip()
    return sections
