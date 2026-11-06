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
end
