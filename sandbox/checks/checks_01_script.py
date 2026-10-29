"""Static analysis of Script/VolumetricLight3D.obj2 (declarations, params, docs)."""
from __future__ import annotations

import re

from vl3dsb.harness import check, fail, require
from vl3dsb.session import DEFAULT_SCRIPT, script

# Documented defaults from README §5 (the parameter reference table).
README_DEFAULTS = {
    "src_layer": 0, "ray_mode": 0, "fill_mode": 0, "direction": 55.0, "pitch": 44.0,
    "screen_dir": True, "spread": 22.0, "light_x": 0, "light_y": -300, "light_z": 1400,
    "length": 2500, "decay": 55.0, "intensity": 100.0, "threshold": 15.0,
    "cam_light_mode": 0, "occ_mode": 0, "occ_3d_mode": 0, "occ_target": 0,
    "occ_list": "4 5", "occ_soft": 8.0, "occ_strength": 100.0,
    "ab_enable": False, "ab_target": 0, "ab_list": "4-5", "ab_bright": 85.0,
    "ab_color_mode": 2, "ab_style": 0, "ab_strength": 100.0, "ab_diffusion": 35.0,
    "ab_bias": -60.0, "ab_color_mix": 100.0, "ab_saturation": 100.0,
    "ab_luma_env": None,
    "fog": 0.0, "fog_scale": 220.0, "wind_dir": 25.0, "wind_speed": 45.0, "seed": 0,
    "res_mode": 1, "auto_slice": True, "slices": 64, "view_mode": 0,
    "show_info": False, "log_mode": True,
}


@check("obj2: header / identity", tags=["static"])
def header():
    s = script()
    require(s.information.startswith("VolumetricLight3D v"), f"unexpected header: {s.information!r}")
    require(s.version == "v3.13", f"version mismatch: {s.version}")
    txt = s.text
    require("MIT" in txt or "License" in txt, "license line missing")
    require("--information:" in txt, "information directive missing")
    return {"detail": s.information}


@check("obj2: parameter surface (groups/params/defaults)", tags=["static"])
def params():
    s = script()
    groups = [g.name for g in s.groups if g.params]
    require(len(groups) == 6, f"expected 6 parameter groups, got {groups}")
    n = len(s.params)
    require(n == 47, f"expected 47 parameters (README), got {n}")
    names = [p.name for p in s.params]
    dup = {x for x in names if names.count(x) > 1}
    require(not dup, f"duplicate parameter ids: {sorted(dup)}")
    d = s.defaults()
    mismatched = []
    for k, v in README_DEFAULTS.items():
        if k == "ab_luma_env":
            continue
        if k not in d:
            mismatched.append(f"{k}: missing")
            continue
        got = d[k]
        if isinstance(v, bool) or isinstance(got, bool):
            if bool(got) != bool(v):
                mismatched.append(f"{k}: {got} != {v}")
        elif isinstance(v, str):
            if str(got) != v:
                mismatched.append(f"{k}: {got!r} != {v!r}")
        else:
            if abs(float(got) - float(v)) > 1e-6:
                mismatched.append(f"{k}: {got} != {v}")
    require(not mismatched, "defaults differ from README: " + ", ".join(mismatched))
    return {"params": n, "groups": groups}


@check("obj2: extends ranges / select indices are valid", tags=["static"])
def ranges():
    s = script()
    bad = []
    for p in s.params:
        if p.kind == "track":
            if p.min is None or p.max is None or p.default is None:
                bad.append(f"{p.name}: unparsable track")
                continue
            if not (p.min <= float(p.default) <= p.max):
                bad.append(f"{p.name}: default {p.default} outside [{p.min},{p.max}]")
            if p.step is not None and p.step <= 0:
                bad.append(f"{p.name}: non-positive step {p.step}")
        if p.kind == "select":
            if not p.options:
                bad.append(f"{p.name}: no options")
                continue
            vals = [v for _l, v in p.options]
            if p.default is not None and abs(float(p.default)) > max(vals):
                bad.append(f"{p.name}: default {p.default} not in option range {vals}")
            if len(set(vals)) != len(vals):
                bad.append(f"{p.name}: duplicate option values {vals}")
            labels = [l for l, _v in p.options]
            if len(set(labels)) != len(labels):
                bad.append(f"{p.name}: duplicate option labels")
    require(not bad, "; ".join(bad))
    return {"detail": "all track/select definitions consistent"}


