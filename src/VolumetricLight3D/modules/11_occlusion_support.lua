-- Sutherland-Hodgman 1平面 3D ポリゴンクリップ（d(P) >= 0 半空間）
local function clip_poly_plane(poly, n_in, nx_p, ny_p, nz_p, off_p)
    local out = {}
    local n_out = 0
    local prev = poly[n_in]
    local prev_d = prev[1] * nx_p + prev[2] * ny_p + prev[3] * nz_p - off_p
    for i = 1, n_in do
        local cur = poly[i]
        local cur_d = cur[1] * nx_p + cur[2] * ny_p + cur[3] * nz_p - off_p
        if cur_d >= 0.0 then
            if prev_d < 0.0 then
                local t = prev_d / (prev_d - cur_d)
                n_out = n_out + 1
                out[n_out] = {
                    prev[1] + (cur[1] - prev[1]) * t,
                    prev[2] + (cur[2] - prev[2]) * t,
                    prev[3] + (cur[3] - prev[3]) * t,
                }
            end
            n_out = n_out + 1
            out[n_out] = cur
        elseif prev_d >= 0.0 then
            local t = prev_d / (prev_d - cur_d)
            n_out = n_out + 1
            out[n_out] = {
                prev[1] + (cur[1] - prev[1]) * t,
                prev[2] + (cur[2] - prev[2]) * t,
                prev[3] + (cur[3] - prev[3]) * t,
            }
        end
        prev, prev_d = cur, cur_d
    end
    return out, n_out
end

-- FFI ワークスペースバッファ（再確保ゼロ・カメラ制御行列から 100% 独立の 32bit float Z バッファ！）
local _rast_ws = _VL_PERSIST.rast_ws

local function ensure_rast_scratch(ffi, need_v)
    if (_rast_ws.sc_cap or 0) < (need_v + 1) then
        local cap = need_v + 1024
        _rast_ws.wvx  = ffi.new("double[?]", cap)
        _rast_ws.wvy  = ffi.new("double[?]", cap)
        _rast_ws.wvz  = ffi.new("double[?]", cap)
        _rast_ws.wd1  = ffi.new("double[?]", cap)
        _rast_ws.wd2  = ffi.new("double[?]", cap)
        _rast_ws.px_a = ffi.new("double[?]", cap)
        _rast_ws.py_a = ffi.new("double[?]", cap)
        _rast_ws.pp_a = ffi.new("double[?]", cap)
        _rast_ws.pt_a = ffi.new("double[?]", cap)
        _rast_ws.sc_cap = cap
    end
end

local function upload_rgba_to_cache(target_name, rgba_buf, w, h)
    local ok_up = false
    -- AviUtl2 C API 仕様厳守：putpixeldata の第 2 引数は必ず getpixeldata が返す lightuserdata ポインタを渡す！
    pcall(function()
        obj.clearbuffer(target_name, w, h)
        if type(obj.getpixeldata) == "function" then
            local ok_ffi, ffi = pcall(require, "ffi")
            local p, pw, ph = obj.getpixeldata(target_name, "rgba")
            if ok_ffi and ffi ~= nil and p ~= nil and (type(pw) ~= "number" or (pw == w and ph == h)) then
                ffi.copy(p, rgba_buf, w * h * 4)
                if type(obj.putpixeldata) == "function" then
                    local r = obj.putpixeldata(target_name, p, w, h, "rgba")
                    if r ~= false then ok_up = true end
                else
                    ok_up = true
                end
            end
        end
    end)
    return ok_up
end

-- インタラクティブ操作時（ドラッグ／拡縮中）の遮蔽プレビュー予算制御
-- Dragging/scaling a foreground occluder invalidates the shadow signature on every
-- mouse sample. A full 1280^2 wire splat of a 30k+ triangle cage is hundreds of
-- milliseconds on the UI thread, and AviUtl2 does not coalesce those samples, so
-- Windows reports "Not Responding". While the same frame is being edited rapidly,
-- build a coarse preview (and reuse the 2D mask instead of obj.load). The first
-- non-drag call — mouse-up refresh, pause, scrub, playback — rebuilds full quality.
local function ensure_interact()
    local st = _VL_PERSIST.interact
    if type(st) ~= "table" then
        st = { last_t = -999.0, last_frame = nil, last_xform = nil, hot = false }
        _VL_PERSIST.interact = st
    end
    if type(_VL_PERSIST.occ2d) ~= "table" then
        _VL_PERSIST.occ2d = { key = nil, rgba = nil, w = 0, h = 0, cap = 0, tiles = nil }
    end
    if type(_VL_PERSIST.occ_stats) ~= "table" then
        _VL_PERSIST.occ_stats = {}
    end
    return st
