# 给本地 DeepSeek Harness（DSH）的真机回归小纸条 — `VolumetricLight3D v3.15` + `VL3DBridge v1.22`

你好呀 DSH！👋  
先超级感谢你那份详尽扎实的《`AviUtl2 2.1.16` 兼容性真机分析》报告！特别是：
1. 你在 **§11** 敏锐地自行复核并纠正了早期探针把 `b.get_status()` 第 7/8 返回值误标为 `sig_scene_ok / sig_editcam_ok` 的解包顺序问题；
2. 你在 **§4** 抓出了 Windows ASLR 改写内存 `OptionalHeader.ImageBase` 导致 `status.sha256` 每次启动都会变动的陈年盲区；
3. 最关键的是你在 **§10.3 (`text-canon.aup2`)** 用对照探针一举抓出了 **`[テキスト]` 图层 `afx, afy, pad` 恒定回退为 `-0.5, -0.5, 0,0,0,0`** 的真实潜伏 Bug！顺着你的线索，我们在官方 `plugin2.h` 注释里定位到了 100% 吻合的根因，并在本 PR 中连同 Windows x64 二进制 `Plugin/VL3DBridge.aux2`（`v1.22`）与 `Script/VolumetricLight3D.obj2`（`v3.15`）一并交叉编译打包好啦。

下面把本分支改动的精确技术细节、新的 `get_status()` 返回槽位定义，以及最值得你在 Windows 真机上跑一轮的 **4 个回归检查点** 留给你作参考～

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
- **严格模式闭环**：`13_camera.lua` 在检测到 `VL3DBridge` 已加载但 `rpm_allowed == false`（例如用户开启 `strict_whitelist_only = 1` 且运行在未授权的未来宿主版本上）时，不再绕过 C++ 门控去跑 Lua FFI 裸读；无插件纯脚本模式下的编辑相机读取也已统一改为经由 `Kernel32.ReadProcessMemory` 安全拷贝。

### 1.3 `VL3DBridge.get_status()` 最新 13 槽返回值契约（向后兼容！）
为了不破坏任何已有脚本对前 10 个返回值的解包，我们在保持 `#1..#10` 顺序不变的前提下，在末尾追加了 `#11..#13`：

| 槽位 | Lua 变量名建议 | 类型 | 含义 | 在 `AviUtl2 v2.1.16` 正常加载时的预期值 |
| :---: | :--- | :---: | :--- | :--- |
| `#1` | `initialized` | `boolean` | 插件 `InitializePlugin` 是否完成 | `true` |
| `#2` | `sdk_ready` | `boolean` | `EDIT_HANDLE` 是否就绪 | `true` |
| `#3` | `rpm_allowed` | `boolean` | 当前是否允许只读内存探针 | `true` |
| `#4` | `whitelist_hit` | `boolean` | 是否命中内置/`.ini` 的 `allow_pe` 或 `allow_sha256` | `true` |
| `#5` | `host_version` | `number` | `InitializePlugin(version)` 传入的宿主版本号 | `2011600` |
| `#6` | `pe_timestamp` | `number` | `FileHeader.TimeDateStamp` | `1793407160`（`0x6AE538B8`） |
| `#7` | `image_size` | `number` | `OptionalHeader.SizeOfImage` | `6250496`（`0x005F6000`） |
| `#8` | `active_scene_id` | `number` | 当前活跃场景索引 | `0`（主场景） |
| `#9` | `mode` | `string` | 运行模式描述串 | `"SDK+ReadOnlyProbe"` |
| `#10` | `sha256_hex` | `string` | **磁盘 `aviutl2.exe` 全文件 SHA-256**（小写 64 字符） | `"29a92f3f02dc09e8...93b8d5"`（恒定不变） |
| `#11` | `sig_scene_ok` | `boolean` | **(v1.22 新增)** Sig 1（`g_luaCtx`）机器码扫描是否通过 | `true` |
| `#12` | `sig_editcam_ok` | `boolean` | **(v1.22 新增)** Sig 2（编辑视角绿相机）机器码扫描是否通过 | `true` |
| `#13` | `strict_whitelist_only` | `boolean` | **(v1.22 新增)** 当前 `VL3DBridge.ini` 是否开启了严格模式 | 默认 `false`（改 `1` 后为 `true`） |

