# DSH 真机验收交接：VolumetricLight3D v3.17 × VolumetricFog3D v2.06

## 本轮实现

- Fog3D 的「散射光源」新增「手动方向 / 体积光联动」选择。**默认仍为手动方向**，旧工程不切换时不改变效果。
- 联动模式读取当前 `scene_id`、当前帧有效的 `VL3DLF v3` 包。每个雾切片使用 4×4 世界空间样点，采样局部 RGB 辐射、光线传播方向以及 Band 1 的 3D/2D 投影阴影，再交给 Fog 原有 GPU Henyey–Greenstein 相位函数。
- 数据不存在、过期、跨场景或无可用光束时回退到原手动散射；不添加 Fog 对 `.aux2` 插件的硬依赖。
- 主仓库包含 Fog v2.06 完整版与 ScriptOnly 安装包构建/同步辅助脚本；未来发布 VolumetricLight3D v3.17 时 workflow 会一并打包。

## 沙箱验证

- 完整回归：**109 PASS / 0 FAIL / 0 SKIP / 0 WARN**。
- 新增真机链路模拟：VolumetricLight3D 发布光场 → Fog 消费光场；验证局部颜色/方向数据生效、2D 投影遮挡降低 Fog 样点辐射，并确认手动模式和无新鲜光场的回退与手动渲染逐像素一致。
- Fog HLSL cbuffer 为 8,240 个 float，沙箱验证常量长度匹配，无 shader 参数错位。
- 上述结果是沙箱/CPU HLSL 模拟，不代替 AviUtl ExEdit2 v2.1.16 真机验收。

## 建议真机验收步骤

1. 安装 VolumetricLight3D v3.17 与 VolumetricFog3D v2.06（完整版或 ScriptOnly 版均可）。保留现有 AviUtl2 项目备份。
2. 建议时间轴顺序从上到下：光源图层 → 2D/3D 遮挡物（可选）→ VolumetricLight3D → VolumetricFog3D。这样 Fog 能读取同帧发布的光场。
3. Fog「散射光」组保持强度大于 0，把「散射光来源」切到「体积光联动」。先用窄光束/彩色光源，确认雾中亮区跟随光束方向和局部颜色。
4. 将 2D 遮挡层插入光源与雾体之间，再试 3D `.obj` 遮挡；确认投射阴影后的雾中散射压低，而未遮挡区域仍亮。
5. 切回「手动方向」，记录画面；重新开启联动但禁用/移除 VolumetricLight3D，确认自动回退且画面与手动模式一致。
6. 拖动时间轴、切换子场景及预览编辑相机，观察无旧帧残光、无跨场景串光、无明显卡顿或闪烁。建议记录帧耗时/CPU 占用；默认 48 切片、共享场每片 4×4 样点。

请回传：AviUtl2 版本、VolumetricLight/Fog 包版本、散射模式、相机模式、遮挡设置、前后对照截图及脚本日志。若色彩或阴影位置不符，请提供最小 `.aup2` 场景和具体帧号。

## 同步到上游 VolumetricFog3D 仓库

本 PR 将整合后的源码放在 `integrations/volumetricfog3d/`。在本地上游仓库 checkout 上同步并构建：

```bash
python3 integrations/volumetricfog3d/apply_to_volumetricfog3d.py /path/to/AviUtl2_VolumetricFog3D --build-pkg
```

此辅助脚本只同步 Fog 脚本、语言包、打包元数据和文档，同步可选的兼容 `VL3DBridge.aux2 v1.22`（完整版仍可不安装；ScriptOnly 不含插件）。
