# 给本地 DeepSeek Harness（DSH）的真机回归小纸条 — `VolumetricLight3D v3.15` + `VL3DBridge v1.22`

你好呀 DSH！👋  
先超级感谢你那份详尽扎实的《`AviUtl2 2.1.16` 兼容性真机分析》以及《`PR #13` 验收报告》！特别是：
1. 你在 **§11** 敏锐地自行复核并纠正了早期探针把 `b.get_status()` 第 7/8 返回值误标为 `sig_scene_ok / sig_editcam_ok` 的解包顺序问题；
2. 你在 **§4** 抓出了 Windows ASLR 改写内存 `OptionalHeader.ImageBase` 导致 `status.sha256` 每次启动都会变动的陈年盲区；
3. 你在 **§10.3 (`text-canon.aup2`)** 用对照探针一举抓出了 **`[テキスト]` 图层 `afx, afy, pad` 恒定回退为 `-0.5, -0.5, 0,0,0,0`** 的真实潜伏 Bug；
4. 以及在 **PR #13 验收报告 §6** 中帮我们精准挑出了初版小纸条里的 3 处 API/常量笔误（`resolve_layer` 14 返回值、`SDK+VERIFIED_RPM`、`get_edit_camera()` / `get_camera_lights()` 与 `0.15` 描边系数）和 `VL3DBridge.ini` 里旧 `v1.21` 遗留的 `allow_sha256` 死条目——现已全部订正归档！🙏

下面保留修正后的完整接口契约与真机验收归档记录，供后续版本回归直接复用～

---

## 1. 三个核心问题的根因与本次改动速览

### 1.1 为什么 `text-canon.aup2` 的 `[テキスト]` 对齐与描边在 `v1.21` 会失效？
在官方 `aviutl2_plugin_sdk/plugin2.h` 中，`EDIT_SECTION::get_object_item_value()` 与 `get_object_alias()` 有一条非常隐蔽的生命周期契约：
> `※次に同一スレッドで文字列返却の関数を使うかコールバック処理の終了まで有効`  
> （返回的 `LPCSTR` 指向宿主同一线程复用的临时字符串缓冲区，**下一次在同一线程调用任何返回字符串的 SDK 函数时即被原地覆写**！）

`v1.21` 的 `ProcReadLayerSdk` 曾这样写：
```cpp
LPCSTR align_utf8 = edit->get_object_item_value(obj, L"テキスト", L"文字揃え");
LPCSTR deco_utf8  = edit->get_object_item_value(obj, L"テキスト", L"文字装飾"); // ← 覆写了 align_utf8 指向的宿主 TLS 缓冲！
LPCSTR sz_utf8    = edit->get_object_item_value(obj, L"テキスト", L"サイズ");   // ← 又把前两个指针内容覆写成了 "64.00"！
ParseTextAlignAndDeco(align_utf8, deco_utf8, sz, ...);
```
结果进入 `ParseTextAlignAndDeco` 时，`align_utf8` 和 `deco_utf8` 指向的都是 `"64"`，既匹配不上 `"右寄せ[下]"` 也匹配不上 `"縁取り文字"`，于是双双落入默认分支 `(-0.5, -0.5)` 和 `pad = 0`；而 `r.is_text` 已经被置为 `true`，又把 Lua 侧 `09_layer_resolve.lua` 的 `obj.getvalue("layer6.text.align")` 兜底短路掉了！之前 Linux 沙箱自测没测出来，是因为旧 mock 返回的是静态 C 字符串字面量。

**`v1.22` 修复**：
- 每次调用 `get_object_item_value()` 后**立刻深拷贝为独立 `std::string`**（同时顺手修了 `ReadSceneCameraViaSdk` 里 `sx` 跨 `sz` 持有的同类隐患）；
- 字号优先通过 `edit->get_object_track_value(obj, L"テキスト", L"サイズ", frame, &sz_track)` 读取当前帧插值结果（支持带关键帧动画的字号缩放！），失败时再解析字符串；
- 若某版本下 `"文字揃え"` 返回空串，自动调用 `edit->get_object_alias(obj)` 从对象 INI 块中兜底提取 `文字揃え=` / `文字装飾=` / `サイズ=`；
- `vl3d_bridge_selftest.cpp` 的模拟宿主已改为每次调用都拿 `0xA5` 擦写同一块 `thread_local char[256]` 缓冲（新增用例 `sdk_text_align_tls_overwrite_dsh_repro`）。

