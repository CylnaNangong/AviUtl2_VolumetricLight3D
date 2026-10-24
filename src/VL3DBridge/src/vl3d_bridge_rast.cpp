#include "vl3d_bridge_core.h"
#include "vl3d_bridge_shaders.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#ifdef _WIN32
#include <d3d11_3.h>
#include <d3dcompiler.h>
#endif

namespace vl3d {

static_assert(offsetof(EDIT_HANDLE, get_high_dpi_ratio) == 0xD0,
              "EDIT_HANDLE::get_high_dpi_ratio must be at offset 0xD0 (Plugin SDK 2026/10/17+)");
static_assert(offsetof(EDIT_HANDLE, get_direct3d_device) == 0xD8,
              "EDIT_HANDLE::get_direct3d_device must be at offset 0xD8 (Plugin SDK 2026/10/24)");
static_assert(sizeof(EDIT_HANDLE) == 0xE0,
              "EDIT_HANDLE size in Plugin SDK 2026/10/24 must be 224 bytes (0xE0)");

namespace {

constexpr uint32_t kMaxMeshSlots = 64;
constexpr uint32_t kMaxLargeTris = 4096;
constexpr int32_t kLargeBboxThreshold = 1024;
constexpr uint32_t kMaxRastDim = 4096;

inline float Clamp01(float v) {
    return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v);
}

inline uint32_t PackProxVal(float pr, uint32_t is_vrm_u) {
    float pc = Clamp01(pr);
    uint32_t q = static_cast<uint32_t>(pc * 16777214.0f + 0.5f) + 1u;
    return (q << 1u) | (is_vrm_u & 1u);
}

struct Vec3 {
    float x, y, z;
};
struct Vec4 {
    float x, y, z, w;
};

inline float Dot3(Vec3 a, Vec3 b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

inline Vec3 Add3(Vec3 a, Vec3 b) {
    return {a.x + b.x, a.y + b.y, a.z + b.z};
}

inline Vec3 Sub3(Vec3 a, Vec3 b) {
    return {a.x - b.x, a.y - b.y, a.z - b.z};
}

inline Vec3 Mul3(Vec3 a, float s) {
    return {a.x * s, a.y * s, a.z * s};
}

inline Vec4 ProjectWorldPtCpu(const RastCBData& cb, Vec3 P) {
    if (cb.c[0][2] < 0.5f) {
        Vec3 w_pos{cb.c[7][0], cb.c[7][1], cb.c[7][2]};
        Vec3 Wr{cb.c[8][0], cb.c[8][1], cb.c[8][2]};
        Vec3 Wd{cb.c[9][0], cb.c[9][1], cb.c[9][2]};
        Vec3 Wn{cb.c[10][0], cb.c[10][1], cb.c[10][2]};
        float u_wn = cb.c[10][3];
        Vec3 sp{cb.c[11][0], cb.c[11][1], cb.c[11][2]};
        float dS_wn = cb.c[11][3];
        float t_ray = 0.0f;
        Vec3 qw{};
        if (cb.c[3][3] < 0.5f) {
            t_ray = Dot3(Sub3(P, w_pos), Wn) / u_wn;
            qw = Sub3(P, Mul3(sp, t_ray));
        } else {
            Vec3 dv = Sub3(P, sp);
            float denom = Dot3(dv, Wn);
            if (std::fabs(denom) < 1.0e-4f) {
                denom = (denom >= 0.0f) ? 1.0e-4f : -1.0e-4f;
            }
            float alpha = dS_wn / denom;
            float vlen = std::sqrt(Dot3(dv, dv));
            t_ray = std::fabs(1.0f - alpha) * vlen;
            qw = Add3(sp, Mul3(dv, alpha));
        }
        Vec3 dq = Sub3(qw, w_pos);
        float u_w = 0.5f + Dot3(dq, Wr) * cb.c[8][3];
        float v_w = 0.5f + Dot3(dq, Wd) * cb.c[9][3];
        constexpr float kInvSpan = 1.0f / 1.08f;
        float x_s = ((u_w + 0.04f) * kInvSpan) * cb.c[0][0];
        float y_s = ((v_w + 0.04f) * kInvSpan) * cb.c[0][1];
        float prox = Clamp01(1.0f - t_ray * cb.c[7][3]);
        float z_front = std::fabs(Dot3(Sub3(P, w_pos), Wn));
        return {x_s, y_s, prox, z_front};
    } else {
        Vec3 cam{cb.c[7][0], cb.c[7][1], cb.c[7][2]};
        Vec3 r_v{cb.c[8][0], cb.c[8][1], cb.c[8][2]};
        Vec3 d_v{cb.c[9][0], cb.c[9][1], cb.c[9][2]};
        Vec3 f_v{cb.c[10][0], cb.c[10][1], cb.c[10][2]};
        Vec3 dv = Sub3(P, cam);
        float zc = std::max(4.0f, Dot3(dv, f_v));
        float inv_z = 1.0f / zc;
        float x_s = 0.5f * cb.c[0][0] + Dot3(dv, r_v) * cb.c[8][3] * inv_z;
        float y_s = 0.5f * cb.c[0][1] + Dot3(dv, d_v) * cb.c[9][3] * inv_z;
        float prox = Clamp01(1.0f - zc * cb.c[7][3]);
        return {x_s, y_s, prox, zc};
    }
}

void RasterizeSubTriCpu(uint32_t* prox_buf, int32_t* row_min, int32_t* row_max,
                        int32_t w, int32_t h,
                        float x0, float y0, float p0,
                        float x1, float y1, float p1,
                        float x2, float y2, float p2,
                        bool allow_wire_splat, uint32_t is_vrm_u, int32_t splat_cap) {
    float minx = std::min({x0, x1, x2});
    float maxx = std::max({x0, x1, x2});
    float miny = std::min({y0, y1, y2});
    float maxy = std::max({y0, y1, y2});
    if (maxx < 0.0f || minx >= static_cast<float>(w) || maxy < 0.0f || miny >= static_cast<float>(h)) {
        return;
    }

    int32_t ix0 = std::max<int32_t>(0, static_cast<int32_t>(std::floor(minx)));
    int32_t ix1 = std::min<int32_t>(w - 1, static_cast<int32_t>(std::floor(maxx)));
    int32_t iy0 = std::max<int32_t>(0, static_cast<int32_t>(std::floor(miny)));
    int32_t iy1 = std::min<int32_t>(h - 1, static_cast<int32_t>(std::floor(maxy)));
    if (ix0 > ix1 || iy0 > iy1) return;

    float area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0);
    float span_max = std::max(maxx - minx, maxy - miny);

    if (allow_wire_splat && std::fabs(area) < 1.35f * std::max(span_max, 0.25f)) {
        float ax = x0, ay = y0, ap = p0;
        float bx = x1, by = y1, bp = p1;
        float d01 = std::fabs(x1 - x0) + std::fabs(y1 - y0);
        float d12 = std::fabs(x2 - x1) + std::fabs(y2 - y1);
        float d20 = std::fabs(x0 - x2) + std::fabs(y0 - y2);
        if (d12 >= d01 && d12 >= d20) {
            ax = x1; ay = y1; ap = p1; bx = x2; by = y2; bp = p2;
        } else if (d20 >= d01) {
            ax = x2; ay = y2; ap = p2; bx = x0; by = y0; bp = p0;
        }
        int32_t cap_i = std::max<int32_t>(1, splat_cap);
        int32_t req_i = std::max<int32_t>(1, static_cast<int32_t>(std::ceil(std::max(std::fabs(bx - ax), std::fabs(by - ay)))));
        int32_t n_step = std::min(cap_i, req_i);
        float inv_ns = 1.0f / static_cast<float>(n_step);
        float stx = (bx - ax) * inv_ns;
        float sty = (by - ay) * inv_ns;
        float stp = (bp - ap) * inv_ns;
        float sx = ax, sy = ay, sp_v = ap;
        for (int32_t s = 0; s <= n_step; ++s) {
            int32_t px_i = static_cast<int32_t>(std::floor(sx));
            int32_t py_i = static_cast<int32_t>(std::floor(sy));
            if (px_i >= 0 && px_i < w && py_i >= 0 && py_i < h) {
                uint32_t pv = PackProxVal(sp_v, is_vrm_u);
                int32_t idx = py_i * w + px_i;
                if (pv > prox_buf[idx]) {
                    prox_buf[idx] = pv;
                    if (px_i < row_min[py_i]) row_min[py_i] = px_i;
                    if (px_i > row_max[py_i]) row_max[py_i] = px_i;
                }
            }
            sx += stx; sy += sty; sp_v += stp;
        }
        if (std::fabs(area) < 1.0e-5f) return;
    }

    if (std::fabs(area) < 1.0e-5f) return;
    if (area < 0.0f) {
        std::swap(x1, x2);
        std::swap(y1, y2);
        std::swap(p1, p2);
        area = -area;
    }

    float inv_a = 1.0f / area;
    float dx12 = x1 - x2, dy12 = y1 - y2;
    float dx20 = x2 - x0, dy20 = y2 - y0;
    float dx01 = x0 - x1, dy01 = y0 - y1;
    float eps = allow_wire_splat
        ? (-0.02f * std::max({std::fabs(dx12) + std::fabs(dy12), std::fabs(dx20) + std::fabs(dy20), 1.0f}))
        : (-1.0e-5f);

    for (int32_t py = iy0; py <= iy1; ++py) {
        float cy = static_cast<float>(py) + 0.5f;
        int32_t px0 = ix0, px1 = ix1;

        float rhs0 = eps + (cy - y1) * dx12 - (0.5f - x1) * dy12;
        if (dy12 > 1.0e-9f)       px0 = std::max(px0, static_cast<int32_t>(std::ceil(rhs0 / dy12 - 1.0e-6f)));
        else if (dy12 < -1.0e-9f) px1 = std::min(px1, static_cast<int32_t>(std::floor(rhs0 / dy12 + 1.0e-6f)));
        else if (rhs0 > 0.0f)     px0 = px1 + 1;

        if (px0 <= px1) {
            float rhs1 = eps + (cy - y2) * dx20 - (0.5f - x2) * dy20;
            if (dy20 > 1.0e-9f)       px0 = std::max(px0, static_cast<int32_t>(std::ceil(rhs1 / dy20 - 1.0e-6f)));
            else if (dy20 < -1.0e-9f) px1 = std::min(px1, static_cast<int32_t>(std::floor(rhs1 / dy20 + 1.0e-6f)));
            else if (rhs1 > 0.0f)     px0 = px1 + 1;
        }
        if (px0 <= px1) {
            float rhs2 = eps + (cy - y0) * dx01 - (0.5f - x0) * dy01;
            if (dy01 > 1.0e-9f)       px0 = std::max(px0, static_cast<int32_t>(std::ceil(rhs2 / dy01 - 1.0e-6f)));
            else if (dy01 < -1.0e-9f) px1 = std::min(px1, static_cast<int32_t>(std::floor(rhs2 / dy01 + 1.0e-6f)));
            else if (rhs2 > 0.0f)     px0 = px1 + 1;
        }

        if (px0 <= px1) {
            float cx = static_cast<float>(px0) + 0.5f;
            float w0 = (cx - x1) * dy12 - (cy - y1) * dx12;
            float w1 = (cx - x2) * dy20 - (cy - y2) * dx20;
            float w2 = (cx - x0) * dy01 - (cy - y0) * dx01;
            int32_t row_off = py * w;
            int32_t rmin = row_min[py];
            int32_t rmax = row_max[py];
            for (int32_t px = px0; px <= px1; ++px) {
                if (w0 >= eps && w1 >= eps && w2 >= eps) {
                    float b0 = w0 * inv_a;
                    float b1 = w1 * inv_a;
                    float pr = b0 * p0 + b1 * p1 + (1.0f - b0 - b1) * p2;
                    uint32_t pv = PackProxVal(pr, is_vrm_u);
                    int32_t idx = row_off + px;
                    if (pv > prox_buf[idx]) {
                        prox_buf[idx] = pv;
                        if (px < rmin) rmin = px;
                        if (px > rmax) rmax = px;
                    }
                }
                w0 += dy12;
                w1 += dy20;
                w2 += dy01;
            }
            row_min[py] = rmin;
            row_max[py] = rmax;
        }
    }
}

int32_t ClipPolyPlaneCpu(const Vec3* in_pts, int32_t n_in, Vec3 pn, float poff, Vec3* out_pts) {
    if (n_in < 3) return 0;
    int32_t n_out = 0;
    Vec3 prev = in_pts[n_in - 1];
    float prev_d = Dot3(prev, pn) - poff;
    for (int32_t i = 0; i < n_in; ++i) {
        Vec3 cur = in_pts[i];
        float cur_d = Dot3(cur, pn) - poff;
        if (cur_d >= 0.0f) {
            if (prev_d < 0.0f && n_out < 6) {
                float t = prev_d / (prev_d - cur_d);
                out_pts[n_out++] = Add3(prev, Mul3(Sub3(cur, prev), t));
            }
            if (n_out < 6) out_pts[n_out++] = cur;
        } else if (prev_d >= 0.0f && n_out < 6) {
            float t = prev_d / (prev_d - cur_d);
            out_pts[n_out++] = Add3(prev, Mul3(Sub3(cur, prev), t));
        }
        prev = cur;
        prev_d = cur_d;
    }
    return n_out;
}

struct MeshSlot {
    int32_t mesh_id = 0;
    std::string slot_key;
    std::string rev_sig;
    int32_t nv = 0;
    int32_t nt = 0;
    uint64_t last_use = 0;
    std::vector<float> verts4;     // nv * 4 floats
    std::vector<uint32_t> tris4;   // nt * 4 uints

#ifdef _WIN32
    ID3D11Device3* gpu_dev = nullptr;
    ID3D11Buffer* vb = nullptr;
    ID3D11ShaderResourceView* vb_srv = nullptr;
    ID3D11Buffer* ib = nullptr;
    ID3D11ShaderResourceView* ib_srv = nullptr;

