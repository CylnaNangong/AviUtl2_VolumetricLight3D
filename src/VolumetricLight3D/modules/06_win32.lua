-- =====================================================================
-- 共有 LuaJIT VM 無衝突 Win32 UTF-16LE ファイル／メモリエンジン
-- （ffi.C の既存定義と一切衝突しない GetProcAddress + ffi.typeof 直結方式）
-- =====================================================================
-- Win32 MultiByteToWideChar persisted in _VL_PERSIST.w32_mb2wc_fn

local function utf8_to_utf16le_array(ffi, s)
    local n = #s
    if n == 0 then
        local z = ffi.new("uint16_t[1]")
        z[0] = 0
        return z, 0
    end
    local mb2wc = _VL_PERSIST.w32_mb2wc_fn
    if type(_G.VL3D_TEST_W32_API) == "table" and _G.VL3D_TEST_W32_API.MultiByteToWideChar ~= nil then
        mb2wc = _G.VL3D_TEST_W32_API.MultiByteToWideChar
    end
    if mb2wc ~= nil then
        -- 1) 厳密 UTF-8 (CP_UTF8=65001, MB_ERR_INVALID_CHARS=8) で変換
        local req = tonumber(mb2wc(65001, 8, s, n, nil, 0)) or 0
        if req > 0 then
            local out = ffi.new("uint16_t[?]", req + 1)
            local got = tonumber(mb2wc(65001, 8, s, n, out, req)) or 0
            if got > 0 then
                out[got] = 0
                return out, got
            end
        end
        -- 2) 非 UTF-8 バイト列（AviUtl2 .log の GBK/CP936・Shift-JIS/CP932 等）は CP_ACP=0 で無欠損変換！
        local req_acp = tonumber(mb2wc(0, 0, s, n, nil, 0)) or 0
        if req_acp > 0 then
            local out = ffi.new("uint16_t[?]", req_acp + 1)
            local got_acp = tonumber(mb2wc(0, 0, s, n, out, req_acp)) or 0
            if got_acp > 0 then
                out[got_acp] = 0
                return out, got_acp
            end
        end
    end
    local out = ffi.new("uint16_t[?]", n + 2)
    local wi = 0
    local i = 1
    while i <= n do
        local b1 = s:byte(i)
        local cp
        if b1 < 0x80 then
            cp = b1
            i = i + 1
        elseif b1 >= 0xC2 and b1 < 0xE0 and i + 1 <= n then
            local b2 = s:byte(i + 1)
            cp = (b1 - 0xC0) * 64 + (b2 - 0x80)
            i = i + 2
        elseif b1 >= 0xE0 and b1 < 0xF0 and i + 2 <= n then
            local b2, b3 = s:byte(i + 1, i + 2)
            cp = (b1 - 0xE0) * 4096 + (b2 - 0x80) * 64 + (b3 - 0x80)
            i = i + 3
        elseif b1 >= 0xF0 and b1 <= 0xF4 and i + 3 <= n then
            local b2, b3, b4 = s:byte(i + 1, i + 3)
            cp = (b1 - 0xF0) * 262144 + (b2 - 0x80) * 4096 + (b3 - 0x80) * 64 + (b4 - 0x80)
            i = i + 4
        else
            cp = b1
            i = i + 1
        end
        if cp >= 0x10000 and cp <= 0x10FFFF then
            local u = cp - 0x10000
            out[wi]     = 0xD800 + math.floor(u / 1024)
            out[wi + 1] = 0xDC00 + (u % 1024)
            wi = wi + 2
        else
            out[wi] = cp % 65536
            wi = wi + 1
        end
    end
    out[wi] = 0
    return out, wi
end

