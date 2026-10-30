// ---------------------------------------------------------------------------
// VolumetricLight3D test sandbox - minimal HLSL execution runtime (C++17)
//
// The sandbox transpiles the HLSL pixel shaders embedded in
// Script/VolumetricLight3D.obj2 into C++ (see codegen.py) and links them
// against this header.  It intentionally implements only the language subset
// that the project actually uses:
//   * float/int/uint/bool scalars and 2..4 component vectors
//   * swizzles, constructors, component-wise arithmetic
//   * Texture2D.Load / Texture2D.SampleLevel, SamplerState (clamp/wrap)
//   * cbuffer (float4 fields + fixed-size arrays)
//   * if/else, for, break, continue, return, out parameters
// No matrices, no switch/while, no UAVs.
//
// Everything is single precision (HLSL `float` == 32-bit) to stay numerically
// close to the GPU.  Image buffers are 8-bit RGBA, matching the AviUtl2
// `cache:` buffers that `obj.getpixeldata(..., "rgba")` exposes to Lua.
// ---------------------------------------------------------------------------
#pragma once

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace hlsl {

struct V {
    float x, y, z, w;
    V() : x(0.f), y(0.f), z(0.f), w(0.f) {}
    V(float s) : x(s), y(s), z(s), w(s) {}
    V(float a, float b) : x(a), y(b), z(0.f), w(0.f) {}
    V(float a, float b, float c) : x(a), y(b), z(c), w(0.f) {}
    V(float a, float b, float c, float d) : x(a), y(b), z(c), w(d) {}
    V(const V& a, float d) : x(a.x), y(a.y), z(a.z), w(d) {}
};

struct IV {
    int32_t x, y, z, w;
    IV() : x(0), y(0), z(0), w(0) {}
    IV(int32_t s) : x(s), y(s), z(s), w(s) {}
    IV(int32_t a, int32_t b) : x(a), y(b), z(0), w(0) {}
    IV(int32_t a, int32_t b, int32_t c) : x(a), y(b), z(c), w(0) {}
    IV(int32_t a, int32_t b, int32_t c, int32_t d) : x(a), y(b), z(c), w(d) {}
    IV(const IV& a, int32_t d) : x(a.x), y(a.y), z(a.z), w(d) {}
    explicit IV(const V& v) : x((int32_t)v.x), y((int32_t)v.y), z((int32_t)v.z), w((int32_t)v.w) {}
};

struct UV {
    uint32_t x, y, z, w;
    UV() : x(0), y(0), z(0), w(0) {}
    UV(uint32_t s) : x(s), y(s), z(s), w(s) {}
    UV(uint32_t a, uint32_t b) : x(a), y(b), z(0), w(0) {}
    UV(uint32_t a, uint32_t b, uint32_t c) : x(a), y(b), z(c), w(0) {}
    UV(uint32_t a, uint32_t b, uint32_t c, uint32_t d) : x(a), y(b), z(c), w(d) {}
    UV(const UV& a, uint32_t d) : x(a.x), y(a.y), z(a.z), w(d) {}
    explicit UV(const V& v)
        : x((uint32_t)v.x), y((uint32_t)v.y), z((uint32_t)v.z), w((uint32_t)v.w) {}
    explicit UV(const IV& v)
        : x((uint32_t)v.x), y((uint32_t)v.y), z((uint32_t)v.z), w((uint32_t)v.w) {}
    explicit UV(const IV& v, uint32_t d) : x((uint32_t)v.x), y((uint32_t)v.y), z((uint32_t)v.z), w(d) {}
};

// --- vector <-> vector conversions used by the generated code --------------
inline V toV(const V& v) { return v; }
inline V toV(const IV& v) { return V((float)v.x, (float)v.y, (float)v.z, (float)v.w); }
inline V toV(const UV& v) { return V((float)v.x, (float)v.y, (float)v.z, (float)v.w); }
inline V toV(float v) { return V(v); }
inline IV toIV(const IV& v) { return v; }
inline IV toIV(const UV& v) { return IV((int32_t)v.x, (int32_t)v.y, (int32_t)v.z, (int32_t)v.w); }
inline IV toIV(const V& v) { return IV(v); }
inline IV toIV(float v) { return IV((int32_t)v); }
inline IV toIV(int32_t v) { return IV(v); }
inline UV toUV(const UV& v) { return v; }
inline UV toUV(const IV& v) { return UV(v); }
inline UV toUV(const V& v) { return UV(v); }
inline UV toUV(float v) { return UV((uint32_t)v); }
inline UV toUV(uint32_t v) { return UV(v); }
inline float tof(float v) { return v; }
inline float tof(int32_t v) { return (float)v; }
inline float tof(uint32_t v) { return (float)v; }
inline float tof(bool v) { return v ? 1.f : 0.f; }
inline int32_t toi(float v) { return (int32_t)v; }
inline int32_t toi(int32_t v) { return v; }
inline int32_t toi(uint32_t v) { return (int32_t)v; }
inline int32_t toi(bool v) { return v ? 1 : 0; }
inline uint32_t tou(float v) { return (uint32_t)v; }
inline uint32_t tou(int32_t v) { return (uint32_t)v; }
inline uint32_t tou(uint32_t v) { return v; }

// --- arithmetic -------------------------------------------------------------
inline V operator+(const V& a, const V& b) { return V(a.x + b.x, a.y + b.y, a.z + b.z, a.w + b.w); }
inline V operator-(const V& a, const V& b) { return V(a.x - b.x, a.y - b.y, a.z - b.z, a.w - b.w); }
inline V operator*(const V& a, const V& b) { return V(a.x * b.x, a.y * b.y, a.z * b.z, a.w * b.w); }
inline V operator/(const V& a, const V& b) { return V(a.x / b.x, a.y / b.y, a.z / b.z, a.w / b.w); }
inline V operator-(const V& a) { return V(-a.x, -a.y, -a.z, -a.w); }
inline V& operator+=(V& a, const V& b) { a.x += b.x; a.y += b.y; a.z += b.z; a.w += b.w; return a; }
inline V& operator-=(V& a, const V& b) { a.x -= b.x; a.y -= b.y; a.z -= b.z; a.w -= b.w; return a; }
inline V& operator*=(V& a, const V& b) { a.x *= b.x; a.y *= b.y; a.z *= b.z; a.w *= b.w; return a; }
inline V& operator/=(V& a, const V& b) { a.x /= b.x; a.y /= b.y; a.z /= b.z; a.w /= b.w; return a; }

inline IV operator+(const IV& a, const IV& b) { return IV(a.x + b.x, a.y + b.y, a.z + b.z, a.w + b.w); }
inline IV operator-(const IV& a, const IV& b) { return IV(a.x - b.x, a.y - b.y, a.z - b.z, a.w - b.w); }
inline IV operator*(const IV& a, const IV& b) { return IV(a.x * b.x, a.y * b.y, a.z * b.z, a.w * b.w); }
inline IV operator/(const IV& a, const IV& b) { return IV(a.x / b.x, a.y / b.y, a.z / b.z, a.w / b.w); }
inline IV operator%(const IV& a, const IV& b) { return IV(a.x % b.x, a.y % b.y, a.z % b.z, a.w % b.w); }
inline IV operator-(const IV& a) { return IV(-a.x, -a.y, -a.z, -a.w); }
inline IV& operator+=(IV& a, const IV& b) { a.x += b.x; a.y += b.y; a.z += b.z; a.w += b.w; return a; }
inline IV& operator-=(IV& a, const IV& b) { a.x -= b.x; a.y -= b.y; a.z -= b.z; a.w -= b.w; return a; }
inline IV& operator*=(IV& a, const IV& b) { a.x *= b.x; a.y *= b.y; a.z *= b.z; a.w *= b.w; return a; }

inline UV operator+(const UV& a, const UV& b) { return UV(a.x + b.x, a.y + b.y, a.z + b.z, a.w + b.w); }
inline UV operator-(const UV& a, const UV& b) { return UV(a.x - b.x, a.y - b.y, a.z - b.z, a.w - b.w); }
inline UV operator*(const UV& a, const UV& b) { return UV(a.x * b.x, a.y * b.y, a.z * b.z, a.w * b.w); }
inline UV operator/(const UV& a, const UV& b) { return UV(a.x / b.x, a.y / b.y, a.z / b.z, a.w / b.w); }
inline UV operator^(const UV& a, const UV& b) { return UV(a.x ^ b.x, a.y ^ b.y, a.z ^ b.z, a.w ^ b.w); }
inline UV operator&(const UV& a, const UV& b) { return UV(a.x & b.x, a.y & b.y, a.z & b.z, a.w & b.w); }
inline UV operator|(const UV& a, const UV& b) { return UV(a.x | b.x, a.y | b.y, a.z | b.z, a.w | b.w); }
inline UV operator>>(const UV& a, const UV& b) { return UV(a.x >> b.x, a.y >> b.y, a.z >> b.z, a.w >> b.w); }
inline UV operator<<(const UV& a, const UV& b) { return UV(a.x << b.x, a.y << b.y, a.z << b.z, a.w << b.w); }
inline UV& operator^=(UV& a, const UV& b) { a.x ^= b.x; a.y ^= b.y; a.z ^= b.z; a.w ^= b.w; return a; }
inline UV& operator+=(UV& a, const UV& b) { a.x += b.x; a.y += b.y; a.z += b.z; a.w += b.w; return a; }
inline UV& operator*=(UV& a, const UV& b) { a.x *= b.x; a.y *= b.y; a.z *= b.z; a.w *= b.w; return a; }

// --- swizzles ---------------------------------------------------------------
inline V swz(const V& a, int i) {
    const float c[4] = {a.x, a.y, a.z, a.w};
    return V(c[i]);
}
inline V swz2(const V& a, int i, int j) {
    const float c[4] = {a.x, a.y, a.z, a.w};
    return V(c[i], c[j]);
}
inline V swz3(const V& a, int i, int j, int k) {
    const float c[4] = {a.x, a.y, a.z, a.w};
    return V(c[i], c[j], c[k]);
}
inline V swz4(const V& a, int i, int j, int k, int l) {
    const float c[4] = {a.x, a.y, a.z, a.w};
    return V(c[i], c[j], c[k], c[l]);
}
inline IV iswz2(const IV& a, int i, int j) {
    const int32_t c[4] = {a.x, a.y, a.z, a.w};
    return IV(c[i], c[j]);
}
inline IV iswz3(const IV& a, int i, int j, int k) {
    const int32_t c[4] = {a.x, a.y, a.z, a.w};
    return IV(c[i], c[j], c[k]);
}
inline UV uswz2(const UV& a, int i, int j) {
    const uint32_t c[4] = {a.x, a.y, a.z, a.w};
    return UV(c[i], c[j]);
}
inline UV uswz3(const UV& a, int i, int j, int k) {
    const uint32_t c[4] = {a.x, a.y, a.z, a.w};
    return UV(c[i], c[j], c[k]);
}

// --- min / max / clamp / abs ------------------------------------------------
inline float min(float a, float b) { return a < b ? a : b; }
inline float max(float a, float b) { return a > b ? a : b; }
inline int32_t min(int32_t a, int32_t b) { return a < b ? a : b; }
inline int32_t max(int32_t a, int32_t b) { return a > b ? a : b; }
inline uint32_t min(uint32_t a, uint32_t b) { return a < b ? a : b; }
inline uint32_t max(uint32_t a, uint32_t b) { return a > b ? a : b; }
inline V min(const V& a, const V& b) { return V(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z), min(a.w, b.w)); }
inline V max(const V& a, const V& b) { return V(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z), max(a.w, b.w)); }
inline V min(const V& a, float b) { return min(a, V(b)); }
inline V max(const V& a, float b) { return max(a, V(b)); }
inline IV min(const IV& a, const IV& b) { return IV(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z), min(a.w, b.w)); }
inline IV max(const IV& a, const IV& b) { return IV(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z), max(a.w, b.w)); }
inline UV min(const UV& a, const UV& b) { return UV(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z), min(a.w, b.w)); }
inline UV max(const UV& a, const UV& b) { return UV(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z), max(a.w, b.w)); }
inline V clamp(const V& v, const V& lo, const V& hi) { return min(max(v, lo), hi); }
inline V clamp(const V& v, float lo, float hi) { return clamp(v, V(lo), V(hi)); }
inline float clamp(float v, float lo, float hi) { return min(max(v, lo), hi); }
inline int32_t clamp(int32_t v, int32_t lo, int32_t hi) { return min(max(v, lo), hi); }
inline IV clamp(const IV& v, const IV& lo, const IV& hi) { return min(max(v, lo), hi); }
inline float saturate(float v) { return clamp(v, 0.f, 1.f); }
inline V saturate(const V& v) { return clamp(v, V(0.f), V(1.f)); }
inline float abs(float v) { return std::fabs(v); }
// 整数版は意図的に置かない: 生成コードはグローバル名前空間の abs(int) を
// 見るため、hlsl::abs(int32_t) を併設すると整数呼び出しが曖昧になる
// （第三者シェーダの `abs(j)` で実際に衝突した）。
inline V abs(const V& v) { return V(abs(v.x), abs(v.y), abs(v.z), abs(v.w)); }