    void ReleaseGpu() {
        if (vb_srv) { vb_srv->Release(); vb_srv = nullptr; }
        if (vb)     { vb->Release();     vb = nullptr; }
        if (ib_srv) { ib_srv->Release(); ib_srv = nullptr; }
        if (ib)     { ib->Release();     ib = nullptr; }
        gpu_dev = nullptr;
    }
#else
    void ReleaseGpu() {}
#endif
};

#ifdef _WIN32
template <typename T>
inline void SafeReleaseCom(T*& p) {
    if (p) {
        p->Release();
        p = nullptr;
    }
}

// Standard COM IID_ID3D10Multithread: {9B7E4E00-342C-4106-A19F-4F2704F689F0}
static const GUID kIID_ID3D10Multithread = {
    0x9b7e4e00u, 0x342cu, 0x4106u, {0xa1u, 0x9fu, 0x4fu, 0x27u, 0x04u, 0xf6u, 0x89u, 0xf0u}
};

using PFN_Vl3dD3DCompile = HRESULT(WINAPI*)(
    LPCVOID pSrcData, SIZE_T SrcDataSize, LPCSTR pSourceName,
    const D3D_SHADER_MACRO* pDefines, ID3DInclude* pInclude,
    LPCSTR pEntrypoint, LPCSTR pTarget, UINT Flags1, UINT Flags2,
    ID3DBlob** ppCode, ID3DBlob** ppErrorMsgs);

struct ScopedCsContextGuard {
    ID3D11DeviceContext* ctx = nullptr;
    ID3D10Multithread* mt = nullptr;
    ID3D11ComputeShader* prev_cs = nullptr;
    ID3D11ClassInstance* prev_inst[16]{};
    UINT prev_inst_cnt = 16;
    ID3D11Buffer* prev_cb[1]{};
    ID3D11ShaderResourceView* prev_srvs[2]{};
    ID3D11UnorderedAccessView* prev_uavs[4]{};