### 1.2 ASLR 不变 `status.sha256` 与 `v2.1.16` 双重白名单
- **磁盘原文件流式哈希**：在 Windows 上，`RefreshHostInfoUnlocked()` 现在直接通过 `GetModuleFileNameW(exe, ...)` 打开磁盘上的 `aviutl2.exe`，以 64 KB 分块计算**完整 EXE 文件的 SHA-256**——输出与 PowerShell `Get-FileHash .\aviutl2.exe -Algorithm SHA256` 完全一致，无论 Windows ASLR 把基址重定位到哪里都**恒定不变**！
- **内存头兜底归一化**：若磁盘文件读取意外受阻回退到内存 PE 头（前 4096 字节）哈希，也会先将本地缓冲副本里的 `OptionalHeader.ImageBase`（`nt + 24 + 0x18`）强制归一化为 `0x140000000ULL` 再做 SHA-256。
- **内置 + `.ini` 双登记**：`VL3DBridge.aux2`（`v1.22`）编译期默认白名单与 `Plugin/VL3DBridge.ini` 均已同时登记：
  - `0x6ADC1302, 0x005F5000`（`AviUtl ExEdit2 v2.1.15`）
  - `0x6AE538B8, 0x005F6000`（`AviUtl ExEdit2 v2.1.16`）
  （已清理旧 `v1.21` 遗留的运行时 4KB 头 `allow_sha256 = ea321cfb...` 死条目，改为注释说明如何按需填入磁盘文件 SHA-256。）
- **严格模式闭环**：`13_camera.lua` 在检测到 `VL3DBridge` 已加载但 `rpm_allowed == false`（例如用户开启 `strict_whitelist_only = 1` 且运行在未授权的未来宿主版本上）时，不再绕过 C++ 门控去跑 Lua FFI 裸读；无插件纯脚本模式下的编辑相机读取也已统一改为经由 `Kernel32.ReadProcessMemory` 安全拷贝。

### 1.3 `VL3DBridge.get_status()` 最新 13 槽返回值契约（向后兼容！）
保持 `#1..#10` 顺序与字符串取值 100% 不变，在末尾追加 `#11..#13`：

| 槽位 | Lua 变量名建议 | 类型 | 含义 | 在 `AviUtl2 v2.1.16` 正常加载时的实测值 |
| :---: | :--- | :---: | :--- | :--- |
| `#1` | `initialized` | `boolean` | 插件 `InitializePlugin` 是否完成 | `true` |
| `#2` | `sdk_ready` | `boolean` | `EDIT_HANDLE` 是否就绪 | `true` |
| `#3` | `rpm_allowed` | `boolean` | 当前是否允许只读内存探针 | `true` |
| `#4` | `whitelist_hit` | `boolean` | 是否命中内置/`.ini` 的 `allow_pe` 或 `allow_sha256` | `true` |
| `#5` | `host_version` | `number` | `InitializePlugin(version)` 传入的宿主版本号 | `2011600` |
| `#6` | `pe_timestamp` | `number` | `FileHeader.TimeDateStamp` | `1793407160`（`0x6AE538B8`） |
| `#7` | `image_size` | `number` | `OptionalHeader.SizeOfImage` | `6250496`（`0x005F6000`） |
| `#8` | `active_scene_id` | `number` | 当前活跃场景索引 | `0`（主场景） |
| `#9` | `mode` | `string` | 运行模式标识串（降级时为 `"SDK_ONLY_SAFE_DEGRADED"`） | `"SDK+VERIFIED_RPM"` |
| `#10` | `sha256_hex` | `string` | **磁盘 `aviutl2.exe` 全文件 SHA-256**（小写 64 字符） | `"29a92f3f...93b8d5"`（恒定不变） |
| `#11` | `sig_scene_ok` | `boolean` | **(v1.22 新增)** Sig 1（`g_luaCtx`）机器码扫描是否通过 | `true` |
| `#12` | `sig_editcam_ok` | `boolean` | **(v1.22 新增)** Sig 2（编辑视角绿相机）机器码扫描是否通过 | `true` |
| `#13` | `strict_whitelist_only` | `boolean` | **(v1.22 新增)** 当前 `VL3DBridge.ini` 是否开启了严格模式 | 默认 `false`（改 `1` 后为 `true`） |

---

## 2. 标准真机验收检查点与精确 Lua 探针签名

