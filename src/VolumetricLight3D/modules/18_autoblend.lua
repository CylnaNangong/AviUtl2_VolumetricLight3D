---------------------------------------------------------------------
-- 前景なじませ（AutoBlendLight 統合サブシステム / MIT License: Copyright (c) 2026 nctype）
-- 単一 local テーブル AB に全ヘルパを集約しトップレベル local スロット消費を +1 に抑制
---------------------------------------------------------------------
local AB = {}

function AB.clamp(v, lo, hi)
    if v < lo then return lo elseif v > hi then return hi else return v end
end

function AB.rgb_to_hsl(r, g, b)
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    local maxc = math.max(r, g, b)
    local minc = math.min(r, g, b)
    local h, s = 0.0, 0.0
    local l = (maxc + minc) * 0.5
    if maxc ~= minc then
        local d = maxc - minc
        s = (l > 0.5) and (d / math.max(2.0 - maxc - minc, 1e-6)) or (d / math.max(maxc + minc, 1e-6))
        if maxc == r then
            h = (g - b) / d + (g < b and 6.0 or 0.0)
        elseif maxc == g then
            h = (b - r) / d + 2.0
        else
            h = (r - g) / d + 4.0
        end
        h = h / 6.0
    end
    return h, s, l
end

function AB.hue_to_rgb(p, q, t)
    if t < 0.0 then t = t + 1.0 end
    if t > 1.0 then t = t - 1.0 end
    if t < 1.0 / 6.0 then return p + (q - p) * 6.0 * t end
    if t < 0.5 then return q end
    if t < 2.0 / 3.0 then return p + (q - p) * (2.0 / 3.0 - t) * 6.0 end
    return p
end

function AB.hsl_to_rgb(h, s, l)
    if s <= 0.0 then
        local v = AB.clamp(math.floor(l * 255.0 + 0.5), 0, 255)
        return v, v, v
    end
    local q = (l < 0.5) and (l * (1.0 + s)) or (l + s - l * s)
    local p = 2.0 * l - q
    return
        AB.clamp(math.floor(AB.hue_to_rgb(p, q, h + 1.0 / 3.0) * 255.0 + 0.5), 0, 255),
        AB.clamp(math.floor(AB.hue_to_rgb(p, q, h) * 255.0 + 0.5), 0, 255),
        AB.clamp(math.floor(AB.hue_to_rgb(p, q, h - 1.0 / 3.0) * 255.0 + 0.5), 0, 255)
end

function AB.tune_backlight_color(r, g, b, mix_val, conf_vote, sat_val)
    local raw_r, raw_g, raw_b = r, g, b
    local h, s, l = AB.rgb_to_hsl(r, g, b)
    local lum_n = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    local max_rgb, min_rgb = math.max(r, g, b), math.min(r, g, b)
    local chroma_n = (max_rgb - min_rgb) / 255.0
    local color_conf = AB.clamp((chroma_n - 0.025) / 0.16, 0.0, 1.0)
    if lum_n < 0.10 then
        color_conf = color_conf * AB.clamp((max_rgb - 8.0) / 36.0, 0.0, 1.0)
    end
    s = AB.clamp(s * 0.58 * color_conf, 0.0, 0.46)
    l = l + (1.0 - l) * 0.16
    l = (lum_n < 0.06) and 0.70 or AB.clamp(l, 0.52, 0.84)
    local tr, tg, tb = AB.hsl_to_rgb(h, s, l)
    local w_mix = 0.06 + (1.0 - lum_n) * 0.05 + (1.0 - color_conf) * 0.08
    tr = AB.clamp(math.floor(tr * (1.0 - w_mix) + 255.0 * w_mix + 0.5), 0, 255)
    tg = AB.clamp(math.floor(tg * (1.0 - w_mix) + 255.0 * w_mix + 0.5), 0, 255)
    tb = AB.clamp(math.floor(tb * (1.0 - w_mix) + 255.0 * w_mix + 0.5), 0, 255)

    local m = AB.clamp(mix_val, 0.0, 200.0)
    if m <= 100.0 then
        local t = m * 0.01
        tr = AB.clamp(math.floor(raw_r + (tr - raw_r) * t + 0.5), 0, 255)
        tg = AB.clamp(math.floor(raw_g + (tg - raw_g) * t + 0.5), 0, 255)
        tb = AB.clamp(math.floor(raw_b + (tb - raw_b) * t + 0.5), 0, 255)
    else
        local t = (m - 100.0) * 0.01
        local nh, _, nl = AB.rgb_to_hsl(tr, tg, tb)
        local nr, ng, nb = AB.hsl_to_rgb(nh, 0.05, AB.clamp(nl + (1.0 - nl) * 0.55, 0.0, 0.94))
        tr = AB.clamp(math.floor(tr + (nr - tr) * t + 0.5), 0, 255)
        tg = AB.clamp(math.floor(tg + (ng - tg) * t + 0.5), 0, 255)
        tb = AB.clamp(math.floor(tb + (nb - tb) * t + 0.5), 0, 255)
    end

    local h2, s2, l2 = AB.rgb_to_hsl(tr, tg, tb)
    local auto_scale = 0.22 + 0.78 * AB.clamp(conf_vote, 0.0, 1.0)
    s2 = AB.clamp(s2 * auto_scale * AB.clamp(sat_val * 0.01, 0.0, 2.0), 0.0, 1.0)
    return AB.hsl_to_rgb(h2, s2, l2)
end

function AB.project_world_to_screen_uv(wx, wy, wz)
    local dx, dy, dz = wx - VL_BF.cam_x, wy - VL_BF.cam_y, wz - VL_BF.cam_z
    local px = dx * VL_BF.r_x + dy * VL_BF.r_y + dz * VL_BF.r_z
    local py = dx * VL_BF.d_x + dy * VL_BF.d_y + dz * VL_BF.d_z
    local zc = dx * VL_BF.f_x + dy * VL_BF.f_y + dz * VL_BF.f_z
    if not VL_BF.is_edit then
        if zc <= 2.0 then return nil, nil, zc end
        local sc = VL_BF.focal_d / zc
        px, py = px * sc, py * sc
    end
    return (0.5 * VL_BF.scr_w + px) / math.max(VL_BF.scr_w, 1.0),
           (0.5 * VL_BF.scr_h + py) / math.max(VL_BF.scr_h, 1.0),
           zc
