local function cached_typeof(ffi, cdecl)
    local ct = _VL_PERSIST.ctypes[cdecl]
    if ct == nil then
        ct = ffi.typeof(cdecl)
        _VL_PERSIST.ctypes[cdecl] = ct
    end
    return ct
end

local function classify_ui_lang(val)
    if type(val) ~= "string" then return "ja" end
    local s = val:match("^%s*(.-)%s*$") or ""
    if s == "" then return "ja" end
    local low = s:lower()
    if s:find("中", 1, true) or s:find("简", 1, true) or s:find("簡", 1, true) or s:find("繁", 1, true)
        or low:find("chinese", 1, true) or low == "zh" or low == "cn" or low == "chs" or low == "cht"
        or low:find("^zh[_%-%. ]") or low:find("[_%-%. ]zh$") or low:find("[_%-%. ]cn$") then
        return "zh"
    end
    if s:find("德", 1, true) or s:find("独", 1, true)
        or low:find("deutsch", 1, true) or low:find("german", 1, true)
        or low == "de" or low == "deu" or low == "ger"
        or low:find("^de[_%-%. ]") or low:find("[_%-%. ]de$")
        or low:find("[_%-%. ]de[_%-%. ]") then
        return "de"
    end
    if low == "default" or s:find("日本", 1, true) or low:find("japanese", 1, true) or low:find("japan", 1, true)
        or low == "ja" or low == "jp" or low == "jpn"
        or low:find("^ja[_%-%. ]") or low:find("[_%-%. ]ja$") or low:find("[_%-%. ]jp$") then
        return "ja"
    end
    return "en"
end

local read_binary_file_utf8
local get_env_utf8
local vl_wall_time_sec
local vl3d_bridge