local function utf16le_ptr_to_utf8(wptr, max_units)
    local lim = max_units or 260
    local chars = {}
    local ci = 0
    local i = 0
    while i < lim do
        local u = wptr[i]
        if u == 0 then break end
        local cp = u
        if u >= 0xD800 and u <= 0xDBFF and (i + 1) < lim then
            local u2 = wptr[i + 1]
            if u2 >= 0xDC00 and u2 <= 0xDFFF then
                cp = 0x10000 + (u - 0xD800) * 1024 + (u2 - 0xDC00)
                i = i + 1
            end
        end
        i = i + 1
        if cp < 0x80 then
            ci = ci + 1; chars[ci] = string.char(cp)
        elseif cp < 0x800 then
            ci = ci + 1
            chars[ci] = string.char(
                0xC0 + math.floor(cp / 64),
                0x80 + (cp % 64)
            )
        elseif cp < 0x10000 then
            ci = ci + 1
            chars[ci] = string.char(
                0xE0 + math.floor(cp / 4096),
                0x80 + (math.floor(cp / 64) % 64),
                0x80 + (cp % 64)
            )
        else
            ci = ci + 1
            chars[ci] = string.char(
                0xF0 + math.floor(cp / 262144),
                0x80 + (math.floor(cp / 4096) % 64),
                0x80 + (math.floor(cp / 64) % 64),
                0x80 + (cp % 64)
            )
        end
    end
    return table.concat(chars)
end

-- E2E テストからも単体検証できるよう公開ヘルパー参照を保持
_G._VL3D_UTF8_TO_U16 = utf8_to_utf16le_array
_G._VL3D_U16_TO_UTF8 = utf16le_ptr_to_utf8


local function pe_find_export_addr(ffi, mod_base, target_name)
    if mod_base == 0 or type(target_name) ~= "string" or target_name == "" then return 0 end
    local b = ffi.cast("const uint8_t*", mod_base)
    if b[0] ~= 0x4D or b[1] ~= 0x5A then return 0 end
    local e_lfanew = ffi.cast("const int32_t*", b + 0x3C)[0]
    -- e_lfanew <= 0x800 keeps the PE optional-header read inside the first 0x890 bytes.
    if e_lfanew < 0x40 or e_lfanew > 0x800 then return 0 end
    local nt = b + e_lfanew
    if nt[0] ~= 0x50 or nt[1] ~= 0x45 or nt[2] ~= 0 or nt[3] ~= 0 then return 0 end
    local magic = ffi.cast("const uint16_t*", nt + 0x18)[0]
    local img_sz = tonumber(ffi.cast("const uint32_t*", nt + 0x50)[0]) or 0
    if img_sz < 0x1000 or img_sz > 0x20000000 then return 0 end
    local dd_off = (magic == 0x20B) and 0x88 or ((magic == 0x10B) and 0x78 or 0)
    if dd_off == 0 or (e_lfanew + dd_off + 8) > img_sz then return 0 end
    local exp_rva = ffi.cast("const uint32_t*", nt + dd_off)[0]
    local exp_sz  = ffi.cast("const uint32_t*", nt + dd_off + 4)[0]
    if exp_rva == 0 or exp_sz < 0x28 or exp_rva >= img_sz or (exp_rva + exp_sz) > img_sz then return 0 end
    local exp = b + exp_rva
    local n_funcs = ffi.cast("const uint32_t*", exp + 0x14)[0]
    local n_names = ffi.cast("const uint32_t*", exp + 0x18)[0]
    local rva_fn  = ffi.cast("const uint32_t*", exp + 0x1C)[0]
    local rva_nm  = ffi.cast("const uint32_t*", exp + 0x20)[0]
    local rva_ord = ffi.cast("const uint32_t*", exp + 0x24)[0]
    if n_funcs == 0 or n_names == 0 or n_funcs > 65536 or n_names > 65536 then return 0 end
    if rva_fn == 0 or rva_nm == 0 or rva_ord == 0 then return 0 end
    if (rva_fn + n_funcs * 4) > img_sz or (rva_nm + n_names * 4) > img_sz or (rva_ord + n_names * 2) > img_sz then
        return 0
    end

    local function safe_cstr(rva, max_len)
        if rva == 0 or rva >= img_sz then return nil end
        local lim = math.min(max_len, img_sz - rva)
        local p = b + rva
        for k = 0, lim - 1 do
            if p[k] == 0 then return ffi.string(p, k) end
        end
        return nil
    end

    local names = ffi.cast("const uint32_t*", b + rva_nm)
    local ords  = ffi.cast("const uint16_t*", b + rva_ord)
    local funcs = ffi.cast("const uint32_t*", b + rva_fn)
    local lo, hi = 0, tonumber(n_names) - 1
    while lo <= hi do
        local mid = math.floor((lo + hi) * 0.5)
        local nm = safe_cstr(tonumber(names[mid]) or 0, 256)
        if nm == nil then return 0 end
        if nm == target_name then
            local ord = ords[mid]
            if ord >= n_funcs then return 0 end
            local fn_rva = funcs[ord]
            if fn_rva == 0 or fn_rva >= img_sz or (fn_rva >= exp_rva and fn_rva < exp_rva + exp_sz) then
                return 0
            end
            return mod_base + tonumber(fn_rva)
        elseif nm < target_name then
            lo = mid + 1
        else
            hi = mid - 1
        end
    end
    return 0
