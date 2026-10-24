#include "vl3d_bridge_core.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <sstream>

namespace vl3d {

namespace {

std::mutex g_bridge_mutex;

inline uint16_t LoadU16LE(const uint8_t* p) {
    return static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8);
}
inline uint32_t LoadU32LE(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) |
           (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}
inline int32_t LoadI32LE(const uint8_t* p) {
    return static_cast<int32_t>(LoadU32LE(p));
}
inline uint64_t LoadU64LE(const uint8_t* p) {
    return static_cast<uint64_t>(LoadU32LE(p)) |
           (static_cast<uint64_t>(LoadU32LE(p + 4)) << 32);
}

std::wstring Utf8ToSdkWide(const std::string& s) {
    std::u16string u16 = Utf8ToUtf16Le(s);
    std::wstring out;
#ifdef _WIN32
    out.assign(u16.begin(), u16.end());
#else
    for (size_t i = 0; i < u16.size(); ++i) {
        uint32_t cp = u16[i];
        if (cp >= 0xD800 && cp <= 0xDBFF && (i + 1) < u16.size()) {
            uint32_t lo = u16[i + 1];
            if (lo >= 0xDC00 && lo <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                ++i;
            }
        }
        out.push_back(static_cast<wchar_t>(cp));
    }
#endif
    return out;
}

std::string TrimAscii(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && (s[a] == ' ' || s[a] == '\t' || s[a] == '\r' || s[a] == '\n')) ++a;
    while (b > a && (s[b - 1] == ' ' || s[b - 1] == '\t' || s[b - 1] == '\r' || s[b - 1] == '\n')) --b;
    return s.substr(a, b - a);
}

std::string ToLowerAscii(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return s;
}

int32_t ParseHexColorString(const char* s) {
    if (!s || !*s) return -1;
    while (*s == ' ' || *s == '\t' || *s == '#') ++s;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    char* endp = nullptr;
    unsigned long val = std::strtoul(s, &endp, 16);
    if (endp == s || (endp - s) < 6) return -1;
    return static_cast<int32_t>(val & 0xFFFFFFu);
}

// Minimal FIPS 180-4 SHA-256 implementation (zero external library dependency)
struct Sha256Ctx {
    uint32_t state[8];
    uint64_t bitlen = 0;
    uint8_t buf[64];
    size_t buflen = 0;

    Sha256Ctx() {
        state[0] = 0x6a09e667u; state[1] = 0xbb67ae85u;
        state[2] = 0x3c6ef372u; state[3] = 0xa54ff53au;
        state[4] = 0x510e527fu; state[5] = 0x9b05688cu;
        state[6] = 0x1f83d9abu; state[7] = 0x5be0cd19u;
    }

    static inline uint32_t RotR(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

    void Transform(const uint8_t block[64]) {
        static const uint32_t K[64] = {
            0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
            0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
            0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
            0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
            0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
            0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
            0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
            0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
        };
        uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            w[i] = (static_cast<uint32_t>(block[i * 4]) << 24) |
                   (static_cast<uint32_t>(block[i * 4 + 1]) << 16) |
                   (static_cast<uint32_t>(block[i * 4 + 2]) << 8) |
                   static_cast<uint32_t>(block[i * 4 + 3]);
        }
        for (int i = 16; i < 64; ++i) {
            uint32_t s0 = RotR(w[i - 15], 7) ^ RotR(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = RotR(w[i - 2], 17) ^ RotR(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
        uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
        for (int i = 0; i < 64; ++i) {
            uint32_t S1 = RotR(e, 6) ^ RotR(e, 11) ^ RotR(e, 25);
            uint32_t ch = (e & f) ^ ((~e) & g);
            uint32_t temp1 = h + S1 + ch + K[i] + w[i];
            uint32_t S0 = RotR(a, 2) ^ RotR(a, 13) ^ RotR(a, 22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t temp2 = S0 + maj;
            h = g; g = f; f = e; e = d + temp1;
            d = c; c = b; b = a; a = temp1 + temp2;
        }
        state[0] += a; state[1] += b; state[2] += c; state[3] += d;
        state[4] += e; state[5] += f; state[6] += g; state[7] += h;
    }

    void Update(const uint8_t* data, size_t len) {
        for (size_t i = 0; i < len; ++i) {
            buf[buflen++] = data[i];
            if (buflen == 64) {
                Transform(buf);
                bitlen += 512;
                buflen = 0;
            }
        }
    }

    std::string FinalHex() {
        uint64_t total_bits = bitlen + buflen * 8;
        buf[buflen++] = 0x80;
        if (buflen > 56) {
            while (buflen < 64) buf[buflen++] = 0;
            Transform(buf);
            buflen = 0;
        }
        while (buflen < 56) buf[buflen++] = 0;
        for (int i = 7; i >= 0; --i) {
            buf[buflen++] = static_cast<uint8_t>((total_bits >> (i * 8)) & 0xFFu);
        }
        Transform(buf);
        char hex[65];
        for (int i = 0; i < 8; ++i) {
            std::snprintf(hex + i * 8, 9, "%08x", state[i]);
        }
        hex[64] = '\0';
        return std::string(hex);
    }
};

}  // namespace

std::string ComputeSha256Hex(const uint8_t* data, size_t len) {
    Sha256Ctx ctx;
    if (data && len > 0) ctx.Update(data, len);
    return ctx.FinalHex();
}

std::string Utf16LeToUtf8(const uint16_t* wptr, size_t max_units) {
    if (!wptr || max_units == 0) return {};
    std::string out;
    out.reserve(max_units);
    for (size_t i = 0; i < max_units; ++i) {
        uint32_t cp = wptr[i];
        if (cp == 0) break;
        if (cp >= 0xD800 && cp <= 0xDBFF && (i + 1) < max_units) {
            uint32_t u2 = wptr[i + 1];
            if (u2 >= 0xDC00 && u2 <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (u2 - 0xDC00);
                ++i;
            }
        }
        if (cp < 0x80) {
            out.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }
    return out;
}

std::u16string Utf8ToUtf16Le(const std::string& utf8) {
    std::u16string out;
    out.reserve(utf8.size());
    size_t i = 0, n = utf8.size();
    while (i < n) {
        uint8_t c = static_cast<uint8_t>(utf8[i]);
        uint32_t cp = 0;
        if (c < 0x80) {
            cp = c; ++i;
        } else if ((c & 0xE0) == 0xC0 && i + 1 < n) {
            cp = ((c & 0x1F) << 6) | (static_cast<uint8_t>(utf8[i + 1]) & 0x3F);
            i += 2;
        } else if ((c & 0xF0) == 0xE0 && i + 2 < n) {
            cp = ((c & 0x0F) << 12) |
                 ((static_cast<uint8_t>(utf8[i + 1]) & 0x3F) << 6) |
                 (static_cast<uint8_t>(utf8[i + 2]) & 0x3F);
            i += 3;
        } else if ((c & 0xF8) == 0xF0 && i + 3 < n) {
            cp = ((c & 0x07) << 18) |
                 ((static_cast<uint8_t>(utf8[i + 1]) & 0x3F) << 12) |
                 ((static_cast<uint8_t>(utf8[i + 2]) & 0x3F) << 6) |
                 (static_cast<uint8_t>(utf8[i + 3]) & 0x3F);
            i += 4;
        } else {
            ++i;
            continue;
        }
        if (cp < 0x10000) {
            out.push_back(static_cast<char16_t>(cp));
        } else {
            cp -= 0x10000;
            out.push_back(static_cast<char16_t>(0xD800 + (cp >> 10)));
            out.push_back(static_cast<char16_t>(0xDC00 + (cp & 0x3FF)));
        }
    }
    return out;
}

BridgeEngine::BridgeEngine() {
    // Official AviUtl ExEdit2 v2.1.15 (2026-10-24) verified PE32+ + .text SHA-256 fingerprint
    AllowlistEntry v2115{};
    v2115.pe_timestamp = 0x6ADC1302u;
    v2115.image_size = 0x005F5000u;
    v2115.sha256_hex = "ea321cfb5c5c171b0fb5496c6aab715171e4d240f999ea2a9b79b074fc9bd688";
    v2115.label = "AviUtl2_v2.1.15_20261024";
    allowlist_.push_back(v2115);
}

void BridgeEngine::SetMemoryOps(const HostMemoryOps& ops) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    ops_ = ops;
}

void BridgeEngine::SetEditHandle(EDIT_HANDLE* edit_handle) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    edit_handle_ = edit_handle;
    status_.sdk_ready = (edit_handle_ != nullptr);
    if (status_.sdk_ready && status_.rpm_allowed) {
        status_.mode = "SDK+VERIFIED_RPM";
    } else if (status_.sdk_ready) {
        status_.mode = "SDK_ONLY_SAFE_DEGRADED";
    }
}

void BridgeEngine::ClearAllowlist() {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    allowlist_.clear();
}

void BridgeEngine::AddAllowlistEntry(const AllowlistEntry& entry) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    AllowlistEntry e = entry;
    e.sha256_hex = ToLowerAscii(TrimAscii(e.sha256_hex));
    allowlist_.push_back(e);
}

void BridgeEngine::SetStrictWhitelistOnly(bool strict_only) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    status_.strict_whitelist_only = strict_only;
}