end

local function occ_stat(key)
    local s = ensure_interact() and _VL_PERSIST.occ_stats
    s[key] = (s[key] or 0) + 1
end

local function occ_xform_sig(models_3d, cands_2d)
    local parts = {}
    for i = 1, #models_3d do
        local m = models_3d[i]
        parts[#parts + 1] = string.format(
            "3:%d:%.2f,%.2f,%.2f:%.3f,%.3f,%.3f:%.3f,%.3f,%.3f",
            m.layer, m.ox, m.oy, m.oz, m.Orx, m.Ory, m.Orz, m.ozx, m.ozy, m.ozz)
    end
    for i = 1, #cands_2d do
        local c = cands_2d[i]
        parts[#parts + 1] = string.format(
            "2:%d:%.2f,%.2f,%.2f:%.3f,%.3f,%.3f:%.3f,%.3f:%.3f",
            c.layer, c.ox, c.oy, c.oz, c.Orx, c.Ory, c.Orz, c.ozx, c.ozy, c.alpha or 1)
    end
    return table.concat(parts, ";")
end

local function occ2d_key(cands, frame)
    local sid_k = (type(obj.scene_id) == "number") and math.floor(obj.scene_id + 0.5) or 0
    local ftag = (sid_k ~= 0) and ("S" .. tostring(sid_k) .. ":" .. tostring(frame or -1)) or tostring(frame or -1)
    local parts = { ftag }
    for i = 1, #cands do
        local c = cands[i]
        parts[#parts + 1] = string.format("%d:%.3f:%d:%.2f:%.2f:%.1f:%.1f:%s",
            c.layer, c.alpha or 1, c.is_text and 1 or 0,
            c.align_fx or 0, c.align_fy or 0, c.pad_r or 0, c.pad_b or 0, c.load_mode or "fx")
    end
    return table.concat(parts, "|")
end

-- now_opt is for tests; the host path uses vl_wall_time_sec().
local function occ_drag_query(xform_sig, cur_frame, now_opt)
    local st = ensure_interact()
    local now = now_opt
    if type(now) ~= "number" then
        now = vl_wall_time_sec()
    end
    local same_frame = (cur_frame ~= nil and cur_frame == st.last_frame)
    local changed = (xform_sig ~= st.last_xform)
    -- Any same-frame transform edit is a drag. Playback and scrub change the frame
    -- and stay on the full-quality path. A later call with the same transform
    -- (mouse-up refresh, pause, play) also rebuilds at full quality.
    local dragging = same_frame and changed and st.last_xform ~= nil
    st.last_t = now
    st.last_frame = cur_frame
    st.last_xform = xform_sig
    st.hot = dragging
    return dragging
end

-- v3.06: 現在の "object" バッファの α カバレッジを疎格子サンプリングで推定するシルエット監査。
-- 公式ドキュメント既知の「obj.load("layer", no, effect) がエフェクト側描画で不正な結果を返す」ケース
-- （全面不透明のベタ矩形化など）を検出する。監査不能時は nil を返し挙動は従来通り。
local function audit_object_alpha(ow, oh)
    if type(obj.getpixeldata) ~= "function" then return nil end
    local ok_a, cov = pcall(function()
        local ok_ffi, ffi = pcall(require, "ffi")
        if not ok_ffi or ffi == nil then return nil end
        local p, pw, ph = obj.getpixeldata("object", "rgba")
        if p == nil then return nil end
        local w = (type(pw) == "number" and pw > 0) and pw or ow
        local h = (type(ph) == "number" and ph > 0) and ph or oh
        if type(w) ~= "number" or type(h) ~= "number" or w < 4 or h < 4 or w * h > 268435456 then return nil end
        local px = ffi.cast("const uint8_t*", p)
        local nx = w < 64 and w or 64
        local ny = h < 64 and h or 64
        local hit, tot = 0, 0
        for j = 0, ny - 1 do
            local row = math.floor((j + 0.5) * h / ny) * w * 4
            for i = 0, nx - 1 do
                local a = px[row + math.floor((i + 0.5) * w / nx) * 4 + 3]
                tot = tot + 1
                if a > 12 then hit = hit + 1 end
            end
        end
        return hit / tot
    end)
    if ok_a and type(cov) == "number" then return cov end
    return nil
end

local function preview_shadow_budget(nt)
    nt = nt or 0
    local res = (nt > 25000) and 256 or 320
    local tri_cap = (nt > 20000) and 4000 or 7000
    return res, tri_cap, 48, 0.012
end

