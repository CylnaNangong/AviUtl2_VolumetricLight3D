#ifndef _WIN32
// Linux x64 Native Hardware Guard-Page & SDK Test Wrapper for VL3DBridge Core
// Uses kernel `process_vm_readv` (exact Linux equivalent of Win32 `ReadProcessMemory`:
// copies inside the OS kernel without ever raising SIGSEGV on PROT_NONE or unmapped pages!).
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <sys/uio.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <string>
#include <vector>

#include "vl3d_bridge_core.h"

namespace {

struct LinuxTestCtx {
    uint64_t exe_base = 0;
    struct RegEntry {
        uint64_t base;
        uint64_t size;
        uint32_t state;
        uint32_t protect;
        uint32_t type;
    };
    RegEntry regs[64]{};
    int n_regs = 0;
    size_t write_attempts = 0;  // Must remain strictly 0!
};

LinuxTestCtx g_lctx;

uint64_t LinuxGetExeBase(void* u) {
    return static_cast<LinuxTestCtx*>(u)->exe_base;
}

bool LinuxReadMem(void*, uint64_t addr, void* dst, size_t size, size_t* out_read) {
    if (out_read) *out_read = 0;
    if (addr < 0x10000 || !dst || size == 0) return false;
    struct iovec local_iov{dst, size};
    struct iovec remote_iov{reinterpret_cast<void*>(static_cast<uintptr_t>(addr)), size};
    ssize_t n = ::process_vm_readv(::getpid(), &local_iov, 1, &remote_iov, 1, 0);
    if (n <= 0) {
        // Page-by-page partial copy fallback (matches ReadProcessMemory page-boundary behavior)
        size_t first_chunk = static_cast<size_t>(4096u - (addr & 4095u));
        if (first_chunk < size) {
            local_iov.iov_len = first_chunk;
            remote_iov.iov_len = first_chunk;
            n = ::process_vm_readv(::getpid(), &local_iov, 1, &remote_iov, 1, 0);
        }
        if (n <= 0) return false;
    }
    if (out_read) *out_read = static_cast<size_t>(n);
    return true;
}

bool LinuxQueryMem(void* u, uint64_t addr, vl3d::MemRegionInfo* out_reg) {
    auto* c = static_cast<LinuxTestCtx*>(u);
    for (int i = 0; i < c->n_regs; ++i) {
        const auto& r = c->regs[i];
        if (addr >= r.base && addr < r.base + r.size) {
            out_reg->base_addr = r.base;
            out_reg->region_size = r.size;
            out_reg->state = r.state;
            out_reg->protect = r.protect;
            out_reg->type = r.type;
            return true;
        }
    }
    return false;
}

}  // namespace