bool BridgeEngine::LoadConfigIniText(const std::string& ini_text) {
    std::istringstream iss(ini_text);
    std::string line;
    while (std::getline(iss, line)) {
        std::string s = TrimAscii(line);
        if (s.empty() || s[0] == ';' || s[0] == '#' || s[0] == '[') continue;
        size_t eq = s.find('=');
        if (eq == std::string::npos) continue;
        std::string k = ToLowerAscii(TrimAscii(s.substr(0, eq)));
        std::string v = TrimAscii(s.substr(eq + 1));
        if (k == "strict_whitelist_only") {
            SetStrictWhitelistOnly(v == "1" || ToLowerAscii(v) == "true");
        } else if (k == "enable_d3d11_cs") {
            std::lock_guard<std::mutex> lk(g_bridge_mutex);
            status_.enable_d3d11_cs = !(v == "0" || ToLowerAscii(v) == "false");
        } else if (k == "force_cpu_rasterizer") {
            SetForceCpuRasterizer(v == "1" || ToLowerAscii(v) == "true");
        } else if (k == "allow_sha256" && v.size() >= 64) {
            AllowlistEntry e;
            e.sha256_hex = ToLowerAscii(v.substr(0, 64));
            e.label = "ini_sha256";
            if (v.size() > 65 && (v[64] == ':' || v[64] == ',')) {
                std::string rest = v.substr(65);
                size_t sep = rest.find_first_of(":,");
                if (sep != std::string::npos) {
                    e.image_size = static_cast<uint32_t>(std::strtoul(rest.substr(0, sep).c_str(), nullptr, 0));
                    e.pe_timestamp = static_cast<uint32_t>(std::strtoul(rest.substr(sep + 1).c_str(), nullptr, 0));
                }
            }
            AddAllowlistEntry(e);
        } else if (k == "allow_pe") {
            // Format: timestamp_hex_or_dec,image_size_hex_or_dec
            size_t cm = v.find(',');
            if (cm != std::string::npos) {
                AllowlistEntry e;
                e.pe_timestamp = static_cast<uint32_t>(std::strtoul(v.substr(0, cm).c_str(), nullptr, 0));
                e.image_size = static_cast<uint32_t>(std::strtoul(v.substr(cm + 1).c_str(), nullptr, 0));
                e.label = "ini_pe";
                if (e.pe_timestamp != 0) AddAllowlistEntry(e);
            }
        }
    }
    return true;
}

bool BridgeEngine::ReadExact(uint64_t addr, void* dst, size_t sz) const {
    if (!ops_.read_mem || addr < 0x10000 || sz == 0) return false;
    size_t got = 0;
    return ops_.read_mem(ops_.user, addr, dst, sz, &got) && (got == sz);
}

bool BridgeEngine::ReadU64(uint64_t addr, uint64_t* out) const {
    uint8_t b[8];
    if (!ReadExact(addr, b, 8)) return false;
    *out = LoadU64LE(b);
    return true;
}

bool BridgeEngine::ReadI32(uint64_t addr, int32_t* out) const {
    uint8_t b[4];
    if (!ReadExact(addr, b, 4)) return false;
    *out = LoadI32LE(b);
    return true;
}

bool BridgeEngine::ReadU8(uint64_t addr, uint8_t* out) const {
    return ReadExact(addr, out, 1);
}

bool BridgeEngine::ReadWString(uint64_t addr, size_t max_units, std::string* out_utf8) const {
    if (!ops_.read_mem || addr < 0x10000 || max_units == 0) return false;
    const size_t cap = std::min<size_t>(max_units, 512);
    std::vector<uint16_t> units(cap + 1, 0);
    size_t total_u = 0;
    uint64_t cur = addr;
    size_t rem = cap;
    while (rem > 0) {
        size_t in_page = static_cast<size_t>(4096u - (cur & 4095u));
        size_t chunk_b = std::min<size_t>(rem * 2, in_page);
        if (chunk_b < 2) chunk_b = 2;
        size_t got_b = 0;
        if (ops_.read_mem(ops_.user, cur, units.data() + total_u, chunk_b, &got_b) && got_b >= 2) {
            size_t got_u = got_b / 2;
            for (size_t k = 0; k < got_u; ++k) {
                if (units[total_u + k] == 0) {
                    *out_utf8 = Utf16LeToUtf8(units.data(), total_u + k);
                    return true;
                }
            }
            total_u += got_u;
            cur += got_u * 2;
            rem -= got_u;
        } else {
            if (!ops_.read_mem(ops_.user, cur, units.data() + total_u, 2, &got_b) || got_b < 2) {
                break;
            }
            if (units[total_u] == 0) {
                *out_utf8 = Utf16LeToUtf8(units.data(), total_u);
                return true;
            }
            total_u += 1;
            cur += 2;
            rem -= 1;
        }
    }
    if (total_u > 0) {
        units[total_u] = 0;
        *out_utf8 = Utf16LeToUtf8(units.data(), total_u);
        return true;
    }
    return false;
}

bool BridgeEngine::InitializeHost(uint32_t host_version) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    status_.initialized = true;
    status_.host_version = host_version;
    status_.pe_valid = false;
    status_.whitelist_hit = false;
    status_.sig_scene_ok = false;
    status_.sig_editcam_ok = false;
    status_.rpm_allowed = false;
    lctx_cands_.clear();
    edit_cam_sig_found_ = false;

    if (!ops_.get_exe_base || !ops_.read_mem) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }

    const uint64_t exe_base = ops_.get_exe_base(ops_.user);
    if (exe_base < 0x10000) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }

    uint8_t hdr[4096];
    size_t hdr_read = 0;
    if (!ops_.read_mem(ops_.user, exe_base, hdr, sizeof(hdr), &hdr_read) || hdr_read < 512) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }

    // Hardened PE64 header & section-table bounds check
    if (hdr[0] != 0x4D || hdr[1] != 0x5A) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }
    const int32_t lfanew = LoadI32LE(hdr + 0x3C);
    if (lfanew < 0x40 || lfanew > 0x800 || static_cast<size_t>(lfanew + 0x88) > hdr_read) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }
    const uint8_t* nt = hdr + lfanew;
    if (nt[0] != 'P' || nt[1] != 'E' || nt[2] != 0 || nt[3] != 0) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }
    const uint16_t machine = LoadU16LE(nt + 4);
    const uint16_t nsec_raw = LoadU16LE(nt + 6);
    const uint32_t pe_ts = LoadU32LE(nt + 8);
    const uint16_t opt_sz = LoadU16LE(nt + 20);
    const uint16_t magic = LoadU16LE(nt + 24);
    const uint16_t nsec = std::min<uint16_t>(nsec_raw, 16);

    if (machine != 0x8664 || magic != 0x020B || opt_sz < 0x70 || opt_sz > 1024 ||
        static_cast<size_t>(lfanew) + 24u + opt_sz + static_cast<size_t>(nsec) * 40u > hdr_read) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }

    const uint32_t img_sz = LoadU32LE(nt + 24 + 0x38);  // OptionalHeader.SizeOfImage
    if (img_sz < 0x1000 || img_sz > 0x20000000u) {
        status_.mode = status_.sdk_ready ? "SDK_ONLY_SAFE_DEGRADED" : "UNVERIFIED_DEGRADED";
        return true;
    }

    status_.pe_valid = true;
    status_.pe_timestamp = pe_ts;
    status_.image_size = img_sz;

    Sha256Ctx sha;
    sha.Update(hdr, hdr_read);

    // Scan .text via kernel-safe ReadProcessMemory chunks to compute SHA-256 + verify code signatures
    const uint8_t* sec_p = nt + 24 + opt_sz;
    for (uint16_t si = 0; si < nsec; ++si) {
        const uint8_t* sh = sec_p + si * 40;
        const uint32_t vsz = LoadU32LE(sh + 8);
        const uint32_t rva = LoadU32LE(sh + 12);
        const uint32_t ch = LoadU32LE(sh + 36);
        const bool is_exec = ((ch & 0x20000000u) != 0) || (sh[0] == '.' && sh[1] == 't' && sh[2] == 'e');
        if (!is_exec || vsz < 64 || vsz > 0x2000000u || rva >= img_sz || rva + vsz > img_sz) continue;

        std::vector<uint8_t> code(vsz);
        size_t got = 0;
        if (!ops_.read_mem(ops_.user, exe_base + rva, code.data(), vsz, &got) || got < 64) continue;
        sha.Update(code.data(), got);

        // Signature 1: lua_getvalue scene-context pointer (48 8B 05 disp32 48 8B 90 88 00 00 00)
        for (size_t p = 0; p + 14 <= got; ++p) {
            if (code[p] == 0x48 && code[p + 1] == 0x8B && code[p + 2] == 0x05 &&
                code[p + 7] == 0x48 && code[p + 8] == 0x8B && code[p + 9] == 0x90 &&
                code[p + 10] == 0x88 && code[p + 11] == 0x00 && code[p + 12] == 0x00 && code[p + 13] == 0x00) {
                int32_t disp = LoadI32LE(code.data() + p + 3);
                uint64_t cand = static_cast<uint64_t>(static_cast<int64_t>(exe_base + rva + p + 7) + disp);
                if (cand >= exe_base && cand + 8 <= exe_base + img_sz) {
                    if (std::find(lctx_cands_.begin(), lctx_cands_.end(), cand) == lctx_cands_.end()) {
                        lctx_cands_.push_back(cand);
                    }
                    status_.sig_scene_ok = true;
                }
            }
        }

        // Signature 2: live Edit-Camera matrix block (48 8B 05 .. 4C 8B 80 .. 49 81 C0 .. EB 07 4C 8D 87)
        for (size_t p = 0; p + 31 <= got && !edit_cam_sig_found_; ++p) {
            if (code[p] == 0x48 && code[p + 1] == 0x8B && code[p + 2] == 0x05 &&
                code[p + 7] == 0x4C && code[p + 8] == 0x8B && code[p + 9] == 0x80 &&
                code[p + 14] == 0x49 && code[p + 15] == 0x81 && code[p + 16] == 0xC0 &&
                code[p + 21] == 0xEB && code[p + 22] == 0x07 &&
                code[p + 23] == 0x4C && code[p + 24] == 0x8D && code[p + 25] == 0x87) {
                int32_t disp_g = LoadI32LE(code.data() + p + 3);
                uint64_t g_addr = static_cast<uint64_t>(static_cast<int64_t>(exe_base + rva + p + 7) + disp_g);
                if (g_addr >= exe_base && g_addr + 8 <= exe_base + img_sz) {
                    edit_cam_g_addr_ = g_addr;
                    edit_cam_off_w_ = LoadU32LE(code.data() + p + 10);
                    edit_cam_off_c_ = LoadU32LE(code.data() + p + 17);
                    edit_cam_sig_found_ = true;
                    status_.sig_editcam_ok = true;
                }
            }
        }
    }

    status_.sha256_hex = sha.FinalHex();

    // Check explicit allowlist (SHA-256 or TimeDateStamp + SizeOfImage)
    for (const auto& e : allowlist_) {
        bool sha_ok = !e.sha256_hex.empty() && (e.sha256_hex == status_.sha256_hex);
        bool ts_ok = (e.pe_timestamp != 0 && e.pe_timestamp == status_.pe_timestamp) &&
                     (e.image_size == 0 || e.image_size == status_.image_size);
        if (sha_ok || ts_ok) {
            status_.whitelist_hit = true;
            break;
        }
    }

    // Gate fixed-offset RPM probing:
    // - If strict_whitelist_only is enabled: MUST match explicit allowlist!
    // - Otherwise: allow ONLY if allowlist matches OR live code instruction signature matched cleanly.
    //   Unknown/incompatible binaries without matching code signatures immediately degrade safely!
    if (status_.strict_whitelist_only) {
        status_.rpm_allowed = status_.whitelist_hit;
    } else {
        status_.rpm_allowed = status_.whitelist_hit || status_.sig_scene_ok || status_.sig_editcam_ok;
    }

    if (status_.sdk_ready && status_.rpm_allowed) {
        status_.mode = "SDK+VERIFIED_RPM";
    } else if (status_.sdk_ready) {
        status_.mode = "SDK_ONLY_SAFE_DEGRADED";
    } else if (status_.rpm_allowed) {
        status_.mode = "VERIFIED_RPM_ONLY";
    } else {
        status_.mode = "UNVERIFIED_DEGRADED";
    }
    return true;
}