end

function AB.sample_grid_region(grid_rgba, gw, gh, u_min, u_max, v_min, v_max, side_mode)
    local gx0 = AB.clamp(math.floor(u_min * gw), 0, gw - 1)
    local gx1 = AB.clamp(math.ceil(u_max * gw), 0, gw - 1)
    local gy0 = AB.clamp(math.floor(v_min * gh), 0, gh - 1)
    local gy1 = AB.clamp(math.ceil(v_max * gh), 0, gh - 1)
    local mid_u = 0.5 * (u_min + u_max)
    local span_u = math.max(0.08, 0.5 * (u_max - u_min))
    local span_v = math.max(0.08, 0.5 * (v_max - v_min))
    local mid_v = 0.5 * (v_min + v_max)

    local sum_r, sum_g, sum_b, sum_lum, sum_w = 0.0, 0.0, 0.0, 0.0, 0.0
    local hvx, hvy, hw_sum = 0.0, 0.0, 0.0

    for gy = gy0, gy1 do
        local v = (gy + 0.5) / gh
        local ny = (v - mid_v) / span_v
        for gx = gx0, gx1 do
            local u = (gx + 0.5) / gw
            if side_mode == 0 or (side_mode < 0 and u <= mid_u) or (side_mode > 0 and u >= mid_u) then
                local nx = (u - mid_u) / span_u
                local d2 = AB.clamp((nx * nx + ny * ny) * 0.35, 0.0, 1.0)
                local idx = (gy * gw + gx) * 4
                local r, g, b, ga = grid_rgba[idx], grid_rgba[idx + 1], grid_rgba[idx + 2], grid_rgba[idx + 3]
                local w = (ga > 4) and ((1.0 - 0.65 * d2) * (ga / 255.0)) or 0.0
                local lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                -- Favor brighter scene + volumetric-beam pixels when picking light color
                local bright_w = w * (0.25 + 0.75 * AB.clamp(lum / 180.0, 0.0, 1.0))
                sum_r = sum_r + r * bright_w
                sum_g = sum_g + g * bright_w
                sum_b = sum_b + b * bright_w
                sum_lum = sum_lum + lum * w
                sum_w = sum_w + bright_w

                local mx, mn = math.max(r, g, b), math.min(r, g, b)
                local chr = (mx - mn) / 255.0
                if chr > 0.018 then
                    local hh, ss = AB.rgb_to_hsl(r, g, b)
                    local cv = bright_w * AB.clamp((chr - 0.018) / 0.18, 0.0, 1.0) * ss
                    local ang = hh * 6.283185307
                    hvx = hvx + math.cos(ang) * cv
                    hvy = hvy + math.sin(ang) * cv
                    hw_sum = hw_sum + cv
                end
            end
        end
    end

    if sum_w <= 1e-5 then return 240, 240, 240, 0.5 end
    local r = AB.clamp(math.floor(sum_r / sum_w + 0.5), 0, 255)
    local g = AB.clamp(math.floor(sum_g / sum_w + 0.5), 0, 255)
    local b = AB.clamp(math.floor(sum_b / sum_w + 0.5), 0, 255)
    local cnt_px = math.max(1, (gx1 - gx0 + 1) * (gy1 - gy0 + 1))
    local mean_lum = sum_lum / math.max(sum_w, 1.0)
    local hue_agree = (hw_sum > 1e-5) and AB.clamp(math.sqrt(hvx * hvx + hvy * hvy) / hw_sum, 0.0, 1.0) or 0.0
    local avg_chr = (math.max(r, g, b) - math.min(r, g, b)) / 255.0
    local conf = hue_agree * AB.clamp((avg_chr - 0.015) / 0.16, 0.0, 1.0)
    local tr, tg, tb = AB.tune_backlight_color(r, g, b, P.ab_color_mix, conf, P.ab_saturation)
    local lf = AB.clamp((mean_lum - 8.0) / 80.0, 0.0, 1.0)
    lf = lf * lf * (3.0 - 2.0 * lf)
    return tr, tg, tb, lf
end

function AB.world_to_self_drawpoly(wx, wy, wz)
    local dx, dy, dz = inv_rot_xyz(
        wx - VL_BF.obj_x, wy - VL_BF.obj_y, wz - VL_BF.obj_z,
        VL_BF.obj_rx, VL_BF.obj_ry, VL_BF.obj_rz
    )
    return dx / VL_BF.zoom, dy / VL_BF.zoom, dz / VL_BF.zoom
end

