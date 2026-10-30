#!/usr/bin/env python3
"""Assemble the modular VolumetricLight3D sources into one AviUtl2 .obj2 file.

AviUtl2 loads the generated single file; the modules are a development-time
layout only. No Lua require/dofile calls, wrappers, or scope changes are added.
The assembler uses only the Python standard library and always emits UTF-8 with
CRLF line endings, matching the original distributed script.

Usage:
    python3 src/VolumetricLight3D/build.py
    python3 src/VolumetricLight3D/build.py --check
    python3 src/VolumetricLight3D/build.py --output sandbox/out/VolumetricLight3D.obj2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SOURCE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SOURCE_ROOT.parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "Script" / "VolumetricLight3D.obj2"
_VALID_SHADER_NAME = re.compile(r"[A-Za-z_]\w*\Z")


class BuildError(RuntimeError):
    """The source manifest or a source file is invalid."""


@dataclass(frozen=True)
class SourceEntry:
    kind: str
    path: str
    name: str | None = None


def _normalise_utf8(path: Path) -> str:
    """Read one source as LF-normalised UTF-8 and require a final newline."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise BuildError(f"{path}: UTF-8 BOM is not allowed; save as plain UTF-8")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{path}: source must be UTF-8: {exc}") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        raise BuildError(f"{path}: source must end with a newline")
    return text


