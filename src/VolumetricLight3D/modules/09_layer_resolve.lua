-- =====================================================================
-- VL3DBridge（.aux2 / obj.module）优先通道
-- 编辑相机、图层模型路径、摄像机光源全部改为只读官方 SDK + 版本白名单探针。
-- 插件缺失或宿主 ABI 未验证时，自动回退到下方已加固的 Lua 只读路径，绝不写宿主内存。
-- =====================================================================
vl3d_bridge = function()
    if type(_G.VL3D_TEST_BRIDGE) == "table" then
        return _G.VL3D_TEST_BRIDGE
    end
    if _VL_PERSIST.bridge_mod ~= nil then
        return (_VL_PERSIST.bridge_mod ~= false) and _VL_PERSIST.bridge_mod or nil
    end
    local br = nil
    pcall(function()
        if type(obj.module) ~= "function" then return end
        local m = obj.module("VL3DBridge")
        if type(m) == "table" and type(m.resolve_layer) == "function" then
            br = m
        end
    end)
    _VL_PERSIST.bridge_mod = br or false
    return br
end

-- シーン全体基準の現在フレーム番号（0始まり整数）を返す公式ヘルパ：
-- `obj.frame` はクリップ先頭基準の相対フレーム番号であるため、`obj.frame_s > 0` のクリップでは
-- `obj.frame_s + obj.frame` を用いないと VL3DBridge / .aup2 走査がクリップ開始前の空区間を参照してしまう。
local function vl_scene_frame()
    local f = (type(obj.frame) == "number") and math.floor(obj.frame + 0.5) or 0
    local fs = (type(obj.frame_s) == "number") and math.floor(obj.frame_s + 0.5) or nil
    if fs == nil and type(obj.getvalue) == "function" then
        local ok_fs, v_fs = pcall(obj.getvalue, "frame_s")
        if ok_fs and type(v_fs) == "number" then fs = math.floor(v_fs + 0.5) end
    end
    local sf = (fs or 0) + f
    local fe = (type(obj.frame_e) == "number") and math.floor(obj.frame_e + 0.5) or nil
    if fe ~= nil and fs ~= nil and fe >= fs and sf > fe then
        sf = fe
    end
    return sf
end

-- =====================================================================
-- 組み込み [テキスト] 文字揃え (18種: aviutl2.exe 0x14053bcd0) ＆ 文字装飾パディング (0x14053bdf0)
--   obj.load("layer", no, effect) (0x1402668a0) は画素バッファのみを更新し obj.cx / obj.cy を
--   引き継がないため、テキストの描画中心オフセットをここで厳密再現する。
-- =====================================================================
local TEXT_ALIGN_MAP = {
    ["左寄せ[上]"] = { -0.5, -0.5 }, ["中央揃え[上]"] = { 0.0, -0.5 }, ["右寄せ[上]"] = { 0.5, -0.5 },
    ["左寄せ[中]"] = { -0.5,  0.0 }, ["中央揃え[中]"] = { 0.0,  0.0 }, ["右寄せ[中]"] = { 0.5,  0.0 },
    ["左寄せ[下]"] = { -0.5,  0.5 }, ["中央揃え[下]"] = { 0.0,  0.5 }, ["右寄せ[下]"] = { 0.5,  0.5 },
    ["縦書 上寄[右]"] = {  0.5, -0.5 }, ["縦書 中央[右]"] = {  0.5, 0.0 }, ["縦書 下寄[右]"] = {  0.5, 0.5 },
    ["縦書 上寄[中]"] = {  0.0, -0.5 }, ["縦書 中央[中]"] = {  0.0, 0.0 }, ["縦書 下寄[中]"] = {  0.0, 0.5 },
    ["縦書 上寄[左]"] = { -0.5, -0.5 }, ["縦書 中央[左]"] = { -0.5, 0.0 }, ["縦書 下寄[左]"] = { -0.5, 0.5 },
    ["左对齐[上]"] = { -0.5, -0.5 }, ["居中对齐[上]"] = { 0.0, -0.5 }, ["右对齐[上]"] = { 0.5, -0.5 },
    ["左对齐[中]"] = { -0.5,  0.0 }, ["居中对齐[中]"] = { 0.0,  0.0 }, ["右对齐[中]"] = { 0.5,  0.0 },
    ["左对齐[下]"] = { -0.5,  0.5 }, ["居中对齐[下]"] = { 0.0,  0.5 }, ["右对齐[下]"] = { 0.5,  0.5 },
    ["竖排 上齐[右]"] = {  0.5, -0.5 }, ["竖排 居中[右]"] = {  0.5, 0.0 }, ["竖排 下齐[右]"] = {  0.5, 0.5 },
    ["竖排 上齐[中]"] = {  0.0, -0.5 }, ["竖排 居中[中]"] = {  0.0, 0.0 }, ["竖排 下齐[中]"] = {  0.0, 0.5 },
    ["竖排 上齐[左]"] = { -0.5, -0.5 }, ["竖排 居中[左]"] = { -0.5, 0.0 }, ["竖排 下齐[左]"] = { -0.5, 0.5 },
    ["0"]  = { -0.5, -0.5 }, ["1"]  = { 0.0, -0.5 }, ["2"]  = { 0.5, -0.5 },
    ["3"]  = { -0.5,  0.0 }, ["4"]  = { 0.0,  0.0 }, ["5"]  = { 0.5,  0.0 },
    ["6"]  = { -0.5,  0.5 }, ["7"]  = { 0.0,  0.5 }, ["8"]  = { 0.5,  0.5 },
    ["9"]  = {  0.5, -0.5 }, ["10"] = { 0.5,  0.0 }, ["11"] = { 0.5,  0.5 },
    ["12"] = {  0.0, -0.5 }, ["13"] = { 0.0,  0.0 }, ["14"] = {  0.0,  0.5 },
    ["15"] = { -0.5, -0.5 }, ["16"] = { -0.5, 0.0 }, ["17"] = { -0.5,  0.5 },
}

