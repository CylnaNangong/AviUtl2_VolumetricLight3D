
-- "object" から直接読み取り、直後に cache:vl3d/pool を 1x1 解放（cache:vl3d/src 不要＝0 バイト！）
local function scan_pool_from_object(lw, lh, thr_norm)
    local u0, u1, v0, v1 = 0.0, 1.0, 0.0, 1.0
    local base_ratio = 0.45
    local found_base = false
    local found_thr  = false
    pcall(function()
        local ok_ffi, ffi = pcall(require, "ffi")
        if ok_ffi and ffi ~= nil then
            local kf = math.max(1, math.ceil(math.max(lw, lh) / 256.0), math.min(32, math.ceil(math.sqrt((lw * lh) / 4096.0))))
            local gw, gh = math.max(1, math.ceil(lw / kf)), math.max(1, math.ceil(lh / kf))
            obj.clearbuffer("cache:vl3d/pool", gw, gh)
            obj.pixelshader("ps_vl_pool", "cache:vl3d/pool", { "object" }, { lw, lh, kf, thr_norm }, "copy")
            local ptr, pw, ph = obj.getpixeldata("cache:vl3d/pool", "rgba")
            if ptr ~= nil and pw == gw and ph == gh then
                local px = ffi.cast("uint8_t*", ptr)
                local bx0, bx1, by0, by1 = gw, -1, gh, -1
                local ax0, ax1, ay0, ay1 = gw, -1, gh, -1
                local sum_base = 0.0
                for gy = 0, gh - 1 do
                    local row = gy * gw
                    for gx = 0, gw - 1 do
                        local idx = (row + gx) * 4
                        local g_val = px[idx + 1]
                        local a_thr = px[idx + 3]
                        if g_val >= 2 then
                            found_base = true
                            if gx < bx0 then bx0 = gx end
                            if gx > bx1 then bx1 = gx end
                            if gy < by0 then by0 = gy end
                            if gy > by1 then by1 = gy end
                            sum_base = sum_base + (g_val / 255.0)
                        end
                        if a_thr >= 8 then
                            found_thr = true
                            if gx < ax0 then ax0 = gx end
                            if gx > ax1 then ax1 = gx end
                            if gy < ay0 then ay0 = gy end
                            if gy > ay1 then ay1 = gy end
                        end
                    end
                end
                if found_base and bx0 <= bx1 and by0 <= by1 then
                    local base_cells = math.max(1.0, (bx1 - bx0 + 1) * (by1 - by0 + 1))
                    base_ratio = math.max(0.12, math.min(1.0, sum_base / base_cells))
                end
                if found_thr and ax0 <= ax1 and ay0 <= ay1 then
                    u0 = math.max(0.0, ax0 / gw)
                    u1 = math.min(1.0, (ax1 + 1) / gw)
                    v0 = math.max(0.0, ay0 / gh)
                    v1 = math.min(1.0, (ay1 + 1) / gh)
                end
            end
            obj.clearbuffer("cache:vl3d/pool", 1, 1)
        end
    end)
    return found_base, found_thr, u0, u1, v0, v1, base_ratio
end

