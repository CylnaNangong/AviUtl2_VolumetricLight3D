# 体积光（3D）测试沙箱 · 使用说明

`AviUtl2_VolumetricLight3D` 的**可复用离线测试沙箱**。它不依赖 Windows、AviUtl2、D3D11 或 GPU：
在本机用 **LuaJIT（与 AviUtl2 同款）** 真机执行 `Script/VolumetricLight3D.obj2`，用 **NumPy 编译器**
执行脚本内嵌的 14 段 HLSL，用 **g++ 编译仓库自带 C++17 源码** 提供 `VL3DBridge` 的原生光栅器，
从而复现从「参数 → 着色器 → 切片几何 → 合成」的完整一帧。

```
参数(47 个) → vl_run_frame() → pool/prep/blur(mip) → 遮挡(shmap/occ/camocc)
            → beamatlas(逐切片光束) → drawpoly(≤N 个四边形) → 合成预览
```

---

## 1. 目录结构

```
sandbox/
├── run_checks.py          # 测试运行器（发现并执行 checks/ 下全部用例）
├── render_demo.py         # 渲染示例图（out/demo/*.png + gallery.png）
├── requirements.txt       # lupa / numpy / pillow
├── vl3dsb/                # 沙箱核心
│   ├── obj2.py            # .obj2 / .aul2 解析器（参数、分组、hide 规则、HLSL 块、i18n）
│   ├── host.py            # AviUtl2 宿主模拟器（obj.* API + LuaJIT 运行时 + 缓冲区/缓存）
│   ├── native.py          # 编译并调用 src/VL3DBridge 的 C++17 测试库（ctypes）
│   ├── hlsl/              # HLSL→NumPy 转译器（transpile.py）与执行器（executor.py）
│   │   └── interface.py   # 静态接口校验：cbuffer 布局 ⇄ Lua 调用点（Astra 规则移植）
│   ├── raster.py          # 切片四边形光栅化（Weight-Blended OIT 近似）
│   ├── assets.py          # 测试素材：彩窗、剪影、渐变、单位立方体 .obj
│   ├── session.py         # 高层 API：build scene → run_frame → PNG/统计
│   └── harness.py         # 极简测试框架（@check / require / skip）
├── checks/                # 7 组、76 个用例
│   ├── checks_01_script.py     # .obj2 静态分析（参数语法/分组/默认值/hide 语义/i18n/安全）
│   ├── checks_02_language.py   # 4 个 .aul2 语言包完整性
│   ├── checks_03_hlsl.py       # 14 段 HLSL 编译、数值行为、静态 uniform 布局
│   ├── checks_04_runtime.py    # 脚本运行时行为（分支、边界、确定性、HUD）
│   ├── checks_05_bridge.py     # C++ 桥（自测、光栅器、白名单、网格缓存）
│   ├── checks_06_features.py   # 端到端特性（2D/3D 遮挡、AutoBlend、雾…）
│   └── checks_07_perf.py       # 性能与显存/缓存预算
└── out/                   # 全部产物（.gitignore 已忽略，可随时删除重建）
    ├── results.json       # 最近一次测试的机器可读结果
    ├── demo/ features/ hlsl/ bridge/ runtime/   # 各类 PNG / 日志
    └── build/native/      # 编译出的 libvl3d_test_<hash>.so
```

## 2. 环境与安装

```bash
python3 -m pip install -r sandbox/requirements.txt      # lupa(LuaJIT) / numpy / pillow
g++ --version                                          # 需要支持 C++17（仅 VL3DBridge 用到）
```

* Python **3.9+**；`lupa` 必须能加载 **LuaJIT 2.1**（`lupa.luajit21`，带 FFI）。
  只有 `lupa.lua51` 时脚本会退化到「无 FFI」路径，部分用例会 SKIP。
* 首次运行会自动用 g++ 编译 `src/VL3DBridge/{core,rast,selftest,test_capi}.cpp`，
  产物按源码 SHA-256 命名缓存到 `out/build/native/`，源码不变不会重复编译。
* 全部用例约 **2–3 分钟**（CPU 软件光栅 + NumPy 着色器，比真实 GPU 慢 2 个数量级；2 核 / 3 GB 实测约 100–110 s）。

## 3. 快速开始

