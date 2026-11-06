# 魔法数字整改记录（v3.18 开发线）

> 目标：把「改错不报错、只会让画面悄悄变」的派生常量收口成**单一出处 + 可执行断言**，
> 并把宿主给的硬上限/限制整理成表、让降级行为在日志里出声。
>
> 硬约束：**渲染画面必须与整改前逐位一致**。本文所有结论都以逐字节比对为依据，
> 不是「看起来差不多」。

---

## 0. 一句话结论

| 项目 | 结果 |
|---|---|
| 整改前基线 | `610042c` 的 `Script/VolumetricLight3D.obj2`（sha256 `20ba4651…`） |
| 整改后 | `f4383072…`（脚本本体体积 534,973 → 554,631 B，全部是注释与常量表） |
| 逐位比对 | **49 / 49 个数组完全一致，0 处差异**（含 `cache:vl3d/camocc` 的原始字节） |
| 沙箱回归 | **118 PASS / 0 FAIL / 0 SKIP / 0 WARN**（整改前 109 PASS，新增 9 个契约用例） |
| 常量表 | `modules/00a_consts.lua` 一张表，6 组、47 个常量，末尾自带启动自检 |

---

## 1. 为什么先做这几项

报告第二遍分析（v3）把 4,195 个字面量按「风险密度」重排后，结论是：**占比最大的 S 类
（59%）几乎无风险；D 类只有 19 处（0.45%），却是唯一「改错不报错、只是画面悄悄变」
的一类**。所以优先级是 D → H → A，而不是按字面量数量。

本次完成的是 **D 类全部 6 条推导链** + **H 类两个最密集的簇**（宿主上限、编辑视点相机）。

---

## 2. 逐族说明

### 2.1 OIT 权重与增益（`K.OIT` / `K.GAIN`）

**这个数是什么**：`poly_alpha` 决定四边形的片元权重 `r4.w = tex.a × poly_alpha`。
宿主 WBOIT 以 `r4.w < 0.001` 丢弃片元。

**这两个数谁定的**：`0.001` 是宿主行为（逆向实测，非官方规范）；`0.2565`（锚点权重）、
`4/255`（基准阶）是本项目 v3.09→v3.12 的实机标定。

**为什么是这个值**：它被两个约束**夹**出来 —— 要让「满覆盖纹素活、3/4 邻域死」，
权重必须落在 `0.001 < w < 0.001/0.75 = 0.001333`。因此

```
0.064125 × (4/255) = 0.0010059 = 0.2565/255     ← 精确成立，已在 Lua double 下验证
```

**怎么改**：6 处裸字面量 → `K.OIT.*` / `K.GAIN.*`；常量表末尾用 `assert` 固化
「派生式必须精确复现锚点」「两条路径都必须落在窗口内」。

**断言形态**：`brightness: OIT alpha window is single-sourced and mirrors the host cutoff`
（变异：`ALPHA_SPOT` 改 0.06 → 立刻 FAIL 并指出偏差）。

---

### 2.2 2D 遮挡图集边长 1344（`K.ATLAS`）

**这个数是什么**：多光源（2×2 / 3×3 网格）时遮挡图集的边长；2×2 → 每格 672、3×3 → 448。

**谁定的**：宿主的 `TemporaryImageCacheSize`（1,600 万点）在「图集 + mip + 前景备份」
同时驻留时给出的**工程分配**——单图集 180 万点。它本来就有名字
（`OCC_SINGLE_BUDGET`），但 1344 是独立字面量，读者看不出二者同源。

**为什么是这个值**：`sqrt(1,800,000) = 1341.64` → 向上取 8 的整倍数 → **1344**。
反证：`1336² = 1,784,896` 装不下，`1344² = 1,806,336` 刚够（超预算 0.35%，
这正是「略超」的来处）。1344 同时能被 2、3 整除，切分不留余数行。

**怎么改**：`SIDE_3` 由 `BUDGET_DOTS / ALIGN` 运行期算出，`assert` 钉住
「== 1344 / 覆盖预算 / 最小性 / 可整除」；`OCC_SINGLE_BUDGET` 改为引用同一来源。

**断言形态**：`perf: 2D occlusion atlas side is derived from the dot budget`
（在 LuaJIT 里真跑常量表读运行时值；变异：预算改 200 万 → 自检立刻 FAIL）。

---

### 2.3 prox 深度编码（`K.PROX` / `prox::k*` / `VL_PROX_*`）

**这个数是什么**：camocc 是 RGBA8 纹理，浮点深度要先压成 `code`，再摊到三个通道：

```
code = depth × 0.48 + (0.01 | 0.51)
r = sat(3c−2)   g = sat(3c−1)   b = sat(3c)      解码 raw = (r+g+b)/3
```