void BridgeEngine::OnSceneChanged(int32_t scene_id) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    status_.active_scene_id = scene_id;
}

void BridgeEngine::InvalidateCache() {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    sdk_clip_cache_.clear();
}

HostStatus BridgeEngine::GetStatus() const {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    return status_;
}

EDIT_HANDLE* BridgeEngine::GetEditHandle() const {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    return edit_handle_;
}

// =========================================================================
// Hardened PE64 Export Table Lookup (SizeOfImage, safe_cstr, ordinal bounds)
// =========================================================================
uint64_t BridgeEngine::FindPeExportAddress(uint64_t mod_base, const char* func_name) const {
    if (!ops_.read_mem || mod_base < 0x10000 || !func_name || !*func_name) return 0;
    uint8_t hdr[4096];
    size_t got = 0;
    if (!ops_.read_mem(ops_.user, mod_base, hdr, sizeof(hdr), &got) || got < 512) return 0;
    if (hdr[0] != 0x4D || hdr[1] != 0x5A) return 0;
    int32_t lfanew = LoadI32LE(hdr + 0x3C);
    if (lfanew < 0x40 || lfanew > 0x800 || static_cast<size_t>(lfanew + 0x90) > got) return 0;
    const uint8_t* nt = hdr + lfanew;
    if (nt[0] != 'P' || nt[1] != 'E' || nt[2] != 0 || nt[3] != 0) return 0;
    uint16_t magic = LoadU16LE(nt + 0x18);
    uint32_t img_sz = LoadU32LE(nt + 0x50);
    if (img_sz < 0x1000 || img_sz > 0x20000000u) return 0;
    size_t dd_off = (magic == 0x020B) ? 0x88 : ((magic == 0x010B) ? 0x78 : 0);
    if (dd_off == 0) return 0;

    uint32_t exp_rva = LoadU32LE(nt + dd_off);
    uint32_t exp_sz = LoadU32LE(nt + dd_off + 4);
    if (exp_rva == 0 || exp_sz < 0x28 || exp_rva >= img_sz || exp_rva + exp_sz > img_sz) return 0;

    uint8_t exp_hdr[0x28];
    if (!ReadExact(mod_base + exp_rva, exp_hdr, sizeof(exp_hdr))) return 0;
    uint32_t n_funcs = LoadU32LE(exp_hdr + 0x14);
    uint32_t n_names = LoadU32LE(exp_hdr + 0x18);
    uint32_t rva_fn = LoadU32LE(exp_hdr + 0x1C);
    uint32_t rva_nm = LoadU32LE(exp_hdr + 0x20);
    uint32_t rva_ord = LoadU32LE(exp_hdr + 0x24);
    if (n_funcs == 0 || n_names == 0 || n_funcs > 65536 || n_names > 65536) return 0;
    if (rva_fn == 0 || rva_nm == 0 || rva_ord == 0) return 0;
    if (rva_fn + n_funcs * 4u > img_sz || rva_nm + n_names * 4u > img_sz || rva_ord + n_names * 2u > img_sz) return 0;

    auto read_safe_cstr = [&](uint32_t rva, std::string* out) -> bool {
        if (rva == 0 || rva >= img_sz) return false;
        size_t lim = std::min<size_t>(256, img_sz - rva);
        char buf[256];
        size_t rd = 0;
        if (!ops_.read_mem(ops_.user, mod_base + rva, buf, lim, &rd) || rd == 0) return false;
        for (size_t k = 0; k < rd; ++k) {
            if (buf[k] == '\0') {
                out->assign(buf, k);
                return true;
            }
        }
        return false;
    };

    int32_t lo = 0, hi = static_cast<int32_t>(n_names) - 1;
    std::string nm;
    while (lo <= hi) {
        int32_t mid = (lo + hi) / 2;
        uint8_t nm_rva_b[4];
        if (!ReadExact(mod_base + rva_nm + static_cast<uint32_t>(mid) * 4u, nm_rva_b, 4)) return 0;
        if (!read_safe_cstr(LoadU32LE(nm_rva_b), &nm)) return 0;
        int cmp = std::strcmp(nm.c_str(), func_name);
        if (cmp == 0) {
            uint8_t ord_b[2];
            if (!ReadExact(mod_base + rva_ord + static_cast<uint32_t>(mid) * 2u, ord_b, 2)) return 0;
            uint16_t ord = LoadU16LE(ord_b);
            if (ord >= n_funcs) return 0;
            uint8_t fn_b[4];
            if (!ReadExact(mod_base + rva_fn + static_cast<uint32_t>(ord) * 4u, fn_b, 4)) return 0;
            uint32_t fn_rva = LoadU32LE(fn_b);
            if (fn_rva == 0 || fn_rva >= img_sz || (fn_rva >= exp_rva && fn_rva < exp_rva + exp_sz)) return 0;
            return mod_base + fn_rva;
        } else if (cmp < 0) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return 0;
}

// =========================================================================
// Hardened 64KB-Chunked Live ModelFile Scanner (TOCTOU-safe)
// =========================================================================
int32_t BridgeEngine::CountLiveModelFileInstancesChunked() const {
    if (!ops_.query_mem || !ops_.read_mem || !ops_.get_exe_base) return 0;
    uint64_t exe_base = ops_.get_exe_base(ops_.user);
    if (exe_base < 0x10000) return 0;

    // 1. Locate UTF-16LE L"モデルファイル\0" in read-only module pages via chunked read_mem
    static const uint16_t kPat[8] = {0x30E2, 0x30C7, 0x30EB, 0x30D5, 0x30A1, 0x30A4, 0x30EB, 0x0000};
    uint8_t hdr[1024];
    size_t got = 0;
    if (!ops_.read_mem(ops_.user, exe_base, hdr, sizeof(hdr), &got) || got < 512) return 0;
    int32_t lf = LoadI32LE(hdr + 0x3C);
    if (lf < 0x40 || lf > 0x380) return 0;
    uint32_t img_sz = std::min<uint32_t>(LoadU32LE(hdr + lf + 0x50), 0x1000000u);

    uint64_t wstr_addr = 0;
    uint64_t cur = exe_base + 0x1000;
    const uint64_t end_img = exe_base + img_sz;
    std::vector<uint8_t> chunk(65536);

    while (cur < end_img && wstr_addr == 0) {
        MemRegionInfo reg{};
        if (!ops_.query_mem(ops_.user, cur, &reg) || reg.region_size == 0) break;
        if (reg.state == 0x1000 && (reg.protect & 0xFFu) == 0x02 && reg.region_size >= 16) {
            uint64_t s0 = std::max(cur, reg.base_addr);
            uint64_t s1 = std::min(end_img, reg.base_addr + reg.region_size);
            for (uint64_t off = s0; off + 16 <= s1 && wstr_addr == 0; off += 65520) {
                size_t req = static_cast<size_t>(std::min<uint64_t>(65536, s1 - off));
                size_t rd = 0;
                if (!ops_.read_mem(ops_.user, off, chunk.data(), req, &rd) || rd < 16) break;
                for (size_t p = 0; p + 16 <= rd; p += 2) {
                    if (std::memcmp(chunk.data() + p, kPat, 16) == 0) {
                        wstr_addr = off + p;
                        break;
                    }
                }
            }
        }
        uint64_t nxt = reg.base_addr + reg.region_size;
        cur = (nxt > cur) ? nxt : (cur + 0x1000);
    }
    if (wstr_addr == 0) return 0;

    // 2. Scan private RW heap regions in 64KB kernel-safe chunks (immune to concurrent VirtualFree/munmap!)
    int32_t found = 0;
    uint64_t addr = 0x10000;
    const uint64_t max_addr = 0x00007FFFFFFF0000ull;
    while (addr < max_addr && found < 16) {
        MemRegionInfo reg{};
        if (!ops_.query_mem(ops_.user, addr, &reg) || reg.region_size == 0) break;
        if (reg.state == 0x1000 && (reg.protect & 0xFFu) == 0x04 && reg.type == 0x20000 &&
            reg.region_size >= 0x120 && reg.region_size <= 0x4000000u) {
            for (uint64_t off = 0; off + 0x110 <= reg.region_size && found < 16; off += 65536) {
                size_t req = static_cast<size_t>(std::min<uint64_t>(65536, reg.region_size - off));
                size_t rd = 0;
                if (!ops_.read_mem(ops_.user, reg.base_addr + off, chunk.data(), req, &rd) || rd < 72) {
                    break;  // Concurrent unmap caught cleanly by kernel read_mem!
                }
                size_t n_qw = rd / 8;
                for (size_t i = 0; i < n_qw; ++i) {
                    if (LoadU64LE(chunk.data() + i * 8) == wstr_addr && (off + i * 8) >= 0x40) {
                        uint64_t inst_addr = reg.base_addr + off + i * 8 - 0x40;
                        uint8_t inst[0x110];
                        if (ReadExact(inst_addr, inst, sizeof(inst))) {
                            uint64_t slen = LoadU64LE(inst + 0xB8);
                            uint64_t scap = LoadU64LE(inst + 0xC0);
                            if (slen >= 4 && slen <= 2048 && scap >= 7 && scap <= 8192 && slen <= scap) {
                                ++found;
                            }
                        }
                    }
                }
            }
        }
        uint64_t nxt = reg.base_addr + reg.region_size;
        addr = (nxt > addr) ? nxt : (addr + 0x1000);
    }
    return found;
}

// =========================================================================
// Tier 1: Official AviUtl2 SDK EDIT_SECTION Resolver + Export Snapshot Cache
// =========================================================================
namespace {

// Exact 18-entry [テキスト] alignment table from aviutl2.exe (VA 0x14053bcd0 / 0x140002240)
// & 7-entry [文字装飾] padding table (VA 0x14053bdf0 / 0x1402670cd).
void ParseTextAlignAndDeco(const std::string& align_raw, const std::string& deco_raw, double fsize,
                           LayerResolveResult* out) {
    if (!out) return;
    out->is_text = true;
    // AviUtl2 default for [テキスト] is index 0: 左寄せ[上] (-0.5, -0.5)
    double fx = -0.5;
    double fy = -0.5;
    std::string a = TrimAscii(align_raw);
    if (!a.empty()) {
        struct AlignEntry { const char* name; double fx; double fy; };
        static const AlignEntry kTable[] = {
            // Canonical 18 entries (Default.aul2 / .aup2 / EDIT_SECTION::get_object_item_value)
            {"左寄せ[上]",     -0.5, -0.5}, {"中央揃え[上]",    0.0, -0.5}, {"右寄せ[上]",     +0.5, -0.5},
            {"左寄せ[中]",     -0.5,  0.0}, {"中央揃え[中]",    0.0,  0.0}, {"右寄せ[中]",     +0.5,  0.0},
            {"左寄せ[下]",     -0.5, +0.5}, {"中央揃え[下]",    0.0, +0.5}, {"右寄せ[下]",     +0.5, +0.5},
            {"縦書 上寄[右]",  +0.5, -0.5}, {"縦書 中央[右]",  +0.5,  0.0}, {"縦書 下寄[右]",  +0.5, +0.5},
            {"縦書 上寄[中]",   0.0, -0.5}, {"縦書 中央[中]",   0.0,  0.0}, {"縦書 下寄[中]",   0.0, +0.5},
            {"縦書 上寄[左]",  -0.5, -0.5}, {"縦書 中央[左]",  -0.5,  0.0}, {"縦書 下寄[左]",  -0.5, +0.5},
            // Simplified/Traditional Chinese UI aliases (defensive fallback)
            {"左对齐[上]",     -0.5, -0.5}, {"居中对齐[上]",    0.0, -0.5}, {"右对齐[上]",     +0.5, -0.5},
            {"左对齐[中]",     -0.5,  0.0}, {"居中对齐[中]",    0.0,  0.0}, {"右对齐[中]",     +0.5,  0.0},
            {"左对齐[下]",     -0.5, +0.5}, {"居中对齐[下]",    0.0, +0.5}, {"右对齐[下]",     +0.5, +0.5},
            {"竖排 上齐[右]",  +0.5, -0.5}, {"竖排 居中[右]",  +0.5,  0.0}, {"竖排 下齐[右]",  +0.5, +0.5},
            {"竖排 上齐[中]",   0.0, -0.5}, {"竖排 居中[中]",   0.0,  0.0}, {"竖排 下齐[中]",   0.0, +0.5},
            {"竖排 上齐[左]",  -0.5, -0.5}, {"竖排 居中[左]",  -0.5,  0.0}, {"竖排 下齐[左]",  -0.5, +0.5},
            {"左對齊[上]",     -0.5, -0.5}, {"置中對齊[上]",    0.0, -0.5}, {"右對齊[上]",     +0.5, -0.5},
            {"左對齊[中]",     -0.5,  0.0}, {"置中對齊[中]",    0.0,  0.0}, {"右對齊[中]",     +0.5,  0.0},
            {"左對齊[下]",     -0.5, +0.5}, {"置中對齊[下]",    0.0, +0.5}, {"右對齊[下]",     +0.5, +0.5},
        };
        bool matched = false;
        for (const auto& e : kTable) {
            if (a == e.name) {
                fx = e.fx;
                fy = e.fy;
                matched = true;
                break;
            }
        }
        if (!matched) {
            char* endp = nullptr;
            long idx = std::strtol(a.c_str(), &endp, 10);
            if (endp && *endp == '\0' && idx >= 0 && idx < 18) {
                fx = kTable[idx].fx;
                fy = kTable[idx].fy;
            }
        }
    }
    out->align_fx = fx;
    out->align_fy = fy;

    double pl = 0.0, pr = 0.0, pt = 0.0, pb = 0.0;
    std::string d = TrimAscii(deco_raw);
    const double sz = (std::isfinite(fsize) && fsize > 1.0) ? fsize : 32.0;
    if (!d.empty() && d != "標準文字" && d != "标准文字" && d != "0") {
        if (d == "影付き文字" || d == "带阴影文字" || d == "1") {
            double s = std::max(1.0, std::floor(sz * 0.06));
            pr = s; pb = s;
        } else if (d == "影付き文字(薄)" || d == "带浅阴影文字" || d == "2") {
            double s = std::max(1.0, std::floor(sz * 0.03));
            pr = s; pb = s;
        } else if (d == "縁取り文字" || d == "描边文字" || d == "3") {
            double p = std::max(1.0, std::floor(sz * 0.15));
            pl = pr = pt = pb = p;
        } else if (d == "縁取り文字(細)" || d == "细描边文字" || d == "4" || d == "縁取り文字(角)" || d == "方角描边文字" || d == "6") {
            double p = std::max(1.0, std::floor(sz * 0.075));
            pl = pr = pt = pb = p;
        } else if (d == "縁取り文字(太)" || d == "粗描边文字" || d == "5") {
            double p = std::max(1.0, std::floor(sz * 0.225));
            pl = pr = pt = pb = p;
        }
    }
    out->pad_l = pl;
    out->pad_r = pr;
    out->pad_t = pt;
    out->pad_b = pb;
}

struct SdkResolveCtx {
    int32_t layer0 = 0;
    int32_t frame0 = 0;
    int32_t scene_id = 0;
    bool executed = false;
    int32_t start_frame = 0;
    int32_t end_frame = 0;
    LayerResolveResult out;
};

void ProcReadLayerSdk(void* param, EDIT_SECTION* edit) {
    auto* ctx = static_cast<SdkResolveCtx*>(param);
    if (!ctx || !edit) return;
    ctx->executed = true;
    // NOTE: Per official plugin2.h, EDIT_SECTION::info is NOT valid in read-only sections.
    // ctx->scene_id is already populated safely from EDIT_HANDLE::get_edit_info (same-thread lock bypass).
    ctx->out.scene_id = ctx->scene_id;
    ctx->out.source_tier = "SDK";

    if (edit->get_layer_enable && !edit->get_layer_enable(ctx->layer0)) {
        ctx->out.status = "HIDDEN";
        ctx->start_frame = 0;
        ctx->end_frame = 100000000;
        return;
    }
    if (!edit->find_object || !edit->get_object_layer_frame) {
        ctx->out.status = "EMPTY";
        return;
    }

    // Walk backward/exact to find object covering frame0:
    // Note: find_object(layer, frame) returns first object at or after `frame`,
    // so check find_object(layer, 0) walking forward if needed to catch clips starting before frame0!
    OBJECT_HANDLE hit_obj = nullptr;
    OBJECT_LAYER_FRAME hit_olf{ctx->layer0, 0, -1};

    int cur_f = 0;
    for (int iter = 0; iter < 512 && cur_f <= ctx->frame0; ++iter) {
        OBJECT_HANDLE o = edit->find_object(ctx->layer0, cur_f);
        if (!o) break;
        OBJECT_LAYER_FRAME olf = edit->get_object_layer_frame(o);
        if (olf.start > ctx->frame0) break;
        if (ctx->frame0 >= olf.start && ctx->frame0 <= olf.end) {
            hit_obj = o;
            hit_olf = olf;
            break;
        }
        cur_f = olf.end + 1;
    }

    if (!hit_obj) {
        ctx->out.status = "EMPTY";
        ctx->start_frame = ctx->frame0;
        ctx->end_frame = ctx->frame0;
        return;
    }

    ctx->start_frame = hit_olf.start;
    ctx->end_frame = hit_olf.end;

    // 1. Check built-in 3D Model [モデルファイル]
    if (edit->get_object_item_value) {
        static const std::wstring kModelFx = Utf8ToSdkWide("モデルファイル");
        static const std::wstring kFileKey = Utf8ToSdkWide("ファイル");
        static const std::wstring kAdjKey  = Utf8ToSdkWide("座標系を調整");
        LPCSTR mpath = edit->get_object_item_value(hit_obj, kModelFx.c_str(), kFileKey.c_str());
        if (mpath && *mpath) {
            ctx->out.status = "OBJ";
            ctx->out.fx_name = "モデルファイル";
            ctx->out.path = mpath;
            LPCSTR adj_s = edit->get_object_item_value(hit_obj, kModelFx.c_str(), kAdjKey.c_str());
            ctx->out.adj = (adj_s && std::atoi(adj_s) != 0) ? 1 : 0;
            return;
        }

        // 2. Check VRMToolkitModel
        static const std::wstring kVrmFx   = Utf8ToSdkWide("VRMToolkitModel");
        static const std::wstring kVrmKey  = Utf8ToSdkWide("VRMファイル");
        LPCSTR vpath = edit->get_object_item_value(hit_obj, kVrmFx.c_str(), kVrmKey.c_str());
        if (vpath && *vpath) {
            ctx->out.status = "VRM";
            ctx->out.fx_name = "VRMToolkitModel";
            ctx->out.path = vpath;
            return;
        }

        // 3. Check @水面 (including official --color extraction unavailable to Lua obj.getvalue!)
        static const std::wstring kWaterFx  = Utf8ToSdkWide("水面");
        static const std::wstring kWaterCol = Utf8ToSdkWide("色");
        LPCSTR wcol = edit->get_object_item_value(hit_obj, kWaterFx.c_str(), kWaterCol.c_str());
        if (wcol && *wcol) {
            ctx->out.status = "WATER";
            ctx->out.fx_name = "水面";
            ctx->out.water_color = ParseHexColorString(wcol);
            return;
        }

        // 4. Check built-in 2D/3D Text [テキスト] alignment & decoration padding (Bug 1 root-cause fix)
        static const std::wstring kTextFx    = Utf8ToSdkWide("テキスト");
        static const std::wstring kTextAlign = Utf8ToSdkWide("文字揃え");
        static const std::wstring kTextDeco  = Utf8ToSdkWide("文字装飾");
        static const std::wstring kTextSize  = Utf8ToSdkWide("サイズ");
        LPCSTR talign = edit->get_object_item_value(hit_obj, kTextFx.c_str(), kTextAlign.c_str());
        if (talign != nullptr) {
            LPCSTR tdeco = edit->get_object_item_value(hit_obj, kTextFx.c_str(), kTextDeco.c_str());
            LPCSTR tsize = edit->get_object_item_value(hit_obj, kTextFx.c_str(), kTextSize.c_str());
            double sz = (tsize && *tsize) ? std::atof(tsize) : 32.0;
            ctx->out.status = "OTHER";
            ctx->out.fx_name = "テキスト";
            ParseTextAlignAndDeco(talign, tdeco ? tdeco : "", sz, &ctx->out);
            return;
        }
    }

    // 5. Parse object alias INI fallback if localized effect name was used
    if (edit->get_object_alias) {
        LPCSTR alias_c = edit->get_object_alias(hit_obj);
        if (alias_c && *alias_c) {
            std::istringstream iss(alias_c);
            std::string ln;
            std::string alias_align, alias_deco;
            double alias_fsize = 32.0;
            bool alias_is_text = false;
            while (std::getline(iss, ln)) {
                std::string s = TrimAscii(ln);
                size_t eq = s.find('=');
                if (eq == std::string::npos) continue;
                std::string k = TrimAscii(s.substr(0, eq));
                std::string v = TrimAscii(s.substr(eq + 1));
                std::string vl = ToLowerAscii(v);
                if (k == "effect.name") {
                    if (ctx->out.fx_name.empty()) ctx->out.fx_name = v;
                    if (v == "テキスト" || v == "文本") alias_is_text = true;
                } else if (k == "文字揃え") {
                    alias_align = v;
                    alias_is_text = true;
                } else if (k == "文字装飾") {
                    alias_deco = v;
                } else if (k == "サイズ" && alias_is_text) {
                    alias_fsize = std::atof(v.c_str());
                } else if (vl.size() >= 4 && vl.substr(vl.size() - 4) == ".obj") {
                    ctx->out.status = "OBJ";
                    ctx->out.path = v;
                    return;
                } else if (vl.size() >= 4 && (vl.substr(vl.size() - 4) == ".vrm" || vl.substr(vl.size() - 4) == ".glb")) {
                    ctx->out.status = "VRM";
                    ctx->out.path = v;
                    return;
                }
            }
            if (alias_is_text) {
                ctx->out.status = "OTHER";
                if (ctx->out.fx_name.empty()) ctx->out.fx_name = "テキスト";
                ParseTextAlignAndDeco(alias_align, alias_deco, alias_fsize, &ctx->out);
                return;
            }
        }
    }

    ctx->out.status = "OTHER";
}

}  // namespace

LayerResolveResult BridgeEngine::ResolveLayerViaSdk(int32_t layer_1based, int32_t frame_0based, bool* sdk_attempted_ok,
                                                    EDIT_SECTION* inline_edit, bool from_script_module) {
    *sdk_attempted_ok = false;
    if (layer_1based < 1) return {};

    int32_t scene_id = status_.active_scene_id;
    if (edit_handle_ && edit_handle_->get_edit_info) {
        // plugin2.h guarantees get_edit_info bypasses locks if current thread already holds one
        EDIT_INFO info{};
        edit_handle_->get_edit_info(&info, sizeof(info));
        scene_id = info.scene_id;
        status_.active_scene_id = scene_id;
    }

    const int32_t layer0 = layer_1based - 1;
    auto cache_store = [&](const SdkResolveCtx& ctx) {
        sdk_clip_cache_.erase(
            std::remove_if(sdk_clip_cache_.begin(), sdk_clip_cache_.end(),
                           [&](const CachedClip& c) {
                               return c.scene_id == ctx.scene_id && c.layer0 == layer0 &&
                                      frame_0based >= c.start_frame && frame_0based <= c.end_frame;
                           }),
            sdk_clip_cache_.end());
        if (sdk_clip_cache_.size() >= 512) sdk_clip_cache_.erase(sdk_clip_cache_.begin());
        sdk_clip_cache_.push_back({ctx.scene_id, layer0, ctx.start_frame, ctx.end_frame, ctx.out});
    };

    // 1. Preferred zero-lock path (AviUtl2 v2.0.54+ / 2005400+):
    //    SCRIPT_MODULE_PARAM::edit is already bound to the active rendering context with ZERO SRWLock contention!
    if (status_.host_version >= 2005400 && inline_edit != nullptr) {
        SdkResolveCtx ctx;
        ctx.layer0 = layer0;
        ctx.frame0 = frame_0based;
        ctx.scene_id = scene_id;
        ProcReadLayerSdk(&ctx, inline_edit);
        if (ctx.executed) {
            *sdk_attempted_ok = true;
            ctx.out.source_tier = "SDK_PARAM_EDIT";
            cache_store(ctx);
            return ctx.out;
        }
    }

    // 2. Lock-free clip snapshot cache (safe during GUI drag on <2005400 AND during EDIT_STATE_SAVE export):
    for (const auto& c : sdk_clip_cache_) {
        if (c.scene_id == scene_id && c.layer0 == layer0 &&
            frame_0based >= c.start_frame && frame_0based <= c.end_frame) {
            *sdk_attempted_ok = true;
            LayerResolveResult r = c.result;
            r.source_tier = "SDK_CACHE";
            return r;
        }
    }

    // 3. NEVER call EDIT_HANDLE::call_read_section_param from inside a script module callback!
    //    During GUI drag/scale, the main thread holds an exclusive edit SRWLock and waits on rendering,
    //    so acquiring a shared read SRWLock on the render thread causes an instant AB-BA deadlock.
    //    Also, call_read_section_param was added in beta42 (2004200), NOT beta31 (2003100).
    if (!from_script_module && edit_handle_ && status_.host_version >= 2004200 && edit_handle_->call_read_section_param) {
        SdkResolveCtx ctx;
        ctx.layer0 = layer0;
        ctx.frame0 = frame_0based;
        ctx.scene_id = scene_id;
        if (edit_handle_->call_read_section_param(&ctx, &ProcReadLayerSdk) && ctx.executed) {
            *sdk_attempted_ok = true;
            status_.active_scene_id = ctx.scene_id;
            cache_store(ctx);
            return ctx.out;
        }
    }

    return {};
}

// =========================================================================
// Tier 2: Verified Read-Only Kernel-Copy Scene-Graph Fallback
// =========================================================================
LayerResolveResult BridgeEngine::ResolveLayerViaRpm(int32_t layer_1based, int32_t frame_0based) {
    LayerResolveResult res;
    res.source_tier = "RPM";
    if (!status_.rpm_allowed || lctx_cands_.empty() || layer_1based < 1) {
        res.status = "UNAVAILABLE";
        return res;
    }

    uint64_t scene_ptr = 0, layers_b = 0, layers_e = 0;
    int32_t ctx_frame = frame_0based, scene_id = 0;

    for (uint64_t cand : lctx_cands_) {
        uint64_t lctx = 0;
        if (!ReadU64(cand, &lctx)) continue;
        for (int depth = 0; lctx >= 0x10000 && depth < 8; ++depth) {
            uint64_t rctx = 0, sptr = 0;
            int32_t cfrm = 0;
            if (ReadU64(lctx + 0x88, &rctx) && ReadU64(rctx, &sptr) && ReadI32(rctx + 0x0C, &cfrm)) {
                if (sptr >= 0x10000 && cfrm >= 0 && cfrm <= 100000000) {
                    uint64_t lb = 0, le = 0;
                    if (ReadU64(sptr + 0x08, &lb) && ReadU64(sptr + 0x10, &le) &&
                        le >= lb && ((le - lb) % 8 == 0)) {
                        uint64_t nl = (le - lb) / 8;
                        if (nl >= 1 && nl <= 4096) {
                            scene_ptr = sptr;
                            ctx_frame = cfrm;
                            layers_b = lb;
                            layers_e = le;
                            ReadI32(sptr, &scene_id);
                            break;
                        }
                    }
                }
            }
            if (!ReadU64(lctx + 0xC8, &lctx)) break;
        }
        if (scene_ptr != 0) break;
    }

    if (scene_ptr == 0) return res;
    status_.active_scene_id = scene_id;
    res.scene_id = scene_id;

    uint64_t n_layers = (layers_e - layers_b) / 8;
    if (static_cast<uint64_t>(layer_1based) > n_layers) {
        res.status = "EMPTY";
        return res;
    }
    uint64_t layer_ptr = 0;
    if (!ReadU64(layers_b + static_cast<uint64_t>(layer_1based - 1) * 8u, &layer_ptr) || layer_ptr < 0x10000) {
        res.status = "EMPTY";
        return res;
    }
    uint64_t lmeta = 0;
    if (ReadU64(layer_ptr + 0x20, &lmeta) && lmeta >= 0x10000) {
        uint8_t hid = 0;
        if (ReadU8(lmeta + 0x10, &hid) && hid != 0) {
            res.status = "HIDDEN";
            return res;
        }
    }

    uint64_t ob = 0, oe = 0;
    if (!ReadU64(layer_ptr, &ob) || !ReadU64(layer_ptr + 0x08, &oe) || oe < ob || ((oe - ob) % 16 != 0)) {
        return res;
    }
    int64_t n_objs = static_cast<int64_t>((oe - ob) / 16);
    if (n_objs <= 0) {
        res.status = "EMPTY";
        return res;
    }

    int32_t tf = ctx_frame;
    int64_t lo = 0, cnt = n_objs;
    while (cnt > 0) {
        int64_t step = cnt / 2;
        int64_t mid = lo + step;
        uint64_t eptr = 0;
        int32_t fend = 0;
        if (!ReadU64(ob + static_cast<uint64_t>(mid) * 16u, &eptr) || !ReadI32(eptr + 0x54, &fend)) {
            return res;
        }
        if (fend < tf) {
            lo = mid + 1;
            cnt -= step + 1;
        } else {
            cnt = step;
        }
    }
    if (lo >= n_objs) {
        res.status = "EMPTY";
        return res;
    }

    uint64_t eobj = 0;
    int32_t fstart = 0, fend = 0;
    if (!ReadU64(ob + static_cast<uint64_t>(lo) * 16u, &eobj) || eobj < 0x10000 ||
        !ReadI32(eobj + 0x50, &fstart) || !ReadI32(eobj + 0x54, &fend) || tf < fstart || tf > fend) {
        res.status = "EMPTY";
        return res;
    }

    uint64_t fxb = 0, fxe = 0, fx0 = 0;
    if (!ReadU64(eobj + 0x18, &fxb) || !ReadU64(eobj + 0x20, &fxe) || fxe <= fxb ||
        !ReadU64(fxb, &fx0) || fx0 < 0x10000) {
        res.status = "EMPTY";
        return res;
    }

    uint8_t fx_dis = 0;
    ReadU8(fx0 + 0x5A, &fx_dis);
    uint64_t nm_ptr = 0;
    std::string fx_name;
    if (!ReadU64(fx0 + 0x40, &nm_ptr) || !ReadWString(nm_ptr, 96, &fx_name) || fx_name.empty()) {
        return res;
    }
    res.fx_name = fx_name;
    if (fx_dis != 0) {
        res.status = "DISABLED";
        return res;
    }

    if (fx_name == "モデルファイル") {
        uint64_t slen = 0, scap = 0;
        uint8_t adj_b = 0;
        ReadU64(fx0 + 0xB8, &slen);
        ReadU64(fx0 + 0xC0, &scap);
        ReadU8(fx0 + 0x108, &adj_b);
        if (slen >= 1 && slen <= 2048 && scap >= 7 && scap <= 8192 && slen <= scap) {
            uint64_t src_waddr = 0;
            bool ok_a = (scap < 8) ? ((src_waddr = fx0 + 0xA8), true) : ReadU64(fx0 + 0xA8, &src_waddr);
            if (ok_a && src_waddr >= 0x10000) {
                std::vector<uint16_t> warr(static_cast<size_t>(slen) + 1, 0);
                if (ReadExact(src_waddr, warr.data(), static_cast<size_t>(slen) * 2u)) {
                    res.status = "OBJ";
                    res.path = Utf16LeToUtf8(warr.data(), static_cast<size_t>(slen));
                    res.adj = (adj_b != 0) ? 1 : 0;
                    return res;
                }
            }
        }
        res.status = "EMPTY";
        return res;
    }

    res.status = "OTHER";
    return res;
}

LayerResolveResult BridgeEngine::ResolveLayer(int32_t layer_1based, int32_t frame_0based,
                                              EDIT_SECTION* inline_edit, bool from_script_module) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    bool sdk_ok = false;
    LayerResolveResult r = ResolveLayerViaSdk(layer_1based, frame_0based, &sdk_ok, inline_edit, from_script_module);
    if (sdk_ok) return r;
    return ResolveLayerViaRpm(layer_1based, frame_0based);
}

