#!/usr/bin/env python3
"""
Builds the AviUtl ExEdit2 drag-and-drop installation package (*.au2pkg.zip) for Snow3D.
  1. Snow3D-v1.23.au2pkg.zip  (Script + Languages; snow needs no plugin)
"""
import argparse
import zipfile
from pathlib import Path

VERSION = "v1.23"
SCRIPT_NAME = "雪（3D）.obj2"
LANG_SUFFIX = ".雪（3D）.aul2"


def to_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def build_package(repo_root: Path, out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    pkg_dir = repo_root / "packaging"

    entries: list[tuple[str, bytes]] = [
        ("package.ini", to_crlf((pkg_dir / "package.ini").read_bytes())),
        ("package.txt", to_crlf((pkg_dir / "package.txt").read_bytes())),
        (f"Script/{SCRIPT_NAME}", to_crlf((repo_root / "Script" / SCRIPT_NAME).read_bytes())),
    ]
    for aul in sorted((repo_root / "Language").glob(f"*{LANG_SUFFIX}")):
        entries.append((f"Language/{aul.name}", to_crlf(aul.read_bytes())))

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arcname, payload in entries:
            zi = zipfile.ZipInfo(filename=arcname, date_time=(2026, 10, 31, 12, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, payload)

    print(f"[OK] Built {out_zip} ({out_zip.stat().st_size:,} bytes, {len(entries)} entries)")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=repo_root / "dist")
    args = ap.parse_args()
    build_package(repo_root, args.out_dir / f"Snow3D-{VERSION}.au2pkg.zip")


if __name__ == "__main__":
    main()
