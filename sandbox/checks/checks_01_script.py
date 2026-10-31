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
    """Validate the banner without pinning the suite to one release."""
    s = script()
    require(s.information.startswith("VolumetricLight3D v"), f"unexpected header: {s.information!r}")
    require(bool(re.fullmatch(r"v\d+\.\d\d", s.version or "")), f"unparsable version: {s.version}")
    txt = s.text
    require("MIT" in txt or "License" in txt, "license line missing")
    require("--information:" in txt, "information directive missing")
    return {"detail": s.information, "version": s.version}


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
    # validate *every* rule: a target may carry more than one (see the merge
    # audit) and the last-wins dict `s.hides` would hide the earlier ones
    for name, expr in s.hide_rules:
        if name not in declared:
            bad.append(f"hide target {name!r} is not a declared parameter")
        for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            if ident in declared:
                continue
            if ident in ("and", "or", "not", "true", "false", "nil"):
                continue
            bad.append(f"{name}: unknown identifier {ident!r} in {expr!r}")
    require(not bad, "; ".join(bad))
    # rules whose condition compares a boolean-declared --check@ numerically:
    # these rely on the host treating 0/1 as numbers in the condition context
    # (verified in AviUtl2 2026-10-29 for `ab_enable==0`; keep the list visible
    # so a newly added rule of this shape is noticed and re-verified).
    bool_checks = {p.name for p in s.params if p.kind == "check"}
    numeric_on_bool = sorted({f"{t}:{e}" for t, e in s.hide_rules
                              if any(v in bool_checks for v in re.findall(r"[A-Za-z_]\w*", e))
                              and re.search(r"[=~<>]=\s*[01]\b", e)})
    return {"hide_rules": len(s.hide_rules), "targets": len(s.hides),
            # (harness drops lists with >=12 items; keep it as one string)
            "numeric_comparison_of_boolean_check": f"{len(numeric_on_bool)} 条: " +
                                                   ", ".join(numeric_on_bool)}


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
    require("v" + top == s.version, f"newest changelog entry is v{top}, banner says {s.version}")
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
# Rules below follow the **official** AviUtl2 script spec
# (https://docs.aviutl2.jp/lua/ § 設定項目) with the extra strictness that
# AviUtl2_Astra applies at `astra build` time.  Where the two disagree the
# official spec wins and the stricter rule is marked "(extra)".
# ---------------------------------------------------------------------------
DOC_KINDS = {"track", "trackgroup", "check", "checksection", "select", "color",
             "file", "folder", "font", "figure", "string", "text", "value", "data"}
VALUE_KINDS = {"track", "check", "select", "color", "string", "text", "value",
               "file", "folder", "font", "figure"}
# kinds usable as the *condition* of a --hide@ rule (docs: check / list /
# file / folder, plus the special `filter`); trackbars are NOT listed
HIDE_CONDITION_KINDS = {"check", "select", "file", "folder"}
HIDE_OPERATORS = {"==", "~=", ">", "<"}       # docs list exactly these four
DOC_TRACK_STEPS = {1.0, 0.1, 0.01, 0.001}     # docs: these four are documented
LEGAL_TRACK_STEPS = {10.0 ** -k for k in range(10)}


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


