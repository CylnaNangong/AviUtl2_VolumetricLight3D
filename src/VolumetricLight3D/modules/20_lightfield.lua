---------------------------------------------------------------------
-- 20. 光場の公開（VL3D Light Field v1）
---------------------------------------------------------------------
-- 同シリーズの 3D スクリプト（雪（3D）/ 体積霧（3D）など）が
-- 「粒子が体積光束に照らされる」表現を作れるよう、このフレームの
-- 3D 光束を **公式の共有テーブル `global`**（docs.aviutl2.jp/lua § 変数
-- 「スクリプト間で共有されるテーブル変数」）へ公開する。
--
-- 設計方針:
--   * 追加ファイル無し / 追加プラグイン無し / 追加パラメータ無し。
--     公式 Lua API だけで完結するので VL3DBridge.aux2 無しでも動く。
--     （`.mod2` は公式 SDK 上は「ネイティブ DLL モジュール」なので
--       Lua の共有ライブラリ代わりには使えない。`obj.data()` は
--       「スクリプト → DLL」用で、スクリプト間の共有には使えない。）
--   * 転送量は 1 光束あたり 189 バイト。書き込みも毎フレーム 1 回だけ。
--   * 受信側（雪（3D）など）は解析的な円錐／矩形の内判定だけで
--     粒子 1 個あたり ~30 演算。GPU 読み戻しも追加シェーダも不要。
--   * 破損・旧バージョン・別スクリプトの値は magic/version で弾く。
--   * `global` が無いホストでも pcall で無害に無効化する。
--
-- フォーマット（リトルエンディアン／オフセットは 1 起点）:
--   [1..5]   "VL3LF"
--   [6]      u8  version
--   [7]      u8  flags  (bit0=光源画像の色 / bit1=前景遮蔽あり / bit2=カメラ有効)
--   [8..11]  u32 scene_frame
--   [12..15] u32 scene_id
--   [16..19] f32 time (obj.time)
--   [20..21] u16 layer      (この光束を描いているレイヤー)
--   [22..23] u16 src_layer  (光源レイヤー)
--   [24..83] f32 cam[15]    (cx,cy,cz, fx,fy,fz, rx,ry,rz, dx,dy,dz,
--                            focal, scr_w, scr_h)
--   [84]     u8  kind       (0=平行 / 1=apex から放射 / 2=apex へ収束)
--   [85]     u8  pad
--   [86..189] f32 beam[26]  (W(3), axis(3), right(3), down(3), apex(3),
--                            r0, Hx, Hy, L, kdec,
--                            col_r, col_g, col_b, lum, power, coverage)
--
-- v2 拡張（遮蔽・多色光源対応／189 バイト以降に追記。v1 の並びは不変）:
--   [190]    u8  colgrid_dim  光源色グリッドの一辺（0 = 無し, 既定 12）
--   [191]    u8  occgrid_w    遮蔽グリッドの幅（0 = 無し, 既定 32）
--   [192]    u8  occgrid_h    遮蔽グリッドの高さ（既定 18, 画面 16:9 相当）
--   [193]    u8  pad
--   [194..197] f32 occ_zmax   遮蔽グリッド深度の正規化上限（カメラ深度）
--   [198..]  色グリッド     colgrid_dim^2 * RGB8（光源面 UV の 0..1 に正規化）
--   ...      遮蔽グリッド   occgrid_w * occgrid_h * RGBA8
--                            R=2D 前景シルエット被覆率(occ_strength 適用済)
--                            G=3D オブジェクト被覆率 / B=3D 表面深度(255=無し)
--                            A=2D 遮蔽平面深度(255=無し)   B/A は 0..255 を
--                            occ_zmax でスケールしたカメラ深度
--   flags bit3 = 色グリッドあり / bit4 = 遮蔽グリッドあり
---------------------------------------------------------------------
local VL_LF_MAGIC       = "VL3LF"
local VL_LF_VERSION     = 2      -- ペイロード版（インデックスは VL3LF1 のまま）
local VL_LF_IDX_VER     = 1
local VL_LF_IDX_KEY     = "VL3D_LF_IDX"
local VL_LF_SLOT_PREFIX = "VL3D_LF:"
local VL_LF_STALE       = 2      -- 受信側が無視する古さ（フレーム）
local VL_LF_COLOR_EVERY = 3      -- 光源色の読み戻し間隔（フレーム）
local VL_LF_CGRID       = 12     -- 光源色グリッドの一辺
local VL_LF_OGRID_W     = 32     -- 遮蔽グリッドの幅
local VL_LF_OGRID_H     = 18     -- 遮蔽グリッドの高さ
local VL_LF_OCC_ZMAX    = 4096.0 -- 遮蔽グリッド深度の正規化上限
local VL_LF_GRID_EVERY  = 2      -- 遮蔽グリッドの再構築間隔（フレーム）