int32_t BridgeEngine::GetLayerWaterColor(int32_t layer_1based, int32_t frame_0based,
                                         EDIT_SECTION* inline_edit, bool from_script_module) {
    LayerResolveResult r = ResolveLayer(layer_1based, frame_0based, inline_edit, from_script_module);
    return r.water_color;
}

EditCameraResult BridgeEngine::ReadEditCamera() {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    EditCameraResult out;
    if (!status_.rpm_allowed || !edit_cam_sig_found_) return out;

    uint64_t app_ptr = 0, win_ptr = 0;
    if (!ReadU64(edit_cam_g_addr_, &app_ptr) || app_ptr < 0x10000) return out;
    if (!ReadU64(app_ptr + edit_cam_off_w_, &win_ptr) || win_ptr < 0x10000) return out;

    float fp[14];
    if (!ReadExact(win_ptr + edit_cam_off_c_, fp, sizeof(fp))) return out;
    if (!std::isfinite(fp[13]) || fp[13] <= 1.0f || fp[13] >= 1.0e7f) return out;
    for (int i = 0; i < 14; ++i) {
        if (!std::isfinite(fp[i])) return out;
    }
    out.ok = true;
    out.x = fp[0]; out.y = fp[1]; out.z = fp[2];
    out.tx = fp[4]; out.ty = fp[5]; out.tz = fp[6];
    out.ux = -fp[8]; out.uy = -fp[9]; out.uz = -fp[10];
    out.rz = fp[12]; out.d = fp[13];
    return out;
}

