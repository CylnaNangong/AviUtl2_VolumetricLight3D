-- =====================================================================
-- VL3DBridge v1.20 メッシュ登録＆ゼロコピー D3D11 CS / C++ 高速ラスタライズ連携
-- =====================================================================
local function vl_encode_b64_ffi(ffi, raw_ptr, n_bytes)
    if n_bytes <= 0 then return "" end
    if _rast_ws.b64_lut == nil then
        local lut = ffi.new("uint8_t[64]")
        local chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        for i = 0, 63 do lut[i] = chars:byte(i + 1) end
        _rast_ws.b64_lut = lut
    end
    local out_len = math.floor((n_bytes + 2) / 3) * 4
    if (_rast_ws.b64_cap or 0) < out_len then
        _rast_ws.b64_out = ffi.new("uint8_t[?]", out_len + 256)
        _rast_ws.b64_cap = out_len + 256
    end
    local src = ffi.cast("const uint8_t*", raw_ptr)
    local dst = _rast_ws.b64_out
    local lut = _rast_ws.b64_lut
    local si, di = 0, 0
    local n_full = math.floor(n_bytes / 3) * 3
    while si < n_full do
        local b0, b1, b2 = src[si], src[si + 1], src[si + 2]
        local v = b0 * 65536 + b1 * 256 + b2
        dst[di]     = lut[math.floor(v / 262144) % 64]
        dst[di + 1] = lut[math.floor(v / 4096) % 64]
        dst[di + 2] = lut[math.floor(v / 64) % 64]
        dst[di + 3] = lut[v % 64]
        si = si + 3
        di = di + 4
    end
    local rem = n_bytes - n_full
    if rem == 1 then
        local b0 = src[si]
        dst[di]     = lut[math.floor(b0 / 4) % 64]
        dst[di + 1] = lut[(b0 % 4) * 16]
        dst[di + 2] = 61 -- '='
        dst[di + 3] = 61
        di = di + 4
    elseif rem == 2 then
        local b0, b1 = src[si], src[si + 1]
        local v = b0 * 256 + b1
        dst[di]     = lut[math.floor(v / 1024) % 64]
        dst[di + 1] = lut[math.floor(v / 16) % 64]
        dst[di + 2] = lut[(v % 16) * 4]
        dst[di + 3] = 61
        di = di + 4
    end
    return ffi.string(dst, di)
end

local function ensure_bridge_mesh_uploaded(ffi, br, md)
    if br == nil or type(br.upload_mesh) ~= "function" or type(br.has_mesh) ~= "function" then
        return 0
    end
    local m = md.mesh
    if m == nil or (m.nv or 0) < 3 or (m.nt or 0) < 1 then return 0 end
    local slot_key = m.bridge_slot_key
    if type(slot_key) ~= "string" or slot_key == "" then
        slot_key = string.format("slot:%s:%d:%s", tostring(md.kind or "M"), tonumber(md.layer) or 0, tostring(m))
        m.bridge_slot_key = slot_key
    end
    local rev_sig = tostring(m.pose_sig or "v1")
    local ok_h, existing_id = pcall(br.has_mesh, slot_key, rev_sig)
    if ok_h and type(existing_id) == "number" and existing_id > 0 then
        return existing_id
    end

    local nv, nt = m.nv, m.nt
    if (_rast_ws.br_vcap or 0) < nv * 3 then
        _rast_ws.br_vbuf = ffi.new("float[?]", nv * 3 + 256)
        _rast_ws.br_vcap = nv * 3 + 256
    end
    if (_rast_ws.br_tcap or 0) < nt * 3 then
        _rast_ws.br_tbuf = ffi.new("uint32_t[?]", nt * 3 + 256)
        _rast_ws.br_tcap = nt * 3 + 256
    end
    local vbuf = _rast_ws.br_vbuf
    local tbuf = _rast_ws.br_tbuf
    local mvx, mvy, mvz = m.vx, m.vy, m.vz
    for k = 1, nv do
        local o = (k - 1) * 3
        vbuf[o]     = mvx[k]
        vbuf[o + 1] = mvy[k]
        vbuf[o + 2] = mvz[k]
    end
    if m.indexed then
        local mi0, mi1, mi2 = m.i0, m.i1, m.i2
        for t = 1, nt do
            local o = (t - 1) * 3
            local a = mi0[t] - 1; if a < 0 then a = 0 elseif a >= nv then a = nv - 1 end
            local b = mi1[t] - 1; if b < 0 then b = 0 elseif b >= nv then b = nv - 1 end
            local c = mi2[t] - 1; if c < 0 then c = 0 elseif c >= nv then c = nv - 1 end
            tbuf[o], tbuf[o + 1], tbuf[o + 2] = a, b, c
        end
    else
        for t = 1, nt do
            local o = (t - 1) * 3
            tbuf[o], tbuf[o + 1], tbuf[o + 2] = o, o + 1, o + 2
        end
    end

    local vb64 = vl_encode_b64_ffi(ffi, vbuf, nv * 12)
    local tb64 = vl_encode_b64_ffi(ffi, tbuf, nt * 12)
    local ok_u, mid = pcall(br.upload_mesh, slot_key, rev_sig, nv, nt, vb64, tb64)
    if ok_u and type(mid) == "number" and mid > 0 then
        return mid
    end
    return 0
