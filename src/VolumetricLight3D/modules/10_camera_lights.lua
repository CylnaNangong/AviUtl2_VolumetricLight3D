-- =====================================================================
-- カメラ制御 3D ライト配列直読＆遅延ライティング連携エンジン（aviutl2.exe 0x140141dc0 / 0x1401301f2 準拠）
--   gbuf = *(*[lctx + 0x88] + 0xD0)
--   [gbuf + 0x98 .. 0xA0] = CameraLight64 配列（1 要素 64 bytes POD float[16]）：
--     int32[0] = type (0:環境光, 2:平行光源, 3/4:点光源, 5:スポット光源)
--     float[1..3] = L (被照射点→光源への単位ベクトル = -ray_dir), float[4..6] = rgb (明るさ乗算済), float[7] = unlit_mul
--     float[8..9] = atten, float[12..14] = world pos (X,Y,Z), float[15] = spot_half_angle_rad (範囲 * pi/180)
-- =====================================================================
local function inspect_or_suppress_camera_lights(suppress_deferred)
    if type(_G.VL3D_TEST_CAM_LIGHTS) == "table" then
        local tl = _G.VL3D_TEST_CAM_LIGHTS
        if suppress_deferred then
            tl.suppress_requested = true
            tl.suppressed = false
        end
        return tl
    end
    local br_l = vl3d_bridge()
    if br_l ~= nil and type(br_l.get_camera_lights) == "function" then
        local ok_l, count, unlit, ar, ag, ab, peak, has_pri, ltype, dx, dy, dz, lr, lg, lb, lum, px, py, pz, cos_ang =
            pcall(br_l.get_camera_lights)
        if ok_l and type(count) == "number" then
            local out_b = {
                count = count,
                unlit = tonumber(unlit) or 1.0,
                amb_r = tonumber(ar) or 0.0,
                amb_g = tonumber(ag) or 0.0,
                amb_b = tonumber(ab) or 0.0,
                peak_gain = tonumber(peak) or 1.0,
                suppressed = false,
                suppress_requested = suppress_deferred and true or false,
                primary = nil,
                source_tier = "BRIDGE",
            }
            if has_pri == true then
                out_b.primary = {
                    ltype = tonumber(ltype) or 0,
                    dx = tonumber(dx) or 0.0, dy = tonumber(dy) or 0.0, dz = tonumber(dz) or 1.0,
                    r = tonumber(lr) or 0.0, g = tonumber(lg) or 0.0, b = tonumber(lb) or 0.0,
                    lum = tonumber(lum) or 0.0,
                    px = tonumber(px) or 0.0, py = tonumber(py) or 0.0, pz = tonumber(pz) or 0.0,
                    cos_ang = tonumber(cos_ang) or 0.0,
                }
            end
            return out_b
        end
    end
    local out = { count = 0, unlit = 1.0, amb_r = 0.0, amb_g = 0.0, amb_b = 0.0, primary = nil, peak_gain = 1.0, suppressed = false }
    local api = get_w32_api()
    if api == nil or api.ReadProcessMemory == nil then return out end
    local ffi = api.ffi
    if ffi.arch ~= "x64" then return out end

    if not _live_sg_state.inited then
        resolve_live_scene_layer_object(1, nil)
    end
    local st = _live_sg_state
    if st.cands == nil or #st.cands == 0 or st.rpm == nil then return out end

    pcall(function()
        if st.lbuf == nil then
            st.lbuf = ffi.new("float[16]")
        end
        local rpm, hproc, nrd = st.rpm, st.hproc, st.nrd
        local u64b = st.u64b
        local function r_u64(addr)
            if addr == nil or addr < 0x10000 then return nil end
            if rpm(hproc, ffi.cast("const void*", addr), u64b, 8, nrd) == 0 or nrd[0] < 8 then return nil end
            return tonumber(u64b[0])
        end

        local gbuf_addr = nil
        local lb_addr, le_addr = nil, nil
        for ci = 1, #st.cands do
            local lctx = r_u64(st.cands[ci])
            local depth = 0
            while lctx ~= nil and lctx >= 0x10000 and depth < 8 do
                local rctx = r_u64(lctx + 0x88)
                local gb   = rctx and r_u64(rctx + 0xD0) or nil
                if gb ~= nil and gb >= 0x10000 then
                    local lb = r_u64(gb + 0x98)
                    local le = r_u64(gb + 0xA0)
                    local lc = r_u64(gb + 0xA8)
                    if lb ~= nil and le ~= nil and lc ~= nil and le >= lb and lc >= le then
                        local diff = le - lb
                        if diff == 0 or (lb >= 0x10000 and (diff % 64) == 0 and diff <= 4096) then
                            gbuf_addr, lb_addr, le_addr = gb, lb, le
                            break
                        end
                    end
                end
                lctx = r_u64(lctx + 0xC8)
                depth = depth + 1
            end
            if gbuf_addr ~= nil then break end
        end

        if gbuf_addr == nil or lb_addr == nil or le_addr == nil or le_addr <= lb_addr then
            return
        end

        local n = math.floor((le_addr - lb_addr) / 64)
        local unlit = 1.0
        local ar, ag, ab = 0.0, 0.0, 0.0
        local sum_dir_pt = 0.0
        local best_score = -1.0
        local best_light = nil
        local f = st.lbuf
        local i32p = ffi.cast("const int32_t*", f)

        for i = 0, n - 1 do
            if rpm(hproc, ffi.cast("const void*", lb_addr + i * 64), f, 64, nrd) ~= 0 and nrd[0] == 64 then
                local ltype = tonumber(i32p[0]) or -1
                local lr, lg, lb_c = tonumber(f[4]) or 0.0, tonumber(f[5]) or 0.0, tonumber(f[6]) or 0.0
                local umul = tonumber(f[7]) or 1.0
                if ltype >= 0 and ltype <= 5 and lr >= 0.0 and lr <= 100.0 and lg >= 0.0 and lg <= 100.0 and lb_c >= 0.0 and lb_c <= 100.0 then
                    out.count = out.count + 1
                    unlit = unlit * math.max(0.0, math.min(1.0, umul))
                    local lum = math.max(lr, lg, lb_c)
                    if ltype == 0 then
                        ar = ar + lr; ag = ag + lg; ab = ab + lb_c
                    else
                        sum_dir_pt = sum_dir_pt + lum
                        if lum > best_score then
                            best_score = lum
                            best_light = {
                                ltype = ltype,
                                dx = tonumber(f[1]) or 0.0,
                                dy = tonumber(f[2]) or 0.0,
                                dz = tonumber(f[3]) or 1.0,
                                r  = lr, g = lg, b = lb_c, lum = lum,
                                px = tonumber(f[12]) or 0.0,
                                py = tonumber(f[13]) or 0.0,
                                pz = tonumber(f[14]) or 0.0,
                                cos_ang = tonumber(f[15]) or 0.0,
                            }
                        end
                    end
                end
            end
        end

        out.unlit = unlit
        out.amb_r, out.amb_g, out.amb_b = ar, ag, ab
        out.primary = best_light
        out.peak_gain = unlit + math.max(ar, ag, ab) + sum_dir_pt

        -- モード 2（カメラ 3D 照明を無効化）：ホスト G-Buffer は一切書き換えず（完全読み取り専用）、
        -- 光源ピーク輝度に基づくゲイン補償のみで 3D モデルの照明を維持したまま白飛びを抑制する
        out.suppressed = false
        out.suppress_requested = suppress_deferred and true or false
    end)
    return out