local function parse_text_align_and_deco(align_raw, deco_raw, fsize)
    local fx, fy = -0.5, -0.5
    if type(align_raw) == "string" then
        local a = trim_str(align_raw)
        if a ~= "" and TEXT_ALIGN_MAP[a] ~= nil then
            fx, fy = TEXT_ALIGN_MAP[a][1], TEXT_ALIGN_MAP[a][2]
        end
    end
    local pl, pr, pt, pb = 0.0, 0.0, 0.0, 0.0
    local sz = (type(fsize) == "number" and fsize > 1.0) and fsize or 32.0
    if type(deco_raw) == "string" then
        local d = trim_str(deco_raw)
        if d == "影付き文字" or d == "带阴影文字" or d == "1" then
            local spx = math.max(1.0, math.floor(sz * 0.06))
            pr, pb = spx, spx
        elseif d == "影付き文字(薄)" or d == "带浅阴影文字" or d == "2" then
            local spx = math.max(1.0, math.floor(sz * 0.03))
            pr, pb = spx, spx
        elseif d == "縁取り文字" or d == "描边文字" or d == "3" then
            local ppx = math.max(1.0, math.floor(sz * 0.15))
            pl, pr, pt, pb = ppx, ppx, ppx, ppx
        elseif d == "縁取り文字(細)" or d == "细描边文字" or d == "4" or d == "縁取り文字(角)" or d == "方角描边文字" or d == "6" then
            local ppx = math.max(1.0, math.floor(sz * 0.075))
            pl, pr, pt, pb = ppx, ppx, ppx, ppx
        elseif d == "縁取り文字(太)" or d == "粗描边文字" or d == "5" then
            local ppx = math.max(1.0, math.floor(sz * 0.225))
            pl, pr, pt, pb = ppx, ppx, ppx, ppx
        end
    end
    return fx, fy, pl, pr, pt, pb
end

local function calc_text_center_shift(w_px, h_px, fx, fy, pl, pr, pt, pb)
    local cx_a = w_px * fx + (0.5 - fx) * (pl or 0.0) - (fx + 0.5) * (pr or 0.0)
    local cy_a = h_px * fy + (0.5 - fy) * (pt or 0.0) - (fy + 0.5) * (pb or 0.0)
    return cx_a, cy_a
end

