# 体积光（3D）· 各模块全面分析与评估

> **后续勘误（2026-10-29）**：本文原始 85 项运行和图像数值保留为历史记录。后续分支审查发现旧沙箱
> 低 alpha 片元阈值过高、多图集切片错误复用最后一批纹理，且旧 RGBA8 预览不能用于 HDR 亮度测量。
> 相关修正、重测结果、两种假设模型与局限见 [v3.14 分支审查](BRANCH_REVIEW_262ea6ab.zh.md)。
> 本轮进一步发现旧版非零起点测试是假阳性，修正模拟场景帧和加载故障后主线/候选各 91 PASS；参见 [v3.01～v3.06 历史分析](HISTORY_301_306.zh.md)。
> 因此下文旧预览的亮度、形状和遮挡数据不构成与真机逐像素一致的证明。

> 对象：`CylnaNangong/AviUtl2_VolumetricLight3D` @ `b179e64`（`VolumetricLight3D.obj2 v3.13` / `VL3DBridge v1.21`）
> 方法：**沙箱实跑**（LuaJIT 执行真实 `.obj2`、HLSL 转译为 C++ 后执行、g++ 编译桥源码）+ 静态审阅 + 85 条自动化用例
> 第二轮（2026-10-29）新增：**静态接口校验**（AviUtl2_Astra 规则移植，见 §3.5）——cbuffer 布局 ⇄ Lua 调用点、属性注释严格语法；**宿主 API 契约层**（官方文档 ⇄ 脚本 ⇄ 模拟宿主，见 §3.5(c)）
> 结论先行：**工程完成度极高（A-），算法设计优秀，主要风险集中在“单文件 9555 行”的可维护性与宿主内存读取的兼容性。**

---

## 0. 评估摘要

| 模块 | 规模 | 完成度 | 质量评级 | 一句话评价 |
| --- | --- | --- | --- | --- |
| `Script/VolumetricLight3D.obj2` | 9555 行 / 451 KB / 70 函数 | ★★★★★ | **A-** | 把「解析宿主 → 真 3D 光束 → 双通道遮挡 → 融合」塞进一个无依赖脚本，工程难度极高；代价是体量与耦合 |
| 内嵌 HLSL（14 段） | 1.4 千行 | ★★★★★ | **A** | `beamatlas` 是核心：236 个 uniform、解析式投影雅可比、WBOIT 感知的 alpha 编码，工业级 |
| `src/VL3DBridge`（C++17） | 4.0 千行 | ★★★★☆ | **A-** | 干净的“可选加速层”，33 项内置自测；Windows 专有部分（D3D11 CS / 插件 ABI）无法在 Linux 验证 |
| 语言包（4 个 `.aul2`） | 4×212 行 | ★★★★★ | **A** | 87 个键全量覆盖、带 Tips 提示、UTF-8/CRLF 规范 |
| 构建与发布 | CMake + mingw 脚本 + ini | ★★★★☆ | **B+** | 可复现性尚可；缺少 CI、版本号分散在 3 处、`sandbox` 未被主 README 索引 |

**历史测试结果（旧沙箱，85 项：85 PASS / 0 FAIL / 0 SKIP / 0 WARN，104 s / 2 核 CPU）**

> 两个原先挂 `WARN` 的疑点都已在真机上裁决并写回规则（`--hide@` 的求值语义与多规则合并，见 §2.2）：
> `--hide@` 条件按官方规范 `(変数名)(比較演算子)(数値)` 求值、同一目标的多条规则按「或」合并。

| 组 | 用例 | 时间 | 覆盖点 |
| --- | --- | --- | --- |
| `checks_01_script` | 15 | 0.1 s | 版本、47 参数/6 分组、默认值 vs README、**属性注释严格语法**、**设定项结构（标签唯一/分组闭合/hide 目标）**、hide 顺序与宿主语义、缓存释放、安全红线 |
| `checks_02_language` | 6 | 0.0 s | 4 个语言包解析、87 键全覆盖、Tips、编码、`[Effect]` 映射 |
| `checks_03_hlsl` | 17 | 4.5 s | 14 段 HLSL 编译 + 逐段数值行为 + **静态 uniform 布局门禁**（含 3 例变异测试）+ 入口点/寄存器检查 |
| `checks_04_runtime` | 16 | 42 s | 全流程执行、3 种光源、边界（未选/自身/空层/0 强度）、HUD 门控、受限沙箱、多帧复用、预算 |
| `checks_05_bridge` | 10 | 0.0 s | C++ 自测、SHA-256/白名单、网格缓存、阴影/相机深度光栅器、非法输入 |
| `checks_06_features` | 9 | 34 s | 2D/3D 遮挡、AutoBlend 全链路、颜色/衰减/长度、雾/风/种子、变换、程序化光源 |
| `checks_07_perf` | 5 | 21 s | 帧时、切片线性度、16M 缓存上限、着色器派发数、绘制批处理 |
| `checks_08_api` | 7 | 2.0 s | **宿主 API 契约**：函数/实参数/枚举键全部对官方文档、`obj` 字段表恰好一致、返回值个数探针、`getpixel` ⇄ `getpixeldata`、两条缓冲路径一致 |

---

## 1. 架构与数据流

### 1.1 运行时调用链（沙箱实测，逐帧）

```
vl_run_frame()                                    ← AviUtl2 每帧调用一次（obj2 主入口，含重入保护）
├─ vl_bf_reset()                                  重置 130+ 个 VL_BF 字段
├─ vl_bf_prepare()                                读参数 → 相机基 → 光源坐标系 → 光源图层捕获
│   ├─ get_camera_basis()                         obj.getoption("camera_mode"/"camera_param")
│   ├─ inspect_or_suppress_camera_lights()        br.get_camera_lights() / 内存直读
│   ├─ obj.load("layer", ln)                      把光源图层抓进 "object"
│   └─ scan_pool_from_object()                    ps_vl_pool → 8×8 统计 → 阈值矩形/填充率
├─ vl_bf_capture()                                "object" → cache:vl3d/src（或用水面/程序化形状）
├─ vl_bf_mip()                                    ps_vl_prep（阈值 knee）+ 2×ps_vl_blur（±20 高斯）
├─ vl_bf_occlusion()                              2D 图集（ps_vl_pack＋occ_blur）/ 3D shmap / camocc
├─ vl_bf_render()                                 每切片 → beamatlas 瓦片 → obj.drawpoly()
└─ AB.run_foreground_blend()                      （可选）ab_mask_proj/env_init/blur/sil_h/sil_v/shade
```

