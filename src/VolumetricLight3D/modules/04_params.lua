---------------------------------------------------------------------
-- 本体（TemporaryImageCacheSize = 16,000,000 ドット厳守・即時解放設計）
---------------------------------------------------------------------
local MAX_OCC = 8
local OCC_SINGLE_BUDGET = 1800000 -- 1 枚あたり 180 万ドット上限

local CACHE_KEYS = {
    "cache:vl3d/pool",
    "cache:vl3d/prep",
    "cache:vl3d/tmp",
    "cache:vl3d/mip",
    "cache:vl3d/lfgrid",
    "cache:vl3d/occ_a",
    "cache:vl3d/occ_b",
    "cache:vl3d/shmap",
    "cache:vl3d/camocc",
    "cache:vl3d/fb_bak",
    "cache:vl3d/ab_fb",
    "cache:vl3d/ab_mask",
    "cache:vl3d/ab_env",
    "cache:vl3d/ab_src",
    "cache:vl3d/ab_front",
}

local function release_work_caches()
    for i = 1, #CACHE_KEYS do
        pcall(function() obj.clearbuffer(CACHE_KEYS[i], 1, 1) end)
    end
end

local P = {}
P.src_layer   = math.floor((src_layer or 0) + 0.5)
P.ray_mode    = ray_mode or 0
P.fill_mode   = fill_mode or 0
P.direction   = direction or 55.0
P.pitch       = pitch or 44.0
P.screen_dir  = screen_dir
if P.screen_dir == nil then P.screen_dir = true end
P.spread      = spread
if P.spread == nil then P.spread = 22.0 end
P.light_x     = light_x or 0.0
P.light_y     = light_y
if P.light_y == nil then P.light_y = -300.0 end
P.light_z     = light_z or 1400.0
P.length      = length
if P.length == nil then P.length = 2500.0 end
P.decay       = decay
if P.decay == nil then P.decay = 55.0 end
P.light_color = light_color
P.intensity   = intensity
if P.intensity == nil then P.intensity = 100.0 end
P.threshold      = threshold
if P.threshold == nil then P.threshold = 15.0 end
P.cam_light_mode = math.floor((cam_light_mode or 0) + 0.5)
P.occ_mode     = math.floor((occ_mode or 0) + 0.5)
P.occ_3d_mode  = math.floor((occ_3d_mode or 0) + 0.5)
P.occ_target   = math.floor((occ_target or 0) + 0.5)
P.occ_list     = (occ_list ~= nil) and tostring(occ_list) or ""
P.occ_soft     = occ_soft
if P.occ_soft == nil then P.occ_soft = 8.0 end
P.occ_strength = occ_strength
if P.occ_strength == nil then P.occ_strength = 100.0 end
P.ab_enable     = (ab_enable == true or ab_enable == 1)
P.ab_target     = math.floor((ab_target or 0) + 0.5)
P.ab_list       = (ab_list ~= nil) and tostring(ab_list) or ""
P.ab_bright     = (ab_bright ~= nil) and ab_bright or 85.0
P.ab_color_mode = math.floor((ab_color_mode ~= nil and ab_color_mode or 2) + 0.5)
P.ab_style      = math.floor((ab_style or 0) + 0.5)
P.ab_strength   = (ab_strength ~= nil) and ab_strength or 100.0
P.ab_diffusion  = (ab_diffusion ~= nil) and ab_diffusion or 35.0
P.ab_bias       = (ab_bias ~= nil) and ab_bias or -60.0
P.ab_color_mix  = (ab_color_mix ~= nil) and ab_color_mix or 100.0
P.ab_saturation = (ab_saturation ~= nil) and ab_saturation or 100.0
P.ab_luma_link  = (ab_luma_link == nil) and true or (ab_luma_link == true or ab_luma_link == 1)
P.ab_env_blend  = (ab_env_blend ~= nil) and ab_env_blend or 30.0
P.ab_env_blur   = (ab_env_blur ~= nil) and ab_env_blur or 160.0
P.fog         = fog
if P.fog == nil then P.fog = 0.0 end
P.fog_scale   = fog_scale or 220.0
P.wind_dir    = wind_dir or 25.0
P.wind_speed  = wind_speed
if P.wind_speed == nil then P.wind_speed = 45.0 end
P.seed        = seed or 0
P.res_mode    = res_mode
if P.res_mode == nil then P.res_mode = 1 end
P.auto_slice  = auto_slice
if P.auto_slice == nil then P.auto_slice = true end
P.slices      = slices or 64
P.view_mode   = view_mode or 0
P.show_info   = (show_info == true or show_info == 1)
P.log_mode    = log_mode
if P.log_mode == nil then P.log_mode = true end

