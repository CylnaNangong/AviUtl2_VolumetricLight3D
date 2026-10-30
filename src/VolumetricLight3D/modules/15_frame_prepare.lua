local function vl_bf_reset()
    for vl_i = 1, #VL_BF_KEYS do
        VL_BF[VL_BF_KEYS[vl_i]] = nil
    end
    VL_BF._abort = false
    VL_BF._discard = nil
end

local function vl_bf_prepare()
    VL_BF.base_alpha= type(obj.alpha) == "number" and obj.alpha or 1.0
    if VL_BF.base_alpha < 0 then VL_BF.base_alpha = 0 elseif VL_BF.base_alpha > 1 then VL_BF.base_alpha = 1 end

    if P.length <= 0.5 or P.intensity <= 0.001 or VL_BF.base_alpha <= 0.0005 then
        pcall(function() obj.setoption("draw_state", true) end)
        do VL_BF._abort = true; return end
    end

    if P.ray_mode > 0 then
        pcall(function() obj.setanchor("light_x,light_y", 0) end)
    end

    VL_BF.obj_x= type(obj.x) == "number" and obj.x or 0.0
    VL_BF.obj_y= type(obj.y) == "number" and obj.y or 0.0
    VL_BF.obj_z= type(obj.z) == "number" and obj.z or 0.0
    VL_BF.obj_rx= type(obj.rx) == "number" and obj.rx or 0.0
    VL_BF.obj_ry= type(obj.ry) == "number" and obj.ry or 0.0
    VL_BF.obj_rz= type(obj.rz) == "number" and obj.rz or 0.0
    VL_BF.zoom= type(obj.zoom) == "number" and obj.zoom or 1.0
    if VL_BF.zoom < 1e-6 then VL_BF.zoom = 1.0 end

    VL_BF.ui_lang= detect_ui_lang()
    VL_BF.scr_w= (type(obj.screen_w) == "number" and obj.screen_w > 1) and obj.screen_w or 1920.0
    VL_BF.scr_h= (type(obj.screen_h) == "number" and obj.screen_h > 1) and obj.screen_h or 1080.0
    if (_VL_PERSIST.reentered or 0) == 0 and (P.src_layer > 0 or P.cam_light_mode == 1) then
        _VL_PERSIST.inited_scenes = _VL_PERSIST.inited_scenes or {}
        local scn_id = (type(obj.scene_id) == "number") and math.floor(obj.scene_id + 0.5) or ((type(obj.scene) == "number") and obj.scene or nil)
        if scn_id == nil then
            local br_i = vl3d_bridge()
            if br_i ~= nil and type(br_i.resolve_layer) == "function" then
                local sl_i = (type(obj.layer) == "number" and obj.layer > 0) and obj.layer or 1
                local ok_s, _, _, _, _, sid = pcall(br_i.resolve_layer, sl_i, vl_scene_frame())
                if ok_s and type(sid) == "number" then scn_id = sid end
            end
        end
        if scn_id == nil then scn_id = _VL_PERSIST.aup2_cache and _VL_PERSIST.aup2_cache.active_scene_id or 0 end
        local ikey = string.format("%s:%d:%dx%d", tostring(scn_id), type(obj.layer) == "number" and obj.layer or 0, math.floor(VL_BF.scr_w), math.floor(VL_BF.scr_h))
        if not _VL_PERSIST.inited_scenes[ikey] then
            _VL_PERSIST.init_t0_map = _VL_PERSIST.init_t0_map or {}
            _VL_PERSIST.init_started_map = _VL_PERSIST.init_started_map or {}
            if _VL_PERSIST.init_t0_map[ikey] == nil then
                _VL_PERSIST.init_t0_map[ikey] = vl_wall_time_sec()
            end
            VL_BF._init_key = ikey
            VL_BF._init_t0 = _VL_PERSIST.init_t0_map[ikey]
            if P.log_mode and not _VL_PERSIST.init_started_map[ikey] then
                _VL_PERSIST.init_started_map[ikey] = true
                pcall(function() print("@info", I18N.init_start[VL_BF.ui_lang] or I18N.init_start.ja) end)
            end
        end
    end
    VL_BF.cam_mode, VL_BF.has_exact_cam, VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d, VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, VL_BF.d_x, VL_BF.d_y, VL_BF.d_z= get_camera_basis()

    ------------------------------------------------------------
