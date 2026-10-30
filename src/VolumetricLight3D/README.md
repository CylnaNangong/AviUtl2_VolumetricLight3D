# VolumetricLight3D 模块化脚本源码

这里是 `Script/VolumetricLight3D.obj2` 的**唯一编辑源**。原来的 9,560 行单文件已拆成
21 个有序 Lua 片段和 14 个独立 HLSL 文件；`VL3DBridge` 的 C++17 源码仍位于
`src/VL3DBridge/`，本次没有改动其实现。

## 为什么仍然生成单个 `.obj2`

这些文件是**构建期模块**，不是 AviUtl2 运行时的 Lua `require()` 模块。官方
[Lua 文档](https://docs.aviutl2.jp/lua/)虽然提供 `require()` 的 UTF-8 钩子，但也提示
“从脚本调用脚本可能无法正常工作”，且 `package.path` 涉及 SJIS；参数注解和
`--[[pixelshader@...]]` 块也必须由宿主从脚本头部读取。因此，发布时仍组合成原有的
单文件、零额外依赖 `.obj2`，不增加安装文件，也不改变顶层 `local` 的作用域与声明顺序。

> **不要直接编辑 `Script/VolumetricLight3D.obj2`。** 它是生成物；沙箱和 CI 会检查它是否
> 与这里的源码逐字节一致。也不要单独执行 `modules/*.lua`：它们会有意引用前面片段的
> 顶层局部变量，只有按清单组合后才是完整脚本。

## 构建与检查

在仓库根目录运行（只需要 Python 标准库）：

```bash
# 从模块重建发布脚本；内容未变化时不会重写文件
python3 src/VolumetricLight3D/build.py

# 只检查，不写入；源码与发布脚本不一致时退出码为 1
python3 src/VolumetricLight3D/build.py --check

# 在隔离位置生成，可用于比较或测试
python3 src/VolumetricLight3D/build.py --output sandbox/out/VolumetricLight3D.obj2

# 完整沙箱回归（含构建同步、确定性和故障注入用例）
python3 sandbox/run_checks.py
```

构建器始终输出 **UTF-8、无 BOM、CRLF**。`manifest.json` 是唯一的顺序清单；新增、
删除或重命名源码后必须同步清单。构建器会拒绝漏登记文件、重复路径/着色器、目录逃逸、
非 UTF-8、BOM 和缺少末尾换行等情况。打包脚本 `packaging/build_au2pkg.py` 也会先重建
`.obj2`，避免把旧生成物装入发布包。

## 目录与职责

```text
src/VolumetricLight3D/
├── build.py             # 确定性组装、--check、隔离输出
├── manifest.json        # 35 个源文件的显式顺序
├── modules/             # 21 个 Lua 构建片段
└── shaders/             # 14 个像素着色器（.hlsl）
```

| Lua 片段 | 职责 |
| --- | --- |
| `00_header.lua` | 版本信息、许可/致谢与历史注释 |
| `01_properties.lua` | 6 组、47 项 AviUtl2 参数及 hide/group 注解 |
| `02_runtime_state.lua` | 宿主别名与跨帧持久状态 |
| `03_i18n.lua` | UI 语言检测、前向声明与内置四语文本 |
| `04_params.lua` | 缓存清单、参数快照与默认值 |
| `05_utils.lua` | 图层列表、变换、通用读取工具 |
| `06_win32.lua` | UTF-16、PE 导出、只读 Win32/文件辅助 |
| `07_mesh_loaders.lua` | ASCII 扫描、OBJ 与 GLB/VRM 网格加载 |
| `08_project_scan.lua` | 语言包、`.aup2`、模型实例与工程扫描 |
| `09_layer_resolve.lua` | 原生桥入口、场景帧、文字对齐与活动图层解析 |
| `10_camera_lights.lua` | 摄像机光源、灯光参数与 3D 图层网格选择 |
| `11_occlusion_support.lua` | 裁剪、遮挡缓存、交互预算与 CPU 三角形光栅 |
| `12_bridge_raster.lua` | 网格上传、阴影图与相机深度图 |
| `13_camera.lua` | 编辑相机回退与相机基向量 |
| `14_frame_state.lua` | 光源池统计、帧状态与 HUD 辅助 |
| `15_frame_prepare.lua` | 帧重置、准备、光源捕获 |
| `16_geometry_mip.lua` | 切片几何、颜色 Mip 与阈值预处理 |
| `17_occlusion_pipeline.lua` | 每帧 2D/3D 遮挡编排 |
| `18_autoblend.lua` | 前景融入背景（AutoBlendLight）子系统 |
| `19_render.lua` | 图集提交、切片绘制、HUD 与日志 |
| `20_main.lua` | 帧管线、重入保护与唯一入口 `vl_run_frame()` |

`shaders/` 中文件名与 AviUtl2 注册名/入口点一致。`beamatlas.hlsl` 是体积切片核心；
`ps_vl_ab_*.hlsl` 属于 AutoBlend；其余文件负责池化、阈值、图集、模糊、程序化光源和
水面。构建器只为它们恢复宿主要求的 `--[[pixelshader@名称: ... ]]` 外包装，不改 HLSL
内容。

## 修改后的交付门禁

1. 运行 `python3 src/VolumetricLight3D/build.py` 更新生成物。
2. 运行 `python3 src/VolumetricLight3D/build.py --check`，确认无漂移。
3. 运行 `python3 sandbox/run_checks.py`；其中 `checks_11_build.py` 会验证：
   - 发布 `.obj2` 与所有模块逐字节一致；
   - 隔离构建确定且重复写入幂等；
   - 内容漂移、漏登记模块、重复清单项都会被拒绝。
4. 涉及渲染行为时，再检查完整 HLSL、运行时、遮挡、AutoBlend、亮度与 C++ 桥测试；
   CPU 沙箱不能替代 Windows + AviUtl2 + D3D11 真机像素验收。
