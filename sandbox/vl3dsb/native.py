"""
Build & load the cross-platform part of `src/VL3DBridge` on Linux.

`vl3d_bridge_selftest.cpp` / `vl3d_bridge_test_capi.cpp` are guarded by
`#ifndef _WIN32` and provide a complete test API (PE64 export parser, SHA-256,
UTF-8/16 helpers, whitelist / signature gate, SDK layer resolution, mesh cache,
CPU shadow + camera-depth rasterizers).  Compiling them with the system g++
gives us the *real* implementation under test - no re-implementation involved.

Only the D3D11 Compute Shader backend is unavailable (Windows-only by design);
everything else runs natively.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import REPO_ROOT
SRC_DIR = REPO_ROOT / "src" / "VL3DBridge"
OUT_DIR = Path(__file__).resolve().parents[1] / "out" / "build" / "native"

SOURCES = [
    "src/vl3d_bridge_core.cpp",
    "src/vl3d_bridge_rast.cpp",
    "src/vl3d_bridge_selftest.cpp",
    "src/vl3d_bridge_test_capi.cpp",
]


class NativeBridge:
    def __init__(self, cxx: Optional[str] = None, extra_flags: Sequence[str] = ()):
        self.cxx = cxx or os.environ.get("VL3D_CXX", "g++")
        self.extra_flags = list(extra_flags)
        self.build_sources()
        self._bind()

    # -- build ---------------------------------------------------------------
    def build_sources(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        for rel in SOURCES + ["src/vl3d_bridge_core.h", "src/vl3d_bridge_shaders.h",
                              "include/aviutl2_sdk/plugin2.h", "include/aviutl2_sdk/module2.h"]:
            p = SRC_DIR / rel
            digest.update(rel.encode())
            digest.update(p.read_bytes())
        self.hash = digest.hexdigest()[:16]
        self.lib_path = OUT_DIR / f"libvl3d_test_{self.hash}.so"
        self.build_log = ""
        if self.lib_path.exists():
            return
        cmd = [self.cxx, "-std=c++17", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
               "-I", str(SRC_DIR / "include")] + self.extra_flags
        cmd += [str(SRC_DIR / rel) for rel in SOURCES]
        cmd += ["-o", str(self.lib_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.build_log = proc.stderr
        if proc.returncode != 0:
            raise RuntimeError("failed to build VL3DBridge Linux test library:\n" + proc.stderr)

    def _bind(self):
        lib = ctypes.CDLL(str(self.lib_path))
        self.lib = lib
        lib.vl3d_test_selftest.restype = ctypes.c_int
        lib.vl3d_test_selftest.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.vl3d_test_reset.argtypes = [ctypes.c_uint64]
        lib.vl3d_test_add_region.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32,
                                             ctypes.c_uint32, ctypes.c_uint32]
        lib.vl3d_test_load_ini.argtypes = [ctypes.c_char_p]
        lib.vl3d_test_init_host.restype = ctypes.c_bool
        lib.vl3d_test_init_host.argtypes = [ctypes.c_uint32]
        lib.vl3d_test_find_export.restype = ctypes.c_uint64
        lib.vl3d_test_find_export.argtypes = [ctypes.c_uint64, ctypes.c_char_p]
        lib.vl3d_test_count_modelfiles.restype = ctypes.c_int32
        lib.vl3d_test_wall_time_sec.restype = ctypes.c_double
        lib.vl3d_test_add_default_allowlist.restype = None
        lib.vl3d_test_get_status_sha256.restype = ctypes.c_int
        lib.vl3d_test_get_status_sha256.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                                    ctypes.POINTER(ctypes.c_int),
                                                    ctypes.POINTER(ctypes.c_uint32),
                                                    ctypes.POINTER(ctypes.c_uint32)]
        lib.vl3d_test_has_mesh.restype = ctypes.c_int32
        lib.vl3d_test_has_mesh.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.vl3d_test_upload_mesh_b64.restype = ctypes.c_int32
        lib.vl3d_test_upload_mesh_b64.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
                                                  ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p]
        lib.vl3d_test_rasterize_shadow.restype = ctypes.c_int
        lib.vl3d_test_rasterize_shadow.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        lib.vl3d_test_rasterize_camdepth.restype = ctypes.c_int
        lib.vl3d_test_rasterize_camdepth.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]

    # -- api -----------------------------------------------------------------
    def selftest(self) -> Tuple[int, str]:
        buf = ctypes.create_string_buffer(1 << 17)
        rc = self.lib.vl3d_test_selftest(buf, len(buf))
        return rc, buf.value.decode("utf-8", "replace")

    def reset(self, exe_base: int = 0):
        self.lib.vl3d_test_reset(ctypes.c_uint64(exe_base))

    def add_region(self, base: int, size: int, state: int, protect: int, typ: int):
        self.lib.vl3d_test_add_region(base, size, state, protect, typ)

    def load_ini(self, text: str):
        self.lib.vl3d_test_load_ini(text.encode())

    def init_host(self, version: int) -> bool:
        return bool(self.lib.vl3d_test_init_host(version))

    def status_sha256(self):
        cap = ctypes.create_string_buffer(80)
        allow = ctypes.c_int(0)
        size = ctypes.c_uint32(0)
        ts = ctypes.c_uint32(0)
        pe_ok = self.lib.vl3d_test_get_status_sha256(cap, len(cap), ctypes.byref(allow),
                                                     ctypes.byref(size), ctypes.byref(ts))
        return pe_ok, cap.value.decode(), bool(allow.value), size.value, ts.value

    def find_export(self, base: int, name: str) -> int:
        return int(self.lib.vl3d_test_find_export(base, name.encode()))

    def wall_time_sec(self) -> float:
        return float(self.lib.vl3d_test_wall_time_sec())

    def has_mesh(self, key: str, rev: str = "") -> int:
        return int(self.lib.vl3d_test_has_mesh(key.encode(), str(rev).encode()))

    def add_default_allowlist(self):
        self.lib.vl3d_test_add_default_allowlist()

    def upload_mesh_b64(self, key: str, rev: str, nv: int, nt: int,
                        vb64, tb64) -> int:
        if isinstance(vb64, str):
            vb64 = vb64.encode()
        if isinstance(tb64, str):
            tb64 = tb64.encode()
        return int(self.lib.vl3d_test_upload_mesh_b64(key.encode(), str(rev).encode(), nv, nt, vb64, tb64))

    def rasterize_shadow(self, dst_addr: int, sh_res: int, params: Sequence[float],
                         insts: Sequence[float], n_inst: int):
        p = (ctypes.c_double * 22)(*params[:22])
        ins = (ctypes.c_double * max(1, len(insts)))(*insts)
        tris = ctypes.c_int(0)
        any_px = ctypes.c_int(0)
        ok = self.lib.vl3d_test_rasterize_shadow(ctypes.c_void_p(dst_addr), sh_res, p, ins,
                                                 n_inst, ctypes.byref(tris), ctypes.byref(any_px))
        return bool(ok), int(tris.value), bool(any_px.value)

    def rasterize_camdepth(self, dst_addr: int, cm_w: int, cm_h: int, params: Sequence[float],
                           insts: Sequence[float], n_inst: int):
        p = (ctypes.c_double * 16)(*params[:16])
        ins = (ctypes.c_double * max(1, len(insts)))(*insts)
        tris = ctypes.c_int(0)
        any_px = ctypes.c_int(0)
        ok = self.lib.vl3d_test_rasterize_camdepth(ctypes.c_void_p(dst_addr), cm_w, cm_h, p, ins,
                                                   n_inst, ctypes.byref(tris), ctypes.byref(any_px))
        return bool(ok), int(tris.value), bool(any_px.value)


def get_native() -> Optional[NativeBridge]:
    try:
        return NativeBridge()
    except Exception:
        return None