end

local function get_w32_api()
    if type(_G.VL3D_TEST_W32_API) == "table" then
        return _G.VL3D_TEST_W32_API
    end
    if _VL_PERSIST.w32_api ~= nil then
        return (_VL_PERSIST.w32_api ~= false) and _VL_PERSIST.w32_api or nil
    end
    local ok_ffi, ffi = pcall(require, "ffi")
    if not (ok_ffi and ffi ~= nil and ffi.os == "Windows") then
        _VL_PERSIST.w32_api = false
        return nil
    end

    local api = nil
    pcall(function()
        -- GetModuleHandleA / GetProcAddress のみ個別 pcall(ffi.cdef)（既に他プラグインが定義済みでもそのシンボルを利用可！）
        pcall(ffi.cdef, "void* GetModuleHandleA(const char* lpModuleName);")
        pcall(ffi.cdef, "void* LoadLibraryA(const char* lpLibFileName);")
        pcall(ffi.cdef, "void* GetProcAddress(void* hModule, const char* lpProcName);")

        local k32_ns = ffi.C
        local h_k32_ptr = nil
        local ok_gmh = pcall(function() h_k32_ptr = k32_ns.GetModuleHandleA("kernel32.dll") end)
        if (not ok_gmh) or h_k32_ptr == nil or tonumber(ffi.cast("uintptr_t", h_k32_ptr)) == 0 then
            k32_ns = ffi.load("kernel32")
            h_k32_ptr = k32_ns.GetModuleHandleA("kernel32.dll")
        end
        local k32_base = tonumber(ffi.cast("uintptr_t", h_k32_ptr)) or 0
        if k32_base == 0 then return end

        -- GetProcAddress 自体を PE エクスポートテーブルから直接取得（ffi.C.GetProcAddress すら汚染されていても 100% 動作！）
        local gpa_addr = pe_find_export_addr(ffi, k32_base, "GetProcAddress")
        local gmh_addr = pe_find_export_addr(ffi, k32_base, "GetModuleHandleA")
        local lla_addr = pe_find_export_addr(ffi, k32_base, "LoadLibraryA")
        if gpa_addr == 0 then
            pcall(function() gpa_addr = tonumber(ffi.cast("uintptr_t", k32_ns.GetProcAddress(h_k32_ptr, "GetProcAddress"))) or 0 end)
        end
        if gpa_addr == 0 then return end

        local fn_gpa = ffi.cast(cached_typeof(ffi, "uintptr_t (__stdcall *)(uintptr_t, const char*)"), gpa_addr)
        local ct_mod = cached_typeof(ffi, "uintptr_t (__stdcall *)(const char*)")
        local fn_gmh = (gmh_addr ~= 0)
            and ffi.cast(ct_mod, gmh_addr)
            or  ffi.cast(ct_mod, fn_gpa(k32_base, "GetModuleHandleA"))
        local fn_lla = (lla_addr ~= 0)
            and ffi.cast(ct_mod, lla_addr)
            or  ffi.cast(ct_mod, fn_gpa(k32_base, "LoadLibraryA"))

        local function bind(mod_h, name, ctype_str)
            if mod_h == 0 then return nil end
            local a = tonumber(fn_gpa(mod_h, name)) or 0
            if a == 0 then return nil end
            return ffi.cast(cached_typeof(ffi, ctype_str), a)
        end

        local t = {
            ffi                    = ffi,
            CreateFileW            = bind(k32_base, "CreateFileW",            "intptr_t (__stdcall *)(const uint16_t*, uint32_t, uint32_t, void*, uint32_t, uint32_t, void*)"),
            GetFileSizeEx          = bind(k32_base, "GetFileSizeEx",          "int32_t  (__stdcall *)(intptr_t, int64_t*)"),
            ReadFile               = bind(k32_base, "ReadFile",               "int32_t  (__stdcall *)(intptr_t, void*, uint32_t, uint32_t*, void*)"),
            CloseHandle            = bind(k32_base, "CloseHandle",            "int32_t  (__stdcall *)(intptr_t)"),
            FindFirstFileW         = bind(k32_base, "FindFirstFileW",         "intptr_t (__stdcall *)(const uint16_t*, void*)"),
            FindNextFileW          = bind(k32_base, "FindNextFileW",          "int32_t  (__stdcall *)(intptr_t, void*)"),
            FindClose              = bind(k32_base, "FindClose",              "int32_t  (__stdcall *)(intptr_t)"),
            GetEnvironmentVariableW= bind(k32_base, "GetEnvironmentVariableW","uint32_t (__stdcall *)(const uint16_t*, uint16_t*, uint32_t)"),
            GetModuleHandleW       = bind(k32_base, "GetModuleHandleW",       "uintptr_t(__stdcall *)(const uint16_t*)"),
            GetModuleFileNameW     = bind(k32_base, "GetModuleFileNameW",     "uint32_t (__stdcall *)(uintptr_t, uint16_t*, uint32_t)"),
            VirtualQuery           = bind(k32_base, "VirtualQuery",           "size_t   (__stdcall *)(const void*, void*, size_t)"),
            GetCurrentProcess      = bind(k32_base, "GetCurrentProcess",      "intptr_t (__stdcall *)(void)"),
            ReadProcessMemory      = bind(k32_base, "ReadProcessMemory",      "int32_t  (__stdcall *)(intptr_t, const void*, void*, size_t, size_t*)"),
            MultiByteToWideChar    = bind(k32_base, "MultiByteToWideChar",    "int32_t  (__stdcall *)(uint32_t, uint32_t, const char*, int32_t, uint16_t*, int32_t)"),
            QueryPerformanceCounter   = bind(k32_base, "QueryPerformanceCounter",   "int32_t (__stdcall *)(int64_t*)"),
            QueryPerformanceFrequency = bind(k32_base, "QueryPerformanceFrequency", "int32_t (__stdcall *)(int64_t*)"),
        }
        if t.MultiByteToWideChar ~= nil then
            _VL_PERSIST.w32_mb2wc_fn = t.MultiByteToWideChar
        end

        -- UCRT (_wfopen / fread / fseek / ftell / fclose) 予備フォールバックも無名関数ポインタでバインド
        local h_crt = tonumber(fn_gmh("ucrtbase.dll")) or 0
        if h_crt == 0 then h_crt = tonumber(fn_lla("ucrtbase.dll")) or 0 end
        if h_crt == 0 then h_crt = tonumber(fn_gmh("msvcrt.dll")) or 0 end
        if h_crt ~= 0 then
            t.wfopen = bind(h_crt, "_wfopen", "void*  (__cdecl *)(const uint16_t*, const uint16_t*)")
            t.fseek  = bind(h_crt, "fseek",   "int32_t(__cdecl *)(void*, long, int32_t)")
            t.ftell  = bind(h_crt, "ftell",   "long   (__cdecl *)(void*)")
            t.fread  = bind(h_crt, "fread",   "size_t (__cdecl *)(void*, size_t, size_t, void*)")
            t.fclose = bind(h_crt, "fclose",  "int32_t(__cdecl *)(void*)")
        end

        if t.CreateFileW ~= nil then
            api = t
        end
    end)

    _VL_PERSIST.w32_api = api or false
    return api
