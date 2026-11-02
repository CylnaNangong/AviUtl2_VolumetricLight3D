# VolumetricFog3D v2.06 — VolumetricLight3D shared-field integration

This directory contains the companion `VolumetricFog3D` source, language packs, and package builder. Its upstream project is [`CylnaNangong/AviUtl2_VolumetricFog3D`](https://github.com/CylnaNangong/AviUtl2_VolumetricFog3D).

## What changed

Fog v2.06 adds a **Scattering Light Source** selector while keeping the original manual direction as the default for old projects. The new **Link to VolumetricLight3D** mode consumes fresh `VL3DLF v3` packets from the current scene. It evaluates local light colour, beam direction, and projected shadow visibility at a 4×4 sample grid for each fog slice, then applies the existing GPU Henyey–Greenstein phase function at those spatial samples. If there is no current shared field, it falls back to the manual scattering controls.

Place the `VolumetricLight3D` layer above the fog layer for same-frame field updates. Use the existing standard blend mode (Normal) for the fog layer.

## Build the two install packages

```bash
python3 integrations/volumetricfog3d/apply_to_volumetricfog3d.py --build-pkg --out-dir dist
```

This produces `VolumetricFog3D-v2.06.au2pkg.zip` and `VolumetricFog3D-v2.06-ScriptOnly.au2pkg.zip`.

## Sync changes into the upstream checkout

```bash
python3 integrations/volumetricfog3d/apply_to_volumetricfog3d.py /path/to/AviUtl2_VolumetricFog3D --build-pkg
```

The helper synchronizes the script, language packs, package metadata, changelogs, third-party notices, and compatible optional VL3DBridge v1.22 files, then builds the packages in the target repository's `dist/` directory.

## Verification status

The shared-field receiver uses the same scene/frame validation and `global` slot protocol as Snow3D. The sandbox can validate packet decoding, per-slice sample layout, shader constants, and package contents. Final visual and performance verification still requires AviUtl ExEdit2 with VolumetricLight3D v3.16 and VolumetricFog3D v2.06 installed together.