end

local function vl_bf_capture()
    -- 1) 光源レイヤーを "object" にロードして即座に走査
    ------------------------------------------------------------
    VL_BF.cam_light_info= inspect_or_suppress_camera_lights(P.cam_light_mode == 2)

    VL_BF.ln= P.src_layer
    VL_BF.self_layer= type(obj.layer) == "number" and obj.layer or -1
    VL_BF.use_cam_virtual_src= false
    VL_BF.cam_spot_inner_rad= 0.0
    VL_BF.cam_spot_outer_rad= 0.0
    VL_BF.cam_spot_eff_rad= 0.0
    VL_BF.cam_atten_pct= 0.0
    VL_BF.cam_ap_dist= nil
    VL_BF.cam_ray_dx, VL_BF.cam_ray_dy, VL_BF.cam_ray_dz= 0.0, 0.0, 1.0

    do
        local cl_p = VL_BF.cam_light_info.primary
        if cl_p ~= nil then
            local dl_p = math.max(1e-5, math.sqrt((cl_p.dx or 0.0)^2 + (cl_p.dy or 0.0)^2 + (cl_p.dz or 0.0)^2))
            VL_BF.cam_ray_dx = -(cl_p.dx or 0.0) / dl_p
            VL_BF.cam_ray_dy = -(cl_p.dy or 0.0) / dl_p
            VL_BF.cam_ray_dz = -(cl_p.dz or 0.0) / dl_p
            if P.cam_light_mode ~= 3 then
                local b_deg, a_pct, r_deg = read_cam_light_fx_extras(cl_p, VL_BF.self_layer)
                VL_BF.cam_atten_pct = a_pct
                local lt_p = tonumber(cl_p.ltype) or 0
                if lt_p == 4 or lt_p == 5 then
                    local out_r
                    if type(r_deg) == "number" then
                        out_r = (r_deg <= 1e-4 and b_deg <= 1e-4) and 0.0 or math.rad(r_deg + 0.5 * b_deg)
                    else
                        out_r = tonumber(cl_p.cos_ang) or 0.0
                    end
                    out_r = math.max(0.0, math.min(math.pi * 0.5, out_r))
                    local in_r = (type(r_deg) == "number")
                        and math.rad(math.max(0.0, r_deg - 0.5 * b_deg))
                        or math.max(0.0, out_r - math.rad(b_deg))
                    in_r = math.max(0.0, math.min(out_r, in_r))
                    VL_BF.cam_spot_inner_rad = in_r
                    VL_BF.cam_spot_outer_rad = out_r
                    VL_BF.cam_spot_eff_rad = math.min(math.rad(86.5), out_r)
                end
            end
        end
    end

    if VL_BF.ln <= 0 then
        if P.cam_light_mode == 1 then
            if VL_BF.cam_light_info.primary == nil then
                draw_center_notice(I18N.no_cam_light[VL_BF.ui_lang] or I18N.no_cam_light.ja, VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d, VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.obj_x, VL_BF.obj_y, VL_BF.obj_z)
                do VL_BF._abort = true; return end
            end
            VL_BF.use_cam_virtual_src = true
        else
            draw_center_notice(I18N.unselected[VL_BF.ui_lang] or I18N.unselected.ja, VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d, VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.obj_x, VL_BF.obj_y, VL_BF.obj_z)
            do VL_BF._abort = true; return end
        end
    end
    if P.cam_light_mode == 1 and VL_BF.cam_light_info.primary ~= nil then
        local cl_chk = VL_BF.cam_light_info.primary
        if (cl_chk.lum or 0.0) <= 1e-5 then
            pcall(function() obj.setoption("draw_state", true) end)
            do VL_BF._abort = true; return end
        end
        local lt_chk = tonumber(cl_chk.ltype) or 0
        if VL_BF.use_cam_virtual_src and (lt_chk == 4 or lt_chk == 5) and VL_BF.cam_spot_outer_rad <= 1e-4 then
            pcall(function() obj.setoption("draw_state", true) end)
            do VL_BF._abort = true; return end
        end
    end
    if VL_BF.ln > 0 and VL_BF.ln == VL_BF.self_layer then
        draw_center_notice(I18N.self_layer[VL_BF.ui_lang] or I18N.self_layer.ja, VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d, VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.obj_x, VL_BF.obj_y, VL_BF.obj_z)
        do VL_BF._abort = true; return end
    end

    VL_BF.thr_norm= math.max(0.0, math.min(0.999, P.threshold / 100.0))
    local _ = nil
    VL_BF.tex_loaded= false
    VL_BF.lw, VL_BF.lh= 0, 0
    VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s= 0.0, 1.0, 0.0, 1.0
    VL_BF.base_ratio= 0.45
    VL_BF.found_thr= true
    VL_BF.water_geo= nil

    VL_BF.exists= (not VL_BF.use_cam_virtual_src)
    if VL_BF.use_cam_virtual_src then
        pcall(function()
            local cl = VL_BF.cam_light_info.primary
            local shape_mode = 0.0
            local r_lim_arg = 2.0 / 128.0
            if cl ~= nil then
                local lt_c = tonumber(cl.ltype) or 0
                if lt_c == 4 or lt_c == 5 then
                    shape_mode = 0.0
                elseif lt_c ~= 1 and lt_c ~= 2 then
                    shape_mode = 2.0
                end
            end
            obj.clearbuffer("object", 256, 256)
            obj.pixelshader("ps_vl_white", "object", {}, { 1.0 / 128.0, 1.0 / 128.0, shape_mode, r_lim_arg }, "copy")
            VL_BF.lw, VL_BF.lh = 256, 256
            local lt_br = (cl ~= nil) and (tonumber(cl.ltype) or 0) or 0
            VL_BF.base_ratio = (lt_br == 1 or lt_br == 2) and 0.85 or 0.78
            VL_BF.tex_loaded = true
        end)
    else
        pcall(function()
            if obj.getvalue("layer" .. VL_BF.ln) == false then VL_BF.exists = false end
        end)
    end

    if VL_BF.exists then
        scan_aul2_translations()
        local w_res_raw, w_fx = get_fx_val(VL_BF.ln, WATER_FX_NAMES, WATER_RES_ITEMS, false)
        if w_res_raw ~= nil then
            local wfx_l  = w_fx and { w_fx } or WATER_FX_NAMES
            local w_sp   = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_SP_ITEMS, false))) or 5.0
            local w_dir  = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_DIR_ITEMS, false))) or -120.0
            local w_rip  = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_RIP_ITEMS, false))) or 30.0
            local w_tilt = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_TLT_ITEMS, false))) or 0.0
            local w_hw   = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_HW_ITEMS, false))) or 5.0
            local w_cl   = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_CL_ITEMS, false))) or 300.0
            local w_hs   = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_HS_ITEMS, false))) or 20.0
            local w_hb   = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_HB_ITEMS, false))) or 5.0
            local w_res  = math.min(math.max(tonumber(w_res_raw) or 50.0, 10.0), 100.0)
            local w_adj  = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_ADJ_ITEMS, false))) or 25.0
            -- AviUtl2 の getvalue は --color 項目に対して nil を返すため、light_color 指定時は優先継承
            local w_col = tonumber((get_fx_val(VL_BF.ln, wfx_l, WATER_COL_ITEMS, false))) or tonumber(P.light_color)
            if w_col == nil then
                local br_w = vl3d_bridge()
                if br_w ~= nil and type(br_w.get_water_color) == "function" then
                    local ok_wc, wc = pcall(br_w.get_water_color, VL_BF.ln, vl_scene_frame())
                    if ok_wc and type(wc) == "number" and wc >= 0 then w_col = wc end
                end
            end
            if w_col == nil then w_col = 0x66c8ff end

            local tex_sz = math.max(128, math.min(2048, math.floor((VL_BF.scr_w * w_res / 100.0) / 8 + 0.5) * 8))
            local sp_s   = w_sp / 10.0
            local drad   = math.rad(w_dir)
            local vx_w   = -math.sin(drad) * sp_s
            local vy_w   =  math.cos(drad) * sp_s
            local cx_w   = -math.sin(drad) * tex_sz * w_cl / 100.0
            local cy_w   =  math.cos(drad) * tex_sz * w_cl / 100.0
            local cr_w   = (math.floor(w_col / 0x10000) % 0x100) / 255.0
            local cg_w   = (math.floor(w_col / 0x100) % 0x100) / 255.0
            local cb_w   = (w_col % 0x100) / 255.0
            local t_cur  = type(obj.time) == "number" and obj.time or 0.0

            pcall(function()
                obj.clearbuffer("object", tex_sz, tex_sz)
                local ok_w = obj.pixelshader("ps_vl_water", "object", {}, {
                    tex_sz, t_cur, sp_s, 50.0 / w_res,
                    vx_w, vy_w, w_hw, w_rip * 0.5,
                    cx_w, cy_w, w_hb, (sp_s + 2.0) * w_hs,
                    cr_w, cg_w, cb_w, 1.0,
                }, "copy")
                local gw_t, gh_t = obj.getpixel()
                if ok_w ~= false and type(gw_t) == "number" and gw_t > 0 then
                    VL_BF.lw, VL_BF.lh = gw_t, gh_t
                    VL_BF.tex_loaded = true
                    VL_BF.water_geo = {
                        w_q = VL_BF.scr_w * 4.0,
                        y_w = VL_BF.scr_h,
                        oy_w = -VL_BF.scr_h * w_adj / 100.0,
                        tilt = w_tilt,
                    }
                end
            end)
            if VL_BF.tex_loaded then
                _, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
            end
        end

        if not VL_BF.tex_loaded then
            local ok_ffi_fb, ffi_fb = pcall(require, "ffi")
            local fb_save, fb_w, fb_h = nil, 0, 0
            local fb_gpu = false
            local fb_use_temp = false
            pcall(function()
                if type(obj.copybuffer) ~= "function" then return end
                local sw = (type(obj.screen_w) == "number" and obj.screen_w > 1) and math.floor(obj.screen_w) or 1920
                local sh = (type(obj.screen_h) == "number" and obj.screen_h > 1) and math.floor(obj.screen_h) or 1080
                if obj.copybuffer("tempbuffer", "framebuffer") ~= false then
                    if obj.clearbuffer("framebuffer", sw, sh) == false then
                        obj.copybuffer("framebuffer", "tempbuffer")
                        return
                    end
                    fb_gpu, fb_use_temp = true, true
                    fb_w, fb_h = sw, sh
                    return
                end
                if sw <= 4096 and sh <= 4096 and (sw * sh) <= 9000000 then
                    obj.clearbuffer("cache:vl3d/fb_bak", sw, sh)
                    if obj.copybuffer("cache:vl3d/fb_bak", "framebuffer") == false then return end
                    if obj.clearbuffer("framebuffer", sw, sh) == false then
                        obj.copybuffer("framebuffer", "cache:vl3d/fb_bak")
                        obj.clearbuffer("cache:vl3d/fb_bak", 1, 1)
                        return
                    end
                    fb_gpu = true
                    fb_w, fb_h = sw, sh
                end
            end)
            if (not fb_gpu) and ok_ffi_fb and ffi_fb ~= nil and type(obj.getpixeldata) == "function" then
                pcall(function()
                    local pf, pw, ph = obj.getpixeldata("framebuffer", "rgba")
                    if pf ~= nil and type(pw) == "number" and pw > 0 and type(ph) == "number" and ph > 0 then
                        fb_w, fb_h = pw, ph
                        fb_save = ffi_fb.new("uint8_t[?]", pw * ph * 4)
                        ffi_fb.copy(fb_save, pf, pw * ph * 4)
                        if type(obj.putpixeldata) == "function" then
                            ffi_fb.fill(pf, pw * ph * 4, 0)
                            obj.putpixeldata("framebuffer", pf, pw, ph, "rgba")
                        end
                    end
                end)
            end

            local function check_and_grab_fb_draw()
                if fb_save == nil or fb_w <= 0 or fb_h <= 0 or type(obj.putpixeldata) ~= "function" then
                    return false
                end
                local grabbed = false
                pcall(function()
                    local pf_c = obj.getpixeldata("framebuffer", "rgba")
                    if pf_c ~= nil then
                        local u32 = ffi_fb.cast("const uint32_t*", pf_c)
                        local total_px = fb_w * fb_h
                        local any_nz = false
                        local stp = math.max(1, math.floor(total_px / 16384))
                        for idx_p = 0, total_px - 1, stp do
                            if u32[idx_p] ~= 0 then any_nz = true; break end
                        end
                        if any_nz then
                            obj.clearbuffer("object", fb_w, fb_h)
                            local p_obj, ow_c, oh_c = obj.getpixeldata("object", "rgba")
                            if p_obj ~= nil and ow_c == fb_w and oh_c == fb_h then
                                ffi_fb.copy(p_obj, pf_c, total_px * 4)
                                obj.putpixeldata("object", p_obj, fb_w, fb_h, "rgba")
                                VL_BF.lw, VL_BF.lh = fb_w, fb_h
                                grabbed = true
                            end
                        end
                    end
                end)
                return grabbed
            end

            local function restore_fb_now()
                if fb_gpu then
                    pcall(function()
                        if fb_use_temp then
                            obj.copybuffer("framebuffer", "tempbuffer")
                            obj.clearbuffer("tempbuffer", 4, 4)
                        else
                            obj.copybuffer("framebuffer", "cache:vl3d/fb_bak")
                            obj.clearbuffer("cache:vl3d/fb_bak", 1, 1)
                        end
                    end)
                    return
                end
                if fb_save ~= nil and fb_w > 0 and fb_h > 0 and type(obj.putpixeldata) == "function" then
                    pcall(function()
                        local pf_r = obj.getpixeldata("framebuffer", "rgba")
                        if pf_r ~= nil then
                            ffi_fb.copy(pf_r, fb_save, fb_w * fb_h * 4)
                            obj.putpixeldata("framebuffer", pf_r, fb_w, fb_h, "rgba")
                        end
                    end)
                end
            end

            pcall(function() obj.load("layer", VL_BF.ln, true) end)
            if check_and_grab_fb_draw() then
                VL_BF.tex_loaded = true
                restore_fb_now()
                _, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
            else
                pcall(function() VL_BF.lw, VL_BF.lh = obj.getpixel() end)
                if type(VL_BF.lw) == "number" and type(VL_BF.lh) == "number" and VL_BF.lw > 0 and VL_BF.lh > 0 then
                    VL_BF.tex_loaded = true
                    restore_fb_now()
                    local ok_base
                    ok_base, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
                    if not ok_base then
                        if fb_save ~= nil and fb_w > 0 and fb_h > 0 and type(obj.putpixeldata) == "function" then
                            pcall(function()
                                local pf0 = obj.getpixeldata("framebuffer", "rgba")
                                if pf0 ~= nil then
                                    ffi_fb.fill(pf0, fb_w * fb_h * 4, 0)
                                    obj.putpixeldata("framebuffer", pf0, fb_w, fb_h, "rgba")
                                end
                            end)
                        end
                        local lw2, lh2 = 0, 0
                        pcall(function() obj.load("layer", VL_BF.ln) end)
                        if check_and_grab_fb_draw() then
                            restore_fb_now()
                            _, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
                        else
                            restore_fb_now()
                            pcall(function() lw2, lh2 = obj.getpixel() end)
                            if type(lw2) == "number" and lw2 > 0 and type(lh2) == "number" and lh2 > 0 then
                                VL_BF.lw, VL_BF.lh = lw2, lh2
                                _, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
                            end
                        end
                    end
                else
                    pcall(function() obj.load("layer", VL_BF.ln) end)
                    if check_and_grab_fb_draw() then
                        VL_BF.tex_loaded = true
                        restore_fb_now()
                        _, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
                    else
                        restore_fb_now()
                        pcall(function() VL_BF.lw, VL_BF.lh = obj.getpixel() end)
                        if type(VL_BF.lw) == "number" and type(VL_BF.lh) == "number" and VL_BF.lw > 0 and VL_BF.lh > 0 then
                            VL_BF.tex_loaded = true
                            _, VL_BF.found_thr, VL_BF.u0_s, VL_BF.u1_s, VL_BF.v0_s, VL_BF.v1_s, VL_BF.base_ratio = scan_pool_from_object(VL_BF.lw, VL_BF.lh, VL_BF.thr_norm)
                        end
                    end
                end
            end
        end
    end

    if not VL_BF.tex_loaded then
        local fmt = I18N.empty_layer[VL_BF.ui_lang] or I18N.empty_layer.ja
        draw_center_notice(string.format(fmt, VL_BF.ln), VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d, VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.obj_x, VL_BF.obj_y, VL_BF.obj_z)
        do VL_BF._abort = true; return end
    end

    if not VL_BF.found_thr then
        pcall(function() obj.setoption("draw_state", true) end)
        do VL_BF._abort = true; return end
    end

    VL_BF.wx, VL_BF.wy, VL_BF.wz, VL_BF._discard, VL_BF._discard, VL_BF.wrz, VL_BF.zx, VL_BF.zy, VL_BF.zz_l, VL_BF._discard, VL_BF.Wrx, VL_BF.Wry, VL_BF.Wrz, VL_BF.Wdx, VL_BF.Wdy, VL_BF.Wdz, VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz=
        read_layer_transform(VL_BF.ln, VL_BF.obj_x, VL_BF.obj_y, VL_BF.obj_z, VL_BF.obj_rx, VL_BF.obj_ry, VL_BF.obj_rz)

    if VL_BF.water_geo == nil and not VL_BF.use_cam_virtual_src then
        local src_f = vl_scene_frame()
        local src_li = resolve_live_scene_layer_object(VL_BF.ln, src_f)
        local src_is_txt, s_fx, s_fy, s_pl, s_pr, s_pt, s_pb = resolve_text_align_for_layer(VL_BF.ln, src_f, src_li, nil)
        if src_is_txt then
            local w_s, h_s = VL_BF.lw, VL_BF.lh
            local cx_s, cy_s = calc_text_center_shift(w_s, h_s, s_fx, s_fy, s_pl, s_pr, s_pt, s_pb)
            VL_BF.wx = VL_BF.wx - VL_BF.Wrx * (cx_s * VL_BF.zx) - VL_BF.Wdx * (cy_s * VL_BF.zy)
            VL_BF.wy = VL_BF.wy - VL_BF.Wry * (cx_s * VL_BF.zx) - VL_BF.Wdy * (cy_s * VL_BF.zy)
            VL_BF.wz = VL_BF.wz - VL_BF.Wrz * (cx_s * VL_BF.zx) - VL_BF.Wdz * (cy_s * VL_BF.zy)
        end
    end

    VL_BF.src_desc= string.format(I18N.src_desc[VL_BF.ui_lang] or I18N.src_desc.ja, VL_BF.ln, VL_BF.lw, VL_BF.lh)
    VL_BF.Hx= math.max(4.0, 0.5 * VL_BF.lw * VL_BF.zx)
    VL_BF.Hy= math.max(4.0, 0.5 * VL_BF.lh * VL_BF.zy)

    if VL_BF.water_geo ~= nil then
        local wq   = VL_BF.water_geo.w_q
        local tr   = math.rad(VL_BF.water_geo.tilt)
        local cy_l = VL_BF.water_geo.oy_w + VL_BF.water_geo.y_w
        local cz_l = wq * 0.75
        VL_BF.wx = VL_BF.wx + VL_BF.Wrx * 0.0 + VL_BF.Wdx * (cy_l * VL_BF.zy) + VL_BF.Wnx * (cz_l * VL_BF.zz_l)
        VL_BF.wy = VL_BF.wy + VL_BF.Wry * 0.0 + VL_BF.Wdy * (cy_l * VL_BF.zy) + VL_BF.Wny * (cz_l * VL_BF.zz_l)
        VL_BF.wz = VL_BF.wz + VL_BF.Wrz * 0.0 + VL_BF.Wdz * (cy_l * VL_BF.zy) + VL_BF.Wnz * (cz_l * VL_BF.zz_l)

        local ux_v, uy_v, uz_v = VL_BF.Wrx * (wq * VL_BF.zx), VL_BF.Wry * (wq * VL_BF.zx), VL_BF.Wrz * (wq * VL_BF.zx)
        local vy_l, vz_l = -wq * math.sin(tr) * VL_BF.zy, wq * math.cos(tr) * VL_BF.zz_l
        local vx_v = VL_BF.Wdx * vy_l + VL_BF.Wnx * vz_l
        local vy_v = VL_BF.Wdy * vy_l + VL_BF.Wny * vz_l
        local vz_v = VL_BF.Wdz * vy_l + VL_BF.Wnz * vz_l

        VL_BF.Hx = math.max(16.0, math.sqrt(ux_v * ux_v + uy_v * uy_v + uz_v * uz_v))
        VL_BF.Hy = math.max(16.0, math.sqrt(vx_v * vx_v + vy_v * vy_v + vz_v * vz_v))
        VL_BF.Wrx, VL_BF.Wry, VL_BF.Wrz = ux_v / VL_BF.Hx, uy_v / VL_BF.Hx, uz_v / VL_BF.Hx
        VL_BF.Wdx, VL_BF.Wdy, VL_BF.Wdz = vx_v / VL_BF.Hy, vy_v / VL_BF.Hy, vz_v / VL_BF.Hy
        local nx_v = VL_BF.Wry * VL_BF.Wdz - VL_BF.Wrz * VL_BF.Wdy
        local ny_v = VL_BF.Wrz * VL_BF.Wdx - VL_BF.Wrx * VL_BF.Wdz
        local nz_v = VL_BF.Wrx * VL_BF.Wdy - VL_BF.Wry * VL_BF.Wdx
        local nl_v = math.max(1e-6, math.sqrt(nx_v * nx_v + ny_v * ny_v + nz_v * nz_v))
        VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz = nx_v / nl_v, ny_v / nl_v, nz_v / nl_v
        if ((VL_BF.cam_x - VL_BF.wx) * (-VL_BF.Wnx) + (VL_BF.cam_y - VL_BF.wy) * (-VL_BF.Wny) + (VL_BF.cam_z - VL_BF.wz) * (-VL_BF.Wnz)) < 0.0 then
            VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz = -VL_BF.Wnx, -VL_BF.Wny, -VL_BF.Wnz
        end
    end

    VL_BF.cx_sh= ((VL_BF.u0_s + VL_BF.u1_s) * 0.5 - 0.5) * 2.0 * VL_BF.Hx
    VL_BF.cy_sh= ((VL_BF.v0_s + VL_BF.v1_s) * 0.5 - 0.5) * 2.0 * VL_BF.Hy
    VL_BF.R_fixed= math.min(math.max(VL_BF.Hx, VL_BF.Hy), 520.0)
    VL_BF.L_char= math.min(260.0, math.max(42.0, VL_BF.R_fixed * (0.12 + 0.36 * VL_BF.base_ratio * VL_BF.base_ratio)))

    ------------------------------------------------------------
end