end

local function build_bridge_instances_array(ffi, br, models_3d)
    local arr = {}
    local o = 0
    for mi = 1, #models_3d do
        local md = models_3d[mi]
        local mid = ensure_bridge_mesh_uploaded(ffi, br, md)
        if mid <= 0 then return nil end
        arr[o + 1]  = mid
        arr[o + 2]  = (md.kind == "VRM") and 1.0 or 0.0
        arr[o + 3]  = md.ox
        arr[o + 4]  = md.oy
        arr[o + 5]  = md.oz
        arr[o + 6]  = md.Orx * md.ozx
        arr[o + 7]  = md.Ory * md.ozx
        arr[o + 8]  = md.Orz * md.ozx
        arr[o + 9]  = md.Odx * md.ozy
        arr[o + 10] = md.Ody * md.ozy
        arr[o + 11] = md.Odz * md.ozy
        arr[o + 12] = md.Onx * md.ozz
        arr[o + 13] = md.Ony * md.ozz
        arr[o + 14] = md.Onz * md.ozz
        o = o + 14
    end
    return arr
end

-- 光源窓 UV 空間への 3D メッシュ影マップ一括ラスタライズ（VL3DBridge D3D11 CS / C++ 優先 ＋ 純 LuaJIT FFI フォールバック）
local function build_3d_shadow_map(models_3d, sh_res, wx, wy, wz, Hx, Hy,
    Wrx, Wry, Wrz, Wdx, Wdy, Wdz, Wnx, Wny, Wnz,
    mode_kind, sx_p, sy_p, sz_p, u_wn, dS_wn, L_tot, preview_opt)

    local ok_ffi, ffi = pcall(require, "ffi")
    if not (ok_ffi and ffi ~= nil) then return false, 0 end

    local preview = (type(preview_opt) == "table")

    -- 静的シーン（カメラ周回のみ）のフレーム間 0ms キャッシュ判定
    local sig_parts = {
        string.format("%d|%.3f|%.3f|%.3f|%.3f|%.3f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%g|%.3f|%.3f|%.3f|%.4f|%.3f|%.2f",
            sh_res, wx, wy, wz, Hx, Hy, Wrx, Wry, Wrz, Wdx, Wdy, Wdz,
            mode_kind, sx_p, sy_p, sz_p, u_wn, dS_wn, L_tot)
    }
    for mi = 1, #models_3d do
        local md = models_3d[mi]
        sig_parts[#sig_parts + 1] = string.format("%d:%s:%d:%.3f,%.3f,%.3f:%.4f,%.4f,%.4f:%.4f,%.4f,%.4f:%.3f,%.3f,%.3f",
            md.layer, tostring(md.mesh.pose_sig or md.mesh), md.mesh.nt,
            md.ox, md.oy, md.oz, md.Orx, md.Ory, md.Orz, md.Odx, md.Ody, md.Odz, md.ozx, md.ozy, md.ozz)
    end
    local cur_sig = table.concat(sig_parts, ";")
    if (not preview) and _rast_ws.sh_sig == cur_sig and _rast_ws.sh_w == sh_res and _rast_ws.rgba_sh ~= nil and _rast_ws.sh_tris > 0 then
        local ok_cached = upload_rgba_to_cache("cache:vl3d/shmap", _rast_ws.rgba_sh, sh_res, sh_res)
        if ok_cached then
            return true, _rast_ws.sh_tris
        end
    end

    -- Tier 1/2: VL3DBridge v1.20 ネイティブ D3D11 ComputeShader (cs_5_0) / C++17 -O3 ゼロコピー高速パス
    local br = vl3d_bridge()
    if br ~= nil and type(br.rasterize_shadow) == "function" and type(obj.getpixeldata) == "function" then
        local inst_arr = build_bridge_instances_array(ffi, br, models_3d)
        if inst_arr ~= nil then
            local splat_lim = (preview and preview_opt.splat_cap) or 2048
            local param_arr = {
                wx, wy, wz, Hx, Hy,
                Wrx, Wry, Wrz, Wdx, Wdy, Wdz, Wnx, Wny, Wnz,
                mode_kind, sx_p, sy_p, sz_p, u_wn, dS_wn, L_tot, splat_lim
            }
            local handled, hw_ok, hw_tris = false, false, 0
            pcall(function()
                obj.clearbuffer("cache:vl3d/shmap", sh_res, sh_res)
                local p_dst, pw, ph = obj.getpixeldata("cache:vl3d/shmap", "rgba")
                if p_dst ~= nil and (type(pw) ~= "number" or (pw == sh_res and ph == sh_res)) then
                    local ok_r, tris_d, any_px, backend = br.rasterize_shadow(p_dst, sh_res, param_arr, inst_arr)
                    if ok_r == true then
                        handled = true
                        ensure_interact()
                        _VL_PERSIST.occ_stats.last_backend = backend or "BRIDGE"
                        _VL_PERSIST.occ_stats.last_sh_res = sh_res
                        _VL_PERSIST.occ_stats.last_tris = tris_d or 0
                        _VL_PERSIST.occ_stats.last_preview = preview
                        if any_px and (tris_d or 0) > 0 then
                            local r_put = true
                            if type(obj.putpixeldata) == "function" then
                                r_put = (obj.putpixeldata("cache:vl3d/shmap", p_dst, sh_res, sh_res, "rgba") ~= false)
                            end
                            if r_put then
                                hw_ok = true
                                hw_tris = tris_d
                                if not preview then
                                    local np = sh_res * sh_res * 4
                                    if _rast_ws.rcap_sh < np then
                                        _rast_ws.rgba_sh = ffi.new("uint8_t[?]", np)
                                        _rast_ws.rcap_sh = np
                                    end
                                    ffi.copy(_rast_ws.rgba_sh, p_dst, np)
                                    _rast_ws.sh_sig  = cur_sig
                                    _rast_ws.sh_w    = sh_res
                                    _rast_ws.sh_tris = tris_d
                                end
                            end
                        end
                    end
                end
            end)
            if handled then
                if preview then occ_stat("shadow_preview") else occ_stat("shadow_full") end
                return hw_ok, hw_tris
            end
        end
    end

    local npix = sh_res * sh_res
    if _rast_ws.zcap_sh < npix then
        _rast_ws.zbuf_sh = ffi.new("float[?]", npix)
        _rast_ws.zcap_sh = npix
    end
    if _rast_ws.rcap_sh < npix * 4 then
        _rast_ws.rgba_sh = ffi.new("uint8_t[?]", npix * 4)
        _rast_ws.rcap_sh = npix * 4
    end
    if _rast_ws.hcap_sh < sh_res then
        _rast_ws.row_sh = { min = ffi.new("int32_t[?]", sh_res), max = ffi.new("int32_t[?]", sh_res) }
        _rast_ws.hcap_sh = sh_res
    end

    local zbuf    = _rast_ws.zbuf_sh
    local rgba    = _rast_ws.rgba_sh
    local row_min = _rast_ws.row_sh.min
    local row_max = _rast_ws.row_sh.max
    ffi.fill(rgba, npix * 4, 0)
    for y = 0, sh_res - 1 do
        row_min[y] = sh_res
        row_max[y] = -1
    end
    -- zbuf は -1.0f 未書き込み初期化（IEEE754: 0xBF800000 だが高速ループで 1536^2 = 1.2ms）
    for i = 0, npix - 1 do zbuf[i] = -1.0 end

    local inv_2Hx  = 1.0 / (2.0 * Hx)
    local inv_2Hy  = 1.0 / (2.0 * Hy)
    local inv_L    = 1.0 / math.max(L_tot, 1.0)
    local inv_span = 1.0 / 1.08

    local pnx, pny, pnz, poff1
    local use_plane2 = false
    local p2nx, p2ny, p2nz, poff2 = 0.0, 0.0, 0.0, 0.0
    if mode_kind < 0.5 then
        local sgn = (u_wn >= 0.0) and 1.0 or -1.0
        pnx, pny, pnz = Wnx * sgn, Wny * sgn, Wnz * sgn
        poff1 = wx * pnx + wy * pny + wz * pnz + 0.5 * math.abs(u_wn)
    elseif mode_kind < 1.5 then
        local sgn = (dS_wn >= 0.0) and 1.0 or -1.0
        pnx, pny, pnz = Wnx * sgn, Wny * sgn, Wnz * sgn
        poff1 = wx * pnx + wy * pny + wz * pnz + 0.5
    else
        local sgn = (dS_wn >= 0.0) and -1.0 or 1.0
        pnx, pny, pnz = Wnx * sgn, Wny * sgn, Wnz * sgn
        poff1 = wx * pnx + wy * pny + wz * pnz + 0.5
        use_plane2 = true
        p2nx, p2ny, p2nz = -pnx, -pny, -pnz
        poff2 = sx_p * p2nx + sy_p * p2ny + sz_p * p2nz + math.max(2.0, math.abs(dS_wn) * 0.02)
    end

    local function project_pt(px, py, pz)
        local t_ray, qwx, qwy, qwz
        if mode_kind < 0.5 then
            t_ray = ((px - wx) * Wnx + (py - wy) * Wny + (pz - wz) * Wnz) / u_wn
            qwx = px - sx_p * t_ray
            qwy = py - sy_p * t_ray
            qwz = pz - sz_p * t_ray
        else
            local dvx, dvy, dvz = px - sx_p, py - sy_p, pz - sz_p
            local denom = dvx * Wnx + dvy * Wny + dvz * Wnz
            if math.abs(denom) < 1e-4 then denom = (denom >= 0.0) and 1e-4 or -1e-4 end
            local alpha = dS_wn / denom
            local vlen = math.sqrt(dvx * dvx + dvy * dvy + dvz * dvz)
            t_ray = math.abs(1.0 - alpha) * vlen
            qwx = sx_p + dvx * alpha
            qwy = sy_p + dvy * alpha
            qwz = sz_p + dvz * alpha
        end
        local dqx, dqy, dqz = qwx - wx, qwy - wy, qwz - wz
        local u_w = 0.5 + (dqx * Wrx + dqy * Wry + dqz * Wrz) * inv_2Hx
        local v_w = 0.5 + (dqx * Wdx + dqy * Wdy + dqz * Wdz) * inv_2Hy
        -- beamatlas の fpos_sh = uv_sh * sh_res - 0.5 と完全 1:1 一致するピクセル座標 [0, sh_res]
        local x_s = ((u_w + 0.04) * inv_span) * sh_res
        local y_s = ((v_w + 0.04) * inv_span) * sh_res
        local prox = 1.0 - t_ray * inv_L
        if prox < 0.0 then prox = 0.0 elseif prox > 1.0 then prox = 1.0 end
        local z_front = math.abs((px - wx) * Wnx + (py - wy) * Wny + (pz - wz) * Wnz)
        return x_s, y_s, prox, z_front
    end

    local total_tris_in = 0
    local max_nv = 0
    local preview_abort = false
    local t_stop = nil
    if preview and type(preview_opt.budget) == "number" then
        t_stop = vl_wall_time_sec() + preview_opt.budget
    end
    for mi = 1, #models_3d do
        total_tris_in = total_tris_in + models_3d[mi].mesh.nt
        if models_3d[mi].mesh.nv > max_nv then max_nv = models_3d[mi].mesh.nv end
    end
    ensure_rast_scratch(ffi, max_nv)
    local stride = math.max(1, math.ceil(total_tris_in / 160000))

    local tri_drawn = 0
    local wvx, wvy, wvz, wd1, wd2 = _rast_ws.wvx, _rast_ws.wvy, _rast_ws.wvz, _rast_ws.wd1, _rast_ws.wd2
    local px_arr, py_arr, pp_arr, pt_arr = _rast_ws.px_a, _rast_ws.py_a, _rast_ws.pp_a, _rast_ws.pt_a

    for mi = 1, #models_3d do
        local md = models_3d[mi]
        local m = md.mesh
        local mvx, mvy, mvz = m.vx, m.vy, m.vz
        local nv = m.nv
        local ox, oy, oz = md.ox, md.oy, md.oz
        local r0x, r0y, r0z = md.Orx * md.ozx, md.Ory * md.ozx, md.Orz * md.ozx
        local d0x, d0y, d0z = md.Odx * md.ozy, md.Ody * md.ozy, md.Odz * md.ozy
        local n0x, n0y, n0z = md.Onx * md.ozz, md.Ony * md.ozz, md.Onz * md.ozz

        for k = 1, nv do
            local lx, ly, lz = mvx[k], mvy[k], mvz[k]
            local wxp = ox + r0x * lx + d0x * ly + n0x * lz
            local wyp = oy + r0y * lx + d0y * ly + n0y * lz
            local wzp = oz + r0z * lx + d0z * ly + n0z * lz
            wvx[k], wvy[k], wvz[k] = wxp, wyp, wzp
            local d1 = wxp * pnx + wyp * pny + wzp * pnz - poff1
            wd1[k] = d1
            local d2 = use_plane2 and (wxp * p2nx + wyp * p2ny + wzp * p2nz - poff2) or 1.0
            wd2[k] = d2
            if d1 >= 0.0 and d2 >= 0.0 then
                px_arr[k], py_arr[k], pp_arr[k], pt_arr[k] = project_pt(wxp, wyp, wzp)
            end
        end

        local nt = m.nt
        local indexed = m.indexed
        local mi0, mi1, mi2 = m.i0, m.i1, m.i2
        -- .obj の細いワイヤー等は間引かず全三角形描画、超高ポリ VRM のみ必要に応じて間引き
        if preview_abort then break end
        local m_stride = (md.kind == "OBJ" and nt <= 60000) and 1 or stride
        local splat_cap = nil
        if preview then
            local cap_n = preview_opt.tri_cap or 6000
            m_stride = math.max(1, m_stride, math.ceil(nt / math.max(1, cap_n)))
            splat_cap = preview_opt.splat_cap or 48
        end

        for ti = 1, nt, m_stride do
            if t_stop ~= nil and (ti % 256 == 1) and vl_wall_time_sec() >= t_stop then
                preview_abort = true
                break
            end
            local a, b, c
            if indexed then
                a, b, c = mi0[ti], mi1[ti], mi2[ti]
            else
                a = (ti - 1) * 3 + 1; b = a + 1; c = a + 2
            end
            local d1a, d1b, d1c = wd1[a], wd1[b], wd1[c]
            if not (d1a < 0.0 and d1b < 0.0 and d1c < 0.0) then
                local d2a, d2b, d2c = wd2[a], wd2[b], wd2[c]
                if not (d2a < 0.0 and d2b < 0.0 and d2c < 0.0) then
                    if d1a >= 0.0 and d1b >= 0.0 and d1c >= 0.0 and d2a >= 0.0 and d2b >= 0.0 and d2c >= 0.0 then
                        local t0, t1, t2 = pt_arr[a], pt_arr[b], pt_arr[c]
                        if not (t0 > L_tot and t1 > L_tot and t2 > L_tot) then
                            rasterize_tri_max(zbuf, row_min, row_max, sh_res, sh_res,
                                px_arr[a], py_arr[a], pp_arr[a],
                                px_arr[b], py_arr[b], pp_arr[b],
                                px_arr[c], py_arr[c], pp_arr[c], 0.0, true, splat_cap)
                            tri_drawn = tri_drawn + 1
                        end
                    else
                        local poly = {
                            { wvx[a], wvy[a], wvz[a] },
                            { wvx[b], wvy[b], wvz[b] },
                            { wvx[c], wvy[c], wvz[c] },
                        }
                        local np = 3
                        poly, np = clip_poly_plane(poly, np, pnx, pny, pnz, poff1)
                        if np >= 3 and use_plane2 then
                            poly, np = clip_poly_plane(poly, np, p2nx, p2ny, p2nz, poff2)
                        end
                        if np >= 3 then
                            local q0x, q0y, q0p = project_pt(poly[1][1], poly[1][2], poly[1][3])
                            local q1x, q1y, q1p = project_pt(poly[2][1], poly[2][2], poly[2][3])
                            for k = 2, np - 1 do
                                local q2x, q2y, q2p = project_pt(poly[k + 1][1], poly[k + 1][2], poly[k + 1][3])
                                rasterize_tri_max(zbuf, row_min, row_max, sh_res, sh_res,
                                    q0x, q0y, q0p, q1x, q1y, q1p, q2x, q2y, q2p, 0.0, true, splat_cap)
                                tri_drawn = tri_drawn + 1
                                q1x, q1y, q1p = q2x, q2y, q2p
                            end
                        end
                    end
                end
            end
        end
    end

    if tri_drawn < 1 then return false, 0 end

    -- Dirty 行のみ可換 3 帯域 (R, G, B, 255) へ変換（R+G+B == B+G+R なので RGBA/BGRA 完全不変！）
    local any_pixel = false
    for y = 0, sh_res - 1 do
        local x0 = row_min[y]
        local x1 = row_max[y]
        if x0 <= x1 then
            any_pixel = true
            local base = y * sh_res
            for x = x0, x1 do
                local pr = zbuf[base + x]
                if pr >= 0.0 then
                    local r = pr * 3.0 - 2.0; if r < 0.0 then r = 0.0 elseif r > 1.0 then r = 1.0 end
                    local g = pr * 3.0 - 1.0; if g < 0.0 then g = 0.0 elseif g > 1.0 then g = 1.0 end
                    local b = pr * 3.0;       if b < 0.0 then b = 0.0 elseif b > 1.0 then b = 1.0 end
                    local di = (base + x) * 4
                    rgba[di]     = math.floor(r * 255.0 + 0.5)
                    rgba[di + 1] = math.floor(g * 255.0 + 0.5)
                    rgba[di + 2] = math.floor(b * 255.0 + 0.5)
                    rgba[di + 3] = 255
                end
            end
        end
    end

    if not any_pixel then return false, 0 end

    local ok_up = upload_rgba_to_cache("cache:vl3d/shmap", rgba, sh_res, sh_res)
    if preview then occ_stat("shadow_preview") else occ_stat("shadow_full") end
    if _VL_PERSIST.occ_stats ~= nil then
        _VL_PERSIST.occ_stats.last_backend = "LUA_FFI"
        _VL_PERSIST.occ_stats.last_sh_res = sh_res
        _VL_PERSIST.occ_stats.last_tris = tri_drawn
        _VL_PERSIST.occ_stats.last_preview = preview
    end
    if ok_up and (not preview) then
        _rast_ws.sh_sig  = cur_sig
        _rast_ws.sh_w    = sh_res
        _rast_ws.sh_tris = tri_drawn
    end
    return ok_up, tri_drawn