end

-- 単調増加壁時計（秒）：初回呼出時に単一クロック源・ゼロ基準エポックを _VL_PERSIST.clock_state にロックし、
-- フレーム途中で VL3DBridge.aux2 が遅延ロードされてもエポック切替（0.00 秒クランプ）を絶対に起こさない設計。
vl_wall_time_sec = function()
    if type(_G.VL3D_TEST_BRIDGE) == "table" and type(_G.VL3D_TEST_BRIDGE.wall_time_sec) == "function" then
        local ok_tb, wt_tb = pcall(_G.VL3D_TEST_BRIDGE.wall_time_sec)
        if ok_tb and type(wt_tb) == "number" then return wt_tb end
    end
    local cs = _VL_PERSIST.clock_state
    if cs == nil then
        cs = { kind = nil, last = 0.0, ofs = 0.0 }
        -- 1) Win32 QueryPerformanceCounter（64-bit 整数ティック差分・DLL ロード前でも即座に高精度単調時刻を取得）
        pcall(function()
            local api = get_w32_api()
            if api ~= nil and api.ffi ~= nil and api.QueryPerformanceCounter ~= nil and api.QueryPerformanceFrequency ~= nil then
                local hz = api.ffi.new("int64_t[1]")
                local c0 = api.ffi.new("int64_t[1]")
                if api.QueryPerformanceFrequency(hz) ~= 0 and tonumber(hz[0]) > 0 and api.QueryPerformanceCounter(c0) ~= 0 then
                    cs.kind = "qpc"
                    cs.fn_c = api.QueryPerformanceCounter
                    cs.inv_hz = 1.0 / tonumber(hz[0])
                    cs.c0 = c0[0]
                    cs.buf = api.ffi.new("int64_t[1]")
                end
            end
        end)
        -- 2) POSIX clock_gettime(CLOCK_MONOTONIC)（Linux テスト環境等）
        if cs.kind == nil then
            pcall(function()
                local ok_f, ffi_l = pcall(require, "ffi")
                if ok_f and ffi_l ~= nil and ffi_l.os ~= "Windows" then
                    pcall(ffi_l.cdef, "int clock_gettime(int clk_id, void* tp);")
                    local fn_cg = ffi_l.cast("int (*)(int, void*)", ffi_l.C.clock_gettime)
                    local ts = ffi_l.new("int64_t[2]")
                    if fn_cg(1, ts) == 0 then
                        cs.kind = "posix"
                        cs.posix_fn = fn_cg
                        cs.posix_ts = ts
                        cs.s0 = ts[0]
                        cs.ns0 = ts[1]
                    end
                end
            end)
        end
        -- 3) VL3DBridge.wall_time_sec()（先行ロードして同一エポックに固定）
        if cs.kind == nil then
            if _VL_PERSIST.bridge_mod == nil and type(vl3d_bridge) == "function" then
                pcall(vl3d_bridge)
            end
            local br_m = _VL_PERSIST.bridge_mod
            if type(br_m) == "table" and type(br_m.wall_time_sec) == "function" then
                local ok_wt, wt = pcall(br_m.wall_time_sec)
                if ok_wt and type(wt) == "number" then
                    cs.kind = "bridge"
                    cs.fn_br = br_m.wall_time_sec
                    cs.t0 = wt
                end
            end
        end
        -- 4) obj.getinfo("clock") / os.clock() フォールバック
        if cs.kind == nil and type(obj) == "table" and type(obj.getinfo) == "function" then
            local ok_c, clk = pcall(obj.getinfo, "clock")
            if ok_c and type(clk) == "number" then
                cs.kind = "aviutl"
                cs.t0 = clk
            end
        end
        if cs.kind == nil and type(os) == "table" and type(os.clock) == "function" then
            cs.kind = "os"
            cs.t0 = os.clock()
        end
        _VL_PERSIST.clock_state = cs
    end

    local raw_t = nil
    if cs.kind == "qpc" then
        if cs.fn_c(cs.buf) ~= 0 then
            raw_t = tonumber(cs.buf[0] - cs.c0) * cs.inv_hz
        end
    elseif cs.kind == "posix" then
        if cs.posix_fn(1, cs.posix_ts) == 0 then
            raw_t = tonumber(cs.posix_ts[0] - cs.s0) + tonumber(cs.posix_ts[1] - cs.ns0) * 1e-9
        end
    elseif cs.kind == "bridge" then
        local ok_wt, wt = pcall(cs.fn_br)
        if ok_wt and type(wt) == "number" then raw_t = wt - cs.t0 end
    elseif cs.kind == "aviutl" then
        local ok_c, clk = pcall(obj.getinfo, "clock")
        if ok_c and type(clk) == "number" then raw_t = clk - cs.t0 end
    elseif cs.kind == "os" then
        raw_t = os.clock() - cs.t0
    end
    if raw_t == nil then
        raw_t = (type(obj) == "table" and type(obj.time) == "number") and obj.time or cs.last
    end
    local cur_t = raw_t + cs.ofs
    if cur_t < cs.last then
        cs.ofs = cs.last - raw_t
        cur_t = cs.last
    else
        cs.last = cur_t
    end
    return cur_t
