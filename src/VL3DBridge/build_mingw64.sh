#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${SCRIPT_DIR}/../../Plugin" ]; then
    OUT_DIR="$(cd "${SCRIPT_DIR}/../../Plugin" && pwd)"
else
    mkdir -p "${SCRIPT_DIR}/../Plugin"
    OUT_DIR="$(cd "${SCRIPT_DIR}/../Plugin" && pwd)"
fi

echo "[1/2] Cross-compiling Windows x64 VL3DBridge.aux2 v1.21 (MinGW-w64 GCC C++17, O3 + D3D11 CS)..."
x86_64-w64-mingw32-g++ -std=c++17 -O3 -Wall -Wextra -Werror \
    -shared -static -static-libgcc -static-libstdc++ \
    -Wl,--enable-stdcall-fixup -Wl,--gc-sections -s \
    -I"${SCRIPT_DIR}/include" \
    "${SCRIPT_DIR}/src/vl3d_bridge_core.cpp" \
    "${SCRIPT_DIR}/src/vl3d_bridge_rast.cpp" \
    "${SCRIPT_DIR}/src/vl3d_bridge_plugin.cpp" \
    -o "${OUT_DIR}/VL3DBridge.aux2"

echo "[2/2] Inspecting PE32+ headers, DLL imports, and exported symbols..."
file "${OUT_DIR}/VL3DBridge.aux2"
x86_64-w64-mingw32-objdump -p "${OUT_DIR}/VL3DBridge.aux2" | grep -E "DLL Name:|Export Table|\[[[:space:]]*[0-9]+\]" | head -n 40
ls -lh "${OUT_DIR}/VL3DBridge.aux2"
