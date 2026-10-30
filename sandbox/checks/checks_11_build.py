"""Regression checks for the modular .obj2 source assembler.

These tests deliberately build bytes rather than importing the Lua fragments:
the distributed AviUtl2 script must remain one self-contained file with exactly
the same top-level local scope as before the source split.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

from vl3dsb.config import REPO_ROOT
from vl3dsb.harness import OUT_DIR, check, require, skip


SOURCE_ROOT = REPO_ROOT / "src" / "VolumetricLight3D"
DISTRIBUTED = REPO_ROOT / "Script" / "VolumetricLight3D.obj2"


def _builder_module():
    path = SOURCE_ROOT / "build.py"
    if not path.is_file():
        skip(f"{REPO_ROOT} predates the modular source layout")
    # Dataclasses and module introspection expect the dynamic module to be in
    # sys.modules while exec_module runs. Include the target path in the name
    # so two repositories cannot collide in a specialised sandbox run.
    key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    module_name = f"vl3d_obj2_builder_{key}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    require(spec is not None and spec.loader is not None, f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@check("build: distributed obj2 exactly matches all modular sources", tags=["static", "build"])
def distributed_is_current():
    mod = _builder_module()
    builder = mod.ScriptBuilder(SOURCE_ROOT)
    entries = builder.entries()
    payload = builder.build()
    require(DISTRIBUTED.is_file(), f"missing generated script: {DISTRIBUTED}")
    actual = DISTRIBUTED.read_bytes()
    require(actual == payload,
            "Script/VolumetricLight3D.obj2 is stale or was edited directly; "
            "run python3 src/VolumetricLight3D/build.py")
    lua_count = sum(e.kind == "lua" for e in entries)
    shader_count = sum(e.kind == "pixelshader" for e in entries)
    require(lua_count >= 10, f"source is no longer meaningfully modular: {lua_count} Lua files")
    require(shader_count == 14, f"expected 14 independent HLSL sources, got {shader_count}")
    return {
        "lua_modules": lua_count,
        "hlsl_shaders": shader_count,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "detail": "generated file is byte-for-byte current",
    }


@check("build: isolated assembly is deterministic and write is idempotent", tags=["static", "build"])
def isolated_build():
    mod = _builder_module()
    builder = mod.ScriptBuilder(SOURCE_ROOT)
    out = OUT_DIR / "build" / "VolumetricLight3D.obj2"
    payload, _changed = builder.write(out)
    _same_payload, changed_again = builder.write(out)
    require(not changed_again, "second identical build unexpectedly rewrote the output")
    require(out.read_bytes() == payload, "isolated output differs from in-memory assembly")
    require(payload == builder.build(), "two in-memory builds are not deterministic")
    require(not payload.startswith(b"\xef\xbb\xbf"), "generated .obj2 unexpectedly has a BOM")
    require(payload.count(b"\n") == payload.count(b"\r\n"), "generated .obj2 contains non-CRLF lines")
    require(payload.endswith(b"\r\n"), "generated .obj2 lacks a final CRLF")
    return {"bytes": len(payload), "lines": payload.count(b"\r\n"),
            "artifacts": [str(out)], "detail": "deterministic UTF-8/CRLF output"}


@check("build: checker catches source drift and unlisted modules", tags=["static", "build"])
def checker_bites():
    mod = _builder_module()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="obj2-build-", dir=OUT_DIR) as temp:
        temp_root = Path(temp)
        copied_source = temp_root / "VolumetricLight3D"
        shutil.copytree(SOURCE_ROOT, copied_source)
        original_output = temp_root / "original.obj2"
        original_output.write_bytes(DISTRIBUTED.read_bytes())
        builder = mod.ScriptBuilder(copied_source)
        require(builder.is_up_to_date(original_output), "clean copied fixture is not current")

        drift = copied_source / "modules" / "05_utils.lua"
        drift.write_bytes(drift.read_bytes() + b"-- seeded source drift\n")
        require(not builder.is_up_to_date(original_output),
                "checker did not notice modified module content")

        unlisted = copied_source / "modules" / "99_unlisted.lua"
        unlisted.write_text("-- must be registered in manifest.json\n", encoding="utf-8")
        try:
            builder.entries()
        except mod.BuildError as exc:
            require("unlisted" in str(exc), f"wrong unlisted-module diagnostic: {exc}")
        else:
            require(False, "builder accepted an unlisted Lua module")
        unlisted.unlink()

        manifest_path = copied_source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"].append(dict(manifest["sources"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        try:
            builder.entries()
        except mod.BuildError as exc:
            require("duplicate" in str(exc), f"wrong duplicate-entry diagnostic: {exc}")
        else:
            require(False, "builder accepted a duplicate manifest entry")

    return {"seeds": "content drift, unlisted file, duplicate manifest entry",
            "detail": "all three intentionally seeded defects were rejected"}