    ScopedCsContextGuard(ID3D11DeviceContext* c, ID3D10Multithread* m) : ctx(c), mt(m) {
        if (mt) mt->Enter();
        if (ctx) {
            ctx->CSGetShader(&prev_cs, prev_inst, &prev_inst_cnt);
            ctx->CSGetConstantBuffers(0, 1, prev_cb);
            ctx->CSGetShaderResources(0, 2, prev_srvs);
            ctx->CSGetUnorderedAccessViews(0, 4, prev_uavs);
        }
    }

    ~ScopedCsContextGuard() {
        if (ctx) {
            ID3D11UnorderedAccessView* null_uavs[4] = {nullptr, nullptr, nullptr, nullptr};
            ID3D11ShaderResourceView* null_srvs[2] = {nullptr, nullptr};
            ctx->CSSetUnorderedAccessViews(0, 4, null_uavs, nullptr);
            ctx->CSSetShaderResources(0, 2, null_srvs);
            ctx->CSSetShader(prev_cs, prev_inst, prev_inst_cnt);
            ctx->CSSetConstantBuffers(0, 1, prev_cb);
            ctx->CSSetShaderResources(0, 2, prev_srvs);
            ctx->CSSetUnorderedAccessViews(0, 4, prev_uavs, nullptr);
            for (UINT i = 0; i < prev_inst_cnt && i < 16; ++i) SafeReleaseCom(prev_inst[i]);
            SafeReleaseCom(prev_cs);
            SafeReleaseCom(prev_cb[0]);
            SafeReleaseCom(prev_srvs[0]);
            SafeReleaseCom(prev_srvs[1]);
            for (int i = 0; i < 4; ++i) SafeReleaseCom(prev_uavs[i]);
        }
        if (mt) mt->Leave();
    }
};
#endif

struct RastManager {
    std::mutex mtx;
    bool force_cpu = false;
    int32_t next_mesh_id = 1;
    uint64_t use_clock = 0;
    std::vector<MeshSlot> slots;
    std::string last_error;

    // Persistent CPU fallback scratch buffers (zero per-frame heap allocation)
    std::vector<uint32_t> cpu_prox_buf;
    std::vector<int32_t> cpu_row_min;
    std::vector<int32_t> cpu_row_max;

#ifdef _WIN32
    ID3D11Device3* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    ID3D10Multithread* mt = nullptr;
    ID3D11ComputeShader* cs_clear = nullptr;
    ID3D11ComputeShader* cs_rast_small = nullptr;
    ID3D11ComputeShader* cs_rast_large = nullptr;
    ID3D11ComputeShader* cs_encode = nullptr;
    ID3D11Buffer* cb_buf = nullptr;
    ID3D11Buffer* meta_buf = nullptr;
    ID3D11UnorderedAccessView* meta_uav = nullptr;
    ID3D11Buffer* staging_meta = nullptr;
    ID3D11Buffer* large_buf = nullptr;
    ID3D11UnorderedAccessView* large_uav = nullptr;
    ID3D11Buffer* prox_buf = nullptr;
    ID3D11UnorderedAccessView* prox_uav = nullptr;
    ID3D11Buffer* rgba_buf = nullptr;
    ID3D11UnorderedAccessView* rgba_uav = nullptr;
    ID3D11Buffer* staging_rgba = nullptr;
    uint32_t pixel_cap = 0;
    bool shaders_ready = false;
    bool init_failed_for_dev = false;
    PFN_Vl3dD3DCompile fn_compile = nullptr;

    void ReleaseDeviceObjects() {
        for (auto& s : slots) s.ReleaseGpu();
        SafeReleaseCom(staging_rgba);
        SafeReleaseCom(rgba_uav);
        SafeReleaseCom(rgba_buf);
        SafeReleaseCom(prox_uav);
        SafeReleaseCom(prox_buf);
        SafeReleaseCom(large_uav);
        SafeReleaseCom(large_buf);
        SafeReleaseCom(staging_meta);
        SafeReleaseCom(meta_uav);
        SafeReleaseCom(meta_buf);
        SafeReleaseCom(cb_buf);
        SafeReleaseCom(cs_encode);
        SafeReleaseCom(cs_rast_large);
        SafeReleaseCom(cs_rast_small);
        SafeReleaseCom(cs_clear);
        SafeReleaseCom(mt);
        SafeReleaseCom(ctx);
        dev = nullptr;
        pixel_cap = 0;
        shaders_ready = false;
        init_failed_for_dev = false;
    }

    bool CompileCs(const char* entry_name, ID3D11ComputeShader** out_cs) {
        if (!fn_compile || !dev) return false;
        ID3DBlob* code = nullptr;
        ID3DBlob* err = nullptr;
        HRESULT hr = fn_compile(
            kVl3dRastHlslCs,
            std::strlen(kVl3dRastHlslCs),
            "vl3d_bridge_cs.hlsl",
            nullptr,
            nullptr,
            entry_name,
            "cs_5_0",
            (1u << 15),  // D3DCOMPILE_OPTIMIZATION_LEVEL3
            0u,
            &code,
            &err);
        if (FAILED(hr) || !code) {
            if (err && err->GetBufferPointer()) {
                last_error.assign(static_cast<const char*>(err->GetBufferPointer()), err->GetBufferSize());
            } else {
                last_error = std::string("D3DCompile failed on ") + entry_name;
            }
            SafeReleaseCom(err);
            SafeReleaseCom(code);
            return false;
        }
        SafeReleaseCom(err);
        hr = dev->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, out_cs);
        SafeReleaseCom(code);
        if (FAILED(hr) || !*out_cs) {
            last_error = std::string("CreateComputeShader failed on ") + entry_name;
            return false;
        }
        return true;
    }

    bool CreateStructuredUavBuffer(UINT elem_stride, UINT elem_count,
                                   ID3D11Buffer** out_buf, ID3D11UnorderedAccessView** out_uav) {
        D3D11_BUFFER_DESC bd{};
        bd.ByteWidth = elem_stride * elem_count;
        bd.Usage = D3D11_USAGE_DEFAULT;
        bd.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
        bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
        bd.StructureByteStride = elem_stride;
        if (FAILED(dev->CreateBuffer(&bd, nullptr, out_buf)) || !*out_buf) {
            return false;
        }
        D3D11_UNORDERED_ACCESS_VIEW_DESC ud{};
        ud.Format = DXGI_FORMAT_UNKNOWN;
        ud.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
        ud.Buffer.FirstElement = 0;
        ud.Buffer.NumElements = elem_count;
        ud.Buffer.Flags = 0;
        if (FAILED(dev->CreateUnorderedAccessView(*out_buf, &ud, out_uav)) || !*out_uav) {
            SafeReleaseCom(*out_buf);
            return false;
        }
        return true;
    }