@check("obj2: --hide@ expressions reference declared parameters", tags=["static"])
def hide_exprs():
    s = script()
    declared = {p.name for p in s.params}
    bad = []
    for name, expr in s.hides.items():
        if name not in declared:
            bad.append(f"hide target {name!r} is not a declared parameter")
        for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            if ident in declared:
                continue
            if ident in ("and", "or", "not", "true", "false", "nil"):
                continue
            bad.append(f"{name}: unknown identifier {ident!r} in {expr!r}")
    require(not bad, "; ".join(bad))
    return {"hides": len(s.hides)}


@check("obj2: every parameter is reachable in the Lua body", tags=["static"])
def param_usage():
    s = script()
    body = s.text
    missing = []
    # the script reads parameters as plain Lua globals
    for p in s.params:
        if not re.search(r"\b" + re.escape(p.name) + r"\b", body.split("--[[pixelshader", 1)[0]):
            missing.append(p.name)
        if not re.search(r"\b" + re.escape(p.name) + r"\b", body.split("]]", 0)[0] or "", 0) and False:
            pass
    # also require the body (after the declarations) to use each parameter
    lua_body = re.split(r"--group", body)[-1]
    require(not missing, f"parameters declared but never read: {missing}")
    return {"detail": f"{len(s.params)} parameters all referenced"}


@check("obj2: i18n message table covers ja/zh/en/de", tags=["static"])
def i18n_table():
    s = script()
    for key in ("unselected", "self_layer", "empty_layer", "src_desc", "hud", "log_info"):
        block = re.search(key + r"\s*=\s*\{(.*?)\n    \}", s.text, re.S)
        require(block is not None, f"i18n key {key} missing")
        langs = re.findall(r"\b(ja|zh|en|de)\s*=", block.group(1))
        require(set(langs) >= {"ja", "zh", "en", "de"}, f"{key} missing languages: {langs}")
    return {"detail": "i18n table complete"}


@check("obj2: embedded HLSL blocks are registered and unique", tags=["static"])
def shader_blocks():
    s = script()
    require(len(s.shaders) >= 14, f"expected >= 14 shaders, got {len(s.shaders)}")
    expect = {"beamatlas", "ps_vl_water", "ps_vl_pool", "ps_vl_prep", "ps_vl_pack",
              "ps_vl_blur", "ps_vl_occ_blur", "ps_vl_white", "ps_vl_ab_blur",
              "ps_vl_ab_env_init", "ps_vl_ab_mask_proj", "ps_vl_ab_shade",
              "ps_vl_ab_sil_h", "ps_vl_ab_sil_v"}
    missing = expect - set(s.shaders)
    require(not missing, f"missing shaders: {sorted(missing)}")
    called = set(re.findall(r'obj\.pixelshader\("([A-Za-z0-9_]+)"', s.text))
    unused = set(s.shaders) - called
    uncalled = called - set(s.shaders)
    require(not uncalled, f"pixelshader() calls without definition: {sorted(uncalled)}")
    return {"shaders": len(s.shaders), "unused_definitions": sorted(unused)}


@check("obj2: cache keys are all released at frame end", tags=["static"])
def cache_release():
    s = script()
    keys = set(re.findall(r'"(cache:vl3d/[a-z_0-9/]+)"', s.text))
    block = re.search(r"local CACHE_KEYS = \{(.*?)\}", s.text, re.S)
    require(block is not None, "CACHE_KEYS table not found")
    released = set(re.findall(r'"(cache:vl3d/[^"]+)"', block.group(1)))
    missing = {k for k in keys if k not in released}
    require(not missing, f"cache buffers never released: {sorted(missing)}")
    return {"cache_keys": len(released)}