**为什么这样编码**：三条通道互为「移位拷贝」——任一 `c` 恰好有一条通道不饱和，
三种情形下三者和恒等于 `3c`，所以均值能无损还原（误差只剩 8bit 量化 ≤ 0.5/255）。
`0.5` 是「是不是 VRM 角色」的标志位，两侧各留 0.01 保护带；三通道均值的量化误差
最坏 `1/510 ≈ 0.00196`，远小于 0.01，标志位不会被翻转。

**雷区**：`0.48` 与 `19_render.lua` 的 `cam_comp = 0.48` **同值不同义** ——
一次全局替换会同时改坏编码与相机补偿。现在两者各有名字。

**怎么改**：五处副本（Lua / C++ / `beamatlas.hlsl` / `ps_vl_lfpack.hlsl` /
`vl3d_bridge_shaders.h` 内嵌 CS）全部改为命名常量；C++ 侧 `constexpr` + 四条
`static_assert`；HLSL 侧用 `const float`（**不用 `static`**：仓库自带的 FXC lint
明确禁止，会走 lazy-init 代码路径）。

**断言形态**：`raster: prox depth codec is identical in Lua / C++ / HLSL and round-trips`
——真跑常量表读 `K.PROX`，与四份副本逐个比对；编解码窗口内不许有裸字面量；
「编码 → 8bit 量化 → 解码」往返误差 ≤ 1/255。
（变异：lfpack 的 `VL_PROX_STEP` 改 0.50 → FAIL 并指出 `prox.STEP drifted … 0.5 != 0.48`。）

---

### 2.4 抖动常量（φ 家族 / IGN）

**这些数是什么**：切片抖动的相位增量与通道偏移。

**谁定的**：`1/φ`、`1/φ²`、`φ²/(1+φ²)=(5+√5)/10`、`1/ρ`（塑料数）是**数论常数**；
`52.9829189 / 0.06711056 / 0.00583715` 是 Jimenez 2014（CoD:AW）公开的 IGN 系数，
属于**外部给定**。

**为什么用 φ**：φ 是「最难被有理数逼近」的无理数（三段定理），其倍数落点最均匀 ——
这就是「相邻切片错开相位、不出年轮条带」的可检验理由。实测 64 个切片的落点间隔
0.0081~0.0213，紧贴 1/64；换成 `0.6` 这种有理数立刻塌成零间隔。

**怎么改**：`VL_PHI_INV / VL_DITHER_G / VL_DITHER_B / VL_R2_INV_RHO / VL_IGN_A|BX|BY`。

**断言形态**：`hlsl: dither constants are the golden-ratio family and stay decorrelated`
——数值身份（8 位小数截断，容差 5e-9）、分层性（并自带判别力自检）、
三通道环距 > 1/4、IGN 在 8×8 块上 64 级不退化且均值 ≈ 0.5。

---

### 2.5 特征光距 L_char（`K.LCHAR`）

**这个数是什么**：亮度归一化的分母（`k_gain_base = 强度/100 × 0.155 / L_char`）。

**谁定的**：三套**各自独立的感知标定**，不是同一公式的三种写法：

| 路径 | 公式 | 位置 |
|---|---|---|
| 屏幕空间一般路径 | `clamp(R_fixed·(0.12 + 0.36·r²), 42, 260)` | 15_frame_prepare |
| 相机光源·平行光 | 固定 `195` | 16_geometry_mip |
| 相机光源·点/聚光 | `clamp(600·tan(min(1.15, 半角))·0.32, 22, 200)` | 16_geometry_mip |

**为什么没有合并**：合并会改变现有画面 —— 超出「整理常量」的范围。本次只做
命名 + 记录 + 断言，并把三条路径唯一共用的屏幕半径上限 `520` 收成一处。

**断言形态**：`brightness: L_char keeps its three estimators inside one envelope`
——固定值 `195` 必须落在另外两条的窗口内，且能被它们的输入复现
（`base_ratio = 0.842` 或半角 `0.792 rad`）；`R_MAX` 必须真的会夹取。
（变异：`PAR_FIXED` 改 250 → 不可达 → FAIL。）

---

### 2.6 宿主上限（`K.HOST`）与显式降级

**这些数是什么**：

| 常量 | 值 | 谁定的 |
|---|---|---|
| `CACHE_DOTS` | 16,000,000 | AviUtl2 默认 `TemporaryImageCacheSize`（超了会被 LRU 驱逐 → 画面闪烁，不报错） |
| `TEX_MAX` | 4096 | D3D11 纹理/RTV 边长硬上限（建 4097 直接失败） |
| `TEX_CAP` | 2048 | **本项目自设**，给「图集 + mip + 前景备份」同时驻留留余量 |
| `CAMERA_EDIT_MODE` | 2 | 宿主枚举：`camera_mode == 2` 即「編集用視点」 |

**改动**：5 处渲染路径改为读表；新增 `vl_limit_note()` 显式降级日志（每个对象每类
只报一次、仅 `log_mode` 开启时、全程 `pcall`），挂在两处真正会悄悄降低画质的地方：

* 图集被宿主 `image_max` 压低 →
  `要求 2560 → 実際 1080、上限 1080`（沙箱实测输出）