    bool EnsureDeviceReady(ID3D11Device3* host_dev) {
        if (!host_dev) return false;
        if (host_dev != dev) {
            ReleaseDeviceObjects();
            dev = host_dev;
        }
        if (FAILED(dev->GetDeviceRemovedReason())) {
            last_error = "D3D11 device removed";
            ReleaseDeviceObjects();
            return false;
        }
        if (shaders_ready) return true;
        if (init_failed_for_dev) return false;

        if (!fn_compile) {
            HMODULE hmod = ::GetModuleHandleW(L"d3dcompiler_47.dll");
            if (!hmod) hmod = ::LoadLibraryW(L"d3dcompiler_47.dll");
            if (hmod) {
                fn_compile = reinterpret_cast<PFN_Vl3dD3DCompile>(
                    reinterpret_cast<void*>(::GetProcAddress(hmod, "D3DCompile")));
            }
            if (!fn_compile) {
                last_error = "d3dcompiler_47.dll!D3DCompile unavailable";
                init_failed_for_dev = true;
                return false;
            }
        }

        dev->GetImmediateContext(&ctx);
        if (!ctx) {
            last_error = "GetImmediateContext returned nullptr";
            init_failed_for_dev = true;
            return false;
        }

        void* mt_ptr = nullptr;
        if (SUCCEEDED(ctx->QueryInterface(kIID_ID3D10Multithread, &mt_ptr)) && mt_ptr) {
            mt = static_cast<ID3D10Multithread*>(mt_ptr);
            mt->SetMultithreadProtected(TRUE);
        } else if (SUCCEEDED(dev->QueryInterface(kIID_ID3D10Multithread, &mt_ptr)) && mt_ptr) {
            mt = static_cast<ID3D10Multithread*>(mt_ptr);
            mt->SetMultithreadProtected(TRUE);
        }

        if (!CompileCs("cs_clear", &cs_clear) ||
            !CompileCs("cs_project_and_rast_small", &cs_rast_small) ||
            !CompileCs("cs_rast_large_tiles", &cs_rast_large) ||
            !CompileCs("cs_encode_rgba", &cs_encode)) {
            init_failed_for_dev = true;
            return false;
        }

        D3D11_BUFFER_DESC cbd{};
        cbd.ByteWidth = sizeof(RastCBData);
        cbd.Usage = D3D11_USAGE_DEFAULT;
        cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        if (FAILED(dev->CreateBuffer(&cbd, nullptr, &cb_buf)) || !cb_buf) {
            last_error = "CreateBuffer(RastCB) failed";
            init_failed_for_dev = true;
            return false;
        }

        if (!CreateStructuredUavBuffer(4u, 4u, &meta_buf, &meta_uav) ||
            !CreateStructuredUavBuffer(16u, kMaxLargeTris * 4u, &large_buf, &large_uav)) {
            last_error = "CreateStructuredUavBuffer(meta/large) failed";
            init_failed_for_dev = true;
            return false;
        }

        D3D11_BUFFER_DESC smd{};
        smd.ByteWidth = 16u;
        smd.Usage = D3D11_USAGE_STAGING;
        smd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        if (FAILED(dev->CreateBuffer(&smd, nullptr, &staging_meta)) || !staging_meta) {
            last_error = "CreateBuffer(staging_meta) failed";
            init_failed_for_dev = true;
            return false;
        }

        shaders_ready = true;
        last_error.clear();
        return true;
    }

    bool EnsurePixelCapacity(uint32_t req_pixels) {
        if (req_pixels <= pixel_cap && prox_uav && rgba_uav && staging_rgba) return true;
        SafeReleaseCom(staging_rgba);
        SafeReleaseCom(rgba_uav);
        SafeReleaseCom(rgba_buf);
        SafeReleaseCom(prox_uav);
        SafeReleaseCom(prox_buf);
        pixel_cap = 0;

        uint32_t cap = std::max<uint32_t>(req_pixels, 1024u * 1024u);
        if (!CreateStructuredUavBuffer(4u, cap, &prox_buf, &prox_uav) ||
            !CreateStructuredUavBuffer(4u, cap, &rgba_buf, &rgba_uav)) {
            last_error = "CreateStructuredUavBuffer(prox/rgba) failed";
            return false;
        }
        D3D11_BUFFER_DESC sbd{};
        sbd.ByteWidth = cap * 4u;
        sbd.Usage = D3D11_USAGE_STAGING;
        sbd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        if (FAILED(dev->CreateBuffer(&sbd, nullptr, &staging_rgba)) || !staging_rgba) {
            last_error = "CreateBuffer(staging_rgba) failed";
            return false;
        }
        pixel_cap = cap;
        return true;
    }

    bool EnsureMeshSrv(MeshSlot& slot) {
        if (slot.gpu_dev == dev && slot.vb_srv && slot.ib_srv) return true;
        slot.ReleaseGpu();
        if (!dev || slot.nv <= 0 || slot.nt <= 0) return false;

        D3D11_BUFFER_DESC vbd{};
        vbd.ByteWidth = static_cast<UINT>(slot.nv) * 16u;
        vbd.Usage = D3D11_USAGE_IMMUTABLE;
        vbd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        vbd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
        vbd.StructureByteStride = 16u;
        D3D11_SUBRESOURCE_DATA vsd{};
        vsd.pSysMem = slot.verts4.data();
        if (FAILED(dev->CreateBuffer(&vbd, &vsd, &slot.vb)) || !slot.vb) return false;

        D3D11_SHADER_RESOURCE_VIEW_DESC vsrv{};
        vsrv.Format = DXGI_FORMAT_UNKNOWN;
        vsrv.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        vsrv.Buffer.FirstElement = 0;
        vsrv.Buffer.NumElements = static_cast<UINT>(slot.nv);
        if (FAILED(dev->CreateShaderResourceView(slot.vb, &vsrv, &slot.vb_srv)) || !slot.vb_srv) {
            slot.ReleaseGpu();
            return false;
        }

        D3D11_BUFFER_DESC ibd{};
        ibd.ByteWidth = static_cast<UINT>(slot.nt) * 16u;
        ibd.Usage = D3D11_USAGE_IMMUTABLE;
        ibd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        ibd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
        ibd.StructureByteStride = 16u;
        D3D11_SUBRESOURCE_DATA isd{};
        isd.pSysMem = slot.tris4.data();
        if (FAILED(dev->CreateBuffer(&ibd, &isd, &slot.ib)) || !slot.ib) {
            slot.ReleaseGpu();
            return false;
        }

        D3D11_SHADER_RESOURCE_VIEW_DESC isrv{};
        isrv.Format = DXGI_FORMAT_UNKNOWN;
        isrv.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        isrv.Buffer.FirstElement = 0;
        isrv.Buffer.NumElements = static_cast<UINT>(slot.nt);
        if (FAILED(dev->CreateShaderResourceView(slot.ib, &isrv, &slot.ib_srv)) || !slot.ib_srv) {
            slot.ReleaseGpu();
            return false;
        }

        slot.gpu_dev = dev;
        return true;
    }
#else
    void ReleaseDeviceObjects() {}
#endif

    ~RastManager() {
        ReleaseDeviceObjects();
    }
};

RastManager& GetRastMgr() {
    static RastManager s_mgr;
    return s_mgr;
}

MeshSlot* FindMeshSlotByIdLocked(RastManager& mgr, int32_t mesh_id) {
    if (mesh_id <= 0) return nullptr;
    for (auto& s : mgr.slots) {
        if (s.mesh_id == mesh_id) {
            s.last_use = ++mgr.use_clock;
            return &s;
        }
    }
    return nullptr;
}