local function detect_ui_lang()
    local now_c = (type(vl_wall_time_sec) == "function") and vl_wall_time_sec() or 0.0
    if now_c == 0.0 then
        pcall(function()
            local c = obj.getinfo("clock")
            if type(c) == "number" then now_c = c end
        end)
    end
    if _VL_PERSIST.cached_lang ~= nil and (now_c - _VL_PERSIST.cached_lang_t) < 2.0 then
        return _VL_PERSIST.cached_lang
    end
    _VL_PERSIST.cached_lang_t = now_c

    local lang = "ja"
    pcall(function()
        local has_io = (type(io) == "table" and type(io.open) == "function")
        if (not has_io) and type(read_binary_file_utf8) ~= "function" then return end
        local paths = {}
        local sp = nil
        pcall(function() sp = obj.getinfo("script_path") end)
        if type(sp) == "string" and #sp > 0 then
            local sep = sp:sub(-1)
            if sep ~= "\\" and sep ~= "/" then sp = sp .. "/" end
            paths[#paths + 1] = sp .. "../aviutl2.ini"
            paths[#paths + 1] = sp .. "../../aviutl2.ini"
            paths[#paths + 1] = sp .. "..\\aviutl2.ini"
            paths[#paths + 1] = sp .. "..\\..\\aviutl2.ini"
        end
        local getenv_fn = (type(get_env_utf8) == "function") and get_env_utf8
            or ((type(os) == "table" and type(os.getenv) == "function") and os.getenv or nil)
        local pdata = getenv_fn and getenv_fn("ProgramData") or nil
        if type(pdata) == "string" and #pdata > 0 then
            paths[#paths + 1] = pdata .. "\\aviutl2\\aviutl2.ini"
        end
        local appdata = getenv_fn and getenv_fn("APPDATA") or nil
        if type(appdata) == "string" and #appdata > 0 then
            paths[#paths + 1] = appdata .. "\\aviutl2\\aviutl2.ini"
        end
        local lappdata = getenv_fn and getenv_fn("LOCALAPPDATA") or nil
        if type(lappdata) == "string" and #lappdata > 0 then
            paths[#paths + 1] = lappdata .. "\\aviutl2\\aviutl2.ini"
        end
        paths[#paths + 1] = "C:\\ProgramData\\aviutl2\\aviutl2.ini"

        for i = 1, #paths do
            local content = nil
            if has_io then
                local fh = io.open(paths[i], "rb")
                if fh ~= nil then
                    content = fh:read("*a")
                    fh:close()
                end
            elseif type(read_binary_file_utf8) == "function" then
                content = read_binary_file_utf8(paths[i])
            end
            if type(content) == "string" and #content > 0 then
                for line in content:gmatch("[^\r\n]+") do
                    local val = line:match("^%s*[Ll]anguage%s*=%s*(.-)%s*$")
                    if val ~= nil then
                        lang = classify_ui_lang(val)
                        break
                    end
                end
                break
            end
        end
    end)

    _VL_PERSIST.cached_lang = lang
    return lang
end

local I18N = {
    unselected = {
        ja = "光源レイヤーが未選択です ^_^",
        zh = "光源图层未选定 ^_^",
        en = "Source layer not selected ^_^",
        de = "Quellebene nicht ausgewählt ^_^",
    },
    self_layer = {
        ja = "自身のレイヤーは光源に指定できません ^_^",
        zh = "光源图层不能选自身图层哦 ^_^",
        en = "Cannot select this layer itself ^_^",
        de = "Die eigene Ebene kann nicht als Quelle gewählt werden ^_^",
    },
    empty_layer = {
        ja = "光源レイヤー #%d に画像がありません ^_^",
        zh = "光源图层 #%d 无图像 ^_^",
        en = "Source layer #%d has no image ^_^",
        de = "Quellebene #%d enthält kein Bild ^_^",
    },
    occ_unselected = {
        ja = "「指定レイヤー」に遮蔽レイヤー番号を入力してください（例: 4 5 または 2,4-6） ^_^",
        zh = "请在「指定图层」填入前景图层编号（支持多个，如 4 5 或 2,4-6） ^_^",
        en = "Enter layer numbers in Occluder Layers (e.g. 4 5 or 2,4-6) ^_^",
        de = "Bitte Ebenennummern unter „Abschattungsebenen“ eingeben (z. B. 4 5 oder 2,4-6) ^_^",
    },
    src_desc = {
        ja = "レイヤー%d(%dx%d)",
        zh = "图层%d(%dx%d)",
        en = "Layer%d(%dx%d)",
        de = "Ebene%d(%dx%d)",
    },
    no_cam_light = {
        ja = "カメラ制御に3D光源（点光源・平行光源等）が見つかりません ^_^",
        zh = "未在「摄像机控制」中检测到点光源/聚光灯/平行光 ^_^",
        en = "No 3D light found on Camera Control ^_^",
        de = "Keine 3D-Lichtquelle in der Kamerasteuerung gefunden ^_^",
    },
    layer_order_tip = {
        ja = "\n※ヒント: 前景光条を表示するには本レイヤーをレイヤー%dより下段へ配置してください",
        zh = "\n※提示: 若需光束穿过前景镂空射向镜头，请将本图层移至图层%d下方",
        en = "\n*Tip: Place this layer below Layer %d so front rays are not painted over",
        de = "\n*Tipp: Diese Ebene unter Ebene %d legen, damit Vordergrundstrahlen sichtbar bleiben",
    },
    hud = {
        ja = "[体積光 3D v3.15] GPU=%s(%dx%dx%dpass) | スライス:%d(dz:%.1f) 遮蔽:%s キャッシュ頂点:%.2fM/14.0M\n光源:レイヤー%d(%dx%d) 座標:(%.0f, %.0f, %.0f) 有効UV:(%.2f..%.2f, %.2f..%.2f) 固有充填:%.0f%%\n長さ:%.0f(最大%.0f) 頂点:%d 最大画素RGBA:(%d,%d,%d,%d)%s",
        zh = "[体积光 3D v3.15] GPU=%s(%dx%dx%d趟) | 切片:%d(dz:%.1f) 遮挡:%s 峰值缓存:%.2fM/14.0M\n光源:图层%d(%dx%d) 坐标:(%.0f, %.0f, %.0f) 有效UV:(%.2f..%.2f, %.2f..%.2f) 固有填充:%.0f%%\n长度:%.0f(全长%.0f) 顶点:%d 峰值RGBA:(%d,%d,%d,%d)%s",
        en = "[Volumetric Light 3D v3.15] GPU=%s(%dx%dx%dpass) | Slices:%d(dz:%.1f) Occ:%s CachePeak:%.2fM/14.0M\nSource:Layer%d(%dx%d) Pos:(%.0f, %.0f, %.0f) ActiveUV:(%.2f..%.2f, %.2f..%.2f) Fill:%.0f%%\nLength:%.0f(Max%.0f) Verts:%d PeakRGBA:(%d,%d,%d,%d)%s",
        de = "[Volumenlicht 3D v3.15] GPU=%s(%dx%dx%d-Pass) | Scheiben:%d(dz:%.1f) Abschat.:%s CacheSpitze:%.2fM/14.0M\nQuelle:Ebene%d(%dx%d) Pos:(%.0f, %.0f, %.0f) AktivUV:(%.2f..%.2f, %.2f..%.2f) Füllung:%.0f%%\nLänge:%.0f(Max%.0f) Verts:%d SpitzenRGBA:(%d,%d,%d,%d)%s",
    },
    log_info = {
        ja = "体積光（3D）v3.15 GPU=%s 光源=%s 遮蔽=%s アトラス=%dx%dx%d スライス=%d(dz=%.1f) 頂点=%d 固有充填=%.2f キャッシュ頂点=%.2fM",
        zh = "体积光（3D）v3.15 GPU=%s 光源=%s 遮挡=%s 图集=%dx%dx%d 切片=%d(dz=%.1f) 顶点=%d 固有填充=%.2f 峰值缓存=%.2fM",
        en = "Volumetric Light (3D) v3.15 GPU=%s Src=%s Occ=%s Atlas=%dx%dx%d Slices=%d(dz=%.1f) Verts=%d Fill=%.2f CachePeak=%.2fM",
        de = "Volumenlicht (3D) v3.15 GPU=%s Quelle=%s Abschat.=%s Atlas=%dx%dx%d Scheiben=%d(dz=%.1f) Verts=%d Füllung=%.2f CacheSpitze=%.2fM",
    },
    shader_err = {
        ja = "体積光（3D）v3.15 シェーダー失敗: ",
        zh = "体积光（3D）v3.15 着色器执行失败: ",
        en = "Volumetric Light (3D) v3.15 shader error: ",
        de = "Volumenlicht (3D) v3.15 Shader-Fehler: ",
    },
    init_start = {
        ja = "体積光（3D）v3.15：初期化中……",
        zh = "体积光（3D）v3.15：初始化中……",
        en = "Volumetric Light (3D) v3.15: Initializing...",
        de = "Volumenlicht (3D) v3.15: Initialisierung läuft...",
    },
    init_done = {
        ja = "体積光（3D）v3.15：初期化完了（%.2f秒）",
        zh = "体积光（3D）v3.15：初始化完成（耗时 %.2f 秒）",
        en = "Volumetric Light (3D) v3.15: Initialized (%.2fs)",
        de = "Volumenlicht (3D) v3.15: Initialisiert (%.2fs)",
    },
}