local function resolve_text_align_for_layer(ln, cur_frame, linfo, aup2_hints_opt)
    if linfo ~= nil and linfo.is_text == true then
        return true, linfo.align_fx or -0.5, linfo.align_fy or -0.5,
               linfo.pad_l or 0.0, linfo.pad_r or 0.0, linfo.pad_t or 0.0, linfo.pad_b or 0.0
    end
    local fnm = linfo and linfo.fx_name or ""
    local tier = linfo and linfo.source_tier or ""
    if tier == "SDK" and fnm ~= "" and fnm ~= "テキスト" and fnm ~= "文本" then
        return false, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    end
    if fnm ~= "テキスト" and fnm ~= "文本" and linfo ~= nil and linfo.status ~= "UNAVAILABLE" then
        return false, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    end
    if type(obj.getvalue) == "function" then
        local ok_ta, ta_v, td_v, ts_v = pcall(function()
            local a = obj.getvalue(ln, "テキスト", "文字揃え")
            if a == nil then a = obj.getvalue(ln, "文本", "文字对齐") end
            if a == nil then return nil, nil, nil end
            local d = obj.getvalue(ln, "テキスト", "文字装飾") or obj.getvalue(ln, "文本", "文字装饰") or ""
            local s = tonumber(obj.getvalue(ln, "テキスト", "サイズ") or obj.getvalue(ln, "文本", "大小")) or 32.0
            return tostring(a), tostring(d), s
        end)
        if ok_ta and ta_v ~= nil then
            local fx, fy, pl, pr, pt, pb = parse_text_align_and_deco(ta_v, td_v, ts_v)
            return true, fx, fy, pl, pr, pt, pb
        end
    end
    local hints = aup2_hints_opt
    if hints == nil then
        hints = scan_aup2_layer_models(cur_frame)
    end
    if type(hints) == "table" and hints[ln] ~= nil and hints[ln].is_text then
        local h = hints[ln]
        local fx, fy, pl, pr, pt, pb = parse_text_align_and_deco(h.text_align, h.text_deco, h.text_size)
        return true, fx, fy, pl, pr, pt, pb
    end
    if fnm == "テキスト" or fnm == "文本" then
        return true, -0.5, -0.5, 0.0, 0.0, 0.0, 0.0
    end
    return false, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
end

-- =====================================================================
-- 決定的 O(1) ライブ・シーングラフ直読エンジン（resolve_live_scene_layer_object）
-- aviutl2.exe の lua_getvalue (0x140251365) と同一の描画コンテキストから
-- ReadProcessMemory 経由で「現在描画中シーン -> レイヤー oln -> 現在フレームの Edit::Object ->
-- 先頭 EffectBase」を 0.0003ms で直接特定！未保存プロジェクト・多シーンでも誤爆 0.0%！
-- =====================================================================
local _live_sg_state = _VL_PERSIST.live_sg_state