template <typename BuildCbFn>
RastOutput ExecuteCpuPassLocked(RastManager& mgr, void* dst_rgba, int32_t w, int32_t h,
                                bool is_camdepth, int32_t splat_cap,
                                const std::vector<RastModelInstance>& instances,
                                BuildCbFn&& build_cb) {
    RastOutput out{};
    out.backend = "CPP_CPU";
    const size_t npix = static_cast<size_t>(w) * static_cast<size_t>(h);
    if (mgr.cpu_prox_buf.size() < npix) mgr.cpu_prox_buf.resize(npix);
    if (mgr.cpu_row_min.size() < static_cast<size_t>(h)) mgr.cpu_row_min.resize(static_cast<size_t>(h));
    if (mgr.cpu_row_max.size() < static_cast<size_t>(h)) mgr.cpu_row_max.resize(static_cast<size_t>(h));

    std::fill_n(mgr.cpu_prox_buf.data(), npix, 0u);
    std::fill_n(mgr.cpu_row_min.data(), h, w);
    std::fill_n(mgr.cpu_row_max.data(), h, -1);

    int32_t total_tris = 0;
    const bool allow_wire = !is_camdepth;

    for (const auto& inst : instances) {
        MeshSlot* slot = FindMeshSlotByIdLocked(mgr, inst.mesh_id);
        if (!slot || slot->nv <= 0 || slot->nt <= 0) continue;
        const RastCBData cb = build_cb(inst, static_cast<uint32_t>(slot->nt));
        const Vec3 o{cb.c[1][0], cb.c[1][1], cb.c[1][2]};
        const Vec3 r0{cb.c[2][0], cb.c[2][1], cb.c[2][2]};
        const Vec3 d0{cb.c[3][0], cb.c[3][1], cb.c[3][2]};
        const Vec3 n0{cb.c[4][0], cb.c[4][1], cb.c[4][2]};
        const Vec3 p1_n{cb.c[5][0], cb.c[5][1], cb.c[5][2]};
        const float p1_off = cb.c[5][3];
        const bool use_p2 = (cb.c[2][3] > 0.5f);
        const Vec3 p2_n{cb.c[6][0], cb.c[6][1], cb.c[6][2]};
        const float p2_off = cb.c[6][3];
        const uint32_t is_vrm_u = inst.is_vrm ? 1u : 0u;
        const float L_tot = cb.c[4][3];

        const float* v4 = slot->verts4.data();
        const uint32_t* t4 = slot->tris4.data();
        const int32_t nt = slot->nt;

        for (int32_t ti = 0; ti < nt; ++ti) {
            const uint32_t ia = t4[ti * 4 + 0];
            const uint32_t ib = t4[ti * 4 + 1];
            const uint32_t ic = t4[ti * 4 + 2];
            auto xform = [&](uint32_t idx) -> Vec3 {
                const float lx = v4[idx * 4 + 0];
                const float ly = v4[idx * 4 + 1];
                const float lz = v4[idx * 4 + 2];
                return {
                    o.x + r0.x * lx + d0.x * ly + n0.x * lz,
                    o.y + r0.y * lx + d0.y * ly + n0.y * lz,
                    o.z + r0.z * lx + d0.z * ly + n0.z * lz,
                };
            };
            Vec3 wa = xform(ia);
            Vec3 wb = xform(ib);
            Vec3 wc = xform(ic);

            float d1a = Dot3(wa, p1_n) - p1_off;
            float d1b = Dot3(wb, p1_n) - p1_off;
            float d1c = Dot3(wc, p1_n) - p1_off;
            if (d1a < 0.0f && d1b < 0.0f && d1c < 0.0f) continue;

            float d2a = use_p2 ? (Dot3(wa, p2_n) - p2_off) : 1.0f;
            float d2b = use_p2 ? (Dot3(wb, p2_n) - p2_off) : 1.0f;
            float d2c = use_p2 ? (Dot3(wc, p2_n) - p2_off) : 1.0f;
            if (d2a < 0.0f && d2b < 0.0f && d2c < 0.0f) continue;

            if (d1a >= 0.0f && d1b >= 0.0f && d1c >= 0.0f &&
                d2a >= 0.0f && d2b >= 0.0f && d2c >= 0.0f) {
                Vec4 pa = ProjectWorldPtCpu(cb, wa);
                Vec4 pb = ProjectWorldPtCpu(cb, wb);
                Vec4 pc = ProjectWorldPtCpu(cb, wc);
                if (allow_wire && pa.w > L_tot && pb.w > L_tot && pc.w > L_tot) continue;
                RasterizeSubTriCpu(mgr.cpu_prox_buf.data(), mgr.cpu_row_min.data(), mgr.cpu_row_max.data(),
                                   w, h, pa.x, pa.y, pa.z, pb.x, pb.y, pb.z, pc.x, pc.y, pc.z,
                                   allow_wire, is_vrm_u, splat_cap);
                total_tris += 1;
            } else {
                Vec3 poly0[3] = {wa, wb, wc};
                Vec3 poly1[6];
                Vec3 poly2[6];
                int32_t n1 = ClipPolyPlaneCpu(poly0, 3, p1_n, p1_off, poly1);
                if (n1 < 3) continue;
                const Vec3* final_poly = poly1;
                int32_t n2 = n1;
                if (use_p2) {
                    n2 = ClipPolyPlaneCpu(poly1, n1, p2_n, p2_off, poly2);
                    final_poly = poly2;
                    if (n2 < 3) continue;
                }
                Vec4 q0 = ProjectWorldPtCpu(cb, final_poly[0]);
                Vec4 q1 = ProjectWorldPtCpu(cb, final_poly[1]);
                for (int32_t k = 1; k < n2 - 1; ++k) {
                    Vec4 q2 = ProjectWorldPtCpu(cb, final_poly[k + 1]);
                    RasterizeSubTriCpu(mgr.cpu_prox_buf.data(), mgr.cpu_row_min.data(), mgr.cpu_row_max.data(),
                                       w, h, q0.x, q0.y, q0.z, q1.x, q1.y, q1.z, q2.x, q2.y, q2.z,
                                       allow_wire, is_vrm_u, splat_cap);
                    total_tris += 1;
                    q1 = q2;
                }
            }
        }
    }

    auto* rgba = static_cast<uint8_t*>(dst_rgba);
    std::memset(rgba, 0, npix * 4u);
    bool any_px = false;
    for (int32_t y = 0; y < h; ++y) {
        int32_t x0 = mgr.cpu_row_min[y];
        int32_t x1 = mgr.cpu_row_max[y];
        if (x0 <= x1) {
            any_px = true;
            const size_t base = static_cast<size_t>(y) * static_cast<size_t>(w);
            for (int32_t x = x0; x <= x1; ++x) {
                uint32_t val = mgr.cpu_prox_buf[base + static_cast<size_t>(x)];
                if (val != 0u) {
                    float pr = Clamp01(static_cast<float>((val >> 1u) - 1u) * (1.0f / 16777214.0f));
                    float code = is_camdepth
                        ? (((val & 1u) != 0u) ? (pr * 0.48f + 0.51f) : (pr * 0.48f + 0.01f))
                        : pr;
                    float rf = Clamp01(code * 3.0f - 2.0f);
                    float gf = Clamp01(code * 3.0f - 1.0f);
                    float bf = Clamp01(code * 3.0f);
                    size_t di = (base + static_cast<size_t>(x)) * 4u;
                    rgba[di + 0] = static_cast<uint8_t>(rf * 255.0f + 0.5f);
                    rgba[di + 1] = static_cast<uint8_t>(gf * 255.0f + 0.5f);
                    rgba[di + 2] = static_cast<uint8_t>(bf * 255.0f + 0.5f);
                    rgba[di + 3] = 255u;
                }
            }
        }
    }

    out.ok = true;
    out.any_pixel = any_px;
    out.tris_drawn = total_tris;
    return out;
}