// =========================================================================
// ReadCameraLights: 100% READ-ONLY (Zero Host GBuffer Writes!)
// =========================================================================
CameraLightsResult BridgeEngine::ReadCameraLights() {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    CameraLightsResult out;
    if (!status_.rpm_allowed || lctx_cands_.empty()) return out;

    uint64_t gbuf_addr = 0, lb_addr = 0, le_addr = 0;
    for (uint64_t cand : lctx_cands_) {
        uint64_t lctx = 0;
        if (!ReadU64(cand, &lctx)) continue;
        for (int depth = 0; lctx >= 0x10000 && depth < 8; ++depth) {
            uint64_t rctx = 0, gb = 0;
            if (ReadU64(lctx + 0x88, &rctx) && ReadU64(rctx + 0xD0, &gb) && gb >= 0x10000) {
                uint64_t lb = 0, le = 0, lc = 0;
                if (ReadU64(gb + 0x98, &lb) && ReadU64(gb + 0xA0, &le) && ReadU64(gb + 0xA8, &lc)) {
                    if (le >= lb && lc >= le) {
                        uint64_t diff = le - lb;
                        if (diff == 0 || (lb >= 0x10000 && (diff % 64 == 0) && diff <= 4096)) {
                            gbuf_addr = gb; lb_addr = lb; le_addr = le;
                            break;
                        }
                    }
                }
            }
            if (!ReadU64(lctx + 0xC8, &lctx)) break;
        }
        if (gbuf_addr != 0) break;
    }

    if (gbuf_addr == 0 || le_addr <= lb_addr) return out;
    const size_t n = static_cast<size_t>((le_addr - lb_addr) / 64);
    double unlit = 1.0, ar = 0.0, ag = 0.0, ab = 0.0, sum_dir = 0.0, best_score = -1.0;

    for (size_t i = 0; i < n; ++i) {
        uint8_t raw[64];
        if (!ReadExact(lb_addr + i * 64u, raw, 64)) continue;
        int32_t ltype = LoadI32LE(raw);
        float f[16];
        std::memcpy(f, raw, 64);
        if (ltype < 0 || ltype > 5) continue;
        if (!std::isfinite(f[4]) || !std::isfinite(f[5]) || !std::isfinite(f[6])) continue;
        if (f[4] < 0.0f || f[4] > 100.0f || f[5] < 0.0f || f[5] > 100.0f || f[6] < 0.0f || f[6] > 100.0f) continue;

        out.count += 1;
        double um = std::isfinite(f[7]) ? std::clamp<double>(f[7], 0.0, 1.0) : 1.0;
        unlit *= um;
        double lum = std::max({static_cast<double>(f[4]), static_cast<double>(f[5]), static_cast<double>(f[6])});
        if (ltype == 0) {
            ar += f[4]; ag += f[5]; ab += f[6];
        } else {
            sum_dir += lum;
            if (lum > best_score) {
                best_score = lum;
                out.has_primary = true;
                out.ltype = ltype;
                out.dx = f[1]; out.dy = f[2]; out.dz = f[3];
                out.r = f[4]; out.g = f[5]; out.b = f[6]; out.lum = lum;
                out.px = f[12]; out.py = f[13]; out.pz = f[14];
                out.cos_ang = f[15];
            }
        }
    }

    out.unlit = unlit;
    out.amb_r = ar; out.amb_g = ag; out.amb_b = ab;
    out.peak_gain = unlit + std::max({ar, ag, ab}) + sum_dir;
    // STRICT POLICY: ZERO WRITES TO gbuf_addr + 0xA0!
    return out;
}