local VL_BF = {}
local VL_BF_KEYS = { "base_alpha", "obj_x", "obj_y", "obj_z", "obj_rx", "obj_ry", "obj_rz", "zoom", "cam_mode", "has_exact_cam", "cam_x", "cam_y", "cam_z", "focal_d", "f_x", "f_y", "f_z", "r_x", "r_y", "r_z", "d_x", "d_y", "d_z", "ui_lang", "scr_w", "scr_h", "cam_light_info", "cam_spot_inner_rad", "cam_spot_outer_rad", "cam_spot_eff_rad", "cam_atten_pct", "cam_ap_dist", "cam_ray_dx", "cam_ray_dy", "cam_ray_dz", "ln", "self_layer", "use_cam_virtual_src", "thr_norm", "tex_loaded", "lw", "lh", "u0_s", "u1_s", "v0_s", "v1_s", "base_ratio", "found_thr", "water_geo", "exists", "wx", "wy", "wz", "wrz", "zx", "zy", "zz_l", "Wrx", "Wry", "Wrz", "Wdx", "Wdy", "Wdz", "Wnx", "Wny", "Wnz", "src_desc", "Hx", "Hy", "cx_sh", "cy_sh", "R_fixed", "L_char", "f_dec", "L_tot", "k_dec", "inv_end", "mode_kind", "sx_p", "sy_p", "sz_p", "u_wn", "dS_wn", "mid_x", "mid_y", "mid_z", "s_min", "s_max", "hw", "hh", "dz", "total_sl", "pts", "pi", "is_edit", "sf_x", "sf_y", "sf_z", "sr_x", "sr_y", "sr_z", "sd_x", "sd_y", "sd_z", "use_source", "lc_r", "lc_g", "lc_b", "mip_ready", "mip_dots", "peak_cache_dots", "occ_str_val", "occ_tex_name", "shmap_tex_name", "camocc_tex_name", "n_occ_active", "sh3d_active", "sh_res_val", "sh_soft_px", "camocc_active", "camocc_w", "camocc_h", "camocc_max_z", "uv_span_x", "uv_span_y", "occ_flat", "occ_desc", "need_manual_hint", "lower_2d_occ_layer", "occ3d_int", "mode_int", "want_2d_occ", "want_3d_occ", "need_3d_sh", "TILES_X", "ATLAS", "TILE", "PAD", "k_gain_base", "fog_amt", "f_scale", "wox", "woy", "woz", "poly_alpha", "min_opt_a", "cam_comp", "cam_mode2_note", "k_gain_gpu", "n_chunks", "ps_ok", "ps_err", "aw", "ah", "total_v", "peak_r", "peak_g", "peak_b", "peak_a", "rxw", "ryw", "rzw", "dxw", "dyw", "dzw", "cam_along_sf", "ch_first", "ch_last", "ch_step", "si_reverse", "any_occ_active", "eff_occ_mode", "peak_m", "interact_drag", "_init_key", "_init_t0" }

-- v3.13: 公式 Lua 仕様 obj.getoption("gui")（編集プレビュー中 true / 動画出力中 false）で
-- HUD・案内オーバーレイを一括ゲートし、書き出し映像へ診断テキストが焼き込まれるのを完全防止する。
local function is_editor_gui_visible()
    local gui_on = true
    pcall(function()
        local g = obj.getoption("gui")
        if type(g) == "boolean" then gui_on = g end
    end)
    return gui_on
end

-- v3.13: 3D 透視カメラ下の HUD 縮小率。ホスト 3D カメラの近クリップ面 zn (~102.4..128) より十分奥
-- (z_hud = 288 @ focal_d = 1024) を確保して HUD 消失を根治しつつ、シーン内光源・遮蔽物より手前を保つ。
local function calc_hud_near_scale(focal_d)
    if (VL_BF.cam_mode or 0) == 0 or VL_BF.has_exact_cam == false or VL_BF.is_edit then
        return 1.0
    end
    local fd = math.max(tonumber(focal_d) or 1024.0, 1.0)
    local z_hud = math.max(256.0, fd * 0.28125)
    local cli_h = VL_BF.cam_light_info
    local cl_h  = cli_h and cli_h.primary or nil
    if cl_h ~= nil and (tonumber(cl_h.ltype) or 0) == 5 then
        local lpx = (tonumber(cl_h.px) or 0.0) - (VL_BF.cam_x or 0.0)
        local lpy = (tonumber(cl_h.py) or 0.0) - (VL_BF.cam_y or 0.0)
        local lpz = (tonumber(cl_h.pz) or 0.0) - (VL_BF.cam_z or -1024.0)
        local z_lp = lpx * (VL_BF.f_x or 0.0) + lpy * (VL_BF.f_y or 0.0) + lpz * (VL_BF.f_z or 1.0)
        if z_lp > 220.0 then
            z_hud = math.min(z_hud, math.max(192.0, z_lp * 0.72))
        end
    end
    return math.min(1.0, z_hud / fd)
end

local function draw_center_notice(msg, cam_x, cam_y, cam_z, focal_d, f_x, f_y, f_z, obj_x, obj_y, obj_z)
    if is_editor_gui_visible() then
        pcall(function()
            local k_hud = calc_hud_near_scale(focal_d)
            obj.setoption("drawtarget", "framebuffer")
            obj.setoption("culling", 0)
            obj.setoption("sampler", "clamp")
            obj.setoption("billboard", 3)
            pcall(function() obj.setfont("", 34, 3, 0xffffff, 0x1f1f33) end)
            obj.load("text", msg)
            obj.ox = cam_x + f_x * (focal_d * k_hud) - obj_x
            obj.oy = cam_y + f_y * (focal_d * k_hud) - obj_y
            obj.oz = cam_z + f_z * (focal_d * k_hud) - obj_z
            obj.draw(0, 0, 0, k_hud, 0.95)
        end)
    end
    pcall(function() obj.setoption("draw_state", true) end)
end