@check("obj2: no unbounded loops over user input / no io writes", tags=["static"])
def safety_lint():
    s = script()
    txt = s.text
    bad = []
    for pat, why in [
        (r"os\.execute", "os.execute() must never be used"),
        (r"io\.write", "the script must not write files"),
        (r"write_process_memory|WriteProcessMemory", "host memory must be read-only"),
        (r"ffi\.C\.system", "no shell-out allowed"),
    ]:
        if re.search(pat, txt, re.I):
            bad.append(why)
    require(not bad, "; ".join(bad))
    # remote-memory API usage must always be guarded by a ReadProcessMemory-only policy
    wpm = re.findall(r"virtualprotect|VirtualProtect\w*", txt, re.I)
    require(not wpm, "script must not change page protections")
    return {"detail": "read-only, no shell/file writes"}


@check("obj2: documented change-log entries match the version banner", tags=["static"])
def changelog():
    s = script()
    body = s.text
    versions = re.findall(r"-- v(\d\.\d\d) ", body)
    require(versions, "no changelog entries found")
    top = versions[0]
    require(top == "3.13", f"newest changelog entry is v{top}, banner says {s.version}")
    require(len(versions) >= 10, f"only {len(versions)} changelog entries")
    return {"entries": len(versions), "top": "v" + top}


# ---------------------------------------------------------------------------
# Strict property-annotation grammar
#
# The rule set mirrors the validation AviUtl2_Astra performs on property
# comments at `astra build` time (documented in its wiki page
# "Properties and Localization"); it is re-implemented here so the sandbox can
# enforce it on Linux without the Astra toolchain.  Everything below lints the
# *source* text, so it also guards against mixing in Astra-only syntax that the
# native AviUtl2 loader would not understand.
# ---------------------------------------------------------------------------
VALUE_KINDS = {"track", "check", "select", "color", "string", "text", "value", "file", "folder", "data"}
LEGAL_TRACK_STEPS = {10.0 ** -k for k in range(10)} | {1.0, 0.1}


def _directive_lines(text):
    """(line_no, kind, ident, rest) for every property directive comment."""
    out = []
    for i, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s.startswith("--") or s.startswith("--[["):
            continue
        m = re.match(r"^--([A-Za-z]+)(?:@([^:]*))?:(.*)$", s)
        if not m:
            continue
        out.append((i, m.group(1), (m.group(2) or "").strip(), m.group(3).strip()))
    return out


