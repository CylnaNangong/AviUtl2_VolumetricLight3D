--------------------------------------------------------------------
-- Shared 3D Light Field Publisher (v3.16, Protocol "VL3DLF\3\0")
-- Publishes compact GPU-packed 3D radiance + cast-shadow + foreground
-- depth fields into AviUtl2's inter-script `global` table so companion
-- 3D particle/object scripts (e.g. Snow3D v1.23+) can illuminate
-- particles inside beams and extinguish them inside 3D/2D shadows.
--
-- Zero extra cache-dot overhead: renders into `"object"` (max 48x114)
-- immediately before `beamatlas` overwrites `"object"` with its 1x1 stub.
--------------------------------------------------------------------

local VL_LF_MAGIC = "VL3DLF\3\0"
local lf_ffi_ok, lf_ffi = pcall(require, "ffi")

local function vl_lf_scene_id()
    if type(obj.scene_id) == "number" then
        return math.floor(obj.scene_id + 0.5)
    end
    if type(obj.scene) == "number" then
        return math.floor(obj.scene + 0.5)
    end
    if _VL_PERSIST and _VL_PERSIST.aup2_cache and type(_VL_PERSIST.aup2_cache.active_scene_id) == "number" then
        return math.floor(_VL_PERSIST.aup2_cache.active_scene_id + 0.5)
    end
    return 0
end