实测一帧（480×270、40 切片、彩窗光源）：

```
shader_calls = ['ps_vl_pool', 'ps_vl_prep', 'ps_vl_blur', 'ps_vl_blur', 'beamatlas', 'beamatlas']
drawpoly     = 2 次（共 66 个四边形）
日志         = "@info 体積光（3D）v3.13 GPU=true 光源=レイヤー1(320x320) 遮蔽=OFF
                アトラス=1080x1080x2 スライス=66(dz=16.4) 頂点=264 固有充填=0.65"
```

### 1.2 关键设计取舍（从代码读出的意图）

| 决策 | 实现 | 评价 |
| --- | --- | --- |
| **光源 = 任意图层** | `obj.load("layer", n)` 抓帧 → 池化 → 阈值矩形 | 极灵活（彩窗/文字/视频都能发光），代价是每帧一次 `load` |
| **体积 = 切片 + 预乘光束图集** | N 个世界空间四边形，纹理来自 `beamatlas` 瓦片 | 兼容 AviUtl2 的 3D OIT，无需深度缓冲 |
| **遮挡走双通道** | 2D 图层签名图集 + 3D 模型阴影图 | 覆盖“2D 角色遮挡”和“真 3D 模型投影”两类需求 |
| **重计算外包给可选插件** | `obj.module("VL3DBridge")`，缺失时纯 LuaJIT 回退 | 零安装可用，装了更快——很好的降级设计 |
| **确定性抖动** | PCG3D + 黄金比切片层化 + 有序抖动 | 消除“年轮”带状伪影（changelog v3.08~v3.13 的主线） |

---

## 2. 模块 A：`Script/VolumetricLight3D.obj2`

### 2.1 体量与结构

| 指标 | 数值 |
| --- | --- |
| 行数 / 字符 | 9555 / 451 484（含 BOM + CRLF） |
| 顶层 `local function` | 70 个（另有 9 个 `AB.*` 方法） |
| `pcall(...)` 保护点 | **172 处**（其中 17 处 `pcall(require,"ffi")`） |
| 注释行 | 583 行，含 60+ 条版本变更记录（v2.2x → v3.13） |
| 内嵌 HLSL 块 | 14 段，约 1.4 千行 |
| `obj.*` API 调用 | 共 257 处：getvalue 56 / clearbuffer 54 / setoption 35 / pixelshader 25 / load 19 / getpixel 18 / getpixeldata 15 / copybuffer 9 / putpixeldata 7 / getinfo 6 / draw 3 / setfont 3 / getoption 3 / drawpoly 2 / module 1 / setanchor 1（`luasurface.scan` 统计，已剔除注释与字符串） |
| 缓存键 | 14 个 `cache:vl3d/*`，全部在帧尾释放（有专门用例校验） |

**评价**：单文件承载全部逻辑，好处是「复制即安装、零依赖、无加载顺序问题」，也是 AviUtl2 生态的通行做法；
坏处是**可读性/可测性/协作成本**随体量线性恶化（9555 行已接近一次阅读的上限）。

### 2.2 参数系统（`--track/--select/--check/--color/--string`）

* 实测声明 **47 个参数、6 个分组**（形 12 / 光 4 / 遮蔽設定 6 / 前景なじませ 14 / 霧 5 / 品質 6），
  与 README §5 参数表 **逐项一致**（用例 `obj2: parameter surface` 断言默认值）。
* **28 条** `--hide@` 规则（覆盖 26 个参数）全部只引用已声明参数、且都出现在被控参数的声明之后，
  无死引用、无空条件（用例 `--hide@ expressions` / `--hide@ rules target parameters declared before them`）。
* 参数在脚本内以**全局变量**读取（AviUtl2 语义），因此必须用 `nil` 判断而非 `false`；
  代码中 `if P.x == nil then P.x = 默认值 end` 的模式贯穿始终——正确且必要。
* **坑（沙箱踩到并已记录）**：`--check` 返回 Lua **布尔**，`P.show_info = (show_info == true or show_info == 1)`；
  若宿主传 `0`/`0.0`，Lua 认为真值但 `== 1` 不成立 → HUD 行为与预期相反。文档已提示用布尔。

**严格语法复核（第二轮，Astra 规则移植）**

对 47 个参数逐条套用 AviUtl2_Astra 的属性校验规则（用例 `obj2: property annotations obey the strict
AviUtl2 grammar`），当前 `v3.13` **全部通过**：

| 检查项 | 结果 |
| --- | --- |
| 27 个 `--track`：`min ≤ 初值 ≤ max`、step ∈ {1, 0.1, …, 1e-9} | ✅ 实测用到的 step 只有 `1` 与 `0.1` |
| 11 个 `--select`：首项 `标签=默认值`、选项值唯一、默认值∈选项 | ✅ 无重复值/重复标签 |
| 6 个 `--check`：初值 ∈ {true,false,0,1} | ✅ 均为 `true/false` |
| 1 个 `--color`：`nil` 或 0–0xffffff | ✅ 用 `nil` |
| 2 个 `--string`、6 组 `--group` | ✅ 引数个数正确 |
| 属性 ID 重复声明 / Astra 方言混入（`--#include`、`local x = 1 --track@_`） | ✅ 均无 |

**两个曾存疑的写法，均已在真机 + 官方规范两侧裁决**

