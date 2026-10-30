--------------------------------------------------------------------
-- 体積光（3D）連動: VL3D Light Field v1 の受信
--------------------------------------------------------------------
-- 体積光（3D）は毎フレーム、その光束の 3D 形状（光源面の位置・向き・
-- 半径・進行方向・拡散角・長さ・減衰・光束色）を AviUtl2 公式の
-- スクリプト間共有テーブル `global` へ書き出しています。
--   VL3D_LF_IDX   = "VL3LF1|<scene_frame>|<layer,layer,...>"
--   VL3D_LF:<n>   = 189 バイト（v1）／以降に追記（v2, リトルエンディアン）
-- v1 = 光束の 3D 形状のみ。v2 は末尾に
--   * 光源色グリッド（12x12 RGB8）… 粒子ごとに光束色を変える
--     （ステンドグラスのような多色光源をそのまま再現）
--   * 遮蔽グリッド（32x18 RGBA8）… 2D 前景シルエットと 3D オブジェクトの
--     前後関係。粒子が影の中なら受光させない
-- を追加する。ここではそれを読み、粒子位置での「受光量」と「光色」を
-- 解析的に求める。CPU 追加コストは粒子 1 個あたり 40 演算程度で、
-- 追加の GPU 読み戻し・追加シェーダは使わない。
--------------------------------------------------------------------
local SNOW_LF = (function()
    local LF = { on = false, n = 0, beams = {}, frame = nil,
                 avg_r = 1.0, avg_g = 1.0, avg_b = 1.0,
                 strength = 1.0, tint = 0.7, spread = 1.0,
                 -- カメラ（遮蔽判定用。全光束で共通）
                 cam = nil, occ_on = false }

    -- ---- 数値デコード（ffi を使わない純 Lua。1 フレーム 1 回だけ） ----
    local function rd_f32(s, i)
        local b1, b2, b3, b4 = s:byte(i, i + 3)
        if not b4 then return 0.0 end
        local e = (b4 % 128) * 2 + math.floor(b3 / 128)
        local m = (b3 % 128) * 65536 + b2 * 256 + b1
        local v
        if e == 0 then
            if m == 0 then return 0.0 end
            v = m * 1.401298464324817e-45
        elseif e == 255 then
            return 0.0
        else
            v = (1.0 + m / 8388608.0) * 2.0 ^ (e - 127)
        end
        if b4 >= 128 then return -v end
        return v
    end

    local function rd_u32(s, i)
        local b1, b2, b3, b4 = s:byte(i, i + 3)
        if not b4 then return 0 end
        return b1 + b2 * 256 + b3 * 65536 + b4 * 16777216
    end

    -- ---- 共有テーブルからこのフレームの光束を集める ----
    local function collect(scene_frame)
        local g = global
        if type(g) ~= "table" then return 0 end
        local idx = g["VL3D_LF_IDX"]
        if type(idx) ~= "string" then return 0 end
        local ver, iframe, csv = idx:match("^VL3LF(%d+)|(%-?%d+)|(.*)$")
        if ver ~= "1" then return 0 end
        iframe = tonumber(iframe) or 0
        local n = 0
        local sr, sg, sb, sw = 0.0, 0.0, 0.0, 0.0
        for tok in csv:gmatch("%d+") do
            local layer = tonumber(tok)
            if layer and layer >= 1 and n < 8 then
                local p = g["VL3D_LF:" .. layer]
                local pver = type(p) == "string" and p:byte(6) or 0
                if type(p) == "string" and #p >= 189 and p:sub(1, 5) == "VL3LF"
                    and (pver == 1 or pver == 2) then
                    local pf = rd_u32(p, 8)
                    -- レイヤー順の都合で 1〜2 フレーム古い値は許容する
                    if math.abs(pf - scene_frame) <= 2 then
                        local kind = p:byte(84) or 0
                        local o = 86
                        -- ペイロードの並びと 1:1 で対応させる（26 floats）
                        local b = {
                            kind,                                              -- 1
                            rd_f32(p, o),      rd_f32(p, o + 4),  rd_f32(p, o + 8),   -- 2..4   W
                            rd_f32(p, o + 12), rd_f32(p, o + 16), rd_f32(p, o + 20),  -- 5..7   axis
                            rd_f32(p, o + 24), rd_f32(p, o + 28), rd_f32(p, o + 32),  -- 8..10  right
                            rd_f32(p, o + 36), rd_f32(p, o + 40), rd_f32(p, o + 44),  -- 11..13 down
                            rd_f32(p, o + 48), rd_f32(p, o + 52), rd_f32(p, o + 56),  -- 14..16 apex
                            rd_f32(p, o + 60),                                        -- 17 r0
                            rd_f32(p, o + 64), rd_f32(p, o + 68),                     -- 18..19 Hx,Hy
                            rd_f32(p, o + 72),                                        -- 20 L
                            rd_f32(p, o + 76),                                        -- 21 kdec
                            rd_f32(p, o + 80), rd_f32(p, o + 84), rd_f32(p, o + 88),  -- 22..24 光束色
                            rd_f32(p, o + 92),                                        -- 25 lum
                            rd_f32(p, o + 96),                                        -- 26 power
                            rd_f32(p, o + 100),                                       -- 27 coverage
                        }
                        if b[20] > 1.0 and b[18] > 0.01 and b[19] > 0.01 then
                            -- カメラ（遮蔽判定と光源面 UV 射影に使う）は全光束で共通
                            if LF.cam == nil then
                                local c = 24
                                LF.cam = {
                                    x = rd_f32(p, c),     y = rd_f32(p, c + 4),  z = rd_f32(p, c + 8),
                                    fx = rd_f32(p, c + 12), fy = rd_f32(p, c + 16), fz = rd_f32(p, c + 20),
                                    rx = rd_f32(p, c + 24), ry = rd_f32(p, c + 28), rz = rd_f32(p, c + 32),
                                    dx = rd_f32(p, c + 36), dy = rd_f32(p, c + 40), dz = rd_f32(p, c + 44),
                                    focal = rd_f32(p, c + 48), sw = rd_f32(p, c + 52), sh = rd_f32(p, c + 56),
                                }
                            end
                            -- v2: 光源色グリッド / 遮蔽グリッド
                            b.cg_dim, b.cg_off = 0, 0
                            b.og_w, b.og_h, b.og_off, b.og_zmax = 0, 0, 0, 4096.0
                            if pver >= 2 and #p >= 197 then
                                local cdim = p:byte(190) or 0
                                local ow = p:byte(191) or 0
                                local oh = p:byte(192) or 0
                                local zmax = rd_f32(p, 194)
                                local off = 198
                                if cdim > 0 and cdim <= 64 and #p >= off + cdim * cdim * 3 - 1 then
                                    b.cg_dim, b.cg_off = cdim, off
                                    off = off + cdim * cdim * 3
                                end
                                if ow > 0 and oh > 0 and ow <= 128 and oh <= 128
                                    and #p >= off + ow * oh * 4 - 1 then
                                    b.og_w, b.og_h, b.og_off = ow, oh, off
                                    b.og_zmax = (zmax > 16.0) and zmax or 4096.0
                                end
                            end
                            b.src = p
                            n = n + 1
                            LF.beams[n] = b
                            local w = math.max(0.05, b[26]) * math.max(0.05, b[25])
                            sr = sr + b[22] * w
                            sg = sg + b[23] * w
                            sb = sb + b[24] * w
                            sw = sw + w
                        end
                    end
                end
            end
        end
        if sw > 1e-4 then
            LF.avg_r, LF.avg_g, LF.avg_b = sr / sw, sg / sw, sb / sw
        else
            LF.avg_r, LF.avg_g, LF.avg_b = 1.0, 1.0, 1.0
        end
        for i = n + 1, #LF.beams do LF.beams[i] = nil end
        LF.occ_on = false
        for i = 1, n do
            if (LF.beams[i].og_w or 0) > 0 then LF.occ_on = true break end
        end
        if n < 1 then LF.cam = nil end
        LF.n = n
        LF.frame = scene_frame
        LF.idx_frame = iframe
        return n
    end

    -- ---- v2: 遮蔽グリッドの参照（画面 UV とカメラ深度から可視率） ----
    local function lf_occ_at(b, su, sv, zc)
        if (b.og_w or 0) < 1 or type(b.src) ~= "string" then return 1.0 end
        if su < 0.0 or su >= 1.0 or sv < 0.0 or sv >= 1.0 then return 1.0 end
        local gw, gh, off = b.og_w, b.og_h, b.og_off
        local gx = math.floor(su * gw)
        local gy = math.floor(sv * gh)
        if gx >= gw then gx = gw - 1 end
        if gy >= gh then gy = gh - 1 end
        if gx < 0 then gx = 0 end
        if gy < 0 then gy = 0 end
        local i = off + (gy * gw + gx) * 4
        local m, cov3, zb, ab = b.src:byte(i, i + 3)
        if not ab then return 1.0 end
        local zm = b.og_zmax or 4096.0
        local vis = 1.0
        -- 2D 前景シルエット: 遮蔽平面より奥にいる粒子だけを落とす
        if m > 0 and ab < 255 then
            local od = (ab / 255.0) * zm
            local f = (zc - od) / math.max(24.0, od * 0.02)
            if f > 0.0 then
                if f > 1.0 then f = 1.0 end
                vis = vis * (1.0 - (m / 255.0) * f)
            end
        end
        -- 3D オブジェクト: 表面より奥なら遮光（奥行き差でソフトに）
        if cov3 > 0 and zb < 255 then
            local zd = (zb / 255.0) * zm
            local f = (zc - zd) / math.max(24.0, zd * 0.02)
            if f > 0.0 then
                if f > 1.0 then f = 1.0 end
                vis = vis * (1.0 - (cov3 / 255.0) * f)
            end
        end
        return vis
    end

    -- ---- v2: 光源色グリッドのバイリニア参照（u,v は光源面の 0..1） ----
    local function lf_color_at(b, u, v)
        local G = b.cg_dim or 0
        if G < 1 or type(b.src) ~= "string" then return nil end
        if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0 then return nil end
        local off = b.cg_off
        local src = b.src
        local fx = u * G - 0.5
        local fy = v * G - 0.5
        local x0 = math.floor(fx)
        local y0 = math.floor(fy)
        local tx = fx - x0
        local ty = fy - y0
        local function cell(ix, iy)
            if ix < 0 then ix = 0 elseif ix >= G then ix = G - 1 end
            if iy < 0 then iy = 0 elseif iy >= G then iy = G - 1 end
            local i = off + (iy * G + ix) * 3
            local r, g, bb = src:byte(i, i + 2)
            if not bb then return 0.0, 0.0, 0.0 end
            return r / 255.0, g / 255.0, bb / 255.0
        end
        local r00, g00, b00 = cell(x0, y0)
        local r10, g10, b10 = cell(x0 + 1, y0)
        local r01, g01, b01 = cell(x0, y0 + 1)
        local r11, g11, b11 = cell(x0 + 1, y0 + 1)
        local w00 = (1.0 - tx) * (1.0 - ty)
        local w10 = tx * (1.0 - ty)
        local w01 = (1.0 - tx) * ty
        local w11 = tx * ty
        return r00 * w00 + r10 * w10 + r01 * w01 + r11 * w11,
               g00 * w00 + g10 * w10 + g01 * w01 + g11 * w11,
               b00 * w00 + b10 * w10 + b01 * w01 + b11 * w11
    end

    -- ---- フレーム開始時に 1 回だけ呼ぶ ----
    function LF.begin(obj, scene_frame)
        LF.on = as_bool(lf_enable, true)
        LF.strength = (tonumber(lf_strength) or 120) / 100
        LF.tint = math.max(0, math.min(1, (tonumber(lf_tint) or 70) / 100))
        LF.spread = 1.0 + math.max(0, math.min(300, tonumber(lf_spread) or 100)) / 200
        if not LF.on or LF.strength <= 0 then
            LF.n = 0
            return 0
        end
        if type(scene_frame) ~= "number" then
            local f = (type(obj.frame) == "number") and math.floor(obj.frame + 0.5) or 0
            local fs = (type(obj.frame_s) == "number") and math.floor(obj.frame_s + 0.5) or 0
            scene_frame = fs + f
        end
        if LF.frame == scene_frame then return LF.n end
        local ok, n = pcall(collect, scene_frame)
        if not ok then
            LF.n = 0
            return 0
        end
        return n
    end

    -- ---- 粒子 1 個の受光量（世界座標, soft=粒子半径[world]） ----
    --   戻り値: 光束色(0..1) と受光量(0..∞, 1 で「光束中心相当」)
    function LF.sample(x, y, z, soft)
        local n = LF.n
        if n < 1 then return 0.0, 0.0, 0.0, 0.0 end
        local s = soft or 0.0
        -- v2: 遮蔽。粒子の画面位置とカメラ深度から可視率を 1 回だけ求める
        --     （2D 前景シルエット / 3D オブジェクトの前後関係）。
        local vis = 1.0
        if LF.occ_on and LF.cam then
            local c = LF.cam
            local zc = (x - c.x) * c.fx + (y - c.y) * c.fy + (z - c.z) * c.fz
            if zc > 1.0 and c.sw > 1.0 and c.sh > 1.0 then
                local su = 0.5 + ((x - c.x) * c.rx + (y - c.y) * c.ry + (z - c.z) * c.rz)
                    * c.focal / (c.sw * zc)
                local sv = 0.5 + ((x - c.x) * c.dx + (y - c.y) * c.dy + (z - c.z) * c.dz)
                    * c.focal / (c.sh * zc)
                vis = lf_occ_at(LF.beams[1], su, sv, zc)
            end
        end
        if vis < 0.002 then return 0.0, 0.0, 0.0, 0.0 end
        local sr, sg, sb, lit = 0.0, 0.0, 0.0, 0.0
        for i = 1, n do
            local b = LF.beams[i]
            local vx, vy, vz = x - b[2], y - b[3], z - b[4]
            local t = vx * b[5] + vy * b[6] + vz * b[7]
            local L = b[20]
            if t >= -s and t <= L + s then
                local ex, ey = b[18], b[19]
                if b[1] ~= 0 then
                    -- 円錐: apex からの距離比で断面を相似拡大／縮小
                    local r0 = b[17]
                    local axd
                    if b[1] == 1 then axd = r0 + t else axd = r0 - t end
                    if axd > s + 0.5 then
                        -- 断面は apex からの距離に比例して相似拡大／縮小する
                        local k = axd / r0
                        ex, ey = ex * k, ey * k
                    else
                        ex, ey = -1.0, -1.0
                    end
                end
                if ex > 0.01 and ey > 0.01 then
                    local lx = (vx * b[8] + vy * b[9] + vz * b[10]) / ex
                    local ly = (vx * b[11] + vy * b[12] + vz * b[13]) / ey
                    local q = math.sqrt(lx * lx + ly * ly)
                    -- 縁の柔らかさ: 粒子半径と断面サイズの比
                    local f = 0.35 * s / math.max(ex, ey)
                    if f < 0.08 then f = 0.08 elseif f > 0.9 then f = 0.9 end
                    if q < 1.0 + f then
                        local cov = 1.0
                        if q > 1.0 - f then
                            local k = (q - (1.0 - f)) / f
                            if k > 1.0 then k = 1.0 end
                            cov = 1.0 - k * k * (3.0 - 2.0 * k)
                        end
                        -- 距離減衰と終端フェード
                        local ta = t
                        if ta < 0.0 then ta = 0.0 end
                        local wdec = math.exp(-ta * b[21])
                        local we = 1.0
                        local lfade = L * 0.72
                        if t > lfade and L > lfade then
                            local k = (t - lfade) / (L - lfade)
                            if k > 1.0 then k = 1.0 end
                            we = 1.0 - k * k * (3.0 - 2.0 * k)
                        end
                        -- 光束の実効輝度: 強度 × 光源の明るさ × 光の充填率
                        --   (col は最大チャンネルが 1 に正規化されているので lum を掛ける)
                        local w = cov * wdec * we * (b[26] or 1.0) * (b[25] or 1.0) * (b[27] or 1.0)
                        w = w * vis
                        if w > 0.0005 then
                            local cr, cg, cb = b[22], b[23], b[24]
                            -- v2: 光源色グリッド（ステンドグラス等の多色光源）
                            --     粒子を光源面へ射影した UV で色を引く
                            local gu = 0.5 + 0.5 * (vx * b[8] + vy * b[9] + vz * b[10]) / b[18]
                            local gv = 0.5 + 0.5 * (vx * b[11] + vy * b[12] + vz * b[13]) / b[19]
                            local gr, gg, gb = lf_color_at(b, gu, gv)
                            if gr then cr, cg, cb = gr, gg, gb end
                            lit = lit + w
                            sr = sr + cr * w
                            sg = sg + cg * w
                            sb = sb + cb * w
                        end
                    end
                end
            end
        end
        if lit > 1e-5 then
            sr, sg, sb = sr / lit, sg / lit, sb / lit
            if lit > 4.0 then lit = 4.0 end
        end
        return sr, sg, sb, lit
    end

    LF.begin(obj)
    return LF
end)()