local function vl_lf_parse_idx(raw)
    local out = {}
    if type(raw) ~= "string" or raw == "" then return out end
    for tok in string.gmatch(raw, "%-?%d+") do
        local n = tonumber(tok)
        if n ~= nil then out[#out + 1] = math.floor(n) end
    end
    return out
end

local function vl_lf_format_idx(list)
    if #list == 0 then return "" end
    local s = {}
    for i = 1, #list do s[i] = tostring(list[i]) end
    return table.concat(s, ",")
end

local function vl_lf_withdraw()
    if global == nil then return end
    local scn = vl_lf_scene_id()
    local lay = (type(obj.layer) == "number") and math.floor(obj.layer + 0.5) or -1
    if lay < 0 then return end
    local slot_key = string.format("VL3D_LF:%d:%d", scn, lay)
    local idx_key  = string.format("VL3D_LF_IDX:%d", scn)
    pcall(function()
        global[slot_key] = ""
        pcall(function() global[slot_key] = nil end)
        local cur = vl_lf_parse_idx(global[idx_key])
        local nxt = {}
        for i = 1, #cur do
            if cur[i] ~= lay then nxt[#nxt + 1] = cur[i] end
        end
        global[idx_key] = vl_lf_format_idx(nxt)
    end)
end

local function vl_lf_publish(
    spot_ray_env_mode,
    cb_tgt_r, cb_tgt_g, cb_tgt_b,
    cb_cl_px, cb_cl_py, cb_cl_pz,
    cl_rdx, cl_rdy, cl_rdz,
    cb_in_rad, cb_out_rad, cb_att_k,
    has_cam_3d_light, cam_light_is_pos, h_core_peak
)
    if global == nil or not lf_ffi_ok or lf_ffi == nil then return end
    if not VL_BF.ps_ok then
        vl_lf_withdraw()
        return
    end
    local scn = vl_lf_scene_id()
    local lay = (type(obj.layer) == "number") and math.floor(obj.layer + 0.5) or -1
    if lay < 0 then return end

    local out_w = 48
    local rad_h = 48
    local n_occ_2d = math.min(MAX_OCC, math.max(0, math.floor((VL_BF.n_occ_active or 0) + 0.5)))
    local sh3d_on  = (VL_BF.sh3d_active or 0) > 0.5
    local cam3d_on = (VL_BF.camocc_active or 0) > 0.5
    local occ_str  = VL_BF.occ_str_val or math.max(0.0, math.min(1.0, (P.occ_strength or 100.0) / 100.0))
    local eff_m    = math.floor((VL_BF.eff_occ_mode or 0) + 0.5)
    local do_sh    = (eff_m == 1 or eff_m == 3) and (occ_str > 0.001)
    local do_fg    = (eff_m == 2 or eff_m == 3) and (occ_str > 0.001)

    local has_sh  = do_sh and (sh3d_on or n_occ_2d > 0)
    local has_cam = do_fg and (cam3d_on or n_occ_2d > 0)
    local sh_h    = has_sh and 48 or 0
    local cam_h   = has_cam and 18 or 0
    local row_sh  = rad_h
    local row_cam = rad_h + sh_h
    local out_h   = rad_h + sh_h + cam_h

    local u0_b = VL_BF.act_u0 or 0.0
    local u1_b = VL_BF.act_u1 or 1.0
    local v0_b = VL_BF.act_v0 or 0.0
    local v1_b = VL_BF.act_v1 or 1.0
    if u1_b <= u0_b + 1e-4 then u0_b, u1_b = 0.0, 1.0 end
    if v1_b <= v0_b + 1e-4 then v0_b, v1_b = 0.0, 1.0 end

    -- Matches beamatlas.hlsl line 784: uv_sh = (uv + 0.04) / 1.08 -> uv in [-0.04, 1.04]
    local su0, su1 = -0.04, 1.04
    local sv0, sv1 = -0.04, 1.04

    local is_spot = (spot_ray_env_mode or 0.0) > 0.5
    -- Note: `lc_rgb` is already baked into `cache:vl3d/mip` for 2D sources (`16_geometry_mip.lua`)
    -- and into `cb_tgt_rgb` for virtual 3D camera sources (`19_render.lua`); do not multiply again.
    local lt_r = cb_tgt_r or 1.0
    local lt_g = cb_tgt_g or 1.0
    local lt_b = cb_tgt_b or 1.0
    local pk_t = math.max(1.0, lt_r, lt_g, lt_b)
    local tint_r, tint_g, tint_b = lt_r / pk_t, lt_g / pk_t, lt_b / pk_t
    local host_pk = math.max(1.0, h_core_peak or 1.0)
    local gain_s = math.max(0.0, (P.intensity / 100.0) * (VL_BF.base_alpha or 1.0) * (VL_BF.cam_comp or 1.0) * (pk_t / host_pk))

    local cam_zmax = 8192.0
    if cam3d_on and type(VL_BF.camocc_max_z) == "number" and VL_BF.camocc_max_z > 1.0 then
        cam_zmax = VL_BF.camocc_max_z
    end
    local focal_s = math.max(1.0, VL_BF.focal_d or 1024.0)
    local inv_fsw = math.max(1.0, VL_BF.scr_w or 1920.0) / focal_s
    local inv_fsh = math.max(1.0, VL_BF.scr_h or 1080.0) / focal_s

    local lf_consts = {
        out_w, rad_h, sh_h, cam_h,
        row_sh, row_cam, VL_BF.mode_kind or 0.0, is_spot and 1.0 or 0.0,
        VL_BF.wx or 0.0, VL_BF.wy or 0.0, VL_BF.wz or 0.0, VL_BF.Hx or 1.0,
        VL_BF.Wrx or 1.0, VL_BF.Wry or 0.0, VL_BF.Wrz or 0.0, VL_BF.Hy or 1.0,
        VL_BF.Wdx or 0.0, VL_BF.Wdy or 1.0, VL_BF.Wdz or 0.0, VL_BF.L_tot or 1.0,
        VL_BF.Wnx or 0.0, VL_BF.Wny or 0.0, VL_BF.Wnz or -1.0, 1.0,
        VL_BF.sx_p or 0.0, VL_BF.sy_p or 0.0, VL_BF.sz_p or -1.0, sh3d_on and 1.0 or 0.0,
        u0_b, u1_b, v0_b, v1_b,
        su0, su1, sv0, sv1,
        tint_r, tint_g, tint_b, n_occ_2d,
        occ_str, cam_zmax, VL_BF.sh_res_val or 4.0, cam3d_on and 1.0 or 0.0,
        VL_BF.uv_span_x or 1.0, VL_BF.uv_span_y or 1.0, VL_BF.camocc_w or 4.0, VL_BF.camocc_h or 4.0,
        VL_BF.cam_x or 0.0, VL_BF.cam_y or 0.0, VL_BF.cam_z or -1024.0, VL_BF.is_edit and 1.0 or 0.0,
        VL_BF.f_x or 0.0, VL_BF.f_y or 0.0, VL_BF.f_z or 1.0, do_fg and 1.0 or 0.0,
        VL_BF.r_x or 1.0, VL_BF.r_y or 0.0, VL_BF.r_z or 0.0, inv_fsw,
        VL_BF.d_x or 0.0, VL_BF.d_y or 1.0, VL_BF.d_z or 0.0, inv_fsh,
    }
    local base_c = #lf_consts
    for oi = 1, MAX_OCC * 16 do
        lf_consts[base_c + oi] = (VL_BF.occ_flat and VL_BF.occ_flat[oi]) or 0.0
    end

    local pack_ok = false
    local pix_ptr = nil
    local pw, ph = 0, 0
    pcall(function()
        obj.clearbuffer("object", out_w, out_h)
        local r = obj.pixelshader(
            "ps_vl_lfpack", "object",
            { "cache:vl3d/mip", VL_BF.occ_tex_name, VL_BF.shmap_tex_name, VL_BF.camocc_tex_name },
            lf_consts, "copy", "clamp"
        )
        if r ~= false then
            local d, w, h = obj.getpixeldata("object", "rgba")
            if d ~= nil and w == out_w and h == out_h then
                pix_ptr, pw, ph = d, w, h
                pack_ok = true
            end
        end
    end)

    if not pack_ok or pix_ptr == nil then
        vl_lf_withdraw()
        return
    end

    local b_min_x, b_min_y, b_min_z =  1e9,  1e9,  1e9
    local b_max_x, b_max_y, b_max_z = -1e9, -1e9, -1e9
    if VL_BF.pts and (VL_BF.pi or 0) > 0 then
        for i = 1, VL_BF.pi do
            local pt = VL_BF.pts[i]
            if pt[1] < b_min_x then b_min_x = pt[1] end
            if pt[2] < b_min_y then b_min_y = pt[2] end
            if pt[3] < b_min_z then b_min_z = pt[3] end
            if pt[1] > b_max_x then b_max_x = pt[1] end
            if pt[2] > b_max_y then b_max_y = pt[2] end
            if pt[3] > b_max_z then b_max_z = pt[3] end
        end
        local pad_bb = 24.0
        b_min_x, b_min_y, b_min_z = b_min_x - pad_bb, b_min_y - pad_bb, b_min_z - pad_bb
        b_max_x, b_max_y, b_max_z = b_max_x + pad_bb, b_max_y + pad_bb, b_max_z + pad_bb
    else
        b_min_x, b_min_y, b_min_z = -1e6, -1e6, -1e6
        b_max_x, b_max_y, b_max_z =  1e6,  1e6,  1e6
    end

    local flags = 1 -- bit0: rad band present
    if has_sh  then flags = flags + 2 end
    if has_cam then flags = flags + 4 end
    if is_spot then flags = flags + 8 end
    if (VL_BF.fog_amt or 0.0) > 0.005 then flags = flags + 16 end
    if (VL_BF.occ3d_int or 0) == 1 then flags = flags + 32 end
    if VL_BF.is_edit then flags = flags + 64 end
    if has_cam_3d_light then flags = flags + 128 end
    if cam_light_is_pos then flags = flags + 256 end

    local z_win_cam = 1e9
    if not VL_BF.is_edit then
        z_win_cam = (VL_BF.wx - VL_BF.cam_x) * VL_BF.f_x
                  + (VL_BF.wy - VL_BF.cam_y) * VL_BF.f_y
                  + (VL_BF.wz - VL_BF.cam_z) * VL_BF.f_z
    end

    local cos_in_sp  = math.cos(math.max(0.0, math.min(math.pi * 0.99, cb_in_rad or 0.0)))
    local cos_out_sp = math.cos(math.max(0.0, math.min(math.pi * 0.99, cb_out_rad or 0.0)))
    local sh_soft_t  = math.max(8.0, (P.occ_soft or 8.0) * 1.8)

    local hdr_bytes = 264
    local pix_bytes = out_w * out_h * 4
    local total_bytes = hdr_bytes + pix_bytes
    local buf = lf_ffi.new("uint8_t[?]", total_bytes)
    lf_ffi.copy(buf, VL_LF_MAGIC, 8)

    local i32 = lf_ffi.cast("int32_t*", buf + 8)
    local cur_sf = (type(obj.frame) == "number") and vl_scene_frame() or -1
    local rnd_id = (_VL_PERSIST and _VL_PERSIST.rendering_id) or 0
    i32[0] = scn
    i32[1] = lay
    i32[2] = math.floor(cur_sf + 0.5)
    i32[3] = math.floor(rnd_id)
    i32[4] = math.floor((VL_BF.mode_kind or 0.0) + 0.5)
    i32[5] = flags
    i32[6] = out_w
    i32[7] = has_sh and out_w or 0

    local f32 = lf_ffi.cast("float*", buf + 40)
    f32[0],  f32[1],  f32[2]  = VL_BF.wx or 0.0,  VL_BF.wy or 0.0,  VL_BF.wz or 0.0
    f32[3],  f32[4],  f32[5]  = VL_BF.Wrx or 1.0, VL_BF.Wry or 0.0, VL_BF.Wrz or 0.0
    f32[6],  f32[7],  f32[8]  = VL_BF.Wdx or 0.0, VL_BF.Wdy or 1.0, VL_BF.Wdz or 0.0
    f32[9],  f32[10], f32[11] = VL_BF.Wnx or 0.0, VL_BF.Wny or 0.0, VL_BF.Wnz or -1.0
    f32[12], f32[13], f32[14] = VL_BF.sx_p or 0.0, VL_BF.sy_p or 0.0, VL_BF.sz_p or -1.0
    f32[15] = 1.0 / math.max(1e-4, 2.0 * (VL_BF.Hx or 1.0))
    f32[16] = 1.0 / math.max(1e-4, 2.0 * (VL_BF.Hy or 1.0))
    f32[17] = VL_BF.dS_wn or 1.0
    f32[18] = VL_BF.u_wn or -1.0
    f32[19] = VL_BF.L_tot or 1.0
    f32[20] = VL_BF.k_dec or 0.0
    f32[21] = VL_BF.inv_end or 1.0
    f32[22], f32[23], f32[24], f32[25] = u0_b, u1_b, v0_b, v1_b
    f32[26], f32[27], f32[28], f32[29] = su0, su1, sv0, sv1
    f32[30], f32[31], f32[32] = gain_s, gain_s, gain_s
    f32[33], f32[34], f32[35] = b_min_x, b_min_y, b_min_z
    f32[36], f32[37], f32[38] = b_max_x, b_max_y, b_max_z
    f32[39], f32[40], f32[41] = cb_cl_px or 0.0, cb_cl_py or 0.0, cb_cl_pz or 0.0
    f32[42], f32[43], f32[44] = cl_rdx or VL_BF.cam_ray_dx or 0.0, cl_rdy or VL_BF.cam_ray_dy or 0.0, cl_rdz or VL_BF.cam_ray_dz or 1.0
    f32[45] = cos_in_sp
    f32[46] = cos_out_sp
    f32[47] = cb_att_k or 0.0
    f32[48] = cam_zmax
    f32[49] = z_win_cam
    f32[50] = sh_soft_t
    f32[51] = VL_BF.fog_amt or 0.0
    f32[52] = 1.0 / math.max(10.0, VL_BF.f_scale or 220.0)
    f32[53], f32[54], f32[55] = VL_BF.wox or 0.0, VL_BF.woy or 0.0, VL_BF.woz or 0.0

    -- Direct RGBA8 copy (obj.getpixeldata("object", "rgba") returns RGBA byte order)
    lf_ffi.copy(buf + hdr_bytes, pix_ptr, pix_bytes)

    -- Encode as pure 7-bit ASCII Base64 ("VL3DB64:...") so AviUtl2's C++ `global`
    -- string bridge (`lua_tostring` / `lua_pushstring` / NUL-terminated c_str) can
    -- never truncate embedded zero bytes on any host build.
    local payload = "VL3DB64:" .. vl_encode_b64_ffi(lf_ffi, buf, total_bytes)
    local slot_key = string.format("VL3D_LF:%d:%d", scn, lay)
    local idx_key  = string.format("VL3D_LF_IDX:%d", scn)
    pcall(function()
        global[slot_key] = payload
        local cur = vl_lf_parse_idx(global[idx_key])
        local seen = false
        for i = 1, #cur do
            if cur[i] == lay then seen = true; break end
        end
        if not seen then
            cur[#cur + 1] = lay
            table.sort(cur)
            global[idx_key] = vl_lf_format_idx(cur)
        end
    end)
end
