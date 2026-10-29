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
│   ├── raster.py          # 切片四边形光栅化（Weight-Blended OIT 近似）
│   ├── assets.py          # 测试素材：彩窗、剪影、渐变、单位立方体 .obj
│   ├── session.py         # 高层 API：build scene → run_frame → PNG/统计
│   └── harness.py         # 极简测试框架（@check / require / skip）
├── checks/                # 7 组、70 个用例
│   ├── checks_01_script.py     # .obj2 静态分析（参数/分组/默认值/i18n/安全）
│   ├── checks_02_language.py   # 4 个 .aul2 语言包完整性
│   ├── checks_03_hlsl.py       # 14 段 HLSL 编译与数值行为
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
* 全部用例约 **3 分钟**（CPU 软件光栅 + NumPy 着色器，比真实 GPU 慢 2 个数量级）。

## 3. 快速开始

```bash
cd sandbox

python3 run_checks.py                  # 跑全部 70 个用例，输出 PASS/FAIL + out/results.json
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

完整的模块级分析见 `docs/ANALYSIS.zh.md`（架构、算法、性能、风险与改进建议）。
