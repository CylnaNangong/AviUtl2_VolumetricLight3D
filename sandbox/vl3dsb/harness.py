"""
Tiny test harness shared by every sandbox check module.

A check is a function decorated with @check(name, tags=..., slow=True/False).
It either returns a value (PASS), returns False/None->FAIL via `fail()`, raises
(FAIL with traceback) or calls `skip()`/`warn()` explicitly.

`python3 run_checks.py` discovers all `checks_*.py` modules in sandbox/checks/.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

SANDBOX_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = SANDBOX_DIR / "out"
CHECKS_DIR = SANDBOX_DIR / "checks"

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"


class Skip(Exception):
    pass


class Fail(Exception):
    pass


@dataclass
class CheckSpec:
    name: str
    fn: Callable
    tags: List[str] = field(default_factory=list)
    module: str = ""
    doc: str = ""


@dataclass
class CheckResult:
    name: str
    status: str
    module: str
    seconds: float = 0.0
    detail: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status, "module": self.module,
            "seconds": round(self.seconds, 4), "detail": self.detail,
            "metrics": self.metrics, "artifacts": self.artifacts, "tags": self.tags,
        }


REGISTRY: List[CheckSpec] = []


def check(name: str, tags: Iterable[str] = ()) -> Callable:
    def deco(fn: Callable) -> Callable:
        REGISTRY.append(CheckSpec(name=name, fn=fn, tags=list(tags),
                                  module=fn.__module__, doc=(fn.__doc__ or "").strip()))
        return fn
    return deco


def skip(msg: str = "") -> None:
    raise Skip(msg)


def fail(msg: str) -> None:
    raise Fail(msg)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def run_module(path: Path) -> List[CheckSpec]:
    """Import a checks module and return the specs it registered."""
    name = "vl3d_checks_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return []
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    before = len(REGISTRY)
    spec.loader.exec_module(mod)
    new = REGISTRY[before:]
    for c in new:
        c.module = path.stem
    return new


def discover(only: Optional[Iterable[str]] = None, tags: Optional[Iterable[str]] = None) -> List[CheckSpec]:
    files = sorted(CHECKS_DIR.glob("checks_*.py"))
    specs: List[CheckSpec] = []
    for f in files:
        specs.extend(run_module(f))
    if only:
        wanted = list(only)
        specs = [s for s in specs if any(w.lower() in s.name.lower() for w in wanted)]
    if tags:
        wanted = set(tags)
        specs = [s for s in specs if wanted & set(s.tags)]
    return specs


def run_check(spec: CheckSpec, verbose: bool = False) -> CheckResult:
    t0 = time.time()
    status, detail, metrics, artifacts = PASS, "", {}, []
    try:
        out = spec.fn()
        if out is False:
            status = FAIL
            detail = "returned False"
        elif isinstance(out, dict):
            metrics = {k: v for k, v in out.items() if not isinstance(v, (list, tuple)) or len(v) < 12}
            detail = str(out.get("detail", ""))
            artifacts = list(out.get("artifacts", []) or [])
            status = str(out.get("status", PASS))
    except Skip as exc:
        status, detail = SKIP, str(exc)
    except Fail as exc:
        status, detail = FAIL, str(exc)
    except AssertionError as exc:
        status, detail = FAIL, f"assertion: {exc}"
    except Exception as exc:  # noqa: BLE001
        status = FAIL
        tb = traceback.format_exc(limit=6)
        detail = f"{type(exc).__name__}: {exc}"
        if verbose:
            detail += "\n" + tb
    return CheckResult(name=spec.name, status=status, module=spec.module,
                       seconds=time.time() - t0, detail=detail, metrics=metrics,
                       artifacts=[str(a) for a in artifacts], tags=spec.tags)


def summarize(results: List[CheckResult]) -> Dict[str, int]:
    out = {PASS: 0, FAIL: 0, SKIP: 0, WARN: 0}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    return out
