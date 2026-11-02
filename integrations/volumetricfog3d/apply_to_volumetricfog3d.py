#!/usr/bin/env python3
"""Sync VolumetricFog3D v2.06 into an upstream checkout or build its packages.

Examples:
    python3 integrations/volumetricfog3d/apply_to_volumetricfog3d.py --build-pkg --out-dir dist
    python3 integrations/volumetricfog3d/apply_to_volumetricfog3d.py /path/to/AviUtl2_VolumetricFog3D --build-pkg
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

CRLF_FILES = [
    "Script/VolumetricFog3D.obj2",
    "Language/简体中文.VolumetricFog3D.aul2",
    "Language/English.VolumetricFog3D.aul2",
    "Language/Deutsch.VolumetricFog3D.aul2",
    "Language/community_de.VolumetricFog3D.aul2",
    "packaging/package.ini",
    "packaging/package.txt",
    "Plugin/VL3DBridge.ini",
]
LF_FILES = [
    "packaging/build_au2pkg.py",
    "docs/CHANGELOG_zh.md",
    "docs/CHANGELOG_ja.md",
    "docs/README_en.md",
    "docs/README_ja.md",
    "THIRD_PARTY_NOTICES.md",
]


def normalize_line_endings() -> None:
    for rel in CRLF_FILES:
        path = HERE / rel
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    for rel in LF_FILES:
        path = HERE / rel
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def sync_to(target: Path) -> None:
    target = target.expanduser().resolve()
    if not (target / "Script").is_dir() or not (target / "Language").is_dir():
        raise SystemExit(f"Not an AviUtl2_VolumetricFog3D checkout: {target}")
    for rel in CRLF_FILES + LF_FILES:
        src = HERE / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        if rel in CRLF_FILES:
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        else:
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        dst.write_bytes(data)
        print(f"[SYNC] {dst}")

    bridge_src = REPO_ROOT / "Plugin" / "VL3DBridge.aux2"
    bridge_dst = target / "Plugin" / "VL3DBridge.aux2"
    bridge_dst.parent.mkdir(parents=True, exist_ok=True)
    bridge_dst.write_bytes(bridge_src.read_bytes())
    print(f"[SYNC] {bridge_dst} (from VolumetricLight3D repository)")


def build_packages(root: Path, out_dir: Path) -> list[Path]:
    builder_path = root / "packaging" / "build_au2pkg.py"
    spec = importlib.util.spec_from_file_location("volumetricfog3d_packaging", builder_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load package builder: {builder_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out_dir.mkdir(parents=True, exist_ok=True)
    built = []
    for suffix, plugin in (("", True), ("-ScriptOnly", False)):
        out_zip = out_dir / f"VolumetricFog3D-{module.VERSION}{suffix}.au2pkg.zip"
        module.build_package(root, out_zip, include_plugin=plugin)
        built.append(out_zip)
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fog_repo", nargs="?", type=Path,
                        help="Optional upstream VolumetricFog3D checkout to update")
    parser.add_argument("--build-pkg", action="store_true", help="Build full and ScriptOnly packages")
    parser.add_argument("--out-dir", type=Path, help="Package output directory (default: dist/)")
    args = parser.parse_args(argv)

    normalize_line_endings()
    root = HERE
    if args.fog_repo is not None:
        root = args.fog_repo.expanduser().resolve()
        sync_to(root)
    if args.build_pkg:
        out = args.out_dir or (root / "dist" if args.fog_repo is not None else REPO_ROOT / "dist")
        for package in build_packages(root, out):
            print(f"[OK] {package} ({package.stat().st_size:,} bytes)")
    elif args.fog_repo is None:
        print("[OK] Normalized integrations/volumetricfog3d line endings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
