#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "aviutl2_sdk/module2.h"
#include "aviutl2_sdk/plugin2.h"

namespace vl3d {

struct MemRegionInfo {
    uint64_t base_addr = 0;
    uint64_t region_size = 0;
    uint32_t state = 0;    // 0x1000 = MEM_COMMIT
    uint32_t protect = 0;  // 0x02=R, 0x04=RW, 0x20=RX, 0x40=RWX
    uint32_t type = 0;     // 0x20000 = MEM_PRIVATE, 0x1000000 = MEM_IMAGE
};

// Pluggable OS memory/host abstraction:
// - Production Windows (.aux2): Win32 ReadProcessMemory + VirtualQuery + GetModuleHandleW(nullptr)
// - Sandbox Linux test build (.so): Guard-page mmap + process_vm_readv / safe kernel-checked copy
struct HostMemoryOps {
    uint64_t (*get_exe_base)(void* user) = nullptr;
    bool (*read_mem)(void* user, uint64_t addr, void* dst, size_t size, size_t* out_read) = nullptr;
    bool (*query_mem)(void* user, uint64_t addr, MemRegionInfo* out_reg) = nullptr;
    void* user = nullptr;
};

struct AllowlistEntry {
    uint32_t pe_timestamp = 0;   // 0 = wildcard if sha256 matches
    uint32_t image_size = 0;     // 0 = wildcard if sha256 matches
    std::string sha256_hex;      // lowercase 64-char hex (empty = match timestamp+size)
    std::string label;
};

struct HostStatus {
    bool initialized = false;
    bool sdk_ready = false;
    bool pe_valid = false;
    bool whitelist_hit = false;
    bool sig_scene_ok = false;
    bool sig_editcam_ok = false;
    bool rpm_allowed = false;    // True ONLY when host ABI is verified safe
    bool strict_whitelist_only = false;
    bool enable_d3d11_cs = true;
    uint32_t host_version = 0;
    uint32_t pe_timestamp = 0;
    uint32_t image_size = 0;
    int32_t active_scene_id = 0;
    std::string sha256_hex;
    std::string mode;            // "SDK+VERIFIED_RPM", "SDK_ONLY_SAFE_DEGRADED", "VERIFIED_RPM_ONLY", "UNVERIFIED_DEGRADED"
};

struct LayerResolveResult {
    std::string status = "UNAVAILABLE"; // "OBJ", "VRM", "WATER", "OTHER", "HIDDEN", "DISABLED", "EMPTY", "UNAVAILABLE"
    std::string fx_name;
    std::string path;
    int32_t adj = 0;
    int32_t scene_id = 0;
    int32_t water_color = -1;           // 0xRRGGBB if @水面 color item found via SDK, else -1
    std::string source_tier;            // "SDK", "SDK_CACHE", "RPM", "NONE"
    // v1.21: Built-in [テキスト] alignment & decoration padding (aviutl2.exe 0x14053bcd0 / 0x1402670cd)
    bool is_text = false;
    double align_fx = 0.0;
    double align_fy = 0.0;
    double pad_l = 0.0;
    double pad_r = 0.0;
    double pad_t = 0.0;
    double pad_b = 0.0;
};

struct EditCameraResult {
    bool ok = false;
    double x = 0.0, y = 0.0, z = -1024.0;
    double tx = 0.0, ty = 0.0, tz = 0.0;
    double ux = 0.0, uy = -1.0, uz = 0.0;
    double rz = 0.0, d = 1024.0;
};

struct CameraLightsResult {
    int32_t count = 0;
    double unlit = 1.0;
    double amb_r = 0.0, amb_g = 0.0, amb_b = 0.0;
    double peak_gain = 1.0;
    bool has_primary = false;
    int32_t ltype = 0;
    double dx = 0.0, dy = 0.0, dz = 1.0;
    double r = 0.0, g = 0.0, b = 0.0, lum = 0.0;
    double px = 0.0, py = 0.0, pz = 0.0;
    double cos_ang = 0.0;
};

// =========================================================================
// v1.20 Native 3D Shadow & Camera-Depth Rasterizer Types (D3D11 CS + C++ CPU)
// =========================================================================
struct alignas(16) RastCBData {
    float c[16][4];
};
static_assert(sizeof(RastCBData) == 256, "RastCBData must be exactly 256 bytes (16 x float4)");

struct GpuRasterStatus {
    bool d3d11_available = false;
    bool d3d11_ready = false;
    bool force_cpu = false;
    int32_t cached_meshes = 0;
    uint64_t gpu_bytes = 0;
    std::string backend;      // "D3D11_CS", "CPP_CPU"
    std::string last_error;
};

struct RastModelInstance {
    int32_t mesh_id = 0;
    bool is_vrm = false;
    float ox = 0.0f, oy = 0.0f, oz = 0.0f;
    float r0x = 1.0f, r0y = 0.0f, r0z = 0.0f;  // Or * ozx
    float d0x = 0.0f, d0y = 1.0f, d0z = 0.0f;  // Od * ozy
    float n0x = 0.0f, n0y = 0.0f, n0z = 1.0f;  // On * ozz
};

struct RastShadowParams {
    float wx = 0.0f, wy = 0.0f, wz = 0.0f;
    float Hx = 100.0f, Hy = 100.0f;
    float Wrx = 1.0f, Wry = 0.0f, Wrz = 0.0f;
    float Wdx = 0.0f, Wdy = 1.0f, Wdz = 0.0f;
    float Wnx = 0.0f, Wny = 0.0f, Wnz = 1.0f;
    float mode_kind = 0.0f;
    float sx_p = 0.0f, sy_p = 0.0f, sz_p = 1.0f;
    float u_wn = 1.0f, dS_wn = 1.0f, L_tot = 1000.0f;
    int32_t splat_cap = 2048;
};

struct RastCamDepthParams {
    float scr_w = 1920.0f, scr_h = 1080.0f;
    float cam_x = 0.0f, cam_y = 0.0f, cam_z = -1024.0f;
    float focal_d = 1024.0f;
    float f_x = 0.0f, f_y = 0.0f, f_z = 1.0f;
    float r_x = 1.0f, r_y = 0.0f, r_z = 0.0f;
    float d_x = 0.0f, d_y = 1.0f, d_z = 0.0f;
    float max_z = 4096.0f;
};

struct RastOutput {
    bool ok = false;
    bool any_pixel = false;
    int32_t tris_drawn = 0;
    std::string backend;  // "D3D11_CS" or "CPP_CPU"
};

RastCBData BuildShadowRastCB(int32_t sh_res, const RastShadowParams& p,
                             const RastModelInstance& inst, uint32_t tri_count);
RastCBData BuildCamDepthRastCB(int32_t cm_w, int32_t cm_h, const RastCamDepthParams& p,
                               const RastModelInstance& inst, uint32_t tri_count);
bool DecodeBase64Bytes(const char* src, size_t src_len, std::vector<uint8_t>* out);

class BridgeEngine {
public:
    BridgeEngine();