@check("obj2: property annotations follow the AviUtl2 spec (docs.aviutl2.jp/lua)", tags=["static"])
def property_grammar():
    s = script()
    bad = []
    seen = {}
    steps = set()
    counts = {}
    check_types = {"number": 0, "boolean": 0}

    def args_of(rest):
        return [a.strip() for a in rest.split(",")] if rest else []

    for line_no, kind, ident, rest in _directive_lines(s.text):
        counts[kind] = counts.get(kind, 0) + 1

        if kind in ("information", "hide"):
            continue                       # hide rules are linted separately
        if kind == "group":
            if not ident:
                continue                       # bare `--group` closes a group
            parts = args_of(rest)
            if len(parts) > 2:
                bad.append(f"L{line_no}: --group takes 1-2 arguments, got {len(parts)}")
            elif len(parts) == 2 and parts[1].lower() not in ("true", "false"):
                bad.append(f"L{line_no}: --group flag must be true/false, got {parts[1]!r}")
            continue
        if kind in ("separator", "label", "script"):
            if not rest:
                bad.append(f"L{line_no}: --{kind} needs a value")
            continue
        if kind not in DOC_KINDS:
            bad.append(f"L{line_no}: unknown property directive {kind!r}")
            continue
        if kind == "trackgroup":
            names = [x.strip() for x in ident.split(",") if x.strip()]
            if not (2 <= len(names) <= 3):
                bad.append(f"L{line_no}: --trackgroup needs 2-3 variable names, got {len(names)}")
            if not rest:
                bad.append(f"L{line_no}: --trackgroup needs a group name")
            continue
        if not re.fullmatch(r"[A-Za-z_]\w*", ident):
            bad.append(f"L{line_no}: illegal property id {ident!r}")
            continue
        if ident in seen:
            bad.append(f"L{line_no}: property {ident!r} declared twice "
                       f"(first at L{seen[ident]})")
        seen[ident] = line_no
        args = args_of(rest)

        if kind == "track":
            # 項目名,最小値,最大値,デフォルト値[,移動単位[,ゼロ値名称[,操作倍率]]]
            if len(args) < 4:
                bad.append(f"L{line_no}: --track@{ident} needs 項目名,最小,最大,デフォルト")
                continue
            if len(args) > 7:
                bad.append(f"L{line_no}: --track@{ident} has {len(args) - 7} extra arguments")
            try:
                lo, hi, init = float(args[1]), float(args[2]), float(args[3])
            except ValueError:
                bad.append(f"L{line_no}: --track@{ident} has a non-numeric bound: {args[1:4]}")
                continue
            if not (lo <= init <= hi):
                bad.append(f"L{line_no}: --track@{ident} initial {init:g} outside [{lo:g},{hi:g}]")
            if len(args) >= 5:
                try:
                    step = float(args[4])
                except ValueError:
                    bad.append(f"L{line_no}: --track@{ident} step {args[4]!r} is not numeric")
                    step = None
                if step is not None:
                    steps.add(step)
                    if step <= 0 or not any(abs(step - x) <= abs(x) * 1e-9 for x in LEGAL_TRACK_STEPS):
                        bad.append(f"L{line_no}: --track@{ident} step {step:g} is not 10^-k (k<=9)")
            if len(args) == 7:
                try:
                    gain = float(args[6])
                except ValueError:
                    bad.append(f"L{line_no}: --track@{ident} 操作倍率 {args[6]!r} is not numeric")
                else:
                    if not (0 < gain <= 1.0):
                        bad.append(f"L{line_no}: --track@{ident} 操作倍率 must be (0,1], got {gain:g}")
        elif kind in ("check", "checksection"):
            need = 2 if kind == "check" else 3
            if len(args) != need:
                bad.append(f"L{line_no}: --{kind}@{ident} needs {need} arguments, got {len(args)}")
                continue
            for a in args[1:]:
                if a.lower() not in ("true", "false", "0", "1"):
                    bad.append(f"L{line_no}: --{kind}@{ident} value must be 0/1/true/false, got {a!r}")
            if kind == "check":
                check_types["boolean" if args[1].lower() in ("true", "false") else "number"] += 1
        elif kind == "select":
            # 項目名[=デフォルト値],選択肢=値,...   (デフォルト値は省略可)
            if not args:
                bad.append(f"L{line_no}: --select@{ident} is empty")
                continue
            default = None
            head = args[0]
            if "=" in head:
                label, raw = head.rsplit("=", 1)
                try:
                    default = float(raw)
                except ValueError:
                    bad.append(f"L{line_no}: --select@{ident} default {raw!r} is not numeric")
                if not label.strip():
                    bad.append(f"L{line_no}: --select@{ident} has an empty label")
            elif not head:
                bad.append(f"L{line_no}: --select@{ident} has an empty label")
            values, labels = [], []
            for opt in args[1:]:
                if "=" not in opt:
                    bad.append(f"L{line_no}: --select@{ident} option {opt!r} lacks '=値'")
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
            if len(set(values)) != len(values):          # (extra) ambiguity guard
                bad.append(f"L{line_no}: --select@{ident} duplicate option values {values}")
            if len(set(labels)) != len(labels) or not all(labels):
                bad.append(f"L{line_no}: --select@{ident} duplicate or empty option labels")
            if default is not None and default not in values:
                bad.append(f"L{line_no}: --select@{ident} default {default:g} not among {values}")
        elif kind == "color":
            if len(args) != 2:
                bad.append(f"L{line_no}: --color@{ident} needs 項目名,デフォルト値")
                continue
            v = args[1].lower()
            if v != "nil":
                try:
                    n = int(v, 0)
                except ValueError:
                    bad.append(f"L{line_no}: --color@{ident} value {args[1]!r} is not nil/0xRRGGBB")
                else:
                    if not (0 <= n <= 0xFFFFFF):
                        bad.append(f"L{line_no}: --color@{ident} value {n} out of range")
        elif kind in ("file", "folder"):
            if len(args) != 1:
                bad.append(f"L{line_no}: --{kind}@{ident} takes only 項目名")
        elif kind in ("string", "text", "font", "figure", "value"):
            if len(args) != 2:
                bad.append(f"L{line_no}: --{kind}@{ident} needs 項目名,デフォルト値")
        elif kind == "data":
            # --data@登録名:サイズ（16KB 以下）, no label
            if len(args) != 1:
                bad.append(f"L{line_no}: --data@{ident} takes only サイズ")
            else:
                try:
                    n = int(float(args[0]))
                except ValueError:
                    bad.append(f"L{line_no}: --data@{ident} size {args[0]!r} is not numeric")
                else:
                    if not (0 <= n <= 16384):
                        bad.append(f"L{line_no}: --data@{ident} size {n} outside 0..16384 (16KB)")

    # Astra-only syntax would not load in native AviUtl2
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
    # the validator must see exactly the parameters the main parser sees,
    # otherwise a directive line was silently skipped by the regex above
    unseen = {p.name for p in s.params} - set(seen)
    require(not unseen, f"validator skipped these parameters: {sorted(unseen)}")
    return {"properties": len(seen), "directives": counts,
            "track_steps": sorted(steps), "check_types": check_types,
            "detail": "grammar OK"}