1. **用数值比较 `--check` 参数（14 处）——✅ 成立（13 条已真机实测，1 条待同法确认）**：
   `ab_enable==0` 13 次、`auto_slice==1` 1 次。
   官方规范（[docs.aviutl2.jp/lua](https://docs.aviutl2.jp/lua/)「設定項目の非表示条件を定義」）明确：
   条件は `(変数名)(比較演算子)(数値)` の形 —— 即**条件上下文里数值比较就是规定写法**。
   实测（2026-10-29，中文 UI，附截图）：勾选「启用前景融入背景」时 14 个子项全部显示；取消勾选后
   **整组只剩复选框本身** ⇒ `ab_enable==0` 生效，说明 `true/false` 声明的 `--check@`
   在隐藏条件上下文里仍按 **0/1 数值**参与比较。另一方向同样成立：勾选「自动动态切片精度」时
   「手动切片数」整行消失、取消勾选后出现（值 64）⇒ `auto_slice==1` 生效。
   至此 **14 条全部真机确认**，且 `==0`（13 条）与 `==1`（1 条）两个极性都覆盖，
   因此「boolean 声明 + 数值比较」是**官方规范写法**而非兼容巧合。
2. **同一参数出现两条 `--hide@`——✅ 按「或」合并**：`occ_list`（`occ_mode==0` + `occ_target<2`）、
   `ab_list`（`ab_enable==0` + `ab_target<1`）。官方示例本身就是同一变量挂两条规则
   （`--hide@pos:chk==0` 与 `--hide@pos:filter==1`）；实测进一步确认：把「融入目标图层」设为
   **指定图层范围或编号**（`ab_target=1`，使 `ab_target<1` 为假）后取消勾选，「目标图层范围」
   **仍然消失** —— 只有 `ab_enable==0` 那条能造成这个结果，两条规则都生效。

**由官方规范修正的三处「想当然」**（此前按 Astra 的方言推断，已按官方文档改正并写入用例）：

| 项目 | Astra 方言 | **官方规范**（docs.aviutl2.jp/lua） | 影响 |
| --- | --- | --- | --- |
| `--track@` 参数个数 | 4–5 个 | 最多 **7** 个：`項目名,最小,最大,デフォルト,移動単位,ゼロ値名称,操作倍率`（倍率 ≤ 1.0） | 本项目只用 5 个参数，无影响；但「多余参数=错误」的规则已放宽 |
| `--select@` 默认值 | 必须有 `=デフォルト値` | **可省略** | 仍推荐写明；省略时不再判错 |
| `--data@` 语法 | `項目名,サイズ`（0–16000） | `登録名:サイズ`（≤ 16KB），**没有项目名** | 本项目未使用 `--data@`，规则已改正 |
| `--hide@` 运算符 | `== ~= >= <= > <` | **只有 `==` `~=` `>` `<`**（无 `>=`/`<=`） | 本项目 28 条规则只用 `==` `>` `<`，合规 |

另外官方规范限定「条件里可引用的项目」只能是 **check / select / file / folder**（外加特殊条件 `filter`），
**不含 trackbar**。本项目 28 条规则引用的全是 `check`/`select`，合规；该约束已写进用例
（`obj2: --hide@ conditions follow the documented grammar`）。

**改进建议**
1. 参数表可由一个 `P_SPEC` 表统一驱动（默认值 + 类型 + 分组），减少 200 行重复的 `if nil then`。
2. 把「隐式默认值」改为显式 `local P = read_params({...})`，便于静态检查漏读参数（当前无自动化保障，只能靠人工）。
3. 把上述 14 条 `--hide@` 改成**与宿主类型无关**的写法（先按验证法确认真机行为，再决定是否改）。

### 2.3 宿主交互层（`obj.*` 模拟契约）

沙箱把 AviUtl2 的行为抽象成 17 个方法（详见 `README.zh.md` §4），实测出的**关键契约**：

| API | 实测行为 | 脚本的应对 |
| --- | --- | --- |
| `obj.load("layer", n)` | 失败返回 `false` | 24 处调用全部包在 `pcall` 内，并检查返回值 |
| `obj.getpixeldata(name,"rgba")` | 返回 `(ptr, w, h)`；缓冲区不存在返回 `nil` | 25 处使用，均先判 `ptr ~= nil` 再判尺寸 |
| `obj.copybuffer` | **是官方 API**（`docs.aviutl2.jp/lua` § obj.copybuffer，返回 `true/false`） | 脚本仍先探测 `type(obj.copybuffer) == "function"`（兼容旧版/禁用环境），缺失时走 `obj.load` 路径；两条路径沙箱都已跑通且**逐像素一致**（§3.5(c)） |
| `obj.module("VL3DBridge")` | 插件未装返回 `nil` | `vl3d_bridge()` 返回 `nil` → 纯 Lua 回退（用例覆盖 `bridge=False`） |
| `obj.getoption("gui")` | 导出时 `false` | HUD/未选中提示自动隐藏（v3.13 特性，用例覆盖） |
| `obj.clearbuffer(name,w,h)` | 分配/重分配 | 14 个缓存键，帧尾全部缩到 1×1（**内存不泄漏**，用例覆盖） |

**评价**：宿主兼容性是本项目最扎实的部分之一——`pcall` 密度（172 处）+ 能力探测（`type(...) == "function"`）
让脚本在「插件缺失 / 老版本宿主 / 沙箱 Lua 环境」下都能退化运行。沙箱实测：把 `os/io/debug` 全部置 nil 后
脚本依旧正常出光（用例 `restricted script sandbox`）。

### 2.4 光源捕获与统计（`scan_pool_from_object`）

* 用 `ps_vl_pool` 把光源图层缩到 `gw×gh`（`kf = ceil(max(lw,lh)/256)`），
  `.g` 通道 = 平均亮度/覆盖率，`.a` 通道 = 阈值命中率。
* Lua 侧 FFI 直接扫描这块小图，得到 **base_ratio（填充率）+ 阈值矩形 u0..v1**。
* 实测：彩窗光源 `固有充填=0.65`、纯白光源 `1.00`；**纯白 1×1 亮度阈值**场景会 `found_thr=false` 并静默退出（预期）。
* 优点：以极低代价（8×8 量级）替代全图扫描，避免每帧读 100 万像素。
* 风险：矩形包围盒对**斜向长条型**光源会显著高估填充率（`base_ratio` 参与 alpha 增益），
  沙箱实测 `stained_window` 0.65 与 `uniform` 1.00 之间的差异会直接改变亮度。

### 2.5 相机与光源模型（`get_camera_basis` / `mode_kind`）

这是全脚本最“物理”的部分，`mode_kind` 三态：

| `mode_kind` | 条件 | 投影方式 |
| --- | --- | --- |
| `0` 平行光 | `spread ≈ 0` 或相机平行光 | `t_ray = ((P−w)·Wn) / u_wn` |
| `1` 点光/锥光（虚拟光圈） | `|spread| ≥ 0.25`、`ray_mode=1/2` | 虚拟发光点 `sp` 后退 `r0 = R_fixed/tan(spread/2)`，沿 `sp→P` 与光平面求交 |
| `2` 点光（双平面裁剪） | `spread < 0` 或 `ray_mode=2` | 追加反向平面 `use_plane2`，只保留光平面之间的楔形空间 |

实测数据（默认参数 `ray_mode=0, spread=22`）：`sp = (−249.8, −322.5, 285.9)`、
`dS_wn = −285.9`、`u_wn = −1.0`、`mode_kind = 1` —— 与上面公式完全吻合。

**评价**：用「虚拟光圈后退」把锥光统一成三次投影，避免了逐切片重算光锥；数学正确、代价极低。
**风险**：`R_fixed`、`u_wn` 的 1e-3 钳位意味着极端 `spread`（±165°）与几乎垂直于光平面的方向会退化，
建议在 UI 层加提示（当前仅在 HUD 里体现）。

### 2.6 几何构建（切片 / 网格细分 / drawpoly）

* 自适应切片：`auto_slice=true` 时按屏幕高度与 `dz` 求 `slices`（实测 64→66，`dz=16.4`）；
  点光/锥光会用 6×6 结点网格（`GRID_N=6`）计算每顶点法线，交给宿主做逐顶点光照。
* 批处理：平行光 66 个四边形只发 **2 次 `drawpoly`**（用例 `draw call batching` 断言 ≤4）；
  点光因每顶点法线不同会更多——这是合理的权衡。
* 顶点在本地坐标系（`* inv_zoom`）提交，符合 AviUtl2 3D 对象约定。

**评价**：几何量可控（66×4=264 顶点），是这个方案能在 AviUtl2 上跑起来的关键。

### 2.7 遮挡系统（三图集）

| 通道 | 纹理 | 生成 | 用途 |
| --- | --- | --- | --- |
| 2D 前景 | `cache:vl3d/occ_b`（打包图集） | `ps_vl_pack` 把每个遮挡图层缩进 tile + `ps_vl_occ_blur` 双向模糊（`.b` 保留原始、`.a` 为柔化） | `beamatlas` 里用 `.b` 做“光线-平面求交”命中判定 |
| 3D 阴影 | `cache:vl3d/shmap`（默认 1280²） | `br.rasterize_shadow()`（C++ CPU / D3D11 CS）或纯 Lua 光栅 | `eval_shmap_bilinear()` 按 `prox`（距光源远近）给出可见度 |
| 相机深度 | `cache:vl3d/camocc`（1280×720） | `br.rasterize_camdepth()` | 轮廓保护（`char_cov`）、前景遮罩 |

实测（3D 立方体遮挡，`occ_mode=3`）：

```
日志：遮蔽=L3(OBJ)(m3,tri:12,C++)  shmap=1280x1280 覆盖率 27.7%
合成：alpha_mean 0.1481 → 0.1481（off）→ 对比 变暗 29.3（|Δalpha| 均值）
```

即**3D 遮挡真实生效**（会砍掉光束），并能在合成图里看到清晰的投射阴影。

**发现的两个真实缺陷**

1. **`occ_mode` 的语义在 HUD 与着色器之间不一致**：HUD 打印 `m3` 表示“3D 阴影+前景遮罩”，
   而 `beamatlas` 内 `do_shadow = (mode==1)||(mode==3)`、`do_fg_mask = (mode>=2)`
   —— 与注释一致，但 `occ_3d_mode=0`（自动）会把 OBJ 交给“纯深度比较”、VRM 交给“轮廓保护”，
   于是**同一份参数在不同模型类型下表现不同**，用户很难建立直觉。建议 HUD 直接显示解析后的
   `do_shadow/do_fg_mask/use_3d_occ` 布尔组合。
2. **`occ_list` 只接受空格/范围写法**（`"4 5"`、`"4-5"`），解析失败时静默退化为“0hit”，
   没有错误提示（只有 `need_manual_hint` 会写 HUD）。建议 `log_mode` 打开时补一条 `@warn`。
3. **对象大小陷阱（沙箱踩坑）**：`load_wavefront_obj_mesh()` 把 OBJ 坐标 ×100，
   所以一个「1 单位」的立方体在场景里是 ±100。写测试/使用教程时如果不说明，会得到“遮挡完全无效”的错觉
   （`prox≈0` → 阴影恒为 1）。已在 `sandbox/README.zh.md` 记录，并给 `write_test_obj(..., scene_units=True)`。

### 2.8 AutoBlendLight（`前景なじませ`，14 参数 / 5 着色器）

链路：`run_foreground_blend()` → `ps_vl_ab_mask_proj`（把前景层投影成 mask）→
`ps_vl_ab_env_init`（环境取色）→ 4×`ps_vl_ab_blur`（大半径环境模糊）→
`ps_vl_ab_sil_h/v`（可分离剪影）→ `ps_vl_ab_shade`（按 `ab_style/ab_bright/ab_diffusion/
ab_bias/ab_color_mix/ab_saturation/ab_luma_link` 合成），最终输出到 framebuffer 再 `beamatlas`。

实测：开启后 shader 序列为
`pool, prep, blur, blur, ab_blur, ab_mask_proj, ab_env_init, ab_blur×5, ab_sil_h, ab_sil_v, ab_shade, beamatlas×2`，
**14 个额外派发**、无宿主错误、图像与关闭时有可见差异。

**评价**：这是本项目的“第二特性”，实现完整、参数语义清晰（14 个参数都有 `--hide` 门控）。
**风险**：它是**破坏性**的（写回 framebuffer），因此必须在 `beamatlas` 之前执行并且状态可恢复；
脚本用 `obj.setoption("drawtarget","framebuffer")` + `blend=none` + `culling=0` —— 沙箱确认 `draw_state` 会被复原。

### 2.9 HUD / i18n / 日志

* 4 语 i18n 表（ja/zh/en/de）覆盖 `unselected / self_layer / empty_layer / src_desc / hud / log_info`；
  用例验证每个 key 都有 4 种语言。
* HUD 通过 `obj.setfont/setanchor/drawtext` 绘制；`show_info=false` 或 `getoption("gui")==false` 时不绘制（两条用例覆盖）。
* `log_mode` 控制 `@info` 输出：实测关闭后 `prints` 为空，打开后有初始化/状态/耗时 3 行。

**评价**：日志格式清晰（版本 / GPU 后端 / 光源 / 遮蔽 / 图集 / 切片 / 顶点 / 填充率 / 缓存），
排查时信息量足。**建议**：把 `@info` 改为可分级（`@info/@warn/@err`），`occ_list` 解析失败应有 `@warn`。

### 2.10 缓存与生命周期

* 14 个 `cache:vl3d/*` 键 + `object` 缓冲区；用例断言**帧尾无 >4 像素的残留**、
  且峰值缓存 **≤ 16 000 000 dot**（AviUtl2 `TemporaryImageCacheSize` 默认值）：
  实测最坏场景（640×360、`res_mode=0`）+ 3D 遮挡 = 2.59 M dot（0.16× 上限），余量充足。
* 图集尺寸自适应：`ATLAS = clamp(min(want, max_w, max_h)/8)*8`，最小 512；实测 1080×1080、tile 135、pad 2。
* `_VL_PERSIST` 跨帧保存网格/光照/相机缓存，配合 `VL_BF_KEYS` 做选择性失效——设计干净。

### 2.11 安全与合规

* 无 `os.execute` / `io.write` / `WriteProcessMemory` / `VirtualProtect`（用例 `safety_lint` 断言）；
  对 aviutl2.exe 只做 `ReadProcessMemory` 读取（`resolve_live_scene_layer_object` 等）。
* 白名单闸门在 C++ 侧（SHA-256 + PE 时间戳 + 可选 PE 文件大小），默认 `strict_whitelist_only=0`
  允许未知版本只读降级——对用户友好，但**意味着任意版本都会尝试内存读取**。
* **风险**：内存直读依赖硬编码偏移（`[inst+0x40]`、`+0xa8`…）。AviUtl2 一旦更新就可能读到垃圾数据；
  当前用「机器码/符号验证 + 失败即回退」缓解，仍建议在 HUD 里显式提示“内存直读已启用/已回退”。

---

## 3. 模块 B：14 段内嵌 HLSL

### 3.1 逐段评估

| 着色器 | 规模 | 作用 | 沙箱验证结论 |
| --- | --- | --- | --- |
| `beamatlas` | 1038 行 / 236 uniform | 每切片光束（投影、遮挡、雾、能量、抖动、WBOIT 编码） | ✅ 确定性、颜色比保持、遮挡开关生效、无 NaN |
| `ps_vl_pool` | 815 字符 | 光源统计（8×8 池化） | ✅ 白→满覆盖、暗→0 |
| `ps_vl_prep` | 869 | 阈值 knee + 颜色源/单色化 | ✅ 双模式正确、95% 阈值可拒绝 60% 亮度 |
| `ps_vl_pack` | 931 | 遮挡图集打包（tile 内缩、越界保留原值） | ✅ tile 内外像素均正确 |
| `ps_vl_blur` | 984 | ±20 高斯（归一化 / 直通） | ✅ 平坦图不变量、单点能量外扩 |
| `ps_vl_occ_blur` | 1018 | 遮挡专用双向模糊（`.b` 原样保留） | ✅ 原始通道逐字节不变 |
| `ps_vl_white` | 1054 | 程序化光源（平场/圆/径向） | ✅ 圆内 255、圆外 0、径向渐变连续 |
| `ps_vl_water` | 733 | 水面焦散光源 | ✅ 同帧确定性、随 `time` 变化 |
| `ps_vl_ab_blur` | 424 | AutoBlend 大半径高斯（可归一化） | ✅ 平坦色保持 |
| `ps_vl_ab_env_init` | 406 | 环境取色（mask 区抑制） | ✅ mask 区 <40、外部 >150 |
| `ps_vl_ab_mask_proj` | 365 | 前景 mask 投影 | ✅ 参与链路无异常 |
| `ps_vl_ab_sil_h/v` | 460/495 | 可分离剪影 | ✅ 参与链路无异常 |
| `ps_vl_ab_shade` | 525 | 前景着色合成 | ✅ 输出整帧、alpha 非全零 |

### 3.2 算法亮点（`beamatlas`）

1. **解析式投影**：不做逐像素 raymarch，而是把每个切片四边形投到光源平面，
   再用 `dq_tx/dq_ty` 的雅可比把屏幕导数映射到 shadowmap / camocc 的采样足迹，
   避免“砂状 speckle”（v3.08 的痛点）。
2. **深度编码**：C++ 光栅器把 `prox = 1 − t_ray/L` 编码成 `r,g,b` 三台阶（每段 1/3），
   解码端 `(c.r+c.g+c.b)/3` 还原。信息量 ~22 bit（GPU 版用 `pack_prox_val` 的 24 bit），
   在 8-bit RGBA 纹理里已经接近极限——很聪明的 host 存储方案。
3. **相机遮挡的轮廓保护**：`char_cov`（`raw>0.50` 位）区分 VRM 角色与普通道具，
   `surf_in_front` 只保护光源前方的表面，防止“角色在光束后面却把光束挖空”。
4. **WBOIT 感知的 alpha**：`out_a = emit_s * ka_eff`，`ka = 0.24`，`min_a = 1/255`；
   脚本注释明确写出宿主合成式 `Out = (1−Reveal)·Σ(c·w)/Σw`，说明作者逆向过宿主实现。
5. **抗带状**：黄金比切片层化 `frac(si·0.618 + jitter)` + 有序抖动 `d_q` → 消除 8-bit 量化条带。

### 3.3 已发现的可移植性问题（转译器相关，非源码 bug）

* 使用 `uint3`、`>>` 位运算、`SampleLevel` 三参、`float4(0,0,0,0)` 整数实参等，
  在 DXC/glslang 之外的工具链上易失败；本沙箱的转译器需专门处理（已记录在
  `sandbox/vl3dsb/hlsl/transpile.py` 注释）。对 FXC（AviUtl2 使用 D3DCompile）是合法的。
* 静态检查：无 `switch` / `discard` / `while`（用例 `fxc_lint`），说明作者有意保持 FXC 兼容子集。

### 3.4 建议

1. 给 `beamatlas` 加一份「uniform 布局表」（`p0..p26` 已有注释，但 `occ[32]` 的 16-float 布局只在 Lua 侧），
   否则改一处 Lua 打包就会静默错位。可行做法：在 C++ 与 Lua 共用一个头文件常量。
2. `ps_vl_blur` / `ps_vl_occ_blur` 的 Lua 端只传 6 个 float，cbuffer 声明 8 个——**当前无害**：
   静态分析确认函数体最远只读到 `p1.y`（下标 5），`p1.zw` 从未被读取，所以这 2 个 float 是纯填充
   （同类还有 `ps_vl_ab_blur` / `ps_vl_ab_env_init` / `ps_vl_ab_mask_proj` 各 1 个）。
   但「声明 8 / 实传 6」在手工维护下很容易演化成真错位，现已由静态用例做门禁
   （§3.5），并保留运行时 `shader_param_mismatches` 作为第二道网。

### 3.5 静态接口校验（第二轮新增，Astra 规则移植）

第二轮把 [AviUtl2_Astra](https://github.com/korarei/AviUtl2_Astra) 在 `astra build` 期执行的
两类校验**重写为 Python 用例**（不依赖 Astra 二进制；Astra 只有 Windows 发布、无 `check`
子命令、无结构化诊断输出）。实现：`sandbox/vl3dsb/hlsl/interface.py` + `checks_01/03`。

**(a) cbuffer 布局 ⇄ 25 个 Lua 调用点**

三个量：`declared`（cbuffer 浮点总数）、`required`（函数体真实读到的最远下标 +1）、
`supplied`（调用点第 4 个实参的浮点个数）。实测（`v3.13`）：

| 着色器 | declared | required | supplied | 结论 |
| --- | ---: | ---: | ---: | --- |
| `beamatlas` | 236 | 236 | 236 | ✅ 字面量 108 项 + `for oi = 1, MAX_OCC*16` 追加 128 项（`occ[32]`），**逐一吻合** |
| `ps_vl_ab_shade` | 52 | 52 | 52 | ✅ |
| `ps_vl_ab_mask_proj` | 36 | 35 | 36 | ✅ 1 float 尾部填充未被读 |
| `ps_vl_ab_blur` | 12 | 11 | 12 | ✅ 1 float 尾部填充 |
| `ps_vl_pack` / `ps_vl_water` | 12 / 16 | 12 / 16 | 12 / 16 | ✅ |
| `ps_vl_prep` / `ps_vl_ab_sil_h` | 8 / 8 | 8 / 8 | 8 / 8 | ✅ |
| `ps_vl_blur` / `ps_vl_occ_blur` | 8 | 6 | 6 | ✅ 2 float 尾部填充（`p1.zw` 从未读取） |
| `ps_vl_ab_env_init` | 4 | 3 | 4 | ✅ 1 float 尾部填充 |
| `ps_vl_ab_sil_v` / `ps_vl_pool` / `ps_vl_white` | 4 | 4 | 4 | ✅ |

这套规则的价值在于**区分「未被读的填充」与「真的少传」**：只看 `declared vs supplied` 会把
5 个着色器误判为缺陷，只看 `required vs supplied` 又能精确放过它们、同时抓住任何真错位。
配套的变异测试（少传 / 多传 / 调用不存在的 shader）证明规则确实会报错。

**(b) 属性注释语法**：以**官方规范**（[docs.aviutl2.jp/lua](https://docs.aviutl2.jp/lua/)，2026-10-29 核对）
为准绳、Astra 的附加严格性为补充，47 个参数逐条通过（明细与三处修正见 §2.2）。用例：
`obj2: property annotations follow the AviUtl2 spec (docs.aviutl2.jp/lua)`、
`obj2: --hide@ conditions follow the documented grammar`、
`obj2: --hide@ multi-rule targets are OR'ed (spec + host verified)`。

**(c) 宿主 API 契约（官方文档 ⇄ 脚本 ⇄ 模拟宿主，2026-10-29 新增）**

(a)(b) 校验的是**脚本**；第三层校验的是**沙箱自己**。若 `host.py` 对某个 `obj.*` 的返回值个数或参数含义猜错，
脚本照样跑、像素照样出，但测的已不是真机上的 AviUtl2 —— 这类失真不会自己暴露。实现：
`vl3dsb/apidoc.py`（官方签名/键表的唯一事实来源，抄自 `docs.aviutl2.jp/lua`，文件头记录核对日期）
+ `vl3dsb/luasurface.py`（静态扫描 `obj.*` 调用/字段/字面量键）+ `vl3dsb/api_lint.py`（规则引擎），
用例见 `checks_08_api.py`（7 项，含 19 个返回值探针与「10 条缺陷 + 2 条合法行」的 linter 自检）。

当场揪出并修好的 **3 处「假阳性绿」**（全是模拟宿主的问题，脚本无责）：

| 现象 | 官方规范 | 修复前的后果 | 修复 |
| --- | --- | --- | --- |
| `obj.getinfo("image_max")` 返回 1 个值（`(1920,1080)` 元组） | 返回 **2 个**值 `max_x, max_y` | `local mw, mh = obj.getinfo("image_max")` 得到 `mw=table, mh=nil`，尺寸上限逻辑整条失效 | 解包成两个整数 |
| `obj.getinfo("clock")` 返回 `nil` | 返回开机以来秒数（number） | 脚本 perf 主路径 `type(c) == "number"` 恒假，只能落到 `os.clock` 回退 | 返回确定性浮点（`HostConfig(fake_clock=…)` 可固定） |
| `obj.copybuffer` 默认标记为「宿主不支持」 | **文档化 API** | 所有用例默认只测 `obj.load` 回退路径，真机主路径（帧缓冲备份）从未被执行 | 默认开启 + 用例证明两条路径逐像素一致 |

同时按文档修正：`setfont` 第 3 参数是**装饰类型 0–6**（旧代码误作 `weight`，AviUtl2 无字重参数）、
`draw` 是 8 参数（含 `rx,ry,rz`）、`drawpoly` 支持「顶点表 + `vertex_num`」与「扁平 20 浮点 + alpha」两种写法且禁止混用、
`load` 的 kind 共 12 种、`obj` 字段表**恰好** 38 个（模拟宿主曾多暴露一个未文档化的 `scene` 别名，已删除）。
新增规则：设定项**标签全局唯一**、分组必须闭合、`--hide@` 目标必须是「有值的设定项」，并各配变异测试。

**仍未被静态覆盖的部分**（诚实边界）：`VL_BF.occ_flat` 内部 16-float/对象的排布语义、
`beamatlas` 里 `p0..p26` 的字段含义、`--hide@` 的条件求值语义（风险 #10）——这三项只能靠
人工审阅或真机验证。

---

## 4. 模块 C：`src/VL3DBridge`（C++17）

| 文件 | 行数 | 职责 | 评估 |
| --- | --- | --- | --- |
| `vl3d_bridge_core.cpp` | 1730 | 桥引擎：PE/SHA-256 校验、白名单、SDK 解析、网格缓存、光照读取、模块导出表 | 结构清晰；`ReadProcessMemory` 全程只读 + 失败降级 |
| `vl3d_bridge_rast.cpp` | 1240 | CPU 光栅器（阴影柱 + 相机深度）+ D3D11 CS 执行器 | 三角形裁剪/子三角形光栅/大三角形队列，实现完整 |
| `vl3d_bridge_shaders.h` | 591 | 内嵌 HLSL（`cs_5_0`） | 与 CPU 路径逐项对齐（`pack_prox_val` 两侧一致） |
| `vl3d_bridge_selftest.cpp` | 556 | 33 项自测 | **可执行、全部 PASS**（沙箱实测） |
| `vl3d_bridge_test_capi.cpp` | 254 | `vl3d_test_*` C API（供非 Windows 构建） | 使 Linux 也能跑真机代码，价值极高 |
| `vl3d_bridge_plugin.cpp` | 194 | AviUtl2 插件入口（Windows） | 无法在 Linux 验证，仅审阅 |

### 4.1 沙箱实测（真实源码，g++ 12.2 -O2）

```
selftest        : rc = 0，33/33 PASS（SHA-256、UTF-16/代理对、PE 解析、白名单、
                  ABI 偏移 0xD0/0xD8/0xE0/0x118、网格 LRU、光栅器中心三角形、文本对齐 18 种）
mesh cache      : upload 返回 slot id（非布尔）；HasMesh(key,rev)==id；零三角形上传被拒
shadow raster   : 12 三角形全部接受、覆盖率 77.3%、r≤g≤b 三台阶单调、确定性、alpha 硬 0/255
plane 裁剪      : 光源平面后方几何被裁掉（tris=0），跨界几何产生 14 个裁剪三角形
camdepth raster : 近处 code 0.92 > 远处 0.68（距离单调）
非法输入        : sh_res<16、n_insts=0、未知 mesh_id 均安全返回，不崩溃
构建            : -Wall -Wextra 零警告；产物按源码 SHA-256 名称缓存
```

### 4.2 评价

* **架构分层正确**：脚本 → `obj.module("VL3DBridge")` → C++；插件缺失时逐级回退到纯 Lua。
* **性能设计合理**：D3D11 CS 走 GPU，失败则 CPU `-O3`；大三角形进队列避免 splat 爆量
  （`kLargeBboxThreshold=1024`、`kMaxLargeTris=4096`）。
* **可测性好**：`test_capi` + `selftest` 让“Windows 专有代码”之外的 90% 都能在 CI 跑。
* **风险**
  1. `#ifdef _WIN32` 的 GPU 路径（CS 编译、UAV/常量缓冲）**完全未被自动测试覆盖**——
     自测只在 CPU 路径。建议至少加“CS 源码字符串 → 编译”的冒烟测试。
  2. 全局单例 `GetGlobalBridgeEngine()` + `mgr.mtx` 单锁：多线程/多实例场景未定义行为（当前宿主是单线程，可接受）。
  3. 白名单 `allow_sha256` 只对**已发布**的 aviutl2.exe 生效，宿主更新后必然进入“未授权降级”——
     需要在发行说明里写清降级后的行为差异。

---

## 5. 模块 D：语言包（`Language/*.aul2`）

* 4 个文件（`Deutsch` / `English` / `community_de` / `简体中文`）各 212 行、**87 个键**、
  覆盖参数名 + 提示 + Tips；`[Language]` / `[Effect]` / `[VolumetricLight3D]` / `[Tips.*]` 结构完整。
* 用例验证：所有键在 zh/de 版本中都存在、值非空、非“未翻译”，编码 UTF-8 + CRLF + 无 BOM。
* 小问题：`light_color` 等颜色的键在 zh 包中保留日文写法 `光源X/光源Y/光源Z`（含 ASCII 轴号，属合理保留，
  用例已按此放宽）。`Deutsch` 与 `community_de` 两个德语包内容高度相似 → 建议合并或明确“社区修订”的差异说明。

---

## 6. 横切关注点

### 6.1 性能

| 维度 | 实测 / 设计 | 评价 |
| --- | --- | --- |
| 每帧着色器派发 | 6（平行光）～20（含 AutoBlend） | 极少，远低于 AviUtl2 单对象预算 |
| 绘制调用 | 2（66 四边形，平行光） | 批处理到位 |
| 缓存峰值 | 0.05 M（默认）～2.59 M dot（640×360 + 3D 遮挡） | 相对 16 M 上限余量 6~300× |
| 切片成本 | 16→48 切片耗时比 1.6~2.4×（线性） | 无超线性爆炸 |
| 主线程开销 | Lua 侧每帧一次全图 `load` + 网格上传（有 `_VL_PERSIST` 缓存） | 3D 模型仅在 pose/变换变化时重传 |

### 6.2 健壮性

* 172 处 `pcall` + 全部关键 API 的能力探测 → 单点失败不会中断整个 AviUtl2 帧。
* 边界实测：光源未选 / 自引用 / 空图层 / 强度 0 / 长度 0 / 阈值过高 / 无 FFI / `os=nil` / 受限沙箱 —— **全部优雅退出**且 `draw_state` 复原。
* 多帧复用（4 帧循环）：输出逐帧一致、无缓存泄漏 —— 说明状态机没有跨帧污染。
* 缺陷：`occ_list` 解析失败静默；内存直读偏移失效时仅靠 HUD 提示。

### 6.3 可维护性

* 优点：命名一致（`vl_bf_*` / `AB.*` / `P.*`）、缓存键集中、`VL_BF_KEYS` 白名单式失效、变更日志详尽（60+ 条）。
* 缺点：
  1. 9555 行单文件、70 个顶层函数，函数平均 130 行；最长的几个（`vl_bf_occlusion`、`vl_bf_render`）已超 400 行。
  2. 全局状态 `VL_BF` 有 130+ 字段，靠字符串键手工同步，字段增删容易遗漏（`VL_BF_KEYS` 已有 130 项）。
  3. 有些“魔法数字”缺注释（如 `1.08` 边距、`0.04` 偏移、`0.24` `wboit_ka`、`360.0` 增益）。
* 建议：抽出 `params.lua` / `occlusion.lua` / `beams.lua` 三段（用打包脚本拼回单文件），
  既保留“单文件发布”又获得可读性。

### 6.4 可测试性（本项目最大短板 → 沙箱已补）

* 仓库原有测试：仅 `vl3d_bridge_selftest.cpp`（C++ 侧）。
  **Lua 侧 9555 行、14 段 HLSL、4 个语言包此前没有任何自动化测试**。
* 本次沙箱补齐 85 条用例，形成「静态分析 → 静态接口门禁 → 单元行为 → 集成 → 特性 → 性能」六层。
* 遗留：Windows/D3D11 路径、真实 aviutl2.exe 内存读取无法离线验证（需 Windows CI + 真机）。

### 6.5 文档与发布

* 主 README 完善（四语、参数表、变更日志、安装步骤），但：
  1. 版本号在三处硬编码（`.obj2` 头部 / `--information` / README 徽标）——容易漂移（用例已能检测）。
  2. `sandbox/` 未在主 README 索引 → 建议加一节 “Testing / 测试沙箱”。
  3. 无 CI 配置（`.github/workflows` 不存在），C++ 自测与（现在的）Python 用例都未自动运行。

---

## 7. 风险清单（按严重度）

| # | 严重度 | 风险 | 触发条件 | 影响 | 缓解建议 |
| --- | --- | --- | --- | --- | --- |
| 1 | 🔴 高 | aviutl2.exe 内存直读依赖硬编码偏移 | 宿主更新 | 场景图层/相机识别失败 → 光源或遮挡失效 | 已有多级回退；建议 HUD 明确提示“内存直读失败”，并把偏移表集中到一处 |
| 2 | 🟠 中（原 🔴 高） | `beamatlas` 的 236 uniform 与 Lua 打包手工对齐 | 修改任一参数 | 静默错位、图像异常且无报错 | **已缓解**：静态用例解析 `local consts = {…}` 108 项 + `for oi = 1, MAX_OCC*16` 追加 128 项 = 236 = cbuffer 大小，漂移会立即 FAIL。剩余依赖：`VL_BF.occ_flat` 的内部排布仍需人读 |
| 3 | 🟠 中 | 3D 遮挡对“模型尺寸单位”极敏感（OBJ ×100） | 首次使用 | 用户以为功能坏了 | 文档写明；可视化 HUD 显示 `prox` 峰值 |
| 4 | 🟠 中 | `occ_list` 解析失败静默 | 手误 | 遮挡不生效 | 加 `@warn` |
| 5 | 🟠 中 | D3D11 CS 路径零自动测试 | 驱动/编译器差异 | 只能 CPU 回退（性能下降） | 加 CS 编译冒烟测试；保留 `force_cpu` |
| 6 | 🟡 低 | 单文件 9555 行 | 持续迭代 | 维护成本、review 困难 | 源码拆分 + 打包脚本 |
| 7 | 🟡 低 | 无 CI | 提交 | 回归靠人工 | GitHub Actions（C++ 自测 + `run_checks.py`） |
| 8 | 🟡 低 | 德语包重复 | 翻译维护 | 内容漂移 | 合并或标注差异 |
| 9 | 🟡 低 | 16M 缓存预算依赖默认值 | 用户改过 `TemporaryImageCacheSize` | 大分辨率 + 3D 遮挡可能超限 | 已在代码侧自适应，建议 HUD 显示峰值 |
| 10 | ✅ 已排除（原 🟡 低） | 14 条 `--hide@` 用数值比较 `--check@`；`occ_list`/`ab_list` 各有两条 `--hide@` | — | 无（曾被怀疑规则失效） | 官方规范明确规定条件为 `(変数名)(演算子)(数値)`；真机实测多次规则亦按「或」合并（§2.2）。保留用例防回归 |
| 11 | ✅ 已闭环 | 模拟宿主与官方文档不一致（返回值个数/参数含义/能力开关） | 沙箱升级、宿主行为记忆偏差 | 测试全绿但测的不是真机行为（**最危险的一类绿**） | 已由 `apidoc.py` + `checks_08_api.py`（7 项，含 19 个返回值探针与 12 条变异）门禁；文档键表更新需同步该文件 |

---

## 8. 改进建议（按投入产出排序）

**马上可做（1~2 小时）**
0. ~~确认 `--hide@` 语义~~ **已完成**：官方规范 + 真机实测都确认了数值比较与多规则「或」合并（§2.2），
   无需改动脚本。建议把这条结论留在代码注释里，避免下次又被当成 bug 反复怀疑。
1. `.github/workflows/ci.yml`：`g++` 编译自测 + `python3 sandbox/run_checks.py`（沙箱已备好 `--json`）。
2. 主 README 增加「测试 / Test sandbox」一节，指向 `sandbox/README.zh.md`。
2b. `vl3dsb/apidoc.py` 是官方文档的**手抄副本**：升级 AviUtl2 后若文档新增 `obj.*`/`obj.getinfo` 键，
    需同步该文件（`api: every obj.* call is documented and emulated` 会在脚本用到新键时立刻报错，作为提醒）。
3. `occ_list` 解析失败时输出 `@warn`；HUD 增加 `prox` 峰值显示。

**短期（1~2 天）**
4. 把 `beamatlas` 的 uniform 布局抽成 Lua/C++ 共享的常量清单，并用沙箱用例做门禁。
5. HUD 显示解析后的 `do_shadow / do_fg_mask / use_3d_occ` 与内存直读状态（成功/回退）。
6. 版本号单一来源（脚本头部常量），README 徽标由脚本生成。

**中期**
7. 源码拆分 + 打包（保持单文件产物），函数级注释补齐魔法数字。
8. 给 D3D11 CS 路径加“源码编译冒烟”测试，并保留 CPU 回退开关。
9. 缓存预算自适应：读取宿主 `TemporaryImageCacheSize`（若可读）并下调图集尺寸。

---

## 9. 附录：沙箱如何证明上述结论

| 结论 | 证据（可复现命令） |
| --- | --- |
| 脚本能完整跑通 | `python3 sandbox/run_checks.py --only "default parameters"` |
| 47 个参数默认值与 README 一致 | `--only "parameter surface"` |
| 14 段 HLSL 都能执行且行为正确 | `--only hlsl` |
| 3D 遮挡真实生效（含 C++ 光栅） | `--only occlusion` / `--only "shadow rasterizer"` |
| C++ 自测 33 项全过 | `--only "self-test"`（日志见 `out/bridge/selftest.log`） |
| 缓存不泄漏、不超 16M | `--only "budget"` / `--only "multi-frame"` |
| HUD 与导出态门控正确 | `--only "HUD gating"` |
| 受限 Lua 沙箱下仍可用 | `--only restricted` |
| 视觉产物 | `python3 sandbox/render_demo.py` → `out/demo/gallery.png` |
| cbuffer 布局与 25 个调用点一致 | `--only "cbuffer layout"`（metrics 里给出每个着色器的 declared/required/supplied） |
| 该静态规则确实会报错（变异测试） | `--only "interface lint rejects"` |
| 属性注释严格语法（Astra 规则） | `--only "property annotations"` |
| `--hide@` 的求值语义与多规则合并 | `--only "--hide@"`（metrics 给出 `multi_rule_targets` 与数值比较次数） |
| 设定项结构（标签唯一 / 分组闭合 / hide 目标） | `--only "structure"` |
| 宿主 API 契约（签名 / 键 / 返回值个数 / 两条缓冲路径） | `--only "api:"` |

> 所有 PNG（`out/demo`、`out/features`、`out/hlsl`、`out/bridge`）都是本轮沙箱真实渲染的结果；
> 此预览仅为 CPU 简化模型，不保证亮度、结构或遮挡与真机一致；见文首后续勘误。