end

local function win32_read_file_wide_direct(wpath)
    if wpath == nil then return nil end
    local api = get_w32_api()
    if api == nil then return nil end
    local ffi = api.ffi
    local got = nil
    pcall(function()
        if api.CreateFileW ~= nil and api.GetFileSizeEx ~= nil and api.ReadFile ~= nil and api.CloseHandle ~= nil then
            local h = api.CreateFileW(wpath, 0x80000000, 7, nil, 3, 0x80, nil)
            if h ~= 0 and h ~= -1 then
                pcall(function()
                    local fsz = ffi.new("int64_t[1]")
                    if api.GetFileSizeEx(h, fsz) ~= 0 then
                        local total = tonumber(fsz[0]) or 0
                        if total > 0 and total <= 268435456 then
                            local buf = ffi.new("uint8_t[?]", total)
                            local rd  = ffi.new("uint32_t[1]")
                            local off = 0
                            while off < total do
                                local chunk = math.min(16777216, total - off)
                                if api.ReadFile(h, buf + off, chunk, rd, nil) == 0 or rd[0] == 0 then
                                    break
                                end
                                off = off + tonumber(rd[0])
                            end
                            if off > 0 then
                                got = ffi.string(buf, off)
                            end
                        end
                    end
                end)
                pcall(api.CloseHandle, h)
            end
        end
    end)
    return got