    void SetMemoryOps(const HostMemoryOps& ops);
    void SetEditHandle(EDIT_HANDLE* edit_handle);
    void ClearAllowlist();
    void AddAllowlistEntry(const AllowlistEntry& entry);
    void SetStrictWhitelistOnly(bool strict_only);
    void SetForceCpuRasterizer(bool force_cpu);
    bool LoadConfigIniText(const std::string& ini_text);

    bool InitializeHost(uint32_t host_version);
    void OnSceneChanged(int32_t scene_id);
    void InvalidateCache();
    void ReleaseGpuResources();

    HostStatus GetStatus() const;
    EDIT_HANDLE* GetEditHandle() const;
    LayerResolveResult ResolveLayer(int32_t layer_1based, int32_t frame_0based,
                                    EDIT_SECTION* inline_edit = nullptr, bool from_script_module = false);
    int32_t GetLayerWaterColor(int32_t layer_1based, int32_t frame_0based,
                               EDIT_SECTION* inline_edit = nullptr, bool from_script_module = false);
    EditCameraResult ReadEditCamera();
    CameraLightsResult ReadCameraLights();
    EditCameraResult ReadSceneCameraViaSdk(int32_t max_layer_1based, int32_t frame_0based,
                                           EDIT_SECTION* inline_edit = nullptr, bool from_script_module = false);

