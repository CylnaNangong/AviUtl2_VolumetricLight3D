--------------------------------------------------------------------
-- 体積光（3D）連動: VL3D Light Field v1 の受信
--------------------------------------------------------------------
-- 体積光（3D）は毎フレーム、その光束の 3D 形状（光源面の位置・向き・
-- 半径・進行方向・拡散角・長さ・減衰・光束色）を AviUtl2 公式の
-- スクリプト間共有テーブル `global` へ書き出しています。
--   VL3D_LF_IDX   = "VL3LF1|<scene_frame>|<layer,layer,...>"
--   VL3D_LF:<n>   = 189 バイトのバイナリ（リトルエンディアン）
-- ここではそれを読み、粒子位置での「受光量」と「光色」を解析的に
-- 求める。CPU 追加コストは粒子 1 個あたり 30 演算程度で、
-- 追加の GPU 読み戻し・追加シェーダは使わない。
--------------------------------------------------------------------
local SNOW_LF = (function()
    local LF = { on = false, n = 0, beams = {}, frame = nil,
                 avg_r = 1.0, avg_g = 1.0, avg_b = 1.0,
                 strength = 1.0, tint = 0.7, spread = 1.0 }

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
                if type(p) == "string" and #p >= 189 and p:sub(1, 5) == "VL3LF"
                    and p:byte(6) == 1 then
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
        LF.n = n
        LF.frame = scene_frame
        LF.idx_frame = iframe
        return n
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
                        if w > 0.0005 then
                            lit = lit + w
                            sr = sr + b[22] * w
                            sg = sg + b[23] * w
                            sb = sb + b[24] * w
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
