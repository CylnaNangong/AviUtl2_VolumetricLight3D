# Evaluation artifacts (2026-10-27, Arena.ai Agent)

Independent verification artifacts for the `体積光（3D） v3.13` / `VL3DBridge v1.21` evaluation.

- `cpu_gpu_shim.py` — numpy CPU backend shim for the GPU-fidelity sandbox
  (replaces the wgpu/Vulkan device with an exact poly-rasterizer + identity
  approximation for `ps_*` fullscreen passes; enables GPU-less regression runs).
- `run_cpu_full_checks.py` — 20-check CPU sandbox suite (v3.06 bundled reference
  checks + v3.13 repository checks + native-bridge cross-validation).
- `analyze_shmap_diff.py` — Lua-rasterizer vs native-C++-rasterizer shadow-map
  diff analysis.
- `cpu_sandbox_checks.log` — full log of the final run (19/20 PASS; the single
  FAIL is the E3 threshold being stricter than the measured 1-pixel/13,197
  fill-rule tolerance — see the analysis script output in the report).
- `cpu_sandbox_report.json` — machine-readable results.

Usage: copy the three `.py` files next to `aviutl2_sandbox.py` inside the
extracted `体積光3D_GPU沙箱_v3.06.zip` sandbox and run
`python run_cpu_full_checks.py` (Python 3.11–3.13, numpy/Pillow/lupa, no GPU
required). The sandbox's `lib_linux/libvl3d_bridge_linux.so` should be rebuilt
from `src/VL3DBridge` (g++ -std=c++17 -O2 -shared -fPIC -I include
src/vl3d_bridge_core.cpp src/vl3d_bridge_rast.cpp src/vl3d_bridge_selftest.cpp
src/vl3d_bridge_test_capi.cpp) if the bundled one requires a newer glibc.
