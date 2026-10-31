#!/usr/bin/env python3
"""Sync Snow3D v1.23 (VL3DLF v3 light-field receiver) into an AviUtl2_Snow3D checkout
or build Snow3D-v1.23.au2pkg.zip directly from integrations/snow3d/.

Usage:
    # Build dist/Snow3D-v1.23.au2pkg.zip directly in this repo:
    python3 integrations/snow3d/apply_to_snow3d.py --build-pkg

    # Apply v1.23 files onto a local AviUtl2_Snow3D clone (and optionally build package):
    python3 integrations/snow3d/apply_to_snow3d.py /path/to/AviUtl2_Snow3D --build-pkg
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

CRLF_GLOBS = [
    "Script/雪（3D）.obj2",
    "Language/简体中文.雪（3D）.aul2",
    "Language/English.雪（3D）.aul2",
    "Language/Deutsch.雪（3D）.aul2",
    "Language/community_de.雪（3D）.aul2",
    "packaging/package.ini",
    "packaging/package.txt",
]

LF_FILES = [
    "packaging/build_au2pkg.py",
    "docs/CHANGELOG_zh.md",
    "docs/CHANGELOG_ja.md",
]


def to_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def to_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def normalise_local() -> None:
    for rel in CRLF_GLOBS:
        p = HERE / rel
        p.write_bytes(to_crlf(p.read_bytes()))
    for rel in LF_FILES:
        p = HERE / rel
        p.write_bytes(to_lf(p.read_bytes()))


def sync_to_target(target_root: Path) -> None:
    target_root = target_root.expanduser().resolve()
    if not (target_root / "Script").is_dir() or not (target_root / "Language").is_dir():
        raise SystemExit(f"Target does not look like an AviUtl2_Snow3D checkout: {target_root}")

    for rel in CRLF_GLOBS:
        src = HERE / rel
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(to_crlf(src.read_bytes()))
        print(f"[SYNC] {dst}")

    for rel in LF_FILES:
        src = HERE / rel
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"[SYNC] {dst}")


def build_pkg_from(root: Path, out_dir: Path) -> Path:
    spec = importlib.util.spec_from_file_location("snow3d_pkg", root / "packaging" / "build_au2pkg.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out_zip = out_dir / f"Snow3D-{mod.VERSION}.au2pkg.zip"
    mod.build_package(root, out_zip)
    return out_zip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snow3d_repo", nargs="?", type=Path, default=None,
                    help="Optional path to a local AviUtl2_Snow3D checkout to update")
    ap.add_argument("--build-pkg", action="store_true",
                    help="Build Snow3D-v1.23.au2pkg.zip into --out-dir (default: dist/)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Output directory for --build-pkg")
    args = ap.parse_args(argv)

    normalise_local()

    if args.snow3d_repo is not None:
        sync_to_target(args.snow3d_repo)
        if args.build_pkg:
            out_d = args.out_dir or (args.snow3d_repo.expanduser().resolve() / "dist")
            build_pkg_from(args.snow3d_repo.expanduser().resolve(), out_d)
    elif args.build_pkg:
        out_d = args.out_dir or (REPO_ROOT / "dist")
        build_pkg_from(HERE, out_d)
    else:
        print("[OK] Normalized integrations/snow3d/ line endings (pass a target path or --build-pkg).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
