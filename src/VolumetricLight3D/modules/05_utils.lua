local function parse_layer_list(spec, skip_a, skip_b)
    local out = {}
    local seen = {}
    if type(spec) ~= "string" or #spec == 0 then return out end
    local norm = spec
        :gsub("，", ",")
        :gsub("、", ",")
        :gsub("；", ",")
        :gsub("　", " ")
        :gsub("～", "-")
        :gsub("~", "-")
        :gsub("ー", "-")
    for tok in norm:gmatch("[0-9]+%s*%-%s*[0-9]+") do
        local a, b = tok:match("([0-9]+)%s*%-%s*([0-9]+)")
        local na, nb = tonumber(a), tonumber(b)
        if na and nb then
            if na > nb then na, nb = nb, na end
            for k = math.max(1, na), math.min(200, nb) do
                if k ~= skip_a and k ~= skip_b and not seen[k] then
                    seen[k] = true
                    out[#out + 1] = k
                end
            end
        end
    end
    local stripped = norm:gsub("[0-9]+%s*%-%s*[0-9]+", " ")
    for num in stripped:gmatch("[0-9]+") do
        local k = tonumber(num)
        if k and k >= 1 and k <= 200 and k ~= skip_a and k ~= skip_b and not seen[k] then
            seen[k] = true
            out[#out + 1] = k
        end
    end
    return out
end

local function is_volume_effect_layer(oln)
    local hit = false
    pcall(function()
        local lp = "layer" .. tostring(oln) .. "."
        if obj.getvalue(oln, "体積光（3D）", "強さ") ~= nil
            or obj.getvalue(oln, "体积光（3D）", "发光强度") ~= nil
            or obj.getvalue(oln, "体积光（3D）", "强度") ~= nil
            or obj.getvalue(oln, "Volumetric Light (3D)", "Intensity") ~= nil
            or obj.getvalue(oln, "Volumenlicht (3D)", "Intensität") ~= nil
            or obj.getvalue(lp .. "体積光（3D）.強さ") ~= nil
            or obj.getvalue(lp .. "体积光（3D）.发光强度") ~= nil
            or obj.getvalue(oln, "VolumetricFog3D", "濃さ") ~= nil
            or obj.getvalue(oln, "体積霧（3D）", "濃さ") ~= nil
            or obj.getvalue(oln, "体積霧（3D）", "霧の濃さ") ~= nil
            or obj.getvalue(oln, "体积雾（3D）", "雾的浓度") ~= nil
            or obj.getvalue(oln, "Volumetric Fog (3D)", "Density") ~= nil
            or obj.getvalue(oln, "Volumennebel (3D)", "Nebeldichte") ~= nil
            or obj.getvalue(oln, "Volumennebel (3D)", "Dichte") ~= nil
            or obj.getvalue(lp .. "VolumetricFog3D.濃さ") ~= nil
            or obj.getvalue(lp .. "体積霧（3D）.濃さ") ~= nil
            or obj.getvalue(lp .. "体积雾（3D）.雾的浓度") ~= nil
            or obj.getvalue(oln, "体積霧（3D）オブジェクト", "濃さ") ~= nil
            or obj.getvalue(oln, "体積霧(3D)オブジェクト", "濃さ") ~= nil
            or obj.getvalue(oln, "体积雾（3D）对象", "浓度") ~= nil
            or obj.getvalue(oln, "雪（3D）", "粒子数") ~= nil
            or obj.getvalue(oln, "3D雪", "粒子数") ~= nil
            or obj.getvalue(oln, "Snow (3D)", "Particles") ~= nil
            or obj.getvalue(oln, "3D Snow", "Particle Count") ~= nil
            or obj.getvalue(oln, "Schnee (3D)", "Partikelanzahl") ~= nil
            or obj.getvalue(oln, "3D-Schnee", "Partikelanzahl") ~= nil
            or obj.getvalue(lp .. "雪（3D）.粒子数") ~= nil
            or obj.getvalue(oln, "カメラ制御", "目標レイヤー") ~= nil
            or obj.getvalue(oln, "摄像机控制", "目标图层") ~= nil
            or obj.getvalue(oln, "Camera Control", "Target Layer") ~= nil
            or obj.getvalue(oln, "Kamerasteuerung", "Zielebene") ~= nil then
            hit = true
        end
    end)
    return hit
end

local function rot_xyz(vx, vy, vz, rx_deg, ry_deg, rz_deg)
    local rad = math.pi / 180.0
    local cx, sx = math.cos(rx_deg * rad), math.sin(rx_deg * rad)
    local cy, sy = math.cos(ry_deg * rad), math.sin(ry_deg * rad)
    local cz, sz = math.cos(rz_deg * rad), math.sin(rz_deg * rad)
    local y1, z1 = vy * cx - vz * sx, vy * sx + vz * cx
    local x2, z2 = vx * cy + z1 * sy, -vx * sy + z1 * cy
    local x3, y3 = x2 * cz - y1 * sz, x2 * sz + y1 * cz
    return x3, y3, z2
end

local function inv_rot_xyz(wx, wy, wz, rx_deg, ry_deg, rz_deg)
    if math.abs(rx_deg) + math.abs(ry_deg) + math.abs(rz_deg) < 1e-5 then
        return wx, wy, wz
    end
    local rad = math.pi / 180.0
    local cx, sx = math.cos(rx_deg * rad), math.sin(rx_deg * rad)
    local cy, sy = math.cos(ry_deg * rad), math.sin(ry_deg * rad)
    local cz, sz = math.cos(rz_deg * rad), math.sin(rz_deg * rad)
    local x2 = wx * cz + wy * sz
    local y1 = -wx * sz + wy * cz
    local vx = x2 * cy - wz * sy
    local z1 = x2 * sy + wz * cy
    local vy = y1 * cx + z1 * sx
    local vz = -y1 * sx + z1 * cx
    return vx, vy, vz
end

local function read_layer_transform(ln, def_x, def_y, def_z, def_rx, def_ry, def_rz)
    local lx, ly, lz = def_x, def_y, def_z
    local lrx, lry, lrz = def_rx, def_ry, def_rz
    local zx, zy, zz = 1.0, 1.0, 1.0
    local cx, cy, cz = 0.0, 0.0, 0.0
    local la = 1.0
    pcall(function()
        local vx = obj.getvalue("layer" .. ln .. ".x")
        local vy = obj.getvalue("layer" .. ln .. ".y")
        local vz = obj.getvalue("layer" .. ln .. ".z")
        if type(vx) == "number" then lx = vx end
        if type(vy) == "number" then ly = vy end
        if type(vz) == "number" then lz = vz end
        local rx1 = obj.getvalue("layer" .. ln .. ".rx")
        local ry1 = obj.getvalue("layer" .. ln .. ".ry")
        local rz1 = obj.getvalue("layer" .. ln .. ".rz")
        if type(rx1) == "number" then lrx = rx1 end
        if type(ry1) == "number" then lry = ry1 end
        if type(rz1) == "number" then lrz = rz1 end
        local sx1 = obj.getvalue("layer" .. ln .. ".sx")
        local sy1 = obj.getvalue("layer" .. ln .. ".sy")
        local sz1 = obj.getvalue("layer" .. ln .. ".sz")
        if type(sx1) == "number" and type(sy1) == "number" then
            zx, zy = math.abs(sx1), math.abs(sy1)
            if type(sz1) == "number" then zz = math.abs(sz1) end
        else
            local lzoom = obj.getvalue("layer" .. ln .. ".zoom")
            local lasp  = obj.getvalue("layer" .. ln .. ".aspect")
            if type(lzoom) == "number" and lzoom > 0.01 then
                local zs = lzoom / 100.0
                local ap = type(lasp) == "number" and lasp or 0.0
                zx = zs * (ap > 0 and math.max(0.01, 1.0 - ap / 100.0) or 1.0)
                zy = zs * (ap < 0 and math.max(0.01, 1.0 + ap / 100.0) or 1.0)
            end
        end
        local cx1 = obj.getvalue("layer" .. ln .. ".cx")
        local cy1 = obj.getvalue("layer" .. ln .. ".cy")
        local cz1 = obj.getvalue("layer" .. ln .. ".cz")
        if type(cx1) == "number" then cx = cx1 end
        if type(cy1) == "number" then cy = cy1 end
        if type(cz1) == "number" then cz = cz1 end
        local al1 = obj.getvalue("layer" .. ln .. ".alpha")
        if type(al1) == "number" then la = math.max(0.0, math.min(1.0, al1)) end
    end)
    local Rr_x, Rr_y, Rr_z = rot_xyz(1, 0, 0, lrx, lry, lrz)
    local Rd_x, Rd_y, Rd_z = rot_xyz(0, 1, 0, lrx, lry, lrz)
    local Rn_x, Rn_y, Rn_z = rot_xyz(0, 0, 1, lrx, lry, lrz)
    lx = lx - Rr_x * (cx * zx) - Rd_x * (cy * zy) - Rn_x * (cz * zz)
    ly = ly - Rr_y * (cx * zx) - Rd_y * (cy * zy) - Rn_y * (cz * zz)
    lz = lz - Rr_z * (cx * zx) - Rd_z * (cy * zy) - Rn_z * (cz * zz)
    return lx, ly, lz, lrx, lry, lrz, zx, zy, zz, la, Rr_x, Rr_y, Rr_z, Rd_x, Rd_y, Rd_z, Rn_x, Rn_y, Rn_z
end

---------------------------------------------------------------------
-- 3D モデル（Wavefront .obj / VRMToolkitModel / GLB・VRM バイナリ）ローダ＆
-- GPU ハードウェア比較(明)ブレンド 3D シャドウ／カメラ深度ラスタライザ
---------------------------------------------------------------------
local OBJ_MESH_CACHE = _VL_PERSIST.obj_mesh_cache
local GLB_MESH_CACHE = _VL_PERSIST.glb_mesh_cache
local VRM_POSE_CACHE = _VL_PERSIST.vrm_pose_cache

local function as_bool(v)
    return v == true or (type(v) == "number" and v ~= 0) or v == "true" or v == "1"
end

local function trim_str(s)
    if type(s) ~= "string" then return "" end
    local t = s:match("^%s*(.-)%s*$") or ""
    if #t >= 2 and ((t:sub(1, 1) == '"' and t:sub(-1) == '"') or (t:sub(1, 1) == "'" and t:sub(-1) == "'")) then
        t = t:sub(2, -2)
    end
    return t
end

local KNOWN_SEARCH_DIRS = _VL_PERSIST.search_dirs
local function remember_dir_of_file(fpath)
    if type(fpath) ~= "string" then return end
    local dir = fpath:match("^(.+)[\\/][^\\/]+$")
    if type(dir) == "string" and #dir > 1 then
        for i = 1, #KNOWN_SEARCH_DIRS do
            if KNOWN_SEARCH_DIRS[i] == dir then return end
        end
        KNOWN_SEARCH_DIRS[#KNOWN_SEARCH_DIRS + 1] = dir
    end
end

local function get_fx_val(oln, fx_names, item_names, try_same_name)
    for fi = 1, #fx_names do
        local fx = fx_names[fi]
        if try_same_name then
            local ok_s, vs = pcall(obj.getvalue, oln, fx, fx)
            if ok_s and vs ~= nil then
                return vs, fx
            end
        end
        for ii = 1, #item_names do
            local ok, v = pcall(obj.getvalue, oln, fx, item_names[ii])
            if ok and v ~= nil then
                return v, fx
            end
        end
    end
    return nil, nil
end

local win32_read_file_wide_from_utf8

read_binary_file_utf8 = function(raw_path)
    local path = trim_str(raw_path)
    if path == "" then return nil end

    local try_paths = { path }
    if not path:match("^[A-Za-z]:[\\/]") and not path:match("^[\\/]") then
        for di = 1, #KNOWN_SEARCH_DIRS do
            local d = KNOWN_SEARCH_DIRS[di]
            local sep = d:find("/") and "/" or "\\"
            if d:sub(-1) ~= "\\" and d:sub(-1) ~= "/" then d = d .. sep end
            try_paths[#try_paths + 1] = d .. path
        end
    else
        remember_dir_of_file(path)
    end

    for pi = 1, #try_paths do
        local p = try_paths[pi]
        if type(io) == "table" and type(io.open) == "function" then
            local fh = io.open(p, "rb")
            if fh ~= nil then
                local data = fh:read("*a")
                fh:close()
                if type(data) == "string" and #data > 0 then
                    return data
                end
            end
        end

        -- 共有 LuaJIT VM (Rusty Scripts Search Plugin 等が ffi.C に別シグネチャ定義済みでも 100% 無衝突！)
        -- GetProcAddress + 無名関数ポインタ ffi.typeof(...) + 純 Lua UTF-8⇔UTF-16LE + _wfopen 二重読込
        local got = win32_read_file_wide_from_utf8(p)
        if type(got) == "string" and #got > 0 then
            return got
        end
    end
    return nil
end

