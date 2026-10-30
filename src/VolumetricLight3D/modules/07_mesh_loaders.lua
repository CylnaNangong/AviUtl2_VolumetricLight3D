local function scan_ascii_float(p, pos, len)
    while pos < len do
        local c = p[pos]
        if c == 32 or c == 9 then pos = pos + 1 else break end
    end
    if pos >= len then return nil, pos end
    local sign = 1.0
    local c = p[pos]
    if c == 45 then sign = -1.0; pos = pos + 1
    elseif c == 43 then pos = pos + 1 end

    local int_part = 0.0
    local frac_part = 0.0
    local frac_len = 0
    local has_digit = false

    while pos < len do
        local d = p[pos] - 48
        if d >= 0 and d <= 9 then
            int_part = int_part * 10.0 + d
            has_digit = true
            pos = pos + 1
        else
            break
        end
    end

    if pos < len and p[pos] == 46 then -- '.'
        pos = pos + 1
        while pos < len do
            local d = p[pos] - 48
            if d >= 0 and d <= 9 then
                if frac_len < 16 then
                    frac_part = frac_part * 10.0 + d
                    frac_len = frac_len + 1
                end
                has_digit = true
                pos = pos + 1
            else
                break
            end
        end
    end

    if not has_digit then return nil, pos end

    local val = int_part
    if frac_len > 0 then
        val = val + frac_part * POW10_NEG[frac_len]
    end

    if pos < len and (p[pos] == 101 or p[pos] == 69) then -- 'e' or 'E'
        local epos = pos + 1
        local esign = 1
        if epos < len then
            if p[epos] == 45 then esign = -1; epos = epos + 1
            elseif p[epos] == 43 then epos = epos + 1 end
        end
        local exp_v = 0
        local has_e = false
        while epos < len do
            local d = p[epos] - 48
            if d >= 0 and d <= 9 then
                if exp_v < 400 then exp_v = exp_v * 10 + d end
                has_e = true
                epos = epos + 1
            else
                break
            end
        end
        if has_e then
            pos = epos
            if exp_v <= 38 then
                val = val * (esign > 0 and POW10_POS[exp_v] or POW10_NEG[exp_v])
            else
                val = val * (10.0 ^ (esign * exp_v))
            end
        end
    end

    return sign * val, pos
end

local function scan_ascii_int(p, pos, len)
    local sign = 1
    local c = p[pos]
    if c == 45 then sign = -1; pos = pos + 1
    elseif c == 43 then pos = pos + 1 end
    local v = 0
    local ok_d = false
    while pos < len do
        local d = p[pos] - 48
        if d >= 0 and d <= 9 then
            v = v * 10 + d
            ok_d = true
            pos = pos + 1
        else
            break
        end
    end
    if not ok_d then return nil, pos end
    return sign * v, pos
end