```bash
cd sandbox

python3 run_checks.py                  # 跑全部 76 个用例，输出 PASS/FAIL/WARN + out/results.json
python3 run_checks.py --list           # 列出全部用例
python3 run_checks.py --only hlsl      # 名字含 "hlsl" 的用例
python3 run_checks.py --tags occ       # 按标签筛选（可有多个 --tags a,b）
python3 run_checks.py -v               # 失败时打印完整 traceback
echo $?                                # 0 = 全通过；1 = 有 FAIL；2 = 没有匹配用例

python3 render_demo.py                 # 渲染 7 张示例图 + 拼版 gallery.png
python3 render_demo.py --only fog,water
```

CI 里只需 `python3 run_checks.py --json out/results.json`：JSON 中每条用例都有
`status / seconds / metrics / artifacts`，便于趋势对比。

用例状态有四种：`PASS` / `FAIL` / `SKIP` / `WARN`。**只有 `FAIL` 会让退出码变 1**；
`WARN` 表示「源码里存在一个只能由真机裁决的问题」（例如下面 5.1 的 `--hide@` 求值语义），
它会被完整写进 `results.json` 的 `detail`，但不会阻塞 CI。

## 4. 作为库使用（最小示例）

```python
import sys; sys.path.insert(0, "sandbox")
from vl3dsb import assets, session
from vl3dsb.host import Layer
import numpy as np

# 场景：图层 1 = 彩窗光源；图层 3 = 前景剪影（2D 遮挡）
layers = [
    Layer(index=1, image=assets.stained_window(200, 200)),
    Layer(index=3, image=assets.silhouette(90, 150), x=40, y=30, z=-200),
]
r = session.run_frame(
    layers,
    {"src_layer": 1, "intensity": 180, "slices": 40, "occ_mode": 2},
    width=480, height=270,          # 屏幕（需与 AviUtl2 工程一致才会有相同 dz）
    layer_index=4,                  # 效果自身所在的图层号
)

print(r.stats())                     # {"alpha_mean":…, "quads":…, "peak_cache_px":…}
print(r.log)                         # 脚本 @info 日志（= AviUtl2 的日志窗口）
print(r.host.shader_calls)           # ['ps_vl_pool', 'ps_vl_prep', 'ps_vl_blur', …, 'beamatlas']
session.save_png(r.volume, "out/my.png")                       # 体积层原图（RGBA）
session.save_png(session.composite(bg, r.volume, gain=4.0), "out/comp.png")  # 合成预览
```

### 4.1 可用的 Python API

| 对象 | 说明 |
| --- | --- |
| `session.run_frame(layers, params, ...)` | 跑一帧，返回 `RenderResult(volume, host, seconds, log)` |
| `session.standard_scene(w,h,...)` | 标准场景（彩窗 + 剪影） |
| `session.composite(bg, volume, gain)` | 按 host 的 WBOIT 语义合成到背景上 |
| `session.shader_library()` / `native()` | 复用的着色器库 / 原生桥（带缓存） |
| `RenderResult.stats()` | alpha 均值/最大值/覆盖率、四边形数、缓存峰值 |
| `r.host.shader_traces[name]` | 每个着色器**最后一次**调用的 uniforms + 输入纹理（调试神器） |
| `r.host.buffers` | 帧结束时的全部缓冲区（`object` + `cache:vl3d/*`） |
| `r.host.shader_param_mismatches` | Lua 传入浮点数 ≠ HLSL cbuffer 大小的记录 |
| `vl3dsb.hlsl.interface.audit(shaders, text)` | **静态**比对 cbuffer 布局与全部 Lua 调用点，不跑任何代码 |

### 4.2 `Layer` 字段

| 字段 | 含义 |
| --- | --- |
| `index` | 图层号（与 AviUtl2 时间轴一致，1 在最上） |
| `image` | `(h,w,4) uint8`，**预乘 alpha**（与 AviUtl2 内部格式一致） |
| `kind` | `image` / `text` / `obj` / `vrm` / `water` / `camera` / `empty` |
| `x,y,z,rx,ry,rz,zoom,alpha` | 图层变换；`zoom` 为百分比（100 = 原始大小） |
| `sx,sy,sz,cx,cy,cz` | 默认 `None` = 宿主不支持（脚本会自动回退到 zoom/aspect，与真机一致） |
| `effects` | 效果参数表，例如 `{"解像度": 50}`、`{"モデルファイル": "a.obj"}` |
| `model_path` / `water_color` | `kind="obj"` 的模型路径 / `kind="water"` 的 `0xRRGGBB` |