#ifdef _WIN32
template <typename BuildCbFn>
bool ExecuteD3D11PassLocked(RastManager& mgr, ID3D11Device3* host_dev,
                            void* dst_rgba, int32_t w, int32_t h,
                            const std::vector<RastModelInstance>& instances,
                            BuildCbFn&& build_cb, RastOutput* out_res) {
    if (!mgr.EnsureDeviceReady(host_dev)) return false;
    const uint32_t npix = static_cast<uint32_t>(w) * static_cast<uint32_t>(h);
    if (!mgr.EnsurePixelCapacity(npix)) return false;

    std::vector<std::pair<MeshSlot*, RastCBData>> draw_list;
    draw_list.reserve(instances.size());
    int32_t total_tris = 0;
    for (const auto& inst : instances) {
        MeshSlot* slot = FindMeshSlotByIdLocked(mgr, inst.mesh_id);
        if (!slot || slot->nv <= 0 || slot->nt <= 0) continue;
        if (!mgr.EnsureMeshSrv(*slot)) return false;
        draw_list.emplace_back(slot, build_cb(inst, static_cast<uint32_t>(slot->nt)));
        total_tris += slot->nt;
    }

    if (draw_list.empty()) {
        std::memset(dst_rgba, 0, static_cast<size_t>(npix) * 4u);
        out_res->ok = true;
        out_res->any_pixel = false;
        out_res->tris_drawn = 0;
        out_res->backend = "D3D11_CS";
        return true;
    }

    ScopedCsContextGuard guard(mgr.ctx, mgr.mt);
    ID3D11DeviceContext* ctx = mgr.ctx;

    ID3D11UnorderedAccessView* uavs[4] = {
        mgr.prox_uav, mgr.rgba_uav, mgr.meta_uav, mgr.large_uav
    };
    ID3D11Buffer* cbs[1] = {mgr.cb_buf};
    ctx->CSSetUnorderedAccessViews(0, 4, uavs, nullptr);
    ctx->CSSetConstantBuffers(0, 1, cbs);

    // Pass 1: Clear g_prox_buf + g_meta_buf
    RastCBData cb0 = draw_list.front().second;
    ctx->UpdateSubresource(mgr.cb_buf, 0, nullptr, &cb0, 0, 0);
    ctx->CSSetShader(mgr.cs_clear, nullptr, 0);
    ctx->Dispatch((npix + 255u) / 256u, 1u, 1u);

    // Pass 2: Per-model local->world transform, plane clip, wire-splat & small-triangle rasterization
    ctx->CSSetShader(mgr.cs_rast_small, nullptr, 0);
    for (const auto& item : draw_list) {
        MeshSlot* slot = item.first;
        const RastCBData& cb = item.second;
        ctx->UpdateSubresource(mgr.cb_buf, 0, nullptr, &cb, 0, 0);
        ID3D11ShaderResourceView* srvs[2] = {slot->vb_srv, slot->ib_srv};
        ctx->CSSetShaderResources(0, 2, srvs);
        ctx->Dispatch((static_cast<UINT>(slot->nt) + 63u) / 64u, 1u, 1u);
    }

    // Pass 3: Parallel 8x8 tile fill for any deferred giant triangles (instant exit if meta[0] == 0)
    ctx->CSSetShader(mgr.cs_rast_large, nullptr, 0);
    ctx->Dispatch((static_cast<UINT>(w) + 7u) / 8u, (static_cast<UINT>(h) + 7u) / 8u, 1u);

    // Pass 4: Commutative 3-band RGBA8 encoding
    ctx->CSSetShader(mgr.cs_encode, nullptr, 0);
    ctx->Dispatch((npix + 255u) / 256u, 1u, 1u);

    // Issue both DMA copies before first Map() so PCIe transfers pipeline cleanly
    ctx->CopyResource(mgr.staging_meta, mgr.meta_buf);
    D3D11_BOX box{0u, 0u, 0u, npix * 4u, 1u, 1u};
    ctx->CopySubresourceRegion(mgr.staging_rgba, 0u, 0u, 0u, 0u, mgr.rgba_buf, 0u, &box);

    D3D11_MAPPED_SUBRESOURCE map_meta{};
    if (FAILED(ctx->Map(mgr.staging_meta, 0, D3D11_MAP_READ, 0, &map_meta)) || !map_meta.pData) {
        mgr.last_error = "Map(staging_meta) failed";
        return false;
    }
    const uint32_t hit_pixels = static_cast<const uint32_t*>(map_meta.pData)[1];
    ctx->Unmap(mgr.staging_meta, 0);

    if (hit_pixels == 0u) {
        std::memset(dst_rgba, 0, static_cast<size_t>(npix) * 4u);
        out_res->ok = true;
        out_res->any_pixel = false;
        out_res->tris_drawn = total_tris;
        out_res->backend = "D3D11_CS";
        return true;
    }

    D3D11_MAPPED_SUBRESOURCE map_rgba{};
    if (FAILED(ctx->Map(mgr.staging_rgba, 0, D3D11_MAP_READ, 0, &map_rgba)) || !map_rgba.pData) {
        mgr.last_error = "Map(staging_rgba) failed";
        return false;
    }
    std::memcpy(dst_rgba, map_rgba.pData, static_cast<size_t>(npix) * 4u);
    ctx->Unmap(mgr.staging_rgba, 0);

    out_res->ok = true;
    out_res->any_pixel = true;
    out_res->tris_drawn = total_tris;
    out_res->backend = "D3D11_CS";
    return true;
}
#endif

}  // namespace

bool DecodeBase64Bytes(const char* src, size_t src_len, std::vector<uint8_t>* out) {
    if (!out) return false;
    out->clear();
    if (!src || src_len == 0) return true;

    static const std::array<int8_t, 256> kLut = [] {
        std::array<int8_t, 256> t{};
        t.fill(-1);
        for (int c = 'A'; c <= 'Z'; ++c) t[static_cast<size_t>(c)] = static_cast<int8_t>(c - 'A');
        for (int c = 'a'; c <= 'z'; ++c) t[static_cast<size_t>(c)] = static_cast<int8_t>(c - 'a' + 26);
        for (int c = '0'; c <= '9'; ++c) t[static_cast<size_t>(c)] = static_cast<int8_t>(c - '0' + 52);
        t[static_cast<size_t>('+')] = 62;
        t[static_cast<size_t>('/')] = 63;
        return t;
    }();

    out->reserve((src_len * 3u) / 4u + 4u);
    uint32_t acc = 0;
    int bits = 0;
    for (size_t i = 0; i < src_len; ++i) {
        uint8_t ch = static_cast<uint8_t>(src[i]);
        if (ch == '=') break;
        int8_t v = kLut[ch];
        if (v < 0) {
            if (ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n') continue;
            return false;
        }
        acc = (acc << 6u) | static_cast<uint32_t>(v);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out->push_back(static_cast<uint8_t>((acc >> bits) & 0xFFu));
        }
    }
    return true;
}