local function load_wavefront_obj_mesh(raw_path, coord_adj, raw_wpath)
    local path = trim_str(raw_path)
    if path == "" and raw_wpath == nil then return nil end
    local flip_z = as_bool(coord_adj)
    local ckey = path .. "|adj=" .. (flip_z and "1" or "0")
    local cached = OBJ_MESH_CACHE[ckey]
    if cached ~= nil then
        return (cached ~= false) and cached or nil
    end

    local data = nil
    if raw_wpath ~= nil then
        data = win32_read_file_wide_direct(raw_wpath)
    end
    if type(data) ~= "string" and path ~= "" then
        data = read_binary_file_utf8(path)
    end
    if type(data) ~= "string" or #data < 8 then
        OBJ_MESH_CACHE[ckey] = false
        return nil
    end

    -- UTF-8 BOM (EF BB BF) を安全にスキップ
    if data:byte(1) == 0xEF and data:byte(2) == 0xBB and data:byte(3) == 0xBF then
        data = data:sub(4)
    end

    local vx, vy, vz = {}, {}, {}
    local nv = 0
    local i0, i1, i2 = {}, {}, {}
    local nt = 0
    local z_mul = flip_z and -100.0 or 100.0
    local face_idx = {}

    local ok_ffi, ffi = pcall(require, "ffi")
    local fast_done = false
    if ok_ffi and ffi ~= nil then
        pcall(function()
            local len = #data
            local p = ffi.cast("const uint8_t*", data)
            local pos = 0

            while pos < len do
                while pos < len do
                    local c = p[pos]
                    if c == 32 or c == 9 then pos = pos + 1 else break end
                end
                if pos >= len then break end

                local b1 = p[pos]
                if b1 == 118 and pos + 1 < len and (p[pos + 1] == 32 or p[pos + 1] == 9) then -- "v "
                    local nx, ny, nz
                    local q = pos + 2
                    nx, q = scan_ascii_float(p, q, len)
                    if nx ~= nil then
                        ny, q = scan_ascii_float(p, q, len)
                        if ny ~= nil then
                            nz, q = scan_ascii_float(p, q, len)
                            if nz ~= nil then
                                nv = nv + 1
                                vx[nv] =  nx * 100.0
                                vy[nv] = -ny * 100.0
                                vz[nv] =  nz * z_mul
                            end
                        end
                    end
                elseif b1 == 102 and pos + 1 < len and (p[pos + 1] == 32 or p[pos + 1] == 9) then -- "f "
                    local nf = 0
                    local lpos = pos + 2
                    while lpos < len and p[lpos] ~= 10 and p[lpos] ~= 13 and p[lpos] ~= 35 do
                        while lpos < len and (p[lpos] == 32 or p[lpos] == 9) do lpos = lpos + 1 end
                        if lpos >= len or p[lpos] == 10 or p[lpos] == 13 or p[lpos] == 35 then break end
                        local idx
                        idx, lpos = scan_ascii_int(p, lpos, len)
                        if idx ~= nil and idx ~= 0 then
                            if idx < 0 then idx = nv + 1 + idx end
                            if idx >= 1 and idx <= nv then
                                nf = nf + 1
                                face_idx[nf] = idx
                            end
                        end
                        while lpos < len and p[lpos] ~= 32 and p[lpos] ~= 9 and p[lpos] ~= 10 and p[lpos] ~= 13 and p[lpos] ~= 35 do
                            lpos = lpos + 1
                        end
                    end
                    if nf >= 3 then
                        local a = face_idx[1]
                        for k = 2, nf - 1 do
                            nt = nt + 1
                            if flip_z then
                                i0[nt], i1[nt], i2[nt] = a, face_idx[k + 1], face_idx[k]
                            else
                                i0[nt], i1[nt], i2[nt] = a, face_idx[k], face_idx[k + 1]
                            end
                        end
                    end
                end

                while pos < len and p[pos] ~= 10 and p[pos] ~= 13 do pos = pos + 1 end
                while pos < len and (p[pos] == 10 or p[pos] == 13) do pos = pos + 1 end
            end
            fast_done = true
        end)
    end

    if not fast_done then
        for line in data:gmatch("[^\r\n]+") do
            local sline = line:match("^%s*(.-)%s*$") or ""
            local b1 = sline:byte(1)
            if b1 == 118 then
                local b2 = sline:byte(2)
                if b2 == 32 or b2 == 9 then
                    local sx, sy, sz = sline:match("^v%s+([%+%-]?[%d%.eE%+%-]+)%s+([%+%-]?[%d%.eE%+%-]+)%s+([%+%-]?[%d%.eE%+%-]+)")
                    local nx, ny, nz = tonumber(sx), tonumber(sy), tonumber(sz)
                    if nx and ny and nz then
                        nv = nv + 1
                        vx[nv] =  nx * 100.0
                        vy[nv] = -ny * 100.0
                        vz[nv] =  nz * z_mul
                    end
                end
            elseif b1 == 102 then
                local b2 = sline:byte(2)
                if b2 == 32 or b2 == 9 then
                    local nf = 0
                    for tok in sline:sub(3):gmatch("([%+%-]?%d+)[^%s]*") do
                        local idx = tonumber(tok)
                        if idx then
                            if idx < 0 then idx = nv + 1 + idx end
                            if idx >= 1 and idx <= nv then
                                nf = nf + 1
                                face_idx[nf] = idx
                            end
                        end
                    end
                    if nf >= 3 then
                        local a = face_idx[1]
                        for k = 2, nf - 1 do
                            nt = nt + 1
                            if flip_z then
                                i0[nt], i1[nt], i2[nt] = a, face_idx[k + 1], face_idx[k]
                            else
                                i0[nt], i1[nt], i2[nt] = a, face_idx[k], face_idx[k + 1]
                            end
                        end
                    end
                end
            end
        end
    end

    if nv < 3 or nt < 1 then
        OBJ_MESH_CACHE[ckey] = false
        return nil
    end

    local minx, maxx = vx[1], vx[1]
    local miny, maxy = vy[1], vy[1]
    local minz, maxz = vz[1], vz[1]
    for k = 2, nv do
        local x, y, z = vx[k], vy[k], vz[k]
        if x < minx then minx = x elseif x > maxx then maxx = x end
        if y < miny then miny = y elseif y > maxy then maxy = y end
        if z < minz then minz = z elseif z > maxz then maxz = z end
    end

    local out_vx, out_vy, out_vz = vx, vy, vz
    local out_i0, out_i1, out_i2 = i0, i1, i2
    if ok_ffi and ffi ~= nil then
        pcall(function()
            local fvx = ffi.new("float[?]", nv + 1)
            local fvy = ffi.new("float[?]", nv + 1)
            local fvz = ffi.new("float[?]", nv + 1)
            for k = 1, nv do
                fvx[k], fvy[k], fvz[k] = vx[k], vy[k], vz[k]
            end
            local fi0 = ffi.new("int32_t[?]", nt + 1)
            local fi1 = ffi.new("int32_t[?]", nt + 1)
            local fi2 = ffi.new("int32_t[?]", nt + 1)
            for k = 1, nt do
                fi0[k], fi1[k], fi2[k] = i0[k], i1[k], i2[k]
            end
            out_vx, out_vy, out_vz = fvx, fvy, fvz
            out_i0, out_i1, out_i2 = fi0, fi1, fi2
        end)
    end

    local mesh = {
        indexed  = true,
        vx = out_vx, vy = out_vy, vz = out_vz, nv = nv,
        i0 = out_i0, i1 = out_i1, i2 = out_i2, nt = nt,
        bmin = { minx, miny, minz },
        bmax = { maxx, maxy, maxz },
        pose_sig = ckey,
        bridge_slot_key = "obj:" .. ckey,
    }
    OBJ_MESH_CACHE[ckey] = mesh
    return mesh
