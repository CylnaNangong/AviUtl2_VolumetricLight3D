---------------------------------------------------------------------
-- 多言語 UI 自動検出（aviutl2.ini の Language 設定を参照・0.5 秒キャッシュ）
---------------------------------------------------------------------
local obj, math = obj, math

local _VL_PERSIST = _G._VL3D_PERSIST_V280
if type(_VL_PERSIST) ~= "table" then
    _VL_PERSIST = {
        ctypes         = {},
        w32_api        = nil,
        w32_mb2wc_fn   = nil,
        cached_lang    = nil,
        cached_lang_t  = -999.0,
        obj_mesh_cache = {},
        glb_mesh_cache = {},
        vrm_pose_cache = {},
        search_dirs    = {},
        aul2_scanned   = false,
        aul2_fx_lists  = nil,
        aup2_cache     = { t = -999.0, raw_entries = nil, active_scene_id = nil, log_scene_id = nil },
        modelfile_cache= { t = -999.0, wstr_addr = nil, list = {} },
        live_sg_state  = { inited = false, cands = nil, hproc = 0, rpm = nil, u64b = nil, i32b = nil, u8b = nil, wbuf = nil, nrd = nil, lbuf = nil },
        edit_cam_probe = { init = false, ok = false, g_addr = 0, off_w = 0, off_c = 0 },
        edit_cam_mbi   = nil,
        rast_ws        = {
            zbuf_sh = nil, zcap_sh = 0,
            rgba_sh = nil, rcap_sh = 0,
            row_sh  = nil, hcap_sh = 0,
            sh_sig  = nil, sh_w    = 0, sh_tris = 0,
            zbuf_cm = nil, zcap_cm = 0,
            rgba_cm = nil, rcap_cm = 0,
            row_cm  = nil, hcap_cm = 0,
            sc_cap  = 0,
            wvx = nil, wvy = nil, wvz = nil, wd1 = nil, wd2 = nil,
            px_a = nil, py_a = nil, pp_a = nil, pt_a = nil,
        },
    }
    _G._VL3D_PERSIST_V280 = _VL_PERSIST
end

