local function vl_bf_occlusion()
    VL_BF.interact_drag = false
    -- 4) 複数前景オブジェクト（2D 画像・テキスト ＋ .obj / .vrm 3D モデル）の統合遮蔽構築！
    --    キャッシュ上限 1,600 万ドットに対し常時 <= 510 万ドット（余裕 1,090 万ドット以上確保！）
    ------------------------------------------------------------
    VL_BF.occ_str_val= math.max(0.0, math.min(1.0, P.occ_strength / 100.0))
    VL_BF.occ_tex_name= "cache:vl3d/occ_a"
    VL_BF.shmap_tex_name= "cache:vl3d/shmap"
    VL_BF.camocc_tex_name= "cache:vl3d/camocc"
    pcall(function() obj.clearbuffer(VL_BF.occ_tex_name, 4, 4) end)
    pcall(function() obj.clearbuffer(VL_BF.shmap_tex_name, 4, 4) end)
    pcall(function() obj.clearbuffer(VL_BF.camocc_tex_name, 4, 4) end)

    VL_BF.n_occ_active= 0
    VL_BF.sh3d_active= 0.0
    VL_BF.sh_res_val= 4.0
    VL_BF.sh_soft_px= 0.0
    VL_BF.camocc_active= 0.0
    VL_BF.camocc_w= 4.0
    VL_BF.camocc_h= 4.0
    VL_BF.camocc_max_z= 16384.0
    VL_BF.uv_span_x, VL_BF.uv_span_y= 1.0, 1.0
    VL_BF.occ_flat= {}
    for i = 1, MAX_OCC * 16 do VL_BF.occ_flat[i] = 0.0 end
    VL_BF.occ_desc= "OFF"
    VL_BF.need_manual_hint= false
    VL_BF.lower_2d_occ_layer= nil

    VL_BF.occ3d_int= math.floor(P.occ_3d_mode + 0.5)
    do
    VL_BF.mode_int= math.floor(P.occ_mode + 0.5)
    VL_BF.want_2d_occ= (VL_BF.mode_int >= 1)
    VL_BF.want_3d_occ= (VL_BF.mode_int >= 1) and (VL_BF.occ3d_int ~= 3)
    VL_BF.need_3d_sh= (VL_BF.mode_int == 1 or VL_BF.mode_int == 3)

    if VL_BF.mip_ready and VL_BF.mode_int > 0 and VL_BF.occ_str_val > 0.001 then
        local cand_layers = {}
        if P.occ_target == 2 then
            cand_layers = parse_layer_list(P.occ_list, VL_BF.ln, VL_BF.self_layer)
            if #cand_layers == 0 then
                VL_BF.need_manual_hint = true
            end
        elseif P.occ_target == 1 then
            local lo = math.min(VL_BF.ln, VL_BF.self_layer > 0 and VL_BF.self_layer or (VL_BF.ln + 32)) + 1
            local hi = math.max(VL_BF.ln, VL_BF.self_layer > 0 and VL_BF.self_layer or (VL_BF.ln + 32)) - 1
            for oln = lo, hi do
                if oln ~= VL_BF.ln and oln ~= VL_BF.self_layer then
                    cand_layers[#cand_layers + 1] = oln
                end
            end
        else
            local max_scan = math.max(36, (VL_BF.self_layer > 0 and VL_BF.self_layer or 8) + 20, VL_BF.ln + 20)
            for oln = 1, max_scan do
                if oln ~= VL_BF.ln and oln ~= VL_BF.self_layer then
                    cand_layers[#cand_layers + 1] = oln
                end
            end
        end

        local cur_frame = type(obj.frame) == "number" and vl_scene_frame() or nil
        local probe0 = resolve_live_scene_layer_object(1, cur_frame)
        local aup2_hints = (VL_BF.want_3d_occ and (probe0 == nil or probe0.status == "UNAVAILABLE"))
            and scan_aup2_layer_models(cur_frame) or nil

        local cands_2d  = {}
        local models_3d = {}
        local hit_names = {}

        for i = 1, #cand_layers do
            local oln = cand_layers[i]
            local o_exists = false
            pcall(function()
                if obj.getvalue("layer" .. oln) == true then o_exists = true end
            end)
            if o_exists and (P.occ_target == 2 or not is_volume_effect_layer(oln)) then
                local ox_w, oy_w, oz_w, _, _, _, ozx, ozy, ozz, ola, Orx, Ory, Orz, Odx, Ody, Odz, Onx, Ony, Onz =
                    read_layer_transform(oln, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                if ola > 0.005 then
                    local mesh3d, mkind, linfo = nil, nil, nil
                    if VL_BF.want_3d_occ then
                        mesh3d, mkind, linfo = try_load_3d_layer_mesh(oln, aup2_hints, cur_frame)
                    else
                        linfo = resolve_live_scene_layer_object(oln, cur_frame)
                    end
                    local lstat = linfo and linfo.status or "UNAVAILABLE"
                    local lfnm  = linfo and linfo.fx_name or ""
                    local skip_2d = (lstat == "EMPTY" or lstat == "HIDDEN" or lstat == "DISABLED"
                        or lstat == "OBJ" or lstat == "VRM" or lstat == "WATER"
                        or lfnm == "カメラ制御" or lfnm == "音声ファイル"
                        or is_volume_fx_name(lfnm))
                    if mesh3d ~= nil then
                        models_3d[#models_3d + 1] = {
                            layer = oln, kind = mkind, mesh = mesh3d,
                            alpha = ola, ozx = ozx, ozy = ozy, ozz = ozz,
                            ox = ox_w, oy = oy_w, oz = oz_w,
                            Orx = Orx, Ory = Ory, Orz = Orz,
                            Odx = Odx, Ody = Ody, Odz = Odz,
                            Onx = Onx, Ony = Ony, Onz = Onz,
                        }
                        hit_names[#hit_names + 1] = string.format("L%d(%s)", oln, mkind)
                    elseif (not skip_2d) and VL_BF.want_2d_occ and #cands_2d < MAX_OCC and has_3d_or_water_signature(oln) == nil then
                        local front_dist = (ox_w - VL_BF.wx) * (-VL_BF.Wnx) + (oy_w - VL_BF.wy) * (-VL_BF.Wny) + (oz_w - VL_BF.wz) * (-VL_BF.Wnz)
                        if P.occ_target == 2 or front_dist > 1.0 then
                            if VL_BF.self_layer > 0 and oln > VL_BF.self_layer then
                                if VL_BF.lower_2d_occ_layer == nil or oln > VL_BF.lower_2d_occ_layer then
                                    VL_BF.lower_2d_occ_layer = oln
                                end
                            end
                            local is_txt, afx, afy, pl, pr, pt, pb = resolve_text_align_for_layer(oln, cur_frame, linfo, aup2_hints)
                            cands_2d[#cands_2d + 1] = {
                                layer = oln, alpha = ola, ozx = ozx, ozy = ozy,
                                ox = ox_w, oy = oy_w, oz = oz_w,
                                Orx = Orx, Ory = Ory, Orz = Orz,
                                Odx = Odx, Ody = Ody, Odz = Odz,
                                Onx = Onx, Ony = Ony, Onz = Onz,
                                is_text = is_txt, align_fx = afx, align_fy = afy,
                                pad_l = pl, pad_r = pr, pad_t = pt, pad_b = pb,
                            }
                        end
                    end
                end
            end
        end

        local live_occ_dots = 0

        local xform_sig = occ_xform_sig(models_3d, cands_2d)
        local dragging = occ_drag_query(xform_sig, cur_frame)
        VL_BF.interact_drag = dragging

        -- 4A) 2D レイヤー遮蔽アトラス構築
        -- ドラッグ中は同一フレームのマスク画素を再利用し、obj.load("layer") を呼ばない。
        -- 位置・回転・拡縮は occ_flat のユニフォーム側で追従する（画素自体は変わらない）。
        local reused_2d = false
        if dragging and VL_BF.want_2d_occ and #cands_2d > 0 then
            -- v3.06: 前回保存時の読み込みモード（fx/raw）を候補へ事前スタンプし、キャッシュキー不一致に
            -- よるドラッグ中のマスク再構築ラグ（点滅）を防止。
            local slot_pre = _VL_PERSIST.occ2d
            if type(slot_pre) == "table" and type(slot_pre.tiles) == "table" then
                for ci = 1, #cands_2d do
                    for ti2 = 1, #slot_pre.tiles do
                        if slot_pre.tiles[ti2].layer == cands_2d[ci].layer then
                            cands_2d[ci].load_mode = slot_pre.tiles[ti2].load_mode
                            break
                        end
                    end
                end
            end
            local slot = try_restore_occ2d(cands_2d, cur_frame)
            if slot ~= nil and type(slot.tiles) == "table" then
                local by_layer = {}
                for ti = 1, #slot.tiles do
                    by_layer[slot.tiles[ti].layer] = slot.tiles[ti]
                end
                local packed_n = 0
                for idx = 1, #cands_2d do
                    local cd = cands_2d[idx]
                    local tile = by_layer[cd.layer]
                    if tile ~= nil and tile.inner_ox > 0 and tile.inner_oy > 0 and tile.atl_ox > 0 and tile.atl_oy > 0 then
                        packed_n = packed_n + 1
                        local oc_x, oc_y, oc_z = cd.ox, cd.oy, cd.oz
                        if cd.is_text then
                            local cx_a, cy_a = calc_text_center_shift(
                                tile.w_raw or tile.ow, tile.h_raw or tile.oh,
                                cd.align_fx, cd.align_fy, cd.pad_l, cd.pad_r, cd.pad_t, cd.pad_b)
                            oc_x = oc_x - cd.Orx * (cx_a * cd.ozx) - cd.Odx * (cy_a * cd.ozy)
                            oc_y = oc_y - cd.Ory * (cx_a * cd.ozx) - cd.Ody * (cy_a * cd.ozy)
                            oc_z = oc_z - cd.Orz * (cx_a * cd.ozx) - cd.Odz * (cy_a * cd.ozy)
                        end
                        local OHx_eff = math.max(2.0, 0.5 * tile.ow * cd.ozx) * (tile.tile_ox / tile.inner_ox)
                        local OHy_eff = math.max(2.0, 0.5 * tile.oh * cd.ozy) * (tile.tile_oy / tile.inner_oy)
                        local b = (packed_n - 1) * 16
                        VL_BF.occ_flat[b + 1],  VL_BF.occ_flat[b + 2],  VL_BF.occ_flat[b + 3],  VL_BF.occ_flat[b + 4]  = oc_x, oc_y, oc_z, 1.0 / (2.0 * OHx_eff)
                        VL_BF.occ_flat[b + 5],  VL_BF.occ_flat[b + 6],  VL_BF.occ_flat[b + 7],  VL_BF.occ_flat[b + 8]  = cd.Orx, cd.Ory, cd.Orz, 1.0 / (2.0 * OHy_eff)
                        VL_BF.occ_flat[b + 9],  VL_BF.occ_flat[b + 10], VL_BF.occ_flat[b + 11], VL_BF.occ_flat[b + 12] = cd.Odx, cd.Ody, cd.Odz, tile.x0 / tile.atl_ox
                        VL_BF.occ_flat[b + 13], VL_BF.occ_flat[b + 14], VL_BF.occ_flat[b + 15], VL_BF.occ_flat[b + 16] = cd.Onx, cd.Ony, cd.Onz, tile.y0 / tile.atl_oy
                        hit_names[#hit_names + 1] = "L" .. tostring(cd.layer) .. ((cd.load_mode == "raw") and "*" or "")
                    end
                end
                if packed_n > 0 then
                    VL_BF.occ_tex_name = "cache:vl3d/occ_a"
                    VL_BF.n_occ_active = packed_n
                    VL_BF.uv_span_x = slot.uv_span_x or 1.0
                    VL_BF.uv_span_y = slot.uv_span_y or 1.0
                    live_occ_dots = live_occ_dots + slot.w * slot.h
                    reused_2d = true
                    occ_stat("layer_load_skipped")
                end
            end
        end
        if (not reused_2d) and VL_BF.want_2d_occ and #cands_2d > 0 then
            -- 注意：vl_bf_mip() 完了後の "object" は以降一切参照されない（vl_bf_render で ATLAS により即上書き）。
            -- 超高解像度光源（4K/8K 超）を cache:* へ生寸退避すると TemporaryImageCacheSize (16M) 超過で
            -- cache:vl3d/mip 等が LRU 蒸発するため、不要な退避キャッシュ確保は行わない。
            local grid_o = (#cands_2d == 1) and 1 or ((#cands_2d <= 4) and 2 or 3)
            local atl_ox, atl_oy = 1344, 1344
            if grid_o == 1 then
                local fw, fh = 0, 0
                pcall(function() obj.load("layer", cands_2d[1].layer, true) end)
                pcall(function() fw, fh = obj.getpixel() end)
                if type(fw) ~= "number" or fw <= 0 then
                    pcall(function() obj.load("layer", cands_2d[1].layer) end)
                    pcall(function() fw, fh = obj.getpixel() end)
                end
                if type(fw) == "number" and fw > 0 and type(fh) == "number" and fh > 0 then
                    cands_2d[1].pre_w, cands_2d[1].pre_h = fw, fh
                    local pad_est = 40.0
                    local want_w, want_h = fw + pad_est, fh + pad_est
                    local sc = math.min(1.0, 2048.0 / math.max(want_w, 1.0), 2048.0 / math.max(want_h, 1.0),
                        math.sqrt(OCC_SINGLE_BUDGET / math.max(want_w * want_h, 1.0)))
                    atl_ox = math.max(64, math.min(2048, math.floor((want_w * sc) / 8) * 8))
                    atl_oy = math.max(64, math.min(2048, math.floor((want_h * sc) / 8) * 8))
                end
            end

            local tile_ox = math.floor(atl_ox / grid_o)
            local tile_oy = math.floor(atl_oy / grid_o)
            atl_ox = tile_ox * grid_o
            atl_oy = tile_oy * grid_o
            local pad_ox = math.max(4.0, math.min(20.0, math.floor(tile_ox * 0.035)))
            local pad_oy = math.max(4.0, math.min(20.0, math.floor(tile_oy * 0.035)))
            local inner_ox = math.max(8.0, tile_ox - 2.0 * pad_ox)
            local inner_oy = math.max(8.0, tile_oy - 2.0 * pad_oy)

            pcall(function()
                local src_p = "cache:vl3d/occ_a"
                local dst_p = "cache:vl3d/occ_b"
                obj.clearbuffer(src_p, atl_ox, atl_oy)
                obj.clearbuffer(dst_p, atl_ox, atl_oy)
                local cur_peak = VL_BF.mip_dots + 2 * atl_ox * atl_oy
                if cur_peak > VL_BF.peak_cache_dots then VL_BF.peak_cache_dots = cur_peak end

                local packed_n = 0
                local saved_tiles = {}
                for idx = 1, #cands_2d do
                    local cd = cands_2d[idx]
                    local ow, oh = cd.pre_w or 0, cd.pre_h or 0
                    local load_src = "fx"
                    if ow <= 0 then
                        pcall(function() obj.load("layer", cd.layer, true) end)
                        pcall(function() ow, oh = obj.getpixel() end)
                        if type(ow) ~= "number" or ow <= 0 then
                            pcall(function() obj.load("layer", cd.layer) end)
                            pcall(function() ow, oh = obj.getpixel() end)
                            load_src = "raw"
                        end
                    end
                    if type(ow) == "number" and ow > 0 and type(oh) == "number" and oh > 0 then
                        -- v3.06: シルエット監査 —— effect=true 読み込みがほぼ全面不透明のベタ矩形を返した
                        -- 場合（公式既知のエフェクト側描画の不定挙動）、効果なし読み込みへ自動フォールバック！
                        local cov_cur = audit_object_alpha(ow, oh)
                        if cov_cur ~= nil and cov_cur >= 0.985 and load_src == "fx" then
                            local rw, rh = 0, 0
                            pcall(function() obj.load("layer", cd.layer) end)
                            pcall(function() rw, rh = obj.getpixel() end)
                            if type(rw) == "number" and rw > 0 and type(rh) == "number" and rh > 0 then
                                local cov_raw = audit_object_alpha(rw, rh)
                                if cov_raw ~= nil and cov_raw <= cov_cur - 0.12 then
                                    ow, oh = rw, rh
                                    load_src = "raw"
                                    occ_stat("opaque_rect_fallback")
                                end
                            end
                            if load_src == "fx" then
                                pcall(function() obj.load("layer", cd.layer, true) end)
                                pcall(function() ow, oh = obj.getpixel() end)
                            end
                        end
                        cd.load_mode = load_src
                        if cov_cur ~= nil and cov_cur >= 0.985 and load_src == "fx" then
                            cd.opq_hint = true
                        end
                        local ti = packed_n
                        local tx = ti % grid_o
                        local ty = math.floor(ti / grid_o)
                        local x0 = tx * tile_ox
                        local y0 = ty * tile_oy

                        local step_x = ow / inner_ox
                        local step_y = oh / inner_oy
                        local taps_x = (step_x <= 1.05) and 1 or math.min(8, 2 * math.ceil(step_x - 1e-5))
                        local taps_y = (step_y <= 1.05) and 1 or math.min(8, 2 * math.ceil(step_y - 1e-5))

                        local ok_pk = obj.pixelshader("ps_vl_pack", dst_p, { src_p, "object" }, {
                            x0, y0, tile_ox, tile_oy,
                            pad_ox, pad_oy, cd.alpha, 0.0,
                            1.0 / ow, 1.0 / oh, taps_x, taps_y,
                        }, "copy")
                        if ok_pk ~= false then
                            src_p, dst_p = dst_p, src_p
                            packed_n = packed_n + 1
                            local w_raw, h_raw = ow, oh
                            local oc_x, oc_y, oc_z = cd.ox, cd.oy, cd.oz
                            if cd.is_text then
                                local rw0, rh0 = 0, 0
                                pcall(function()
                                    obj.load("layer", cd.layer, false)
                                    rw0, rh0 = obj.getpixel()
                                end)
                                if type(rw0) == "number" and rw0 > 0 and type(rh0) == "number" and rh0 > 0 then
                                    w_raw, h_raw = rw0, rh0
                                end
                                local cx_a, cy_a = calc_text_center_shift(
                                    w_raw, h_raw, cd.align_fx, cd.align_fy, cd.pad_l, cd.pad_r, cd.pad_t, cd.pad_b)
                                oc_x = oc_x - cd.Orx * (cx_a * cd.ozx) - cd.Odx * (cy_a * cd.ozy)
                                oc_y = oc_y - cd.Ory * (cx_a * cd.ozx) - cd.Ody * (cy_a * cd.ozy)
                                oc_z = oc_z - cd.Orz * (cx_a * cd.ozx) - cd.Odz * (cy_a * cd.ozy)
                            end
                            local OHx_eff = math.max(2.0, 0.5 * ow * cd.ozx) * (tile_ox / inner_ox)
                            local OHy_eff = math.max(2.0, 0.5 * oh * cd.ozy) * (tile_oy / inner_oy)
                            local b = (packed_n - 1) * 16
                            VL_BF.occ_flat[b + 1],  VL_BF.occ_flat[b + 2],  VL_BF.occ_flat[b + 3],  VL_BF.occ_flat[b + 4]  = oc_x, oc_y, oc_z, 1.0 / (2.0 * OHx_eff)
                            VL_BF.occ_flat[b + 5],  VL_BF.occ_flat[b + 6],  VL_BF.occ_flat[b + 7],  VL_BF.occ_flat[b + 8]  = cd.Orx, cd.Ory, cd.Orz, 1.0 / (2.0 * OHy_eff)
                            VL_BF.occ_flat[b + 9],  VL_BF.occ_flat[b + 10], VL_BF.occ_flat[b + 11], VL_BF.occ_flat[b + 12] = cd.Odx, cd.Ody, cd.Odz, x0 / atl_ox
                            VL_BF.occ_flat[b + 13], VL_BF.occ_flat[b + 14], VL_BF.occ_flat[b + 15], VL_BF.occ_flat[b + 16] = cd.Onx, cd.Ony, cd.Onz, y0 / atl_oy
                            hit_names[#hit_names + 1] = "L" .. tostring(cd.layer) .. ((cd.load_mode == "raw") and "*" or "")
                            saved_tiles[#saved_tiles + 1] = {
                                layer = cd.layer, ow = ow, oh = oh, w_raw = w_raw, h_raw = h_raw,
                                x0 = x0, y0 = y0,
                                tile_ox = tile_ox, tile_oy = tile_oy,
                                inner_ox = inner_ox, inner_oy = inner_oy,
                                atl_ox = atl_ox, atl_oy = atl_oy,
                                load_mode = cd.load_mode or "fx",
                            }
                        end
                    end
                end

                if packed_n > 0 then
                    local soft_sig = (P.occ_soft / 100.0) * math.min(14.0, math.min(inner_ox, inner_oy) * 0.020)
                    local soft_rad = math.min(20, math.ceil(soft_sig * 2.5))
                    if soft_rad >= 1 and soft_sig > 0.2 then
                        obj.pixelshader("ps_vl_occ_blur", dst_p, { src_p },
                            { atl_ox, atl_oy, 1.0, 0.0, soft_sig, soft_rad }, "copy")
                        obj.pixelshader("ps_vl_occ_blur", src_p, { dst_p },
                            { atl_ox, atl_oy, 0.0, 1.0, soft_sig, soft_rad }, "copy")
                    end
                    obj.clearbuffer(dst_p, 1, 1)
                    VL_BF.occ_tex_name = src_p
                    VL_BF.n_occ_active = packed_n
                    live_occ_dots = atl_ox * atl_oy
                    VL_BF.uv_span_x    = tile_ox / atl_ox
                    VL_BF.uv_span_y    = tile_oy / atl_oy
                    if (_VL_PERSIST.reentered or 0) == 0 then
                        save_occ2d_atlas(src_p, atl_ox, atl_oy, occ2d_key(cands_2d, cur_frame), saved_tiles, VL_BF.uv_span_x, VL_BF.uv_span_y)
                    else
                        VL_BF.n_occ_active = 0
                        occ_stat("layer_load_reentered")
                    end
                    occ_stat("layer_load")
                else
                    obj.clearbuffer(src_p, 4, 4)
                    obj.clearbuffer(dst_p, 1, 1)
                    VL_BF.occ_tex_name = src_p
                end
            end)
            pcall(function() obj.clearbuffer("object", 4, 4) end)
        end

        -- 4B) .obj / .vrm 3D モデル影柱マップ＆カメラ深度遮蔽マップ構築（VL3DBridge D3D11 CS / C++ 優先 ＋ 純 LuaJIT FFI フォールバック）
        local sh_tris_drawn = 0
        if VL_BF.want_3d_occ and #models_3d > 0 then
            local br_occ = vl3d_bridge()
            local has_hw_rast = (br_occ ~= nil and type(br_occ.rasterize_shadow) == "function")
            local nt_all = 0
            for mi = 1, #models_3d do
                nt_all = nt_all + (models_3d[mi].mesh.nt or 0)
            end
            if VL_BF.need_3d_sh then
                local want_sh = (P.res_mode == 0) and 1536 or ((P.res_mode == 2) and 896 or 1280)
                local preview_opt = nil
                if dragging then
                    if has_hw_rast then
                        -- D3D11 CS / C++ -O3 稼働時は全三角形（間引きなし）のまま 768^2 でリアルタイム高精度描画！
                        if want_sh > 768 then want_sh = 768 end
                        preview_opt = { splat_cap = 512 }
                    else
                        local pres, tri_cap, splat_cap, budget = preview_shadow_budget(nt_all)
                        if want_sh > pres then want_sh = pres end
                        preview_opt = { tri_cap = tri_cap, splat_cap = splat_cap, budget = budget }
                    end
                end
                local ok_sh, n_tr = build_3d_shadow_map(
                    models_3d, want_sh, VL_BF.wx, VL_BF.wy, VL_BF.wz, VL_BF.Hx, VL_BF.Hy,
                    VL_BF.Wrx, VL_BF.Wry, VL_BF.Wrz, VL_BF.Wdx, VL_BF.Wdy, VL_BF.Wdz, VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz,
                    VL_BF.mode_kind, VL_BF.sx_p, VL_BF.sy_p, VL_BF.sz_p, VL_BF.u_wn, VL_BF.dS_wn, VL_BF.L_tot,
                    preview_opt
                )
                if ok_sh and n_tr > 0 then
                    VL_BF.sh3d_active   = 1.0
                    VL_BF.sh_res_val    = want_sh
                    VL_BF.sh_soft_px    = (P.occ_soft / 100.0) * (want_sh * 0.011)
                    sh_tris_drawn = n_tr
                    live_occ_dots = live_occ_dots + want_sh * want_sh
                end
            end

            if not VL_BF.is_edit then
                local cm_w = (P.res_mode == 2) and 960 or 1280
                local cm_preview_opt = nil
                if dragging then
                    if has_hw_rast then
                        if cm_w > 640 then cm_w = 640 end
                        cm_preview_opt = { hw_fast = true }
                    else
                        local cm_pres = (nt_all > 25000) and 320 or 384
                        if cm_w > cm_pres then cm_w = cm_pres end
                        cm_preview_opt = { tri_cap = (nt_all > 20000) and 6000 or 10000, budget = 0.010 }
                    end
                end
                local cm_h = math.max(160, math.min(800, math.floor((cm_w * VL_BF.scr_h / math.max(VL_BF.scr_w, 1.0)) / 8 + 0.5) * 8))
                local far_cam = 4096.0
                for i = 1, VL_BF.pi do
                    local zc_i = (VL_BF.pts[i][1] - VL_BF.cam_x) * VL_BF.f_x + (VL_BF.pts[i][2] - VL_BF.cam_y) * VL_BF.f_y + (VL_BF.pts[i][3] - VL_BF.cam_z) * VL_BF.f_z
                    if zc_i + 512.0 > far_cam then far_cam = zc_i + 512.0 end
                end
                local ok_cm = build_3d_cam_depth_map(
                    models_3d, cm_w, cm_h, VL_BF.scr_w, VL_BF.scr_h,
                    VL_BF.cam_x, VL_BF.cam_y, VL_BF.cam_z, VL_BF.focal_d,
                    VL_BF.f_x, VL_BF.f_y, VL_BF.f_z, VL_BF.r_x, VL_BF.r_y, VL_BF.r_z, VL_BF.d_x, VL_BF.d_y, VL_BF.d_z, far_cam,
                    cm_preview_opt
                )
                if ok_cm then
                    VL_BF.camocc_active = 1.0
                    VL_BF.camocc_w      = cm_w
                    VL_BF.camocc_h      = cm_h
                    VL_BF.camocc_max_z  = far_cam
                    live_occ_dots = live_occ_dots + cm_w * cm_h
                end
            end
        end

        local total_live_dots = VL_BF.mip_dots + live_occ_dots
        if total_live_dots > VL_BF.peak_cache_dots then VL_BF.peak_cache_dots = total_live_dots end

        if #hit_names > 0 then
            if sh_tris_drawn > 0 then
                local bk = (_VL_PERSIST.occ_stats and _VL_PERSIST.occ_stats.last_backend) or ""
                local bk_tag = (bk == "D3D11_CS") and ",CS" or ((bk == "CPP_CPU") and ",C++" or "")
                VL_BF.occ_desc = string.format("%s(m%d,tri:%d%s)", table.concat(hit_names, "+"), P.occ_mode, sh_tris_drawn, bk_tag)
            else
                VL_BF.occ_desc = string.format("%s(m%d)", table.concat(hit_names, "+"), P.occ_mode)
            end
        else
            VL_BF.occ_desc = "0hit"
        end
        -- v3.06: ほぼ全面不透明のマスク（本来のシルエットが取得できていない可能性）を HUD で可視化。
        if #hit_names > 0 then
            local any_opq = false
            for qi = 1, #cands_2d do
                if cands_2d[qi].opq_hint then any_opq = true end
            end
            if any_opq then VL_BF.occ_desc = VL_BF.occ_desc .. ",opq" end
        end
        if dragging and sh_tris_drawn > 0 then
            VL_BF.occ_desc = VL_BF.occ_desc .. ",preview"
        end
    end
    end

    -- ドラッグ中はスライスを 1 チャンクに抑え、GPU パスもマウスサンプルに追いつかせる。
    -- 停止・再生・スクラブの次呼び出しは interact_drag が立たないのでフルスライスに戻る。
    if VL_BF.interact_drag and type(VL_BF.total_sl) == "number" and VL_BF.total_sl > 64
        and type(VL_BF.s_max) == "number" and type(VL_BF.s_min) == "number" then
        VL_BF.total_sl = 64
        VL_BF.dz = math.max(1.0, (VL_BF.s_max - VL_BF.s_min) / VL_BF.total_sl)
    end

    ------------------------------------------------------------
end