> **OBJ 单位**：脚本的 `load_wavefront_obj_mesh()` 把 OBJ 坐标 ×100（Y 取反，可选 Z 取反），
> 所以 `assets.write_test_obj(path, size)` 的 `size` 是 **OBJ 单位**；想直接按场景单位给尺寸用
> `scene_units=True`（内部会 ÷100）。

### 4.3 `params` 取值规则（很重要）

* 名字 = `.obj2` 里的参数 ID（`src_layer`、`ray_mode`、`occ_mode`…），不是日文标签。
* `--track` 是数字；`--select` 是数字（选项序号）；`--check` 必须是 **Python 布尔**——
  传 `0.0/1.0` 会变成「Lua 里的真值」，与真机行为不一致（用例 `HUD gating` 就踩过这个坑）。
* 不传的参数取 `.obj2` 声明的默认值（与 `README` §5 参数表一致，用例会校验）。
  `src_layer` 默认 **0（未选择）**，所以任何想看到光束的调用都要显式给 `src_layer: 1`。

## 5. 沙箱覆盖了什么、没覆盖什么

**已真实执行（不是 mock）**

* `.obj2` 全部 9555 行 Lua：初始化、参数解析、`vl_run_frame` 全流程、日志/HUD、缓存释放、错误保护分支。
* 14 段内嵌 HLSL：逐条转译为 NumPy 并执行，含 `beamatlas` 的 236 个 uniform、遮挡解码、雾噪声。
* `src/VL3DBridge` 的 C++17 代码：内置 33 项自测、SHA-256/白名单门、网格缓存、CPU 阴影/相机深度光栅器。
* `.aul2` 语言包解析与覆盖率、`.obj2` 静态规则（默认值、hide 表达式、缓存释放、安全红线）。

**刻意近似（结论只在趋势上有效）**

| 项目 | 沙箱做法 | 影响 |
| --- | --- | --- |
| 3D OIT 合成 | WBOIT 近似：`Reveal *= (1-w)`，`rgb = Σ(c·w)/Σw` | 光的形状/颜色比例/遮挡正确，**绝对亮度偏高或偏低** |
| 深度权重 `w(z)` | 未实现（视为 1） | 前后切片次序正确，逐切片权重不精确 |
| D3D11 Compute Shader | 仅 C++ CPU 后端（`CPP_CPU`） | 结果应与 GPU 一致（自测覆盖），但慢 |
| AviUtl2 官方 SDK 内存读取 | `resolve_layer` 由 Python 场景表提供 | 无法验证对 aviutl2.exe 的内存布局 |
| `copybuffer` | 默认不支持（与 AviUtl2 一致），可 `copybuffer=True` 打开 | 两条代码路径都跑通 |
| 时间/帧 | 固定 `time`、可控 `frame`/`frame_s` | 动画类用例可复现 |

> 因此请把 PNG 当作「结构正确、亮度近似」的预览。审核亮度/色彩时请以真机为准。

### 5.1 静态接口校验（AviUtl2_Astra 规则移植）

