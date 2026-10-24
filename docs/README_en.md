<div align="center">

# Volumetric Light (3D) / 体積光（3D） / 体积光（3D）

**THE REAL 3D Volumetric Light (God Rays / Crepuscular Rays / Light Shafts) Custom Object for AviUtl ExEdit2**  
**With Built-in 3D-Linked Foreground Light Blending & Optional D3D11 CS GPU Acceleration**

`体積光（3D）.obj2 v3.06` &nbsp;|&nbsp; `VL3DBridge.aux2 v1.21` &nbsp;|&nbsp; `AviUtl ExEdit2 v2.1.15+` &nbsp;|&nbsp; `MIT License`

**🌐 Language:** &nbsp; **[简体中文 (Chinese)](../README.md)** &nbsp;|&nbsp; **[日本語 (Japanese)](README_ja.md)** &nbsp;|&nbsp; **English**

*(4 UI Languages Supported: 日本語 / 简体中文 / English / Deutsch)*

</div>

---

<div align="center">
  <img src="assets/demo.gif" alt="Volumetric Light (3D) Real-Time Demo" width="720" />
  <br />
  <sub>▲ Real-time 3D volumetric god-ray emission, 2D/3D foreground shadow shafts, and edge-light blending</sub>
</div>

<br />

<div align="center">
  <img src="assets/comparison_off_on.jpg" alt="OFF vs ON Comparison" width="860" />
  <br />
  <sub>▲ Left: OFF (raw layers) ｜ Right: ON (Volumetric Light (3D) + Foreground Occlusion + Foreground Blend)</sub>
  <br />
  <sub>🎤 Demo Model: DiffSinger AI Virtual Singer <b>Nangong Yan (南宫颜)</b> (<a href="https://cylnanangong.wixsite.com/ngyy">Official Site</a> ｜ <a href="https://github.com/CylnaNangong/NGYY_ENG_Dataset">GitHub English Dataset</a>) · Character Portrait via <a href="https://www.neka.cc/">neka.cc</a> by artist <b>Litangfeng (离堂风)</b></sub>
</div>

---

## Table of Contents

