# 体积光（3D）测试沙箱 · 使用说明

`AviUtl2_VolumetricLight3D` 的**可复用离线测试沙箱**。它不依赖 Windows、AviUtl2、D3D11 或 GPU：
在本机用 **LuaJIT** 执行 `Script/VolumetricLight3D.obj2`，将内嵌 HLSL 转译成 C++ 并用 **g++** 编译，
用 NumPy 管理图像缓冲，并编译仓库自带的 `VL3DBridge` C++17 原生光栅器。
这能跑通「参数 → 着色器 → 切片几何 → 近似合成」，但**不是 AviUtl2 真机渲染**。

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
├── compare_brightness.py  # 多版本 / 两种假设合成模型下的光束比较
├── ablate_brightness.py   # 配对消融 v3.13 alpha 编码与低 alpha 门
├── compare_screenshots.py # 截图区域测量与指定几何配准
├── probe_observed_scene.py # 按实机界面可见设置构造 CPU 假设场景
├── verify_aup2_brightness.py # 解析真实 .aup2 子合成并执行 3D OIT 跨版本亮度对比
├── requirements.txt       # lupa / numpy / pillow
├── vl3dsb/                # 沙箱核心
│   ├── obj2.py            # .obj2 / .aul2 解析器（参数、分组、hide 规则、HLSL 块、i18n）
│   ├── aup2.py            # .aup2 工程与 OpenGlow 解析器（多语言参数键映射与关键帧插值）
│   ├── luasurface.py      # 扫描 Lua 里的 obj.* 调用 / 字段读 / 字面量键（不执行）
│   ├── apidoc.py          # 官方宿主 API 签名与键表（docs.aviutl2.jp/lua 的转录）
│   ├── api_lint.py        # 宿主 API 契约 linter（脚本 ⇄ 文档 ⇄ 模拟宿主）
│   ├── config.py          # 被测试仓库与沙箱本身的路径
│   ├── host.py            # AviUtl2 宿主模拟器（obj.* API + LuaJIT 运行时 + 缓冲区/缓存）
│   ├── native.py          # 编译并调用 src/VL3DBridge 的 C++17 测试库（ctypes）
│   ├── hlsl/              # HLSL→C++ 转译器（transpile.py）与执行器（executor.py）
│   │   └── interface.py   # 静态接口校验：cbuffer 布局 ⇄ Lua 调用点（Astra 规则移植）
│   ├── raster.py          # 四边形近似光栅化（RGBA8 预览 / 两种浮点假设模型）
│   ├── compositor3d.py    # C++17+OpenMP 3D 摄像机透视 + 深度缓冲 + Shader #118 OIT 合成器
│   ├── assets.py          # 测试素材：彩窗、剪影、渐变、单位立方体 .obj
│   ├── session.py         # 高层 API：build scene → run_frame → PNG/统计
│   └── harness.py         # 极简测试框架（@check / require / skip）
├── checks/                # 12 组、107 个用例
│   ├── checks_01_script.py     # .obj2 静态分析（参数语法/分组/默认值/hide 语义/i18n/安全）
│   ├── checks_02_language.py   # 4 个 .aul2 语言包完整性
│   ├── checks_03_hlsl.py       # 14 段 HLSL 编译、数值行为、静态 uniform 布局
│   ├── checks_04_runtime.py    # 脚本运行时行为（分支、边界、确定性、HUD）
│   ├── checks_05_bridge.py     # C++ 桥（自测、光栅器、白名单、网格缓存）
│   ├── checks_06_features.py   # 端到端特性（2D/3D 遮挡、AutoBlend、雾…）
│   ├── checks_07_perf.py       # 性能与显存/缓存预算
│   ├── checks_08_api.py        # 宿主 API 契约（文档签名/键/返回值 ⇄ 模拟宿主）
│   ├── checks_09_raster.py     # 低 alpha 阈值、图集快照及十六进制 HLSL 回归
│   ├── checks_10_brightness.py # 非聚光源常量 OIT 令牌、聚光源隔离与 3D .aup2 亮度回归
│   ├── checks_11_build.py      # 模块化源码 ⇄ 单文件生成物同步、确定性及故障注入
│   └── checks_12_lightfield.py # 光场发布/接收：载荷格式、接收端数学、补丁幂等、雪（3D）端到端
└── out/                   # 全部产物（.gitignore 已忽略，可随时删除重建）
    ├── results.json       # 最近一次测试的机器可读结果
    ├── demo/ features/ hlsl/ bridge/ runtime/   # 各类 PNG / 日志
    └── build/native/      # 编译出的 libvl3d_test_<hash>.so
