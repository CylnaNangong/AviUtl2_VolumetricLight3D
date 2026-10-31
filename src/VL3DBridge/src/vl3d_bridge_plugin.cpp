#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <string>
#include <vector>

#include "vl3d_bridge_core.h"

namespace {

HMODULE g_self_hmod = nullptr;
DWORD g_host_version = 0;

// RAII Win32 HANDLE wrapper (exception-safe CloseHandle)
struct ScopedWinHandle {
    HANDLE h = INVALID_HANDLE_VALUE;
    explicit ScopedWinHandle(HANDLE v) : h(v) {}
    ~ScopedWinHandle() {
        if (h != nullptr && h != INVALID_HANDLE_VALUE) {
            ::CloseHandle(h);
        }
    }
    ScopedWinHandle(const ScopedWinHandle&) = delete;
    ScopedWinHandle& operator=(const ScopedWinHandle&) = delete;
    bool valid() const { return h != nullptr && h != INVALID_HANDLE_VALUE; }
};

uint64_t WinGetExeBase(void*) {
    HMODULE exe = ::GetModuleHandleW(nullptr);
    return reinterpret_cast<uint64_t>(exe);
}

// Kernel-mode SEH-safe memory reader (ReadProcessMemory -> MmCopyVirtualMemory):
// Never dereferences raw pointers in user-mode; returns false cleanly on PAGE_NOACCESS / PAGE_GUARD / unmapped!
bool WinReadMem(void*, uint64_t addr, void* dst, size_t size, size_t* out_read) {
    if (out_read) *out_read = 0;
    if (addr < 0x10000 || !dst || size == 0) return false;
    SIZE_T got = 0;
    BOOL ok = ::ReadProcessMemory(
        ::GetCurrentProcess(),
        reinterpret_cast<LPCVOID>(static_cast<uintptr_t>(addr)),
        dst,
        size,
        &got);
    if (out_read) *out_read = static_cast<size_t>(got);
    return (ok != FALSE) && (got > 0);
}

bool WinQueryMem(void*, uint64_t addr, vl3d::MemRegionInfo* out_reg) {
    if (!out_reg) return false;
    MEMORY_BASIC_INFORMATION mbi{};
    SIZE_T r = ::VirtualQuery(
        reinterpret_cast<LPCVOID>(static_cast<uintptr_t>(addr)),
        &mbi,
        sizeof(mbi));
    if (r == 0) return false;
    out_reg->base_addr = reinterpret_cast<uint64_t>(mbi.BaseAddress);
    out_reg->region_size = static_cast<uint64_t>(mbi.RegionSize);
    out_reg->state = static_cast<uint32_t>(mbi.State);
    out_reg->protect = static_cast<uint32_t>(mbi.Protect);
    out_reg->type = static_cast<uint32_t>(mbi.Type);
    return true;
}

// Compute ASLR-invariant SHA-256 directly from the on-disk host executable (matches `Get-FileHash aviutl2.exe`).
bool WinHashExeFile(void*, std::string* out_sha256_hex) {
    if (!out_sha256_hex) return false;
    wchar_t exe_path[MAX_PATH * 4] = {};
    DWORD n = ::GetModuleFileNameW(nullptr, exe_path, MAX_PATH * 4 - 4);
    if (n == 0 || n >= MAX_PATH * 4 - 4) return false;
    ScopedWinHandle fh(::CreateFileW(
        exe_path, GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!fh.valid()) return false;
    LARGE_INTEGER fsz{};
    if (!::GetFileSizeEx(fh.h, &fsz) || fsz.QuadPart <= 0 || fsz.QuadPart > (256LL * 1024 * 1024)) return false;

    vl3d::Sha256Stream sha;
    std::vector<uint8_t> chunk(65536);
    LONGLONG rem = fsz.QuadPart;
    while (rem > 0) {
        DWORD want = static_cast<DWORD>(rem > 65536 ? 65536 : rem);
        DWORD rd = 0;
        if (!::ReadFile(fh.h, chunk.data(), want, &rd, nullptr) || rd == 0) return false;
        sha.Update(chunk.data(), rd);
        rem -= rd;
    }
    *out_sha256_hex = sha.FinalHex();
    return out_sha256_hex->size() == 64u;
}

void LoadSidecarConfigIni() {
    if (!g_self_hmod) return;
    wchar_t path_buf[MAX_PATH * 4] = {};
    DWORD n = ::GetModuleFileNameW(g_self_hmod, path_buf, MAX_PATH * 4 - 16);
    if (n == 0 || n >= MAX_PATH * 4 - 16) return;
    std::wstring ini_path(path_buf, n);
    size_t dot = ini_path.find_last_of(L'.');
    size_t slash = ini_path.find_last_of(L"\\/");
    if (dot != std::wstring::npos && (slash == std::wstring::npos || dot > slash)) {
        ini_path = ini_path.substr(0, dot) + L".ini";
    } else {
        ini_path += L".ini";
    }
    ScopedWinHandle fh(::CreateFileW(
        ini_path.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!fh.valid()) return;
    LARGE_INTEGER fsz{};
    if (!::GetFileSizeEx(fh.h, &fsz) || fsz.QuadPart <= 0 || fsz.QuadPart > 1048576) return;
    std::string text(static_cast<size_t>(fsz.QuadPart), '\0');
    DWORD rd = 0;
    if (::ReadFile(fh.h, text.data(), static_cast<DWORD>(text.size()), &rd, nullptr) && rd > 0) {
        text.resize(rd);
        vl3d::GetGlobalBridgeEngine().LoadConfigIniText(text);
    }
}

void OnClearCache(EDIT_SECTION*) {
    vl3d::GetGlobalBridgeEngine().InvalidateCache();
}

void OnChangeScene(EDIT_SECTION* edit) {
    if (edit && edit->info) {
        vl3d::GetGlobalBridgeEngine().OnSceneChanged(edit->info->scene_id);
    }
    vl3d::GetGlobalBridgeEngine().InvalidateCache();
}

void OnProjectLoad(PROJECT_FILE*) {
    vl3d::GetGlobalBridgeEngine().InvalidateCache();
}

COMMON_PLUGIN_TABLE g_common_plugin_table = {
    L"VL3DBridge",
    L"VL3DBridge v1.22 - Lock-Free Read-Only Bridge + D3D11 CS 3D Rasterizer for VolumetricLight3D (体積光（3D）)",
};

}  // namespace

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID) {
    if (ul_reason_for_call == DLL_PROCESS_ATTACH) {
        g_self_hmod = hModule;
        ::DisableThreadLibraryCalls(hModule);
    }
    return TRUE;
}

extern "C" {

// 1. Official AviUtl ExEdit2 Common Plugin (.aux2) Exports
__declspec(dllexport) DWORD RequiredVersion() {
    // Compatible from AviUtl ExEdit2 beta18+ (2001800); beta31+ features gated dynamically on g_host_version
    return 2001800;
}

__declspec(dllexport) bool InitializePlugin(DWORD version) {
    g_host_version = version;
    auto& eng = vl3d::GetGlobalBridgeEngine();
    eng.ResetDefaultAllowlist();
    vl3d::HostMemoryOps ops{};
    ops.get_exe_base = &WinGetExeBase;
    ops.read_mem = &WinReadMem;
    ops.query_mem = &WinQueryMem;
    ops.hash_exe_file = &WinHashExeFile;
    ops.user = nullptr;
    eng.SetMemoryOps(ops);
    LoadSidecarConfigIni();
    return eng.InitializeHost(version);
}

__declspec(dllexport) void UninitializePlugin() {
    vl3d::GetGlobalBridgeEngine().InvalidateCache();
    vl3d::GetGlobalBridgeEngine().ReleaseGpuResources();
}

__declspec(dllexport) COMMON_PLUGIN_TABLE* GetCommonPluginTable() {
    return &g_common_plugin_table;
}

__declspec(dllexport) void RegisterPlugin(HOST_APP_TABLE* host) {
    if (!host) return;
    if (host->set_plugin_information) {
        host->set_plugin_information(L"VL3DBridge v1.22 (Lock-Free Read-Only Bridge + D3D11 CS 3D Shadow Rasterizer)");
    }
    if (host->create_edit_handle) {
        EDIT_HANDLE* eh = host->create_edit_handle();
        vl3d::GetGlobalBridgeEngine().SetEditHandle(eh);
    }
    if (g_host_version >= 2003100 && host->register_script_module_name) {
        host->register_script_module_name(vl3d::GetGlobalBridgeScriptModuleTable(), L"VL3DBridge");
    } else if (host->register_script_module) {
        host->register_script_module(vl3d::GetGlobalBridgeScriptModuleTable());
    }
    if (host->register_clear_cache_handler) {
        host->register_clear_cache_handler(&OnClearCache);
    }
    if (host->register_change_scene_handler) {
        host->register_change_scene_handler(&OnChangeScene);
    }
    if (host->register_project_load_handler) {
        host->register_project_load_handler(&OnProjectLoad);
    }
}

// 2. Also export GetScriptModuleTable() so VL3DBridge can optionally be loaded directly as a .mod2!
__declspec(dllexport) SCRIPT_MODULE_TABLE* GetScriptModuleTable() {
    auto st = vl3d::GetGlobalBridgeEngine().GetStatus();
    if (!st.initialized) {
        InitializePlugin(2003100);
    }
    return vl3d::GetGlobalBridgeScriptModuleTable();
}

}  // extern "C"
#endif  // _WIN32