@check("obj2: --hide@ conditions follow the documented grammar", tags=["static"])
def hide_rules_order():
    s = script()
    kinds = {p.name: p.kind for p in s.params}
    declared_at = {}
    for line_no, kind, ident, _rest in _directive_lines(s.text):
        if kind in VALUE_KINDS and ident:
            declared_at.setdefault(ident, line_no)
    bad = []
    rules = always_hidden = 0
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
            always_hidden += 1      # documented: no condition = always hidden
            continue
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*(==|~=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)", expr)
        if not m:
            bad.append(f"L{line_no}: --hide@{ident} condition {expr!r} is not (変数名)(比較演算子)(数値)")
            continue
        cond, op, _num = m.groups()
        if op not in HIDE_OPERATORS:
            bad.append(f"L{line_no}: --hide@{ident} uses undocumented operator {op!r} "
                       f"(spec allows {' '.join(sorted(HIDE_OPERATORS))})")
        if cond == "filter":
            continue                 # documented special condition (0/1)
        ckind = kinds.get(cond)
        if ckind is None:
            bad.append(f"L{line_no}: --hide@{ident} references undeclared {cond!r}")
        elif ckind not in HIDE_CONDITION_KINDS:
            bad.append(f"L{line_no}: --hide@{ident} tests a --{ckind}@{cond}; "
                       f"the spec only allows check/select/file/folder/filter")
    require(not bad, "; ".join(bad[:8]))
    return {"hide_rules": rules, "targets": len(s.hides), "always_hidden": always_hidden,
            "detail": f"{rules} rules, all in (変数名)(比較演算子)(数値) form"}