RastCBData BuildShadowRastCB(int32_t sh_res, const RastShadowParams& p,
                             const RastModelInstance& inst, uint32_t tri_count) {
    RastCBData cb{};
    const float res_f = static_cast<float>(sh_res);
    const float inv_2Hx = 1.0f / std::max(2.0f * p.Hx, 1.0e-4f);
    const float inv_2Hy = 1.0f / std::max(2.0f * p.Hy, 1.0e-4f);
    const float inv_L = 1.0f / std::max(p.L_tot, 1.0f);

    float pnx = 0.0f, pny = 0.0f, pnz = 1.0f, poff1 = 0.0f;
    bool use_plane2 = false;
    float p2nx = 0.0f, p2ny = 0.0f, p2nz = 0.0f, poff2 = 0.0f;

    if (p.mode_kind < 0.5f) {
        float sgn = (p.u_wn >= 0.0f) ? 1.0f : -1.0f;
        pnx = p.Wnx * sgn; pny = p.Wny * sgn; pnz = p.Wnz * sgn;
        poff1 = p.wx * pnx + p.wy * pny + p.wz * pnz + 0.5f * std::fabs(p.u_wn);
    } else if (p.mode_kind < 1.5f) {
        float sgn = (p.dS_wn >= 0.0f) ? 1.0f : -1.0f;
        pnx = p.Wnx * sgn; pny = p.Wny * sgn; pnz = p.Wnz * sgn;
        poff1 = p.wx * pnx + p.wy * pny + p.wz * pnz + 0.5f;
    } else {
        float sgn = (p.dS_wn >= 0.0f) ? -1.0f : 1.0f;
        pnx = p.Wnx * sgn; pny = p.Wny * sgn; pnz = p.Wnz * sgn;
        poff1 = p.wx * pnx + p.wy * pny + p.wz * pnz + 0.5f;
        use_plane2 = true;
        p2nx = -pnx; p2ny = -pny; p2nz = -pnz;
        poff2 = p.sx_p * p2nx + p.sy_p * p2ny + p.sz_p * p2nz + std::max(2.0f, std::fabs(p.dS_wn) * 0.02f);
    }

    cb.c[0][0] = res_f;      cb.c[0][1] = res_f;      cb.c[0][2] = 0.0f;                   cb.c[0][3] = static_cast<float>(tri_count);
    cb.c[1][0] = inst.ox;    cb.c[1][1] = inst.oy;    cb.c[1][2] = inst.oz;                cb.c[1][3] = inst.is_vrm ? 1.0f : 0.0f;
    cb.c[2][0] = inst.r0x;   cb.c[2][1] = inst.r0y;   cb.c[2][2] = inst.r0z;               cb.c[2][3] = use_plane2 ? 1.0f : 0.0f;
    cb.c[3][0] = inst.d0x;   cb.c[3][1] = inst.d0y;   cb.c[3][2] = inst.d0z;               cb.c[3][3] = p.mode_kind;
    cb.c[4][0] = inst.n0x;   cb.c[4][1] = inst.n0y;   cb.c[4][2] = inst.n0z;               cb.c[4][3] = p.L_tot;
    cb.c[5][0] = pnx;        cb.c[5][1] = pny;        cb.c[5][2] = pnz;                    cb.c[5][3] = poff1;
    cb.c[6][0] = p2nx;       cb.c[6][1] = p2ny;       cb.c[6][2] = p2nz;                   cb.c[6][3] = poff2;
    cb.c[7][0] = p.wx;       cb.c[7][1] = p.wy;       cb.c[7][2] = p.wz;                   cb.c[7][3] = inv_L;
    cb.c[8][0] = p.Wrx;      cb.c[8][1] = p.Wry;      cb.c[8][2] = p.Wrz;                  cb.c[8][3] = inv_2Hx;
    cb.c[9][0] = p.Wdx;      cb.c[9][1] = p.Wdy;      cb.c[9][2] = p.Wdz;                  cb.c[9][3] = inv_2Hy;
    cb.c[10][0] = p.Wnx;     cb.c[10][1] = p.Wny;     cb.c[10][2] = p.Wnz;                 cb.c[10][3] = (std::fabs(p.u_wn) < 1.0e-5f) ? 1.0e-5f : p.u_wn;
    cb.c[11][0] = p.sx_p;    cb.c[11][1] = p.sy_p;    cb.c[11][2] = p.sz_p;                cb.c[11][3] = p.dS_wn;
    cb.c[12][0] = static_cast<float>(std::max<int32_t>(1, p.splat_cap));
    cb.c[12][1] = static_cast<float>(kLargeBboxThreshold);
    cb.c[12][2] = static_cast<float>(kMaxLargeTris);
    cb.c[12][3] = static_cast<float>(static_cast<uint32_t>(sh_res) * static_cast<uint32_t>(sh_res));
    return cb;
}

RastCBData BuildCamDepthRastCB(int32_t cm_w, int32_t cm_h, const RastCamDepthParams& p,
                               const RastModelInstance& inst, uint32_t tri_count) {
    RastCBData cb{};
    const float wf = static_cast<float>(cm_w);
    const float hf = static_cast<float>(cm_h);
    const float scale_x = (p.focal_d / std::max(p.scr_w, 1.0f)) * wf;
    const float scale_y = (p.focal_d / std::max(p.scr_h, 1.0f)) * hf;
    const float inv_mz = 1.0f / std::max(p.max_z, 1.0f);
    const float near_off = p.cam_x * p.f_x + p.cam_y * p.f_y + p.cam_z * p.f_z + 4.0f;

    cb.c[0][0] = wf;         cb.c[0][1] = hf;         cb.c[0][2] = 1.0f;                   cb.c[0][3] = static_cast<float>(tri_count);
    cb.c[1][0] = inst.ox;    cb.c[1][1] = inst.oy;    cb.c[1][2] = inst.oz;                cb.c[1][3] = inst.is_vrm ? 1.0f : 0.0f;
    cb.c[2][0] = inst.r0x;   cb.c[2][1] = inst.r0y;   cb.c[2][2] = inst.r0z;               cb.c[2][3] = 0.0f;
    cb.c[3][0] = inst.d0x;   cb.c[3][1] = inst.d0y;   cb.c[3][2] = inst.d0z;               cb.c[3][3] = 0.0f;
    cb.c[4][0] = inst.n0x;   cb.c[4][1] = inst.n0y;   cb.c[4][2] = inst.n0z;               cb.c[4][3] = p.max_z;
    cb.c[5][0] = p.f_x;      cb.c[5][1] = p.f_y;      cb.c[5][2] = p.f_z;                  cb.c[5][3] = near_off;
    cb.c[7][0] = p.cam_x;    cb.c[7][1] = p.cam_y;    cb.c[7][2] = p.cam_z;                cb.c[7][3] = inv_mz;
    cb.c[8][0] = p.r_x;      cb.c[8][1] = p.r_y;      cb.c[8][2] = p.r_z;                  cb.c[8][3] = scale_x;
    cb.c[9][0] = p.d_x;      cb.c[9][1] = p.d_y;      cb.c[9][2] = p.d_z;                  cb.c[9][3] = scale_y;
    cb.c[10][0] = p.f_x;     cb.c[10][1] = p.f_y;     cb.c[10][2] = p.f_z;                 cb.c[10][3] = 1.0f;
    cb.c[12][0] = 1.0f;
    cb.c[12][1] = static_cast<float>(kLargeBboxThreshold);
    cb.c[12][2] = static_cast<float>(kMaxLargeTris);
    cb.c[12][3] = static_cast<float>(static_cast<uint32_t>(cm_w) * static_cast<uint32_t>(cm_h));
    return cb;
}

void BridgeEngine::SetForceCpuRasterizer(bool force_cpu) {
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);
    mgr.force_cpu = force_cpu;
}

void BridgeEngine::ReleaseGpuResources() {
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);
    mgr.ReleaseDeviceObjects();
    mgr.slots.clear();
}

GpuRasterStatus BridgeEngine::GetGpuRasterStatus() {
    const HostStatus st = GetStatus();
    EDIT_HANDLE* eh = GetEditHandle();
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);

    GpuRasterStatus gs{};
    gs.force_cpu = mgr.force_cpu;
    gs.cached_meshes = static_cast<int32_t>(mgr.slots.size());
    for (const auto& s : mgr.slots) {
        gs.gpu_bytes += static_cast<uint64_t>(s.nv) * 16ull + static_cast<uint64_t>(s.nt) * 16ull;
    }

#ifdef _WIN32
    ID3D11Device3* dev3 = nullptr;
    if (st.host_version >= 2011500u && eh && eh->get_direct3d_device) {
        dev3 = eh->get_direct3d_device();
    }
    gs.d3d11_available = (dev3 != nullptr);
    if (dev3 && st.enable_d3d11_cs && !mgr.force_cpu) {
        gs.d3d11_ready = mgr.EnsureDeviceReady(dev3);
    }
    gs.backend = (gs.d3d11_ready && st.enable_d3d11_cs && !mgr.force_cpu) ? "D3D11_CS" : "CPP_CPU";
