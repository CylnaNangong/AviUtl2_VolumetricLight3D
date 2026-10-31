# Snow3D (`雪（3D）`) v1.23 — VolumetricLight3D 3D 光场与阴影联动集成包

本目录存放与 **`VolumetricLight3D v3.16+`** 配套联动的 **`Snow3D（雪（3D））v1.23`** 完整源码、多语言包、打包脚本与更新历史，目录结构与上游仓库 [`CylnaNangong/AviUtl2_Snow3D`](https://github.com/CylnaNangong/AviUtl2_Snow3D) 1:1 严格对齐。

## 目录内容

- `Script/雪（3D）.obj2`：`雪（3D）v1.23` 主脚本（新增「体積光連動 / 体积光联动」折叠参数组、`ps_snow_shape_tint` 着色器与 `SNOW_LF` 真 3D 光场/阴影采样器，UTF-8 CRLF）。
- `Language/*.雪（3D）.aul2`：简体中文 / English / Deutsch / community_de 四份语言包（完整翻译全部 6 组 44 项参数与 `[Tips]` 悬停说明，UTF-8 CRLF）。
- `packaging/{build_au2pkg.py,package.ini,package.txt}`：`Snow3D-v1.23.au2pkg.zip` 一键拖拽安装包构建配置。
- `docs/{CHANGELOG_zh.md,CHANGELOG_ja.md}`：中/日双语 `v1.23` 更新历史。
- `apply_to_snow3d.py`：一键同步到 `AviUtl2_Snow3D` 本地仓库并构建 `.au2pkg.zip` 的辅助工具。

## 快速构建与同步方法

### 1. 直接在本仓库生成 `Snow3D-v1.23.au2pkg.zip`

```bash
python3 integrations/snow3d/apply_to_snow3d.py --build-pkg
# 输出：dist/Snow3D-v1.23.au2pkg.zip
```

### 2. 一键同步到 `AviUtl2_Snow3D` 仓库并打包

```bash
python3 integrations/snow3d/apply_to_snow3d.py /path/to/AviUtl2_Snow3D --build-pkg
# 自动以标准 CRLF/LF 同步 Script/、Language/、packaging/、docs/ 并生成 Snow3D-v1.23.au2pkg.zip
```