```

### 1.1 主脚本的模块化源码

`Script/VolumetricLight3D.obj2` 已改为由 [`src/VolumetricLight3D/`](../src/VolumetricLight3D/) 生成的单文件发布产物。
唯一编辑源是 **21 个有序 Lua 片段 + 14 个独立 HLSL 文件 + `manifest.json`**；它们不是运行时的
`require()` 模块，因此 AviUtl2 的安装方式、顶层局部变量作用域和文件数量均不变。
C++17 插件源码仍在 `src/VL3DBridge/`。模块职责和构建规则见[模块化源码说明](../src/VolumetricLight3D/README.md)。

```bash
# 仓库根目录：重建并检查；--check 不写文件，漂移时退出码为 1
python3 src/VolumetricLight3D/build.py
python3 src/VolumetricLight3D/build.py --check

# 只跑构建组（含漏登记/重复项/内容漂移故障注入）
python3 sandbox/run_checks.py --only "build:"
```

`checks_11_build.py` 会实际重组并比对全部字节，而不只检查文件是否存在；隔离输出位于 `out/build/`。
对拆分前的历史快照使用 `--repo` 时，若没有 `src/VolumetricLight3D/build.py`，该组会 SKIP，其余历史测试不受影响。

## 2. 环境与安装

```bash
python3 -m pip install -r sandbox/requirements.txt      # lupa(LuaJIT) / numpy / pillow
g++ --version                                          # 需要支持 C++17（仅 VL3DBridge 用到）
```

* Python **3.9+**；`lupa` 必须能加载 **LuaJIT 2.1**（`lupa.luajit21`，带 FFI）。
  只有 `lupa.lua51` 时脚本会退化到「无 FFI」路径，部分用例会 SKIP。
* 首次运行会自动用 g++ 编译 `src/VL3DBridge/{core,rast,selftest,test_capi}.cpp`，
  产物按源码 SHA-256 命名缓存到 `out/build/native/`，源码不变不会重复编译。
* 全部用例约 **2–3 分钟**（CPU HLSL 转译执行 + 软件光栅；本次 2 核环境实测约 124–130 s）。

## 3. 快速开始

```bash
cd sandbox

python3 run_checks.py                  # 跑全部 107 个用例，输出 PASS/FAIL/WARN + out/results.json
python3 run_checks.py --repo /path/to/other-checkout  # 用本沙箱检验另一个仓库快照
python3 run_checks.py --list           # 列出全部用例
python3 run_checks.py --only hlsl      # 名字含 "hlsl" 的用例
python3 run_checks.py --tags occlusion # 按标签筛选（可有多个 --tags a,b）
python3 run_checks.py -v               # 失败时打印完整 traceback
echo $?                                # 0 = 全通过；1 = 有 FAIL；2 = 没有匹配用例