#else
    (void)st;
    (void)eh;
    gs.d3d11_available = false;
    gs.d3d11_ready = false;
    gs.backend = "CPP_CPU";
#endif
    gs.last_error = mgr.last_error;
    return gs;
}

int32_t BridgeEngine::HasMesh(const std::string& slot_key, const std::string& rev_sig) {
    if (slot_key.empty()) return 0;
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);
    for (auto& s : mgr.slots) {
        if (s.slot_key == slot_key) {
            if (s.rev_sig == rev_sig && s.nv > 0 && s.nt > 0) {
                s.last_use = ++mgr.use_clock;
                return s.mesh_id;
            }
            return 0;
        }
    }
    return 0;
}

int32_t BridgeEngine::UploadMeshRaw(const std::string& slot_key, const std::string& rev_sig,
                                    const float* xyz3, int32_t nv,
                                    const uint32_t* idx3_0based, int32_t nt) {
    if (slot_key.empty() || !xyz3 || !idx3_0based || nv <= 0 || nt <= 0 ||
        nv > 2000000 || nt > 4000000) {
        return 0;
    }
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);

    MeshSlot* target = nullptr;
    for (auto& s : mgr.slots) {
        if (s.slot_key == slot_key) {
            target = &s;
            break;
        }
    }
    if (!target) {
        if (mgr.slots.size() >= kMaxMeshSlots) {
            size_t lru_idx = 0;
            for (size_t i = 1; i < mgr.slots.size(); ++i) {
                if (mgr.slots[i].last_use < mgr.slots[lru_idx].last_use) lru_idx = i;
            }
            mgr.slots[lru_idx].ReleaseGpu();
            target = &mgr.slots[lru_idx];
        } else {
            mgr.slots.emplace_back();
            target = &mgr.slots.back();
        }
        target->mesh_id = mgr.next_mesh_id++;
        target->slot_key = slot_key;
    } else {
        target->ReleaseGpu();
    }

    target->rev_sig = rev_sig;
    target->nv = nv;
    target->nt = nt;
    target->last_use = ++mgr.use_clock;
    target->verts4.resize(static_cast<size_t>(nv) * 4u);
    target->tris4.resize(static_cast<size_t>(nt) * 4u);

    for (int32_t i = 0; i < nv; ++i) {
        target->verts4[static_cast<size_t>(i) * 4u + 0u] = xyz3[static_cast<size_t>(i) * 3u + 0u];
        target->verts4[static_cast<size_t>(i) * 4u + 1u] = xyz3[static_cast<size_t>(i) * 3u + 1u];
        target->verts4[static_cast<size_t>(i) * 4u + 2u] = xyz3[static_cast<size_t>(i) * 3u + 2u];
        target->verts4[static_cast<size_t>(i) * 4u + 3u] = 1.0f;
    }
    const uint32_t max_idx = static_cast<uint32_t>(nv - 1);
    for (int32_t t = 0; t < nt; ++t) {
        uint32_t a = std::min(idx3_0based[static_cast<size_t>(t) * 3u + 0u], max_idx);
        uint32_t b = std::min(idx3_0based[static_cast<size_t>(t) * 3u + 1u], max_idx);
        uint32_t c = std::min(idx3_0based[static_cast<size_t>(t) * 3u + 2u], max_idx);
        target->tris4[static_cast<size_t>(t) * 4u + 0u] = a;
        target->tris4[static_cast<size_t>(t) * 4u + 1u] = b;
        target->tris4[static_cast<size_t>(t) * 4u + 2u] = c;
        target->tris4[static_cast<size_t>(t) * 4u + 3u] = 0u;
    }
    return target->mesh_id;
}

int32_t BridgeEngine::UploadMeshBase64(const std::string& slot_key, const std::string& rev_sig,
                                       int32_t nv, int32_t nt,
                                       const char* verts_b64, const char* tris_b64) {
    if (slot_key.empty() || nv <= 0 || nt <= 0 || !verts_b64 || !tris_b64 ||
        nv > 2000000 || nt > 4000000) {
        return 0;
    }
    std::vector<uint8_t> v_bytes;
    std::vector<uint8_t> t_bytes;
    if (!DecodeBase64Bytes(verts_b64, std::strlen(verts_b64), &v_bytes)) return 0;
    if (!DecodeBase64Bytes(tris_b64, std::strlen(tris_b64), &t_bytes)) return 0;
    if (v_bytes.size() < static_cast<size_t>(nv) * 12u) return 0;
    if (t_bytes.size() < static_cast<size_t>(nt) * 12u) return 0;

    return UploadMeshRaw(
        slot_key,
        rev_sig,
        reinterpret_cast<const float*>(v_bytes.data()),
        nv,
        reinterpret_cast<const uint32_t*>(t_bytes.data()),
        nt);
}

RastOutput BridgeEngine::RasterizeShadow(void* dst_rgba, int32_t sh_res,
                                         const RastShadowParams& params,
                                         const std::vector<RastModelInstance>& instances) {
    if (!dst_rgba || sh_res < 4 || sh_res > static_cast<int32_t>(kMaxRastDim) || instances.empty()) {
        return {};
    }
    const HostStatus st = GetStatus();
    EDIT_HANDLE* eh = GetEditHandle();
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);

    auto build_cb = [&](const RastModelInstance& inst, uint32_t tri_cnt) {
        return BuildShadowRastCB(sh_res, params, inst, tri_cnt);
    };

#ifdef _WIN32
    if (st.enable_d3d11_cs && !mgr.force_cpu && st.host_version >= 2011500u && eh && eh->get_direct3d_device) {
        ID3D11Device3* dev3 = eh->get_direct3d_device();
        if (dev3) {
            RastOutput gpu_out{};
            if (ExecuteD3D11PassLocked(mgr, dev3, dst_rgba, sh_res, sh_res, instances, build_cb, &gpu_out)) {
                return gpu_out;
            }
        }
    }
#else
    (void)st;
    (void)eh;
#endif

    return ExecuteCpuPassLocked(mgr, dst_rgba, sh_res, sh_res, false, params.splat_cap, instances, build_cb);
}

RastOutput BridgeEngine::RasterizeCamDepth(void* dst_rgba, int32_t cm_w, int32_t cm_h,
                                           const RastCamDepthParams& params,
                                           const std::vector<RastModelInstance>& instances) {
    if (!dst_rgba || cm_w < 4 || cm_h < 4 ||
        cm_w > static_cast<int32_t>(kMaxRastDim) || cm_h > static_cast<int32_t>(kMaxRastDim) ||
        instances.empty()) {
        return {};
    }
    const HostStatus st = GetStatus();
    EDIT_HANDLE* eh = GetEditHandle();
    auto& mgr = GetRastMgr();
    std::lock_guard<std::mutex> lk(mgr.mtx);

    auto build_cb = [&](const RastModelInstance& inst, uint32_t tri_cnt) {
        return BuildCamDepthRastCB(cm_w, cm_h, params, inst, tri_cnt);
    };

#ifdef _WIN32
    if (st.enable_d3d11_cs && !mgr.force_cpu && st.host_version >= 2011500u && eh && eh->get_direct3d_device) {
        ID3D11Device3* dev3 = eh->get_direct3d_device();
        if (dev3) {
            RastOutput gpu_out{};
            if (ExecuteD3D11PassLocked(mgr, dev3, dst_rgba, cm_w, cm_h, instances, build_cb, &gpu_out)) {
                return gpu_out;
            }
        }
    }
#else
    (void)st;
    (void)eh;
#endif

    return ExecuteCpuPassLocked(mgr, dst_rgba, cm_w, cm_h, true, 1, instances, build_cb);
}

}  // namespace vl3d