extern "C" {

void vl3d_test_reset(uint64_t exe_base) {
    g_lctx = LinuxTestCtx{};
    g_lctx.exe_base = exe_base;
    auto& eng = vl3d::GetGlobalBridgeEngine();
    eng.ClearAllowlist();
    eng.SetStrictWhitelistOnly(false);
    eng.SetEditHandle(nullptr);
    eng.InvalidateCache();
    vl3d::HostMemoryOps ops{};
    ops.get_exe_base = &LinuxGetExeBase;
    ops.read_mem = &LinuxReadMem;
    ops.query_mem = &LinuxQueryMem;
    ops.user = &g_lctx;
    eng.SetMemoryOps(ops);
}

void vl3d_test_add_region(uint64_t base, uint64_t size, uint32_t state, uint32_t protect, uint32_t type) {
    if (g_lctx.n_regs < 64) {
        g_lctx.regs[g_lctx.n_regs++] = {base, size, state, protect, type};
    }
}

void vl3d_test_load_ini(const char* ini_text) {
    if (ini_text) vl3d::GetGlobalBridgeEngine().LoadConfigIniText(ini_text);
}

bool vl3d_test_init_host(uint32_t version) {
    return vl3d::GetGlobalBridgeEngine().InitializeHost(version);
}

void vl3d_test_set_edit_handle(EDIT_HANDLE* eh) {
    vl3d::GetGlobalBridgeEngine().SetEditHandle(eh);
}

uint64_t vl3d_test_find_export(uint64_t mod_base, const char* func_name) {
    return vl3d::GetGlobalBridgeEngine().FindPeExportAddress(mod_base, func_name);
}

int32_t vl3d_test_count_modelfiles() {
    return vl3d::GetGlobalBridgeEngine().CountLiveModelFileInstancesChunked();
}

SCRIPT_MODULE_TABLE* vl3d_test_get_module_table() {
    return vl3d::GetGlobalBridgeScriptModuleTable();
}

void vl3d_test_add_default_allowlist() {
    vl3d::GetGlobalBridgeEngine().ResetDefaultAllowlist();
}

int vl3d_test_get_status_sha256(char* out_buf, int out_cap, int* out_allowlisted, uint32_t* out_size, uint32_t* out_ts) {
    auto st = vl3d::GetGlobalBridgeEngine().GetStatus();
    if (out_allowlisted) *out_allowlisted = st.whitelist_hit ? 1 : 0;
    if (out_size) *out_size = st.image_size;
    if (out_ts) *out_ts = st.pe_timestamp;
    if (out_buf && out_cap > 0) {
        std::snprintf(out_buf, static_cast<size_t>(out_cap), "%s", st.sha256_hex.c_str());
    }
    return st.pe_valid ? 1 : 0;
}

double vl3d_test_wall_time_sec() {
    static const auto t0 = std::chrono::steady_clock::now();
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

int32_t vl3d_test_has_mesh(const char* key, const char* rev) {
    return vl3d::GetGlobalBridgeEngine().HasMesh(key ? key : "", rev ? rev : "");
}

int32_t vl3d_test_upload_mesh_b64(const char* key, const char* rev, int nv, int nt, const char* vb64, const char* tb64) {
    if (!key || !vb64 || !tb64 || nv < 3 || nt < 1) return 0;
    return vl3d::GetGlobalBridgeEngine().UploadMeshBase64(key, rev ? rev : "", nv, nt, vb64, tb64);
}

int vl3d_test_rasterize_shadow(uint8_t* dst_rgba, int sh_res, const double* params22, const double* insts14, int n_insts,
                               int* out_tris, int* out_any) {
    if (!dst_rgba || !params22 || sh_res < 16 || n_insts < 1 || !insts14) return 0;
    vl3d::RastShadowParams sp{};
    sp.wx        = static_cast<float>(params22[0]);
    sp.wy        = static_cast<float>(params22[1]);
    sp.wz        = static_cast<float>(params22[2]);
    sp.Hx        = static_cast<float>(params22[3]);
    sp.Hy        = static_cast<float>(params22[4]);
    sp.Wrx       = static_cast<float>(params22[5]);
    sp.Wry       = static_cast<float>(params22[6]);
    sp.Wrz       = static_cast<float>(params22[7]);
    sp.Wdx       = static_cast<float>(params22[8]);
    sp.Wdy       = static_cast<float>(params22[9]);
    sp.Wdz       = static_cast<float>(params22[10]);
    sp.Wnx       = static_cast<float>(params22[11]);
    sp.Wny       = static_cast<float>(params22[12]);
    sp.Wnz       = static_cast<float>(params22[13]);
    sp.mode_kind = static_cast<float>(params22[14]);
    sp.sx_p      = static_cast<float>(params22[15]);
    sp.sy_p      = static_cast<float>(params22[16]);
    sp.sz_p      = static_cast<float>(params22[17]);
    sp.u_wn      = static_cast<float>(params22[18]);
    sp.dS_wn     = static_cast<float>(params22[19]);
    sp.L_tot     = static_cast<float>(params22[20]);
    sp.splat_cap = static_cast<int32_t>(params22[21]);
    std::vector<vl3d::RastModelInstance> v_inst(static_cast<size_t>(n_insts));
    for (int i = 0; i < n_insts; ++i) {
        const double* b = insts14 + i * 14;
        auto& m = v_inst[static_cast<size_t>(i)];
        m.mesh_id = static_cast<int32_t>(b[0]);
        m.is_vrm  = (b[1] > 0.5);
        m.ox  = static_cast<float>(b[2]);
        m.oy  = static_cast<float>(b[3]);
        m.oz  = static_cast<float>(b[4]);
        m.r0x = static_cast<float>(b[5]);
        m.r0y = static_cast<float>(b[6]);
        m.r0z = static_cast<float>(b[7]);
        m.d0x = static_cast<float>(b[8]);
        m.d0y = static_cast<float>(b[9]);
        m.d0z = static_cast<float>(b[10]);
        m.n0x = static_cast<float>(b[11]);
        m.n0y = static_cast<float>(b[12]);
        m.n0z = static_cast<float>(b[13]);
    }
    auto res = vl3d::GetGlobalBridgeEngine().RasterizeShadow(dst_rgba, sh_res, sp, v_inst);
    if (out_tris) *out_tris = res.tris_drawn;
    if (out_any) *out_any = res.any_pixel ? 1 : 0;
    return res.ok ? 1 : 0;
}

int vl3d_test_rasterize_camdepth(uint8_t* dst_rgba, int cm_w, int cm_h, const double* params16,
                                 const double* insts14, int n_insts, int* out_tris, int* out_any) {
    if (!dst_rgba || !params16 || cm_w < 16 || cm_h < 16 || n_insts < 1 || !insts14) return 0;
    vl3d::RastCamDepthParams cp{};
    cp.scr_w   = static_cast<float>(params16[0]);
    cp.scr_h   = static_cast<float>(params16[1]);
    cp.cam_x   = static_cast<float>(params16[2]);
    cp.cam_y   = static_cast<float>(params16[3]);
    cp.cam_z   = static_cast<float>(params16[4]);
    cp.focal_d = static_cast<float>(params16[5]);
    cp.f_x     = static_cast<float>(params16[6]);
    cp.f_y     = static_cast<float>(params16[7]);
    cp.f_z     = static_cast<float>(params16[8]);
    cp.r_x     = static_cast<float>(params16[9]);
    cp.r_y     = static_cast<float>(params16[10]);
    cp.r_z     = static_cast<float>(params16[11]);
    cp.d_x     = static_cast<float>(params16[12]);
    cp.d_y     = static_cast<float>(params16[13]);
    cp.d_z     = static_cast<float>(params16[14]);
    cp.max_z   = static_cast<float>(params16[15]);
    std::vector<vl3d::RastModelInstance> v_inst(static_cast<size_t>(n_insts));
    for (int i = 0; i < n_insts; ++i) {
        const double* b = insts14 + i * 14;
        auto& m = v_inst[static_cast<size_t>(i)];
        m.mesh_id = static_cast<int32_t>(b[0]);
        m.is_vrm  = (b[1] > 0.5);
        m.ox  = static_cast<float>(b[2]);
        m.oy  = static_cast<float>(b[3]);
        m.oz  = static_cast<float>(b[4]);
        m.r0x = static_cast<float>(b[5]);
        m.r0y = static_cast<float>(b[6]);
        m.r0z = static_cast<float>(b[7]);
        m.d0x = static_cast<float>(b[8]);
        m.d0y = static_cast<float>(b[9]);
        m.d0z = static_cast<float>(b[10]);
        m.n0x = static_cast<float>(b[11]);
        m.n0y = static_cast<float>(b[12]);
        m.n0z = static_cast<float>(b[13]);
    }
    auto res = vl3d::GetGlobalBridgeEngine().RasterizeCamDepth(dst_rgba, cm_w, cm_h, cp, v_inst);
    if (out_tris) *out_tris = res.tris_drawn;
    if (out_any) *out_any = res.any_pixel ? 1 : 0;
    return res.ok ? 1 : 0;
}

}  // extern "C"
#endif  // !_WIN32
