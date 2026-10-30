
    ------------------------------------------------------------
    -- 体積光（3D）連動: 光束に照らされた粒子の加算発光
    --   ・光束の内側にある粒子だけを、光束色で色付けした
    --     図形テクスチャ（ぼかし済み）で加算合成する
    --   ・加算合成は順序に依存しないので深度ソートは不要
    ------------------------------------------------------------
    if SNOW_LF.on and SNOW_LF.strength > 0 and #parts > 0 then
        local n_lit = 0
        for k = 1, #parts do
            if (parts[k][15] or 0) > 0.004 then n_lit = n_lit + 1 end
        end
        if n_lit > 0 and fig_ok then
            local t_mix = SNOW_LF.tint
            local tint_r = col_r + (SNOW_LF.avg_r - col_r) * t_mix
            local tint_g = col_g + (SNOW_LF.avg_g - col_g) * t_mix
            local tint_b = col_b + (SNOW_LF.avg_b - col_b) * t_mix
            local tinted = false
            pcall(function()
                local w, h = obj.getpixel()
                if type(w) ~= "number" or type(h) ~= "number" or w < 1 or h < 1 then return end
                if obj.copybuffer("cache:snow118/lf_src", "object") == false then return end
                if obj.clearbuffer("object", w, h) == false then return end
                if obj.pixelshader("ps_snow_shape_tint", "object",
                    { "cache:snow118/lf_src" },
                    { tint_r, tint_g, tint_b, 1.0, 0.0, 0.0, 0.0, 0.0 }, "copy") == false then
                    return
                end
                tinted = true
            end)
            pcall(function() obj.clearbuffer("cache:snow118/lf_src", 1, 1) end)

            if tinted then
                local old_blend = "none"
                pcall(function()
                    local b = obj.getoption("blend")
                    if type(b) == "string" and b ~= "" then old_blend = b end
                end)
                obj.setoption("blend", "add", "force")
                obj.setoption("culling", 0)
                obj.setoption("drawtarget", "framebuffer")

                local glow = SNOW_LF.spread
                local n_b = 6
                local buckets = {}
                for i = 1, n_b do buckets[i] = { list = {}, n = 0 } end
                for k = 1, #parts do
                    local p = parts[k]
                    local lit = p[15] or 0
                    if lit > 0.004 then
                        local p_x, p_y, p_z = p[1], p[2], p[3]
                        local rs = p[4] * 0.5 * glow
                        local rot = p[5]
                        local ca = math.cos(rot)
                        local sa = math.sin(rot)
                        local e_x = (r_x * ca + u_x * sa) * shape_scale_x
                        local e_y = (r_y * ca + u_y * sa) * shape_scale_x
                        local e_z = (r_z * ca + u_z * sa) * shape_scale_x
                        local g_x = (-r_x * sa + u_x * ca) * shape_scale_y
                        local g_y = (-r_y * sa + u_y * ca) * shape_scale_y
                        local g_z = (-r_z * sa + u_z * ca) * shape_scale_y
                        local q = math.floor(math.min(1.0, lit) * n_b)
                        if q < 0 then q = 0 elseif q >= n_b then q = n_b - 1 end
                        local bk = buckets[q + 1]
                        local li = #bk.list
                        bk.list[li + 1] = { p_x - e_x * rs + g_x * rs,
                                            p_y - e_y * rs + g_y * rs,
                                            p_z - e_z * rs + g_z * rs, 0, 0 }
                        bk.list[li + 2] = { p_x + e_x * rs + g_x * rs,
                                            p_y + e_y * rs + g_y * rs,
                                            p_z + e_z * rs + g_z * rs, 1, 0 }
                        bk.list[li + 3] = { p_x + e_x * rs - g_x * rs,
                                            p_y + e_y * rs - g_y * rs,
                                            p_z + e_z * rs - g_z * rs, 1, 1 }
                        bk.list[li + 4] = { p_x - e_x * rs - g_x * rs,
                                            p_y - e_y * rs - g_y * rs,
                                            p_z - e_z * rs - g_z * rs, 0, 1 }
                        bk.n = bk.n + 1
                    end
                end
                for i = 1, n_b do
                    local bk = buckets[i]
                    if bk.n > 0 then
                        local alpha_v = ((i - 0.5) / n_b) * SNOW_LF.strength
                        if alpha_v > 1.0 then alpha_v = 1.0 end
                        for s0 = 1, #bk.list, 6000 do
                            local chunk = {}
                            for j = s0, math.min(s0 + 5999, #bk.list) do
                                chunk[#chunk + 1] = bk.list[j]
                            end
                            obj.drawpoly(chunk, 4, alpha_v)
                            draw_verts = draw_verts + #chunk
                        end
                    end
                end
                pcall(function() obj.setoption("blend", old_blend, "force") end)
                if P.log_mode then
                    -- 既存のログ間引き（global.snow3d_lf）と同じ方式で出す
                    pcall(function()
                        local now_t = obj.time or 0
                        local last = _SNOW_PERSIST.lf_log_t or -1e9
                        if math.abs(now_t - last) > 2.0 or now_t < last then
                            _SNOW_PERSIST.lf_log_t = now_t
                            print("@info", string.format(
                                "雪（3D）体積光連動: 受光粒子 %d / %d（光束 %d 本, 光束色 %.2f,%.2f,%.2f）",
                                n_lit, #parts, SNOW_LF.n, tint_r, tint_g, tint_b))
                        end
                    end)
                end
            end
        end
    end