* 单遮挡物图集被像素预算/边长上限压低

⚠ `4096` 在本项目里另有几种无关用法（PE 页扫描块、UTF-16 平面步长、结构体尺寸上界、
光栅 splat 上限），本次**只**替换了「纹理边长」语义的那几处，没有全局替换。

---

### 2.7 编辑视点相机回退（`camera_mode == 2`）

**问题**：官方文档写明该模式下 `camera_param` 不反映编辑视点，所以脚本经由
VL3DBridge / FFI 读实时相机。

**改动**：读不到时**仍然回退**（回退必须保留），但改为出声：
`体积光（3D）v3.17：读不到编辑用视点的相机，已回退到时间轴相机计算（安装 VL3DBridge 后可跟随）`。

**断言形态**：`runtime: edit-camera fallback is announced once instead of silently mismatching`
——用 `bridge=False` 复现「没装 VL3DBridge」→ 必须且只报一条、渲染仍照常；
桥接可用时不报；时间轴模式不报。

---

## 3. 现在的常量表

```
K = {
  OIT   : DISCARD, EDGE_KILL_F, MIN_WEIGHT, BASE_SPOT, BASE_DEFAULT,
          ALPHA_SPOT, ALPHA_DEFAULT, (W_SPOT, W_BASE — 运行期派生)
  GAIN  : CALIB, QUANT, REF_INTENSITY, CAM_COMP_SPOT
  ATLAS : BUDGET_DOTS, ALIGN, (SIDE_3 — 派生)
  PROX  : STEP, BASE_MESH, BASE_CHAR, FLAG, CHAN, OFF_R/G/B
  LCHAR : R_MAX, BASE, RATIO2, MIN_FIXED, MAX_FIXED, PAR_FIXED,
          SPOT_GAIN, SPOT_COEF, ANG_MAX, MIN_SPOT, MAX_SPOT
  HOST  : CACHE_DOTS, TEX_MAX, TEX_CAP, CAMERA_EDIT_MODE
}
```

着色器侧对应：`beamatlas.hlsl` 的 `VL_PROX_* / VL_PHI_* / VL_IGN_* /
VL_SHADOW_SOFT_DIST / VL_SOFT_MIN_F / VL_SOFT_MAX_F`；
C++ 侧 `src/VL3DBridge/src/vl3d_bridge_prox.h`。

---

## 4. 怎么复现这些结论

```bash
# 1) 重建脚本（确定性：同一输入必然同一 sha256）
python3 src/VolumetricLight3D/build.py

# 2) 逐位金标准（49 个数组，含中间缓冲）
python3 sandbox/tools/golden.py capture /tmp/base.npz     # 在改动前的 commit 上抓
python3 sandbox/tools/golden.py compare /tmp/base.npz     # 改动后比对

# 3) 全量沙箱（118 项）
python3 sandbox/run_checks.py
```

对照基线可以直接用 git 取历史产物，不必重建：

```bash
git show 610042c:Script/VolumetricLight3D.obj2 > Script/VolumetricLight3D.obj2
python3 sandbox/tools/golden.py capture /tmp/base.npz
git checkout -- Script/VolumetricLight3D.obj2   # 或还原成当前构建
python3 sandbox/tools/golden.py compare /tmp/base.npz
```

> 注意：金标准比较的是**构建产物**，改完 `modules/*.lua` 或 `shaders/*.hlsl`
> 必须先 `build.py` 再比对，否则比的是旧脚本。

---

## 5. 本次没有做的事（以及为什么）

| 项 | 原因 |
|---|---|
| 合并 L_char 的三条路径 | 会改变现有画面；需要重新标定，属于「改行为」而非「整理常量」 |
| 清理 PE 结构偏移（`08/09/13` 的几百个数字） | 宿主二进制布局，🔒 项；只能加表 + 加断言，动了会直接崩，不在本轮 |
| 全局替换 `4096` / `2048` / `0.48` | 它们各自有 3~4 种语义，全局替换是**制造** bug |
| 统一 HLSL 与 Lua 的常量来源 | Lua 与 HLSL 无法共享符号；当前用「契约用例对齐数值」替代，代价是仍需手工同步一处 |

---

## 6. 局限（如实说明）

沙箱是**纯 CPU** 环境：LuaJIT 执行脚本、`g++` 编译 C++ 桥接、HLSL 由转译器翻成
C++ 后由 NumPy 光栅化，**没有 GPU / D3D11**。因此本文能证明的是：

* 脚本几何、图集、相机遮挡深度、WBOIT 权重、3D 合成在 CPU 侧**逐位一致**；
* 118 项断言全绿，其中 9 项是新增的常量契约，每项都做过变异测试。

**不能**替代的是：真机 D3D11 下的像素级复核。建议在 AviUtl2 里抽 3 个场景
（普通平行光 / 相机 3D 聚光 / 前景遮挡 holdout）各出一帧对比图，确认与 v3.17 无异。