---

## 2. 建议你在 Windows 真机上跑的 4 个验收点

拉取本 PR 分支后，`Plugin/VL3DBridge.aux2`（`v1.22`，Zig `x86_64-windows-gnu` 交叉编译 PE32+ DLL）、`Plugin/VL3DBridge.ini` 与 `Script/VolumetricLight3D.obj2`（`v3.15`）均已是最新构建产物，可直接拷入你的隔离测试宿主目录（`C:\ProgramData\aviutl2` 或便携目录）验证：

1. **检查点 A — `[テキスト]` 对齐与描边回归（重点！）**  
   打开你构造的 `probe/text-canon.aup2`（Layer 6：`文字揃え=右寄せ[下]`、`文字装飾=縁取り文字`、`サイズ=64`），在第 120 帧调用：
   ```lua
   local ok, x, y, z, rx, ry, rz, sx, sy, sz, op,
         kind, fpath, obj_id,
         has_sh, sh_col, has_ln, ln_col,
         has_3d, is_txt, afx, afy,
         pl, pr, pt, pb = b.resolve_layer(6, 120)
   ```
   - **预期结果**：`ok == true`、`is_txt == true`、**`afx == 0.5`、`afy == 0.5`**（不再是 `-0.5, -0.5`！），且 **`pl, pr, pt, pb == 9, 9, 9, 9`**（`max(1, floor(64 * 0.14 + 0.5)) = 9`，不再是 `0,0,0,0`！）。
   - 如果把文本图层本身选为体积光的发光层或 2D 遮挡层，右对齐/居中对齐文本的光源中心与遮挡框现在应当严丝合缝对齐字形中心了。

2. **检查点 B — ASLR 下连续重启两次 `aviutl2.exe` 校验 `sha256` 恒定性 & 13 槽状态**  
   连续启动两次 `AviUtl2 v2.1.16` 打印 `b.get_status()` 全部 13 个返回值：
   - **预期结果**：两次启动的第 `#10` 槽 `sha256_hex` **完全相同**，且等于你在本地测得的 `aviutl2.exe` 磁盘文件 SHA-256（`29a92f3f02dc09e83ca23b025279da8321b31cb787266509c8d55ba16193b8d5`）；`#11 sig_scene_ok == true`、`#12 sig_editcam_ok == true`、`#13 strict_whitelist_only == false`。

3. **检查点 C — `strict_whitelist_only = 1` 严格白名单正向放行与负向拦截**  
   - **正向**：将 `Plugin/VL3DBridge.ini` 中 `strict_whitelist_only = 1` 打开，在 `v2.1.16`（及 `v2.1.15`）上启动 -> 预期 `whitelist_hit == true`、`rpm_allowed == true`、`strict_whitelist_only == true`、`mode == "SDK+ReadOnlyProbe"`，摄像机内部 3D 光源（`probe/light-test.aup2`）与绿相机依然正常工作。
   - **负向（模拟未知新版本拦截）**：保持 `strict_whitelist_only = 1`，若想测试拦截路径，可临时填一行不存在的 `allow_sha256 = 0000...`（注：`allow_pe` 已内置 `v2.1.15`/`v2.1.16`，如需在 `v2.1.16` 上强测未命中路径，只能改二进制或等未来新版；正常确认正向 `whitelist_hit == true` 即可！）。

4. **检查点 D — 原测试工程成片渲染与 D3D11 CS 硬件光栅对拍**  
   用 `给你用的测试文件.aup2` 与 `probe/light-test.aup2` 在 `v2.1.16` 跑一遍帧导出与 HUD 检查：
   - HUD 左上角应显示 `v3.15`、`,CS`（D3D11 ComputeShader 激活）；
   - `b.get_camera_state()` 检出摄像机 3D 光源与绿相机切换正常，全程零崩溃、零异常日志。

再次感谢你的细致测试！如果真机跑下来发现任何新的边界现象（哪怕是 1 px 的细微偏差），随时贴进报告里告诉我们，我们继续一起打磨！✨
