local function vl_bf_render()
    -- 5) GPU マルチパス体積描画
    ------------------------------------------------------------
    -- 光場（v2）用の光源色グリッド / 遮蔽グリッドは、mip と遮蔽キャッシュが
    -- 生きているこのタイミングで作る（フレーム末の公開時には既に解放済みのため）。
    pcall(vl_lf_prepare)
    VL_BF.TILES_X, VL_BF.ATLAS, VL_BF.TILE, VL_BF.PAD= 8, 2560, 320, 2.0
    VL_BF.k_gain_base= (P.intensity / 100.0) * 0.155 / VL_BF.L_char
    VL_BF.fog_amt= math.max(0.0, math.min(1.0, P.fog / 100.0))
    VL_BF.f_scale= math.max(10.0, P.fog_scale)
    VL_BF.wox, VL_BF.woy, VL_BF.woz= 0.0, 0.0, 0.0
    do
        local max_w, max_h = 4096, 4096
        pcall(function()
            local mw, mh = obj.getinfo("image_max")
            if type(mw) == "number" and mw >= 512 then max_w = mw end
            if type(mh) == "number" and mh >= 512 then max_h = mh end
        end)
        local want_atlas = (P.res_mode == 0) and 3584 or ((P.res_mode == 2) and 1536 or 2560)
        VL_BF.ATLAS = math.floor(math.min(want_atlas, max_w, max_h) / VL_BF.TILES_X) * VL_BF.TILES_X
        if VL_BF.ATLAS < 512 then VL_BF.ATLAS = 512 end
        VL_BF.TILE = VL_BF.ATLAS / VL_BF.TILES_X
        local t_now = type(obj.time) == "number" and obj.time or 0.0
        local wrad = math.rad(P.wind_dir)
        VL_BF.wox = -math.cos(wrad) * P.wind_speed * t_now + P.seed * 31.7
        VL_BF.woy = -math.sin(wrad) * P.wind_speed * t_now + P.seed * 67.3
        VL_BF.woz = P.wind_speed * 0.35 * t_now + P.seed * 109.1
    end

    obj.setoption("drawtarget", "framebuffer")
    obj.setoption("culling", 0)
    obj.setoption("sampler", "clamp")
    pcall(function() obj.setoption("billboard", 0) end)
    pcall(function() obj.setoption("blend", "none") end)

    -- 3D OIT シーンでは lua_drawpoly(verts, 4, poly_alpha) が v1.z = poly_alpha を直接渡す（機械語 0x1404404b0 検証済）。
    -- v3.12: 仮想スポット光源（ltype = 4/5）では解析的「視線∩光円錐∩球 L_tot」6点弦積分 ＋ min_opt_a = 4/255（poly_alpha = 0.064125,
    -- 外縁ゼロ隣接 r4.w = 0.001006 ハードウェア遮断を維持したまま 4 倍高精細化して p^4 破棄クリフ＆馬蹄形アーチを根治、
    -- 低輝度 s255 < 4 はスライス位相コヒーレント生存で白床面暗化ゼロを維持）＋ IG 準乱数ディザを適用。
    VL_BF.poly_alpha= 0.27
    VL_BF.min_opt_a= 1.0 / 255.0
    VL_BF.cam_comp= 1.0
    VL_BF.cam_mode2_note= ""
    local cli_r = VL_BF.cam_light_info or { count = 0, unlit = 1.0, amb_r = 0.0, amb_g = 0.0, amb_b = 0.0, peak_gain = 1.0, primary = nil }
    local cl_pr = cli_r.primary
    local cam_comp_mode = 0.0
    if P.cam_light_mode == 1 and cl_pr ~= nil then
        cam_comp_mode = 1.0
    elseif (P.cam_light_mode == 0 or P.cam_light_mode == 2) and (cli_r.count or 0) > 0 and not cli_r.suppressed then
        cam_comp_mode = 2.0
        if P.cam_light_mode == 2 then
            VL_BF.cam_mode2_note = " mode2=host-comp"
        end
    end
    local lt_pr = (cl_pr ~= nil) and (tonumber(cl_pr.ltype) or 0) or 0
    local is_virtual_spot = VL_BF.use_cam_virtual_src and (cam_comp_mode > 0.5 and cam_comp_mode < 1.5)
        and (lt_pr == 4 or lt_pr == 5)
    local spot_ray_env_mode = is_virtual_spot and 1.0 or 0.0
    local wboit_ka = 0.0
    if P.fill_mode ~= 1 then
        if is_virtual_spot then
            VL_BF.poly_alpha = 0.064125
            VL_BF.min_opt_a  = 4.0 / 255.0
            VL_BF.cam_comp   = 0.48
            wboit_ka         = 0.0
        else
            VL_BF.poly_alpha = 0.27
            VL_BF.min_opt_a  = 1.0 / 255.0
            wboit_ka         = 0.0
        end
    end
    VL_BF.k_gain_gpu= VL_BF.k_gain_base * (VL_BF.base_alpha / VL_BF.poly_alpha) * 1.05 * VL_BF.cam_comp

    VL_BF.n_chunks= math.ceil(VL_BF.total_sl / 64)
    VL_BF.ps_ok= VL_BF.mip_ready
    VL_BF.ps_err= nil
    VL_BF.aw, VL_BF.ah= 0, 0
    VL_BF.total_v= 0
    VL_BF.peak_r, VL_BF.peak_g, VL_BF.peak_b, VL_BF.peak_a= 0, 0, 0, 0
    if P.ab_enable then
        AB.run_foreground_blend()
        obj.setoption("drawtarget", "framebuffer")
        obj.setoption("culling", 0)
        obj.setoption("sampler", "clamp")
        pcall(function() obj.setoption("billboard", 0) end)
        pcall(function() obj.setoption("blend", "none") end)
    end

    if P.show_info and VL_BF.mip_ready then
        local hp = _VL_PERSIST.hud_peak
        local now_hp = vl_wall_time_sec()
        local cur_f_hp = type(obj.frame) == "number" and vl_scene_frame() or -1
        if VL_BF.interact_drag and type(hp) == "table" and hp.frame == cur_f_hp then
            VL_BF.peak_r, VL_BF.peak_g, VL_BF.peak_b, VL_BF.peak_a = hp.r, hp.g, hp.b, hp.a
        elseif type(hp) == "table" and hp.frame == cur_f_hp and (now_hp - (hp.t or -999.0)) < 0.20 then
            VL_BF.peak_r, VL_BF.peak_g, VL_BF.peak_b, VL_BF.peak_a = hp.r, hp.g, hp.b, hp.a
        else
            pcall(function()
                local ok_ffi, ffi = pcall(require, "ffi")
                if ok_ffi and ffi ~= nil then
                    local ptr, mw_p, mh_p = obj.getpixeldata("cache:vl3d/mip")
                    if ptr ~= nil and type(mw_p) == "number" and type(mh_p) == "number" and mw_p > 0 and mh_p > 0 then
                        local px = ffi.cast("uint8_t*", ptr)
                        local step_x = math.max(1, math.floor(mw_p / 48))
                        local step_y = math.max(1, math.floor(mh_p / 48))
                        local best_s = -1
                        for y = 0, mh_p - 1, step_y do
                            local row = y * mw_p
                            for x = 0, mw_p - 1, step_x do
                                local idx = (row + x) * 4
                                local r, g, b, a = px[idx], px[idx + 1], px[idx + 2], px[idx + 3]
                                local sc = (r + g + b) * a
                                if a > 4 and sc > best_s then
                                    best_s = sc
                                    VL_BF.peak_r, VL_BF.peak_g, VL_BF.peak_b, VL_BF.peak_a = r, g, b, a
                                end
                            end
                        end
                        _VL_PERSIST.hud_peak = {
                            frame = cur_f_hp, t = now_hp,
                            r = VL_BF.peak_r, g = VL_BF.peak_g, b = VL_BF.peak_b, a = VL_BF.peak_a,
                        }
                    end
                end
            end)
        end
    end

    VL_BF.rxw, VL_BF.ryw, VL_BF.rzw= VL_BF.sr_x * VL_BF.hw, VL_BF.sr_y * VL_BF.hw, VL_BF.sr_z * VL_BF.hw
    VL_BF.dxw, VL_BF.dyw, VL_BF.dzw= VL_BF.sd_x * VL_BF.hh, VL_BF.sd_y * VL_BF.hh, VL_BF.sd_z * VL_BF.hh

    VL_BF.cam_along_sf= (VL_BF.cam_x - VL_BF.mid_x) * VL_BF.sf_x + (VL_BF.cam_y - VL_BF.mid_y) * VL_BF.sf_y + (VL_BF.cam_z - VL_BF.mid_z) * VL_BF.sf_z
    VL_BF.ch_first, VL_BF.ch_last, VL_BF.ch_step = nil, nil, nil
    VL_BF.si_reverse = nil
    if VL_BF.cam_along_sf >= 0.0 then
        VL_BF.ch_first, VL_BF.ch_last, VL_BF.ch_step = 0, VL_BF.n_chunks - 1, 1
        VL_BF.si_reverse = false
    else
        VL_BF.ch_first, VL_BF.ch_last, VL_BF.ch_step = VL_BF.n_chunks - 1, 0, -1
        VL_BF.si_reverse = true
    end

    VL_BF.any_occ_active= (VL_BF.n_occ_active > 0) or (VL_BF.sh3d_active > 0.5) or (VL_BF.camocc_active > 0.5)
    VL_BF.eff_occ_mode= VL_BF.any_occ_active and (P.occ_mode * 1.0) or 0.0

    local cb_unlit = tonumber(cli_r.unlit) or 1.0
    local cb_amb_r = tonumber(cli_r.amb_r) or 0.0
    local cb_amb_g = tonumber(cli_r.amb_g) or 0.0
    local cb_amb_b = tonumber(cli_r.amb_b) or 0.0
    local cb_ltype = (cl_pr ~= nil) and (tonumber(cl_pr.ltype) or 0.0) or 0.0
    local cb_a01   = math.max(0.0, math.min(1.0, (VL_BF.cam_atten_pct or 0.0) / 100.0))
    local cb_att_k = cb_a01 * cb_a01 * 2.2
    local cb_cl_r  = (cl_pr ~= nil) and (tonumber(cl_pr.r) or 0.0) or 0.0
    local cb_cl_g  = (cl_pr ~= nil) and (tonumber(cl_pr.g) or 0.0) or 0.0
    local cb_cl_b  = (cl_pr ~= nil) and (tonumber(cl_pr.b) or 0.0) or 0.0
    local cb_cl_px = (cl_pr ~= nil) and (tonumber(cl_pr.px) or 0.0) or 0.0
    local cb_cl_py = (cl_pr ~= nil) and (tonumber(cl_pr.py) or 0.0) or 0.0
    local cb_cl_pz = (cl_pr ~= nil) and (tonumber(cl_pr.pz) or 0.0) or 0.0
    local cb_sum_dp = math.max(0.0, (tonumber(cli_r.peak_gain) or 1.0) - cb_unlit - math.max(cb_amb_r, cb_amb_g, cb_amb_b))
    local cb_other_lum = 0.5 * math.max(0.0, cb_sum_dp - ((cl_pr ~= nil) and (tonumber(cl_pr.lum) or 0.0) or 0.0))
    local cb_in_rad  = VL_BF.cam_spot_inner_rad or 0.0
    local cb_out_rad = VL_BF.cam_spot_outer_rad or 0.0
    local cb_tgt_r, cb_tgt_g, cb_tgt_b = 1.0, 1.0, 1.0
    if cam_comp_mode > 0.5 and cam_comp_mode < 1.5 and cl_pr ~= nil then
        local cl_lum = tonumber(cl_pr.lum) or 0.0
        if P.light_color ~= nil then
            if VL_BF.use_cam_virtual_src then
                cb_tgt_r, cb_tgt_g, cb_tgt_b = VL_BF.lc_r * cl_lum, VL_BF.lc_g * cl_lum, VL_BF.lc_b * cl_lum
            else
                cb_tgt_r, cb_tgt_g, cb_tgt_b = cl_lum, cl_lum, cl_lum
            end
        else
            cb_tgt_r, cb_tgt_g, cb_tgt_b = cb_cl_r, cb_cl_g, cb_cl_b
        end
    end

    local use_dir_norm = (cam_comp_mode > 0.5) and (cl_pr ~= nil) and (cb_ltype > 0.5 and cb_ltype < 2.5)
    local use_pos_grid = (cam_comp_mode > 0.5) and (cl_pr ~= nil) and (cb_ltype > 2.5)
    local flip_wind = (VL_BF.cam_along_sf >= 0.0)
    local has_self_rot = (math.abs(VL_BF.obj_rx) + math.abs(VL_BF.obj_ry) + math.abs(VL_BF.obj_rz)) > 1e-5
    local inv_zoom = 1.0 / VL_BF.zoom
    local dir_vnx, dir_vny, dir_vnz = 0.0, 0.0, -1.0
    local def_lwx, def_lwy, def_lwz = -VL_BF.sf_x, -VL_BF.sf_y, -VL_BF.sf_z
    if cl_pr ~= nil then
        local dl_n = math.max(1e-5, math.sqrt((cl_pr.dx or 0.0)^2 + (cl_pr.dy or 0.0)^2 + (cl_pr.dz or 0.0)^2))
        if (cb_ltype > 0.5 and cb_ltype < 2.5) or cb_ltype > 3.5 then
            def_lwx, def_lwy, def_lwz = (cl_pr.dx or 0.0) / dl_n, (cl_pr.dy or 0.0) / dl_n, (cl_pr.dz or 0.0) / dl_n
        end
        if use_dir_norm then
            dir_vnx, dir_vny, dir_vnz = inv_rot_xyz(def_lwx, def_lwy, def_lwz, VL_BF.obj_rx, VL_BF.obj_ry, VL_BF.obj_rz)
        end
    end
    local KNOT_M = { -8.0, -2.6, -0.95, -0.32, 0.0, 0.32, 0.95, 2.6, 8.0 }

    for ch = VL_BF.ch_first, VL_BF.ch_last, VL_BF.ch_step do
        if not VL_BF.ps_ok then break end
        local ch_start = ch * 64
        local ch_n = math.min(64, VL_BF.total_sl - ch_start)
        local s_ch_min = VL_BF.s_min + ch_start * VL_BF.dz

        local consts = {
            VL_BF.TILE, VL_BF.TILES_X, ch_n, VL_BF.PAD,
            VL_BF.mid_x, VL_BF.mid_y, VL_BF.mid_z, s_ch_min,
            VL_BF.sf_x, VL_BF.sf_y, VL_BF.sf_z, VL_BF.dz,
            VL_BF.sr_x, VL_BF.sr_y, VL_BF.sr_z, VL_BF.hw,
            VL_BF.sd_x, VL_BF.sd_y, VL_BF.sd_z, VL_BF.hh,
            VL_BF.wx, VL_BF.wy, VL_BF.wz, 1.0 / (2.0 * VL_BF.Hx),
            VL_BF.Wrx, VL_BF.Wry, VL_BF.Wrz, 1.0 / (2.0 * VL_BF.Hy),
            VL_BF.Wdx, VL_BF.Wdy, VL_BF.Wdz, VL_BF.dS_wn,
            VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz, VL_BF.u_wn,
            VL_BF.sx_p, VL_BF.sy_p, VL_BF.sz_p, VL_BF.mode_kind,
            VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.is_edit and 1.0 or 0.0,
            VL_BF.k_dec, VL_BF.L_tot, VL_BF.inv_end, VL_BF.k_gain_gpu,
            ch_start, (P.fill_mode == 1) and 1.0 or 0.0, VL_BF.min_opt_a, 1.0,
            VL_BF.lc_r, VL_BF.lc_g, VL_BF.lc_b, VL_BF.fog_amt,
            VL_BF.wox, VL_BF.woy, VL_BF.woz, 1.0 / VL_BF.f_scale,
            VL_BF.n_occ_active, VL_BF.eff_occ_mode, VL_BF.occ_str_val, VL_BF.sh3d_active,
            VL_BF.uv_span_x, VL_BF.uv_span_y, VL_BF.sh_res_val, VL_BF.sh_soft_px,
            VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.camocc_active,
            VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, VL_BF.focal_d / math.max(VL_BF.scr_w, 1.0),
            VL_BF.d_x, VL_BF.d_y, VL_BF.d_z, VL_BF.focal_d / math.max(VL_BF.scr_h, 1.0),
            VL_BF.camocc_w, VL_BF.camocc_h, VL_BF.camocc_max_z, VL_BF.occ3d_int * 1.0,
            cam_comp_mode, cb_unlit, cb_ltype, cb_att_k,
            cb_amb_r, cb_amb_g, cb_amb_b, cb_in_rad,
            cb_cl_r, cb_cl_g, cb_cl_b, cb_out_rad,
            cb_cl_px, cb_cl_py, cb_cl_pz, cb_other_lum,
            VL_BF.cam_ray_dx, VL_BF.cam_ray_dy, VL_BF.cam_ray_dz, spot_ray_env_mode,
            cb_tgt_r, cb_tgt_g, cb_tgt_b, wboit_ka,
        }
        local base_len = #consts
        for oi = 1, MAX_OCC * 16 do
            consts[base_len + oi] = VL_BF.occ_flat[oi]
        end

        if (VL_BF.n_occ_active or 0) == 0 then pcall(function() obj.clearbuffer(VL_BF.occ_tex_name, 4, 4) end) end
        if (VL_BF.sh3d_active or 0) < 0.5 then pcall(function() obj.clearbuffer(VL_BF.shmap_tex_name, 4, 4) end) end
        if (VL_BF.camocc_active or 0) < 0.5 then pcall(function() obj.clearbuffer(VL_BF.camocc_tex_name, 4, 4) end) end
        local ok_ps, err_ps = pcall(function()
            local c1 = obj.clearbuffer("object", VL_BF.ATLAS, VL_BF.ATLAS)
            local c2 = obj.pixelshader("beamatlas", "object",
                { "cache:vl3d/mip", VL_BF.occ_tex_name, VL_BF.shmap_tex_name, VL_BF.camocc_tex_name },
                consts, "copy", "clip")
            VL_BF.aw, VL_BF.ah = obj.getpixel()
            if (c1 == false) or (c2 == false) or type(VL_BF.aw) ~= "number" or VL_BF.aw <= 0 then
                VL_BF.ps_ok = false
            end
        end)
        if not ok_ps then
            VL_BF.ps_ok = false
            VL_BF.ps_err = err_ps
            break
        end


        if VL_BF.ps_ok then
            local verts = {}
            local vi = 0
            local s0_i = VL_BF.si_reverse and (ch_n - 1) or 0
            local s1_i = VL_BF.si_reverse and 0 or (ch_n - 1)
            local st_i = VL_BF.si_reverse and -1 or 1
            local hw_s, hh_s = VL_BF.hw, VL_BF.hh
            local inv_2hw_s = 1.0 / math.max(2.0 * hw_s, 1e-4)
            local inv_2hh_s = 1.0 / math.max(2.0 * hh_s, 1e-4)
            local srx, sry, srz = VL_BF.sr_x, VL_BF.sr_y, VL_BF.sr_z
            local sdx, sdy, sdz = VL_BF.sd_x, VL_BF.sd_y, VL_BF.sd_z
            local sfx, sfy, sfz = VL_BF.sf_x, VL_BF.sf_y, VL_BF.sf_z
            local obj_x, obj_y, obj_z = VL_BF.obj_x, VL_BF.obj_y, VL_BF.obj_z
            local orx_d, ory_d, orz_d = VL_BF.obj_rx, VL_BF.obj_ry, VL_BF.obj_rz

            for si = s0_i, s1_i, st_i do
                local tx_i = si % VL_BF.TILES_X
                local ty_i = math.floor(si / VL_BF.TILES_X)
                local u0 = (tx_i * VL_BF.TILE + VL_BF.PAD) / VL_BF.ATLAS
                local u1 = ((tx_i + 1) * VL_BF.TILE - VL_BF.PAD) / VL_BF.ATLAS
                local v0 = (ty_i * VL_BF.TILE + VL_BF.PAD) / VL_BF.ATLAS
                local v1 = ((ty_i + 1) * VL_BF.TILE - VL_BF.PAD) / VL_BF.ATLAS

                local s_val = s_ch_min + (si + 0.5) * VL_BF.dz
                local cwx = VL_BF.mid_x + sfx * s_val
                local cwy = VL_BF.mid_y + sfy * s_val
                local cwz = VL_BF.mid_z + sfz * s_val

                if use_pos_grid then
                    local dlcx, dlcy, dlcz = cb_cl_px - cwx, cb_cl_py - cwy, cb_cl_pz - cwz
                    local lx_s = dlcx * srx + dlcy * sry + dlcz * srz
                    local ly_s = dlcx * sdx + dlcy * sdy + dlcz * sdz
                    local lz_s = dlcx * sfx + dlcy * sfy + dlcz * sfz
                    local rk = math.max(24.0, math.abs(lz_s))

                    local xk = { -hw_s }
                    local nxk = 1
                    for ki = 1, 9 do
                        local kv = lx_s + KNOT_M[ki] * rk
                        if kv > -hw_s + 2.0 and kv < hw_s - 2.0 and kv > xk[nxk] + 1.5 then
                            nxk = nxk + 1
                            xk[nxk] = kv
                        end
                    end
                    nxk = nxk + 1
                    xk[nxk] = hw_s

                    local yk = { -hh_s }
                    local nyk = 1
                    for ki = 1, 9 do
                        local kv = ly_s + KNOT_M[ki] * rk
                        if kv > -hh_s + 2.0 and kv < hh_s - 2.0 and kv > yk[nyk] + 1.5 then
                            nyk = nyk + 1
                            yk[nyk] = kv
                        end
                    end
                    nyk = nyk + 1
                    yk[nyk] = hh_s

                    local du_s = u1 - u0
                    local dv_s = v1 - v0
                    local c_dx, c_dy, c_dz = cwx - obj_x, cwy - obj_y, cwz - obj_z
                    local nodes = {}
                    for iy = 1, nyk do
                        local yv = yk[iy]
                        local vv = v0 + dv_s * ((yv + hh_s) * inv_2hh_s)
                        local ywx, ywy, ywz = sdx * yv, sdy * yv, sdz * yv
                        local r_base = (iy - 1) * nxk
                        for ix = 1, nxk do
                            local xv = xk[ix]
                            local uu = u0 + du_s * ((xv + hw_s) * inv_2hw_s)
                            local off_x = srx * xv + ywx
                            local off_y = sry * xv + ywy
                            local off_z = srz * xv + ywz
                            local px, py, pz = c_dx + off_x, c_dy + off_y, c_dz + off_z
                            local lx_v, ly_v, lz_v = dlcx - off_x, dlcy - off_y, dlcz - off_z
                            local ll2 = lx_v * lx_v + ly_v * ly_v + lz_v * lz_v
                            local nwx, nwy, nwz
                            if ll2 > 1e-4 then
                                local inv_ll = 1.0 / math.sqrt(ll2)
                                nwx, nwy, nwz = lx_v * inv_ll, ly_v * inv_ll, lz_v * inv_ll
                            else
                                nwx, nwy, nwz = def_lwx, def_lwy, def_lwz
                            end
                            if has_self_rot then
                                px, py, pz = inv_rot_xyz(px, py, pz, orx_d, ory_d, orz_d)
                                nwx, nwy, nwz = inv_rot_xyz(nwx, nwy, nwz, orx_d, ory_d, orz_d)
                            end
                            nodes[r_base + ix] = { px * inv_zoom, py * inv_zoom, pz * inv_zoom, uu, vv, nwx, nwy, nwz }
                        end
                    end

                    for iy = 1, nyk - 1 do
                        local r0 = (iy - 1) * nxk
                        local r1 = iy * nxk
                        for ix = 1, nxk - 1 do
                            local n00 = nodes[r0 + ix]
                            local n10 = nodes[r0 + ix + 1]
                            local n11 = nodes[r1 + ix + 1]
                            local n01 = nodes[r1 + ix]
                            if flip_wind then
                                verts[vi + 1] = n00
                                verts[vi + 2] = n01
                                verts[vi + 3] = n11
                                verts[vi + 4] = n10
                            else
                                verts[vi + 1] = n00
                                verts[vi + 2] = n10
                                verts[vi + 3] = n11
                                verts[vi + 4] = n01
                            end
                            vi = vi + 4
                        end
                    end
                elseif use_dir_norm then
                    local cx, cy, cz = cwx - obj_x, cwy - obj_y, cwz - obj_z
                    local p0x, p0y, p0z = cx - VL_BF.rxw - VL_BF.dxw, cy - VL_BF.ryw - VL_BF.dyw, cz - VL_BF.rzw - VL_BF.dzw
                    local p1x, p1y, p1z = cx + VL_BF.rxw - VL_BF.dxw, cy + VL_BF.ryw - VL_BF.dyw, cz + VL_BF.rzw - VL_BF.dzw
                    local p2x, p2y, p2z = cx + VL_BF.rxw + VL_BF.dxw, cy + VL_BF.ryw + VL_BF.dyw, cz + VL_BF.rzw + VL_BF.dzw
                    local p3x, p3y, p3z = cx - VL_BF.rxw + VL_BF.dxw, cy - VL_BF.ryw + VL_BF.dyw, cz - VL_BF.rzw + VL_BF.dzw
                    if has_self_rot then
                        p0x, p0y, p0z = inv_rot_xyz(p0x, p0y, p0z, orx_d, ory_d, orz_d)
                        p1x, p1y, p1z = inv_rot_xyz(p1x, p1y, p1z, orx_d, ory_d, orz_d)
                        p2x, p2y, p2z = inv_rot_xyz(p2x, p2y, p2z, orx_d, ory_d, orz_d)
                        p3x, p3y, p3z = inv_rot_xyz(p3x, p3y, p3z, orx_d, ory_d, orz_d)
                    end
                    local v00 = { p0x * inv_zoom, p0y * inv_zoom, p0z * inv_zoom, u0, v0, dir_vnx, dir_vny, dir_vnz }
                    local v10 = { p1x * inv_zoom, p1y * inv_zoom, p1z * inv_zoom, u1, v0, dir_vnx, dir_vny, dir_vnz }
                    local v11 = { p2x * inv_zoom, p2y * inv_zoom, p2z * inv_zoom, u1, v1, dir_vnx, dir_vny, dir_vnz }
                    local v01 = { p3x * inv_zoom, p3y * inv_zoom, p3z * inv_zoom, u0, v1, dir_vnx, dir_vny, dir_vnz }
                    if flip_wind then
                        verts[vi + 1], verts[vi + 2], verts[vi + 3], verts[vi + 4] = v00, v01, v11, v10
                    else
                        verts[vi + 1], verts[vi + 2], verts[vi + 3], verts[vi + 4] = v00, v10, v11, v01
                    end
                    vi = vi + 4
                else
                    local cx, cy, cz = cwx - obj_x, cwy - obj_y, cwz - obj_z
                    local p0x, p0y, p0z = cx - VL_BF.rxw - VL_BF.dxw, cy - VL_BF.ryw - VL_BF.dyw, cz - VL_BF.rzw - VL_BF.dzw
                    local p1x, p1y, p1z = cx + VL_BF.rxw - VL_BF.dxw, cy + VL_BF.ryw - VL_BF.dyw, cz + VL_BF.rzw - VL_BF.dzw
                    local p2x, p2y, p2z = cx + VL_BF.rxw + VL_BF.dxw, cy + VL_BF.ryw + VL_BF.dyw, cz + VL_BF.rzw + VL_BF.dzw
                    local p3x, p3y, p3z = cx - VL_BF.rxw + VL_BF.dxw, cy - VL_BF.ryw + VL_BF.dyw, cz - VL_BF.rzw + VL_BF.dzw
                    if has_self_rot then
                        p0x, p0y, p0z = inv_rot_xyz(p0x, p0y, p0z, orx_d, ory_d, orz_d)
                        p1x, p1y, p1z = inv_rot_xyz(p1x, p1y, p1z, orx_d, ory_d, orz_d)
                        p2x, p2y, p2z = inv_rot_xyz(p2x, p2y, p2z, orx_d, ory_d, orz_d)
                        p3x, p3y, p3z = inv_rot_xyz(p3x, p3y, p3z, orx_d, ory_d, orz_d)
                    end
                    verts[vi + 1] = { p0x * inv_zoom, p0y * inv_zoom, p0z * inv_zoom, u0, v0 }
                    verts[vi + 2] = { p1x * inv_zoom, p1y * inv_zoom, p1z * inv_zoom, u1, v0 }
                    verts[vi + 3] = { p2x * inv_zoom, p2y * inv_zoom, p2z * inv_zoom, u1, v1 }
                    verts[vi + 4] = { p3x * inv_zoom, p3y * inv_zoom, p3z * inv_zoom, u0, v1 }
                    vi = vi + 4
                end
            end
            obj.drawpoly(verts, 4, VL_BF.poly_alpha)
            VL_BF.total_v = VL_BF.total_v + vi
        end
    end

    -- 前景なじませ（AutoBlendLight 統合パス：ON 時のみ指定2D前景レイヤーへ逆光/縁取り/環境乗算を適用）
    -- 全作業キャッシュをフレーム終了時に即座に 1x1 へ解放（OpenGlow r3 / サブシーンと完全共存！）
    release_work_caches()

    local gui_hud_ok = is_editor_gui_visible()
    if gui_hud_ok and (VL_BF.need_manual_hint or P.show_info) then
        pcall(function() obj.setoption("blend", "none") end)
    end

    if (not VL_BF.ps_ok) and P.log_mode and VL_BF.ps_err ~= nil then
        pcall(function()
            local epfx = I18N.shader_err[VL_BF.ui_lang] or I18N.shader_err.ja
            print("@error", epfx .. tostring(VL_BF.ps_err))
        end)
    end

    if gui_hud_ok and VL_BF.need_manual_hint then
        pcall(function()
            local omsg = I18N.occ_unselected[VL_BF.ui_lang] or I18N.occ_unselected.ja
            local k_hud = calc_hud_near_scale(VL_BF.focal_d)
            obj.setoption("drawtarget", "framebuffer")
            obj.setoption("culling", 0)
            obj.setoption("sampler", "clamp")
            obj.setoption("billboard", 3)
            pcall(function() obj.setfont("", 28, 3, 0xffffff, 0x1f1f33) end)
            obj.load("text", omsg)
            obj.ox = VL_BF.cam_x + (VL_BF.f_x * VL_BF.focal_d - VL_BF.d_x * 320.0) * k_hud - VL_BF.obj_x
            obj.oy = VL_BF.cam_y + (VL_BF.f_y * VL_BF.focal_d - VL_BF.d_y * 320.0) * k_hud - VL_BF.obj_y
            obj.oz = VL_BF.cam_z + (VL_BF.f_z * VL_BF.focal_d - VL_BF.d_z * 320.0) * k_hud - VL_BF.obj_z
            obj.draw(0, 0, 0, k_hud, 0.94)
        end)
    end

    VL_BF.peak_m= VL_BF.peak_cache_dots / 1000000.0

    if gui_hud_ok and P.show_info then
        pcall(function()
            local hud_fmt = I18N.hud[VL_BF.ui_lang] or I18N.hud.ja
            local extra_tip = ""
            if VL_BF.lower_2d_occ_layer ~= nil then
                local tip_f = I18N.layer_order_tip[VL_BF.ui_lang] or I18N.layer_order_tip.ja
                extra_tip = string.format(tip_f, VL_BF.lower_2d_occ_layer)
            end
            local info_txt = string.format(
                hud_fmt,
                tostring(VL_BF.ps_ok), VL_BF.aw or 0, VL_BF.ah or 0, VL_BF.n_chunks, VL_BF.total_sl, VL_BF.dz, VL_BF.occ_desc, VL_BF.peak_m,
                VL_BF.ln, VL_BF.lw, VL_BF.lh, VL_BF.wx, VL_BF.wy, VL_BF.wz, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio * 100.0,
                P.length, VL_BF.L_tot, VL_BF.total_v, VL_BF.peak_r, VL_BF.peak_g, VL_BF.peak_b, VL_BF.peak_a, extra_tip
            )
            -- v3.13: 動画出力中 (obj.getoption("gui") == false) は HUD を自動非表示。編集プレビュー中は 3D カメラの近クリップ面 zn (~102.4..128) より奥かつ
            -- シーン内光源・遮蔽物より手前の z_hud (~288 @ focal_d=1024) へ等倍縮小投影し、HUD 消失とシャドウマップ横縞影の両方を完全根絶！
            local k_hud = calc_hud_near_scale(VL_BF.focal_d)
            obj.setoption("drawtarget", "framebuffer")
            obj.setoption("culling", 0)
            obj.setoption("sampler", "clamp")
            obj.setoption("billboard", 3)
            pcall(function() obj.setfont("", 18, 3, 0xffffff, 0x111122) end)
            obj.load("text", info_txt)
            obj.ox = VL_BF.cam_x + (VL_BF.f_x * VL_BF.focal_d - VL_BF.r_x * 420.0 - VL_BF.d_x * 400.0) * k_hud - VL_BF.obj_x
            obj.oy = VL_BF.cam_y + (VL_BF.f_y * VL_BF.focal_d - VL_BF.r_y * 420.0 - VL_BF.d_y * 400.0) * k_hud - VL_BF.obj_y
            obj.oz = VL_BF.cam_z + (VL_BF.f_z * VL_BF.focal_d - VL_BF.r_z * 420.0 - VL_BF.d_z * 400.0) * k_hud - VL_BF.obj_z
            obj.draw(0, 0, 0, k_hud, 0.92)
        end)
    end

    pcall(function() obj.setoption("draw_state", true) end)

    if P.log_mode then
        pcall(function()
            local log_fmt = I18N.log_info[VL_BF.ui_lang] or I18N.log_info.ja
            local msg = string.format(
                log_fmt,
                tostring(VL_BF.ps_ok), VL_BF.src_desc, VL_BF.occ_desc, VL_BF.aw or 0, VL_BF.ah or 0, VL_BF.n_chunks, VL_BF.total_sl, VL_BF.dz, VL_BF.total_v, VL_BF.base_ratio, VL_BF.peak_m
            ) .. (VL_BF.cam_mode2_note or "")
            -- 同一文の連続出力はログ窓の再描画だけで UI を止める（ドラッグ中に実測で洪水になる）。
            local now_l = vl_wall_time_sec()
            local recent = (now_l - (_VL_PERSIST.last_log_t or -999.0)) < 0.40
            local same = (_VL_PERSIST.last_log == msg)
            -- Identical lines, and any line during a drag, print at most once per 0.4s.
            if not ((same or VL_BF.interact_drag) and recent) then
                print("@info", msg)
                _VL_PERSIST.last_log = msg
                _VL_PERSIST.last_log_t = now_l
            end
        end)
    end
    if VL_BF._init_key ~= nil then
        local ikey_done = VL_BF._init_key
        _VL_PERSIST.inited_scenes = _VL_PERSIST.inited_scenes or {}
        _VL_PERSIST.inited_scenes[ikey_done] = true
        local t0_done = VL_BF._init_t0 or (_VL_PERSIST.init_t0_map and _VL_PERSIST.init_t0_map[ikey_done]) or nil
        if _VL_PERSIST.init_t0_map ~= nil then _VL_PERSIST.init_t0_map[ikey_done] = nil end
        if P.log_mode then
            pcall(function()
                local now_done = vl_wall_time_sec()
                local dt_i = math.max(0.0, now_done - (t0_done or now_done))
                local dfmt = I18N.init_done[VL_BF.ui_lang] or I18N.init_done.ja
                print("@info", string.format(dfmt, dt_i))
            end)
        end
        VL_BF._init_key = nil
    end
end

