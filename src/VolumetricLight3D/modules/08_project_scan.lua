local function append_unique_str(list, val)
    val = trim_str(val)
    if val == "" then return end
    for i = 1, #list do
        if list[i] == val then return end
    end
    list[#list + 1] = val
end

if _VL_PERSIST.aul2_fx_lists ~= nil and #_VL_PERSIST.aul2_fx_lists >= 32 then
    local fl = _VL_PERSIST.aul2_fx_lists
    VRM_FX_NAMES, VRM_FILE_ITEMS, VRM_SCALE_ITEMS, VRM_MAXT_ITEMS = fl[1], fl[2], fl[3], fl[4]
    VRM_FX_ITEMS, VRM_FY_ITEMS, VRM_FZ_ITEMS, VRM_ANIM_ITEMS     = fl[5], fl[6], fl[7], fl[8]
    VRM_SPD_ITEMS, VRM_JSON_ITEMS, VRM_BLD_ITEMS                 = fl[9], fl[10], fl[11]
    OBJ_FX_NAMES, OBJ_FILE_ITEMS, OBJ_ADJ_ITEMS                  = fl[12], fl[13], fl[14]
    WATER_FX_NAMES, WATER_SP_ITEMS, WATER_DIR_ITEMS, WATER_RIP_ITEMS = fl[15], fl[16], fl[17], fl[18]
    WATER_TLT_ITEMS, WATER_HW_ITEMS, WATER_CL_ITEMS, WATER_HS_ITEMS  = fl[19], fl[20], fl[21], fl[22]
    WATER_HB_ITEMS, WATER_RES_ITEMS, WATER_ADJ_ITEMS, WATER_COL_ITEMS= fl[23], fl[24], fl[25], fl[26]
    SPOT_FX_BASES, POINT_FX_BASES, SPOT_RANGE_KEYS               = fl[27], fl[28], fl[29]
    SPOT_BLUR_KEYS, SPOT_BRIGHT_KEYS, LIGHT_ATTEN_KEYS           = fl[30], fl[31], fl[32]
else
    _VL_PERSIST.aul2_scanned = false
end

local function scan_aul2_translations()
    if _VL_PERSIST.aul2_scanned then return end
    _VL_PERSIST.aul2_scanned = true
    _VL_PERSIST.aul2_fx_lists = {
        VRM_FX_NAMES, VRM_FILE_ITEMS, VRM_SCALE_ITEMS, VRM_MAXT_ITEMS,
        VRM_FX_ITEMS, VRM_FY_ITEMS, VRM_FZ_ITEMS, VRM_ANIM_ITEMS,
        VRM_SPD_ITEMS, VRM_JSON_ITEMS, VRM_BLD_ITEMS,
        OBJ_FX_NAMES, OBJ_FILE_ITEMS, OBJ_ADJ_ITEMS,
        WATER_FX_NAMES, WATER_SP_ITEMS, WATER_DIR_ITEMS, WATER_RIP_ITEMS,
        WATER_TLT_ITEMS, WATER_HW_ITEMS, WATER_CL_ITEMS, WATER_HS_ITEMS,
        WATER_HB_ITEMS, WATER_RES_ITEMS, WATER_ADJ_ITEMS, WATER_COL_ITEMS,
        SPOT_FX_BASES, POINT_FX_BASES, SPOT_RANGE_KEYS,
        SPOT_BLUR_KEYS, SPOT_BRIGHT_KEYS, LIGHT_ATTEN_KEYS,
    }

    local dirs = {}
    if type(_G.VL3D_TEST_LANG_DIRS) == "table" then
        for i = 1, #_G.VL3D_TEST_LANG_DIRS do dirs[#dirs + 1] = _G.VL3D_TEST_LANG_DIRS[i] end
    end
    local pdata = get_env_utf8("PROGRAMDATA") or "C:\\ProgramData"
    dirs[#dirs + 1] = pdata .. "\\aviutl2\\Language"
    local appdata = get_env_utf8("APPDATA")
    if appdata then dirs[#dirs + 1] = appdata .. "\\aviutl2\\Language" end
    local lad = get_env_utf8("LOCALAPPDATA")
    if lad then dirs[#dirs + 1] = lad .. "\\aviutl2\\Language" end
    pcall(function()
        local wapi = get_w32_api()
        if wapi ~= nil and wapi.GetModuleFileNameW ~= nil then
            local wbuf = wapi.ffi.new("uint16_t[1024]")
            local n = wapi.GetModuleFileNameW(0, wbuf, 1024)
            if n > 0 and n < 1024 then
                local exep = utf16le_ptr_to_utf8(wbuf, tonumber(n))
                local exedir = exep:match("^(.*)[/\\][^/\\]+$")
                if exedir and exedir ~= "" then
                    dirs[#dirs + 1] = exedir .. "\\Language"
                    dirs[#dirs + 1] = exedir .. "\\data\\Language"
                end
            end
        end
    end)
    pcall(function()
        local sp = obj.getinfo("script_path")
        if type(sp) == "string" and #sp > 0 then
            local sep = sp:find("/") and "/" or "\\"
            local s = sp
            if s:sub(-1) ~= "\\" and s:sub(-1) ~= "/" then s = s .. sep end
            dirs[#dirs + 1] = s .. "Language"
            dirs[#dirs + 1] = s .. ".." .. sep .. "Language"
            dirs[#dirs + 1] = s .. ".." .. sep .. ".." .. sep .. "Language"
        end
    end)

    local key_targets = {
        ["モデルファイル"]     = { OBJ_FX_NAMES, OBJ_FILE_ITEMS },
        ["モデルオブジェクト"] = { OBJ_FX_NAMES, OBJ_FILE_ITEMS },
        ["ファイル"]           = { OBJ_FILE_ITEMS },
        ["座標系を調整"]       = { OBJ_ADJ_ITEMS },
        ["VRMToolkitModel"]    = { VRM_FX_NAMES },
        ["VRMファイル"]        = { VRM_FILE_ITEMS },
        ["モデル倍率"]         = { VRM_SCALE_ITEMS },
        ["素材毎最大三角形数"] = { VRM_MAXT_ITEMS },
        ["X反転"]              = { VRM_FX_ITEMS },
        ["Y反転"]              = { VRM_FY_ITEMS },
        ["Z反転"]              = { VRM_FZ_ITEMS },
        ["VRMAファイル"]       = { VRM_ANIM_ITEMS },
        ["アニメ速度(%)"]      = { VRM_SPD_ITEMS },
        ["表情JSON"]           = { VRM_JSON_ITEMS },
        ["VRMAとJSON"]         = { VRM_BLD_ITEMS },
        ["水面"]               = { WATER_FX_NAMES },
        ["速度"]               = { WATER_SP_ITEMS },
        ["方向"]               = { WATER_DIR_ITEMS },
        ["波紋"]               = { WATER_RIP_ITEMS },
        ["傾き"]               = { WATER_TLT_ITEMS },
        ["波紋幅"]             = { WATER_HW_ITEMS },
        ["波紋位置"]           = { WATER_CL_ITEMS },
        ["波紋速度"]           = { WATER_HS_ITEMS },
        ["水平境界ぼかし"]     = { WATER_HB_ITEMS },
        ["解像度"]             = { WATER_RES_ITEMS },
        ["位置補正"]           = { WATER_ADJ_ITEMS },
        ["色"]                 = { WATER_COL_ITEMS },
        ["スポット光源(カメラ制御)"] = { SPOT_FX_BASES },
        ["点光源(カメラ制御)"]       = { POINT_FX_BASES },
        ["範囲"]                     = { SPOT_RANGE_KEYS },
        ["ぼかし"]                   = { SPOT_BLUR_KEYS },
        ["明るさ"]                   = { SPOT_BRIGHT_KEYS },
        ["減衰"]                     = { LIGHT_ATTEN_KEYS },
    }

    local seen_path = {}
    for di = 1, #dirs do
        local files = list_dir_files_utf8(dirs[di], "*.aul2")
        for fi = 1, #files do
            local fp = files[fi].path
            if fp and not seen_path[fp] then
                seen_path[fp] = true
                local txt = read_binary_file_utf8(fp)
                if type(txt) == "string" and #txt > 4 then
                    if txt:byte(1) == 0xEF and txt:byte(2) == 0xBB and txt:byte(3) == 0xBF then
                        txt = txt:sub(4)
                    end
                    for line in txt:gmatch("[^\r\n]+") do
                        local eq = line:find("=", 1, true)
                        if eq and eq > 1 then
                            local k = trim_str(line:sub(1, eq - 1))
                            local tgts = key_targets[k]
                            if tgts ~= nil then
                                local v_str = line:sub(eq + 1)
                                for ti_k = 1, #tgts do
                                    append_unique_str(tgts[ti_k], v_str)
                                end
                            end
                        end
                    end
                end
            end
        end
    end
end

-- 予備エンジン：プロジェクト (.aup2) / AutoBackup_*.aup2 からレイヤー上の .obj / .vrm パスを直接解決
local _aup2_cache = _VL_PERSIST.aup2_cache
local function scan_aup2_layer_models(cur_frame)
    local now_t = vl_wall_time_sec()
    if _aup2_cache.raw_entries ~= nil and (now_t - _aup2_cache.t) < 8.0 then
        local want_scn = _aup2_cache.active_scene_id
        if want_scn == nil then want_scn = _aup2_cache.log_scene_id end
        if want_scn == nil then want_scn = 0 end
        local out = {}
        local re = _aup2_cache.raw_entries
        for i = 1, #re do
            local e = re[i]
            if (e.scene or 0) == want_scn then
                if cur_frame == nil or (cur_frame >= e.f0 and cur_frame <= e.f1) then
                    if out[e.layer] == nil then out[e.layer] = e end
                end
            end
        end
        return out
    end

    local cand_files = {}
    local function collect_dir(d)
        local fs = list_dir_files_utf8(d, "*.aup2")
        for i = 1, #fs do cand_files[#cand_files + 1] = fs[i] end
    end
    if type(_G.VL3D_TEST_AUP2_DIRS) == "table" then
        for i = 1, #_G.VL3D_TEST_AUP2_DIRS do collect_dir(_G.VL3D_TEST_AUP2_DIRS[i]) end
    end
    pcall(function()
        local pp = obj.getinfo("project_path")
        if type(pp) == "string" and #pp > 0 then collect_dir(pp) end
    end)
    local pdata = get_env_utf8("PROGRAMDATA") or "C:\\ProgramData"
    local appdata = get_env_utf8("APPDATA")
    local lad = get_env_utf8("LOCALAPPDATA")
    local log_dirs = { pdata .. "\\aviutl2\\Log" }
    local ini_files = { pdata .. "\\aviutl2\\aviutl2.ini" }
    collect_dir(pdata .. "\\aviutl2\\Backup")
    if appdata then
        collect_dir(appdata .. "\\aviutl2\\Backup")
        log_dirs[#log_dirs + 1] = appdata .. "\\aviutl2\\Log"
        ini_files[#ini_files + 1] = appdata .. "\\aviutl2\\aviutl2.ini"
    end
    if lad then
        collect_dir(lad .. "\\aviutl2\\Backup")
        log_dirs[#log_dirs + 1] = lad .. "\\aviutl2\\Log"
        ini_files[#ini_files + 1] = lad .. "\\aviutl2\\aviutl2.ini"
    end
    if type(_G.VL3D_TEST_LOG_DIRS) == "table" then
        for i = 1, #_G.VL3D_TEST_LOG_DIRS do log_dirs[#log_dirs + 1] = _G.VL3D_TEST_LOG_DIRS[i] end
    end
    pcall(function()
        local sp = obj.getinfo("script_path")
        if type(sp) == "string" and #sp > 0 then
            local sep = sp:find("/") and "/" or "\\"
            local s = sp
            if s:sub(-1) ~= "\\" and s:sub(-1) ~= "/" then s = s .. sep end
            collect_dir(s .. ".." .. sep .. "Backup")
            collect_dir(s .. ".." .. sep .. ".." .. sep .. "Backup")
            log_dirs[#log_dirs + 1] = s .. ".." .. sep .. "Log"
            log_dirs[#log_dirs + 1] = s .. ".." .. sep .. ".." .. sep .. "Log"
            ini_files[#ini_files + 1] = s .. ".." .. sep .. "aviutl2.ini"
            ini_files[#ini_files + 1] = s .. ".." .. sep .. ".." .. sep .. "aviutl2.ini"
        end
    end)
    for ldi = 1, #log_dirs do
        local lfs = list_dir_files_utf8(log_dirs[ldi], "*.log")
        table.sort(lfs, function(a, b) return (a.mtime or 0) > (b.mtime or 0) end)
        for li = 1, math.min(#lfs, 2) do
            local ltxt = read_binary_file_utf8(lfs[li].path)
            if type(ltxt) == "string" and #ltxt > 16 then
                local last_aup = nil
                local last_scn = nil
                for line in ltxt:gmatch("[^\r\n]+") do
                    local mp = line:match("project file%s*:%s*(.+%.aup2)%s*$")
                    if mp then last_aup = trim_str(mp) end
                    -- [Edit::EditService::updateScene] set current scnene [Name](5)
                    local sid = line:match("set current scne?ne%s+%b[]%((%-?%d+)%)")
                    if sid then last_scn = tonumber(sid) end
                end
                if last_scn ~= nil then
                    _aup2_cache.log_scene_id = last_scn
                end
                if type(last_aup) == "string" and #last_aup > 5 then
                    remember_dir_of_file(last_aup)
                    cand_files[#cand_files + 1] = { path = last_aup, mtime = 9.0e18 }
                    break
                end
            end
        end
    end
    for ii = 1, #ini_files do
        local itxt = read_binary_file_utf8(ini_files[ii])
        if type(itxt) == "string" and #itxt > 8 then
            for line in itxt:gmatch("[^\r\n]+") do
                local eq = line:find("=", 1, true)
                if eq then
                    local vv = trim_str(line:sub(eq + 1))
                    if vv:lower():sub(-5) == ".aup2" then
                        remember_dir_of_file(vv)
                        cand_files[#cand_files + 1] = { path = vv, mtime = 8.0e18 - ii }
                    end
                end
            end
        end
    end

    table.sort(cand_files, function(a, b) return (a.mtime or 0) > (b.mtime or 0) end)

    local entries = {}
    local max_try = math.min(#cand_files, 3)
    for fi = 1, max_try do
        local txt = read_binary_file_utf8(cand_files[fi].path)
        if type(txt) == "string" and #txt > 16 then
            if txt:byte(1) == 0xEF and txt:byte(2) == 0xBB and txt:byte(3) == 0xBF then
                txt = txt:sub(4)
            end
            local cur_scene = 0
            local in_scene_hdr = false
            local cur_obj = nil
            for line in txt:gmatch("[^\r\n]+") do
                local s = trim_str(line)
                if s:byte(1) == 91 and s:byte(#s) == 93 then -- [Section]
                    local sec = s:sub(2, #s - 1)
                    local sl = sec:lower()
                    if sl == "scene" then
                        cur_scene = 0
                        in_scene_hdr = true
                        cur_obj = nil
                    else
                        local sc_num = sl:match("^scene%.(%d+)$")
                        if sc_num ~= nil then
                            cur_scene = tonumber(sc_num) or 0
                            in_scene_hdr = true
                            cur_obj = nil
                        else
                            in_scene_hdr = false
                            -- 実機 .aup2 の [0] / [0.0] とテスト用 [Object.0] / [Object.0.0] の両形式に完全対応
                            local main_id, sub_id = sec:match("^(%d+)%.?(.*)$")
                            if main_id == nil then
                                main_id, sub_id = sec:match("^Object%.(%d+)%.?(.*)$")
                            end
                            if main_id ~= nil and (sub_id == "" or sub_id:match("^%d+$") ~= nil) then
                                local oid = tonumber(main_id)
                                if sub_id == "" then
                                    cur_obj = { oid = oid, scene = cur_scene, layer = nil, f0 = 0, f1 = 1e9, path = nil, kind = nil, adj = 0 }
                                    entries[#entries + 1] = cur_obj
                                end
                            else
                                cur_obj = nil
                            end
                        end
                    end
                elseif in_scene_hdr then
                    local eq = s:find("=", 1, true)
                    if eq and eq > 1 then
                        local k = trim_str(s:sub(1, eq - 1))
                        local v = trim_str(s:sub(eq + 1))
                        if k == "scene" then
                            local sv = tonumber(v)
                            if sv ~= nil then cur_scene = sv end
                        end
                    end
                elseif cur_obj ~= nil then
                    local eq = s:find("=", 1, true)
                    if eq and eq > 1 then
                        local k = trim_str(s:sub(1, eq - 1))
                        local v = trim_str(s:sub(eq + 1))
                        if k == "scene" then
                            local sv = tonumber(v)
                            if sv ~= nil then cur_obj.scene = sv end
                        elseif k == "layer" then
                            local lv = tonumber(v)
                            if lv ~= nil then cur_obj.layer = lv + 1 end
                        elseif k == "frame" then
                            local a, b = v:match("^(%-?%d+)%s*,%s*(%-?%d+)")
                            if a and b then cur_obj.f0, cur_obj.f1 = tonumber(a), tonumber(b) end
                        elseif k == "effect.name" and (v == "テキスト" or v == "文本") then
                            if cur_obj.kind == nil then cur_obj.kind = "TEXT" end
                            cur_obj.is_text = true
                        elseif k == "文字揃え" then
                            if cur_obj.kind == nil then cur_obj.kind = "TEXT" end
                            cur_obj.is_text = true
                            cur_obj.text_align = v
                        elseif k == "文字装飾" then
                            cur_obj.text_deco = v
                        elseif k == "サイズ" and cur_obj.is_text then
                            cur_obj.text_size = tonumber(v)
                        else
                            local vl = v:lower()
                            if vl:sub(-4) == ".obj" then
                                cur_obj.path = v
                                cur_obj.kind = "OBJ"
                            elseif vl:sub(-4) == ".vrm" or vl:sub(-4) == ".glb" then
                                if cur_obj.path == nil then
                                    cur_obj.path = v
                                    cur_obj.kind = "VRM"
                                end
                            else
                                for ai = 1, #OBJ_ADJ_ITEMS do
                                    if k == OBJ_ADJ_ITEMS[ai] then
                                        cur_obj.adj = tonumber(v) or 0
                                        break
                                    end
                                end
                            end
                        end
                    end
                end
            end
            if #entries > 0 then break end
        end
    end

    local valid_e = {}
    for i = 1, #entries do
        local e = entries[i]
        if e.layer ~= nil and (e.path ~= nil or e.is_text) then valid_e[#valid_e + 1] = e end
    end
    _aup2_cache.t = now_t
    _aup2_cache.raw_entries = valid_e

    local want_scn = _aup2_cache.active_scene_id
    if want_scn == nil then want_scn = _aup2_cache.log_scene_id end
    if want_scn == nil then want_scn = 0 end

    local out = {}
    for i = 1, #valid_e do
        local e = valid_e[i]
        if (e.scene or 0) == want_scn then
            if cur_frame == nil or (cur_frame >= e.f0 and cur_frame <= e.f1) then
                if out[e.layer] == nil then out[e.layer] = e end
            end
        end
    end
    return out
end

local function load_vrm_layer_mesh(oln, fallback_vrm_path)
    scan_aul2_translations()
    local vrm_file, fx_hit = get_fx_val(oln, VRM_FX_NAMES, VRM_FILE_ITEMS)
    vrm_file = trim_str(vrm_file)
    if vrm_file == "" and type(fallback_vrm_path) == "string" then
        vrm_file = trim_str(fallback_vrm_path)
    end
    if vrm_file == "" then return nil end
    remember_dir_of_file(vrm_file)

    local fx_list  = fx_hit and { fx_hit } or VRM_FX_NAMES
    local mscale   = tonumber((get_fx_val(oln, fx_list, VRM_SCALE_ITEMS))) or 100.0
    local max_tris = math.max(256, math.floor(tonumber((get_fx_val(oln, fx_list, VRM_MAXT_ITEMS))) or 50000))
    local fx_b     = as_bool((get_fx_val(oln, fx_list, VRM_FX_ITEMS)))
    local fy_b     = as_bool((get_fx_val(oln, fx_list, VRM_FY_ITEMS)))
    local fz_b     = as_bool((get_fx_val(oln, fx_list, VRM_FZ_ITEMS)))
    local anim_f   = trim_str((get_fx_val(oln, fx_list, VRM_ANIM_ITEMS)) or "")
    local anim_spd = math.max(0, math.floor(tonumber((get_fx_val(oln, fx_list, VRM_SPD_ITEMS))) or 100))
    local tl_json  = trim_str((get_fx_val(oln, fx_list, VRM_JSON_ITEMS)) or '{"v":1}')
    if tl_json == "" then tl_json = '{"v":1}' end
    local blend_i  = tonumber((get_fx_val(oln, fx_list, VRM_BLD_ITEMS))) or 0
    local blend_m  = (math.floor(blend_i + 0.5) == 1) and "override" or "combine"

    local scale_mul = math.max(1.0, math.floor(mscale))
    local sx_f = fx_b and -1.0 or 1.0
    local sy_f = fy_b and -1.0 or 1.0
    local sz_f = fz_b and -1.0 or 1.0

    local ltime = nil
    pcall(function() ltime = obj.getvalue("layer" .. oln .. ".time") end)
    if type(ltime) ~= "number" then
        ltime = type(obj.time) == "number" and obj.time or 0.0
    end
    local anim_time = ltime * (anim_spd / 100.0)

    -- 1) VRMToolkitBridge 優先（ボーン変形・VRMA モーション・揺れ物物理・表情完全追従！）
    local ok_br, bridge = pcall(obj.module, "VRMToolkitBridge")
    if ok_br and type(bridge) == "table" and type(bridge.open_model) == "function" then
        local got_mesh = nil
        pcall(function()
            local cache_slot = tostring(_aup2_cache.active_scene_id or 0) .. ":" .. tostring(oln)
            local h_entry = VRM_POSE_CACHE[cache_slot]
            local model_id = vrm_file .. "|" .. tostring(max_tris) .. "|" .. anim_f
            if type(h_entry) ~= "table" or h_entry.model_id ~= model_id then
                if type(h_entry) == "table" and type(h_entry.handle) == "number" and h_entry.handle > 0 then
                    pcall(function()
                        if type(bridge.close_model) == "function" then
                            bridge.close_model(h_entry.handle)
                        end
                    end)
                end
                local h = bridge.open_model(vrm_file, max_tris, anim_f)
                if type(h) ~= "number" or h <= 0 then return end
                h_entry = {
                    model_id = model_id,
                    handle   = h,
                    req_time = as_bool(bridge.requires_time_update(h)),
                    pose_key = nil,
                    mesh     = nil,
                    fvx      = nil, fvy = nil, fvz = nil, fcap = 0,
                }
                VRM_POSE_CACHE[cache_slot] = h_entry
            end

            local tl_req = (tl_json ~= '{"v":1}')
            local t_key  = (h_entry.req_time or tl_req) and string.format("%.5f", anim_time) or "static"
            local pkey   = string.format("%s|%g|%g|%g|%g|%s|%s",
                t_key, scale_mul, sx_f, sy_f, sz_f, tl_json, blend_m)

            if h_entry.pose_key == pkey and h_entry.mesh ~= nil then
                got_mesh = h_entry.mesh
                return
            end

            local h = h_entry.handle
            bridge.prepare_frame(h, anim_time, tl_json, blend_m)
            local gcnt = bridge.get_group_count(h) or 0
            local tvx, tvy, tvz = {}, {}, {}
            local nv = 0
            local mx, my, mz = scale_mul * sx_f, scale_mul * sy_f, scale_mul * sz_f
            local CHUNK = 4096

            for gi = 0, gcnt - 1 do
                local mode = bridge.get_group_mode(h, gi)
                local ga = 1.0
                if mode == "texture" and type(bridge.get_group_alpha) == "function" then
                    ga = tonumber(bridge.get_group_alpha(h, gi)) or 1.0
                end
                local tcnt = bridge.get_group_triangle_count(h, gi) or 0
                if ga > 0.02 and tcnt > 0 then
                    local st = 0
                    while st < tcnt do
                        local req = math.min(CHUNK, tcnt - st)
                        if mode == "texture" then
                            local flat = bridge.get_group_texture_chunk(h, gi, st, req)
                            local flen = flat and #flat or 0
                            local i = 1
                            while i + 14 <= flen do
                                nv = nv + 1; tvx[nv] = flat[i]      * mx; tvy[nv] = flat[i + 1]  * my; tvz[nv] = flat[i + 2]  * mz
                                nv = nv + 1; tvx[nv] = flat[i + 5]  * mx; tvy[nv] = flat[i + 6]  * my; tvz[nv] = flat[i + 7]  * mz
                                nv = nv + 1; tvx[nv] = flat[i + 10] * mx; tvy[nv] = flat[i + 11] * my; tvz[nv] = flat[i + 12] * mz
                                i = i + 15
                            end
                        else
                            local flat = bridge.get_group_color_chunk(h, gi, st, req)
                            local flen = flat and #flat or 0
                            local i = 1
                            while i + 20 <= flen do
                                nv = nv + 1; tvx[nv] = flat[i]      * mx; tvy[nv] = flat[i + 1]  * my; tvz[nv] = flat[i + 2]  * mz
                                nv = nv + 1; tvx[nv] = flat[i + 7]  * mx; tvy[nv] = flat[i + 8]  * my; tvz[nv] = flat[i + 9]  * mz
                                nv = nv + 1; tvx[nv] = flat[i + 14] * mx; tvy[nv] = flat[i + 15] * my; tvz[nv] = flat[i + 16] * mz
                                i = i + 21
                            end
                        end
                        st = st + req
                    end
                end
            end

            if nv >= 3 then
                local out_vx, out_vy, out_vz = tvx, tvy, tvz
                local ok_fv, ffi_v = pcall(require, "ffi")
                if ok_fv and ffi_v ~= nil then
                    pcall(function()
                        if (h_entry.fcap or 0) < (nv + 1) then
                            h_entry.fvx = ffi_v.new("float[?]", nv + 256)
                            h_entry.fvy = ffi_v.new("float[?]", nv + 256)
                            h_entry.fvz = ffi_v.new("float[?]", nv + 256)
                            h_entry.fcap = nv + 256
                        end
                        for k = 1, nv do
                            h_entry.fvx[k] = tvx[k]
                            h_entry.fvy[k] = tvy[k]
                            h_entry.fvz[k] = tvz[k]
                        end
                        out_vx, out_vy, out_vz = h_entry.fvx, h_entry.fvy, h_entry.fvz
                    end)
                end
                got_mesh = {
                    indexed = false,
                    vx = out_vx, vy = out_vy, vz = out_vz,
                    nv = nv, nt = math.floor(nv / 3),
                    pose_sig = pkey,
                    bridge_slot_key = "vrm:" .. cache_slot,
                }
                h_entry.pose_key = pkey
                h_entry.mesh     = got_mesh
            end
        end)
        if got_mesh ~= nil then return got_mesh end
    end

    -- 2) 純 Lua GLB/VRM バイナリ予備デコーダ
    return load_glb_vrm_fallback(vrm_file, scale_mul, sx_f, sy_f, sz_f)
end

local function has_3d_or_water_signature(oln)
    scan_aul2_translations()
    local op = get_fx_val(oln, OBJ_FX_NAMES, OBJ_FILE_ITEMS, true)
    if type(op) == "string" and trim_str(op) ~= "" then return "OBJ" end
    local vp = get_fx_val(oln, VRM_FX_NAMES, VRM_FILE_ITEMS, true)
    if type(vp) == "string" and trim_str(vp) ~= "" then return "VRM" end
    local wp = get_fx_val(oln, WATER_FX_NAMES, WATER_RES_ITEMS, false)
    if wp ~= nil then return "WATER" end
    return nil
end

-- aviutl2.exe プロセスメモリ上の [モデルファイル] (ModelFileEffect) インスタンス直接検出（第 3 予備エンジン）
-- 機械語検証済レイアウト (0x14017dfb9..0x14017e050):
--   [inst + 0x40] = L"モデルファイル" (.rdata 固定リテラルアドレス)
--   [inst + 0xa8] = std::wstring m_filePath (SSO <= 7 chars inline / >= 8 heap wchar_t*, +0xb8 len, +0xc0 cap)
--   [inst + 0x108]= uint8_t m_adjCoord (0 / 1)
local _live_modelfile_cache = _VL_PERSIST.modelfile_cache

local function scan_live_modelfile_instances()
    local api = get_w32_api()
    if api == nil or api.GetModuleHandleW == nil or api.VirtualQuery == nil or api.ReadProcessMemory == nil then
        return _live_modelfile_cache.list
    end
    local ffi = api.ffi
    if ffi.arch ~= "x64" then return _live_modelfile_cache.list end

    local now_t = vl_wall_time_sec()
    if now_t - _live_modelfile_cache.t < 0.75 then
        return _live_modelfile_cache.list
    end
    _live_modelfile_cache.t = now_t

    local found = {}
    pcall(function()
        local mbi = ffi.new("uint8_t[64]")
        local nrd = ffi.new("size_t[1]")
        local hproc = (api.GetCurrentProcess ~= nil) and api.GetCurrentProcess() or ffi.cast("intptr_t", -1)
        local function rpm(addr, dst, nbytes)
            if addr == nil or addr < 0x10000 or nbytes <= 0 then return false end
            nrd[0] = 0
            if api.ReadProcessMemory(hproc, ffi.cast("const void*", addr), dst, nbytes, nrd) == 0 then
                return false
            end
            return tonumber(nrd[0]) or 0
        end
        local function page_info(addr)
            if addr == nil or addr < 0x10000 then return nil end
            if api.VirtualQuery(ffi.cast("const void*", addr), mbi, 48) < 48 then return nil end
            return {
                base = tonumber(ffi.cast("const uint64_t*", mbi)[0]) or 0,
                size = tonumber(ffi.cast("const uint64_t*", mbi + 24)[0]) or 0,
                state = ffi.cast("const uint32_t*", mbi + 32)[0],
                prot = ffi.cast("const uint32_t*", mbi + 36)[0],
                typ = ffi.cast("const uint32_t*", mbi + 40)[0],
            }
        end

        local exe_base = tonumber(api.GetModuleHandleW(nil)) or 0
        local exe_pi = page_info(exe_base)
        if exe_base == 0 or exe_pi == nil or exe_pi.state ~= 0x1000 then return end

        -- PE ヘッダはカーネル経由（ReadProcessMemory）で安全に読み取り、不正な e_lfanew 参照を防止
        local hdr = ffi.new("uint8_t[4096]")
        local hdr_n = rpm(exe_base, hdr, 4096)
        if not hdr_n or hdr_n < 0x200 or hdr[0] ~= 0x4D or hdr[1] ~= 0x5A then return end
        local lf = ffi.cast("const int32_t*", hdr + 0x3C)[0]
        if lf < 0x40 or lf > 0x800 or (lf + 0x58) > hdr_n then return end
        if hdr[lf] ~= 0x50 or hdr[lf + 1] ~= 0x45 then return end
        local img_sz = math.min(tonumber(ffi.cast("const uint32_t*", hdr + lf + 0x50)[0]) or 0, 0x1000000)
        if img_sz < 0x1000 then return end

        if _live_modelfile_cache.wstr_addr == nil then
            _live_modelfile_cache.wstr_addr = 0
            local pat = ffi.new("uint8_t[16]")
            local pw = ffi.new("uint16_t[8]", { 0x30E2, 0x30C7, 0x30EB, 0x30D5, 0x30A1, 0x30A4, 0x30EB, 0x0000 })
            ffi.copy(pat, pw, 16)
            local chunk = ffi.new("uint8_t[65536]")
            local cur = exe_base + 0x1000
            local end_img = exe_base + img_sz
            while cur < end_img and _live_modelfile_cache.wstr_addr == 0 do
                local pi = page_info(cur)
                if pi == nil or pi.size <= 0 then break end
                local pm = pi.prot % 256
                if pi.state == 0x1000 and pm == 2 and pi.size >= 16 then
                    local s0 = math.max(cur, pi.base)
                    local s1 = math.min(end_img, pi.base + pi.size)
                    local off = s0
                    while off + 16 <= s1 and _live_modelfile_cache.wstr_addr == 0 do
                        local req = math.min(65536, s1 - off)
                        local got = rpm(off, chunk, req)
                        if not got or got < 16 then break end
                        local lim = got - 16
                        local p = 0
                        while p <= lim do
                            local match = true
                            for k = 0, 15 do
                                if chunk[p + k] ~= pat[k] then match = false; break end
                            end
                            if match then
                                _live_modelfile_cache.wstr_addr = off + p
                                break
                            end
                            p = p + 2
                        end
                        if got < req then break end
                        off = off + math.max(1, got - 16)
                    end
                end
                local nxt = pi.base + math.max(pi.size, 0x1000)
                if nxt <= cur then break end
                cur = nxt
            end
        end

        local wstr_target = _live_modelfile_cache.wstr_addr or 0
        if wstr_target == 0 then return end
        local target_u64 = ffi.cast("uint64_t", wstr_target)
        local chunk_qw = ffi.new("uint64_t[8192]")
        local inst_buf = ffi.new("uint8_t[0x110]")
        local addr = 0x10000
        local max_user = 0x7FFFFFFF0000
        while addr < max_user and #found < 16 do
            local pi = page_info(addr)
            if pi == nil or pi.size <= 0 then break end
            if pi.state == 0x1000 and pi.prot == 0x04 and pi.typ == 0x20000 and pi.size >= 0x120 and pi.size <= 0x4000000 then
                local off_b = 0
                while off_b + 0x110 <= pi.size and #found < 16 do
                    local req_b = math.min(65536, pi.size - off_b)
                    local got = rpm(pi.base + off_b, chunk_qw, req_b)
                    if not got or got < 72 then
                        break
                    end
                    local got_qw = math.floor(got / 8) - 1
                    for i = 0, got_qw do
                        if chunk_qw[i] == target_u64 and (off_b + i * 8) >= 0x40 then
                            local inst_addr = pi.base + off_b + i * 8 - 0x40
                            local igot = rpm(inst_addr, inst_buf, 0x110)
                            if igot and igot >= 0x110 then
                                local vt0 = tonumber(ffi.cast("const uint64_t*", inst_buf)[0]) or 0
                                local vt1 = tonumber(ffi.cast("const uint64_t*", inst_buf + 0x68)[0]) or 0
                                if vt0 > exe_base and vt0 < exe_base + 0x1000000 and vt1 > exe_base and vt1 < exe_base + 0x1000000 then
                                    local slen = tonumber(ffi.cast("const uint64_t*", inst_buf + 0xB8)[0]) or 0
                                    local scap = tonumber(ffi.cast("const uint64_t*", inst_buf + 0xC0)[0]) or 0
                                    if slen >= 4 and slen <= 1024 and scap >= 7 and scap <= 4096 and slen <= scap then
                                        local warr = nil
                                        if scap < 8 then
                                            warr = ffi.cast("const uint16_t*", inst_buf + 0xA8)
                                        else
                                            local hp = tonumber(ffi.cast("const uint64_t*", inst_buf + 0xA8)[0]) or 0
                                            local tmpw = ffi.new("uint16_t[?]", slen + 1)
                                            local wgot = rpm(hp, tmpw, slen * 2)
                                            if wgot and wgot >= slen * 2 then
                                                tmpw[slen] = 0
                                                warr = tmpw
                                            end
                                        end
                                        if warr ~= nil then
                                            local fpath = utf16le_ptr_to_utf8(warr, slen)
                                            local adj_b = inst_buf[0x108]
                                            if fpath ~= "" and fpath:lower():find(".obj", 1, true) then
                                                found[#found + 1] = { path = fpath, adj = (adj_b ~= 0) and 1 or 0 }
                                            end
                                        end
                                    end
                                end
                            end
                        end
                    end
                    if got < req_b then break end
                    off_b = off_b + 65536
                end
            end
            local nxt = pi.base + pi.size
            if nxt <= addr then break end
            addr = nxt
        end
    end)

    _live_modelfile_cache.list = found
    return found
end