// --- math -------------------------------------------------------------------
inline float frac(float v) { return v - std::floor(v); }
inline V frac(const V& v) { return V(frac(v.x), frac(v.y), frac(v.z), frac(v.w)); }
inline float floor(float v) { return std::floor(v); }
inline V floor(const V& v) { return V(floor(v.x), floor(v.y), floor(v.z), floor(v.w)); }
inline float ceil(float v) { return std::ceil(v); }
inline V ceil(const V& v) { return V(ceil(v.x), ceil(v.y), ceil(v.z), ceil(v.w)); }
inline float sqrt(float v) { return std::sqrt(v); }
inline V sqrt(const V& v) { return V(sqrt(v.x), sqrt(v.y), sqrt(v.z), sqrt(v.w)); }
inline float rsqrt(float v) { return 1.f / std::sqrt(v); }
inline V rsqrt(const V& v) { return V(rsqrt(v.x), rsqrt(v.y), rsqrt(v.z), rsqrt(v.w)); }
inline float exp(float v) { return std::exp(v); }
inline V exp(const V& v) { return V(exp(v.x), exp(v.y), exp(v.z), exp(v.w)); }
inline float sin(float v) { return std::sin(v); }
inline float cos(float v) { return std::cos(v); }
inline float acos(float v) { return std::acos(v); }
inline float atan2(float a, float b) { return std::atan2(a, b); }
inline float pow(float a, float b) { return std::pow(a, b); }
inline V pow(const V& a, float b) { return V(pow(a.x, b), pow(a.y, b), pow(a.z, b), pow(a.w, b)); }
inline V pow(const V& a, const V& b) { return V(pow(a.x, b.x), pow(a.y, b.y), pow(a.z, b.z), pow(a.w, b.w)); }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
inline V lerp(const V& a, const V& b, float t) { return a + (b - a) * V(t); }
inline V lerp(const V& a, const V& b, const V& t) { return a + (b - a) * t; }
inline float dot(const V& a, const V& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline float length(const V& a) { return std::sqrt(dot(a, a)); }
inline V normalize(const V& a) {
    float l = length(a);
    return l > 1e-20f ? a / V(l) : V(0.f);
}
inline float smoothstep(float e0, float e1, float x) {
    float t = clamp((x - e0) / (e1 - e0 == 0.f ? 1e-20f : e1 - e0), 0.f, 1.f);
    return t * t * (3.f - 2.f * t);
}
inline V smoothstep(float e0, float e1, const V& x) {
    return V(smoothstep(e0, e1, x.x), smoothstep(e0, e1, x.y), smoothstep(e0, e1, x.z),
             smoothstep(e0, e1, x.w));
}
inline float fmod(float a, float b) { return std::fmod(a, b); }

// --- textures ---------------------------------------------------------------
// Passed from Python: width/height + RGBA float32 data (0..1).
struct vl_tex {
    int w = 0;
    int h = 0;
    const float* px = nullptr;
};

// Global sampler configuration applied to every SamplerState in a shader run.
// (AviUtl2 sets the sampler state through obj.setoption("sampler", ...) /
//  the pixel-shader call itself; the sandbox exposes it explicitly.)
inline int g_filter = 1;   // 0 = point, 1 = linear
inline int g_address = 0;  // 0 = clamp, 1 = wrap

struct Smp {
    int filter = 1;   // 0 = point, 1 = linear
    int address = 0;  // 0 = clamp, 1 = wrap
};

struct Tex {
    int w = 0;
    int h = 0;
    const V* px = nullptr;

    V Load(const IV& ip) const {
        if (w <= 0 || h <= 0 || px == nullptr) return V(0.f);
        if (ip.x < 0 || ip.y < 0 || ip.x >= w || ip.y >= h) return V(0.f);
        return px[(size_t)ip.y * (size_t)w + (size_t)ip.x];
    }

    V SampleLevel(const Smp& s, const V& uv, float /*lod*/ = 0.f) const {
        if (w <= 0 || h <= 0 || px == nullptr) return V(0.f);
        float u = uv.x;
        float v = uv.y;
        if (s.address == 1) {
            u = u - std::floor(u);
            v = v - std::floor(v);
        } else {
            u = clamp(u, 0.f, 1.f);
            v = clamp(v, 0.f, 1.f);
        }
        float fx = u * (float)w - 0.5f;
        float fy = v * (float)h - 0.5f;
        if (s.filter == 0) {
            int ix = (int)std::floor(fx + 0.5f);
            int iy = (int)std::floor(fy + 0.5f);
            if (s.address == 1) {
                ix = ((ix % w) + w) % w;
                iy = ((iy % h) + h) % h;
            } else {
                ix = ix < 0 ? 0 : (ix >= w ? w - 1 : ix);
                iy = iy < 0 ? 0 : (iy >= h ? h - 1 : iy);
            }
            return px[(size_t)iy * (size_t)w + (size_t)ix];
        }
        int x0 = (int)std::floor(fx);
        int y0 = (int)std::floor(fy);
        float tx = fx - (float)x0;
        float ty = fy - (float)y0;
        auto at = [&](int x, int y) -> V {
            if (s.address == 1) {
                x = ((x % w) + w) % w;
                y = ((y % h) + h) % h;
            } else {
                x = x < 0 ? 0 : (x >= w ? w - 1 : x);
                y = y < 0 ? 0 : (y >= h ? h - 1 : y);
            }
            return px[(size_t)y * (size_t)w + (size_t)x];
        };
        V c00 = at(x0, y0), c10 = at(x0 + 1, y0), c01 = at(x0, y0 + 1), c11 = at(x0 + 1, y0 + 1);
        return lerp(lerp(c00, c10, tx), lerp(c01, c11, tx), ty);
    }
};

}  // namespace hlsl