end

local function read_cam_light_fx_extras(cl, self_layer)
    local blur_deg = 0.0
    local atten_pct = 0.0
    local range_deg = nil
    if type(cl) ~= "table" then return blur_deg, atten_pct, range_deg end
    if type(cl.blur_deg) == "number" then blur_deg = math.max(0.0, math.min(90.0, cl.blur_deg)) end
    if type(cl.atten_pct) == "number" then atten_pct = math.max(0.0, math.min(100.0, cl.atten_pct)) end
    if type(cl.range_deg) == "number" then range_deg = math.max(0.0, math.min(90.0, cl.range_deg)) end
    if type(cl.blur_deg) == "number" and type(cl.atten_pct) == "number" then
        return blur_deg, atten_pct, range_deg
    end
    local lt = tonumber(cl.ltype) or 0
    local is_spot = (lt == 4 or lt == 5)
    if (not is_spot) and lt ~= 3 then
        return blur_deg, atten_pct, range_deg
    end
    if type(obj) ~= "table" or obj.getvalue == nil then
        return blur_deg, atten_pct, range_deg
    end

    pcall(function()
        scan_aul2_translations()
        local fx_bases = is_spot and SPOT_FX_BASES or POINT_FX_BASES
        local best_score = 1e30
        local hit_ln = nil
        local max_ln = 100
        local cl_ang = tonumber(cl.cos_ang) or 0.0
        local cl_lum = tonumber(cl.lum) or 0.0
        local cl_px  = tonumber(cl.px) or 0.0
        local cl_py  = tonumber(cl.py) or 0.0
        local cl_pz  = tonumber(cl.pz) or 0.0

        local function safe_get_num(ln, fx, keys)
            for ki = 1, #keys do
                local ok_v, v = pcall(obj.getvalue, ln, fx, keys[ki])
                if ok_v and type(v) == "number" then return v end
            end
            return nil
        end

        local function safe_get_one(ln, fx, k)
            local ok_v, v = pcall(obj.getvalue, ln, fx, k)
            if ok_v and type(v) == "number" then return v end
            return nil
        end

        -- 注意: カメラ制御レイヤーは通常メディアオブジェクトを持たないため obj.getvalue("layer"..ln) は false を返す。
        -- また「目標中心回転」や「グループ制御」併用時はローカル (X,Y,Z) とワールド cl.p が乖離する（d2 >> 4.0）ため、
        -- layerN ゲートや d2 ハード上限を設けず、角度一致・輝度一致を主スコアとし距離 d2 は微小タイブレークのみに用いる。
        local function probe_layer(ln)
            if ln == nil or ln < 1 or ln > max_ln then return false end
            local any_on_ln = false
            local probe_keys = is_spot and SPOT_BLUR_KEYS or LIGHT_ATTEN_KEYS
            for bi = 1, #fx_bases do
                local base = fx_bases[bi]
                local found_base = false
                for idx = 0, 3 do
                    local fx = (idx == 0) and base or (base .. ":" .. tostring(idx))
                    local v_main = safe_get_num(ln, fx, probe_keys)
                    if v_main ~= nil then
                        found_base = true
                        any_on_ln = true
                        local vx = safe_get_one(ln, fx, "X") or cl_px
                        local vy = safe_get_one(ln, fx, "Y") or cl_py
                        local vz = safe_get_one(ln, fx, "Z") or cl_pz
                        local dx_m, dy_m, dz_m = vx - cl_px, vy - cl_py, vz - cl_pz
                        local d_tie = math.min(0.25, math.sqrt(dx_m * dx_m + dy_m * dy_m + dz_m * dz_m) * 1.0e-5)

                        local v_br = safe_get_num(ln, fx, SPOT_BRIGHT_KEYS)
                        local lum_err = (v_br ~= nil) and math.abs((v_br * 0.01) - cl_lum) or 0.05

                        local score = lum_err * 2.0 + d_tie
                        local cand_blur = 0.0
                        local cand_atten = 0.0
                        local cand_range = nil

                        if is_spot then
                            cand_blur = math.max(0.0, math.min(90.0, v_main))
                            local vr = safe_get_num(ln, fx, SPOT_RANGE_KEYS)
                            if vr ~= nil then
                                cand_range = math.max(0.0, math.min(90.0, vr))
                                if cl_ang > 1.0e-4 then
                                    local pred_out = math.rad(cand_range + 0.5 * cand_blur)
                                    score = math.abs(pred_out - cl_ang) * 20.0 + lum_err * 2.0 + d_tie
                                end
                            end
                            local va = safe_get_num(ln, fx, LIGHT_ATTEN_KEYS)
                            cand_atten = math.max(0.0, math.min(100.0, va or 0.0))
                        else
                            cand_atten = math.max(0.0, math.min(100.0, v_main))
                        end

                        if score < best_score then
                            best_score = score
                            hit_ln = ln
                            blur_deg = cand_blur
                            atten_pct = cand_atten
                            range_deg = cand_range
                        end
                    else
                        break
                    end
                end
                if found_base then break end
            end
            return any_on_ln
        end

        local cached_ln = _VL_PERSIST.last_cam_light_layer
        if type(cached_ln) == "number" and probe_layer(cached_ln) and best_score < 0.02 then
            return
        end
        for ln = 1, max_ln do
            if ln ~= cached_ln then
                probe_layer(ln)
                if best_score < 0.002 then break end
            end
        end
        if hit_ln ~= nil then
            _VL_PERSIST.last_cam_light_layer = hit_ln
        end
    end)
    return blur_deg, atten_pct, range_deg
