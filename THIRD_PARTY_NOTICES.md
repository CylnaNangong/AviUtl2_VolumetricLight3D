# Third-Party Notices & Acknowledgements

`体積光（3D）` (`Volumetric Light (3D)`) and `VL3DBridge.aux2` incorporate or build upon the following open-source projects, algorithms, and SDK headers from the AviUtl ExEdit2 ecosystem.

---

## 1. AutoBlendLight (`AutoBlendLight.anm2` v1.3.3) — MIT License

- **Author**: nctype
- **Repository**: <https://github.com/nctype/aviutl2_script_AutoBlendLight>
- **Scope in this project**: The optional `前景なじませ` (`Foreground Blend` / `前景融入背景`) subsystem in `Script/体積光（3D）.obj2` adapts AutoBlendLight's background color confidence estimation, natural backlight/rim color tuning (`tune_backlight_color`), gradient/outline rim styling, and blurred environment multiply blending into a 3D volumetric-light-aware GPU pipeline.

```text
MIT License

Copyright (c) 2026 nctype

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 2. 光束_H & EffectUtils_Budget.lua (`HexScript`) — MIT No Attribution (MIT-0)

- **Author**: [HexBrowns](https://github.com/HexBrowns)
- **Repository**: <https://github.com/HexBrowns/HexScript>
- **Scope in this project**: Parameter ergonomics, distance falloff curve, highlight threshold extraction, internal 3D fog/noise styling, and cache budget discipline were inspired by `光束_H` (`MIT-0`) and `EffectUtils_Budget.lua`.

```text
MIT No Attribution

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## 3. AviUtl ExEdit2 TemporaryImageCacheSize Research & Ecosystem Interop

- **Authors / Projects**:
  - **[HexBrowns](https://github.com/HexBrowns)** — [`HexScript`](https://github.com/HexBrowns/HexScript) (`EffectUtils_Budget.lua`)
  - **[IZUisMe](https://github.com/IZUisMe)** — [`aviutl2-open-glow`](https://github.com/IZUisMe/aviutl2-open-glow) (`Open Glow r3`)
  - **[CylnaNangong](https://github.com/CylnaNangong)** — [`aviutl2-open-glow-budget-fix`](https://github.com/CylnaNangong/aviutl2-open-glow-budget-fix)
  - **[nove-nove](https://github.com/nove-nove)** — [`VRM Toolkit for AviUtl2`](https://github.com/nove-nove/aviutl2-vrm-toolkit) (`VRMToolkitModel` / `VRMToolkitBridge` runtime interop)
- **Acknowledgement**: Informed the strict `<= 16,000,000` pixel live cache ceiling, post-frame `1x1` slot release strategy, and optional `.vrm` 3D avatar occlusion support used in `体積光（3D）.obj2`.

---

## 4. AviUtl ExEdit2 Plugin SDK Headers (`plugin2.h`, `module2.h`)

- **Author**: ＫＥＮくん (KENkun)
- **Official Site**: <https://spring-fragrance.mints.ne.jp/aviutl/>
- **Scope in this project**: `src/VL3DBridge/include/aviutl2_sdk/plugin2.h` and `module2.h` are cross-platform C++17 header adaptations of the official AviUtl ExEdit2 Plugin SDK (`2026/10/24`, `v2.1.15` ABI) used to build `Plugin/VL3DBridge.aux2`. No AviUtl2 host binaries (`aviutl2.exe`) are included or redistributed.