function AB.run_foreground_blend()
    if not P.ab_enable or not VL_BF.ps_ok then return end
    if type(obj.copybuffer) ~= "function" then return end

    local self_l = VL_BF.self_layer
    local want_map = {}
    local cand_list = {}
    if P.ab_target == 1 then
        cand_list = parse_layer_list(P.ab_list, VL_BF.ln, self_l)
        for i = 1, #cand_list do want_map[cand_list[i]] = true end
    elseif P.occ_mode > 0 and P.occ_target == 2 then
        cand_list = parse_layer_list(P.occ_list, VL_BF.ln, self_l)
        for i = 1, #cand_list do want_map[cand_list[i]] = true end
    else
        local hi_scan = (self_l > 0) and (self_l - 1) or math.max(36, VL_BF.ln + 20)
        for oln = 1, hi_scan do
            if oln ~= VL_BF.ln and oln ~= self_l then
                cand_list[#cand_list + 1] = oln
                want_map[oln] = true
            end
        end
    end
    if #cand_list == 0 then return end

    -- Also scan all foreground layers < self_layer so non-target closer layers get exact alpha holdout
    local scan_max = (self_l > 0) and (self_l - 1) or 36
    for i = 1, #cand_list do
        if cand_list[i] > scan_max then scan_max = cand_list[i] end
    end

    local cur_frame = type(obj.frame) == "number" and vl_scene_frame() or nil
    local probe0 = resolve_live_scene_layer_object(1, cur_frame)
    local aup2_hints = (probe0 == nil or probe0.status == "UNAVAILABLE") and scan_aup2_layer_models(cur_frame) or nil

    local targets = {}
    local all_fg_2d = {}
    for oln = 1, scan_max do
        if oln ~= VL_BF.ln and oln ~= self_l then
            local o_exists = false
            pcall(function() if obj.getvalue("layer" .. oln) == true then o_exists = true end end)
            if o_exists and not is_volume_effect_layer(oln) then
                local ox_w, oy_w, oz_w, _, _, _, ozx, ozy, _, ola, Orx, Ory, Orz, Odx, Ody, Odz, Onx, Ony, Onz =
                    read_layer_transform(oln, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                if ola > 0.005 then
                    local linfo = resolve_live_scene_layer_object(oln, cur_frame)
                    local lstat = linfo and linfo.status or "UNAVAILABLE"
                    local lfnm  = linfo and linfo.fx_name or ""
                    local skip_2d = (lstat == "EMPTY" or lstat == "HIDDEN" or lstat == "DISABLED"
                        or lstat == "OBJ" or lstat == "VRM" or lstat == "WATER"
                        or lfnm == "カメラ制御" or lfnm == "音声ファイル"
                        or is_volume_fx_name(lfnm)
                        or has_3d_or_water_signature(oln) ~= nil)
                    if not skip_2d then
                        local is_txt, afx, afy, pl, pr, pt, pb = resolve_text_align_for_layer(oln, cur_frame, linfo, aup2_hints)
                        local zc_center = (ox_w - VL_BF.cam_x) * VL_BF.f_x + (oy_w - VL_BF.cam_y) * VL_BF.f_y + (oz_w - VL_BF.cam_z) * VL_BF.f_z
                        local rec = {
                            layer = oln, alpha = ola, ozx = ozx, ozy = ozy,
                            ox = ox_w, oy = oy_w, oz = oz_w, zc = zc_center,
                            Orx = Orx, Ory = Ory, Orz = Orz,
                            Odx = Odx, Ody = Ody, Odz = Odz,
                            Onx = Onx, Ony = Ony, Onz = Onz,
                            is_text = is_txt, align_fx = afx, align_fy = afy,
                            pad_l = pl, pad_r = pr, pad_t = pt, pad_b = pb,
                            is_target = (want_map[oln] == true),
                        }
                        all_fg_2d[#all_fg_2d + 1] = rec
                        if rec.is_target then targets[#targets + 1] = rec end
                    end
                end
            end
        end
    end
    if #targets == 0 then return end

    -- Free only already-consumed pool/prep/tmp caches (keep mip, occ_a/b, shmap, camocc alive for vl_bf_render!)
    local pre_free = { "cache:vl3d/pool", "cache:vl3d/prep", "cache:vl3d/tmp" }
    for i = 1, #pre_free do pcall(function() obj.clearbuffer(pre_free[i], 1, 1) end) end

    local sw = math.max(16, math.floor(VL_BF.scr_w))
    local sh = math.max(16, math.floor(VL_BF.scr_h))
    local ew = AB.clamp(math.floor(sw * 0.25), 64, 512)
    local eh = AB.clamp(math.floor(sh * 0.25), 36, 288)
    obj.clearbuffer("cache:vl3d/ab_fb", ew, eh)
    pcall(function()
        local ok_direct = obj.pixelshader("ps_vl_ab_blur", "cache:vl3d/ab_fb", { "framebuffer" }, {
            0.5 / ew, 0.0, 0.85, 0.0,  ew, eh, 0.0, 0.0,  0.0, 0.0, 0.0, 0.0
        }, "copy", "clamp")
        if ok_direct == false then
            if obj.copybuffer("tempbuffer", "framebuffer") ~= false then
                obj.pixelshader("ps_vl_ab_blur", "cache:vl3d/ab_fb", { "tempbuffer" }, {
                    0.5 / ew, 0.0, 0.85, 0.0,  ew, eh, 0.0, 0.0,  0.0, 0.0, 0.0, 0.0
                }, "copy", "clamp")
                obj.clearbuffer("tempbuffer", 4, 4)
            end
        end
    end)
    obj.clearbuffer("cache:vl3d/ab_mask", ew, eh)

    -- Load dimensions & build union character silhouette mask at 1/4 res
    local active_targets = {}
    for ti = 1, #targets do
        local T = targets[ti]
        local ow, oh = 0, 0
        local w_raw_t, h_raw_t = 0, 0
        if T.is_text then
            pcall(function()
                obj.load("layer", T.layer, false)
                w_raw_t, h_raw_t = obj.getpixel()
            end)
        end
        pcall(function()
            obj.load("layer", T.layer, true)
            ow, oh = obj.getpixel()
        end)
        if type(ow) == "number" and ow > 0 and type(oh) == "number" and oh > 0 then
            T.ow, T.oh = ow, oh
            if T.is_text then
                local w_raw = (type(w_raw_t) == "number" and w_raw_t > 0) and w_raw_t or ow
                local h_raw = (type(h_raw_t) == "number" and h_raw_t > 0) and h_raw_t or oh
                local cx_a, cy_a = calc_text_center_shift(w_raw, h_raw, T.align_fx, T.align_fy, T.pad_l, T.pad_r, T.pad_t, T.pad_b)
                T.ox = T.ox - T.Orx * (cx_a * T.ozx) - T.Odx * (cy_a * T.ozy)
                T.oy = T.oy - T.Ory * (cx_a * T.ozx) - T.Ody * (cy_a * T.ozy)
                T.oz = T.oz - T.Orz * (cx_a * T.ozx) - T.Odz * (cy_a * T.ozy)
                T.zc = (T.ox - VL_BF.cam_x) * VL_BF.f_x + (T.oy - VL_BF.cam_y) * VL_BF.f_y + (T.oz - VL_BF.cam_z) * VL_BF.f_z
            end
            local inv_2Hx = 1.0 / math.max(2.0, ow * T.ozx)
            local inv_2Hy = 1.0 / math.max(2.0, oh * T.ozy)
            T.inv_2Hx, T.inv_2Hy = inv_2Hx, inv_2Hy
            pcall(function()
                obj.pixelshader("ps_vl_ab_mask_proj", "cache:vl3d/ab_mask", { "object", "object" }, {
                    ew, eh, VL_BF.focal_d, T.alpha,
                    VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, sw,
                    VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, sh,
                    VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, VL_BF.is_edit and 1.0 or 0.0,
                    VL_BF.d_x, VL_BF.d_y, VL_BF.d_z, 0.0,
                    T.ox, T.oy, T.oz, inv_2Hx,
                    T.Orx, T.Ory, T.Orz, inv_2Hy,
                    T.Odx, T.Ody, T.Odz, 0.0,
                    T.Onx, T.Ony, T.Onz, 0.0,
                }, "add", "clamp")
            end)
            active_targets[#active_targets + 1] = T
        end
    end
    if #active_targets == 0 then return end

    -- Sort active targets back-to-front (farther zc first, tie-break ascending layer index)
    table.sort(active_targets, function(a, b)
        if VL_BF.cam_mode > 0 and math.abs(a.zc - b.zc) > 1.0 then
            return a.zc > b.zc
        end
        return a.layer < b.layer
    end)

    -- If カメラ制御 deferred OIT leaves framebuffer empty (alpha ~ 0), project 3D background layers behind targets into ab_fb!
    local ok_ffi_chk, ffi_chk = pcall(require, "ffi")
    if VL_BF.cam_mode > 0 and ok_ffi_chk and ffi_chk ~= nil and type(obj.getpixeldata) == "function" then
        local fb_cov = 1.0
        pcall(function()
            obj.clearbuffer("object", 16, 9)
            obj.pixelshader("ps_vl_ab_blur", "object", { "cache:vl3d/ab_fb" }, {
                0.5 / 16.0, 0.0, 1.0, 0.0,  16, 9, 0.0, 0.0,  0.0, 0.0, 0.0, 0.0
            }, "copy", "clamp")
            local p16, w16, h16 = obj.getpixeldata("object", "rgba")
            if p16 ~= nil and w16 == 16 and h16 == 9 then
                local px16 = ffi_chk.cast("uint8_t*", p16)
                local sa = 0
                for k = 0, 16 * 9 - 1 do sa = sa + px16[k * 4 + 3] end
                fb_cov = sa / (16.0 * 9.0 * 255.0)
            end
        end)
        if fb_cov < 0.05 then
            local bg_cands = {}
            local min_tz = active_targets[#active_targets].zc
            for fi = 1, #all_fg_2d do
                local F = all_fg_2d[fi]
                if (not F.is_target) and F.layer < self_l and F.zc > min_tz + 1.0 then
                    bg_cands[#bg_cands + 1] = F
                end
            end
            if VL_BF.ln > 0 and VL_BF.ln < self_l and VL_BF.exists then
                local szc = (VL_BF.wx - VL_BF.cam_x) * VL_BF.f_x + (VL_BF.wy - VL_BF.cam_y) * VL_BF.f_y + (VL_BF.wz - VL_BF.cam_z) * VL_BF.f_z
                if szc > min_tz + 1.0 then
                    bg_cands[#bg_cands + 1] = {
                        layer = VL_BF.ln, alpha = 1.0, ozx = VL_BF.zx, ozy = VL_BF.zy,
                        ox = VL_BF.wx, oy = VL_BF.wy, oz = VL_BF.wz, zc = szc,
                        Orx = VL_BF.Wrx, Ory = VL_BF.Wry, Orz = VL_BF.Wrz,
                        Odx = VL_BF.Wdx, Ody = VL_BF.Wdy, Odz = VL_BF.Wdz,
                        Onx = VL_BF.Wnx, Ony = VL_BF.Wny, Onz = VL_BF.Wnz,
                        is_text = false,
                    }
                end
            end
            table.sort(bg_cands, function(a, b) return a.zc > b.zc end)
            if #bg_cands > 0 then
                obj.clearbuffer("cache:vl3d/ab_fb", ew, eh)
                local c_mode = 2.0
                for bi = 1, math.min(#bg_cands, 4) do
                    local B = bg_cands[bi]
                    pcall(function()
                        obj.load("layer", B.layer, true)
                        local bow, boh = obj.getpixel()
                        if type(bow) == "number" and bow > 0 and type(boh) == "number" and boh > 0 then
                            obj.clearbuffer("tempbuffer", ew, eh)
                            obj.pixelshader("ps_vl_ab_mask_proj", "tempbuffer", { "object", "cache:vl3d/ab_fb" }, {
                                ew, eh, VL_BF.focal_d, B.alpha,
                                VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, sw,
                                VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, sh,
                                VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, VL_BF.is_edit and 1.0 or 0.0,
                                VL_BF.d_x, VL_BF.d_y, VL_BF.d_z, c_mode,
                                B.ox, B.oy, B.oz, 1.0 / math.max(2.0, bow * B.ozx),
                                B.Orx, B.Ory, B.Orz, 1.0 / math.max(2.0, boh * B.ozy),
                                B.Odx, B.Ody, B.Odz, 0.0,
                                B.Onx, B.Ony, B.Onz, 0.0,
                            }, "copy", "clamp")
                            obj.copybuffer("cache:vl3d/ab_fb", "tempbuffer")
                            c_mode = 1.0
                        end
                    end)
                end
            end
        end
    end

    -- Build normalized character-excluded scene+beam light field in cache:vl3d/ab_env (ew x eh)
    obj.clearbuffer("object", ew, eh)
    obj.pixelshader("ps_vl_ab_env_init", "object", { "cache:vl3d/ab_fb", "cache:vl3d/ab_mask" }, { ew, eh, 1.25, 0.0 }, "copy", "clamp")
    obj.clearbuffer("cache:vl3d/ab_mask", 1, 1)
    obj.clearbuffer("cache:vl3d/ab_fb", 1, 1)

    local blur_r = math.max(2.0, P.ab_env_blur * 0.12)
    local passes = (P.ab_env_blur > 180.0) and 2 or 1
    local fb_r = AB.clamp(VL_BF.lc_r, 0.05, 1.0)
    local fb_g = AB.clamp(VL_BF.lc_g, 0.05, 1.0)
    local fb_b = AB.clamp(VL_BF.lc_b, 0.05, 1.0)

    obj.clearbuffer("tempbuffer", ew, eh)
    for pi = 1, passes do
        local sc_p = (pi == 1) and 1.0 or 2.2
        local step_x = (blur_r * sc_p / 6.0) / ew
        local step_y = (blur_r * sc_p / 6.0) / eh
        obj.pixelshader("ps_vl_ab_blur", "tempbuffer", { "object" }, {
            step_x, 0.0, 3.0, 0.0,  ew, eh, 0.0, 0.0,  fb_r, fb_g, fb_b, 0.0
        }, "copy", "clamp")
        local do_norm = (pi == passes) and 1.0 or 0.0
        obj.pixelshader("ps_vl_ab_blur", "object", { "tempbuffer" }, {
            0.0, step_y, 3.0, do_norm,  ew, eh, 0.0, 0.0,  fb_r, fb_g, fb_b, 0.0
        }, "copy", "clamp")
    end
    obj.copybuffer("cache:vl3d/ab_env", "object")

    -- Downsample cache:vl3d/ab_env to 64x36 once for fast CPU color-confidence & brightness pickup
    local gw, gh = 64, 36
    local grid_ptr = nil
    local ok_ffi, ffi_ab = pcall(require, "ffi")
    if ok_ffi and ffi_ab ~= nil and type(obj.getpixeldata) == "function" then
        pcall(function()
            obj.clearbuffer("object", gw, gh)
            obj.pixelshader("ps_vl_ab_blur", "object", { "cache:vl3d/ab_env" }, {
                0.5 / gw, 0.0, 1.5, 0.0,  gw, gh, 0.0, 0.0,  fb_r, fb_g, fb_b, 0.0
            }, "copy", "clamp")
            local p_raw, rw, rh = obj.getpixeldata("object", "rgba")
            if p_raw ~= nil and rw == gw and rh == gh then
                local copy_buf = ffi_ab.new("uint8_t[?]", gw * gh * 4)
                ffi_ab.copy(copy_buf, p_raw, gw * gh * 4)
                grid_ptr = copy_buf
            end
        end)
    end

    local mip_ptr = nil
    if P.ab_color_mode == 2 and VL_BF.mip_ready and ok_ffi and ffi_ab ~= nil and type(obj.getpixeldata) == "function" then
        pcall(function()
            obj.clearbuffer("object", 32, 18)
            obj.pixelshader("ps_vl_ab_blur", "object", { "cache:vl3d/mip" }, {
                0.5 / 32.0, 0.0, 1.5, 0.0,  32, 18, 0.0, 0.0,  fb_r, fb_g, fb_b, 0.0
            }, "copy", "clamp")
            local pm, rmw, rmh = obj.getpixeldata("object", "rgba")
            if pm ~= nil and rmw == 32 and rmh == 18 then
                local mbuf = ffi_ab.new("uint8_t[?]", 32 * 18 * 4)
                ffi_ab.copy(mbuf, pm, 32 * 18 * 4)
                mip_ptr = mbuf
            end
        end)
    end

    -- Project 3D volumetric light source center to screen UV for directional mode 2
    local lsrc_u, lsrc_v = AB.project_world_to_screen_uv(VL_BF.wx, VL_BF.wy, VL_BF.wz)
    local ab_names = {}

    for ti = 1, #active_targets do
        local T = active_targets[ti]
        -- Check if any NON-target 2D foreground layer sits in front of T
        local closer_nt = {}
        for fi = 1, #all_fg_2d do
            local F = all_fg_2d[fi]
            if not F.is_target and F.layer ~= T.layer then
                local is_in_front = false
                if VL_BF.cam_mode > 0 and math.abs(F.zc - T.zc) > 1.0 then
                    is_in_front = (F.zc < T.zc)
                else
                    is_in_front = (F.layer > T.layer)
                end
                if is_in_front then closer_nt[#closer_nt + 1] = F end
            end
        end

        local has_front_2d = 0.0
        local front_tex = VL_BF.camocc_tex_name
        local fw = AB.clamp(sw, 128, 960)
        local fh = AB.clamp(sh, 72, 540)
        if #closer_nt > 0 then
            obj.clearbuffer("cache:vl3d/ab_front", fw, fh)
            for ci = 1, #closer_nt do
                local C = closer_nt[ci]
                pcall(function()
                    local trw0, trh0 = 0, 0
                    if C.is_text then
                        obj.load("layer", C.layer, false)
                        trw0, trh0 = obj.getpixel()
                    end
                    obj.load("layer", C.layer, true)
                    local cow, coh = obj.getpixel()
                    if type(cow) == "number" and cow > 0 and type(coh) == "number" and coh > 0 then
                        local cx_c, cy_c, cz_c = C.ox, C.oy, C.oz
                        if C.is_text then
                            local rw = (type(trw0) == "number" and trw0 > 0) and trw0 or cow
                            local rh = (type(trh0) == "number" and trh0 > 0) and trh0 or coh
                            local cxa, cya = calc_text_center_shift(rw, rh, C.align_fx, C.align_fy, C.pad_l, C.pad_r, C.pad_t, C.pad_b)
                            cx_c = cx_c - C.Orx * (cxa * C.ozx) - C.Odx * (cya * C.ozy)
                            cy_c = cy_c - C.Ory * (cxa * C.ozx) - C.Ody * (cya * C.ozy)
                            cz_c = cz_c - C.Orz * (cxa * C.ozx) - C.Odz * (cya * C.ozy)
                        end
                        obj.pixelshader("ps_vl_ab_mask_proj", "cache:vl3d/ab_front", { "object", "object" }, {
                            fw, fh, VL_BF.focal_d, C.alpha,
                            VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, sw,
                            VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, sh,
                            VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, VL_BF.is_edit and 1.0 or 0.0,
                            VL_BF.d_x, VL_BF.d_y, VL_BF.d_z, 0.0,
                            cx_c, cy_c, cz_c, 1.0 / math.max(2.0, cow * C.ozx),
                            C.Orx, C.Ory, C.Orz, 1.0 / math.max(2.0, coh * C.ozy),
                            C.Odx, C.Ody, C.Odz, 0.0,
                            C.Onx, C.Ony, C.Onz, 0.0,
                        }, "add", "clamp")
                        has_front_2d = 1.0
                        front_tex = "cache:vl3d/ab_front"
                    end
                end)
            end
        end

        -- Reload target layer T and GPU-downsample directly into bounded cache:vl3d/ab_src (<= 2048x2048 & <= 1.8M dots!)
        local work_sc = math.min(1.0, 2048.0 / T.ow, 2048.0 / T.oh, math.sqrt(OCC_SINGLE_BUDGET / math.max(1.0, T.ow * T.oh)))
        local base_w = math.max(2, math.min(2048, math.floor(T.ow * work_sc + 0.5)))
        local base_h = math.max(2, math.min(2048, math.floor(T.oh * work_sc + 0.5)))
        local ok_ld = false
        pcall(function()
            obj.load("layer", T.layer, true)
            local ow2, oh2 = obj.getpixel()
            if type(ow2) == "number" and ow2 > 0 and type(oh2) == "number" and oh2 > 0 then
                obj.clearbuffer("cache:vl3d/ab_src", base_w, base_h)
                local ds_step = (work_sc < 0.999) and (0.28 / base_w) or 0.0
                local ds_sig  = (work_sc < 0.999) and 0.75 or 0.25
                ok_ld = (obj.pixelshader("ps_vl_ab_blur", "cache:vl3d/ab_src", { "object" }, {
                    ds_step, 0.0, ds_sig, 0.0,  base_w, base_h, 0.0, 0.0,  0.0, 0.0, 0.0, 0.0
                }, "copy", "clamp") ~= false)
                obj.clearbuffer("object", 4, 4)
            end
        end)

        if ok_ld then
            local inside_t = AB.clamp((P.ab_bias + 100.0) / 200.0, 0.0, 1.0)
            local total_rim_px = AB.clamp(1.0 + P.ab_diffusion * 0.30, 1.0, 64.0) * work_sc
            local rim_in_px  = total_rim_px * inside_t
            local rim_out_px = total_rim_px * (1.0 - inside_t)
            local rad_in     = AB.clamp(2.0 + P.ab_diffusion * 0.55, 2.0, 96.0) * work_sc
            local rad_out    = AB.clamp(2.0 + P.ab_diffusion * 0.42, 2.0, 72.0) * work_sc * (1.05 - 0.85 * inside_t)

            local pad_px = (P.ab_style == 2) and math.ceil(rim_out_px + 3.0) or math.ceil(rad_out * 1.35 + 4.0)
            if P.ab_strength <= 0.05 then pad_px = 2 end
            local lw = base_w + pad_px * 2
            local lh = base_h + pad_px * 2

            -- Project T's 4 corners to screen UV to find local sampling box
            local hw_w, hh_w = 0.5 * T.ow * T.ozx, 0.5 * T.oh * T.ozy
            local u0_b, u1_b, v0_b, v1_b = 1.0, 0.0, 1.0, 0.0
            for sx_i = -1, 1, 2 do
                for sy_i = -1, 1, 2 do
                    local cu, cv = AB.project_world_to_screen_uv(
                        T.ox + T.Orx * hw_w * sx_i + T.Odx * hh_w * sy_i,
                        T.oy + T.Ory * hw_w * sx_i + T.Ody * hh_w * sy_i,
                        T.oz + T.Orz * hw_w * sx_i + T.Odz * hh_w * sy_i)
                    if cu ~= nil then
                        if cu < u0_b then u0_b = cu end
                        if cu > u1_b then u1_b = cu end
                        if cv < v0_b then v0_b = cv end
                        if cv > v1_b then v1_b = cv end
                    end
                end
            end
            if u0_b > u1_b then u0_b, u1_b, v0_b, v1_b = 0.25, 0.75, 0.20, 0.80 end
            u0_b = AB.clamp(u0_b - 0.06, 0.0, 0.98)
            u1_b = AB.clamp(u1_b + 0.06, 0.02, 1.0)
            v0_b = AB.clamp(v0_b - 0.06, 0.0, 0.98)
            v1_b = AB.clamp(v1_b + 0.06, 0.02, 1.0)

            local lr, lg, lb, lf = math.floor(fb_r * 255.0 + 0.5), math.floor(fb_g * 255.0 + 0.5), math.floor(fb_b * 255.0 + 0.5), 0.85
            local rr, rg, rb, rf = lr, lg, lb, lf
            if grid_ptr ~= nil then
                if P.ab_color_mode == 0 then
                    lr, lg, lb, lf = AB.sample_grid_region(grid_ptr, gw, gh, u0_b, u1_b, v0_b, v1_b, 0)
                    rr, rg, rb, rf = lr, lg, lb, lf
                else
                    lr, lg, lb, lf = AB.sample_grid_region(grid_ptr, gw, gh, u0_b, u1_b, v0_b, v1_b, -1)
                    rr, rg, rb, rf = AB.sample_grid_region(grid_ptr, gw, gh, u0_b, u1_b, v0_b, v1_b, 1)
                end
            end

            -- Mode 2 (体積光＋背景2色連動): infuse volumetric beam emission color & compute 3D light direction in sprite local XY
            local dir_x, dir_y, dir_w = 0.0, -1.0, 0.0
            if P.ab_color_mode == 2 then
                local bl_r, bl_g, bl_b = math.floor(fb_r * 255.0 + 0.5), math.floor(fb_g * 255.0 + 0.5), math.floor(fb_b * 255.0 + 0.5)
                local br_r, br_g, br_b = bl_r, bl_g, bl_b
                if mip_ptr ~= nil and not VL_BF.use_cam_virtual_src then
                    -- Map sprite screen/world footprint onto mip level-0 tile [0..0.666, 0..1.0]
                    local mu0, mu1 = u0_b, u1_b
                    local mv0, mv1 = v0_b, v1_b
                    if VL_BF.exists and (VL_BF.Hx or 0) > 1.0 and (VL_BF.Hy or 0) > 1.0 then
                        local su0, su1, sv0, sv1 = 1.0, 0.0, 1.0, 0.0
                        local den_sf = VL_BF.sf_x * VL_BF.Wnx + VL_BF.sf_y * VL_BF.Wny + VL_BF.sf_z * VL_BF.Wnz
                        for sx_i = -1, 1, 2 do
                            for sy_i = -1, 1, 2 do
                                local px_w = T.ox + T.Orx * hw_w * sx_i + T.Odx * hh_w * sy_i
                                local py_w = T.oy + T.Ory * hw_w * sx_i + T.Ody * hh_w * sy_i
                                local pz_w = T.oz + T.Orz * hw_w * sx_i + T.Odz * hh_w * sy_i
                                if math.abs(den_sf) > 1e-3 then
                                    local t_hit = ((VL_BF.wx - px_w) * VL_BF.Wnx + (VL_BF.wy - py_w) * VL_BF.Wny + (VL_BF.wz - pz_w) * VL_BF.Wnz) / den_sf
                                    px_w = px_w + VL_BF.sf_x * t_hit
                                    py_w = py_w + VL_BF.sf_y * t_hit
                                    pz_w = pz_w + VL_BF.sf_z * t_hit
                                end
                                local rx_s, ry_s, rz_s = px_w - VL_BF.wx, py_w - VL_BF.wy, pz_w - VL_BF.wz
                                local uu = 0.5 + (rx_s * VL_BF.Wrx + ry_s * VL_BF.Wry + rz_s * VL_BF.Wrz) / (2.0 * VL_BF.Hx)
                                local vv = 0.5 + (rx_s * VL_BF.Wdx + ry_s * VL_BF.Wdy + rz_s * VL_BF.Wdz) / (2.0 * VL_BF.Hy)
                                if uu < su0 then su0 = uu end
                                if uu > su1 then su1 = uu end
                                if vv < sv0 then sv0 = vv end
                                if vv > sv1 then sv1 = vv end
                            end
                        end
                        if su0 <= su1 then
                            mu0 = AB.clamp(su0 - 0.08, 0.0, 0.96)
                            mu1 = AB.clamp(su1 + 0.08, 0.04, 1.0)
                            mv0 = AB.clamp(sv0 - 0.08, 0.0, 0.96)
                            mv1 = AB.clamp(sv1 + 0.08, 0.04, 1.0)
                        end
                    end
                    bl_r, bl_g, bl_b = AB.sample_grid_region(mip_ptr, 32, 18, mu0, mu1, mv0, mv1, -1)
                    br_r, br_g, br_b = AB.sample_grid_region(mip_ptr, 32, 18, mu0, mu1, mv0, mv1,  1)
                else
                    bl_r, bl_g, bl_b = AB.tune_backlight_color(bl_r, bl_g, bl_b, P.ab_color_mix, 0.85, P.ab_saturation)
                    br_r, br_g, br_b = bl_r, br_g, br_b
                end
                local beam_infuse = AB.clamp(P.intensity / 200.0, 0.12, 0.50)
                lr = math.floor(lr * (1.0 - beam_infuse) + bl_r * beam_infuse + 0.5)
                lg = math.floor(lg * (1.0 - beam_infuse) + bl_g * beam_infuse + 0.5)
                lb = math.floor(lb * (1.0 - beam_infuse) + bl_b * beam_infuse + 0.5)
                rr = math.floor(rr * (1.0 - beam_infuse) + br_r * beam_infuse + 0.5)
                rg = math.floor(rg * (1.0 - beam_infuse) + br_g * beam_infuse + 0.5)
                rb = math.floor(rb * (1.0 - beam_infuse) + br_b * beam_infuse + 0.5)
                local beam_lf = AB.clamp(P.intensity / 210.0, 0.0, 0.88)
                lf = AB.clamp(lf * 0.55 + beam_lf * 0.65, 0.0, 1.0)
                rf = AB.clamp(rf * 0.55 + beam_lf * 0.65, 0.0, 1.0)

                -- Vector from sprite world center T.(ox,oy,oz) toward 3D light source VL_BF.(wx,wy,wz) projected onto sprite (Or, Od)
                local vx_l, vy_l, vz_l = VL_BF.wx - T.ox, VL_BF.wy - T.oy, VL_BF.wz - T.oz
                local lx_p = vx_l * T.Orx + vy_l * T.Ory + vz_l * T.Orz
                local ly_p = vx_l * T.Odx + vy_l * T.Ody + vz_l * T.Odz
                local ll = math.sqrt(lx_p * lx_p + ly_p * ly_p)
                if ll > 1.0 then
                    dir_x, dir_y, dir_w = lx_p / ll, ly_p / ll, 0.75
                elseif lsrc_u ~= nil then
                    local dx_s = lsrc_u - 0.5 * (u0_b + u1_b)
                    local dy_s = lsrc_v - 0.5 * (v0_b + v1_b)
                    local dl = math.sqrt(dx_s * dx_s + dy_s * dy_s)
                    if dl > 1e-3 then dir_x, dir_y, dir_w = dx_s / dl, dy_s / dl, 0.65 end
                end
            end

            local eff_lf = P.ab_luma_link and lf or 1.0
            local eff_rf = P.ab_luma_link and rf or 1.0
            local left_str  = P.ab_strength * eff_lf
            local right_str = P.ab_strength * eff_rf

            -- Separable silhouette field: cache:vl3d/ab_src -> object (H) -> tempbuffer (V)
            obj.clearbuffer("object", lw, lh)
            obj.pixelshader("ps_vl_ab_sil_h", "object", { "cache:vl3d/ab_src" }, {
                lw, lh, base_w, base_h,  pad_px, pad_px, rad_in, rad_out
            }, "copy", "clamp")
            obj.clearbuffer("tempbuffer", lw, lh)
            obj.pixelshader("ps_vl_ab_sil_v", "tempbuffer", { "object" }, {
                lw, lh, rad_in, rad_out
            }, "copy", "clamp")

            local step_rx = T.Orx * (T.ozx / work_sc)
            local step_ry = T.Ory * (T.ozx / work_sc)
            local step_rz = T.Orz * (T.ozx / work_sc)
            local step_dx = T.Odx * (T.ozy / work_sc)
            local step_dy = T.Ody * (T.ozy / work_sc)
            local step_dz = T.Odz * (T.ozy / work_sc)

            obj.clearbuffer("object", lw, lh)
            obj.pixelshader("ps_vl_ab_shade", "object",
                { "cache:vl3d/ab_src", "tempbuffer", "cache:vl3d/ab_env", front_tex },
                {
                    lw, lh, base_w, base_h,
                    pad_px, pad_px, P.ab_style, (P.ab_color_mode >= 1) and 1.0 or 0.0,
                    lr / 255.0, lg / 255.0, lb / 255.0, left_str,
                    rr / 255.0, rg / 255.0, rb / 255.0, right_str,
                    P.ab_bright, inside_t, P.ab_env_blend * 0.01, has_front_2d,
                    rim_out_px, rim_in_px, dir_x, dir_y,
                    T.ox, T.oy, T.oz, dir_w,
                    step_rx, step_ry, step_rz, sw,
                    step_dx, step_dy, step_dz, sh,
                    VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d,
                    VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.is_edit and 1.0 or 0.0,
                    VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, (has_front_2d < 0.5) and VL_BF.camocc_active or 0.0,
                    VL_BF.d_x, VL_BF.d_y, VL_BF.d_z, VL_BF.camocc_max_z,
                }, "copy", "clamp")

            -- Submit 3D quad nudged 0.05px toward camera with exact perspective-invariant scale
            local z_nudge = (VL_BF.cam_mode > 0 and not VL_BF.is_edit and T.zc > 4.0) and 0.05 or 0.0
            local p_scale = (z_nudge > 0.0) and ((T.zc - z_nudge) / T.zc) or 1.0
            local c_wx = VL_BF.cam_x + (T.ox - VL_BF.cam_x) * p_scale
            local c_wy = VL_BF.cam_y + (T.oy - VL_BF.cam_y) * p_scale
            local c_wz = VL_BF.cam_z + (T.oz - VL_BF.cam_z) * p_scale
            local q_hw = 0.5 * lw * (T.ozx / work_sc) * p_scale
            local q_hh = 0.5 * lh * (T.ozy / work_sc) * p_scale
            local rx_q, ry_q, rz_q = T.Orx * q_hw, T.Ory * q_hw, T.Orz * q_hw
            local dx_q, dy_q, dz_q = T.Odx * q_hh, T.Ody * q_hh, T.Odz * q_hh

            local x0, y0, z0 = AB.world_to_self_drawpoly(c_wx - rx_q - dx_q, c_wy - ry_q - dy_q, c_wz - rz_q - dz_q)
            local x1, y1, z1 = AB.world_to_self_drawpoly(c_wx + rx_q - dx_q, c_wy + ry_q - dy_q, c_wz + rz_q - dz_q)
            local x2, y2, z2 = AB.world_to_self_drawpoly(c_wx + rx_q + dx_q, c_wy + ry_q + dy_q, c_wz + rz_q + dz_q)
            local x3, y3, z3 = AB.world_to_self_drawpoly(c_wx - rx_q + dx_q, c_wy - ry_q + dy_q, c_wz - rz_q + dz_q)

            obj.setoption("drawtarget", "framebuffer")
            obj.setoption("culling", 0)
            obj.setoption("sampler", "clamp")
            pcall(function() obj.setoption("billboard", 0) end)
            pcall(function() obj.setoption("blend", "none") end)
            obj.drawpoly({
                { x0, y0, z0, 0.0, 0.0 },
                { x1, y1, z1, 1.0, 0.0 },
                { x2, y2, z2, 1.0, 1.0 },
                { x3, y3, z3, 0.0, 1.0 },
            }, 4, T.alpha)
            VL_BF.total_v = VL_BF.total_v + 4
            ab_names[#ab_names + 1] = "L" .. tostring(T.layer)
        end
    end

    local post_ab = { "cache:vl3d/ab_fb", "cache:vl3d/ab_mask", "cache:vl3d/ab_env", "cache:vl3d/ab_src", "cache:vl3d/ab_front", "tempbuffer" }
    for i = 1, #post_ab do pcall(function() obj.clearbuffer(post_ab[i], 1, 1) end) end
    if #ab_names > 0 then
        VL_BF.occ_desc = VL_BF.occ_desc .. "+AB(" .. table.concat(ab_names, ",") .. ")"
        local ab_dots = ew * eh * 3 + 1024 * 1024
        local tot_d = (VL_BF.peak_cache_dots or 0) + ab_dots
        if tot_d > VL_BF.peak_cache_dots then VL_BF.peak_cache_dots = tot_d end
    end
end