local function save_occ2d_atlas(src_name, w, h, key, tiles, uv_span_x, uv_span_y)
    local ok_ffi, ffi = pcall(require, "ffi")
    if not (ok_ffi and ffi ~= nil) or type(obj.getpixeldata) ~= "function" then return false end
    local saved = false
    pcall(function()
        local p, pw, ph = obj.getpixeldata(src_name, "rgba")
        if p == nil then return end
        local ww = (type(pw) == "number" and pw > 0) and pw or w
        local hh = (type(ph) == "number" and ph > 0) and ph or h
        if ww < 1 or hh < 1 or ww > 4096 or hh > 4096 then return end
        local nbytes = ww * hh * 4
        local slot = _VL_PERSIST.occ2d
        if (slot.cap or 0) < nbytes then
            slot.rgba = ffi.new("uint8_t[?]", nbytes)
            slot.cap = nbytes
        end
        ffi.copy(slot.rgba, p, nbytes)
        slot.w, slot.h = ww, hh
        slot.key = key
        slot.tiles = tiles
        slot.uv_span_x = uv_span_x
        slot.uv_span_y = uv_span_y
        saved = true
    end)
    return saved
end

local function try_restore_occ2d(cands, frame)
    local slot = _VL_PERSIST.occ2d
    if type(slot) ~= "table" or slot.rgba == nil or slot.key ~= occ2d_key(cands, frame) then
        return nil
    end
    if not upload_rgba_to_cache("cache:vl3d/occ_a", slot.rgba, slot.w, slot.h) then
        return nil
    end
    return slot
end

_G._VL3D_OCC_API = {
    drag_query = occ_drag_query,
    xform_sig = occ_xform_sig,
    occ2d_key = occ2d_key,
    preview_budget = preview_shadow_budget,
    ensure_interact = ensure_interact,
}