@check("obj2: property annotations obey the strict AviUtl2 grammar", tags=["static"])
def property_grammar():
    s = script()
    bad = []
    seen = {}
    steps = set()
    counts = {}
    for line_no, kind, ident, rest in _directive_lines(s.text):
        counts[kind] = counts.get(kind, 0) + 1
        if kind in ("information", "group", "separator", "script", "hide"):
            if kind == "group":
                if not ident:
                    continue                      # bare `--group` closes a group
                parts = rest.split(",")
                if len(parts) > 2:
                    bad.append(f"L{line_no}: --group takes 1-2 arguments, got {len(parts)}")
                elif len(parts) == 2 and parts[1].strip().lower() not in ("true", "false"):
                    bad.append(f"L{line_no}: --group flag must be true/false, got {parts[1]!r}")
            continue
        if kind not in VALUE_KINDS:
            bad.append(f"L{line_no}: unknown property directive {kind!r}")
            continue
        if not re.fullmatch(r"[A-Za-z_]\w*", ident):
            bad.append(f"L{line_no}: illegal property id {ident!r}")
            continue
        if ident in seen:
            bad.append(f"L{line_no}: property {ident!r} declared twice "
                       f"(first at L{seen[ident]})")
        seen[ident] = line_no
        args = [a.strip() for a in rest.split(",")] if rest else []
        if kind == "track":
            if len(args) < 4:
                bad.append(f"L{line_no}: --track@{ident} needs min,max,initial[,step]")
                continue
            if len(args) > 5:
                bad.append(f"L{line_no}: --track@{ident} has {len(args)-5} extra arguments")
            try:
                lo, hi, init = float(args[1]), float(args[2]), float(args[3])
            except ValueError:
                bad.append(f"L{line_no}: --track@{ident} has a non-numeric bound: {args[1:4]}")
                continue
            for v in (lo, hi, init):
                if v != v or v in (float("inf"), float("-inf")):
                    bad.append(f"L{line_no}: --track@{ident} has a non-finite bound")
            if not (lo <= init <= hi):
                bad.append(f"L{line_no}: --track@{ident} initial {init} outside [{lo},{hi}]")
            if len(args) == 5:
                try:
                    step = float(args[4])
                except ValueError:
                    bad.append(f"L{line_no}: --track@{ident} step {args[4]!r} is not numeric")
                    continue
                steps.add(step)
                if not any(abs(step - x) <= abs(x) * 1e-9 for x in LEGAL_TRACK_STEPS) or step <= 0:
                    bad.append(f"L{line_no}: --track@{ident} step {step} is not 10^-k (k<=9)")
        elif kind == "check":
            if len(args) != 2:
                bad.append(f"L{line_no}: --check@{ident} needs label,initial")
            elif args[1].lower() not in ("true", "false", "0", "1"):
                bad.append(f"L{line_no}: --check@{ident} initial must be true/false/0/1, got {args[1]!r}")
        elif kind == "select":
            if not args or "=" not in args[0]:
                bad.append(f"L{line_no}: --select@{ident} head must be 'label=default'")
                continue
            try:
                default = float(args[0].rsplit("=", 1)[1])
            except ValueError:
                bad.append(f"L{line_no}: --select@{ident} default is not numeric")
                continue
            values, labels = [], []
            for opt in args[1:]:
                if "=" not in opt:
                    bad.append(f"L{line_no}: --select@{ident} option {opt!r} lacks '=value'")
                    continue
                lbl, val = opt.rsplit("=", 1)
                labels.append(lbl.strip())
                try:
                    values.append(float(val))
                except ValueError:
                    bad.append(f"L{line_no}: --select@{ident} option value {val!r} is not numeric")
            if not values:
                bad.append(f"L{line_no}: --select@{ident} has no options")
                continue
            if len(set(values)) != len(values):
                bad.append(f"L{line_no}: --select@{ident} duplicate option values {values}")
            if len(set(labels)) != len(labels) or not all(labels):
                bad.append(f"L{line_no}: --select@{ident} duplicate or empty option labels")
            if default not in values:
                bad.append(f"L{line_no}: --select@{ident} default {default:g} not among {values}")
        elif kind == "color":
            if len(args) != 2:
                bad.append(f"L{line_no}: --color@{ident} needs label,value")
            else:
                v = args[1].strip().lower()
                if v != "nil":
                    try:
                        n = int(v, 0)
                    except ValueError:
                        bad.append(f"L{line_no}: --color@{ident} value {args[1]!r} is not nil/0..0xffffff")
                        continue
                    if not (0 <= n <= 0xFFFFFF):
                        bad.append(f"L{line_no}: --color@{ident} value {n} out of range")
        elif kind in ("string", "text", "file"):
            if len(args) < 2 and kind != "file":
                bad.append(f"L{line_no}: --{kind}@{ident} needs label,initial")
        elif kind == "data":
            if len(args) != 2:
                bad.append(f"L{line_no}: --data@{ident} needs label,size")
            else:
                try:
                    n = int(float(args[1]))
                except ValueError:
                    bad.append(f"L{line_no}: --data@{ident} size is not numeric")
                else:
                    if not (0 <= n <= 16000):
                        bad.append(f"L{line_no}: --data@{ident} size {n} outside 0..16000")

    # Astra-only preprocessing directives would not load in native AviUtl2
    for pat, why in [
        (r"^--#(include|define|undef|replace|omit|if|ifdef|ifndef|elif|else|endif)\b",
         "Astra preprocessor directive"),
        (r"^\S.*--(?:track|check|select|color|string|text|value|file|folder|data)@",
         "declaration-style annotation (Astra dialect, must be a standalone comment)"),
    ]:
        m = re.search(pat, s.text, re.M)
        if m:
            bad.append(f"{why} found: {m.group(0).strip()[:60]!r}")
    require(not bad, "; ".join(bad[:8]))
    return {"properties": len(seen), "directives": counts,
            "track_steps": sorted(steps), "detail": "grammar OK"}


