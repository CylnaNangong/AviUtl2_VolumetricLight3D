# VolumetricFog3D / Volumetric Fog (3D) / 体积雾（3D）

`VolumetricFog3D.obj2 v2.06` &nbsp;|&nbsp; `VL3DBridge.aux2 v1.22` (optional) &nbsp;|&nbsp; `AviUtl ExEdit2 v2.1.15+` &nbsp;|&nbsp; `MIT License`

[简体中文](../README.md) | [日本語](README_ja.md)

![Fog effect ON/OFF comparison](assets/comparison_off_on.jpg)

> 🌫️ **THE REAL 3D continuous volumetric fog custom object for AviUtl ExEdit2** — a GPU-integrated continuous 3D density field. No billboard-particle fakes.

## 1. Overview & Key Features

Generates a **continuous 3D atmospheric fog volume** directly inside AviUtl2's 3D camera space:

- **Continuous GPU density field**: HLSL pixel shaders compute 4-octave 3D fBm noise, Beer-Lambert transmittance, exponential height falloff, and Henyey-Greenstein forward scattering in real time — the fog is a true continuous medium.
- **Hardware Z-buffer true 3D occlusion**: every slice is drawn as a 3D polygon, interlocking natively with AviUtl2's hardware depth buffer — foreground subjects stay crisp while distant backgrounds sink into progressively deeper fog, automatically.
- **Editor-view 360° sphere mode**: detects the preview's editor viewpoint (green camera icon) and switches to a gaze-centered 3D spherical volume — orbit freely without mysterious stripes or cross-section corners.
- **Optional GPU acceleration**: pair with [`VL3DBridge.aux2`](https://github.com/CylnaNangong/VolumetricLight3D) (same plugin as VolumetricLight3D) for live editor-view sync; falls back to pure LuaJIT with full functionality when absent.

> 📜 **v2.05 change**: the script's identifier was renamed from 「体積霧（3D）」 to **`VolumetricFog3D`** (UI display names remain localized by language packs). Re-add the object in existing projects, or keep the old script installed side-by-side. `Volumetric Light (3D)` v3.07+ recognizes both names automatically.

> ✨ **v2.06 shared-light-field mode**: select “Link to VolumetricLight3D” under Scattering Light Source to sample local beam colour, travel direction, and projected shadows from `VL3DLF v3`. Manual direction remains the default; without a fresh field the effect falls back to manual scattering. The link requires VolumetricLight3D v3.16 or newer in the same scene.

## 2. System Requirements

| Item | Requirement |
|---|---|
| Host | AviUtl ExEdit2 `v2.1.15` or newer (by KEN-kun; install separately) |
| OS | Windows (Direct3D 11) |
| Optional | `VL3DBridge.aux2 v1.22` (editor-view acceleration); VolumetricLight3D v3.16+ for shared-field scattering |

## 3. Installation (`.au2pkg.zip` Drag-and-Drop Supported)

1. Download **`VolumetricFog3D-v2.06.au2pkg.zip`** (full edition, with optional GPU plugin) or **`VolumetricFog3D-v2.06-ScriptOnly.au2pkg.zip`**;
2. **Drag the zip onto the AviUtl2 window** — done (official drag-and-drop package format);
3. Manual: place `Script/VolumetricFog3D.obj2` and `Language/*.aul2` into `C:\ProgramData\aviutl2\Script\` and `C:\ProgramData\aviutl2\Language\`.

## 4. Quick Start

1. Add the **VolumetricFog3D** custom object on any layer (between background and subjects is recommended);
2. With a Camera Control object present it renders in true 3D perspective; the preview's editor viewpoint automatically engages sphere mode;
3. Tune 「濃さ (Density)」 and fog color. Choose Manual Direction for the original controls, or Link to VolumetricLight3D for local beam colour, direction, and shadow-aware scattering.

## 5. Complete Parameter Reference (Click to Expand)

<details>
<summary><b>Parameter list (defaults)</b></summary>

| Group | Parameter | Range (default) | Description |
|---|---|---|---|
| Fog | 濃さ Density | 0–100 (60) | Overall density |
| Fog | 霧の色 Color | — (#a8bcd8) | Base fog color |
| Fog | 模様の大きさ | 50–4000 (550) | Noise spatial scale |
| Fog | 模様の強さ | 0–100 (85) | Noise contrast (0 = uniform) |
| Flow | 風X/Y/Z Wind | ±2000 (15/0/0) | Wind velocity |
| Flow | 揺らぎ速さ | 0–10 (2) | Turbulence speed |
| Area | 手前の距離 / 奥行き | 0–20000 (150) / 100–30000 (3200) | Near distance / depth |
| Area | 画面の広がり | 100–250 (135) | Lateral coverage |
| Area | 高さ減衰 / 基準の高さY | 0–100 (0) / ±10000 (200) | Height falloff |
| Light | 散乱の強さ | 0–100 (25) | Back-scatter strength |
| Light | 光の色 / 前方散乱(g) / direction | — (#dce6ff / 0.45) | HG phase & direction |
| Render | スライス数 | 8–64 (48) | Volume slice count |
| Render | quality / view mode / depth fade / seed | — | Rendering options |

</details>

## 6. Compatibility & Internal Dependencies

- Editor-view detection relies on `VL3DBridge.aux2`'s **read-only** access to host internals; future AviUtl2 updates may break that specific feature (the fog itself keeps rendering correctly in standard camera mode).
- Works fully without the plugin (pure LuaJIT path).

## 7. Future Roadmap

- Cross-script 3D optics with sister projects [`VolumetricLight3D`](https://github.com/CylnaNangong/VolumetricLight3D) and [`Snow3D`](https://github.com/CylnaNangong/Snow3D) via `VL3DBridge`: **let volumetric light shafts illuminate and tint the fog volume in real time**.
- Colored volumetric fog (in-fog dispersion), volumetric self-shadowing.

## 8. Reporting Bugs

- **[GitHub Issues](../../issues) is the best channel!** Please include the script version (enable 「ログに出力」 to see `VolumetricFog3D v2.06 ...`), your AviUtl ExEdit2 version, reproduction steps, and screenshots.
- **Discord**: join the Japanese AviUtl2 community **「AviUtl2の情報が知りたいだろって‼」** ([invite](https://discord.gg/8qzmGY5NgQ)) and ping **南宫若馨Cylna** (`@nangongerregtcylna`, the author)!

## 9. Authors, Acknowledgements & License

- **Authors**: [Cylna Nangong](https://github.com/CylnaNangong) & **Arena.ai Agent**
- **Acknowledgements**: KEN-kun (AviUtl ExEdit2 & Plugin SDK); the AviUtl2 community ([nctype](https://github.com/nctype), [HexBrowns](https://github.com/HexBrowns), [IZUisMe](https://github.com/IZUisMe), …); **Gemini** (wrote the original HLSL fog shader); ChatGPT / Microsoft Copilot.
- **License**: MIT (see [LICENSE](../LICENSE) & [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)). AviUtl ExEdit2 is KEN-kun's software; this project is independent of it.