end

local function try_load_3d_layer_mesh(oln, aup2_hint_map, cur_frame)
    scan_aul2_translations()

    -- 第 0 エンジン（最優先・O(1) ゼロ誤爆）：aviutl2.exe ライブ・シーングラフ直読
    local live_info = resolve_live_scene_layer_object(oln, cur_frame)
    local ls = live_info and live_info.status or "UNAVAILABLE"

    if ls == "EMPTY" or ls == "HIDDEN" or ls == "DISABLED" then
        return nil, nil, live_info
    end
    if ls == "OBJ" then
        local m = load_wavefront_obj_mesh(live_info.path or "", live_info.adj or 0, live_info.wpath)
        if m ~= nil then return m, "OBJ", live_info end
        return nil, nil, live_info
    end
    if ls == "VRM" then
        local vpath = (type(live_info.path) == "string" and trim_str(live_info.path) ~= "") and live_info.path or nil
        if vpath == nil and type(aup2_hint_map) == "table" and aup2_hint_map[oln] ~= nil and aup2_hint_map[oln].kind == "VRM" then
            vpath = aup2_hint_map[oln].path
        end
        local vm_sdk = load_vrm_layer_mesh(oln, vpath)
        if vm_sdk ~= nil then return vm_sdk, "VRM", live_info end
        return nil, nil, live_info
    end
    if ls == "OTHER" then
        -- 組み込み 2D 図形・画像・動画・テキスト・シーン・カメラ制御等は 100% 3D モデルではないため即座に除外！
        local fnm = live_info.fx_name or ""
        if fnm == "図形" or fnm == "画像ファイル" or fnm == "動画ファイル" or fnm == "テキスト"
            or fnm == "シーン" or fnm == "カメラ制御" or fnm == "音声ファイル" or fnm == "パーティクル出力"
            or fnm == "フレームバッファ" or fnm == "直前オブジェクト" then
            return nil, nil, live_info
        end
        -- カスタムオブジェクト（VRMToolkitModel 等）は VRM 読込のみ試行し、無関係な .obj へのフォールバックを完全遮断！
        local hint_vrm = (type(aup2_hint_map) == "table" and aup2_hint_map[oln] ~= nil and aup2_hint_map[oln].kind == "VRM")
            and aup2_hint_map[oln].path or nil
        local vm_live = load_vrm_layer_mesh(oln, hint_vrm)
        if vm_live ~= nil then return vm_live, "VRM", live_info end
        return nil, nil, live_info
    end

    -- 以下は live_info が UNAVAILABLE（非 Windows 単体テスト環境や将来 ABI 変更時）の厳格フォールバック
    local obj_path, fx_obj = get_fx_val(oln, OBJ_FX_NAMES, OBJ_FILE_ITEMS, true)
    if type(obj_path) == "string" and trim_str(obj_path) ~= "" then
        local cadj = get_fx_val(oln, fx_obj and { fx_obj } or OBJ_FX_NAMES, OBJ_ADJ_ITEMS)
        local m = load_wavefront_obj_mesh(obj_path, cadj or 0)
        if m ~= nil then return m, "OBJ", live_info end
    end
    local vm = load_vrm_layer_mesh(oln, nil)
    if vm ~= nil then return vm, "VRM", live_info end

    -- 第 2 エンジン：アクティブシーン ID で厳格フィルタ済みの .aup2 / AutoBackup_*.aup2 ヒント
    if type(aup2_hint_map) == "table" and aup2_hint_map[oln] ~= nil then
        local h = aup2_hint_map[oln]
        if h.kind == "OBJ" then
            local m = load_wavefront_obj_mesh(h.path, h.adj or 0)
            if m ~= nil then return m, "OBJ", live_info end
        elseif h.kind == "VRM" then
            local vm2 = load_vrm_layer_mesh(oln, h.path)
            if vm2 ~= nil then return vm2, "VRM", live_info end
        end
    end

    -- 第 3 エンジン（無差別フォールバック完全撤廃！必ず OBJ シグネチャ一致レイヤーのみ限定！）
    if has_3d_or_water_signature(oln) == "OBJ" then
        local live_list = scan_live_modelfile_instances()
        if #live_list == 1 then
            local m = load_wavefront_obj_mesh(live_list[1].path, live_list[1].adj or 0)
            if m ~= nil then return m, "OBJ", live_info end
        end
    end
    return nil, nil, live_info
end

