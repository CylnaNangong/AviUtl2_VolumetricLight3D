#!/usr/bin/env python3
"""
Builds AviUtl ExEdit2 drag-and-drop installation packages (*.au2pkg.zip) for Volumetric Light (3D).
  1. VolumetricLight3D-v3.10.au2pkg.zip             (Full Recommended Edition: Script + VL3DBridge Plugin + Languages)
  2. VolumetricLight3D-v3.10-ScriptOnly.au2pkg.zip  (Pure Script Edition: Script + Languages, without native .aux2)
"""
import argparse
import zipfile
from pathlib import Path

VERSION = "v3.10"


def to_crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def build_package(repo_root: Path, out_zip: Path, include_plugin: bool = True) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    pkg_dir = repo_root / "packaging"

    if include_plugin:
        ini_bytes = to_crlf((pkg_dir / "package.ini").read_bytes())
        txt_bytes = to_crlf((pkg_dir / "package.txt").read_bytes())
    else:
        ini_str = (
            "[package]\n"
            "id=VolumetricLight3D\n"
            "name=体積光（3D） / Volumetric Light (3D) [Script Only]\n"
            f"information=体積光（3D） {VERSION} (Script Only) by 南宮若馨Cylna & Arena.ai Agent\n"
            "uninstallSubFolderFile=0\n"
        )
        ini_bytes = to_crlf(ini_str.encode("utf-8"))
        txt_bytes = to_crlf((pkg_dir / "package.txt").read_bytes())

    entries: list[tuple[str, bytes]] = [
        ("package.ini", ini_bytes),
        ("package.txt", txt_bytes),
        ("Script/体積光（3D）.obj2", to_crlf((repo_root / "Script/体積光（3D）.obj2").read_bytes())),
    ]

    if include_plugin:
        entries.append(("Plugin/VL3DBridge.aux2", (repo_root / "Plugin/VL3DBridge.aux2").read_bytes()))
        entries.append(("Plugin/VL3DBridge.ini", to_crlf((repo_root / "Plugin/VL3DBridge.ini").read_bytes())))

    for aul in sorted((repo_root / "Language").glob("*.体積光（3D）.aul2")):
        entries.append((f"Language/{aul.name}", to_crlf(aul.read_bytes())))

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arcname, payload in entries:
            zi = zipfile.ZipInfo(filename=arcname, date_time=(2026, 10, 24, 12, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, payload)

    print(f"[OK] Built {out_zip} ({out_zip.stat().st_size:,} bytes, {len(entries)} entries)")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=repo_root / "dist")
    args = ap.parse_args()

    build_package(repo_root, args.out_dir / f"VolumetricLight3D-{VERSION}.au2pkg.zip", include_plugin=True)
    build_package(repo_root, args.out_dir / f"VolumetricLight3D-{VERSION}-ScriptOnly.au2pkg.zip", include_plugin=False)


if __name__ == "__main__":
    main()