end

win32_read_file_wide_from_utf8 = function(utf8_path)
    local api = get_w32_api()
    if api == nil then return nil end
    local ffi = api.ffi
    local got = nil

    pcall(function()
        local wpath = utf8_to_utf16le_array(ffi, utf8_path)
        -- 1) Primary: kernel32!CreateFileW (GENERIC_READ=0x80000000, SHARE_RWD=7, OPEN_EXISTING=3)
        if api.CreateFileW ~= nil and api.GetFileSizeEx ~= nil and api.ReadFile ~= nil and api.CloseHandle ~= nil then
            local h = api.CreateFileW(wpath, 0x80000000, 7, nil, 3, 0x80, nil)
            if h ~= 0 and h ~= -1 then
                pcall(function()
                    local fsz = ffi.new("int64_t[1]")
                    if api.GetFileSizeEx(h, fsz) ~= 0 then
                        local total = tonumber(fsz[0]) or 0
                        if total > 0 and total <= 268435456 then
                            local buf = ffi.new("uint8_t[?]", total)
                            local rd  = ffi.new("uint32_t[1]")
                            local off = 0
                            while off < total do
                                local chunk = math.min(16777216, total - off)
                                if api.ReadFile(h, buf + off, chunk, rd, nil) == 0 or rd[0] == 0 then
                                    break
                                end
                                off = off + tonumber(rd[0])
                            end
                            if off > 0 then
                                got = ffi.string(buf, off)
                            end
                        end
                    end
                end)
                pcall(api.CloseHandle, h)
            end
        end

        -- 2) Secondary fallback: ucrtbase/msvcrt _wfopen(wpath, L"rb")
        if got == nil and api.wfopen ~= nil and api.fseek ~= nil and api.ftell ~= nil and api.fread ~= nil and api.fclose ~= nil then
            local wmode = utf8_to_utf16le_array(ffi, "rb")
            local fp = api.wfopen(wpath, wmode)
            if fp ~= nil then
                if api.fseek(fp, 0, 2) == 0 then
                    local sz = tonumber(api.ftell(fp)) or 0
                    api.fseek(fp, 0, 0)
                    if sz > 0 and sz <= 268435456 then
                        local buf = ffi.new("uint8_t[?]", sz)
                        local nrd = tonumber(api.fread(buf, 1, sz, fp)) or 0
                        if nrd > 0 then
                            got = ffi.string(buf, nrd)
                        end
                    end
                end
                api.fclose(fp)
            end
        end
    end)

    return got