end

_G._VL3D_OCC_API.build_shadow = build_3d_shadow_map

-- カメラ視点深度マップ（純 LuaJIT FFI 32bit Z バッファ・VRM キャラクター識別フラグ＋静的フレーム署名キャッシュ＋ドラッグ予算制御付き）
local function build_3d_cam_depth_map(models_3d, cm_w, cm_h, scr_w, scr_h,
    cam_x, cam_y, cam_z, focal_d, f_x, f_y, f_z, r_x, r_y, r_z, d_x, d_y, d_z, max_z, preview_opt)

    local ok_ffi, ffi = pcall(require, "ffi")
    if not (ok_ffi and ffi ~= nil) then return false end

    local preview = (type(preview_opt) == "table")
    local sig_parts = {
        string.format("%d|%d|%d|%.1f|%.1f|%.3f|%.3f|%.3f|%.2f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.1f",
            preview and 1 or 0, cm_w, cm_h, scr_w, scr_h,
            cam_x, cam_y, cam_z, focal_d, f_x, f_y, f_z, r_x, r_y, r_z, d_x, d_y, d_z, max_z)
    }
    for mi = 1, #models_3d do
        local md = models_3d[mi]
        local m = md.mesh
        sig_parts[#sig_parts + 1] = string.format("%s|%d|%d|%.3f|%.3f|%.3f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f",
            tostring(m), md.layer or 0, m.nt or 0,
            md.ox, md.oy, md.oz, md.ozx, md.ozy, md.ozz,
            md.Orx, md.Ory, md.Orz, md.Odx, md.Ody, md.Odz)
    end
    local cur_cm_sig = table.concat(sig_parts, ";")
    if (not preview) and _rast_ws.cm_sig == cur_cm_sig and _rast_ws.cm_w == cm_w and _rast_ws.cm_h == cm_h and _rast_ws.rgba_cm ~= nil then
        if not _rast_ws.cm_any then return false end
        local ok_cached = upload_rgba_to_cache("cache:vl3d/camocc", _rast_ws.rgba_cm, cm_w, cm_h)
        if ok_cached then
            occ_stat("camocc_cached")
            return true
        end
    end

    -- Tier 1/2: VL3DBridge v1.20 ネイティブ D3D11 ComputeShader (cs_5_0) / C++17 -O3 カメラ深度ゼロコピー高速パス
    local br = vl3d_bridge()
    if br ~= nil and type(br.rasterize_camdepth) == "function" and type(obj.getpixeldata) == "function" then
        local inst_arr = build_bridge_instances_array(ffi, br, models_3d)
        if inst_arr ~= nil then
            local cam_params = {
                scr_w, scr_h, cam_x, cam_y, cam_z, focal_d,
                f_x, f_y, f_z, r_x, r_y, r_z, d_x, d_y, d_z, max_z
            }
            local handled_cm, hw_cm_ok = false, false
            pcall(function()
                obj.clearbuffer("cache:vl3d/camocc", cm_w, cm_h)
                local p_dst, pw, ph = obj.getpixeldata("cache:vl3d/camocc", "rgba")
                if p_dst ~= nil and (type(pw) ~= "number" or (pw == cm_w and ph == cm_h)) then
                    local ok_r, tris_d, any_px = br.rasterize_camdepth(p_dst, cm_w, cm_h, cam_params, inst_arr)
                    if ok_r == true then
                        handled_cm = true
                        if any_px and (tris_d or 0) > 0 then
                            local r_put = true
                            if type(obj.putpixeldata) == "function" then
                                r_put = (obj.putpixeldata("cache:vl3d/camocc", p_dst, cm_w, cm_h, "rgba") ~= false)
                            end
                            if r_put then
                                hw_cm_ok = true
                                if not preview then
                                    local np = cm_w * cm_h * 4
                                    if _rast_ws.rcap_cm < np then
                                        _rast_ws.rgba_cm = ffi.new("uint8_t[?]", np)
                                        _rast_ws.rcap_cm = np
                                    end
                                    ffi.copy(_rast_ws.rgba_cm, p_dst, np)
                                    _rast_ws.cm_sig = cur_cm_sig
                                    _rast_ws.cm_w   = cm_w
                                    _rast_ws.cm_h   = cm_h
                                    _rast_ws.cm_any = true
                                end
                            end
                        else
                            if not preview then
                                _rast_ws.cm_sig = cur_cm_sig
                                _rast_ws.cm_w   = cm_w
                                _rast_ws.cm_h   = cm_h
                                _rast_ws.cm_any = false
                            end
                        end
                    end
                end
            end)
            if handled_cm then
                return hw_cm_ok
            end
        end
    end

    local npix = cm_w * cm_h
    if _rast_ws.zcap_cm < npix then
        _rast_ws.zbuf_cm = ffi.new("float[?]", npix)
        _rast_ws.zcap_cm = npix
    end
    if _rast_ws.rcap_cm < npix * 4 then
        _rast_ws.rgba_cm = ffi.new("uint8_t[?]", npix * 4)
        _rast_ws.rcap_cm = npix * 4
    end
    if _rast_ws.hcap_cm < cm_h then
        _rast_ws.row_cm = { min = ffi.new("int32_t[?]", cm_h), max = ffi.new("int32_t[?]", cm_h) }
        _rast_ws.hcap_cm = cm_h
    end

    local zbuf    = _rast_ws.zbuf_cm
    local rgba    = _rast_ws.rgba_cm
    local row_min = _rast_ws.row_cm.min
    local row_max = _rast_ws.row_cm.max
    ffi.fill(rgba, npix * 4, 0)
    for y = 0, cm_h - 1 do
        row_min[y] = cm_w
        row_max[y] = -1
    end
    for i = 0, npix - 1 do zbuf[i] = -1.0 end

    local scale_x  = (focal_d / math.max(scr_w, 1.0)) * cm_w
    local scale_y  = (focal_d / math.max(scr_h, 1.0)) * cm_h
    local half_w   = cm_w * 0.5
    local half_h   = cm_h * 0.5
    local inv_mz   = 1.0 / math.max(max_z, 1.0)
    local near_off = cam_x * f_x + cam_y * f_y + cam_z * f_z + 4.0

    local function proj_cam(px, py, pz)
        local dx, dy, dz = px - cam_x, py - cam_y, pz - cam_z
        local zc = math.max(4.0, dx * f_x + dy * f_y + dz * f_z)
        local inv_z = 1.0 / zc
        -- beamatlas の uv_c * cm_lim - 0.5 と完全 1:1 一致するピクセル座標 [0, cm_w] x [0, cm_h]
        local xs = half_w + (dx * r_x + dy * r_y + dz * r_z) * scale_x * inv_z
        local ys = half_h + (dx * d_x + dy * d_y + dz * d_z) * scale_y * inv_z
        local prox = 1.0 - zc * inv_mz
        if prox < 0.0 then prox = 0.0 elseif prox > 1.0 then prox = 1.0 end
        return xs, ys, prox
    end

    local total_tris_in = 0
    local max_nv = 0
    for mi = 1, #models_3d do
        total_tris_in = total_tris_in + models_3d[mi].mesh.nt
        if models_3d[mi].mesh.nv > max_nv then max_nv = models_3d[mi].mesh.nv end
    end
    ensure_rast_scratch(ffi, max_nv)
    local tri_cap_cm = (preview and type(preview_opt.tri_cap) == "number") and preview_opt.tri_cap or 140000
    local stride = math.max(1, math.ceil(total_tris_in / tri_cap_cm))
    local t_stop_cm = (preview and type(preview_opt.budget) == "number") and (vl_wall_time_sec() + preview_opt.budget) or nil
    local abort_cm = false

    local tri_drawn = 0
    local wvx, wvy, wvz, wdc = _rast_ws.wvx, _rast_ws.wvy, _rast_ws.wvz, _rast_ws.wd1
    local cx_a, cy_a, cp_a   = _rast_ws.px_a, _rast_ws.py_a, _rast_ws.pp_a

    for mi = 1, #models_3d do
        if abort_cm then break end
        local md = models_3d[mi]
        local m = md.mesh
        local mvx, mvy, mvz = m.vx, m.vy, m.vz
        local nv = m.nv
        local ox, oy, oz = md.ox, md.oy, md.oz
        local r0x, r0y, r0z = md.Orx * md.ozx, md.Ory * md.ozx, md.Orz * md.ozx
        local d0x, d0y, d0z = md.Odx * md.ozy, md.Ody * md.ozy, md.Odz * md.ozy
        local n0x, n0y, n0z = md.Onx * md.ozz, md.Ony * md.ozz, md.Onz * md.ozz
        -- VRM モデルは tag_bias = 2.0（輪郭保護対象）、OBJ モデルは 0.0
        local tag_bias = (md.kind == "VRM") and 2.0 or 0.0

        for k = 1, nv do
            local lx, ly, lz = mvx[k], mvy[k], mvz[k]
            local wxp = ox + r0x * lx + d0x * ly + n0x * lz
            local wyp = oy + r0y * lx + d0y * ly + n0y * lz
            local wzp = oz + r0z * lx + d0z * ly + n0z * lz
            wvx[k], wvy[k], wvz[k] = wxp, wyp, wzp
            local dc = wxp * f_x + wyp * f_y + wzp * f_z - near_off
            wdc[k] = dc
            if dc >= 0.0 then
                cx_a[k], cy_a[k], cp_a[k] = proj_cam(wxp, wyp, wzp)
            end
        end

        local nt = m.nt
        local indexed = m.indexed
        local mi0, mi1, mi2 = m.i0, m.i1, m.i2
        local m_stride = ((not preview) and md.kind == "OBJ" and nt <= 60000) and 1 or stride

        for ti = 1, nt, m_stride do
            if t_stop_cm ~= nil and (tri_drawn % 256 == 255) and vl_wall_time_sec() >= t_stop_cm then
                abort_cm = true
                break
            end
            local a, b, c
            if indexed then a, b, c = mi0[ti], mi1[ti], mi2[ti]
            else a = (ti - 1) * 3 + 1; b = a + 1; c = a + 2 end
            local dca, dcb, dcc = wdc[a], wdc[b], wdc[c]
            if not (dca < 0.0 and dcb < 0.0 and dcc < 0.0) then
                if dca >= 0.0 and dcb >= 0.0 and dcc >= 0.0 then
                    rasterize_tri_max(zbuf, row_min, row_max, cm_w, cm_h,
                        cx_a[a], cy_a[a], cp_a[a],
                        cx_a[b], cy_a[b], cp_a[b],
                        cx_a[c], cy_a[c], cp_a[c], tag_bias, false)
                    tri_drawn = tri_drawn + 1
                else
                    local poly, np = clip_poly_plane({
                        { wvx[a], wvy[a], wvz[a] },
                        { wvx[b], wvy[b], wvz[b] },
                        { wvx[c], wvy[c], wvz[c] },
                    }, 3, f_x, f_y, f_z, near_off)
                    if np >= 3 then
                        local q0x, q0y, q0p = proj_cam(poly[1][1], poly[1][2], poly[1][3])
                        local q1x, q1y, q1p = proj_cam(poly[2][1], poly[2][2], poly[2][3])
                        for k = 2, np - 1 do
                            local q2x, q2y, q2p = proj_cam(poly[k + 1][1], poly[k + 1][2], poly[k + 1][3])
                            rasterize_tri_max(zbuf, row_min, row_max, cm_w, cm_h,
                                q0x, q0y, q0p, q1x, q1y, q1p, q2x, q2y, q2p, tag_bias, false)
                            tri_drawn = tri_drawn + 1
                            q1x, q1y, q1p = q2x, q2y, q2p
                        end
                    end
                end
            end
        end
    end

    if tri_drawn < 1 then return false end

    local any_pixel = false
    for y = 0, cm_h - 1 do
        local x0 = row_min[y]
        local x1 = row_max[y]
        if x0 <= x1 then
            any_pixel = true
            local base = y * cm_w
            for x = x0, x1 do
                local v = zbuf[base + x]
                if v >= 0.0 then
                    local code
                    if v >= 2.0 then
                        -- VRM キャラクター: [0.51, 0.99]
                        code = (v - 2.0) * 0.48 + 0.51
                    else
                        -- OBJ メッシュ: [0.01, 0.49]
                        code = v * 0.48 + 0.01
                    end
                    local r = code * 3.0 - 2.0; if r < 0.0 then r = 0.0 elseif r > 1.0 then r = 1.0 end
                    local g = code * 3.0 - 1.0; if g < 0.0 then g = 0.0 elseif g > 1.0 then g = 1.0 end
                    local b = code * 3.0;       if b < 0.0 then b = 0.0 elseif b > 1.0 then b = 1.0 end
                    local di = (base + x) * 4
                    rgba[di]     = math.floor(r * 255.0 + 0.5)
                    rgba[di + 1] = math.floor(g * 255.0 + 0.5)
                    rgba[di + 2] = math.floor(b * 255.0 + 0.5)
                    rgba[di + 3] = 255
                end
            end
        end
    end

    if not any_pixel then
        if not preview then
            _rast_ws.cm_sig = cur_cm_sig
            _rast_ws.cm_w   = cm_w
            _rast_ws.cm_h   = cm_h
            _rast_ws.cm_any = false
        end
        return false
    end
    local ok_cm_up = upload_rgba_to_cache("cache:vl3d/camocc", rgba, cm_w, cm_h)
    if ok_cm_up and (not preview) then
        _rast_ws.cm_sig = cur_cm_sig
        _rast_ws.cm_w   = cm_w
        _rast_ws.cm_h   = cm_h
        _rast_ws.cm_any = true
    end
    return ok_cm_up
end