1. **检查点 A — `[テキスト]` 对齐与描边回归（14 返回值契约）**  
   在 `probe/text-canon.aup2`（Layer 6：`文字揃え=右寄せ[下]`、`文字装飾=縁取り文字`、`サイズ=64`）第 120 帧调用：
   ```lua
   local status, fx, fpath, adj, scene_id, water_col, src_tier,
         is_txt, afx, afy, pl, pr, pt, pb = b.resolve_layer(6, 120)
   ```
   - **预期结果**：`status == "OTHER"`、`fx == "テキスト"`、`src_tier == "SDK_PARAM_EDIT"`、`is_txt == true`、**`afx == 0.5`、`afy == 0.5`**，且 **`pl, pr, pt, pb == 9, 9, 9, 9`**（`ParseTextAlignAndDeco` 中 `縁取り文字` 公式为 `max(1, floor(size * 0.15))`，`floor(64 * 0.15) = 9`；`影付き文字` R/B 为 `floor(size * 0.10)`，`縁取り文字(細)` 四边为 `floor(size * 0.08)`）。

2. **检查点 B — ASLR 下连续重启两次 `aviutl2.exe` 校验 `sha256` 恒定性 & 13 槽状态**  
   连续启动两次 `AviUtl2 v2.1.16` 打印 `b.get_status()` 全部 13 个返回值：
   - **预期结果**：两次启动的第 `#10` 槽 `sha256_hex` 完全相同且等于 `Get-FileHash aviutl2.exe -Algorithm SHA256`；`#9 == "SDK+VERIFIED_RPM"`、`#11 sig_scene_ok == true`、`#12 sig_editcam_ok == true`、`#13 strict_whitelist_only == false`。

3. **检查点 C — `strict_whitelist_only = 1` 严格白名单验证**  
   在 `Plugin/VL3DBridge.ini` 开启 `strict_whitelist_only = 1`（含保留或清空 `allow_pe` 测内置默认白名单）：
   - **预期结果**：在 `v2.1.16` 与 `v2.1.15` 上 `whitelist_hit == true`、`rpm_allowed == true`、`strict_whitelist_only == true`、`mode == "SDK+VERIFIED_RPM"`。

4. **检查点 D — 原测试工程、D3D11 CS 硬件光栅与三件套联动**  
   - 调用 `b.get_edit_camera()`（绿相机姿态）与 `b.get_camera_lights()`（摄像机控制内 3D 点光源/平行光数组）确认返回有效数据；
   - 调用 `b.get_gpu_status()` 确认高模 `.obj` 下 `backend == "D3D11_CS"` 且日志 `遮挡=` 字段含 `,CS`。

---

## 3. PR #13 真机验收回执归档（2026-10-31，by DeepSeek Harness）

- **被测二进制**：
  - `Plugin/VL3DBridge.aux2` `v1.22`（`574,976` B，SHA-256 `5d92e78e…63d08b40`）
  - `Script/VolumetricLight3D.obj2` `v3.15`（`498,280` B，SHA-256 `25aec7d3…1342251e`）
- **实测结论（`AviUtl ExEdit2 v2.1.16` + 对照 `v2.1.15`）**：
  - ✅ **检查点 A**：`text-canon.aup2` Layer 6 在 `v2.1.16` 与 `v2.1.15` 双版本均精准返回 `is_text=true, afx=0.5, afy=0.5, pad=9,9,9,9`。
  - ✅ **检查点 B**：连续两次启动 `#10 sha256` 完全一致并与磁盘 `aviutl2.exe` 文件哈希相等；`#11/#12` 双机器码签名均为 `true`。
  - ✅ **检查点 C**：`strict_whitelist_only=1` 在完整 `.ini` 与空 `.ini`（仅 `strict=1`）下均命中白名单并正常放行。
  - ✅ **检查点 D & D3D11 CS & 3D 三件套**：
    - `给你用的测试文件.aup2` `v3.15` vs `v3.14` 逐像素完全一致（`mean diff = 0.0 / max diff = 0`）；
    - `bairdcage.obj`（114,036 顶点）成功激活 `backend=D3D11_CS`（`tri:38012,CS`），`CS` vs `CPU` `max diff = 1`（`>2` 像素 `0.00%`），且 `2.1.15` 与 `2.1.16` 的 CS 成片逐像素零差异（`max diff = 0`）；
    - `カメラ制御` + `VolumetricLight3D v3.15` + `VolumetricFog3D v2.05` + `雪（3D）v1.22` 七层真 3D 三件套在 `2.1.15` 与 `2.1.16` 上成片逐像素一致（`max diff = 0`），全程零崩溃。

再次感谢 DSH 的严谨护航！🤝✨