EditCameraResult BridgeEngine::ReadSceneCameraViaSdk(int32_t max_layer_1based, int32_t frame_0based,
                                                     EDIT_SECTION* inline_edit, bool from_script_module) {
    std::lock_guard<std::mutex> lk(g_bridge_mutex);
    EditCameraResult out;
    if (max_layer_1based < 1) return out;

    struct CamCtx {
        int32_t max_l0 = 0;
        int32_t frame0 = 0;
        double scr_h = 1080.0;
        EditCameraResult cam;
    } ctx{std::min<int32_t>(max_layer_1based - 1, 128), frame_0based, 1080.0, {}};
    if (edit_handle_ && edit_handle_->get_edit_info) {
        EDIT_INFO ei{};
        edit_handle_->get_edit_info(&ei, sizeof(ei));
        if (ei.height >= 16) ctx.scr_h = static_cast<double>(ei.height);
    }

    auto cb = [](void* p, EDIT_SECTION* edit) {
        auto* c = static_cast<CamCtx*>(p);
        if (!c || !edit || !edit->find_object || !edit->get_object_layer_frame || !edit->get_object_item_value) return;
        static const std::wstring kCamFx = Utf8ToSdkWide("カメラ制御");
        static const std::wstring kStdFx = Utf8ToSdkWide("標準描画");
        static const std::wstring kX = Utf8ToSdkWide("X"), kY = Utf8ToSdkWide("Y"), kZ = Utf8ToSdkWide("Z");
        static const std::wstring kTX = Utf8ToSdkWide("目標X"), kTY = Utf8ToSdkWide("目標Y"), kTZ = Utf8ToSdkWide("目標Z");
        static const std::wstring kTL = Utf8ToSdkWide("目標レイヤー");
        static const std::wstring kRZ = Utf8ToSdkWide("回転"), kFOV = Utf8ToSdkWide("視野角");
        for (int l0 = c->max_l0; l0 >= 0; --l0) {
            if (edit->get_layer_enable && !edit->get_layer_enable(l0)) continue;
            int cur_f = 0;
            for (int it = 0; it < 256 && cur_f <= c->frame0; ++it) {
                OBJECT_HANDLE o = edit->find_object(l0, cur_f);
                if (!o) break;
                OBJECT_LAYER_FRAME olf = edit->get_object_layer_frame(o);
                if (olf.start > c->frame0) break;
                if (c->frame0 >= olf.start && c->frame0 <= olf.end) {
                    LPCSTR sx = edit->get_object_item_value(o, kCamFx.c_str(), kX.c_str());
                    LPCSTR sz = edit->get_object_item_value(o, kCamFx.c_str(), kZ.c_str());
                    if (sx && sz) {
                        auto rd = [&](const std::wstring& k, double def) {
                            LPCSTR v = edit->get_object_item_value(o, kCamFx.c_str(), k.c_str());
                            return (v && *v) ? std::atof(v) : def;
                        };
                        c->cam.ok = true;
                        c->cam.x = rd(kX, 0.0);
                        c->cam.y = rd(kY, 0.0);
                        c->cam.z = rd(kZ, -1024.0);
                        c->cam.tx = rd(kTX, 0.0);
                        c->cam.ty = rd(kTY, 0.0);
                        c->cam.tz = rd(kTZ, 0.0);
                        c->cam.rz = rd(kRZ, 0.0);
                        double fov_deg = rd(kFOV, 0.0);
                        if (fov_deg > 1.0 && fov_deg < 179.0) {
                            constexpr double kDeg2Rad = 3.14159265358979323846 / 180.0;
                            c->cam.d = (c->scr_h * 0.5) / std::tan(fov_deg * 0.5 * kDeg2Rad);
                        } else {
                            c->cam.d = 1024.0;
                        }
                        // Official Default.aul2 [Tips.カメラ制御]:
                        // 目標レイヤー pointing to camera layer itself => camera position is target origin;
                        // pointing to another layer => add that layer's standard X/Y/Z.
                        LPCSTR stl = edit->get_object_item_value(o, kCamFx.c_str(), kTL.c_str());
                        int tl_1b = (stl && *stl) ? std::atoi(stl) : 0;
                        if (tl_1b == l0 + 1) {
                            c->cam.tx += c->cam.x;
                            c->cam.ty += c->cam.y;
                            c->cam.tz += c->cam.z;
                        } else if (tl_1b >= 1 && tl_1b <= 1024) {
                            int tl0 = tl_1b - 1;
                            int tf = 0;
                            for (int tit = 0; tit < 256 && tf <= c->frame0; ++tit) {
                                OBJECT_HANDLE to = edit->find_object(tl0, tf);
                                if (!to) break;
                                OBJECT_LAYER_FRAME tolf = edit->get_object_layer_frame(to);
                                if (tolf.start > c->frame0) break;
                                if (c->frame0 >= tolf.start && c->frame0 <= tolf.end) {
                                    auto rstd = [&](const std::wstring& k) {
                                        LPCSTR v = edit->get_object_item_value(to, kStdFx.c_str(), k.c_str());
                                        return (v && *v) ? std::atof(v) : 0.0;
                                    };
                                    c->cam.tx += rstd(kX);
                                    c->cam.ty += rstd(kY);
                                    c->cam.tz += rstd(kZ);
                                    break;
                                }
                                tf = tolf.end + 1;
                            }
                        }
                        return;
                    }
                    break;
                }
                cur_f = olf.end + 1;
            }
        }
    };
    if (status_.host_version >= 2005400 && inline_edit != nullptr) {
        cb(&ctx, inline_edit);
        return ctx.cam;
    }
    if (!from_script_module && status_.host_version >= 2004200 && edit_handle_ && edit_handle_->call_read_section_param) {
        edit_handle_->call_read_section_param(&ctx, cb);
    }
    return ctx.cam;
}

