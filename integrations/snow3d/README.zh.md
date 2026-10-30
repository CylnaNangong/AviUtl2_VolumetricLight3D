# 雪（3D）受光联动补丁 — 让雪花被体积光束照亮

本目录是给 **[CylnaNangong/AviUtl2_Snow3D](https://github.com/CylnaNangong/AviUtl2_Snow3D)**
用的「接收端」实现。体积光（3D）从 v3.15 起会把每帧的光束形状发布到官方共享表 `global`
（协议见 [`docs/LIGHTFIELD_zh.md`](../../docs/LIGHTFIELD_zh.md)），这里的补丁让雪（3D）读它，
并让**落在光束内部的雪粒染上光束颜色、以加算合成发光**。

```
Layer 3  体積光（3D）   ← 发布光场
Layer 4  雪（3D）       ← 读取光场，粒子受光
```

预览图（沙箱近似投影）：[`docs/assets/lightfield_snow_compare.png`](../../docs/assets/lightfield_snow_compare.png) ——
左为关闭、右为开启「被体积光照亮」。

---

## 1. 快速使用

```bash
# 预览将要做的修改（不写文件）
python3 integrations/snow3d/apply_lightfield.py --snow3d ../AviUtl2_Snow3D --dry-run

# 应用到你的 Snow3D 克隆（原文件会备份为 .obj2.bak）
python3 integrations/snow3d/apply_lightfield.py --snow3d ../AviUtl2_Snow3D
```

也可以直接对单个脚本操作：

```bash
python3 integrations/snow3d/apply_lightfield.py --script "C:\...\Script\雪（3D）.obj2"
```

在雪（3D）仓库里提交 PR 时，等价做法是应用随附的 diff：

```bash
cd ../AviUtl2_Snow3D
git apply /path/to/integrations/snow3d/snow3d_lightfield.patch
```

> 补丁针对 **雪（3D）v1.22** 的锚点编写，幂等（重复执行不会二次插入），
> 找不到锚点时会以返回码 2 明确失败，不会产生半截文件。

---

## 2. 补丁做了什么

| 位置 | 内容 |
| :--- | :--- |
| 头部 | 版本号 +1，追加「体積光（3D）連動」更新说明 |
| 设置项 | 新增分组「体積光連動」4 项参数（见下） |
| 着色器 | 新增 `ps_snow_shape_tint`：把（已模糊的）图形纹理乘上光束色，保留 α |
| 接收库 | `receiver.lua`：解析 `global` 光场 + 解析式圆/矩形体外受光采样 |
| 粒子循环 | 每颗粒子算一次受光色与受光量（世界坐标） |
| 绘制 | 主通道后追加「加算发光通道」：只画受光粒子，按受光量分 6 档批次 |

新增参数：

| 界面名（日文原名） | 范围 | 默认 | 说明 |
| :--- | :--- | :---: | :--- |
| **被体积光照亮**（`体積光に照らされる`） | 勾选 | ON | 总开关。未安装体积光 / 没有光束时不产生任何影响 |
| **受光强度**（`受光の強さ`） | 0–300% | 120 | 加算发光的整体强度 |
| **光色混色**（`光色の混色`） | 0–100% | 70 | 0 = 只增亮（保留雪自身颜色），100 = 完全变成光束颜色 |
| **受光晕开**（`受光のにじみ`） | 0–300% | 100 | 受光粒子的发光面外扩比例（光晕大小） |

语言包：雪（3D）的 `Language/*.aul2` 可另行补 4 个键（不补也能用，会显示日文标签）。

---

## 3. 性能

- 每帧一次解析（≤8 条光束，约 200 次浮点运算 + 少量 `string.byte`）。
- 每颗粒子一次采样：约 30 次浮点运算 × 光束数。
- 额外 GPU 开销：每帧 1 个 128×128 的像素着色器 pass + 受光粒子的一次加算绘制（按 6 档合批）。
- 不新增缓冲（临时用 `cache:snow118/lf_src`，用完立即释放为 1×1）。

4000 粒子 + 1 条光束的实测增量在 CPU 端为微秒级，GPU 端仅多一次小尺寸绘制。

---

## 4. 已知限制

1. 体积光的 2D/3D 前景遮挡不参与光场：被前景挡住的雪仍会发亮（v1 限制，见
   [`docs/LIGHTFIELD_zh.md` §4](../../docs/LIGHTFIELD_zh.md)）。
2. 彩色光源（彩绘玻璃等）在雪上会平均成单一色相。
3. 加算通道使用 `obj.setoption("blend","add","force")`，绘制结束后会恢复图层原本的合成模式。
4. 雪（3D）放在体积光**上方**时读到的是上一帧光场（播放时不可见，跳帧时可能差 1 帧）。

---

## 5. 文件说明

```
integrations/snow3d/
├── apply_lightfield.py                    # 补丁工具（幂等、带 Lua 语法校验）
├── snow3d_lightfield.patch                # 针对雪（3D）v1.22 的 git diff
├── lua/
│   ├── params.lua                         # 新增设置项
│   ├── tint_shader.hlsl                   # ps_snow_shape_tint
│   ├── receiver.lua                       # 光场解码 + 受光采样（核心）
│   ├── particle_probe.lua                 # 粒子循环里的取光
│   └── lit_pass.lua                       # 加算发光绘制通道
└── README.zh.md
```

`lua/` 下的文件是**唯一编辑源**，`apply_lightfield.py` 只负责把它们插入到雪（3D）脚本的锚点位置；
沙箱测试直接执行 `lua/receiver.lua` 本体，因此文档、补丁与测试不会各自漂移。