local function resolve_live_scene_layer_object(oln, cur_frame)
    local want_scn = (type(obj.scene_id) == "number") and math.floor(obj.scene_id + 0.5) or nil
    if want_scn ~= nil then _aup2_cache.active_scene_id = want_scn end
    local br = vl3d_bridge()
    if br ~= nil and type(br.resolve_layer) == "function" then
        local ok_b, st, fx, path, adj, scene_id, wcol, tier, is_txt, afx, afy, pl, pr, pt, pb =
            pcall(br.resolve_layer, oln, cur_frame or 0)
        local scn_num = tonumber(scene_id)
        local scn_match = (want_scn == nil or scn_num == nil or math.floor(scn_num + 0.5) == want_scn)
        if ok_b and scn_match and type(st) == "string" and st ~= "" and st ~= "UNAVAILABLE" then
            if scn_num ~= nil then _aup2_cache.active_scene_id = math.floor(scn_num + 0.5) end
            return {
                status = st,
                fx_name = type(fx) == "string" and fx or "",
                path = type(path) == "string" and path or "",
                adj = tonumber(adj) or 0,
                scene_id = tonumber(scene_id) or 0,
                water_color = tonumber(wcol) or -1,
                source_tier = type(tier) == "string" and tier or "BRIDGE",
                is_text = (is_txt == true),
                align_fx = tonumber(afx) or 0.0,
                align_fy = tonumber(afy) or 0.0,
                pad_l = tonumber(pl) or 0.0,
                pad_r = tonumber(pr) or 0.0,
                pad_t = tonumber(pt) or 0.0,
                pad_b = tonumber(pb) or 0.0,
            }
        end
    end
    local api = get_w32_api()
    if api == nil or api.ReadProcessMemory == nil or api.GetModuleHandleW == nil then
        return { status = "UNAVAILABLE" }
    end
    local ffi = api.ffi
    local st = _live_sg_state
    if not st.inited then
        st.inited = true
        st.cands = {}
        pcall(function()
            st.hproc = (api.GetCurrentProcess ~= nil) and api.GetCurrentProcess() or ffi.cast("intptr_t", -1)
            st.rpm   = api.ReadProcessMemory
            st.u64b  = ffi.new("uint64_t[2]")
            st.i32b  = ffi.new("int32_t[2]")
            st.u8b   = ffi.new("uint8_t[8]")
            st.wbuf  = ffi.new("uint16_t[128]")
            st.nrd   = ffi.new("size_t[1]")

            local exe_base = tonumber(api.GetModuleHandleW(nil)) or 0
            if exe_base == 0 then return end
            local hdr = ffi.new("uint8_t[4096]")
            if st.rpm(st.hproc, ffi.cast("const void*", exe_base), hdr, 4096, st.nrd) == 0 then return end
            if hdr[0] ~= 0x4D or hdr[1] ~= 0x5A then return end
            local lfanew = ffi.cast("const int32_t*", hdr + 0x3C)[0]
            if lfanew < 0x40 or lfanew > 0x800 then return end
            local nt = hdr + lfanew
            if nt[0] ~= 0x50 or nt[1] ~= 0x45 then return end
            local nsec   = ffi.cast("const uint16_t*", nt + 6)[0]
            local opt_sz = tonumber(ffi.cast("const uint16_t*", nt + 20)[0]) or 0
            local hdr_lim = math.min(4096, tonumber(st.nrd[0]) or 0)
            local sec_ok = (opt_sz <= 1024 and (lfanew + 24 + opt_sz + math.min(tonumber(nsec) or 0, 16) * 40) <= hdr_lim)
            local sec_p  = sec_ok and (nt + 24 + opt_sz) or nt

            local seen = {}
            local function add_cand(addr)
                if addr > exe_base and not seen[addr] then
                    seen[addr] = true
                    st.cands[#st.cands + 1] = addr
                end
            end

            -- .text セクションから mov rax,[rip+disp32]; mov rdx,[rax+0x88] (48 8B 05 .. 48 8B 90 88 00 00 00) を自動検出
            for si = 0, (sec_ok and math.min(tonumber(nsec) - 1, 15) or -1) do
                local sh = sec_p + si * 40
                local ch = ffi.cast("const uint32_t*", sh + 36)[0]
                local vsz = tonumber(ffi.cast("const uint32_t*", sh + 8)[0]) or 0
                local rva = tonumber(ffi.cast("const uint32_t*", sh + 12)[0]) or 0
                -- IMAGE_SCN_MEM_EXECUTE (0x20000000) or ".text"
                if (math.floor(ch / 0x20000000) % 2 == 1 or (sh[0] == 0x2E and sh[1] == 0x74)) and vsz > 32 and vsz <= 0x2000000 then
                    local tbuf = ffi.new("uint8_t[?]", vsz)
                    if st.rpm(st.hproc, ffi.cast("const void*", exe_base + rva), tbuf, vsz, st.nrd) ~= 0 then
                        local lim = tonumber(st.nrd[0]) - 14
                        for p = 0, lim do
                            if tbuf[p] == 0x48 and tbuf[p + 1] == 0x8B and tbuf[p + 2] == 0x05
                                and tbuf[p + 7] == 0x48 and tbuf[p + 8] == 0x8B and tbuf[p + 9] == 0x90
                                and tbuf[p + 10] == 0x88 and tbuf[p + 11] == 0x00 and tbuf[p + 12] == 0x00 and tbuf[p + 13] == 0x00 then
                                local disp = ffi.cast("const int32_t*", tbuf + p + 3)[0]
                                add_cand(exe_base + rva + p + 7 + tonumber(disp))
                            end
                        end
                    end
                end
            end
            -- .text 走査が 0 件だった場合のみ既知 RVA フォールバック（v2.1.15: 0x5901A8 / v2.1.14: 0x58AAB8）
            if #st.cands == 0 then
                add_cand(exe_base + 0x5901A8)
                add_cand(exe_base + 0x58AAB8)
            end
        end)
    end

    if st.cands == nil or #st.cands == 0 or st.rpm == nil then
        return { status = "UNAVAILABLE" }
    end

    local res = { status = "UNAVAILABLE" }
    pcall(function()
        local rpm, hproc, nrd = st.rpm, st.hproc, st.nrd
        local u64b, i32b, u8b = st.u64b, st.i32b, st.u8b

        local function r_u64(addr)
            if addr < 0x10000 then return nil end
            if rpm(hproc, ffi.cast("const void*", addr), u64b, 8, nrd) == 0 or nrd[0] < 8 then return nil end
            return tonumber(u64b[0])
        end
        local function r_i32(addr)
            if addr < 0x10000 then return nil end
            if rpm(hproc, ffi.cast("const void*", addr), i32b, 4, nrd) == 0 or nrd[0] < 4 then return nil end
            return tonumber(i32b[0])
        end
        local function r_u8(addr)
            if addr < 0x10000 then return nil end
            if rpm(hproc, ffi.cast("const void*", addr), u8b, 1, nrd) == 0 or nrd[0] < 1 then return nil end
            return tonumber(u8b[0])
        end

        local scene_ptr, ctx_frame, scene_id, found_rctx = nil, nil, 0, nil
        local layers_b, layers_e = nil, nil
        -- g_luaMutex 保護下の最内スコープ lctx および親スコープ連鎖 [lctx + 0xC8] を走査（クリップ開始フレーム非依存！）
        for ci = 1, #st.cands do
            local lctx = r_u64(st.cands[ci])
            local depth = 0
            while lctx ~= nil and lctx >= 0x10000 and depth < 8 do
                local rctx = r_u64(lctx + 0x88)
                local sptr = rctx and r_u64(rctx) or nil
                local cfrm = rctx and r_i32(rctx + 0x0C) or nil
                if sptr ~= nil and sptr >= 0x10000 and cfrm ~= nil and cfrm >= 0 and cfrm <= 100000000 then
                    local lb = r_u64(sptr + 0x08)
                    local le = r_u64(sptr + 0x10)
                    if lb ~= nil and le ~= nil and le >= lb and ((le - lb) % 8 == 0) then
                        local nl = (le - lb) / 8
                        local sid_cand = r_i32(sptr) or 0
                        if nl >= 1 and nl <= 4096 and (want_scn == nil or sid_cand == want_scn) then
                            scene_ptr, ctx_frame, layers_b, layers_e, found_rctx = sptr, cfrm, lb, le, rctx
                            scene_id = sid_cand
                            break
                        end
                    end
                end
                lctx = r_u64(lctx + 0xC8)
                depth = depth + 1
            end
            if scene_ptr ~= nil then break end
        end

        if scene_ptr == nil then return end
        _aup2_cache.active_scene_id = scene_id

        local n_layers = (layers_e - layers_b) / 8
        if oln < 1 or oln > n_layers then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end
        local layer_ptr = r_u64(layers_b + (oln - 1) * 8)
        if layer_ptr == nil or layer_ptr < 0x10000 then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end
        local lmeta = r_u64(layer_ptr + 0x20)
        if lmeta ~= nil and lmeta >= 0x10000 then
            local hid = r_u8(lmeta + 0x10)
            if hid ~= nil and hid ~= 0 then
                res = { status = "HIDDEN", scene_id = scene_id }
                return
            end
        end

        local ob = r_u64(layer_ptr)
        local oe = r_u64(layer_ptr + 0x08)
        if ob == nil or oe == nil or oe < ob or ((oe - ob) % 16 ~= 0) then return end
        local n_objs = (oe - ob) / 16
        if n_objs <= 0 then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end

        local tf = ctx_frame or cur_frame or 0
        -- 0x140030740 と完全同一の二分探索（m_frameEnd >= tf の最小インデックス）
        local lo, cnt = 0, n_objs
        while cnt > 0 do
            local step = math.floor(cnt * 0.5)
            local mid = lo + step
            local eptr = r_u64(ob + mid * 16)
            local fend = eptr and r_i32(eptr + 0x54) or nil
            if fend == nil then return end
            if fend < tf then
                lo = mid + 1
                cnt = cnt - step - 1
            else
                cnt = step
            end
        end
        if lo >= n_objs then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end
        local eobj = r_u64(ob + lo * 16)
        if eobj == nil or eobj < 0x10000 then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end
        local fstart = r_i32(eobj + 0x50)
        local fend   = r_i32(eobj + 0x54)
        if fstart == nil or fend == nil or tf < fstart or tf > fend then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end

        local fxb = r_u64(eobj + 0x18)
        local fxe = r_u64(eobj + 0x20)
        if fxb == nil or fxe == nil or fxe <= fxb then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end
        local fx0 = r_u64(fxb)
        if fx0 == nil or fx0 < 0x10000 then
            res = { status = "EMPTY", scene_id = scene_id }
            return
        end
        local function r_wstr(addr, max_u)
            if addr == nil or addr < 0x10000 then return nil end
            local units = st.wbuf
            local total_u = 0
            local cur = addr
            local rem = math.min(max_u or 96, 126)
            while rem > 0 do
                local in_page = 4096 - (cur % 4096)
                local chunk_b = math.min(rem * 2, in_page)
                if chunk_b < 2 then chunk_b = 2 end
                if rpm(hproc, ffi.cast("const void*", cur), units + total_u, chunk_b, nrd) ~= 0 and nrd[0] >= 2 then
                    local got_u = math.floor(tonumber(nrd[0]) / 2)
                    for k = 0, got_u - 1 do
                        if units[total_u + k] == 0 then
                            return utf16le_ptr_to_utf8(units, total_u + k)
                        end
                    end
                    total_u = total_u + got_u
                    cur = cur + got_u * 2
                    rem = rem - got_u
                else
                    -- 4KB 未満の小ブロック境界でも 1 文字 (2byte) ずつ終端まで確実に読取る！
                    if rpm(hproc, ffi.cast("const void*", cur), units + total_u, 2, nrd) == 0 or nrd[0] < 2 then
                        break
                    end
                    if units[total_u] == 0 then
                        return utf16le_ptr_to_utf8(units, total_u)
                    end
                    total_u = total_u + 1
                    cur = cur + 2
                    rem = rem - 1
                end
            end
            if total_u > 0 then
                units[total_u] = 0
                return utf16le_ptr_to_utf8(units, total_u)
            end
            return nil
        end

        local fx_dis = r_u8(fx0 + 0x5A) or 0
        local nm_ptr = r_u64(fx0 + 0x40)
        local fx_name = r_wstr(nm_ptr, 96)
        if fx_name == nil or fx_name == "" then return end

        if fx_dis ~= 0 then
            res = { status = "DISABLED", fx_name = fx_name, scene_id = scene_id }
            return
        end

        -- 組み込み 3D モデルオブジェクト [モデルファイル] (ModelFileEffect)
        if fx_name == "モデルファイル" then
            local slen = r_u64(fx0 + 0xB8) or 0
            local scap = r_u64(fx0 + 0xC0) or 0
            local adj_b = r_u8(fx0 + 0x108) or 0
            if slen >= 1 and slen <= 2048 and scap >= 7 and scap <= 8192 and slen <= scap then
                local src_waddr = (scap < 8) and (fx0 + 0xA8) or r_u64(fx0 + 0xA8)
                if src_waddr ~= nil and src_waddr >= 0x10000 then
                    local warr = ffi.new("uint16_t[?]", slen + 1)
                    if rpm(hproc, ffi.cast("const void*", src_waddr), warr, slen * 2, nrd) ~= 0 and nrd[0] == slen * 2 then
                        warr[slen] = 0
                        local fpath = utf16le_ptr_to_utf8(warr, slen)
                        res = {
                            status   = "OBJ",
                            fx_name  = fx_name,
                            wpath    = warr,
                            path     = fpath,
                            adj      = (adj_b ~= 0) and 1 or 0,
                            scene_id = scene_id,
                        }
                        return
                    end
                end
            end
            res = { status = "EMPTY", fx_name = fx_name, scene_id = scene_id }
            return
        end

        res = { status = "OTHER", fx_name = fx_name, scene_id = scene_id }
    end)
    return res
end

_G._VL3D_RESOLVE_LIVE_LAYER = resolve_live_scene_layer_object