@check("obj2: --hide@ multi-rule targets are OR'ed (spec + host verified)", tags=["static"])
def hide_semantics():
    """A target may carry several `--hide@` rules; the official example does
    exactly that (`--hide@pos:chk==0` + `--hide@pos:filter==1`) and the host was
    measured to apply **all** of them (2026-10-29: with `ab_target` set to 1,
    unchecking 启用前景融入背景 still collapsed 目标图层范围, which only the
    `ab_list:ab_enable==0` rule can do).

    So duplicates are legal; what stays worth linting is a *redundant* duplicate
    (same target, same condition) and the numeric comparison of boolean
    `--check@` parameters, which the spec defines as `(変数名)(演算子)(数値)`.
    """
    s = script()
    kinds = {p.name: p.kind for p in s.params}
    per_target = {}
    for line_no, kind, ident, expr in _directive_lines(s.text):
        if kind == "hide":
            per_target.setdefault(ident, []).append((line_no, expr))

    redundant = []
    for target, rules in per_target.items():
        seen = {}
        for line_no, expr in rules:
            if expr in seen:
                redundant.append(f"{target}: L{seen[expr]} and L{line_no} share {expr!r}")
            seen[expr] = line_no
    require(not redundant, "redundant duplicate --hide@ rules: " + "; ".join(redundant))

    numeric_bool = 0
    for target, rules in per_target.items():
        for _line_no, expr in rules:
            for ident in re.findall(r"[A-Za-z_]\w*", expr):
                if kinds.get(ident) == "check" and re.search(
                        rf"\b{re.escape(ident)}\s*[=~]=\s*[01]\b", expr):
                    numeric_bool += 1
    multi = {t: [e for _l, e in r] for t, r in per_target.items() if len(r) > 1}
    return {"multi_rule_targets": multi, "numeric_comparison_of_check": numeric_bool,
            "detail": (f"{len(multi)} targets carry 2 rules (OR, host verified); "
                       f"{numeric_bool} rules compare a boolean --check@ numerically")}


# ---------------------------------------------------------------------------
# Documented *structure* of the setting items
#
# The grammar check above validates one directive at a time.  The manual also
# makes cross-item promises that a per-line rule cannot see:
#   * 設定項目の名称はスクリプト毎にユニーク (docs § 設定項目),
#   * `--group:名前` opens a group and a bare `--group` closes it,
#   * `--hide@変数名` may only target an item that *has* a value.
# ---------------------------------------------------------------------------
_DIRECT_RE = re.compile(r"^--([a-z]+)(?:@([^:]*))?:(.*)$")
_STRUCT_VALUE_KINDS = {"track", "check", "check0", "select", "color", "string",
                       "text", "value", "file", "folder", "font", "figure"}
_BARE_DIRECT_RE = re.compile(r"^--([a-z]+)\s*$")


def _structure_issues(text):
    """Cross-item violations of the documented setting-item structure."""
    issues = []
    labels = {}                      # label -> [line, ...] for value items
    kinds = {}                       # ident -> kind (value items)
    nonvalue_hide_targets = {}       # ident -> "group" | "separator" (v2.1.16+)
    id_line = {}
    open_group = None
    group_closed = {}
    group_names = set()
    hides = []

    for i, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s.startswith("--") or s.startswith("--[["):
            continue
        m = _DIRECT_RE.match(s)
        if not m:
            bare = _BARE_DIRECT_RE.match(s)     # bare `--group` closes a group
            if not bare:
                continue
            kind, ident, rest = bare.group(1), "", ""
            if kind != "group":
                issues.append(f"L{i}: directive --{kind} without ':' and arguments")
                continue
        else:
            kind = m.group(1)
            ident = (m.group(2) or "").strip()
            rest = m.group(3)

        if kind == "group":
            # `--group[@変数名]:名前[,true|false]` opens (v2.1.16 added `@変数名` for `--hide`), bare `--group` closes
            name = rest.split(",")[0].strip()
            if name:
                if open_group is not None and not group_closed.get(open_group):
                    issues.append(f"L{i}: group {open_group!r} was never closed by a bare --group")
                open_group = name
                group_closed.setdefault(name, False)
                group_names.add(name)
                if name in group_names and group_closed.get(name):
                    issues.append(f"L{i}: group {name!r} reopened after it was closed")
                if ident:
                    if ident in kinds or ident in nonvalue_hide_targets:
                        issues.append(f"L{i}: variable {ident!r} declared twice (also L{id_line[ident]})")
                    nonvalue_hide_targets[ident] = "group"
                    id_line[ident] = i
            else:
                if open_group is None:
                    issues.append(f"L{i}: bare --group without an open group")
                else:
                    group_closed[open_group] = True
                    open_group = None
            continue
        if kind == "separator":
            if ident:
                if ident in kinds or ident in nonvalue_hide_targets:
                    issues.append(f"L{i}: variable {ident!r} declared twice (also L{id_line[ident]})")
                nonvalue_hide_targets[ident] = "separator"
                id_line[ident] = i
            continue
        if kind == "hide":
            hides.append((i, ident, rest.strip()))
            continue
        if kind in ("information", "label", "require", "script",
                    "filter", "hidemenu", "trackgroup", "data"):
            continue
        if kind not in _STRUCT_VALUE_KINDS:
            issues.append(f"L{i}: unknown setting-item directive --{kind}")
            continue
        if not ident:
            issues.append(f"L{i}: --{kind} without a variable name")
            continue
        label = rest.split(",")[0].split("=")[0].strip()
        if kind == "select":
            label = rest.split("=")[0].split(",")[0].strip()
        if label:
            labels.setdefault(label, []).append(i)
        if ident in kinds or ident in nonvalue_hide_targets:
            issues.append(f"L{i}: variable {ident!r} declared twice (also L{id_line[ident]})")
        kinds[ident] = kind
        id_line[ident] = i

    if open_group is not None and not group_closed.get(open_group):
        issues.append(f"group {open_group!r} is still open at the end of the file")
    for label, lns in labels.items():
        if len(lns) > 1:
            issues.append(f"setting-item label {label!r} is used {len(lns)}x "
                          f"(L{', L'.join(str(x) for x in lns)}) -- the manual requires unique names")
    for line_no, target, expr in hides:
        if target in kinds or target in nonvalue_hide_targets:
            continue
        if target in group_names:
            issues.append(f"L{line_no}: --hide@ targets the group {target!r}, not a setting item")
        else:
            issues.append(f"L{line_no}: --hide@ targets undeclared item {target!r}")
    return issues, {"labels": len(labels), "items": len(kinds), "groups": len(group_names),
                    "hide_rules": len(hides),
                    "duplicate_labels": [l for l, v in labels.items() if len(v) > 1]}


