#!/usr/bin/env python3
"""雪（3D）へ「体積光（3D）に照らされる」受光連動を追加するパッチ適用ツール。

VolumetricLight3D 側は毎フレーム、公式の共有テーブル ``global`` へ
VL3D Light Field v1（1 光束 189 バイト）を書き出します。このツールは
CylnaNangong/AviUtl2_Snow3D の ``Script/雪（3D）.obj2`` に、その光場を
読んで粒子を発光させる受光パスを差し込みます。

使い方::

    # 表示だけ（書き込まない）
    python3 apply_lightfield.py --snow3d /path/to/AviUtl2_Snow3D --dry-run

    # 実際に書き換える（既存ファイルは .bak に退避）
    python3 apply_lightfield.py --snow3d /path/to/AviUtl2_Snow3D

    # 任意の .obj2 を直接指定
    python3 apply_lightfield.py --script "/path/Script/雪（3D）.obj2"

冪等です（2 回実行しても二重挿入しません）。Lua 側のブロックは
``lua/`` 以下が唯一の編集元で、このスクリプトはそれらをアンカー位置へ
挿入するだけです。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUA_DIR = HERE / "lua"

MARK_BEGIN = "-- >>> VL3D Light Field v1 (VolumetricLight3D integration) >>>"
MARK_END = "-- <<< VL3D Light Field v1 <<<"

DEFAULT_SCRIPT_NAMES = ("雪（3D）.obj2", "雪(3D).obj2", "Snow3D.obj2", "Snow (3D).obj2")


def read(name: str) -> str:
    return (LUA_DIR / name).read_text(encoding="utf-8").strip("\n")


def load_blocks():
    return {
        "params": read("params.lua"),
        "shader": read("tint_shader.hlsl"),
        "receiver": read("receiver.lua"),
        "probe": read("particle_probe.lua"),
        "lit_pass": read("lit_pass.lua"),
    }


def wrap(tag: str, body: str) -> str:
    return f"{MARK_BEGIN} [{tag}]\n{body}\n{MARK_END}"


# ---------------------------------------------------------------------------
# anchors（雪（3D）v1.22 系。改行は LF へ正規化してから探す）
# ---------------------------------------------------------------------------
ANCHOR_INFO = re.compile(r"^--information:雪（3D）v([0-9.]+)\s*$", re.M)
ANCHOR_BODY_COMMENT = "--------------------------------------------------------------------\n-- 雪（3D）\n"
ANCHOR_PIXELSHADER_END = "\nlocal obj, math = obj, math"
ANCHOR_P_END = "if P.near_dist < 0 then P.near_dist = 0 end\n"
ANCHOR_PARTICLE_BRIGHT = (
    "            local rot = spin_v * t * (0.4 + 1.2 * r_ro) + r_ro * 2 * math.pi\n"
    "            local bright = 0.85 + 0.3 * r_sz\n"
)
ANCHOR_PARTS_END = (
    "                a_fig                     -- 図形テクスチャ有効α（bright反映済み）\n"
    "            }\n"
)
ANCHOR_LOG_SECTION = (
    "    ------------------------------------------------------------\n"
    "    -- ログ出力（描画モードの可視化）\n"
)
ANCHOR_TIME = "    local t = obj.time\n    local parts = {}\n"


class PatchError(RuntimeError):
    pass


def patch_text(src: str, blocks: dict) -> tuple[str, list[str]]:
    """Returns (patched_text, applied_anchor_names)."""
    if MARK_BEGIN in src:
        return src, ["already-patched"]

    applied: list[str] = []
    out = src

    # 1) バージョン表記と更新履歴（任意・見つからなければ飛ばす）
    m = ANCHOR_INFO.search(out)
    if m:
        ver = m.group(1)
        try:
            nxt = ".".join(str(int(x)) for x in ver.split("."))
            parts = [int(x) for x in nxt.split(".")]
        except ValueError:
            parts = []
        if parts and parts[-1] < 99:
            parts[-1] += 1
            new_ver = ".".join(str(x) for x in parts)
            note = (
                f"--\n"
                f"-- v{new_ver} 更新点 (体積光（3D）連動: 光束に照らされた粒子の受光):\n"
                f"--   ・同シリーズの「体積光（3D）」が毎フレーム公式の共有テーブル `global` へ\n"
                f"--     公開する VL3D Light Field v1 を読み、光束の内側にある雪粒を\n"
                f"--     光束色で色付けして加算発光させる受光パスを追加。\n"
                f"--   ・新パラメータ群「体積光連動」（体積光に照らされる / 受光の強さ /\n"
                f"--     光色の混色 / 受光のにじみ）。未導入環境では完全に無効（従来と同一）。\n"
                f"--   ・CPU 追加コストは粒子 1 個あたり ~30 演算。GPU 読み戻し・追加バッファ無し。\n"
            )
            out = out[:m.start()] + f"--information:雪（3D）v{new_ver}" + note + out[m.end():]
            applied.append("version")

    # 2) パラメータ群（本体コメントの直前）
    if ANCHOR_BODY_COMMENT in out:
        out = out.replace(
            ANCHOR_BODY_COMMENT,
            wrap("params", blocks["params"]) + "\n\n" + ANCHOR_BODY_COMMENT,
            1,
        )
        applied.append("params")
    else:
        raise PatchError("parameter-group anchor not found (-- 雪（3D） body comment)")

    # 3) 受光色乗算ピクセルシェーダ（既存シェーダ定義の後ろ）
    if "ps_snow_shape_tint" not in out:
        if ANCHOR_PIXELSHADER_END not in out:
            raise PatchError("pixel-shader anchor not found (local obj, math = ...)")
        out = out.replace(ANCHOR_PIXELSHADER_END,
                          "\n" + blocks["shader"] + ANCHOR_PIXELSHADER_END, 1)
        applied.append("shader")

    # 4) 受信ライブラリ（P パラメータ確定の直後）
    if ANCHOR_P_END not in out:
        raise PatchError("P-table anchor not found (P.near_dist clamp)")
    out = out.replace(ANCHOR_P_END, ANCHOR_P_END + "\n" + wrap("receiver", blocks["receiver"]) + "\n", 1)
    applied.append("receiver")

    # 5) フレーム開始時に光場を集める
    if ANCHOR_TIME not in out:
        raise PatchError("frame anchor not found (local t = obj.time / local parts = {})")
    out = out.replace(
        ANCHOR_TIME,
        "    local t = obj.time\n"
        "    local parts = {}\n"
        "    SNOW_LF.begin(obj)\n"
        "    local lf_sample = SNOW_LF.sample\n",
        1,
    )
    applied.append("begin")

    # 6) 粒子ごとの受光量
    if ANCHOR_PARTICLE_BRIGHT not in out:
        raise PatchError("particle anchor not found (rot / bright)")
    probe = blocks["probe"].rstrip("\n").replace("\n", "\n")
    out = out.replace(ANCHOR_PARTICLE_BRIGHT,
                      ANCHOR_PARTICLE_BRIGHT + probe + "\n", 1)
    applied.append("probe")

    # 7) parts へ受光量を追加
    if ANCHOR_PARTS_END not in out:
        raise PatchError("parts anchor not found (a_fig comment)")
    out = out.replace(
        ANCHOR_PARTS_END,
        "                a_fig,                    -- 図形テクスチャ有効α（bright反映済み）\n"
        "                lf_r, lf_g, lf_b, lf_lit  -- 体積光（3D）連動の受光色と受光量\n"
        "            }\n",
        1,
    )
    applied.append("parts-fields")

    # 8) 加算発光パス（ログ節の直前）
    if ANCHOR_LOG_SECTION not in out:
        raise PatchError("log-section anchor not found")
    out = out.replace(ANCHOR_LOG_SECTION,
                      blocks["lit_pass"].rstrip("\n") + "\n\n" + ANCHOR_LOG_SECTION, 1)
    applied.append("lit-pass")

    return out, applied


def find_script(snow3d: Path) -> Path:
    for name in DEFAULT_SCRIPT_NAMES:
        cand = snow3d / "Script" / name
        if cand.is_file():
            return cand
    hits = sorted((snow3d / "Script").glob("*.obj2")) if (snow3d / "Script").is_dir() else []
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"雪（3D）.obj2 not found under {snow3d}/Script (candidates: "
                     f"{[h.name for h in hits]})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snow3d", help="AviUtl2_Snow3D リポジトリのルート")
    ap.add_argument("--script", help=".obj2 / .anm2 を直接指定")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず結果だけ表示")
    ap.add_argument("--no-backup", action="store_true", help=".bak を作らない")
    args = ap.parse_args(argv)

    if args.script:
        path = Path(args.script).expanduser()
    elif args.snow3d:
        path = find_script(Path(args.snow3d).expanduser())
    else:
        ap.error("--snow3d か --script のどちらかが必要です")
    if not path.is_file():
        raise SystemExit(f"{path} is not a file")

    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")

    blocks = load_blocks()
    try:
        patched, applied = patch_text(text, blocks)
    except PatchError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("       このパッチは雪（3D）v1.22 系のアンカーを前提にしています。", file=sys.stderr)
        return 2

    if applied == ["already-patched"]:
        print(f"[SKIP] already patched: {path}")
        return 0

    # 構文チェック（LuaJIT があれば実行する）
    syntax_ok, syntax_msg = lua_syntax_check(patched)
    print(f"[{'OK' if syntax_ok else 'WARN'}] lua syntax: {syntax_msg}")

    new_raw = patched.replace("\n", "\r\n").encode("utf-8") if crlf else patched.encode("utf-8")
    print(f"[{'DRY' if args.dry_run else 'APPLY'}] {path}  ({len(raw)} -> {len(new_raw)} bytes)")
    print("        anchors: " + ", ".join(applied))
    if args.dry_run:
        return 0
    if not args.no_backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_bytes(new_raw)
    print("[OK] patched. AviUtl2 の「キャッシュを破棄」または再起動で反映されます。")
    return 0


def lua_syntax_check(code: str):
    """Best-effort LuaJIT syntax check (lupa is optional)."""
    try:
        from lupa.luajit21 import LuaRuntime  # type: ignore
    except Exception:
        return True, "lupa 未導入のためスキップ"
    rt = LuaRuntime(unpack_returned_tuples=True)
    fn = rt.eval(
        'function(s) local f, err = loadstring(s) '
        'if f then return true, "ok" else return false, tostring(err) end end'
    )
    ok, msg = fn(code)
    return bool(ok), str(msg)


if __name__ == "__main__":
    raise SystemExit(main())