end

-- Unicode 対応ディレクトリ内ファイル一覧（ファイル名＋更新時刻 64bit スコア）
local function list_dir_files_utf8(dir_path, pattern_ext)
    local out = {}
    local base = trim_str(dir_path)
    if base == "" then return out end
    local last_c = base:sub(-1)
    if last_c ~= "\\" and last_c ~= "/" then
        base = base .. (base:find("/") and "/" or "\\")
    end

    local api = get_w32_api()
    if api ~= nil and api.FindFirstFileW ~= nil and api.FindNextFileW ~= nil and api.FindClose ~= nil then
        local ffi = api.ffi
        pcall(function()
            local pat = base .. (pattern_ext or "*")
            local wpat = utf8_to_utf16le_array(ffi, pat)
            local fdata = ffi.new("uint8_t[1024]")
            local hf = api.FindFirstFileW(wpat, fdata)
            if hf == 0 or hf == -1 then return end
            repeat
                local attr = ffi.cast("const uint32_t*", fdata)[0]
                if (attr % 32) < 16 then -- FILE_ATTRIBUTE_DIRECTORY (0x10) 以外の通常ファイル
                    local ft_lo = tonumber(ffi.cast("const uint32_t*", fdata + 20)[0]) or 0
                    local ft_hi = tonumber(ffi.cast("const uint32_t*", fdata + 24)[0]) or 0
                    local wname = ffi.cast("const uint16_t*", fdata + 44)
                    local fname = utf16le_ptr_to_utf8(wname, 260)
                    if fname ~= "" and fname ~= "." and fname ~= ".." then
                        out[#out + 1] = {
                            path  = base .. fname,
                            name  = fname,
                            mtime = ft_hi * 4294967296.0 + ft_lo,
                        }
                    end
                end
            until api.FindNextFileW(hf, fdata) == 0
            api.FindClose(hf)
        end)
    elseif type(_G.VL3D_TEST_DIR_LISTING) == "function" then
        -- 単体テスト専用フック（本番環境では Win32 UTF-16LE FindFirstFileW のみを使用し外部プロセス起動は完全ゼロ）
        out = _G.VL3D_TEST_DIR_LISTING(base, pattern_ext) or out
    end
    return out
end

get_env_utf8 = function(var_name)
    local api = get_w32_api()
    if api ~= nil and api.GetEnvironmentVariableW ~= nil then
        local got = nil
        pcall(function()
            local ffi = api.ffi
            local wn = utf8_to_utf16le_array(ffi, var_name)
            local outw = ffi.new("uint16_t[1024]")
            local n = api.GetEnvironmentVariableW(wn, outw, 1024)
            if n > 0 and n < 1024 then
                got = utf16le_ptr_to_utf8(outw, tonumber(n))
            end
        end)
        if type(got) == "string" and #got > 0 then return got end
    end
    if type(os) == "table" and type(os.getenv) == "function" then
        local v = os.getenv(var_name)
        if type(v) == "string" and #v > 0 then return v end
    end
    return nil
end

-- 純 LuaJIT バイト直読・ロケール完全非依存 IEEE-754 高速数値スキャナ（C.strtod / C.strtol 不要！）
local POW10_POS = {}
local POW10_NEG = {}
for k = 0, 38 do
    POW10_POS[k] = 10.0 ^ k
    POW10_NEG[k] = 10.0 ^ (-k)
end