end

-- 純 Lua GLB / VRM 0.x & 1.0 バイナリ予備パーサ（VRMToolkitBridge 非搭載環境でも .vrm/.glb を直接復元）
local function load_glb_vrm_fallback(raw_path, scale_mul, sx_f, sy_f, sz_f)
    local path = trim_str(raw_path)
    if path == "" then return nil end
    local base_key = path
    local raw_mesh = GLB_MESH_CACHE[base_key]
    if raw_mesh == nil then
        raw_mesh = false
        pcall(function()
            local ok_ffi, ffi = pcall(require, "ffi")
            if not ok_ffi or ffi == nil then return end
            local data = read_binary_file_utf8(path)
            if type(data) ~= "string" or #data < 28 then return end
            local bytes = ffi.cast("const uint8_t*", data)
            local u32 = function(ofs)
                return bytes[ofs] + bytes[ofs + 1] * 256 + bytes[ofs + 2] * 65536 + bytes[ofs + 3] * 16777216
            end
            if u32(0) ~= 0x46546C67 then return end -- "glTF"
            local total_len = math.min(#data, u32(8))
            local pos = 12
            local json_str, bin_ofs, bin_len = nil, 0, 0
            while pos + 8 <= total_len do
                local clen = u32(pos)
                local ctyp = u32(pos + 4)
                if pos + 8 + clen > #data then break end
                if ctyp == 0x4E4F534A then -- JSON
                    json_str = data:sub(pos + 9, pos + 8 + clen)
                elseif ctyp == 0x004E4942 then -- BIN
                    bin_ofs = pos + 8
                    bin_len = clen
                end
                pos = pos + 8 + clen
            end
            if type(json_str) ~= "string" or bin_ofs == 0 or bin_len < 12 then return end

            -- ミニマリスト JSON パーサ（RFC 8259 完全準拠）
            local jp = 1
            local jlen = #json_str
            local function skip_ws()
                local _, e = json_str:find("^[ \t\r\n]+", jp)
                if e then jp = e + 1 end
            end
            local parse_val
            local function parse_str()
                jp = jp + 1 -- skip '"'
                local out = {}
                while jp <= jlen do
                    local c = json_str:byte(jp)
                    if c == 34 then jp = jp + 1; return table.concat(out) end
                    if c == 92 then
                        jp = jp + 1
                        local esc = json_str:byte(jp)
                        if esc == 110 then out[#out + 1] = "\n"
                        elseif esc == 114 then out[#out + 1] = "\r"
                        elseif esc == 116 then out[#out + 1] = "\t"
                        elseif esc == 117 then jp = jp + 4; out[#out + 1] = "?"
                        elseif esc then out[#out + 1] = string.char(esc) end
                        jp = jp + 1
                    else
                        local s0 = jp
                        while jp <= jlen do
                            local cc = json_str:byte(jp)
                            if cc == 34 or cc == 92 then break end
                            jp = jp + 1
                        end
                        out[#out + 1] = json_str:sub(s0, jp - 1)
                    end
                end
                return table.concat(out)
            end
            local function parse_arr()
                jp = jp + 1
                local arr, idx = {}, 0
                skip_ws()
                if json_str:byte(jp) == 93 then jp = jp + 1; return arr end
                while jp <= jlen do
                    skip_ws()
                    idx = idx + 1
                    arr[idx] = parse_val()
                    skip_ws()
                    local c = json_str:byte(jp)
                    if c == 44 then jp = jp + 1
                    elseif c == 93 then jp = jp + 1; break
                    else break end
                end
                return arr
            end
            local function parse_obj()
                jp = jp + 1
                local tbl = {}
                skip_ws()
                if json_str:byte(jp) == 125 then jp = jp + 1; return tbl end
                while jp <= jlen do
                    skip_ws()
                    if json_str:byte(jp) ~= 34 then break end
                    local k = parse_str()
                    skip_ws()
                    if json_str:byte(jp) == 58 then jp = jp + 1 end
                    skip_ws()
                    tbl[k] = parse_val()
                    skip_ws()
                    local c = json_str:byte(jp)
                    if c == 44 then jp = jp + 1
                    elseif c == 125 then jp = jp + 1; break
                    else break end
                end
                return tbl
            end
            parse_val = function()
                skip_ws()
                local c = json_str:byte(jp)
                if c == 34 then return parse_str()
                elseif c == 91 then return parse_arr()
                elseif c == 123 then return parse_obj()
                elseif json_str:sub(jp, jp + 3) == "true" then jp = jp + 4; return true
                elseif json_str:sub(jp, jp + 4) == "false" then jp = jp + 5; return false
                elseif json_str:sub(jp, jp + 3) == "null" then jp = jp + 4; return nil
                else
                    local s, e = json_str:find("^[%+%-]?%d+%.?%d*[eE]?[%+%-]?%d*", jp)
                    if s then
                        local num = tonumber(json_str:sub(s, e))
                        jp = e + 1
                        return num
                    end
                    jp = jp + 1
                    return nil
                end
            end

            local doc = parse_val()
            if type(doc) ~= "table" or type(doc.meshes) ~= "table"
                or type(doc.accessors) ~= "table" or type(doc.bufferViews) ~= "table" then
                return
            end

            local is_vrm0 = (type(doc.extensions) == "table" and doc.extensions.VRM ~= nil and doc.extensions.VRMC_vrm == nil)
            local hand_x = is_vrm0 and -1.0 or 1.0
            local hand_y = -1.0
            local hand_z = is_vrm0 and 1.0 or -1.0

            -- ノード階層 TRS 行列（4x4 列優先ではなく 3x4 アフィン）を計算
            local nodes = doc.nodes or {}
            local has_parent = {}
            for ni = 1, #nodes do
                local ch = nodes[ni] and nodes[ni].children
                if type(ch) == "table" then
                    for ci = 1, #ch do
                        has_parent[(ch[ci] or -1) + 1] = true
                    end
                end
            end
            local function trs_mat(nd)
                if type(nd.matrix) == "table" and #nd.matrix == 16 then
                    local m = nd.matrix
                    return {
                        m[1], m[5], m[9],  m[13],
                        m[2], m[6], m[10], m[14],
                        m[3], m[7], m[11], m[15],
                    }
                end
                local t = nd.translation or { 0, 0, 0 }
                local q = nd.rotation or { 0, 0, 0, 1 }
                local s = nd.scale or { 1, 1, 1 }
                local x, y, z, w = q[1] or 0, q[2] or 0, q[3] or 0, q[4] or 1
                local xx, yy, zz = x * x, y * y, z * z
                local xy, xz, yz = x * y, x * z, y * z
                local wx, wy, wz = w * x, w * y, w * z
                local sx0, sy0, sz0 = s[1] or 1, s[2] or 1, s[3] or 1
                return {
                    (1 - 2 * (yy + zz)) * sx0, 2 * (xy - wz) * sy0,       2 * (xz + wy) * sz0,       t[1] or 0,
                    2 * (xy + wz) * sx0,       (1 - 2 * (xx + zz)) * sy0, 2 * (yz - wx) * sz0,       t[2] or 0,
                    2 * (xz - wy) * sx0,       2 * (yz + wx) * sy0,       (1 - 2 * (xx + yy)) * sz0, t[3] or 0,
                }
            end
            local function mul_m34(A, B)
                return {
                    A[1]*B[1] + A[2]*B[5] + A[3]*B[9],  A[1]*B[2] + A[2]*B[6] + A[3]*B[10], A[1]*B[3] + A[2]*B[7] + A[3]*B[11], A[1]*B[4] + A[2]*B[8] + A[3]*B[12] + A[4],
                    A[5]*B[1] + A[6]*B[5] + A[7]*B[9],  A[5]*B[2] + A[6]*B[6] + A[7]*B[10], A[5]*B[3] + A[6]*B[7] + A[7]*B[11], A[5]*B[4] + A[6]*B[8] + A[7]*B[12] + A[8],
                    A[9]*B[1] + A[10]*B[5] + A[11]*B[9],A[9]*B[2] + A[10]*B[6] + A[11]*B[10],A[9]*B[3] + A[10]*B[7] + A[11]*B[11],A[9]*B[4] + A[10]*B[8] + A[11]*B[12] + A[12],
                }
            end
            local mesh_mats = {}
            local function walk_node(nidx, pmat, depth)
                if depth > 64 then return end
                local nd = nodes[nidx]
                if type(nd) ~= "table" then return end
                local wmat = mul_m34(pmat, trs_mat(nd))
                if type(nd.mesh) == "number" then
                    local mi = nd.mesh + 1
                    mesh_mats[mi] = mesh_mats[mi] or {}
                    mesh_mats[mi][#mesh_mats[mi] + 1] = wmat
                end
                if type(nd.children) == "table" then
                    for ci = 1, #nd.children do
                        walk_node((nd.children[ci] or -1) + 1, wmat, depth + 1)
                    end
                end
            end
            local ident = { 1,0,0,0, 0,1,0,0, 0,0,1,0 }
            for ni = 1, #nodes do
                if not has_parent[ni] then walk_node(ni, ident, 0) end
            end

            local vx, vy, vz = {}, {}, {}
            local i0, i1, i2 = {}, {}, {}
            local nv, nt = 0, 0
            local bin_ptr = bytes + bin_ofs

            for mi = 1, #doc.meshes do
                local mm = doc.meshes[mi]
                local mats = mesh_mats[mi] or { ident }
                local prims = mm and mm.primitives
                if type(prims) == "table" then
                    for pi = 1, #prims do
                        local pr = prims[pi]
                        local mode = (pr.mode == nil) and 4 or pr.mode
                        local p_acc_i = pr.attributes and pr.attributes.POSITION
                        if mode == 4 and type(p_acc_i) == "number" then
                            local p_acc = doc.accessors[p_acc_i + 1]
                            local p_bv  = p_acc and doc.bufferViews[(p_acc.bufferView or -1) + 1]
                            if p_acc and p_bv and p_acc.componentType == 5126 and (p_acc.count or 0) >= 3 then
                                local vcnt = p_acc.count
                                local stride = p_bv.byteStride or 12
                                local p_base = (p_bv.byteOffset or 0) + (p_acc.byteOffset or 0)
                                if type(vcnt) == "number" and vcnt >= 3 and vcnt <= 5000000
                                    and type(stride) == "number" and stride >= 12 and stride <= 4096 and (stride % 4) == 0
                                    and type(p_base) == "number" and p_base >= 0 and p_base + (vcnt - 1) * stride + 12 <= bin_len then
                                    for ti_m = 1, #mats do
                                        local M = mats[ti_m]
                                        local base_v = nv
                                        for k = 0, vcnt - 1 do
                                            local fp = ffi.cast("const float*", bin_ptr + p_base + k * stride)
                                            local px0, py0, pz0 = fp[0], fp[1], fp[2]
                                            local wx0 = M[1]*px0 + M[2]*py0 + M[3]*pz0 + M[4]
                                            local wy0 = M[5]*px0 + M[6]*py0 + M[7]*pz0 + M[8]
                                            local wz0 = M[9]*px0 + M[10]*py0 + M[11]*pz0 + M[12]
                                            nv = nv + 1
                                            vx[nv] = wx0 * hand_x
                                            vy[nv] = wy0 * hand_y
                                            vz[nv] = wz0 * hand_z
                                        end
                                        if type(pr.indices) == "number" then
                                            local i_acc = doc.accessors[pr.indices + 1]
                                            local i_bv  = i_acc and doc.bufferViews[(i_acc.bufferView or -1) + 1]
                                            if i_acc and i_bv then
                                                local icnt = i_acc.count or 0
                                                local ibase = (i_bv.byteOffset or 0) + (i_acc.byteOffset or 0)
                                                local ct = i_acc.componentType
                                                local bpe = (ct == 5121) and 1 or ((ct == 5123) and 2 or 4)
                                                if ibase >= 0 and ibase + icnt * bpe <= bin_len then
                                                    local u16p = ffi.cast("const uint16_t*", bin_ptr + ibase)
                                                    local u32p = ffi.cast("const uint32_t*", bin_ptr + ibase)
                                                    local u8p  = bin_ptr + ibase
                                                    local k = 0
                                                    while k + 2 < icnt do
                                                        local a, b, c
                                                        if bpe == 2 then a, b, c = u16p[k], u16p[k+1], u16p[k+2]
                                                        elseif bpe == 4 then a, b, c = u32p[k], u32p[k+1], u32p[k+2]
                                                        else a, b, c = u8p[k], u8p[k+1], u8p[k+2] end
                                                        if a < vcnt and b < vcnt and c < vcnt then
                                                            nt = nt + 1
                                                            i0[nt], i1[nt], i2[nt] = base_v + a + 1, base_v + b + 1, base_v + c + 1
                                                        end
                                                        k = k + 3
                                                    end
                                                end
                                            end
                                        else
                                            local k = 1
                                            while k + 2 <= vcnt do
                                                nt = nt + 1
                                                i0[nt], i1[nt], i2[nt] = base_v + k, base_v + k + 1, base_v + k + 2
                                                k = k + 3
                                            end
                                        end
                                    end
                                end
                            end
                        end
                    end
                end
            end
            if nv >= 3 and nt >= 1 then
                local fvx = ffi.new("float[?]", nv + 1)
                local fvy = ffi.new("float[?]", nv + 1)
                local fvz = ffi.new("float[?]", nv + 1)
                local fi0 = ffi.new("int32_t[?]", nt + 1)
                local fi1 = ffi.new("int32_t[?]", nt + 1)
                local fi2 = ffi.new("int32_t[?]", nt + 1)
                for k = 1, nv do fvx[k], fvy[k], fvz[k] = vx[k], vy[k], vz[k] end
                for k = 1, nt do fi0[k], fi1[k], fi2[k] = i0[k], i1[k], i2[k] end
                raw_mesh = { indexed = true, vx = fvx, vy = fvy, vz = fvz, nv = nv, i0 = fi0, i1 = fi1, i2 = fi2, nt = nt }
            end
        end)
        if raw_mesh ~= nil then
            _VL_PERSIST.glb_lru = _VL_PERSIST.glb_lru or {}
            local lru = _VL_PERSIST.glb_lru
            if GLB_MESH_CACHE[base_key] == nil then
                lru[#lru + 1] = base_key
                while #lru > 8 do
                    local ev = table.remove(lru, 1)
                    if ev ~= nil and ev ~= base_key then
                        GLB_MESH_CACHE[ev] = nil
                    end
                end
            end
        end
        GLB_MESH_CACHE[base_key] = raw_mesh
    end
    if not raw_mesh then return nil end

    local mx, my, mz = scale_mul * sx_f, scale_mul * sy_f, scale_mul * sz_f
    local scaled = raw_mesh._scaled
    if scaled == nil then
        local ok_sc, ffi_sc = pcall(require, "ffi")
        if not ok_sc or ffi_sc == nil then return nil end
        local nv = raw_mesh.nv
        scaled = {
            indexed = true,
            vx = ffi_sc.new("float[?]", nv + 1),
            vy = ffi_sc.new("float[?]", nv + 1),
            vz = ffi_sc.new("float[?]", nv + 1),
            nv = nv,
            i0 = raw_mesh.i0, i1 = raw_mesh.i1, i2 = raw_mesh.i2, nt = raw_mesh.nt,
            bridge_slot_key = "glb:" .. base_key,
        }
        raw_mesh._scaled = scaled
    end
    if raw_mesh._sx ~= mx or raw_mesh._sy ~= my or raw_mesh._sz ~= mz then
        local nv = raw_mesh.nv
        local rvx, rvy, rvz = raw_mesh.vx, raw_mesh.vy, raw_mesh.vz
        local svx, svy, svz = scaled.vx, scaled.vy, scaled.vz
        for k = 1, nv do
            svx[k] = rvx[k] * mx
            svy[k] = rvy[k] * my
            svz[k] = rvz[k] * mz
        end
        raw_mesh._sx, raw_mesh._sy, raw_mesh._sz = mx, my, mz
        scaled.pose_sig = string.format("%g|%g|%g", mx, my, mz)
    end
    return scaled
end

local VRM_FX_NAMES   = { "VRMToolkitModel", "VRM Toolkit Model", "VRMモデル", "VRM模型", "VRM Model", "VRMモデル表示" }
local VRM_FILE_ITEMS = { "VRMファイル", "VRM文件", "VRM檔案", "VRM档案", "VRM File", "ファイル", "文件", "File" }
local VRM_SCALE_ITEMS= { "モデル倍率", "模型倍率", "模型缩放", "倍率", "Model Scale", "Scale" }
local VRM_MAXT_ITEMS = { "素材毎最大三角形数", "每材质最大三角形数", "最大三角形数", "Max Triangles", "Max Triangles per Material" }
local VRM_FX_ITEMS   = { "X反転", "X反转", "X翻转", "Flip X" }
local VRM_FY_ITEMS   = { "Y反転", "Y反转", "Y翻转", "Flip Y" }
local VRM_FZ_ITEMS   = { "Z反転", "Z反转", "Z翻转", "Flip Z" }
local VRM_ANIM_ITEMS = { "VRMAファイル", "VRMA文件", "VRMA檔案", "VRMA File" }
local VRM_SPD_ITEMS  = { "アニメ速度(%)", "动画速度(%)", "動畫速度(%)", "Animation Speed(%)" }
local VRM_JSON_ITEMS = { "表情JSON", "Expression JSON", "表情 JSON" }
local VRM_BLD_ITEMS  = { "VRMAとJSON", "VRMA与JSON", "VRMA與JSON", "VRMA and JSON" }

local OBJ_FX_NAMES   = {
    "モデルファイル", "模型文件", "3D模型", "3D模型文件", "三维模型", "模型物件", "模型对象",
    "模型檔案", "模型档案", "模型", "Model File", "3D Model", "Modelldatei", "3D-Modell",
    "3Dモデル", "モデルオブジェクト",
}
-- aviutl2.exe の組み込み [モデルファイル] はエフェクト名・ファイル項目名ともに「モデルファイル」（翻訳後も同一名）
local OBJ_FILE_ITEMS = {
    "モデルファイル", "模型文件", "ファイル", "文件", "文件路径", "参照文件", "参考文件", "选择文件", "选取文件", "目标文件",
    "檔案", "档案", "檔案路徑", "参照ファイル",
    "3D模型", "3D模型文件", "三维模型", "模型物件", "模型对象", "模型檔案", "模型档案", "模型",
    "File", "Model File", "3D Model", "File Path", "Reference File", "Modelldatei", "3D-Modell", "Datei", "Dateipfad",
}
local OBJ_ADJ_ITEMS  = {
    "座標系を調整", "调整坐标系", "調整座標系", "坐标系调整", "坐标调整",
    "Adjust Coordinates", "Adjust coordinate system", "Coordinate System",
    "Koordinatensystem anpassen", "Koordinaten anpassen",
}

local WATER_FX_NAMES   = { "水面", "Water Surface", "Water", "Wasseroberfläche" }
local WATER_SP_ITEMS   = { "速度", "Speed", "Geschwindigkeit" }
local WATER_DIR_ITEMS  = { "方向", "Direction", "Richtung" }
local WATER_RIP_ITEMS  = { "波紋", "波纹", "Ripple", "Wellen" }
local WATER_TLT_ITEMS  = { "傾き", "倾角", "倾斜", "傾斜", "Tilt", "Slope", "Neigung" }
local WATER_HW_ITEMS   = { "波紋幅", "波纹宽度", "波紋寬度", "Ripple Width", "Wellenbreite" }
local WATER_CL_ITEMS   = { "波紋位置", "波纹位置", "Ripple Position", "Wellenposition" }
local WATER_HS_ITEMS   = { "波紋速度", "波纹速度", "Ripple Speed", "Wellengeschwindigkeit" }
local WATER_HB_ITEMS   = { "水平境界ぼかし", "水平边界模糊", "水平邊界模糊", "Horizon Blur", "Horizontunschärfe" }
local WATER_RES_ITEMS  = { "解像度", "分辨率", "解析度", "Resolution", "Auflösung" }
local WATER_ADJ_ITEMS  = { "位置補正", "位置修正", "位置补偿", "位置補正値", "Position Offset", "Positionskorrektur" }
local WATER_COL_ITEMS  = { "色", "颜色", "顏色", "Color", "Farbe" }

local SPOT_FX_BASES    = {
    "スポット光源(カメラ制御)", "聚光源(摄像机)", "聚光灯(摄像机)", "聚光源(摄影机)", "聚光灯(摄影机)",
    "聚光源(相機控制)", "聚光燈(相機控制)", "Spot Light (Camera)", "Spotlight (Camera)", "Spotlicht (Kamera)",
}
local POINT_FX_BASES   = {
    "点光源(カメラ制御)", "点光源(摄像机)", "点光源(摄影机)", "點光源(相機控制)",
    "Point Light (Camera)", "Punktlicht (Kamera)",
}
local SPOT_RANGE_KEYS  = { "範囲", "范围", "範圍", "Range", "Angle", "Cone Angle", "Bereich", "Winkel" }
local SPOT_BLUR_KEYS   = { "ぼかし", "模糊", "柔化", "羽化", "Blur", "Softness", "Feather", "Weichzeichnen", "Unschärfe" }
local SPOT_BRIGHT_KEYS = { "明るさ", "发光度", "發光度", "亮度", "Brightness", "Intensity", "Luminosity", "Helligkeit" }
local LIGHT_ATTEN_KEYS = { "減衰", "衰减", "衰減", "Attenuation", "Falloff", "Dämpfung", "Abnahme" }