class ScriptBuilder:
    """Deterministic assembler for one source tree."""

    def __init__(self, source_root: Path | str = SOURCE_ROOT):
        self.source_root = Path(source_root).expanduser().resolve()
        self.manifest_path = self.source_root / "manifest.json"

    def _source_path(self, relative: str) -> Path:
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise BuildError(f"manifest path must stay inside the source tree: {relative!r}")
        path = (self.source_root / rel).resolve()
        try:
            path.relative_to(self.source_root)
        except ValueError as exc:
            raise BuildError(f"source path escapes the source tree: {relative!r}") from exc
        if not path.is_file():
            raise BuildError(f"manifest source does not exist: {relative!r}")
        return path

    def entries(self) -> list[SourceEntry]:
        try:
            manifest: Any = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BuildError(f"missing manifest: {self.manifest_path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError(f"cannot read {self.manifest_path}: {exc}") from exc

        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise BuildError("manifest.json must be an object with version = 1")
        raw_entries = manifest.get("sources")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise BuildError("manifest.json sources must be a non-empty array")

        entries: list[SourceEntry] = []
        seen_paths: set[str] = set()
        seen_shaders: set[str] = set()
        declared_files: set[str] = set()
        for index, raw in enumerate(raw_entries):
            where = f"manifest sources[{index}]"
            if not isinstance(raw, dict):
                raise BuildError(f"{where} must be an object")
            kind = raw.get("kind")
            path = raw.get("path")
            name = raw.get("name")
            if kind not in ("lua", "pixelshader"):
                raise BuildError(f"{where}: unsupported kind {kind!r}")
            if not isinstance(path, str) or not path:
                raise BuildError(f"{where}: path must be a non-empty string")
            if path in seen_paths:
                raise BuildError(f"{where}: duplicate source path {path!r}")
            seen_paths.add(path)

            if kind == "lua":
                if name is not None:
                    raise BuildError(f"{where}: Lua entries must not have a shader name")
                if not path.startswith("modules/") or not path.endswith(".lua"):
                    raise BuildError(f"{where}: Lua sources must be modules/*.lua")
            else:
                if not isinstance(name, str) or not _VALID_SHADER_NAME.fullmatch(name):
                    raise BuildError(f"{where}: invalid pixel shader name {name!r}")
                if name in seen_shaders:
                    raise BuildError(f"{where}: duplicate pixel shader {name!r}")
                seen_shaders.add(name)
                if not path.startswith("shaders/") or not path.endswith(".hlsl"):
                    raise BuildError(f"{where}: shaders must be shaders/*.hlsl")
                if Path(path).stem != name:
                    raise BuildError(f"{where}: shader filename must match {name!r}")

            self._source_path(path)
            declared_files.add(path)
            entries.append(SourceEntry(kind=kind, path=path, name=name))

        actual_files = {
            p.relative_to(self.source_root).as_posix()
            for folder, pattern in (("modules", "*.lua"), ("shaders", "*.hlsl"))
            for p in (self.source_root / folder).rglob(pattern)
            if p.is_file()
        }
        missing = sorted(declared_files - actual_files)
        unlisted = sorted(actual_files - declared_files)
        if missing or unlisted:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unlisted:
                details.append("unlisted: " + ", ".join(unlisted))
            raise BuildError("manifest/source file mismatch (" + "; ".join(details) + ")")
        return entries

    def build(self) -> bytes:
        """Return the complete .obj2 payload without touching the output file."""
        parts: list[str] = []
        shader_count = 0
        for entry in self.entries():
            path = self._source_path(entry.path)
            text = _normalise_utf8(path)
            if entry.kind == "lua":
                if "--[[pixelshader@" in text or "--[[computeshader@" in text:
                    raise BuildError(
                        f"{entry.path}: inline shader found; put HLSL in shaders/ and use the manifest"
                    )
                parts.append(text)
            else:
                # The original format has exactly one blank line after every block.
                parts.append(f"--[[pixelshader@{entry.name}:\n{text}]]\n\n")
                shader_count += 1

        assembled = "".join(parts)
        if not assembled.startswith("--information:"):
            raise BuildError("the first module must contain the .obj2 --information header")
        if assembled.count("--[[pixelshader@") != shader_count:
            raise BuildError("assembled shader count is inconsistent with the manifest")
        return assembled.replace("\n", "\r\n").encode("utf-8")

    def is_up_to_date(self, output: Path | str = DEFAULT_OUTPUT) -> bool:
        output = Path(output)
        return output.is_file() and output.read_bytes() == self.build()

    def write(self, output: Path | str = DEFAULT_OUTPUT) -> tuple[bytes, bool]:
        """Build and write *output* if needed; return (payload, changed)."""
        payload = self.build()
        output = Path(output)
        if output.is_file() and output.read_bytes() == payload:
            return payload, False
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        return payload, True


def build_obj2(source_root: Path | str = SOURCE_ROOT) -> bytes:
    """Small public helper used by packaging and sandbox regression tests."""
    return ScriptBuilder(source_root).build()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT,
                        help="directory containing manifest.json (mainly for isolated tests)")
    parser.add_argument("--output", type=Path, default=None,
                        help=f"output .obj2 (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--check", action="store_true",
                        help="do not write; fail if output differs from the modular sources")
    args = parser.parse_args(argv)
    output = args.output if args.output is not None else DEFAULT_OUTPUT

    try:
        builder = ScriptBuilder(args.source_root)
        payload = builder.build()
        if args.check:
            if not output.is_file():
                print(f"[FAIL] generated script is missing: {output}", file=sys.stderr)
                return 1
            actual = output.read_bytes()
            if actual != payload:
                print(f"[FAIL] {output} is stale or was edited directly", file=sys.stderr)
                print(f"       expected sha256 {_sha256(payload)}", file=sys.stderr)
                print(f"       actual   sha256 {_sha256(actual)}", file=sys.stderr)
                print(f"       run: python3 {Path(__file__).as_posix()}", file=sys.stderr)
                return 1
            print(f"[OK] {output} matches {len(builder.entries())} ordered sources "
                  f"({len(payload):,} bytes, sha256 {_sha256(payload)})")
            return 0

        _payload, changed = builder.write(output)
        state = "Built" if changed else "Already up to date"
        print(f"[OK] {state}: {output} ({len(payload):,} bytes, sha256 {_sha256(payload)})")
        return 0
    except (BuildError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