    // v1.20 3D Mesh Cache & Hardware/Native Rasterization API
    GpuRasterStatus GetGpuRasterStatus();
    int32_t HasMesh(const std::string& slot_key, const std::string& rev_sig);
    int32_t UploadMeshBase64(const std::string& slot_key, const std::string& rev_sig,
                             int32_t nv, int32_t nt,
                             const char* verts_b64, const char* tris_b64);
    int32_t UploadMeshRaw(const std::string& slot_key, const std::string& rev_sig,
                          const float* xyz3, int32_t nv,
                          const uint32_t* idx3_0based, int32_t nt);
    RastOutput RasterizeShadow(void* dst_rgba, int32_t sh_res,
                               const RastShadowParams& params,
                               const std::vector<RastModelInstance>& instances);
    RastOutput RasterizeCamDepth(void* dst_rgba, int32_t cm_w, int32_t cm_h,
                                 const RastCamDepthParams& params,
                                 const std::vector<RastModelInstance>& instances);

    // Hardened PE64 export lookup utility (bounds-checked against malformed headers)
    uint64_t FindPeExportAddress(uint64_t mod_base, const char* func_name) const;
    // Hardened 64KB-chunked live ModelFile instance scanner (TOCTOU-safe)
    int32_t CountLiveModelFileInstancesChunked() const;

private:
    bool ReadExact(uint64_t addr, void* dst, size_t sz) const;
    bool ReadU64(uint64_t addr, uint64_t* out) const;
    bool ReadI32(uint64_t addr, int32_t* out) const;
    bool ReadU8(uint64_t addr, uint8_t* out) const;
    bool ReadWString(uint64_t addr, size_t max_units, std::string* out_utf8) const;

    LayerResolveResult ResolveLayerViaSdk(int32_t layer_1based, int32_t frame_0based, bool* sdk_attempted_ok,
                                          EDIT_SECTION* inline_edit = nullptr, bool from_script_module = false);
    LayerResolveResult ResolveLayerViaRpm(int32_t layer_1based, int32_t frame_0based);

    HostMemoryOps ops_{};
    EDIT_HANDLE* edit_handle_ = nullptr;
    HostStatus status_{};
    std::vector<AllowlistEntry> allowlist_;

    // Verified RVA / candidate pointers (used ONLY in read-only mode)
    std::vector<uint64_t> lctx_cands_;
    bool edit_cam_sig_found_ = false;
    uint64_t edit_cam_g_addr_ = 0;
    uint32_t edit_cam_off_w_ = 0;
    uint32_t edit_cam_off_c_ = 0;

    struct CachedClip {
        int32_t scene_id = 0;
        int32_t layer0 = 0;
        int32_t start_frame = 0;
        int32_t end_frame = 0;
        LayerResolveResult result;
    };
    std::vector<CachedClip> sdk_clip_cache_;
};

// UTF-8 <-> UTF-16LE helpers (cross-platform, surrogate-pair safe)
std::string Utf16LeToUtf8(const uint16_t* wptr, size_t max_units);
std::u16string Utf8ToUtf16Le(const std::string& utf8);
std::string ComputeSha256Hex(const uint8_t* data, size_t len);

// ScriptModule function table builder (shared between Windows .aux2 and Linux native test .so)
SCRIPT_MODULE_TABLE* GetGlobalBridgeScriptModuleTable();
BridgeEngine& GetGlobalBridgeEngine();

}  // namespace vl3d