-- 純 LuaJIT FFI 2D 三角形 MAX-proximity ラスタライザ（極細メッシュ欠落防止スプラット付き）
local function rasterize_tri_max(zbuf, row_min, row_max, w, h, x0, y0, p0, x1, y1, p1, x2, y2, p2, tag_bias, allow_wire_splat, splat_cap)
    local minx = x0; if x1 < minx then minx = x1 end; if x2 < minx then minx = x2 end
    local maxx = x0; if x1 > maxx then maxx = x1 end; if x2 > maxx then maxx = x2 end
    local miny = y0; if y1 < miny then miny = y1 end; if y2 < miny then miny = y2 end
    local maxy = y0; if y1 > maxy then maxy = y1 end; if y2 > maxy then maxy = y2 end

    if maxx < 0.0 or minx >= w or maxy < 0.0 or miny >= h then return end

    local ix0 = math.floor(minx)
    local ix1 = math.floor(maxx)
    local iy0 = math.floor(miny)
    local iy1 = math.floor(maxy)
    if ix0 < 0 then ix0 = 0 end
    if ix1 >= w then ix1 = w - 1 end
    if iy0 < 0 then iy0 = 0 end
    if iy1 >= h then iy1 = h - 1 end
    if ix0 > ix1 or iy0 > iy1 then return end

    local area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    -- 影マップ (allow_wire_splat == true) 専用：極細ポリゴン（細線・格子メッシュ等）の欠落防止スプラット
    -- カメラ深度 (allow_wire_splat == false) では無効化し、シルエット接線ポリゴンの外周黒縁膨張を 0.0px に根絶！
    local span_max = math.max(maxx - minx, maxy - miny)
    if allow_wire_splat and math.abs(area) < 1.35 * math.max(span_max, 0.25) then
        local ax, ay, ap, bx_e, by_e, bp = x0, y0, p0, x1, y1, p1
        local d01 = math.abs(x1 - x0) + math.abs(y1 - y0)
        local d12 = math.abs(x2 - x1) + math.abs(y2 - y1)
        local d20 = math.abs(x0 - x2) + math.abs(y0 - y2)
        if d12 >= d01 and d12 >= d20 then
            ax, ay, ap, bx_e, by_e, bp = x1, y1, p1, x2, y2, p2
        elseif d20 >= d01 then
            ax, ay, ap, bx_e, by_e, bp = x2, y2, p2, x0, y0, p0
        end
        local n_step = math.min(splat_cap or 2048, math.max(1, math.ceil(math.max(math.abs(bx_e - ax), math.abs(by_e - ay)))))
        local inv_ns = 1.0 / n_step
        local stx, sty, stp_p = (bx_e - ax) * inv_ns, (by_e - ay) * inv_ns, (bp - ap) * inv_ns
        local sx, sy, sp_v = ax, ay, ap
        for _ = 0, n_step do
            local px_i = math.floor(sx)
            local py_i = math.floor(sy)
            if px_i >= 0 and px_i < w and py_i >= 0 and py_i < h then
                local pr_c = sp_v
                if pr_c < 0.0 then pr_c = 0.0 elseif pr_c > 1.0 then pr_c = 1.0 end
                local idx = py_i * w + px_i
                local cur = zbuf[idx]
                local cur_p = (cur >= 2.0) and (cur - 2.0) or cur
                if pr_c > cur_p then
                    zbuf[idx] = pr_c + tag_bias
                    if px_i < row_min[py_i] then row_min[py_i] = px_i end
                    if px_i > row_max[py_i] then row_max[py_i] = px_i end
                end
            end
            sx = sx + stx; sy = sy + sty; sp_v = sp_v + stp_p
        end
        if math.abs(area) < 1e-5 then return end
    end

    if math.abs(area) < 1e-5 then return end
    if area < 0.0 then
        x1, y1, p1, x2, y2, p2 = x2, y2, p2, x1, y1, p1
        area = -area
    end
    local inv_a = 1.0 / area
    local dx12, dy12 = x1 - x2, y1 - y2
    local dx20, dy20 = x2 - x0, y2 - y0
    local dx01, dy01 = x0 - x1, y0 - y1
    local eps = allow_wire_splat
        and (-0.02 * math.max(math.abs(dx12) + math.abs(dy12), math.abs(dx20) + math.abs(dy20), 1.0))
        or (-1e-5)

    for py = iy0, iy1 do
        local cy = py + 0.5
        -- Analytic 3-edge half-space X-interval clipping on scanline y = cy:
        -- w_e(px) = (px + 0.5 - xe) * dye - (cy - ye) * dxe >= eps  <=>  dye * px >= rhs_e
        local px0, px1 = ix0, ix1
        local rhs0 = eps + (cy - y1) * dx12 - (0.5 - x1) * dy12
        if dy12 > 1e-9 then
            local lb = math.ceil(rhs0 / dy12 - 1e-12)
            if lb > px0 then px0 = lb end
        elseif dy12 < -1e-9 then
            local ub = math.floor(rhs0 / dy12 + 1e-12)
            if ub < px1 then px1 = ub end
        elseif rhs0 > 0.0 then
            px0 = px1 + 1
        end
        if px0 <= px1 then
            local rhs1 = eps + (cy - y2) * dx20 - (0.5 - x2) * dy20
            if dy20 > 1e-9 then
                local lb = math.ceil(rhs1 / dy20 - 1e-12)
                if lb > px0 then px0 = lb end
            elseif dy20 < -1e-9 then
                local ub = math.floor(rhs1 / dy20 + 1e-12)
                if ub < px1 then px1 = ub end
            elseif rhs1 > 0.0 then
                px0 = px1 + 1
            end
        end
        if px0 <= px1 then
            local rhs2 = eps + (cy - y0) * dx01 - (0.5 - x0) * dy01
            if dy01 > 1e-9 then
                local lb = math.ceil(rhs2 / dy01 - 1e-12)
                if lb > px0 then px0 = lb end
            elseif dy01 < -1e-9 then
                local ub = math.floor(rhs2 / dy01 + 1e-12)
                if ub < px1 then px1 = ub end
            elseif rhs2 > 0.0 then
                px0 = px1 + 1
            end
        end
        if px0 <= px1 then
            local cx = px0 + 0.5
            local w0 = (cx - x1) * dy12 - (cy - y1) * dx12
            local w1 = (cx - x2) * dy20 - (cy - y2) * dx20
            local w2 = (cx - x0) * dy01 - (cy - y0) * dx01
            local row_off = py * w
            local rmin = row_min[py]
            local rmax = row_max[py]
            for px = px0, px1 do
                if w0 >= eps and w1 >= eps and w2 >= eps then
                    local b0 = w0 * inv_a
                    local b1 = w1 * inv_a
                    local pr = b0 * p0 + b1 * p1 + (1.0 - b0 - b1) * p2
                    if pr < 0.0 then pr = 0.0 elseif pr > 1.0 then pr = 1.0 end
                    local idx = row_off + px
                    local cur = zbuf[idx]
                    local cur_p = (cur >= 2.0) and (cur - 2.0) or cur
                    if pr > cur_p then
                        zbuf[idx] = pr + tag_bias
                        if px < rmin then rmin = px end
                        if px > rmax then rmax = px end
                    end
                end
                w0 = w0 + dy12
                w1 = w1 + dy20
                w2 = w2 + dy01
            end
            row_min[py] = rmin
            row_max[py] = rmax
        end
    end
end