// =========================================================================
// Official AviUtl2 SCRIPT_MODULE_TABLE Callbacks (`obj.module("VL3DBridge")`)
// =========================================================================
BridgeEngine& GetGlobalBridgeEngine() {
    static BridgeEngine s_engine;
    return s_engine;
}

namespace {

void ModGetStatus(SCRIPT_MODULE_PARAM* p) {
    if (!p) return;
    HostStatus st = GetGlobalBridgeEngine().GetStatus();
    if (p->push_result_boolean) p->push_result_boolean(st.initialized);
    if (p->push_result_boolean) p->push_result_boolean(st.sdk_ready);
    if (p->push_result_boolean) p->push_result_boolean(st.rpm_allowed);
    if (p->push_result_boolean) p->push_result_boolean(st.whitelist_hit);
    if (p->push_result_int) p->push_result_int(static_cast<int>(st.host_version));
    if (p->push_result_int) p->push_result_int(static_cast<int>(st.pe_timestamp));
    if (p->push_result_int) p->push_result_int(static_cast<int>(st.image_size));
    if (p->push_result_int) p->push_result_int(st.active_scene_id);
    if (p->push_result_string) p->push_result_string(st.mode.c_str());
    if (p->push_result_string) p->push_result_string(st.sha256_hex.c_str());
}

void ModResolveLayer(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_int) return;
    int layer_1based = p->get_param_int(0);
    int frame_0based = (p->get_param_num && p->get_param_num() >= 2) ? p->get_param_int(1) : 0;
    EDIT_SECTION* inline_edit = (GetGlobalBridgeEngine().GetStatus().host_version >= 2005400) ? p->edit : nullptr;
    LayerResolveResult r = GetGlobalBridgeEngine().ResolveLayer(layer_1based, frame_0based, inline_edit, true);
    if (p->push_result_string) p->push_result_string(r.status.c_str());
    if (p->push_result_string) p->push_result_string(r.fx_name.c_str());
    if (p->push_result_string) p->push_result_string(r.path.c_str());
    if (p->push_result_int) p->push_result_int(r.adj);
    if (p->push_result_int) p->push_result_int(r.scene_id);
    if (p->push_result_int) p->push_result_int(r.water_color);
    if (p->push_result_string) p->push_result_string(r.source_tier.c_str());
    // v1.21 trailing metadata for built-in [テキスト] alignment & padding (backward compatible)
    if (p->push_result_boolean) p->push_result_boolean(r.is_text);
    if (p->push_result_double) {
        p->push_result_double(r.align_fx);
        p->push_result_double(r.align_fy);
        p->push_result_double(r.pad_l);
        p->push_result_double(r.pad_r);
        p->push_result_double(r.pad_t);
        p->push_result_double(r.pad_b);
    }
}

void ModGetWaterColor(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_int || !p->push_result_int) return;
    int layer_1based = p->get_param_int(0);
    int frame_0based = (p->get_param_num && p->get_param_num() >= 2) ? p->get_param_int(1) : 0;
    EDIT_SECTION* inline_edit = (GetGlobalBridgeEngine().GetStatus().host_version >= 2005400) ? p->edit : nullptr;
    p->push_result_int(GetGlobalBridgeEngine().GetLayerWaterColor(layer_1based, frame_0based, inline_edit, true));
}

void ModGetEditCamera(SCRIPT_MODULE_PARAM* p) {
    if (!p) return;
    EditCameraResult c = GetGlobalBridgeEngine().ReadEditCamera();
    if (p->push_result_boolean) p->push_result_boolean(c.ok);
    if (!c.ok || !p->push_result_double) return;
    p->push_result_double(c.x); p->push_result_double(c.y); p->push_result_double(c.z);
    p->push_result_double(c.tx); p->push_result_double(c.ty); p->push_result_double(c.tz);
    p->push_result_double(c.ux); p->push_result_double(c.uy); p->push_result_double(c.uz);
    p->push_result_double(c.rz); p->push_result_double(c.d);
}

void ModGetCameraLights(SCRIPT_MODULE_PARAM* p) {
    if (!p) return;
    CameraLightsResult l = GetGlobalBridgeEngine().ReadCameraLights();
    if (p->push_result_int) p->push_result_int(l.count);
    if (p->push_result_double) {
        p->push_result_double(l.unlit);
        p->push_result_double(l.amb_r);
        p->push_result_double(l.amb_g);
        p->push_result_double(l.amb_b);
        p->push_result_double(l.peak_gain);
    }
    if (p->push_result_boolean) p->push_result_boolean(l.has_primary);
    if (l.has_primary && p->push_result_int && p->push_result_double) {
        p->push_result_int(l.ltype);
        p->push_result_double(l.dx); p->push_result_double(l.dy); p->push_result_double(l.dz);
        p->push_result_double(l.r);  p->push_result_double(l.g);  p->push_result_double(l.b);
        p->push_result_double(l.lum);
        p->push_result_double(l.px); p->push_result_double(l.py); p->push_result_double(l.pz);
        p->push_result_double(l.cos_ang);
    }
}

void ModGetSceneCamera(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_int) return;
    int max_l = p->get_param_int(0);
    int frame0 = (p->get_param_num && p->get_param_num() >= 2) ? p->get_param_int(1) : 0;
    EDIT_SECTION* inline_edit = (GetGlobalBridgeEngine().GetStatus().host_version >= 2005400) ? p->edit : nullptr;
    EditCameraResult c = GetGlobalBridgeEngine().ReadSceneCameraViaSdk(max_l, frame0, inline_edit, true);
    if (p->push_result_boolean) p->push_result_boolean(c.ok);
    if (!c.ok || !p->push_result_double) return;
    p->push_result_double(c.x); p->push_result_double(c.y); p->push_result_double(c.z);
    p->push_result_double(c.tx); p->push_result_double(c.ty); p->push_result_double(c.tz);
    p->push_result_double(c.ux); p->push_result_double(c.uy); p->push_result_double(c.uz);
    p->push_result_double(c.rz); p->push_result_double(c.d);
}

void ModWallTimeSec(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->push_result_double) return;
    static const auto t0 = std::chrono::steady_clock::now();
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    p->push_result_double(sec);
}

void ModGetGpuStatus(SCRIPT_MODULE_PARAM* p) {
    if (!p) return;
    GpuRasterStatus gs = GetGlobalBridgeEngine().GetGpuRasterStatus();
    if (p->push_result_boolean) p->push_result_boolean(gs.d3d11_ready);
    if (p->push_result_boolean) p->push_result_boolean(gs.d3d11_available);
    if (p->push_result_string) p->push_result_string(gs.backend.c_str());
    if (p->push_result_int) p->push_result_int(gs.cached_meshes);
    if (p->push_result_double) p->push_result_double(static_cast<double>(gs.gpu_bytes));
    if (p->push_result_string) p->push_result_string(gs.last_error.c_str());
}

