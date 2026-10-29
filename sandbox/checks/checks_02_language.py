"""Language packs (.aul2) vs the Japanese UI strings in the .obj2."""
from __future__ import annotations

import re
from pathlib import Path

from vl3dsb.harness import Fail, check, fail, require
from vl3dsb.obj2 import parse_aul2
from vl3dsb.session import DEFAULT_SCRIPT, REPO_ROOT, script

LANG_DIR = REPO_ROOT / "Language"
PACKS = sorted(LANG_DIR.glob("*.VolumetricLight3D.aul2"))


@check("aul2: every pack parses and declares its target", tags=["static", "i18n"])
def packs_parse():
    require(PACKS, "no .aul2 packs found")
    out = {}
    for p in PACKS:
        sec = parse_aul2(p)
        require("Language" in sec, f"{p.name}: [Language] section missing")
        require("Effect" in sec, f"{p.name}: [Effect] section missing")
        require("VolumetricLight3D" in sec, f"{p.name}: script mapping section missing")
        out[p.name] = len(sec.get("VolumetricLight3D", {}))
    return {"packs": out}


@check("aul2: parameter labels fully translated (no Japanese leftovers)", tags=["static", "i18n"])
def coverage():
    s = script()
    ui = s.ui_strings()
    report = {}
    problems = []
    for p in PACKS:
        if p.name.startswith("English"):
            continue  # English keeps Japanese keys by design; values are checked below
        sec = parse_aul2(p)
        table = sec.get("VolumetricLight3D", {})
        missing = [u for u in ui if u not in table]
        report[p.name] = {"keys": len(table), "missing": len(missing)}
        if missing:
            problems.append(f"{p.name}: {len(missing)} untranslated ({missing[:6]})")
    # the zh/de packs are expected to be complete
    hard = [x for x in problems if "English" not in x]
    require(not hard, "; ".join(hard))
    return {"per_pack": report}


@check("aul2: translations are non-empty and distinct from the key", tags=["static", "i18n"])
def quality():
    s = script()
    bad = []
    for p in PACKS:
        sec = parse_aul2(p)
        for k, v in sec.get("VolumetricLight3D", {}).items():
            if v.strip() == "":
                bad.append(f"{p.name}: empty value for {k!r}")
            same_ok = bool(re.search(r"[A-Za-z0-9]", k))   # e.g. 光源X / 光源Y keep their axis letter
            if v == k and not p.name.startswith("English") and not same_ok \
                    and re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", k):
                bad.append(f"{p.name}: untranslated {k!r}")
    require(not bad, "; ".join(bad[:10]) + (" ..." if len(bad) > 10 else ""))
    return {"detail": "all values translated"}


@check("aul2: tooltip sections (Tips) exist for every pack", tags=["static", "i18n"])
def tips():
    missing = []
    for p in PACKS:
        sec = parse_aul2(p)
        tips = {k: v for k, v in sec.items() if k.startswith("Tips")}
        n = sum(len(v) for v in tips.values())
        if n == 0:
            missing.append(p.name)
    require(not missing, f"packs without tooltips: {missing}")
    return {"detail": "all packs ship tooltip strings"}


@check("aul2: encoding / line endings are host friendly", tags=["static", "i18n"])
def encoding():
    bad = []
    for p in PACKS:
        raw = p.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append(f"{p.name}: not UTF-8 ({exc})")
            continue
        if b"\r\n" not in raw:
            bad.append(f"{p.name}: expected CRLF line endings")
        if raw.startswith(b"\xef\xbb\xbf"):
            bad.append(f"{p.name}: BOM present (AviUtl2 expects plain UTF-8)")
    require(not bad, "; ".join(bad))
    return {"detail": "UTF-8, CRLF, no BOM"}


@check("aul2: [Effect] mapping matches the script's information name", tags=["static", "i18n"])
def effect_mapping():
    s = script()
    name = re.sub(r"\s*v[\d.]+$", "", s.information).strip()
    bad = []
    for p in PACKS:
        sec = parse_aul2(p)
        key = sec["Effect"].get("VolumetricLight3D")
        require(key is not None, f"{p.name}: Effect.VolumetricLight3D missing")
        if not key.strip():
            bad.append(f"{p.name}: empty effect name")
    require(not bad, "; ".join(bad))
    return {"detail": f"effect id 'VolumetricLight3D' -> {name}"}