-- f32 / u16 / u32 パッカー（ffi が無い環境では純 Lua の IEEE754 変換へ退避）
local _LF_FFI_OK, _LF_FFI = pcall(require, "ffi")
if type(_LF_FFI) ~= "table" then _LF_FFI_OK = false end
if _G.VL3D_TEST_LF_FORCE_PURE_PACK then _LF_FFI_OK = false end
local _LF_F32_BUF = _LF_FFI_OK and _LF_FFI.new("float[1]") or nil

local function lf_pure_f32(v)
    -- 純 Lua による IEEE754 binary32 エンコード（リトルエンディアン）
    if v ~= v then return string.char(0, 0, 192, 127) end
    local sign = 0
    if v < 0 then sign = 128; v = -v end
    if v == 0 then return string.char(0, 0, 0, sign) end
    if v == math.huge then return string.char(0, 0, 128, sign + 127) end
    local m, e = math.frexp(v)              -- v = m * 2^e, 0.5 <= m < 1
    e = e - 1
    local mant
    if e < -126 then
        -- 非正規化数（1e-38 未満）。実用上ほぼ 0 なので丸め優先。
        mant = math.floor(v / 1.401298464324817e-45 + 0.5)
        if mant >= 8388608 then
            return string.char(0, 0, 128, sign + 127)
        end
        e = -127
    else
        mant = math.floor((m * 2.0 - 1.0) * 8388608.0 + 0.5)
        if mant >= 8388608 then mant, e = 0, e + 1 end
    end
    local efield = (e >= -126) and (e + 127) or 0
    if efield >= 255 then return string.char(0, 0, 128, sign + 127) end
    local b1 = mant % 256
    local b2 = math.floor(mant / 256) % 256
    local b3 = math.floor(mant / 65536) % 128 + (efield % 8) * 128
    local b4 = math.floor(efield / 8) + sign
    return string.char(b1, b2, b3, b4)
end

local function lf_f32(v)
    if type(v) ~= "number" or v ~= v then v = 0.0 end
    if _LF_FFI_OK then
        _LF_F32_BUF[0] = v
        return _LF_FFI.string(_LF_F32_BUF, 4)
    end
    return lf_pure_f32(v)
end

local function lf_u32(u)
    u = math.floor((type(u) == "number" and u) or 0) % 4294967296
    return string.char(u % 256, math.floor(u / 256) % 256,
                       math.floor(u / 65536) % 256, math.floor(u / 16777216) % 256)
end

local function lf_u16(u)
    u = math.floor((type(u) == "number" and u) or 0) % 65536
    return string.char(u % 256, math.floor(u / 256) % 256)
end

local function lf_read_u32(s, i)
    local b1, b2, b3, b4 = s:byte(i, i + 3)
    if not b4 then return nil end
    return b1 + b2 * 256 + b3 * 65536 + b4 * 16777216
end

-- ---------------------------------------------------------------
-- `global`（スクリプト間共有テーブル）への安全なアクセス
-- ---------------------------------------------------------------
local function lf_global()
    if _G.VL3D_TEST_LF_NO_GLOBAL then return nil end
    local ok, tbl = pcall(function() return global end)
    if ok and type(tbl) == "table" then return tbl end
    return nil
end

