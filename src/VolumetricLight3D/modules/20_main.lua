local function beam_frame()
    vl_bf_reset()
    vl_bf_prepare()
    if VL_BF._abort then vl_lf_withdraw(); return end
    vl_bf_capture()
    if VL_BF._abort then vl_lf_withdraw(); return end
    vl_bf_geometry()
    if VL_BF._abort then vl_lf_withdraw(); return end
    vl_bf_mip()
    if VL_BF._abort then vl_lf_withdraw(); return end
    vl_bf_occlusion()
    if VL_BF._abort then vl_lf_withdraw(); return end
    vl_bf_render()
    if VL_BF._abort then vl_lf_withdraw(); return end
end


local function vl_run_frame()
    -- Per-layer re-entrancy protection:
    -- 1. Same-layer cycle or occlusion-stage re-entry aborts immediately without clobbering outer caches.
    -- 2. Cross-layer capture-stage re-entry (e.g. Layer 5 sampling Layer 3 before outer mip/cache build)
    --    snapshots and restores outer VL_BF & P cleanly so multi-instance setups never corrupt state!
    _VL_PERSIST.in_beam_layers = _VL_PERSIST.in_beam_layers or {}
    local self_key = (type(obj.layer) == "number" and obj.layer > 0) and obj.layer or 0
    if _VL_PERSIST.in_beam then
        if _VL_PERSIST.in_beam_layers[self_key] or (_VL_PERSIST.in_beam_stage or 0) >= 2 then
            _VL_PERSIST.reentered = (_VL_PERSIST.reentered or 0) + 1
            return false
        end
        local saved_bf = {}
        for k, v in pairs(VL_BF) do saved_bf[k] = v end
        local saved_p = {}
        for k, v in pairs(P) do saved_p[k] = v end
        local saved_stage = _VL_PERSIST.in_beam_stage
        _VL_PERSIST.in_beam_layers[self_key] = true
        local ok_in, err_in = pcall(beam_frame)
        _VL_PERSIST.in_beam_layers[self_key] = nil
        _VL_PERSIST.in_beam_stage = saved_stage
        for k in pairs(VL_BF) do VL_BF[k] = nil end
        for k, v in pairs(saved_bf) do VL_BF[k] = v end
        for k in pairs(P) do P[k] = nil end
        for k, v in pairs(saved_p) do P[k] = v end
        if not ok_in then
            pcall(function() print("@error", "Volumetric Light (3D) v3.17 nested error: " .. tostring(err_in)) end)
        end
        return ok_in
    end
    _VL_PERSIST.in_beam = true
    _VL_PERSIST.in_beam_layers[self_key] = true
    _VL_PERSIST.in_beam_stage = 1
    _VL_PERSIST.reentered = 0
    local ok, err = pcall(function()
        vl_bf_reset()
        vl_bf_prepare()
        if VL_BF._abort then vl_lf_withdraw(); return end
        vl_bf_capture()
        if VL_BF._abort then vl_lf_withdraw(); return end
        _VL_PERSIST.in_beam_stage = 2
        vl_bf_geometry()
        if VL_BF._abort then vl_lf_withdraw(); return end
        vl_bf_mip()
        if VL_BF._abort then vl_lf_withdraw(); return end
        vl_bf_occlusion()
        if VL_BF._abort then vl_lf_withdraw(); return end
        vl_bf_render()
    end)
    _VL_PERSIST.in_beam_layers[self_key] = nil
    _VL_PERSIST.in_beam_stage = 0
    _VL_PERSIST.in_beam = false
    if not ok then
        vl_lf_withdraw()
        release_work_caches()
        pcall(function() obj.setoption("draw_state", true) end)
        pcall(function() print("@error", "Volumetric Light (3D) v3.17 error: " .. tostring(err)) end)
    end
    return ok
end

vl_run_frame()
