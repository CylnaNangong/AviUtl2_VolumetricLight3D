"""加载脚本的数值常量表（``modules/00a_consts.lua``）的真实运行期值。

为什么不在 Python 里重写一遍常量：那样只会多出第 N 份副本。这里直接在
LuaJIT 里执行**脚本自己用的那个文件**，连带跑完它末尾的 ``assert``，
再读回 ``K``。于是「常量表自检有没有通过」和「检查看到的数值是多少」
是同一个事实来源。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .config import REPO_ROOT

CONSTS_LUA: Path = REPO_ROOT / "src" / "VolumetricLight3D" / "modules" / "00a_consts.lua"


def _to_python(value: Any) -> Any:
    """lupa 的 table → 嵌套 dict（只处理本表用到的数字/表）。"""
    if hasattr(value, "items"):
        return {str(k): _to_python(v) for k, v in value.items()}
    if isinstance(value, (int, float)):
        return float(value)
    return value


def lua_consts(path: Path | None = None) -> Dict[str, Any]:
    """执行常量表并返回 ``K``（失败时抛出带原始 Lua 报错的 AssertionError）。"""
    from lupa.luajit21 import LuaRuntime

    src_path = Path(path or CONSTS_LUA)
    if not src_path.exists():
        raise AssertionError(f"missing {src_path}")
    lua = LuaRuntime(unpack_returned_tuples=True)
    try:
        table = lua.execute(src_path.read_text(encoding="utf-8") + "\nreturn K")
    except Exception as exc:                       # noqa: BLE001 - LuaError
        raise AssertionError(f"00a_consts.lua failed its own self-check: {exc}") from exc
    return _to_python(table)