python3 run_checks.py --only lightfield # 只跑跨脚本光场联动（含雪（3D）端到端，需 VL3DSB_SNOW3D）
python3 render_demo.py                 # 渲染 7 张示例图 + 拼版 gallery.png
python3 render_demo.py --only fog,water
```

`checks_12_lightfield.py` 里那 1 个雪（3D）端到端用例需要一份 Snow3D 仓库副本：默认依次查找
`../AviUtl2_Snow3D`、`../Snow3D`，也可用环境变量 `VL3DSB_SNOW3D=/path/to/repo` 指定；
找不到脚本时该用例记为 `SKIP`（不影响退出码）。

CI 里只需 `python3 run_checks.py --json out/results.json`：JSON 中每条用例都有
`status / seconds / metrics / artifacts`，便于趋势对比。

用例状态有四种：`PASS` / `FAIL` / `SKIP` / `WARN`。**只有 `FAIL` 会让退出码变 1**；
`WARN` 表示「源码里存在一个只能由真机裁决的问题」，它会写进 `results.json` 的 `detail` 但不阻塞 CI。
2026-10-29 修正模拟场景帧和 effect/raw 加载后，对本仓库 v3.13 与候选 v3.14 快照各执行 **91 PASS / 0 FAIL / 0 SKIP / 0 WARN**。
之前的非零起点测试因沙箱重置 `frame_s` 且桥忽略查询帧而是假阳性；修正后的测试使用场景帧、活动时间范围与负对照，但并未模拟真实嵌套工程。
这是 CPU 沙箱结果，不代表真机像素一致（详见 [分支审查报告](docs/BRANCH_REVIEW_262ea6ab.zh.md)和 [v3.01～v3.06 历史分析](docs/HISTORY_301_306.zh.md)）。
两个原有的 WARN 疑点已在 2026-10-29 用官方文档 + 真机实测裁决，见 5.1 节；
模拟宿主与官方文档的 3 处不一致见 5.2 节。

### 3.1 比较分支和自带素材

`--repo` 同时切换被测 `.obj2`、`Language/` 和 `src/VL3DBridge/`；编译产物仍在当前沙箱 `out/`。
`--script PATH` 只替换脚本，语言包和桥依然来自 `--repo`（默认本仓库）。输出 JSON 含被测路径。
例如把目标提交导出为**只读快照**，不必切换当前分支：

```bash
# 在仓库根目录；先 fetch 所需提交，然后：
mkdir -p sandbox/out/candidate
git archive <目标提交> | tar -x -C sandbox/out/candidate
python3 sandbox/run_checks.py --repo sandbox/out/candidate --json sandbox/out/candidate-results.json
```

用真实素材构造同一小场景，比较脚本变化（素材路径可位于仓库之外，**不会被复制或提交**）：

```bash
python3 sandbox/compare_brightness.py \
  --reference /path/to/v306.obj2 \
  --before Script/VolumetricLight3D.obj2 \
  --after sandbox/out/candidate/Script/VolumetricLight3D.obj2 \
  --window /path/to/window.jpg --character /path/to/character.png \
  --camera-probes --intensity-sweep --json sandbox/out/brightness.json
```

不提供 `--window/--character` 则使用沙箱生成素材；可省略 `--reference`。
脚本固定 256×144、缩放素材并自设参数，不会读取 `.aup2`；两种浮点模型不含真实宿主光照和深度权重。

若要直接加载真实 `.aup2` 工程的子合成（含关键帧插值、`OpenGlow` 与 3D 透视 + Shader #118 OIT 合成）进行跨版本逐版复现，可使用：

```bash
python3 sandbox/verify_aup2_brightness.py \
  --aup2 /path/to/project.aup2 --scene autoblend测试 \
  --assets /path/to/assets_dir \
  --scripts v3.07=/path/to/v3.07.obj2 v3.13=/path/to/v3.13.obj2 v3.14=Script/VolumetricLight3D.obj2
```
输出的亮度代理只用于对比**该模型和该场景**，不能直接声称真机提升百分比。

如需分离比例/常量 alpha 与 HLSL 低 alpha 门的作用，可运行：

```bash
python3 sandbox/ablate_brightness.py \
  --after sandbox/out/candidate/Script/VolumetricLight3D.obj2 \
  --window /path/to/window.jpg --character /path/to/character.png \
  --intensities 180 30 5 --json sandbox/out/brightness-ablation.json