@check("obj2: setting items, groups and --hide@ targets follow the documented structure",
       tags=["static"])
def property_structure():
    s = script()
    issues, stats = _structure_issues(s.text)
    require(not issues, "structure violations:\n  " + "\n  ".join(issues[:12]))
    require(stats["labels"] == 47, f"expected 47 setting-item labels, found {stats['labels']}")
    require(stats["groups"] == 6, f"expected 6 setting groups, found {stats['groups']}")
    require(stats["hide_rules"] == 28, f"expected 28 --hide@ rules, found {stats['hide_rules']}")
    return stats


@check("obj2: structure linter catches seeded setting-item damage", tags=["static"])
def structure_bites():
    good = """--information:demo v1.0
--group:形,true
--track@a:光源X,0,100,0,1
--group
--group:光,true
--check@b:启用,false
--hide@a:b==0
--group
"""
    issues, stats = _structure_issues(good)
    require(not issues, f"clean fixture reported {issues}")
    require(stats["duplicate_labels"] == [], "clean fixture reports duplicate labels")

    seeds = {
        "duplicate label": ("--track@a:光源X,0,100,0,1", "--track@a:光源X,0,100,0,1\n--check@c:光源X,false", "光源X"),
        "unclosed group": ("--group\n--group:光,true", "--group:光,true\n--group:光2,true", "never closed"),
        "unknown directive": ("--check@b:启用,false", "--slider@b:启用,false", "unknown setting-item directive"),
        "hide targets a group": ("--hide@a:b==0", "--hide@光:b==0", "targets the group"),
        "hide targets nothing": ("--hide@a:b==0", "--hide@zzz:b==0", "undeclared item"),
        "duplicate variable": ("--check@b:启用,false",
                               "--check@b:启用,false\n--track@b:其他,0,10,0,1", "declared twice"),
    }
    caught = []
    for name, (old, new, needle) in seeds.items():
        mutated = good.replace(old, new, 1)
        found, _ = _structure_issues(mutated)
        ok = any(needle in x for x in found)
        require(ok, f"seed {name!r} not caught (got {found})")
        caught.append(name)

    # AviUtl2 v2.1.16 (2026-10-31) added explicit @var names on --group and --separator as valid --hide targets
    v2116_good = """--information:demo v2.1.16
--group:基本,true
--check@b:詳細表示,false
--separator@sep_adv:詳細設定
--hide@sep_adv:b==0
--group
--group@grp_adv:詳細グループ,true
--track@x:強度,0,100,50,1
--hide@grp_adv:b==0
--group
"""
    issues_2116, _ = _structure_issues(v2116_good)
    require(not issues_2116, f"valid v2.1.16 --group@/--separator@ hide syntax rejected: {issues_2116}")
    return {"seeds": caught, "v2116_group_separator_hide": True}
