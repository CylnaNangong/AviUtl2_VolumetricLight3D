---------------------------------------------------------------------
-- 00a_consts.lua — 数值常量表（K）
---------------------------------------------------------------------
-- 本文件是「跨分支共享的数值常量」的唯一出处。规则：
--   ・新增或修改常量 → 只改这里；render/geometry 片段只允许引用 K.*
--   ・本表与 sandbox/checks/checks_10_brightness.py 的性质断言成对维护，
--     改数值前先跑 `python3 sandbox/run_checks.py --only brightness`
--   ・这里只放「有明确含义、会被多处引用」的值。局部的一次性系数
--     （例如某个平滑窗口的中点）仍写在原地，避免把表变成废纸篓。
--
-- 位置说明：本片段必须排在 00_header 之后、任何读取 K 的片段之前。
---------------------------------------------------------------------

local K = {
    ---------------------------------------------------------------
    -- 宿主 3D OIT 的片元权重契约（逆向自 Shader #118，非官方 API 规范）
    ---------------------------------------------------------------
    -- 宿主对每个四边形片元计算 r4.w = tex.a * poly_alpha：
    --   ・r4.w <  DISCARD  → 片元被硬件丢弃（用于砍掉光锥外缘的零值邻域）
    --   ・r4.w >= DISCARD  → 参与 WBOIT 累积
    -- 同时要求 0.75 * r4.w < DISCARD，这样 2x2 双线性里「3/4 权重来自零纹素」
    -- 的边缘像素也会被同一阈值丢弃，从而得到「满覆盖活、边缘死」的硬边界。
    -- 于是合法窗口是 (DISCARD, DISCARD / 0.75) = (0.001, 0.001333)。
    -- 详见 docs/CHANGELOG_zh.md v3.09 / v3.11 / v3.12 三节与 §8.4(1)。
    OIT = {
        DISCARD      = 0.001,             -- r4.w 丢弃阈值（实测，非官方规范）
        EDGE_KILL_F  = 0.994,             -- 边缘覆盖 f < 此值应被丢弃 → 权重下限的由来
        MIN_WEIGHT   = 0.2565 / 255.0,    -- 锚点权重 = 0.0010058824（v3.09 起实机验证）
        BASE_SPOT    = 4.0 / 255.0,       -- 虚拟聚光的恒定基准阶（v3.12，k_min = 4）
        BASE_DEFAULT = 1.0 / 255.0,       -- 其它路径的恒定基准阶（v3.10）
        ALPHA_SPOT   = 0.2565 / 4.0,      -- = MIN_WEIGHT / BASE_SPOT = 0.064125（精确相等）
        ALPHA_DEFAULT = 0.27,             -- 非聚光路径的拉伸 alpha（与 BASE_DEFAULT 配对）
    },

    ---------------------------------------------------------------
    -- 亮度标定（k_gain）
    ---------------------------------------------------------------
    -- 有效辉度 = k_gain_base * (base_alpha / poly_alpha) * QUANT * cam_comp，
    -- 其中的 (base_alpha / poly_alpha) 是「换了 poly_alpha 也要保持同一亮度」的
    -- 补偿项：两者必须一起改，否则整条光束的亮度会阶跃。
    GAIN = {
        CALIB = 0.155,                    -- k_gain_base 的标定常数（见 docs/CHANGELOG v3.14）
        REF_INTENSITY = 100.0,            -- 强度归一化基准（100 = 默认强度）
        QUANT = 1.05,                     -- 8bit 量化互补余量
    },
    ---------------------------------------------------------------
    -- 2D 遮蔽图集（occ_atlas）的分辨率预算
    ---------------------------------------------------------------
    -- 单张图集允许的像素上限是 180 万点（04_params.lua 原本就把它命名为
    -- OCC_SINGLE_BUDGET，注释「1 枚あたり 180 万ドット上限」），这个上限
    -- 来自宿主 TemporaryImageCacheSize（1,600 万点）在「图集 + mip + 前景
    -- 备份」同时驻留时的分配，不是 D3D 的硬限制。
    --
    -- 多光源（2x2 / 3x3 网格）时图集边长取 1344：它不是一个独立调出来的
    -- 数字，而是「能塞进 180 万点的最小 8 对齐正方形边长」——
    --     sqrt(1,800,000) = 1341.64…  →  向上取 8 的整倍数  →  1344
    -- 反证：1336² = 1,784,896 < 1,800,000（太小），1344² = 1,806,336（刚够，
    -- 超出预算 0.35%，这正是「略超」的来源，见 §8.4(2)）。
    -- 1344 同时是 64 的整倍数（21×64），也是 2、3 的整倍数，所以 2x2 得
    -- 672、3x3 得 448，切分后不产生余数像素。
    ---------------------------------------------------------------
    -- 相机遮挡深度图的 8bit RGB 编码（自定协议；Lua / C++ / 两份 HLSL 共四份副本）
    ---------------------------------------------------------------
    -- camocc 纹理是 RGBA8，浮点深度必须先压成 [0,1] 的 code，再摊到三个通道：
    --     r = sat(CHAN*code - 2), g = sat(CHAN*code - 1), b = sat(CHAN*code)
    -- 三通道互为「移位拷贝」：任一 code 恰好有一条不饱和，三者和恒等于 3*code，
    -- 所以 HLSL 侧 raw = (r+g+b)/3 能还原（误差只剩 8bit 量化 ≤ 0.5/255）。
    -- code 落在哪个区间由「是不是 VRM 角色」决定，FLAG 是区间标志位：
    --     网格 [0.01, 0.49]、角色 [0.51, 0.99]
    -- 两侧各留 0.01 保护带；三通道均值的量化误差是 1/510 ≈ 0.00196，
    -- 所以标志位不会被量化误差翻转（断言见文件末尾）。
    -- 注意：此处的 0.48 与 19_render.lua 的 cam_comp = 0.48 只是同值，
    -- 语义无关（一个是编码斜率，一个是相机补偿），千万不要一起替换。
    ---------------------------------------------------------------
    -- 特征光距 L_char：把「光束有多大」折算成一个等效距离
    ---------------------------------------------------------------
    -- L_char 参与亮度归一化（k_gain_base = 强度/100 * CALIB / L_char），所以它
    -- 一旦变化，整条光束的亮度会跟着变。历史上有三条**互不相同的估算路径**，
    -- 它们不是同一个公式的三个写法，而是三套针对不同光源类型的标定：
    --   (a) 屏幕空间一般路径（15_frame_prepare）：
    --       L = clamp(R_fixed * (BASE + RATIO2 * base_ratio^2), MIN_FIXED, MAX_FIXED)
    --   (b) 相机光源·平行光（16_geometry_mip）：固定 PAR_FIXED
    --   (c) 相机光源·点/聚光（16_geometry_mip）：
    --       L = clamp(SPOT_GAIN * tan(min(ANG_MAX, half_angle)) * SPOT_COEF,
    --                 MIN_SPOT, MAX_SPOT)
    -- (a) 的 R_fixed 与 (c) 的 ref_r 都先夹到同一个屏幕半径上限 R_MAX ——
    -- 这是三条路径唯一共用的量。
    -- PAR_FIXED 同时落在 (a)(c) 的窗口内，是「切换光源类型时亮度不跳变」的
    -- 最低保障；要真正消除分支间的不连续，得重做标定（会改变现有画面），
    -- 不在常量整理范围内。
    LCHAR = {
        R_MAX     = 520.0,   -- 屏幕半径上限（(a) 的 R_fixed 与 (c) 的 ref_r 共用）
        BASE      = 0.12,    -- (a) 常数项
        RATIO2    = 0.36,    -- (a) base_ratio² 的系数
        MIN_FIXED = 42.0,    -- (a) 下限
        MAX_FIXED = 260.0,   -- (a) 上限
        PAR_FIXED = 195.0,   -- (b) 平行光的固定特征距离
        SPOT_GAIN = 600.0,   -- (c) tan(半角) → 参考半径
        SPOT_COEF = 0.32,    -- (c) 参考半径 → 特征距离
        ANG_MAX   = 1.15,    -- (c) 参与计算的半角上限（rad）
        MIN_SPOT  = 22.0,    -- (c) 下限
        MAX_SPOT  = 200.0,   -- (c) 上限
    },
    PROX = {
        STEP      = 0.48,   -- 归一化深度 → code 的斜率（区间总宽 0.49 - 0.01）
        BASE_MESH = 0.01,   -- 网格区间下限
        BASE_CHAR = 0.51,   -- 角色区间下限（= FLAG + 0.01）
        FLAG      = 0.50,   -- 解码侧判据：raw > FLAG 即角色
        CHAN      = 3.0,    -- 通道展开系数
        OFF_R     = 2.0,    -- r = CHAN*code - OFF_R
        OFF_G     = 1.0,    -- g = CHAN*code - OFF_G
        OFF_B     = 0.0,    -- b = CHAN*code - OFF_B
    },
    ATLAS = {
        BUDGET_DOTS = 1800000,            -- 单图集像素预算（宿主缓存内的工程分配）
        ALIGN       = 8,                  -- 边长对齐（与 15_frame_prepare 的 /8 对齐一致）
    },
}

