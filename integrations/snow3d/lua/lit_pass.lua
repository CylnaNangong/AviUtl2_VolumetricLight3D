
    ------------------------------------------------------------
    -- 体積光（3D）連動: 光束に照らされた粒子の加算発光
    --   ・光束の内側にある粒子だけを、受光色で色付けした
    --     図形テクスチャ（ぼかし済み）で加算合成する
    --   ・受光色は粒子ごとに違う（ステンドグラス等の多色光源）ため、
    --     色を量子化して上位 4 グループへまとめ、グループ単位で
    --     テクスチャを染め直して描く（ドローコールは最大 4 色 × 3 段階）
    --   ・加算合成は順序に依存しないので深度ソートは不要
    ------------------------------------------------------------
    if SNOW_LF.on and SNOW_LF.strength > 0 and #parts > 0 then
        local t_mix = SNOW_LF.tint
        local function lf_tint_of(p)
            local cr = 1.0 + (((p[12] or 1.0)) - 1.0) * t_mix
            local cg = 1.0 + (((p[13] or 1.0)) - 1.0) * t_mix
            local cb = 1.0 + (((p[14] or 1.0)) - 1.0) * t_mix
            if cr < 0.0 then cr = 0.0 elseif cr > 1.5 then cr = 1.5 end
            if cg < 0.0 then cg = 0.0 elseif cg > 1.5 then cg = 1.5 end
            if cb < 0.0 then cb = 0.0 elseif cb > 1.5 then cb = 1.5 end
            return cr, cg, cb
        end

        -- 1) 受光色を 4 段量子化して集計（上位 4 色だけ残す）
        local groups, glist = {}, {}
        local n_lit = 0
        for k = 1, #parts do
            local p = parts[k]
            local lit = p[15] or 0
            if lit > 0.004 then
                n_lit = n_lit + 1
                local cr, cg, cb = lf_tint_of(p)
                local qr = math.floor(cr * 3.999)
                local qg = math.floor(cg * 3.999)
                local qb = math.floor(cb * 3.999)
                if qr < 0 then qr = 0 elseif qr > 3 then qr = 3 end
                if qg < 0 then qg = 0 elseif qg > 3 then qg = 3 end
                if qb < 0 then qb = 0 elseif qb > 3 then qb = 3 end
                local key = qr * 16 + qg * 4 + qb
                local g = groups[key]
                if not g then
                    g = { n = 0, sr = 0.0, sg = 0.0, sb = 0.0 }
                    groups[key] = g
                    glist[#glist + 1] = g
                end
                g.n = g.n + 1
                g.sr = g.sr + cr
                g.sg = g.sg + cg
                g.sb = g.sb + cb
            end
        end

        if n_lit > 0 and fig_ok then
            table.sort(glist, function(a, b) return a.n > b.n end)
            for gi = 5, #glist do glist[gi] = nil end
            for gi = 1, #glist do
                local g = glist[gi]
                g.r = g.sr / g.n
                g.g = g.sg / g.n
                g.b = g.sb / g.n
                g.buckets = {}
                for bi = 1, 3 do g.buckets[bi] = { list = {}, n = 0 } end
            end

            -- 2) 各粒子を最も近い色グループへ振り分け
            local glow = SNOW_LF.spread
            for k = 1, #parts do
                local p = parts[k]
                local lit = p[15] or 0
                if lit > 0.004 then
                    local cr, cg, cb = lf_tint_of(p)
                    local best, bd = glist[1], 1.0e9
                    for gi = 1, #glist do
                        local g = glist[gi]
                        local dr, dg, db = cr - g.r, cg - g.g, cb - g.b
                        local d = dr * dr + dg * dg + db * db
                        if d < bd then bd = d; best = g end
                    end
                    local qi = math.floor(math.min(1.0, lit) * 3)
                    if qi > 2 then qi = 2 end
                    local bk = best.buckets[qi + 1]
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
                    local p_x, p_y, p_z = p[1], p[2], p[3]
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

            local old_blend = "none"
            local old_culling = nil
            pcall(function()
                local b = obj.getoption("blend")
                if type(b) == "string" and b ~= "" then old_blend = b end
            end)
            obj.setoption("blend", "add", "force")
            obj.setoption("culling", 0)
            obj.setoption("drawtarget", "framebuffer")

            local w_img, h_img = 0, 0
            pcall(function() w_img, h_img = obj.getpixel() end)
            pcall(function() obj.copybuffer("cache:snow118/lf_src", "object") end)

            for gi = 1, #glist do
                local g = glist[gi]
                local gr, gg, gb = g.r, g.g, g.b
                local tinted = false
                pcall(function()
                    if type(w_img) ~= "number" or w_img < 1 or h_img < 1 then return end
                    if obj.clearbuffer("object", w_img, h_img) == false then return end
                    if obj.pixelshader("ps_snow_shape_tint", "object",
                        { "cache:snow118/lf_src" },
                        { gr, gg, gb, 1.0, 0.0, 0.0, 0.0, 0.0 }, "copy") == false then
                        return
                    end
                    tinted = true
                end)
                if tinted then
                    for bi = 1, 3 do
                        local bk = g.buckets[bi]
                        if bk ~= nil and bk.n > 0 then
                            local alpha_v = ((bi - 0.5) / 3) * SNOW_LF.strength
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
                end
            end

            pcall(function() obj.clearbuffer("cache:snow118/lf_src", 1, 1) end)
            pcall(function() obj.setoption("blend", old_blend, "force") end)

            if P.log_mode then
                pcall(function()
                    local now_t = obj.time or 0
                    local last = _SNOW_PERSIST.lf_log_t or -1e9
                    if math.abs(now_t - last) > 2.0 or now_t < last then
                        _SNOW_PERSIST.lf_log_t = now_t
                        local g0 = glist[1]
                        print("@info", string.format(
                            "雪（3D）体積光連動: 受光粒子 %d / %d（光束 %d 本, 色 %d 系統, 主色 %.2f,%.2f,%.2f）",
                            n_lit, #parts, SNOW_LF.n, #glist, g0.r, g0.g, g0.b))
                    end
                end)
            end
        end
    end