```

它仅在 `out/` 中创建并自动清理脚本临时副本；不提供素材时使用沙箱素材。
这是配对反事实实验，保留其他着色器门和假设的宿主片元阈值，不代表真机完整合成。

实机截图可用 `compare_screenshots.py --help` 计算相同区域的屏幕像素代理，并可在显式给定缩放/平移后比较预览与播放器截图；它不自动保证同帧或同色彩管线。`probe_observed_scene.py --help` 使用界面上可见的一部分设置跑 CPU 参考场景，**不是** `.aup2` 重现。2026-10-29 截图的具体证据、配准参数与局限见 [分支审查报告](docs/BRANCH_REVIEW_262ea6ab.zh.md)。

用户提供 v3.00～v3.06 八份原版之后，可用 `compare_original_versions.py` 直接逐版执行并注入特定故障；`--versions-dir` 接受原版 `.obj2` 或上传改名的 `.txt`，`--window` 和 `--character` 可使用仓库外素材，`--candidate` 可与 v3.06 比较，`--hires` 追加大尺寸 shader 对照。输出和复现命令见 [历史分析报告](docs/HISTORY_301_306.zh.md)，本次 [机器结果](results/history-301-306-originals.json) 持久保留。测试使用模拟 framebuffer 和场景，不代表真实宿主或工程。

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
| `session.composite(bg, volume, gain)` | 把旧 RGBA8 **近似预览**叠到背景；不是宿主 WBOIT 的精确重现 |
| `r.assumed_emission("weighted" / "over")` | 计算两种假设合成下的预乘 float RGBA，可保留 HDR，结果仍不是实机测量 |
| `session.shader_library()` / `native()` | 复用的着色器库 / 原生桥（带缓存） |
| `RenderResult.stats()` | alpha 均值/最大值/覆盖率、四边形数、缓存峰值 |
| `r.host.shader_traces[name]` | 每个着色器**最后一次**调用的 uniforms + 输入纹理（调试神器） |
| `r.host.buffers` | 帧结束时的全部缓冲区（`object` + `cache:vl3d/*`） |
| `r.host.shader_param_mismatches` | Lua 传入浮点数 ≠ HLSL cbuffer 大小的记录 |
| `vl3dsb.hlsl.interface.audit(shaders, text)` | **静态**比对 cbuffer 布局与全部 Lua 调用点，不跑任何代码 |
| `vl3dsb.api_lint.lint(text, shader_names)` | **静态**比对脚本用到的 `obj.*` 与官方文档签名/键表，返回 `issues` / `notes` |
| `vl3dsb.luasurface.scan(text)` | 提取 `obj.foo(...)` 调用（含实参原文）、`obj.field` 读/写、字面量键 |

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
| RGBA8 预览 | 旧版近似按 `tex.a × drawpoly_alpha` 加权颜色并计算覆盖率 | 不能表示超亮 HDR；**既不是宿主精确 WBOIT，也不能据此比较亮度** |
| 浮点对照模型 | `weighted` 假设切片等深度权重；`over` 假设录制顺序的预乘叠加；都保留 HDR | 两种模型均非宿主实现，差异只说明假设模型下的趋势 |
| 深度、透视与照明 | 四边形只做正交投影；浮点模型不模拟真实相机光照 / 深度权重 | 形状、边缘、遮挡和颜色都有偏差，不能当真机像素结论 |
| 低 alpha 片元阈值 | 依据项目逆向记录，暂假设 `tex.a × drawpoly_alpha <= 0.001` 丢弃 | 此值不是官方 API 规范，不同宿主版本需核对 |
| D3D11 Compute Shader | 仅 C++ CPU 后端（`CPP_CPU`） | 无法验证 GPU 后端行为或与 CPU 逐像素一致 |
| AviUtl2 官方 SDK 内存读取 | `resolve_layer` 由 Python 场景表提供 | 无法验证对 aviutl2.exe 的内存布局 |
| `copybuffer` | **默认开启**（`obj.copybuffer` 是官方 API）；`copybuffer=False` 可强制走 `obj.load` 回退路径 | 两条路径逐像素一致（用例 `api: obj.copybuffer is documented+on, and both buffer paths agree`） |
| 时间/帧 | 固定 `time`、可控局部 `frame` 和起点 `frame_s`；模拟图层起止与桥查询采用场景帧 | 可复现模拟动画；不等于验证真实嵌套工程 |

> 不要把 PNG 或本沙箱的数值当作真机亮度、色彩或边缘一致的证明。审核观感请以同工程、同帧的 AviUtl2 导出为准。
> 每次 `obj.drawpoly` 已保存当时的图集快照；多于 64 切片时不会误把最后一批纹理用于全部切片。
> 快照是沙箱进程额外使用的内存，不计入模拟的宿主 `cache_pixels` 指标。

### 5.1 静态接口校验（AviUtl2_Astra 规则移植）

沙箱除了「跑一帧然后看像素」，还有一层**不执行任何代码**的静态校验。规则的**第一来源是 AviUtl2 官方
规范** [`docs.aviutl2.jp/lua`](https://docs.aviutl2.jp/lua/)（設定項目 / 非表示条件），
[AviUtl2_Astra](https://github.com/korarei/AviUtl2_Astra) 在 `astra build` 时执行的检查
（uniform 布局、属性注释）作为补充与交叉验证。这里是**规则移植**而不是依赖 Astra：
Astra 只发布 Windows 二进制、没有 `check` 子命令、也没有结构化诊断输出，而官方规范只描述、
不提供校验器——两者都被重写成 Python，可以在任何 Linux CI 上跑
（实现见 `vl3dsb/hlsl/interface.py` 与 `checks_01_script.py`）。

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

**(b) 属性注释语法**（用例 `obj2: property annotations follow the AviUtl2 spec (docs.aviutl2.jp/lua)`）

规则的**第一来源是官方规范** [`docs.aviutl2.jp/lua`](https://docs.aviutl2.jp/lua/)「設定項目」一节，
Astra 的附加严格性只作为补充（有冲突时以官方为准）。

| 指令 | 规则（官方规范 + 括号内为附加严格项） |
| --- | --- |
| 全部 | ID 匹配 `[A-Za-z_]\w*`；同名属性不得重复声明；注释必须独占一行（不允许 `local x = 1 --track@_` 这种 Astra 方言）；不得出现 `--#include/--#define/--#if` 等 Astra 预处理指令 |
| `--track@` | `項目名,最小,最大,デフォルト[,移動単位[,ゼロ値名称[,操作倍率]]]` —— **最多 7 个参数**；`min ≤ 初值 ≤ max`；step 必须是 `10^-k`(k≤9)；操作倍率 ∈ (0,1] |
| `--check@` | `項目名,初値`，初値 ∈ {0,1,true,false}；**用 0/1 时变量是 number，用 true/false 时是 boolean**（用例会统计两种类型） |
| `--select@` | `項目名[=デフォルト値],選択肢=値,…`；默认值**可省略**，写了就必须是某个选项；（附加）选项值/标签唯一 |
| `--color@` | `項目名,デフォルト値`；`nil`（可选透明）或 `0..0xffffff` |
| `--file@ / --folder@` | 只有 `項目名`，无初值 |
| `--string@ / --text@ / --font@ / --figure@ / --value@` | `項目名,デフォルト値` |
| `--data@` | `登録名:サイズ`（**没有项目名**，≤ 16 KB） |
| `--group:` / `--group` | `グループ名[,true/false]`；`--group` 单独出现 = 分组结束 |
| `--hide@` | 目标须已声明且**声明在前**；条件形如 `(変数名)(比較演算子)(数値)`，运算符**只有 `== ~=` `>` `<`**；条件可引用 check/select/file/folder 与特殊条件 `filter`（**不能引用 trackbar**）；条件留空 = 永远隐藏 |

**关于「同一参数挂多条 `--hide@`」**（用例 `obj2: --hide@ multi-rule targets are OR'ed`）：
官方示例本身就给同一变量写了两条（`--hide@pos:chk==0` + `--hide@pos:filter==1`），实测也确认是**按「或」合并**——
把「融入目标图层」设为「指定图层范围或编号」（使 `ab_target<1` 为假）后取消勾选「启用前景融入背景」，
「目标图层范围」仍然消失（只有 `ab_enable==0` 那条能做到）。因此 `occ_list` / `ab_list` 的双规则是**正确写法**，
用例只拦「同目标 + 同条件」的冗余重复。

> **真机核对记录（2026-10-29，AviUtl2 中文 UI，附截图）**
> ① 展开「前景融入背景」组：勾选「启用前景融入背景」时 14 个子参数全部显示；取消勾选后
> **整组只剩复选框本身**、14 个子参数全部收起 ⇒ `--hide@ab_*:ab_enable==0`（13 条）按数值比较有效，
> 即 `--check@` 声明为 `true/false`（boolean）时，隐藏条件上下文仍按 **0/1 数值**求值。
> ② 同一组内「融入目标图层 = 指定图层范围或编号」（`ab_target=1`，使 `ab_target<1` 为假）时取消勾选，
> 「目标图层范围」仍随之消失 ⇒ 同一目标的两条 `--hide@` 是**按「或」合并**，不是「后者覆盖前者」。
> ③ 「品质 → 自动动态切片精度」**勾选**时「手动切片数」整行不出现；**取消勾选**后该行出现（值 64）
> ⇒ `--hide@slices:auto_slice==1` 有效。到此 **14 条「数值比较 boolean `--check@`」全部真机确认**，
> 且两个方向都覆盖到了：`==0` 在未勾选时隐藏（①，13 条）、`==1` 在勾选时隐藏（③，1 条）。
> 官方规范同时明确 `--check@` 用 `true/false` 时变量是 **boolean**，用 `0/1` 时是 **number** ——
> 这解释了脚本正文为何写 `ab_enable == true or ab_enable == 1`（条件上下文数值化、正文保持布尔）。

> **中文 UI**：属性面板的语言由 `Language/` 下的 `.aul2` 决定。界面为简体中文时显示的是 8.1 节的中文列，
> 例如「启用前景融入背景」「融入目标图层」「前景遮挡模式」，而不是上面这些日文原文。

### 5.2 宿主 API 契约（官方文档 ⇄ 脚本 ⇄ 模拟宿主）

沙箱最容易被忽视的失真点不是脚本，而是**模拟宿主本身**：如果 `host.py` 对某个 `obj.*` 的返回值个数、
参数含义猜错了，脚本照样能跑、像素照样能出，但测的已经不是真机上的那个 AviUtl2。
`checks_08_api.py` 的 7 个用例把这条链路钉死（单一事实来源：`vl3dsb/apidoc.py`，
逐条抄自 [`docs.aviutl2.jp/lua`](https://docs.aviutl2.jp/lua/)，文件头记录核对日期 `2026-10-29`）：

| 用例 | 断言 |
| --- | --- |
| `api: every obj.* call is documented and emulated` | 脚本用到的 16 个函数全部在文档里，且 `host.py` 都实现了 |
| `api: argument counts and enum keys match the documented signatures` | 每个调用点实参个数落在文档区间；`load` 的 kind、`setoption/getoption` 的键、缓冲区名都是文档枚举 |
| `api: the emulated host exposes the documented obj.* field table` | 模拟 `obj` 表**恰好**暴露文档列出的 38 个字段（少一个/多一个都 FAIL） |
| `api: the fake returns the documented values` | 19 个探针：`getinfo("image_max")` 必须返回 **2 个**值、`getpixel()` 2 个、`getpixel(x,y,"rgb")` 4 个、`getinfo("clock")` 是数字、`setanchor` 返回数字、`draw` 接受 8 个参数、`setfont` 第 3 参数是装饰类型 0–6、`drawpoly` 三种写法且拒绝混用 |
| `api: obj.getpixel and obj.getpixeldata agree` | 同一 texel 经 `getpixel` 与经 FFI 指针（`getpixeldata`）读出的值一致 |
| `api: obj.copybuffer is documented+on, and both paths agree` | 默认开启 + 两条缓冲路径逐像素一致（α 最大差 ≤ 2） |
| `api: API linter catches seeded drift` | 10 条人造缺陷（未知函数/多传参数/未知键/非法 blend/装饰类型越界…）必须全部报出，同一片段里 2 条合法行不得误报 |

**这一层当场揪出并修好了 3 个「假阳性绿」**（都是模拟宿主的问题，不是脚本的问题）：

| 现象 | 真实规范 | 后果（修复前） | 修复 |
| --- | --- | --- | --- |
| `obj.getinfo("image_max")` 只返回一个值（一个 `(1920,1080)` 元组） | 返回**两个**值 `max_x, max_y` | 脚本 `local mw, mh = obj.getinfo("image_max")` 拿到 `mw=table, mh=nil`，尺寸上限逻辑整条失效 | 解包为两个整数 |
| `obj.getinfo("clock")` 返回 `nil` | 返回开机以来的秒数（number） | 脚本的 perf 主路径 `if type(c) == "number"` 永远为假，只能走 `os.clock` 回退 | 返回确定性浮点（可用 `HostConfig(fake_clock=…)` 固定） |
| `obj.copybuffer` 默认「不支持」 | **是文档化 API**，返回 `true/false` | 所有用例默认只测 `obj.load` 回退路径，真机主路径（帧缓冲备份）从未被执行 | 默认开启，并加用例证明两条路径一致 |

顺带修正的规范细节：`obj.setfont` 第 3 个参数是**装饰类型 0–6**（旧代码误称 `weight`，而 AviUtl2 没有字重参数）、
`obj.draw` 是 8 个参数（`rx,ry,rz`）、`obj.drawpoly` 同时支持「顶点表 + `vertex_num`」与「扁平 20 个浮点 + alpha」两种写法且**禁止混用**、
`obj.load` 的 kind 共 12 种（`movie/movie.frame/movie.info/image/text/text.layout/textlayout/figure/framebuffer/tempbuffer/layer/before`）。
这些若猜错，脚本不会报错，只会「安静地走到另一条分支」——正是静态契约层要拦的东西。

## 6. 扩展：写一个新用例

```python
# sandbox/checks/checks_09_mine.py
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
| 想让 `WARN` 也算失败 | `run_checks.py` 只把 `FAIL` 计入退出码；需要强制时读 `out/results.json` 的 `counts` 自行判定 |

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