-- 由预算推导出的图集边长（放在表外，因为它是 BUDGET_DOTS/ALIGN 的函数）
K.ATLAS.SIDE_3 = K.ATLAS.ALIGN * math.ceil(math.sqrt(K.ATLAS.BUDGET_DOTS) / K.ATLAS.ALIGN)

---------------------------------------------------------------------
-- 启动自检：常量之间的隐含关系一旦被改坏，立刻在控制台暴露，
-- 而不是等到「画面悄悄变了」才发现。
---------------------------------------------------------------------
do
    local o = K.OIT
    local w_spot  = o.ALPHA_SPOT * o.BASE_SPOT
    local w_base  = o.ALPHA_DEFAULT * o.BASE_DEFAULT
    local function _win_ok(w)
        return w > o.DISCARD and 0.75 * w < o.DISCARD
    end
    -- 派生式必须精确复现历史锚点（差分放 0 之外就只有量化误差，不允许）
    assert(math.abs(w_spot - o.MIN_WEIGHT) < 1e-12,
           "K.OIT: ALPHA_SPOT*BASE_SPOT drifted from MIN_WEIGHT")
    -- 两条路径都必须落在宿主丢弃窗口内
    assert(_win_ok(w_spot), "K.OIT: spot weight left the (DISCARD, DISCARD/0.75) window")
    assert(_win_ok(w_base), "K.OIT: default weight left the (DISCARD, DISCARD/0.75) window")
    K.OIT.W_SPOT  = w_spot            -- 运行期只读缓存，便于调试输出
    K.OIT.W_BASE  = w_base
    -- 图集边长必须是「预算的平方根向上取整到 ALIGN」；改 BUDGET_DOTS 会自动跟随
    local a = K.ATLAS
    assert(a.SIDE_3 == 1344,
           "K.ATLAS: SIDE_3 drifted from the budget-derived 1344")
    assert(a.SIDE_3 * a.SIDE_3 >= a.BUDGET_DOTS,
           "K.ATLAS: atlas side no longer covers the dot budget")
    assert((a.SIDE_3 - a.ALIGN) * (a.SIDE_3 - a.ALIGN) < a.BUDGET_DOTS,
           "K.ATLAS: atlas side is larger than the smallest one covering the budget")
    -- 2x2 / 3x3 网格要能整除（672 / 448），否则 ceil 会多出整行整列
    assert(a.SIDE_3 % 2 == 0 and a.SIDE_3 % 3 == 0,
           "K.ATLAS: atlas side is not divisible by the 2x2 / 3x3 grid factors")
    -- prox 编码的区间必须首尾相接、标志位必须居中，且保护带大于量化误差
    local px = K.PROX
    local mesh_top = px.BASE_MESH + px.STEP        -- 0.49
    local char_top = px.BASE_CHAR + px.STEP        -- 0.99
    assert(math.abs(mesh_top - 0.49) < 1e-12, "K.PROX: mesh range no longer ends at 0.49")
    assert(math.abs(char_top - 0.99) < 1e-12, "K.PROX: char range no longer ends at 0.99")
    assert(char_top <= 1.0, "K.PROX: char range overflows the 0..1 code space")
    assert(math.abs(px.FLAG - (mesh_top + px.BASE_CHAR) * 0.5) < 1e-12,
           "K.PROX: FLAG is no longer midway between the two ranges")
    -- 量化误差：三通道均值最坏 1/510；保护带必须比它宽
    local q_err = 1.0 / 510.0
    assert(px.FLAG - mesh_top > q_err, "K.PROX: mesh guard band is thinner than 8bit quantization")
    assert(px.BASE_CHAR - px.FLAG > q_err, "K.PROX: char guard band is thinner than 8bit quantization")
    -- 三通道展开必须能还原：任一 code，clamp 后三者和恒为 CHAN*code
    for i = 0, 64 do
        local c = i / 64
        local s3 = 0
        for k = 0, 2 do
            local v = c * px.CHAN - k
            if v < 0 then v = 0 elseif v > 1 then v = 1 end
            s3 = s3 + v
        end
        assert(math.abs(s3 - px.CHAN * c) < 1e-12, "K.PROX: channel spread is not lossless")
    end
    -- L_char 三条路径的窗口关系：固定值必须落在另外两条的窗口内，
    -- 否则切换光源类型时亮度会跳变（这是历史遗留的感知标定，不是数学恒等式）
    local lc = K.LCHAR
    assert(lc.MIN_FIXED < lc.PAR_FIXED and lc.PAR_FIXED < lc.MAX_FIXED,
           "K.LCHAR: PAR_FIXED left the screen-space window")
    assert(lc.MIN_SPOT < lc.PAR_FIXED and lc.PAR_FIXED < lc.MAX_SPOT,
           "K.LCHAR: PAR_FIXED left the point/spot window")
    assert(lc.MIN_FIXED < lc.MAX_FIXED and lc.MIN_SPOT < lc.MAX_SPOT and lc.MIN_SPOT > 0,
           "K.LCHAR: inverted or non-positive clamp window")
    -- R_MAX 必须真的会生效：不加夹取时 (c) 的参考半径要能超过它
    assert(lc.SPOT_GAIN * math.tan(lc.ANG_MAX) > lc.R_MAX,
           "K.LCHAR: R_MAX no longer clamps the spot reference radius")
    assert(K.GAIN.REF_INTENSITY > 0, "K.GAIN: reference intensity must be positive")
end