-- ---------------------------------------------------------------
-- 光源の代表色
--   「光の色」指定時 / カメラ光源追従時はその色を使い、それ以外は
--   cache:vl3d/mip（＝しきい値適用後の光源色）を間引いて平均する。
--   VRAM 読み戻しは VL_LF_COLOR_EVERY フレームに 1 回だけに抑え、
--   結果はシークで破綻しないようシーンフレームでキャッシュする。
-- ---------------------------------------------------------------
local function lf_source_color(scene_frame)
    if type(P.light_color) == "number" then
        return math.floor(P.light_color / 0x10000) % 0x100 / 255.0,
               math.floor(P.light_color / 0x100) % 0x100 / 255.0,
               (P.light_color % 0x100) / 255.0, 1.0, 0, nil
    end
    if VL_BF.use_source == 0.0 then
        return VL_BF.lc_r or 1.0, VL_BF.lc_g or 1.0, VL_BF.lc_b or 1.0, 1.0, 0, nil
    end
    local cache = _VL_PERSIST.lf_color
    if type(cache) == "table" and type(cache.lum) == "number" then
        if cache.frame == scene_frame then
            return cache.r, cache.g, cache.b, cache.lum, 1, cache.grid
        end
        if type(scene_frame) == "number" and type(cache.frame) == "number"
            and scene_frame >= cache.frame and (scene_frame - cache.frame) < VL_LF_COLOR_EVERY then
            return cache.r, cache.g, cache.b, cache.lum, 1, cache.grid
        end
    end
    local r, g, b, lum = VL_BF.lc_r or 1.0, VL_BF.lc_g or 1.0, VL_BF.lc_b or 1.0, 1.0
    local grid = nil
    local err = nil
    if VL_BF.mip_ready and _LF_FFI_OK then
        local okc
        okc, err = pcall(function()
            local ptr, mw, mh = obj.getpixeldata("cache:vl3d/mip", "rgba")
            if ptr == nil or type(mw) ~= "number" or type(mh) ~= "number" then
                _G.__VL_LF_EARLY = string.format("ptr=%s w=%s h=%s", tostring(ptr), tostring(mw), tostring(mh))
                return
            end
            if mw < 1 or mh < 1 then _G.__VL_LF_EARLY = "size" return end
            local px = _LF_FFI.cast("uint8_t*", ptr)
            local step_x = math.max(1, math.floor(mw / 24))
            local step_y = math.max(1, math.floor(mh / 24))
            local sr, sg, sb, sa, n = 0.0, 0.0, 0.0, 0.0, 0
            for y = 0, mh - 1, step_y do
                local row = y * mw
                for x = 0, mw - 1, step_x do
                    local idx = (row + x) * 4
                    local rr, gg, bb, aa = px[idx], px[idx + 1], px[idx + 2], px[idx + 3]
                    if aa > 4 then
                        sr = sr + rr * aa
                        sg = sg + gg * aa
                        sb = sb + bb * aa
                        sa = sa + aa
                        n = n + 1
                    end
                end
            end
            if n < 1 or sa < 1.0 then
                _G.__VL_LF_EARLY = string.format("n=%d sa=%.1f mw=%d mh=%d a0=%d", n, sa, mw, mh, px[3])
                return
            end
            -- 乗算済み平均色 → ストレート色（ゲインは受信側の強さで調整）
            -- 実測の平均色をそのまま公開（暗い光源は暗いまま＝物理的に正しい）。
            -- 受信側は「受光の強さ」で持ち上げられる。
            local ir, ig, ib = math.min(1.0, sr / sa), math.min(1.0, sg / sa), math.min(1.0, sb / sa)
            r, g, b = ir, ig, ib
            lum = math.max(0.0, math.max(ir, math.max(ig, ib)))

            -- ---- 光源色グリッド（光束色を粒子ごとに変える = ステンドグラス対応）----
            -- しきい値で切り詰めた発光矩形 (u0..u1, v0..v1) を G x G に分割し、
            -- セル中央の色を mip から拾う。受信側は粒子を光源面へ射影した UV で
            -- これをバイリニア参照するので、赤い窓の前の雪は赤く、青い窓の前の
            -- 雪は青く光る。
            local gu0, gu1 = VL_BF.u0_s or 0.0, VL_BF.u1_s or 1.0
            local gv0, gv1 = VL_BF.v0_s or 0.0, VL_BF.v1_s or 1.0
            if not (gu1 > gu0) then gu0, gu1 = 0.0, 1.0 end
            if not (gv1 > gv0) then gv0, gv1 = 0.0, 1.0 end
            local G = VL_LF_CGRID
            local cells = {}
            for gy = 0, G - 1 do
                local vv = gv0 + (gy + 0.5) / G * (gv1 - gv0)
                local my = math.floor(vv * mh)
                if my < 0 then my = 0 elseif my >= mh then my = mh - 1 end
                local row = my * mw
                for gx = 0, G - 1 do
                    local uu = gu0 + (gx + 0.5) / G * (gu1 - gu0)
                    local mx = math.floor(uu * mw)
                    if mx < 0 then mx = 0 elseif mx >= mw then mx = mw - 1 end
                    local idx = (row + mx) * 4
                    local rr, gg, bb, aa = px[idx], px[idx + 1], px[idx + 2], px[idx + 3]
                    if aa <= 8 then
                        -- 透明（＝そこからは光が出ていない）: 黒 = 受光なし
                        cells[#cells + 1] = "\0\0\0"
                    else
                        cells[#cells + 1] = string.char(rr, gg, bb)
                    end
                end
            end
            grid = table.concat(cells)
        end)
        if not okc then err = tostring(err) end
    end
    _VL_PERSIST.lf_color = { frame = scene_frame, r = r, g = g, b = b, lum = lum,
                             grid = grid, err = err }
    return r, g, b, lum, 1, grid
end

-- ---------------------------------------------------------------
-- 遮蔽グリッド（v2）
--   このスクリプト自身が光束を描くときに使っている遮蔽情報を、そのまま
--   画面 32x18 の粗いグリッドへ落とし込んで公開する。
--     * 2D 前景シルエット … beamatlas と同じ「カメラ→セルの視線 × 遮蔽平面」
--       交点で occ アトラスのマスクを引く（ps_vl_lfgrid）。
--     * 3D オブジェクト（.obj / .vrm）… cache:vl3d/camocc の被覆率と
--       カメラ深度をそのまま拾う。
--   受信側は粒子の画面位置とカメラ深度だけで「影の中か」を判定できる。
--   生成は GPU 1 パス（32x18 = 576 画素）+ 読み戻しなので、
--   VL_LF_GRID_EVERY フレームに 1 回だけ更新して結果を再利用する。
-- ---------------------------------------------------------------
local function lf_build_occ_grid(scene_frame)
    if not _LF_FFI_OK then return nil end
    local cache = _VL_PERSIST.lf_ogrid
    local n_occ = math.floor((VL_BF.n_occ_active or 0) + 0.5)
    local camocc = (VL_BF.camocc_active or 0.0) > 0.5
    if n_occ <= 0 and not camocc then
        _VL_PERSIST.lf_ogrid = nil
        return nil
    end
    if type(cache) == "table" and type(cache.blob) == "string"
        and type(scene_frame) == "number" and type(cache.frame) == "number"
        and scene_frame >= cache.frame and (scene_frame - cache.frame) < VL_LF_GRID_EVERY
        and cache.n_occ == n_occ and cache.camocc == camocc then
        return cache.blob
    end
    local blob = nil
    pcall(function()
        local c = {
            VL_LF_OGRID_W, VL_LF_OGRID_H, VL_BF.scr_w or 1920.0, VL_BF.scr_h or 1080.0,
            VL_BF.cam_x or 0.0, VL_BF.cam_y or 0.0, VL_BF.cam_z or 0.0, VL_BF.focal_d or 1024.0,
            VL_BF.f_x or 0.0, VL_BF.f_y or 0.0, VL_BF.f_z or 1.0, n_occ,
            VL_BF.r_x or 1.0, VL_BF.r_y or 0.0, VL_BF.r_z or 0.0, VL_BF.uv_span_x or 1.0,
            VL_BF.d_x or 0.0, VL_BF.d_y or 1.0, VL_BF.d_z or 0.0, VL_BF.uv_span_y or 1.0,
            VL_BF.occ_str_val or 1.0, VL_BF.camocc_w or 4.0, VL_BF.camocc_h or 4.0, VL_LF_OCC_ZMAX,
            camocc and 1.0 or 0.0, 0.0, 0.0, 0.0,
        }
        for i = 1, 128 do c[#c + 1] = VL_BF.occ_flat[i] or 0.0 end
        obj.clearbuffer("cache:vl3d/lfgrid", VL_LF_OGRID_W, VL_LF_OGRID_H)
        obj.pixelshader("ps_vl_lfgrid", "cache:vl3d/lfgrid",
            { VL_BF.occ_tex_name, VL_BF.camocc_tex_name }, c, "copy", "clip")
        local ptr, gw, gh = obj.getpixeldata("cache:vl3d/lfgrid", "rgba")
        if ptr == nil or type(gw) ~= "number" or type(gh) ~= "number" then return end
        if gw < 1 or gh < 1 then return end
        local need = gw * gh * 4
        blob = _LF_FFI.string(_LF_FFI.cast("const char*", ptr), need)
    end)
    if type(blob) ~= "string" or #blob ~= VL_LF_OGRID_W * VL_LF_OGRID_H * 4 then
        blob = nil
    end
    _VL_PERSIST.lf_ogrid = { frame = scene_frame, blob = blob, n_occ = n_occ, camocc = camocc }
    return blob
end

-- ---------------------------------------------------------------
-- このフレームの光束 1 本ぶんのペイロード
-- ---------------------------------------------------------------
local function lf_build_payload(scene_frame)
    local kind = 0
    if VL_BF.mode_kind == 1.0 then kind = 1
    elseif VL_BF.mode_kind == 2.0 then kind = 2 end

    -- 実際に光っている矩形（しきい値で切り詰めた範囲）→ 中心・半径
    local u0, u1 = VL_BF.u0_s or 0.0, VL_BF.u1_s or 1.0
    local v0, v1 = VL_BF.v0_s or 0.0, VL_BF.v1_s or 1.0
    if not (u1 > u0) then u0, u1 = 0.0, 1.0 end
    if not (v1 > v0) then v0, v1 = 0.0, 1.0 end
    local Hx = math.max(1.0, VL_BF.Hx or 1.0)
    local Hy = math.max(1.0, VL_BF.Hy or 1.0)
    local cxl = ((u0 + u1) * 0.5 - 0.5) * 2.0 * Hx
    local cyl = ((v0 + v1) * 0.5 - 0.5) * 2.0 * Hy
    local hx = math.max(1.0, Hx * (u1 - u0) * 0.5)
    local hy = math.max(1.0, Hy * (v1 - v0) * 0.5)

    local wx = (VL_BF.wx or 0.0) + (VL_BF.Wrx or 1.0) * cxl + (VL_BF.Wdx or 0.0) * cyl
    local wy = (VL_BF.wy or 0.0) + (VL_BF.Wry or 0.0) * cxl + (VL_BF.Wdy or 1.0) * cyl
    local wz = (VL_BF.wz or 0.0) + (VL_BF.Wrz or 0.0) * cxl + (VL_BF.Wdz or 0.0) * cyl

    local apex_x = VL_BF.sx_p or 0.0
    local apex_y = VL_BF.sy_p or 0.0
    local apex_z = VL_BF.sz_p or 0.0

    -- 光の進行方向（単位ベクトル）
    local ax, ay, az
    if kind == 0 then
        ax, ay, az = apex_x, apex_y, apex_z       -- 平行光: s が進行方向
    else
        local sgn = (kind == 1) and 1.0 or -1.0   -- 放射: apex→面 / 収束: 面→apex
        ax, ay, az = (wx - apex_x) * sgn, (wy - apex_y) * sgn, (wz - apex_z) * sgn
    end
    local al = math.sqrt(ax * ax + ay * ay + az * az)
    if al < 1e-6 then
        ax, ay, az = -(VL_BF.Wnx or 0.0), -(VL_BF.Wny or 0.0), -(VL_BF.Wnz or 1.0)
        al = math.sqrt(ax * ax + ay * ay + az * az)
        if al < 1e-6 then ax, ay, az, al = 0.0, 0.0, 1.0, 1.0 end
    end
    ax, ay, az = ax / al, ay / al, az / al

    -- apex と光源面の軸方向距離（円錐の相似スケール基準）
    local r0 = 0.0
    if kind == 1 then
        r0 = (wx - apex_x) * ax + (wy - apex_y) * ay + (wz - apex_z) * az
    elseif kind == 2 then
        r0 = (apex_x - wx) * ax + (apex_y - wy) * ay + (apex_z - wz) * az
    end
    if r0 < 1.0 then r0 = 1.0 end

    local L = math.max(1.0, VL_BF.L_tot or 1.0)
    local kdec = math.max(0.0, VL_BF.k_dec or 0.0)
    local col_r, col_g, col_b, lum, col_from_src, col_grid = lf_source_color(scene_frame)
    local power = (P.intensity or 100.0) / 100.0
    local coverage = math.max(0.0, math.min(1.0, VL_BF.base_ratio or 1.0))

    local occ_grid = lf_build_occ_grid(scene_frame)

    local flags = 0
    if type(P.light_color) ~= "number" and VL_BF.use_source ~= 0.0 then flags = flags + 1 end
    if VL_BF.any_occ_active then flags = flags + 2 end
    if VL_BF.cam_mode and VL_BF.cam_mode > 0 then flags = flags + 4 end
    if col_grid then flags = flags + 8 end
    if occ_grid then flags = flags + 16 end

    local t = {}
    t[#t + 1] = VL_LF_MAGIC
    t[#t + 1] = string.char(VL_LF_VERSION, flags)
    t[#t + 1] = lf_u32(scene_frame or 0)
    t[#t + 1] = lf_u32(obj.scene_id or 0)
    t[#t + 1] = lf_f32(obj.time or 0.0)
    t[#t + 1] = lf_u16(obj.layer or VL_BF.self_layer or 0)
    t[#t + 1] = lf_u16(VL_BF.ln or 0)
    -- camera
    t[#t + 1] = lf_f32(VL_BF.cam_x or 0.0); t[#t + 1] = lf_f32(VL_BF.cam_y or 0.0); t[#t + 1] = lf_f32(VL_BF.cam_z or 0.0)
    t[#t + 1] = lf_f32(VL_BF.f_x or 0.0);   t[#t + 1] = lf_f32(VL_BF.f_y or 0.0);   t[#t + 1] = lf_f32(VL_BF.f_z or 1.0)
    t[#t + 1] = lf_f32(VL_BF.r_x or 1.0);   t[#t + 1] = lf_f32(VL_BF.r_y or 0.0);   t[#t + 1] = lf_f32(VL_BF.r_z or 0.0)
    t[#t + 1] = lf_f32(VL_BF.d_x or 0.0);   t[#t + 1] = lf_f32(VL_BF.d_y or 1.0);   t[#t + 1] = lf_f32(VL_BF.d_z or 0.0)
    t[#t + 1] = lf_f32(VL_BF.focal_d or 1024.0)
    t[#t + 1] = lf_f32(VL_BF.scr_w or 1920.0); t[#t + 1] = lf_f32(VL_BF.scr_h or 1080.0)
    -- beam
    t[#t + 1] = string.char(kind, 0)
    t[#t + 1] = lf_f32(wx); t[#t + 1] = lf_f32(wy); t[#t + 1] = lf_f32(wz)
    t[#t + 1] = lf_f32(ax); t[#t + 1] = lf_f32(ay); t[#t + 1] = lf_f32(az)
    -- right(Wr) → down(Wd) の順（26 値の並びを崩さないこと）
    t[#t + 1] = lf_f32(VL_BF.Wrx or 1.0); t[#t + 1] = lf_f32(VL_BF.Wry or 0.0); t[#t + 1] = lf_f32(VL_BF.Wrz or 0.0)
    t[#t + 1] = lf_f32(VL_BF.Wdx or 0.0); t[#t + 1] = lf_f32(VL_BF.Wdy or 1.0); t[#t + 1] = lf_f32(VL_BF.Wdz or 0.0)
    t[#t + 1] = lf_f32(apex_x); t[#t + 1] = lf_f32(apex_y); t[#t + 1] = lf_f32(apex_z)
    t[#t + 1] = lf_f32(r0)
    t[#t + 1] = lf_f32(hx); t[#t + 1] = lf_f32(hy)
    t[#t + 1] = lf_f32(L)
    t[#t + 1] = lf_f32(kdec)
    t[#t + 1] = lf_f32(col_r); t[#t + 1] = lf_f32(col_g); t[#t + 1] = lf_f32(col_b)
    t[#t + 1] = lf_f32(lum)
    t[#t + 1] = lf_f32(power)
    t[#t + 1] = lf_f32(coverage)
    -- ---- v2: 遮蔽グリッド / 光源色グリッド ----
    local cdim = col_grid and VL_LF_CGRID or 0
    local ow, oh = 0, 0
    if occ_grid then ow, oh = VL_LF_OGRID_W, VL_LF_OGRID_H end
    t[#t + 1] = string.char(cdim % 256, ow % 256, oh % 256, 0)
    t[#t + 1] = lf_f32(VL_LF_OCC_ZMAX)
    if cdim > 0 then t[#t + 1] = col_grid end
    if ow > 0 then t[#t + 1] = occ_grid end
    return table.concat(t)
end

-- ---------------------------------------------------------------
-- 共有インデックス（どのレイヤーが今フレーム有効な光束を持つか）
--   "VL3LF1|<scene_frame>|<layer,layer,...>"
-- ---------------------------------------------------------------
local function lf_update_index(g, scene_frame, layer)
    local cur
    pcall(function() cur = g[VL_LF_IDX_KEY] end)
    local layers, seen = {}, {}
    if type(cur) == "string" then
        local csv = cur:match("^VL3LF%d+|%d+|(.*)$")
        if csv then
            for tok in csv:gmatch("%d+") do
                local n = tonumber(tok)
                if n and n >= 1 and n <= 100 and not seen[n] then
                    seen[n] = true
                    local keep = (n == layer)
                    if not keep then
                        local payload
                        pcall(function() payload = g[VL_LF_SLOT_PREFIX .. n] end)
                        if type(payload) == "string" and payload:sub(1, 5) == VL_LF_MAGIC then
                            local f = lf_read_u32(payload, 8)
                            if f and type(scene_frame) == "number" and scene_frame - f <= VL_LF_STALE then
                                keep = true
                            end
                        end
                    end
                    if keep then layers[#layers + 1] = n end
                end
            end
        end
    end
    if not seen[layer] then layers[#layers + 1] = layer end
    table.sort(layers)
    pcall(function()
        g[VL_LF_IDX_KEY] = string.format("VL3LF%d|%d|%s", VL_LF_IDX_VER,
            math.floor(scene_frame or 0), table.concat(layers, ","))
    end)
    return layers
end

-- ---------------------------------------------------------------
-- フレーム途中（描画直前）の準備
--   光源色グリッドと遮蔽グリッドは、光束の描画に使うキャッシュ
--   （cache:vl3d/mip, occ アトラス, camocc）が生きている間にしか作れない。
--   そこで vl_bf_render() の冒頭で 1 回だけ呼び、結果を
--   _VL_PERSIST に貯めておく（フレーム末の公開時はそれを読むだけ）。
-- ---------------------------------------------------------------
function vl_lf_prepare()
    if _G.VL3D_TEST_LF_DISABLE then return false end
    pcall(function()
        lf_source_color(vl_scene_frame())
    end)
    pcall(function()
        lf_build_occ_grid(vl_scene_frame())
    end)
    return true
end

-- ---------------------------------------------------------------
-- 公開 / 撤回
-- ---------------------------------------------------------------
local function vl_lf_withdraw()
    local g = lf_global()
    if not g then return false end
    local ok = pcall(function()
        local layer = math.floor(obj.layer or VL_BF.self_layer or 0)
        if layer >= 1 then
            g[VL_LF_SLOT_PREFIX .. layer] = ""
            lf_update_index(g, vl_scene_frame(), layer)
        end
    end)
    return ok
end

local function vl_lf_publish()
    if _G.VL3D_TEST_LF_DISABLE then return false end
    local g = lf_global()
    if not g then return false end
    local layer = math.floor(obj.layer or VL_BF.self_layer or 0)
    if layer < 1 then return false end
    local ok, payload = pcall(function()
        local scene_frame = vl_scene_frame()
        local s = lf_build_payload(scene_frame)
        g[VL_LF_SLOT_PREFIX .. layer] = s
        lf_update_index(g, scene_frame, layer)
        _VL_PERSIST.lf_last = { frame = scene_frame, bytes = #s, layer = layer }
        if _G.VL3D_TEST_LF_DEBUG then
            local c = _VL_PERSIST.lf_color
            local o = _VL_PERSIST.lf_ogrid
            _G.__VL_LF_DBG = {
                bytes = #s, mip_ready = VL_BF.mip_ready and 1 or 0,
                grid_bytes = (type(c) == "table" and type(c.grid) == "string") and #c.grid or -1,
                grid_err = (type(c) == "table" and c.err) or "",
                ogrid_bytes = (type(o) == "table" and type(o.blob) == "string") and #o.blob or -1,
                n_occ = VL_BF.n_occ_active or 0, camocc = VL_BF.camocc_active or 0,
            }
        end
        return s
    end)
    return ok and type(payload) == "string"
end