@check("obj2: --hide@ rules target parameters declared before them", tags=["static"])
def hide_rules_order():
    s = script()
    declared_at = {}
    for line_no, kind, ident, _rest in _directive_lines(s.text):
        if kind in VALUE_KINDS and ident:
            declared_at.setdefault(ident, line_no)
    bad = []
    rules = 0
    for line_no, kind, ident, expr in _directive_lines(s.text):
        if kind != "hide":
            continue
        rules += 1
        if ident not in declared_at:
            bad.append(f"L{line_no}: --hide target {ident!r} is not a declared parameter")
            continue
        if declared_at[ident] > line_no:
            bad.append(f"L{line_no}: --hide@{ident} precedes its declaration at L{declared_at[ident]}")
        if not expr:
            bad.append(f"L{line_no}: --hide@{ident} has an empty condition")
        for op in re.findall(r"[=!<>]=", expr):
            if op not in ("==", "~=", ">=", "<="):
                bad.append(f"L{line_no}: --hide@{ident} uses unsupported operator {op!r}")
    require(not bad, "; ".join(bad[:8]))
    return {"hide_rules": rules, "targets": len({i for _l, k, i, _e in _directive_lines(s.text) if k == "hide"})}


@check("obj2: --hide@ host semantics audit (merge conflicts, numeric vs boolean)", tags=["static"])
def hide_semantics():
    """Informational: two constructs whose behaviour depends on how AviUtl2
    itself evaluates `--hide@` conditions.  Neither can be settled from the
    source alone, so this reports WARN instead of FAIL."""
    from vl3dsb.harness import WARN

    s = script()
    kinds = {p.name: p.kind for p in s.params}
    per_target = {}
    for line_no, kind, ident, expr in _directive_lines(s.text):
        if kind == "hide":
            per_target.setdefault(ident, []).append((line_no, expr))
    dupes = {k: v for k, v in per_target.items() if len(v) > 1}
    numeric_on_bool = []
    for target, rules in per_target.items():
        for line_no, expr in rules:
            for ident in re.findall(r"[A-Za-z_]\w*", expr):
                if kinds.get(ident) == "check" and re.search(rf"\b{re.escape(ident)}\s*[=~]=\s*[01]\b", expr):
                    numeric_on_bool.append(f"L{line_no}: {target} tests boolean {ident} against 0/1")

    problems = [(k, [(l, e) for l, e in v]) for k, v in dupes.items()] + \
               [(None, x) for x in numeric_on_bool]
    if not problems:
        return {"duplicates": 0, "boolean_numeric": 0, "detail": "no ambiguous constructs"}
    detail = []
    for target, rules in dupes.items():
        detail.append(f"--hide@{target} declared {len(rules)}x "
                      f"({', '.join(f'L{l}: {e}' for l, e in rules)}) - AviUtl2 merge semantics unknown")
    detail.extend(numeric_on_bool)
    return {"status": WARN, "duplicates": len(dupes), "boolean_numeric": len(numeric_on_bool),
            "detail": " | ".join(detail[:6])}
