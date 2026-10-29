"""High level convenience layer: build scenes, run frames, save artefacts."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import assets
from .hlsl.executor import ShaderLibrary, extract_shaders
from .host import Host, HostConfig, Layer
from .native import NativeBridge
from .obj2 import Obj2Script, parse_obj2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPT = REPO_ROOT / "Script" / "VolumetricLight3D.obj2"
OUT_ROOT = Path(__file__).resolve().parents[1] / "out"


# ---------------------------------------------------------------------------
# Reusable caches (shader build + native build are expensive)
# ---------------------------------------------------------------------------
_libs: Dict[str, ShaderLibrary] = {}
_native: Optional[NativeBridge] = None
_parsed: Dict[str, Obj2Script] = {}


def script(path: Optional[Path] = None) -> Obj2Script:
    p = Path(path or DEFAULT_SCRIPT)
    key = str(p.resolve())
    if key not in _parsed:
        _parsed[key] = parse_obj2(p)
    return _parsed[key]


def shader_library(path: Optional[Path] = None) -> ShaderLibrary:
    p = Path(path or DEFAULT_SCRIPT)
    key = str(p.resolve())
    if key not in _libs:
        _libs[key] = ShaderLibrary(extract_shaders(script(p).text))
    return _libs[key]


def native() -> Optional[NativeBridge]:
    global _native
    if _native is None:
        try:
            _native = NativeBridge()
        except Exception:
            _native = None
    return _native


# ---------------------------------------------------------------------------
# Scene builder
# ---------------------------------------------------------------------------
def standard_scene(w: int = 320, h: int = 200, *, with_occluder: bool = True,
                   occluder_z: float = 0.0, light_size: int = 160) -> List[Layer]:
    """Layer 1 = stained-glass light source, layer 3 = foreground silhouette."""
    layers = [Layer(index=1, image=assets.stained_window(light_size, light_size), kind="image")]
    if with_occluder:
        layers.append(Layer(index=3, image=assets.silhouette(90, 150),
                            kind="image", x=20, y=15, z=occluder_z))
    return layers


@dataclass
class RenderResult:
    frame: int
    volume: Optional[np.ndarray]          # (h,w,4) RGBA8 of the drawn volume light
    host: Host
    seconds: float
    log: List[str] = field(default_factory=list)

    @property
    def alpha(self) -> np.ndarray:
        return self.volume[..., 3] if self.volume is not None else np.zeros((0, 0), np.uint8)

    @property
    def rgb(self) -> np.ndarray:
        return self.volume[..., :3] if self.volume is not None else np.zeros((0, 0, 3), np.uint8)

    def stats(self) -> Dict[str, float]:
        a = self.alpha.astype(np.float32) / 255.0
        rgb = self.rgb.astype(np.float32) / 255.0
        return {
            "alpha_mean": float(a.mean()),
            "alpha_max": float(a.max()),
            "alpha_coverage": float((a > 2 / 255).mean()),
            "rgb_mean": float(rgb.mean()),
            "rgb_max": float(rgb.max()),
            "peak_cache_px": float(self.host.peak_cache_pixels),
            "quads": float(len(self.host.quads)),
        }


def run_frame(layers: Sequence[Layer], params: Optional[Dict[str, Any]] = None,
              *, width: int = 320, height: int = 200, frame: int = 0,
              layer_index: int = 4, scene_id: int = 0, frame_s: int = 0,
              camera: Optional[Dict[str, float]] = None, camera_mode: int = 0,
              gui: bool = True, bridge: bool = True, copybuffer: bool = False,
              script_path: Optional[Path] = None, extra_cfg: Optional[Dict[str, Any]] = None,
              host_factory=None) -> RenderResult:
    sc = script(script_path)
    cfg = HostConfig(screen_w=width, screen_h=height, gui=gui, camera_mode=camera_mode,
                     camera=camera, bridge=bridge, copybuffer_supported=copybuffer,
                     script_path=str(script_path or DEFAULT_SCRIPT),
                     cache_pixels=16_000_000)
    if extra_cfg:
        for k, v in extra_cfg.items():
            setattr(cfg, k, v)
    if host_factory is not None:
        host = host_factory(cfg)
    else:
        host = Host(sc, layers, cfg, shader_lib=shader_library(script_path),
                    params=params or {}, native=native() if bridge else None)
    host.layer_index = layer_index
    host.scene_id = scene_id
    host.frame_s = frame_s
    host.frame_e = 10 ** 9
    t0 = time.time()
    host.run_frame(frame)
    dt = time.time() - t0
    vol = host.volume_layer()
    return RenderResult(frame=frame, volume=vol, host=host, seconds=dt,
                        log=list(host.prints))


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def save_png(img: np.ndarray, path: Path, gain: float = 1.0) -> Path:
    from PIL import Image
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(arr * gain * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if arr.ndim == 3 and arr.shape[-1] == 4:
        Image.fromarray(arr, "RGBA").save(path)
    elif arr.ndim == 3 and arr.shape[-1] == 3:
        Image.fromarray(arr, "RGB").save(path)
    else:
        Image.fromarray(arr).save(path)
    return path


def composite(bg: Optional[np.ndarray], volume: Optional[np.ndarray], gain: float = 3.0) -> np.ndarray:
    """Preview composite the same way the AviUtl2 timeline would (approx.)."""
    if volume is None:
        return bg if bg is not None else np.zeros((1, 1, 4), np.uint8)
    H, W = volume.shape[:2]
    out = np.zeros((H, W, 4), np.float32)
    if bg is not None:
        b = bg.astype(np.float32) / 255.0
        hh, ww = min(H, b.shape[0]), min(W, b.shape[1])
        y0, x0 = (H - hh) // 2, (W - ww) // 2
        out[y0:y0 + hh, x0:x0 + ww] = b[:hh, :ww]
    v = volume.astype(np.float32) / 255.0
    a = np.clip(v[..., 3:4] * gain, 0, 1)
    out[..., :3] = v[..., :3] * a + out[..., :3] * (1 - a)
    out[..., 3] = np.maximum(out[..., 3], a[..., 0])
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def write_json(path: Path, obj) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