void ModHasMesh(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_string || !p->push_result_int) return;
    const char* key = p->get_param_string(0);
    const char* rev = (p->get_param_num && p->get_param_num() >= 2) ? p->get_param_string(1) : "";
    int32_t mid = GetGlobalBridgeEngine().HasMesh(key ? key : "", rev ? rev : "");
    p->push_result_int(mid);
}

void ModUploadMesh(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_string || !p->get_param_int || !p->push_result_int) return;
    if (p->get_param_num && p->get_param_num() < 6) {
        p->push_result_int(0);
        return;
    }
    const char* key = p->get_param_string(0);
    const char* rev = p->get_param_string(1);
    int32_t nv = p->get_param_int(2);
    int32_t nt = p->get_param_int(3);
    const char* vb64 = p->get_param_string(4);
    const char* tb64 = p->get_param_string(5);
    int32_t mid = GetGlobalBridgeEngine().UploadMeshBase64(
        key ? key : "", rev ? rev : "", nv, nt, vb64, tb64);
    p->push_result_int(mid);
}

std::vector<RastModelInstance> ReadInstancesFromParamArray(SCRIPT_MODULE_PARAM* p, int arg_idx) {
    std::vector<RastModelInstance> out;
    if (!p || !p->get_param_array_num || !p->get_param_array_double) return out;
    const int n_vals = p->get_param_array_num(arg_idx);
    // Each instance is packed as 14 numbers:
    // [0]=mesh_id, [1]=is_vrm(0/1), [2..4]=ox,oy,oz, [5..7]=r0x,r0y,r0z, [8..10]=d0x,d0y,d0z, [11..13]=n0x,n0y,n0z
    const int n_inst = n_vals / 14;
    if (n_inst <= 0) return out;
    out.reserve(static_cast<size_t>(std::min(n_inst, 64)));
    for (int i = 0; i < n_inst && i < 64; ++i) {
        const int b = i * 14;
        RastModelInstance m{};
        m.mesh_id = static_cast<int32_t>(std::llround(p->get_param_array_double(arg_idx, b + 0)));
        m.is_vrm  = (p->get_param_array_double(arg_idx, b + 1) > 0.5);
        m.ox  = static_cast<float>(p->get_param_array_double(arg_idx, b + 2));
        m.oy  = static_cast<float>(p->get_param_array_double(arg_idx, b + 3));
        m.oz  = static_cast<float>(p->get_param_array_double(arg_idx, b + 4));
        m.r0x = static_cast<float>(p->get_param_array_double(arg_idx, b + 5));
        m.r0y = static_cast<float>(p->get_param_array_double(arg_idx, b + 6));
        m.r0z = static_cast<float>(p->get_param_array_double(arg_idx, b + 7));
        m.d0x = static_cast<float>(p->get_param_array_double(arg_idx, b + 8));
        m.d0y = static_cast<float>(p->get_param_array_double(arg_idx, b + 9));
        m.d0z = static_cast<float>(p->get_param_array_double(arg_idx, b + 10));
        m.n0x = static_cast<float>(p->get_param_array_double(arg_idx, b + 11));
        m.n0y = static_cast<float>(p->get_param_array_double(arg_idx, b + 12));
        m.n0z = static_cast<float>(p->get_param_array_double(arg_idx, b + 13));
        if (m.mesh_id > 0) out.push_back(m);
    }
    return out;
}

void ModRasterizeShadow(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_data || !p->get_param_int ||
        !p->get_param_array_num || !p->get_param_array_double) {
        if (p && p->push_result_boolean) p->push_result_boolean(false);
        return;
    }
    if (p->get_param_num && p->get_param_num() < 4) {
        if (p->push_result_boolean) p->push_result_boolean(false);
        return;
    }
    void* dst = p->get_param_data(0);
    int32_t sh_res = p->get_param_int(1);
    const int n_p = p->get_param_array_num(2);
    if (!dst || n_p < 21) {
        if (p->push_result_boolean) p->push_result_boolean(false);
        return;
    }

    RastShadowParams sp{};
    sp.wx        = static_cast<float>(p->get_param_array_double(2, 0));
    sp.wy        = static_cast<float>(p->get_param_array_double(2, 1));
    sp.wz        = static_cast<float>(p->get_param_array_double(2, 2));
    sp.Hx        = static_cast<float>(p->get_param_array_double(2, 3));
    sp.Hy        = static_cast<float>(p->get_param_array_double(2, 4));
    sp.Wrx       = static_cast<float>(p->get_param_array_double(2, 5));
    sp.Wry       = static_cast<float>(p->get_param_array_double(2, 6));
    sp.Wrz       = static_cast<float>(p->get_param_array_double(2, 7));
    sp.Wdx       = static_cast<float>(p->get_param_array_double(2, 8));
    sp.Wdy       = static_cast<float>(p->get_param_array_double(2, 9));
    sp.Wdz       = static_cast<float>(p->get_param_array_double(2, 10));
    sp.Wnx       = static_cast<float>(p->get_param_array_double(2, 11));
    sp.Wny       = static_cast<float>(p->get_param_array_double(2, 12));
    sp.Wnz       = static_cast<float>(p->get_param_array_double(2, 13));
    sp.mode_kind = static_cast<float>(p->get_param_array_double(2, 14));
    sp.sx_p      = static_cast<float>(p->get_param_array_double(2, 15));
    sp.sy_p      = static_cast<float>(p->get_param_array_double(2, 16));
    sp.sz_p      = static_cast<float>(p->get_param_array_double(2, 17));
    sp.u_wn      = static_cast<float>(p->get_param_array_double(2, 18));
    sp.dS_wn     = static_cast<float>(p->get_param_array_double(2, 19));
    sp.L_tot     = static_cast<float>(p->get_param_array_double(2, 20));
    sp.splat_cap = (n_p >= 22) ? static_cast<int32_t>(std::llround(p->get_param_array_double(2, 21))) : 2048;

    std::vector<RastModelInstance> insts = ReadInstancesFromParamArray(p, 3);
    RastOutput r = GetGlobalBridgeEngine().RasterizeShadow(dst, sh_res, sp, insts);
    if (p->push_result_boolean) p->push_result_boolean(r.ok);
    if (p->push_result_int) p->push_result_int(r.tris_drawn);
    if (p->push_result_boolean) p->push_result_boolean(r.any_pixel);
    if (p->push_result_string) p->push_result_string(r.backend.c_str());
}

void ModRasterizeCamDepth(SCRIPT_MODULE_PARAM* p) {
    if (!p || !p->get_param_data || !p->get_param_int ||
        !p->get_param_array_num || !p->get_param_array_double) {
        if (p && p->push_result_boolean) p->push_result_boolean(false);
        return;
    }
    if (p->get_param_num && p->get_param_num() < 5) {
        if (p->push_result_boolean) p->push_result_boolean(false);
        return;
    }
    void* dst = p->get_param_data(0);
    int32_t cm_w = p->get_param_int(1);
    int32_t cm_h = p->get_param_int(2);
    const int n_p = p->get_param_array_num(3);
    if (!dst || n_p < 16) {
        if (p->push_result_boolean) p->push_result_boolean(false);
        return;
    }

    RastCamDepthParams cp{};
    cp.scr_w   = static_cast<float>(p->get_param_array_double(3, 0));
    cp.scr_h   = static_cast<float>(p->get_param_array_double(3, 1));
    cp.cam_x   = static_cast<float>(p->get_param_array_double(3, 2));
    cp.cam_y   = static_cast<float>(p->get_param_array_double(3, 3));
    cp.cam_z   = static_cast<float>(p->get_param_array_double(3, 4));
    cp.focal_d = static_cast<float>(p->get_param_array_double(3, 5));
    cp.f_x     = static_cast<float>(p->get_param_array_double(3, 6));
    cp.f_y     = static_cast<float>(p->get_param_array_double(3, 7));
    cp.f_z     = static_cast<float>(p->get_param_array_double(3, 8));
    cp.r_x     = static_cast<float>(p->get_param_array_double(3, 9));
    cp.r_y     = static_cast<float>(p->get_param_array_double(3, 10));
    cp.r_z     = static_cast<float>(p->get_param_array_double(3, 11));
    cp.d_x     = static_cast<float>(p->get_param_array_double(3, 12));
    cp.d_y     = static_cast<float>(p->get_param_array_double(3, 13));
    cp.d_z     = static_cast<float>(p->get_param_array_double(3, 14));
    cp.max_z   = static_cast<float>(p->get_param_array_double(3, 15));

    std::vector<RastModelInstance> insts = ReadInstancesFromParamArray(p, 4);
    RastOutput r = GetGlobalBridgeEngine().RasterizeCamDepth(dst, cm_w, cm_h, cp, insts);
    if (p->push_result_boolean) p->push_result_boolean(r.ok);
    if (p->push_result_int) p->push_result_int(r.tris_drawn);
    if (p->push_result_boolean) p->push_result_boolean(r.any_pixel);
    if (p->push_result_string) p->push_result_string(r.backend.c_str());
}

SCRIPT_MODULE_FUNCTION g_functions[] = {
    {L"get_status",         &ModGetStatus},
    {L"resolve_layer",      &ModResolveLayer},
    {L"get_water_color",    &ModGetWaterColor},
    {L"get_edit_camera",    &ModGetEditCamera},
    {L"get_camera_lights",  &ModGetCameraLights},
    {L"get_scene_camera",   &ModGetSceneCamera},
    {L"wall_time_sec",      &ModWallTimeSec},
    {L"get_gpu_status",     &ModGetGpuStatus},
    {L"has_mesh",           &ModHasMesh},
    {L"upload_mesh",        &ModUploadMesh},
    {L"rasterize_shadow",   &ModRasterizeShadow},
    {L"rasterize_camdepth", &ModRasterizeCamDepth},
    {nullptr,               nullptr},
};

SCRIPT_MODULE_TABLE g_module_table = {
    L"VL3DBridge v1.21 (Lock-Free Read-Only Bridge + D3D11 CS 3D Shadow Rasterizer for Volumetric Light 3D)",
    g_functions,
};

}  // namespace

SCRIPT_MODULE_TABLE* GetGlobalBridgeScriptModuleTable() {
    return &g_module_table;
}

}  // namespace vl3d