- [1. Overview & Key Features](#1-overview--key-features)
- [2. System Requirements](#2-system-requirements)
- [3. Installation (`.au2pkg.zip` Drag-and-Drop Supported)](#3-installation-au2pkgzip-drag-and-drop-supported)
- [4. Quick Start & Recommended Layer Order](#4-quick-start--recommended-layer-order)
- [5. Complete Parameter Reference (Click to Expand)](#5-complete-parameter-reference-click-to-expand)
- [6. Reverse-Engineering / Memory-Probe Compatibility Risks](#6-reverse-engineering--memory-probe-compatibility-risks)
- [7. Future Roadmap](#7-future-roadmap)
- [8. Reporting Bugs](#8-reporting-bugs)
- [9. Building `VL3DBridge.aux2` & `.au2pkg.zip` Packages](#9-building-vl3dbridgeaux2--au2pkgzip-packages)
- [10. Authors, Acknowledgements & License](#10-authors-acknowledgements--license)

---

## 1. Overview & Key Features

**`Volumetric Light (3D)` (`体積光（3D）`)** is **THE REAL 3D volumetric light (god rays / crepuscular rays / light shafts)** custom object for **AviUtl ExEdit2**. It extrudes bright pixels from any source layer (images, videos, text, shapes, or procedural custom objects such as `@水面`) or from a 3D light inside **Camera Control (`カメラ制御`)** into 3D space, producing physical 2D/3D shadow shafts and natural foreground edge-light blending.

### ✨ Key Highlights

1. **THE REAL 3D Ray-Slab Volumetric Integration**
   - Supports **Parallel**, **Radiate (Point Light)**, and **Converge** ray modes while preserving 100% of the source layer's per-pixel RGB colors (with optional tint override), smooth threshold extraction, distance falloff, and animated 3D internal smoke/dust noise.
   - Camera-aligned billboards (dynamically scaling between 64 and 192 slices) prevent slicing artifacts across 360° camera orbits without making `.obj` or `.vrm` 3D models turn semi-transparent.

2. **Hybrid 2D Layer + 3D Model (`.obj` / `.vrm`) Foreground Occlusion**
   - Casts volumetric 3D shadow shafts from foreground 2D layers, `Text` objects (supporting all **18 text alignments** and **7 border/shadow decorations**), built-in `.obj` 3D models, and `.vrm` avatars (`VRMToolkitModel`) with dark-halo-free silhouette holdout.

3. **Built-in 3D-Linked Foreground Blend (`AutoBlendLight` Integration)**
   - Integrates **nctype**'s [`AutoBlendLight`](https://github.com/nctype/aviutl2_script_AutoBlendLight) (MIT License) directly inside `.obj2` as a 3D-beam-aware GPU pipeline (OFF by default; no separate `.anm2` effect needed).
   - Automatically detects foreground 2D layers or accepts custom layer ranges (`4-5`, `3,5,7-9`), sampling directional volumetric beam colors and blurred background light to wrap foreground characters in realistic backlight gradients or crisp rim highlights while protecting frontmost subtitles/UI layers.

4. **Optional `VL3DBridge.aux2` Companion Plugin: D3D11 ComputeShader + Lock-Free Bridge**
   - **Hardware GPU Rasterizer (`cs_5_0`)**: Uses AviUtl2 `v2.1.15+`'s official `EDIT_HANDLE::get_direct3d_device()` API to rasterize 3D shadow maps and camera depth maps on the GPU — **20x–45x faster** on high-poly meshes with 100% full-triangle shadow fidelity even while dragging parameters.
   - **Lock-Free Parameter Read**: Reads scene parameters directly via `SCRIPT_MODULE_PARAM::edit`, eliminating drag-time UI deadlocks and tracking unsaved (`pre-Ctrl+S`) text alignment and parameter edits instantaneously.
   - **3-Tier Automatic Fallback**: `D3D11_CS (GPU)` → `C++17 -O3 (CPU)` → `Pure LuaJIT FFI`. Everything works standalone even without `VL3DBridge.aux2`.

5. **Strict 16-Million-Pixel Cache Budget & 8K / 16K Resilience**
   - Engineered to stay strictly inside AviUtl2's default `TemporaryImageCacheSize` (16M pixels) and D3D11 `4096px` RTV limits — even on **8K UHD (`7680×4320`)** canvases or **50MP / 16K** source assets (peak live cache ~`3.9M–7.2M` pixels, released to `1×1` at end of frame).
   - Emits `Initializing...` / `Initialization complete` console notifications during first-frame loading of heavy scenes or sub-scenes so asset decoding waits are clearly communicated.

---

## 2. System Requirements

| Component | Requirement / Notes |
| :--- | :--- |
| **OS** | Windows 10 / Windows 11 (64-bit) |
| **Host Application** | **AviUtl ExEdit2 `v2.1.15` (2026-10-24) or newer recommended**<br><sub>*Note: `VL3DBridge.aux2 v1.21` uses the official D3D11 device API added in `v2.1.15` for `cs_5_0` GPU shadow acceleration. Earlier `v2.0.54+` builds automatically fall back to the C++ `-O3` CPU or pure LuaJIT path.*</sub> |
| **GPU** | DirectX 11 (`Shader Model 5.0` / `cs_5_0`) compatible GPU (NVIDIA / AMD / Intel Arc or iGPU) |
| **Cache Config** | Works out-of-the-box with AviUtl2's default `TemporaryImageCacheSize` (16M pixels) |
| **Optional Companion** | [`VRMToolkit`](https://github.com/nove-nove/aviutl2-vrm-toolkit) (`VRMToolkitBridge`) is only needed if you use `.vrm` avatars as 3D occluders |

---

## 3. Installation (`.au2pkg.zip` Drag-and-Drop Supported)

### 🚀 Method 1: Drag & Drop `.au2pkg.zip` onto the Preview Window (Recommended — Ready to Use!)

1. Download the latest package from **GitHub Releases** (**do NOT unzip it**):
   - **`VolumetricLight3D-v3.06.au2pkg.zip`** (Full Recommended Edition: core script + `VL3DBridge.aux2` GPU plugin + language packs)
   - *Or `VolumetricLight3D-v3.06-ScriptOnly.au2pkg.zip` (Script-Only Edition: `.obj2` + language packs without native `.aux2`)*
2. Open AviUtl ExEdit2 and **drag & drop the `.au2pkg.zip` file directly onto the AviUtl2 preview window**, then confirm the installation dialog.
3. If you installed the Full Edition with `VL3DBridge.aux2`, **restart AviUtl2 once** to load the native plugin & language packs (you can manage or uninstall the package anytime from AviUtl2's **Package Information** menu).

### 📂 Method 2: Manual Folder Copy

Copy **`Script`**, **`Plugin`** (recommended), and **`Language`** into your AviUtl2 data directory (typically `C:\ProgramData\aviutl2\`, or next to `aviutl2.exe` in portable setups):

```text
C:\ProgramData\aviutl2\
├── Script\
│   └── 体積光（3D）.obj2                  # [Required] Core custom object script (v3.06)
├── Plugin\
│   ├── VL3DBridge.aux2                    # [Recommended] Lock-free read & D3D11 CS GPU shadow plugin (v1.21)
│   └── VL3DBridge.ini                     # [Optional] Probe whitelist & GPU config (next to .aux2)
└── Language\                              # [Optional] UI localization packs (Japanese is built-in)
    ├── 简体中文.体積光（3D）.aul2
    ├── English.体積光（3D）.aul2           # <-- Translates UI & tooltips into English ("Volumetric Light (3D)")
    ├── Deutsch.体積光（3D）.aul2
    └── community_de.体積光（3D）.aul2
```

### 💡 Standalone `.obj2` vs. Paired with `VL3DBridge.aux2`

| Workflow / Scene | `Script\体積光（3D）.obj2` Only | Paired with `Plugin\VL3DBridge.aux2` (Rec.) |
| :--- | :--- | :--- |
| **2D Image / Video / Text Rays + Foreground Blend** | Full GPU HLSL speed | Same GPU speed + **instant unsaved text alignment & decoration tracking** |
| **High-Poly `.obj` / `.vrm` Camera Flythroughs** | Pure LuaJIT CPU rasterizer (heavier on dense meshes) | **D3D11 `cs_5_0` GPU parallel rasterizer (`20x–45x` faster)** |
| **Live Parameter Dragging with Occlusion Enabled** | Auto-downscales & caps CPU work at 12ms while dragging | **Zero lock contention + 100% full-polygon real-time shadow shafts** |

---

## 4. Quick Start & Recommended Layer Order

AviUtl ExEdit2 renders timeline layers **from top to bottom (`Layer 1 → Layer N`)**. Place **`Volumetric Light (3D)`** **below** your light source and foreground character/model layers, and **above** frontmost subtitle/UI layers:

```text
Layer 1  [Background / Light Source]  Image, video, text, shape, @水面, etc. (e.g. Source Layer = 1)
Layer 2  [Foreground 3D Model]        .obj 3D Model or .vrm avatar (casts physical 3D shadow shafts)
Layer 3  [Foreground 2D Layer]        Character art / cutout / prop (occludes rays & optional Foreground Blend)
Layer 4  [Volumetric Light (3D)]  ★   ← Add Custom Object "Volumetric Light (3D)" here, set Source Layer = 1
Layer 5  [Subtitles / UI Overlay]     Placed below Volumetric Light so text/UI stays crisp and unaffected
```

> [!NOTE]
> **⏳ First-Time Scene Load Notice (~10s Initial Compile — Not Frozen!)**  
> The very first time you add `Volumetric Light (3D)`, open a project containing it, or switch into a sub-scene during an AviUtl2 session, the host compiles all 14 embedded HLSL shaders and initializes high-resolution textures/meshes. This may pause the preview for **roughly 5–15 seconds (typically ~10 seconds on high-res scenes)**. Even if you work with the Log window closed, please wait a few moments — once initialized, playback and scrubbing run smoothly in real time!

---

## 5. Complete Parameter Reference (Click to Expand)

All **47 parameters across 6 UI groups** (showing English UI labels from `English.体積光（3D）.aul2` alongside original Japanese keys):

<details>
<summary><b>📐 Group 1: Beam Shape (<code>形</code> — 12 Parameters: Source Layer, 3D Ray Geometry, Length &amp; Falloff)</b></summary>

| Parameter (EN / JA) | Range / Options | Default | Description & Usage Tips |
| :--- | :--- | :---: | :--- |
| **Source Layer** (`光源レイヤー`) | `0 – 100` | `0` | Layer number used as light source (image, video, text, shape, `@水面`; `0` = unselected, or emits directly from a Camera 3D light when *Follow Camera Light* is active). |
| **Ray Mode** (`光の出方`) | `Parallel` / `Radiate` / `Converge` | `Parallel` | Beam projection geometry: parallel shafts, outward point-light radiation, or inward convergence. |
| **Fill Style** (`光の中身`) | `Volumetric` / `Flat Solid` | `Volumetric` | `Volumetric` renders smooth atmospheric Tyndall scattering; `Flat Solid` renders stylized crisp shafts. |
| **Direction** (`方向`) | `-360° – 360°` | `55.0` | *(Parallel mode)* In-plane projection angle in degrees. |
| **Forward Tilt** (`手前角度`) | `-90° – 90°` | `44.0` | *(Parallel mode)* 3D elevation angle tilting the shaft forward toward the viewer. |
| **Screen-Space Dir** (`画面基準`) | `ON / OFF` | `ON` | *(Parallel mode)* Evaluates Direction in screen space when ON, or relative to source layer axes when OFF. |
| **Spread Angle** (`拡散角度`) | `-160° – 160°` | `22.0` | *(Parallel mode)* Cone spread (`0°` = strictly parallel, positive widens, negative converges). |
| **Light X** (`光源X`) | `-100000 – 100000` | `0` | *(Radiate / Converge)* X offset of the virtual 3D focal point (pixels). |
| **Light Y** (`光源Y`) | `-100000 – 100000` | `-300` | *(Radiate / Converge)* Y offset of the virtual 3D focal point (pixels). |
| **Light Z** (`光源Z`) | `10 – 20000` | `1400` | *(Radiate / Converge)* Z distance between the source plane and virtual focal point (smaller = wider fan). |
| **Length** (`長さ`) | `0 – 12000` | `2500` | Total 3D extension length of the volumetric shaft (pixels). |
| **Falloff** (`減衰`) | `0 – 100%` | `55.0` | Distance attenuation along the shaft (`0%` = uniform brightness, higher = smoother tip fade). |

</details>

<details>
<summary><b>💡 Group 2: Light &amp; Color (<code>光</code> — 4 Parameters: Tint, Intensity, Threshold &amp; Camera 3D Light Sync)</b></summary>

| Parameter (EN / JA) | Range / Options | Default | Description & Usage Tips |
| :--- | :--- | :---: | :--- |
| **Light Color (Opt)** (`光の色`) | Color Picker / Clear | *Cleared* | **Leave cleared to preserve 100% of the source layer's per-pixel RGB colors**; pick a color to tint uniformly. |
| **Intensity** (`強さ`) | `0 – 1000%` | `100.0` | Overall emission brightness multiplier. |
| **Threshold** (`しきい値`) | `0 – 100%` | `15.0` | Luminance extraction cutoff (`0%` = all pixels emit; raise so only bright highlights cast rays). |
| **Camera 3D Light Sync** (`カメラ光源連携`) | `Auto (Anti-Overexposure, Rec.)`<br>`Follow Camera Light (Pos & Color)`<br>`Suppress Camera Light (Pure Emissive)`<br>`Off (Raw Lighting)` | `Auto` | Controls interaction with 3D lights inside *Camera Control*: `Auto` prevents blowout, while `Follow Camera Light` syncs beam origin & color to point/spot/directional 3D lights. |

</details>

<details>
<summary><b>🌑 Group 3: Occlusion (Shadow Shafts) (<code>遮蔽設定</code> — 6 Parameters: 2D Layers &amp; <code>.obj</code> / <code>.vrm</code> 3D Shadows)</b></summary>

| Parameter (EN / JA) | Range / Options | Default | Description & Usage Tips |
| :--- | :--- | :---: | :--- |
| **Foreground Occlusion** (`前景遮蔽`) | `Off` / `3D Shadow Shafts Only` / `Foreground Silhouette Mask Only` / `3D Shadow + Foreground Mask (Rec.)` | `Off` | Enables volumetric light blocking by foreground objects. `3D Shadow + Foreground Mask (Rec.)` gives the most natural result. |
| **3D Object Mode** (`3Dオブジェクト処理`) | `Auto (VRM Holdout + OBJ Depth)`<br>`Physical 3D Depth (Keep Front Beam)`<br>`Crisp Holdout (Cut Front Beam)`<br>`Ignore 3D Objects (2D Only)` | `Auto` | Controls depth vs. silhouette holdout for `.obj` and `.vrm` models (`Auto` keeps VRM characters crisp while allowing beams in front of `.obj` scenery). |
| **Occluder Scope** (`遮蔽対象`) | `Auto (All Foreground Layers)`<br>`Auto (Between Source & Self)`<br>`Specify Layer Numbers` | `Auto (All)` | Selects which layers cast shadows (companion Volumetric Fog & Snow layers are skipped automatically). |
| **Occluder Layers** (`指定レイヤー`) | Text (up to 8 layers) | `4 5` | *(When Specify Layer Numbers is selected)* Supports spaces, commas, and ranges (e.g. `4 5` or `2,4-6`). |
| **Shadow Softness** (`影の柔らかさ`) | `0 – 100` | `8.0` | Penumbra softness along 3D shadow shaft edges. |
| **Shadow Strength** (`遮蔽の強さ`) | `0 – 100%` | `100.0` | Opacity of shadow attenuation (`100%` = full physical occlusion). |

</details>

<details>
<summary><b>🎨 Group 4: Foreground Blend (<code>前景なじませ</code> — 14 Parameters: AutoBlendLight 3D Backlight &amp; Rim Integration)</b></summary>

| Parameter (EN / JA) | Range / Options | Default | Description & Usage Tips |
| :--- | :--- | :---: | :--- |
| **Enable Foreground Blend** (`前景なじませを有効化`) | `ON / OFF` | `OFF` | Wraps target foreground 2D layers in scene & 3D volumetric light so cutouts sit naturally in the beam. |
| **Blend Target Scope** (`なじませ対象`) | `Auto (All Foreground 2D Layers)` / `Specify Layer Range / Numbers` | `Auto` | Choose `Specify Layer Range / Numbers` to blend only specific character/prop layers. |
| **Target Layer Range** (`対象レイヤー指定`) | Text | `4-5` | *(When Specify is selected)* Supports single layers, ranges, and lists (`3`, `4-5`, `3,5,7-9`). Frontmost non-target layers remain untouched. |
| **Base Brightness** (`ベース明るさ`) | `0 – 200%` | `85` | Base albedo brightness of target 2D layers (`75–90%` enhances dramatic backlit contrast). |
| **Color Pickup Mode** (`カラーモード`) | `Single Color` / `Dual Color (Left/Right)` / `Volumetric + Scene Dual Sync (Rec.)` | `Volumetric + Scene Dual Sync` | Samples directional colors from both the 3D volumetric beam and background scene. |
| **Rim Light Style** (`ライトスタイル`) | `Gradient Wrap (Backlight)` / `Gradient Fill (Normal)` / `Outline Rim (Edge Highlight)` | `Gradient Wrap (Backlight)` | Choose between soft backlit light wrap or crisp silhouette rim highlights. |
| **Rim Light Strength** (`ライト強さ`) | `0 – 200%` | `100` | Overall strength of the rim / edge light wrap. |
| **Light Diffusion** (`拡散幅`) | `0 – 200` | `35` | Inward diffusion width or rim thickness. |
| **Bias (Outer <-> Inner)** (`偏り(外⇔内)`) | `-100 – 100` | `-60` | Negative shifts toward outer edge glow; positive wraps deeper into the interior. |
| **Natural Color Tune** (`自然補正`) | `0 – 200` | `100` | Tames oversaturated colors for natural light wrap (`100` = balanced, `200` = neutral white). |
| **Rim Saturation** (`彩度`) | `0 – 200%` | `100` | Saturation multiplier for sampled rim light colors. |
| **Scene Brightness Link** (`背景明るさ連動`) | `ON / OFF` | `ON` | Scales rim light brightness automatically according to surrounding scene & beam luminance. |
| **Environment Blend** (`環境なじませ量`) | `0 – 100%` | `30` | Multiplicative ambient color tinting sampled from the foreground-excluded blurred background. |
| **Environment Blur** (`環境ぼかし`) | `0 – 500` | `160` | Spatial blur radius of the ambient color field. |

</details>

<details>
<summary><b>🌫️ Group 5: Internal Fog &amp; Noise (<code>霧の設定</code> — 5 Parameters: 3D Smoke &amp; Atmospheric Dust)</b></summary>

| Parameter (EN / JA) | Range / Options | Default | Description & Usage Tips |
| :--- | :--- | :---: | :--- |
| **Fog Amount** (`霧`) | `0 – 100%` | `0.0` | Density of animated 3D smoke/dust modulation inside the beam (`0%` = pure smooth rays with zero noise cost). |
| **Fog Scale** (`霧の大きさ`) | `10 – 2000` | `220` | Spatial scale of the 3D fog pattern (pixels). |
| **Wind Angle** (`風向き`) | `-360° – 360°` | `25.0` | Drift direction angle for internal fog animation. |
| **Wind Speed** (`風の速さ`) | `-5000 – 5000` | `45` | Drift speed in pixels per second. |
| **Random Seed** (`シード`) | `-1000 – 1000` | `0` | Random pattern seed for the 3D noise field. |

</details>

<details>
<summary><b>⚙️ Group 6: Quality &amp; Display (<code>品質</code> — 6 Parameters: Resolution, Dynamic Slices &amp; Diagnostic HUD)</b></summary>

| Parameter (EN / JA) | Range / Options | Default | Description & Usage Tips |
| :--- | :--- | :---: | :--- |
| **Resolution** (`解像度`) | `High` / `Standard` / `Lightweight (1/2)` | `Standard` | Volume atlas & shadow map resolution preset (`Lightweight` for fast scrubbing; `Standard` / `High` for final export). |
| **Auto Dynamic Precision** (`自動動的精度`) | `ON / OFF` | `ON` | Automatically scales slice count continuously between `64` and `192` based on beam depth. |
| **Slice Count** (`スライス数`) | `16 – 64` | `64` | *(When Auto Dynamic Precision is OFF)* Manual billboard slice count. |
| **Slice Alignment** (`視点追従`) | `Auto (3D Camera & Edit View)` / `Side Slices (X-Axis)` | `Auto` | Aligns slices perpendicular to the active 3D camera ray or along X-axis planes. |
| **Show Status HUD** (`画面に状態表示`) | `ON / OFF` | `OFF` | Renders a top-left diagnostic HUD showing GPU mode (`,CS`), atlas size, occluders, and cache usage (**turn OFF before exporting**). |
| **Log to Console** (`ログに出力`) | `ON / OFF` | `ON` | Outputs status summaries to AviUtl2's Log Window. |

</details>

---

## 6. Reverse-Engineering / Memory-Probe Compatibility Risks

AviUtl ExEdit2 is actively evolving, and **the official Plugin SDK does not yet expose APIs for certain internal host states** — specifically the deferred G-Buffer 3D light array inside Camera Control (`カメラ制御`) and the interactive Editor Free-Camera ("green camera icon") view matrix. To bridge these gaps today, optional sub-features use **read-only binary memory probes** calibrated on `aviutl2.exe` (`v2.1.15`).

We disclose the exact scope of version-sensitivity transparently below:

| Feature Module | Implementation | Risk on Future `aviutl2.exe` Updates |
| :--- | :--- | :--- |
| **Core 3D Volumetric Rays, Mips & 3D Fog Noise**<br>**2D Silhouette Occlusion & Foreground Blend (`AutoBlendLight`)** | 100% Official Script APIs + HLSL Pixel Shaders | **Minimal / Stable** |
| **`VL3DBridge.aux2` Lock-Free Layer / Text / Water / Camera Read**<br>**`D3D11 ComputeShader (cs_5_0)` Hardware 3D Shadow Rasterizer** | 100% Official Plugin SDK (`plugin2.h` / `module2.h` `2026-10-24`) | **Minimal / Stable** (Official SDK ABI) |
| **Auto-Detecting 3D Lights Inside Camera Control (`Camera 3D Light Sync`)** | Read-only memory probe of host G-Buffer light array | **May temporarily stop detecting in-camera 3D lights** if internal struct offsets change |
| **Editor Free-Camera ("Green Camera") Tracking & Standalone Unsaved Scene Probe** | Read-only memory probe + `.aup2` fallback | **May degrade to render-camera alignment / saved `.aup2` fallback** if offsets shift |

### 🛡️ Crash-Free Safety & Automatic Fallback Guarantees

1. **Zero Memory Writes**: Neither `体積光（3D）.obj2` nor `VL3DBridge.aux2` ever writes to or hooks `aviutl2.exe` memory. All probes use kernel-validated `ReadProcessMemory` calls that safely return failure codes without raising access-violation crashes.
2. **PE Fingerprint & Machine-Code Signature Gating**: Whenever AviUtl2 updates and any binary signature fails validation, `VL3DBridge.aux2` **automatically disables all memory probes and falls back cleanly to Pure Official SDK Mode** — your `.aup2` projects will not crash or corrupt, and core volumetric rays, 2D/3D shadows, `cs_5_0` GPU acceleration, and Foreground Blend continue working normally.
3. **Strict Whitelist Option**: Set `strict_whitelist_only = 1` in `Plugin\VL3DBridge.ini` to restrict probes exclusively to SHA-256-verified `aviutl2.exe` builds.

---

## 7. Future Roadmap

- **3D Optical Coupling with Companion `Volumetric Fog (3D)` & `Snow (3D)` Scripts**: Sharing the 3D volumetric light field via `VL3DBridge` so **`Volumetric Light (3D)` beams can locally illuminate 3D Volumetric Fog and tint falling 3D Snowflakes passing through light shafts in real time**.
- **Full Migration to Official Plugin SDK APIs**: Replacing remaining read-only memory probes immediately as KEN-kun exposes official SDK interfaces for Camera Control 3D lights, editor viewport matrices, and 3D mesh buffers.
- **Broader 3D Asset & Multi-Light Support**: Native `.glb` / `.gltf` mesh rasterization inside `VL3DBridge`'s D3D11 ComputeShader pipeline, multi-light simultaneous emission, and colored translucent shadow shafts (e.g., stained-glass caustics).
- **Unified ComputeShader Volumetric Integration**: Exploring `ID3D11Device3` compute-shader froxel/ray-marching integration to further reduce cache bandwidth on high-FPS `4K` / `8K` timelines.
- **Expanded Presets & Localization**: Stage spotlights, window sunbeams, underwater shafts, and continued community localization polish.

---

## 8. Reporting Bugs

- **[GitHub Issues](../../issues) is the best channel!** Please include:
  - the script version (turn on 「画面に状態表示 / status overlay」 to show it in the top-left HUD, e.g. `v3.06`) and your AviUtl ExEdit2 version;
  - steps to reproduce, a screenshot of your timeline layer layout, and of the visual glitch (**keep the HUD visible** — fields like `遮蔽=…` / `光源=…` are key clues!);
  - if the HUD occlusion tag shows `,opq` (mask nearly fully opaque — check that the occluder image has an alpha channel) or `L4*` (that layer fell back to raw image load), mention it too ^_^
- **Discord community**: you can also join the Japanese AviUtl2 community server **「AviUtl2の情報が知りたいだろって‼」** ([invite link](https://discord.gg/8qzmGY5NgQ)) and ping **南宫若馨Cylna** (`@nangongerregtcylna`, the author) directly!

---

## 9. Building `VL3DBridge.aux2` & `.au2pkg.zip` Packages

Full C++17 source code, embedded HLSL `cs_5_0` shaders, and official AviUtl2 Plugin SDK headers are provided under [`src/VL3DBridge/`](../src/VL3DBridge/), and the `.au2pkg.zip` builder script is provided under [`packaging/`](../packaging/) (no proprietary `aviutl2.exe` binaries are included).

```bash
# 1. Build VL3DBridge.aux2 with CMake (MSVC or MinGW-w64)
cmake -S src/VL3DBridge -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release

# (Or build directly via MinGW-w64 helper script)
./src/VL3DBridge/build_mingw64.sh

# 2. Generate ready-to-install .au2pkg.zip packages (output to dist/)
python3 packaging/build_au2pkg.py
```

---

## 10. Authors, Acknowledgements & License

### 👩‍💻 Authors & Co-Development
- **Cylna Nangong (南宫若馨Cylna)**
  - [Bilibili](https://space.bilibili.com/81615223) ｜ [YouTube](https://www.youtube.com/@CylnaNangong) ｜ [X (Twitter)](https://x.com/CylnaNangong) ｜ [Twitch](https://www.twitch.tv/cylna_nangong) ｜ [GitHub](https://github.com/CylnaNangong)
- **Arena.ai Agent** (Co-developer — core architecture redesign, `VL3DBridge` GPU acceleration & 3D integration)

### 🙏 Acknowledgements

#### Community Prior Art & Open-Source Foundations
- **[nctype](https://github.com/nctype)** — Foreground Blend's color-confidence weighting, backlight chromaticity tuning, and environment light-wrap pipeline are adapted from [`AutoBlendLight`](https://github.com/nctype/aviutl2_script_AutoBlendLight) (`v1.3.3`, MIT License).
- **[HexBrowns](https://github.com/HexBrowns)** — Beam parameter conventions, falloff curve, luminance threshold, and 3D fog noise draw inspiration from `光束_H` (MIT-0) and [`EffectUtils_Budget.lua`](https://github.com/HexBrowns/HexScript/blob/main/Script/EffectUtils_Budget.lua) in [`HexScript`](https://github.com/HexBrowns/HexScript).
- **[IZUisMe](https://github.com/IZUisMe)** ([`Open Glow r3`](https://github.com/IZUisMe/aviutl2-open-glow)), **[HexBrowns](https://github.com/HexBrowns)** ([`HexScript`](https://github.com/HexBrowns/HexScript)), and **[CylnaNangong](https://github.com/CylnaNangong)** ([`aviutl2-open-glow-budget-fix`](https://github.com/CylnaNangong/aviutl2-open-glow-budget-fix)) — `TemporaryImageCacheSize` (16M-pixel) cache-budget engineering insights.
- **[nove-nove](https://github.com/nove-nove)** ([`VRM Toolkit for AviUtl2`](https://github.com/nove-nove/aviutl2-vrm-toolkit)) — `.vrm` 3D avatar & `VRMToolkitBridge` ecosystem integration.
- **KEN-kun (ＫＥＮくん)** ([Official Website](https://spring-fragrance.mints.ne.jp/aviutl/) ｜ [X `@__kenkun`](https://x.com/__kenkun)) — Creator of AviUtl ExEdit2 and the official AviUtl2 Plugin SDK.

#### AI Collaboration Credits
- **Arena.ai Agent** — Co-designed and implemented the `v2.90`–`v3.06` architecture, `VL3DBridge.aux2` lock-free bridge & D3D11 ComputeShader rasterizer, `AutoBlendLight` 3D integration, and extreme-resolution cache hardening.
- **ChatGPT** — Code review & bug hunting (spotted the trilinear grid-vertex coordinate bug in the 3D fog noise shader!).
- **Gemini** — Authored the initial HLSL volumetric fog prototype (making the companion *Volumetric Fog (3D)* script a happy byproduct of this project XD).
- **Microsoft Copilot** — Patiently helped investigate cache-overflow issues and organize notes when high-resolution images first broke `OpenGlow`.

#### Special Thanks
- **[JS小僵尸](https://space.bilibili.com/90326786)** — Huge thanks to our wonderfully patient commissioner for tolerating our schedule delays! Without your PV commission sending me on a quest for the right atmospheric lighting tools, I never would have teamed up with AI to build the *Volumetric Light (3D)*, *Volumetric Fog (3D)*, and *Snow (3D)* suite from scratch!
- **Demo Showcase Credits**: Featuring our original DiffSinger AI virtual singer **Nangong Yan (南宫颜)** ([Official Website](https://cylnanangong.wixsite.com/ngyy) / [GitHub English Dataset](https://github.com/CylnaNangong/NGYY_ENG_Dataset)), with character illustration created via [neka.cc](https://www.neka.cc/) by artist **Litangfeng (离堂风)**.

### 📄 License
Released under the **[MIT License](../LICENSE)**. See **[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)** for full third-party copyright notices and license texts.
Changelogs: **[Japanese CHANGELOG](CHANGELOG_ja.md)** ｜ **[Chinese CHANGELOG](CHANGELOG_zh.md)**