沙箱除了「跑一帧然后看像素」，还有一层**不执行任何代码**的静态校验，规则来自
[AviUtl2_Astra](https://github.com/korarei/AviUtl2_Astra) 在 `astra build` 时执行的检查
（uniform 布局校验、属性注释校验）。这里是**规则移植**而不是依赖 Astra：Astra 只发布 Windows
二进制、没有 `check` 子命令、也没有结构化诊断输出，因此它的规则被重写成 Python，
可以在任何 Linux CI 上跑（实现见 `vl3dsb/hlsl/interface.py` 与 `checks_01_script.py`）。

**(a) cbuffer 布局 ⇄ Lua 调用点**（用例 `hlsl: cbuffer layout matches every Lua call site`）

三个数字决定结论，缺一不可：

| 量 | 含义 | 来源 |
| --- | --- | --- |
| `declared` | D3D11 cbuffer 的浮点总数 | `float4 x[n]` → `4*n`，累加 |
| `required` | 着色器**函数体真的读**到的最远下标 +1 | 扫描 `p1.y` / `occ[i]` 等访问，**排除 cbuffer 声明本身** |
| `supplied` | Lua `obj.pixelshader(...)` 第 4 个实参的浮点个数 | 解析实参表；变量表会顺着 `local consts = {…}` + `for oi = 1, MAX_OCC*16` 追加循环求值 |

判定：`supplied < required` → **FAIL**（着色器读到垃圾）；`required ≤ supplied ≤ declared` → PASS，
差额记入 `trailing_padding_floats`（未使用的尾部填充，无害但有记录价值）；`supplied > declared` → **FAIL**。
另有变异测试 `hlsl: interface lint rejects deliberately broken call sites`，用 3 个人造缺陷
（少传一个 uniform / 多传 / 调用不存在的 shader）证明这条规则真的会报错，而不是永远 PASS。

对当前 `v3.13` 的结论：

```
shader                declared  required  supplied   备注
beamatlas                  236       236       236   含 occ[32]×4 = 128 的追加循环
ps_vl_ab_shade              52        52        52
ps_vl_ab_mask_proj          36        35        36   1 float 尾部未使用
ps_vl_pack / water       12 / 16   12 / 16   12 / 16
ps_vl_ab_blur               12        11        12   1 float 尾部未使用
ps_vl_prep / ab_sil_h     8 / 8     8 / 8     8 / 8
ps_vl_blur / occ_blur        8         6         6   2 float 尾部未使用
ps_vl_ab_env_init            4         3         4   1 float 尾部未使用
ps_vl_ab_sil_v / pool / white  4     4         4
```

也就是说：**`beamatlas` 那条“最容易静默错位”的 236 段打包现在有机器门禁了**。

**(b) 属性注释语法**（用例 `obj2: property annotations obey the strict AviUtl2 grammar`）

| 指令 | 规则 |
| --- | --- |
| 全部 | ID 必须匹配 `[A-Za-z_]\w*`；**同名属性不得重复声明**；注释必须独占一行（不允许 `local x = 1 --track@_` 这种 Astra 方言）；不得出现 `--#include/--#define/--#if` 等 Astra 预处理指令 |
| `--track@` | 必须 4 个参数（标签,min,max,初值）+ 可选 1 个 step；`min ≤ 初值 ≤ max`；step 必须是 `10^-k`(k≤9)；参数过多报警 |
| `--check@` | 初值只能是 `true/false/0/1` |
| `--select@` | 首项必须是 `标签=默认值`；选项整数值唯一、标签唯一且非空；**默认值必须是某个选项** |
| `--color@` | `nil` 或 `0..0xffffff` |
| `--string@` | 标签 + 初值 |
| `--hide@` | 目标必须已声明且**声明在前**；空条件报错；运算符只允许 `== ~= >= <= > <` |

附加一条**信息性**用例 `obj2: --hide@ host semantics audit`（状态 `WARN`）：它会列出两类
「源码本身无法裁决、必须在 AviUtl2 里看一眼」的情况——
① 同一参数有两条 `--hide@`（「指定图层」`occ_list` / 「目标图层范围」`ab_list`）；② 用 `==0` / `==1`
去比较 `--check@` 参数（共 14 条，主要是「启用前景融入背景」`ab_enable`）。详见 `docs/ANALYSIS.zh.md` 风险 #10。

> **中文 UI**：属性面板的语言由 `Language/` 下的 `.aul2` 决定。界面为简体中文时显示的是 8.1 节的中文列，
> 例如「启用前景融入背景」「融入目标图层」「前景遮挡模式」，而不是上面这些日文原文。

## 6. 扩展：写一个新用例

```python
# sandbox/checks/checks_08_mine.py
from vl3dsb.harness import check, require
from vl3dsb.session import run_frame

@check("mine: 我的检查", tags=["mine"])
def mine():
    r = run_frame([], {"src_layer": 1}, width=160, height=90)
    require(r.volume is not None, "没有产出几何")
    return {"alpha_mean": r.stats()["alpha_mean"]}   # 返回 dict = PASS + metrics
```

* 发现规则：`checks/checks_*.py` 自动加载，`name` 唯一即可。
* `require(False, "原因")` → FAIL；`skip("原因")` → SKIP；抛异常 → FAIL（`-v` 打印 traceback）。
* 返回值里的 `artifacts` 会被写进 `results.json`，方便把 PNG 挂到 CI 报告上。

## 7. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `SKIP bridge: … native build unavailable` | 没有 g++ 或 g++ < C++17；装 `g++` 后重跑 |
| 全部用例 `no beam` | `src_layer` 仍为默认 0，或 `intensity`/`length` 为 0（脚本会静默退出） |
| 输出全黑但日志有 `GPU=true` | 光源图层本身全黑（阈值 `threshold` 高于画面亮度），换 `assets.stained_window()` |
| 遮挡「无效」 | 2D 遮挡要求遮挡层在光源平面 **前方**（`z` 更小，如 -200）；3D 遮挡需要 `kind="obj"` 且 `occ_list` 指定图层号 |
| 编译很慢 | 首次 g++ 编译约 20 秒；之后按源码 hash 复用 `out/build/native/libvl3d_test_*.so` |
| `lupa` 报 FFI 不可用 | 安装带 LuaJIT 的 lupa（`lupa.luajit21`），或接受相关用例 SKIP |
| 有一条 `[WARN] obj2: --hide@ host semantics audit` | **正常**，这是提示去真机确认 `--hide@` 条件语义（见 5.1 节），不影响退出码 |

## 8. 参考：参数速查（47 个，默认值来自 `.obj2`）

`形(12) 光(4) 遮蔽設定(6) 前景なじませ(14) 霧(5) 品質(6)`

| 分组 | 参数（ID : 默认） |
| --- | --- |
| 形（12） | `src_layer:0` `ray_mode:0`(平行) `fill_mode:0` `direction:55` `pitch:44` `screen_dir:true` `spread:22` `light_x:0` `light_y:-300` `light_z:1400` `length:2500` `decay:55` |
| 光（4） | `light_color`(颜色) `intensity:100` `threshold:15` `cam_light_mode:0`(自动) |
| 遮蔽設定（6） | `occ_mode:0`(关) `occ_3d_mode:0`(自动) `occ_target:0`(全部) `occ_list:"4 5"` `occ_soft:8` `occ_strength:100` |
| 前景なじませ（14） | `ab_enable:false` `ab_target:0` `ab_list:"4-5"` `ab_bright:85` `ab_color_mode:2` `ab_style:0` `ab_strength:100` `ab_diffusion:35` `ab_bias:-60` `ab_color_mix:100` `ab_saturation:100` `ab_luma_link:true` `ab_env_blend:30` `ab_env_blur:160` |
| 霧の設定（5） | `fog:0` `fog_scale:220` `wind_dir:25` `wind_speed:45` `seed:0` |
| 品質（6） | `res_mode:1`(标准) `auto_slice:true` `slices:64` `view_mode:0` `show_info:false` `log_mode:true` |

`ray_mode` 0=平行光源 / 1=点光源 / 2=聚光；`occ_mode` 0=关 / 1=仅 3D 阴影 /
2=仅前景遮罩 / 3=3D 阴影+前景遮罩（推荐）；`cam_light_mode` 0=自动（防过曝）/1=跟随相机光 /
2=禁用相机照明 / 3=原样受光。`light_color` 为 `0xRRGGBB` 整数，默认 `0xFFFFFF`。

### 8.1 中文 UI 对照表（`Language/简体中文.VolumetricLight3D.aul2`）

AviUtl2 的界面语言为**简体中文**时，属性面板显示的是下表的「中文 UI」列（不是日文）。
本表由脚本参数与语言包**程序化对照生成**，47 个参数无一缺失；分组名同样会汉化。

| 分组（日文 → 中文） | 参数 ID | 日文标签 | 中文 UI | 选项（中文 = 值） |
| --- | --- | --- | --- | --- |
| 形 → **光束形态** | `src_layer` | 光源レイヤー | **光源图层** |  |
|  | `ray_mode` | 光の出方 | **发射方式** | 平行光=0；点光源放射=1；向焦点收束=2 |
|  | `fill_mode` | 光の中身 | **光束质感** | 体积柔光=0；纯色实心=1 |
|  | `direction` | 方向 | **平面方向** |  |
|  | `pitch` | 手前角度 | **朝前倾角** |  |
|  | `screen_dir` | 画面基準 | **按画面方向基准** |  |
|  | `spread` | 拡散角度 | **扩散角度** |  |
|  | `light_x` | 光源X | **光源X** |  |
|  | `light_y` | 光源Y | **光源Y** |  |
|  | `light_z` | 光源Z | **光源Z** |  |
|  | `length` | 長さ | **光束长度** |  |
|  | `decay` | 減衰 | **距离衰减** |  |
| 光 → **光线与颜色** | `light_color` | 光の色 | **指定光色（留空取原图）** |  |
|  | `intensity` | 強さ | **发光强度** |  |
|  | `threshold` | しきい値 | **亮度阈值** |  |
|  | `cam_light_mode` | カメラ光源連携 | **摄像机光源联动** | 自动抗过曝补偿(推荐)=0；同步跟随摄像机光源(位置与颜色)=1；屏蔽摄像机3D光照(纯自发光)=2；关闭联动(原样受光)=3 |
| 遮蔽設定 → **前景遮挡** | `occ_mode` | 前景遮蔽 | **前景遮挡模式** | 关闭=0；仅真实3D遮光投影=1；仅前景轮廓遮挡=2；3D投影 + 前景遮挡(推荐)=3 |
|  | `occ_3d_mode` | 3Dオブジェクト処理 | **3D物件处理** | 自动(VRM轮廓保护 + OBJ真实前后)=0；真实3D前后深度(保留前方光束)=1；轮廓清晰保护(裁切模型前方光束)=2；忽略3D物件(仅处理2D图层)=3 |
|  | `occ_target` | 遮蔽対象 | **遮挡对象范围** | 自动检测前方全部图层=0；自动(光源与本图层之间)=1；手动指定图层编号=2 |
|  | `occ_list` | 指定レイヤー | **指定图层** |  |
|  | `occ_soft` | 影の柔らかさ | **阴影柔和度** |  |
|  | `occ_strength` | 遮蔽の強さ | **遮挡强度** |  |
| 前景なじませ → **前景融入背景** | `ab_enable` | 前景なじませを有効化 | **启用前景融入背景** |  |
|  | `ab_target` | なじませ対象 | **融入目标图层** | 自动检测前方全部2D图层=0；指定图层范围或编号=1 |
|  | `ab_list` | 対象レイヤー指定 | **目标图层范围** |  |
|  | `ab_bright` | ベース明るさ | **基础亮度** |  |
|  | `ab_color_mode` | カラーモード | **采光色彩模式** | 单色采光=0；双色(左右分区)=1；体积光+背景双色联动(推荐)=2 |
|  | `ab_style` | ライトスタイル | **边缘光样式** | 柔和渐变(逆光包裹)=0；柔和渐变(常规受光)=1；轮廓描边光(边缘高光)=2 |
|  | `ab_strength` | ライト強さ | **边缘光强度** |  |
|  | `ab_diffusion` | 拡散幅 | **边缘扩散宽度** |  |
|  | `ab_bias` | 偏り(外⇔内) | **光晕内外偏移** |  |
|  | `ab_color_mix` | 自然補正 | **色彩自然修正** |  |
|  | `ab_saturation` | 彩度 | **边缘光饱和度** |  |
|  | `ab_luma_link` | 背景明るさ連動 | **随场景亮度自动联动** |  |
|  | `ab_env_blend` | 環境なじませ量 | **背景环境色融合度** |  |
|  | `ab_env_blur` | 環境ぼかし | **环境采样模糊度** |  |
| 霧の設定 → **烟雾扰动** | `fog` | 霧 | **烟雾浓度** |  |
|  | `fog_scale` | 霧の大きさ | **烟雾尺度** |  |
|  | `wind_dir` | 風向き | **风向角度** |  |
|  | `wind_speed` | 風の速さ | **流动风速** |  |
|  | `seed` | シード | **随机种子** |  |
| 品質 → **品质与显示** | `res_mode` | 解像度 | **图集分辨率** | 高精细=0；标准=1；轻量（1/2）=2 |
|  | `auto_slice` | 自動動的精度 | **自动动态切片精度** |  |
|  | `slices` | スライス数 | **手动切片数** |  |
|  | `view_mode` | 視点追従 | **切片朝向** | 自动（支持3D摄像机与编辑视角）=0；侧面切片（X轴）=1 |
|  | `show_info` | 画面に状態表示 | **画面显示状态HUD** |  |
|  | `log_mode` | ログに出力 | **输出日志** |  |

> 语言包文件名必须放在 AviUtl2 的 `Language\` 目录（如 `C:\ProgramData\aviutl2\Language\`）；
> 若面板仍显示日文，说明语言包未加载或被同名的其他包覆盖（用例 `aul2: [Effect] mapping` 会校验包与脚本的对应关系）。

完整的模块级分析见 `docs/ANALYSIS.zh.md`（架构、算法、性能、风险与改进建议）。
