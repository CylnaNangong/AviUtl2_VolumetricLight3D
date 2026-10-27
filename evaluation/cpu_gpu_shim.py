#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPU GPU shim for the VolumetricLight3D Sandbox.

Replaces VulkanGpuBackend's wgpu device with a numpy CPU implementation that
supports the exact API subset used by aviutl2_sandbox.py:

  * textures (rgba8unorm / rgba16float / depth32float), views, write/read_texture
  * uniform buffers, bind groups, pipelines (fullscreen ps_* passes + poly passes)
  * render passes with load/clear, alpha/add/screen blend, depth test/write
  * fs_poly fragment semantics (premultiplied, shader-118 OIT, untextured vcol)

Pixel-shader ("ps_*") full-screen passes are approximated with an identity
pass (dst := blend(src0, dst)) because executing SPIR-V on CPU is out of scope.
This preserves the full Lua control flow, buffer/cache lifecycle, call
sequence and CPU-side rasterization while producing approximately-composited
images. Structural assertions (errors, logs, HUD, cache budget, fallbacks,
shadow-map contents) remain fully valid.
"""
import numpy as np

# ---------------------------------------------------------------------------
# CPU resource objects
# ---------------------------------------------------------------------------


class CPUShaderModule:
    def __init__(self, tag):
        self.tag = tag


class CPUTexture:
    def __init__(self, w, h, fmt):
        self.w, self.h, self.fmt = w, h, fmt
        if fmt == "depth32float":
            self.data = np.ones((h, w), dtype=np.float32)
        else:
            self.data = np.zeros((h, w, 4), dtype=np.float32)
        self._view = CPUView(self)

    def create_view(self):
        return self._view

    def from_bytes(self, data, bytes_per_row=None):
        if self.fmt == "rgba8unorm":
            arr = np.frombuffer(data, dtype=np.uint8)
            n = self.w * self.h * 4
            if arr.size < n:
                arr = np.concatenate([arr, np.zeros(n - arr.size, dtype=np.uint8)])
            self.data = arr[: n].reshape(self.h, self.w, 4).astype(np.float32) / 255.0
        elif self.fmt == "rgba16float":
            arr = np.frombuffer(data, dtype=np.float16)
            n = self.w * self.h * 4
            if arr.size < n:
                arr = np.concatenate([arr, np.zeros(n - arr.size, dtype=np.float16)])
            self.data = arr[: n].reshape(self.h, self.w, 4).astype(np.float32)
        elif self.fmt == "depth32float":
            arr = np.frombuffer(data, dtype=np.float32)
            n = self.w * self.h
            if arr.size < n:
                arr = np.concatenate([arr, np.zeros(n - arr.size, dtype=np.float32)])
            self.data = arr[: n].reshape(self.h, self.w).copy()
        else:
            raise ValueError(f"unsupported format {self.fmt}")

    def to_bytes(self):
        if self.fmt == "rgba8unorm":
            u8 = np.clip(np.round(self.data * 255.0), 0, 255).astype(np.uint8)
            return u8.tobytes()
        if self.fmt == "rgba16float":
            return self.data.astype(np.float16).tobytes()
        return self.data.astype(np.float32).tobytes()


class CPUView:
    def __init__(self, tex):
        self.tex = tex


class CPUBuffer:
    def __init__(self, data):
        if isinstance(data, np.ndarray):
            self.data = np.ascontiguousarray(data)
        else:
            self.data = np.frombuffer(data, dtype=np.uint8)


class CPUBindGroup:
    def __init__(self, entries):
        self.entries = {}
        for e in entries:
            res = e["resource"]
            if isinstance(res, dict) and "buffer" in res:
                self.entries[e["binding"]] = res["buffer"]
            else:
                self.entries[e["binding"]] = res  # view or sampler


class CPUPipeline:
    def __init__(self, is_poly, blend_state, depth_stencil, fs_entry):
        self.is_poly = is_poly
        self.blend = blend_state
        self.use_depth = depth_stencil is not None
        self.depth_write = bool(depth_stencil and depth_stencil.get("depth_write_enabled"))
        self.fs_entry = fs_entry


class CPURenderPass:
    """Executes draws eagerly (single-threaded in-order submission is equivalent)."""

    def __init__(self, device, color_view, load_op, clear_value, ds_view, ds_load, ds_clear):
        self.dev = device
        self.tex = color_view.tex
        if load_op == "clear":
            cv = clear_value if clear_value is not None else (0, 0, 0, 0)
            if self.tex.fmt == "depth32float":
                self.tex.data.fill(float(cv))
            else:
                self.tex.data[...] = np.array(cv, dtype=np.float32)
        self.ds_tex = ds_view.tex if ds_view is not None else None
        if self.ds_tex is not None and ds_load == "clear":
            self.ds_tex.data.fill(float(ds_clear if ds_clear is not None else 1.0))
        self.pipe = None
        self.bg = None
        self.vbuf = None

    def set_pipeline(self, pipe):
        self.pipe = pipe

    def set_bind_group(self, _idx, bg):
        self.bg = bg

    def set_vertex_buffer(self, _slot, buf):
        self.vbuf = buf

    def draw(self, n, *args):
        if self.pipe is None or self.bg is None:
            return
        if self.pipe.is_poly:
            if self.vbuf is None or n < 3:
                return
            self.dev._rasterize_polys(self, int(n))
        else:
            self.dev._fullscreen_ps_pass(self)

    def end(self):
        pass


class CPUEncoder:
    def __init__(self, device):
        self.device = device
        self.cmds = []

    def begin_render_pass(self, color_attachments, depth_stencil_attachment=None):
        att = color_attachments[0]
        ds = depth_stencil_attachment or {}
        return CPURenderPass(
            self.device,
            att["view"],
            att.get("load_op", "load"),
            att.get("clear_value"),
            ds.get("view"),
            ds.get("depth_load_op"),
            ds.get("depth_clear_value", 1.0),
        )

    def finish(self):
        return self.cmds


class CPUQueue:
    def __init__(self, device):
        self.device = device

    def write_texture(self, dst, data, layout, size):
        tex = dst["texture"]
        tex.from_bytes(bytes(data), layout.get("bytes_per_row"))

    def read_texture(self, src, layout, size):
        return src["texture"].to_bytes()

    def submit(self, cmds):
        pass  # draws executed eagerly


class CPUDevice:
    def __init__(self):
        self.queue = CPUQueue(self)

    # -- resource creation ---------------------------------------------------
    def create_shader_module(self, code=None):
        return CPUShaderModule("mod")

    def create_texture(self, size, format, usage=0):
        w, h = int(size[0]), int(size[1])
        return CPUTexture(w, h, format)

    def create_buffer_with_data(self, data, usage=0):
        return CPUBuffer(data)

    def create_bind_group_layout(self, entries=None):
        return ("bgl", tuple(sorted((e.get("binding", 0)) for e in (entries or []))))

    def create_pipeline_layout(self, bind_group_layouts=None):
        return ("pl", tuple(bind_group_layouts or ()))

    def create_sampler(self, **kw):
        mode = kw.get("address_mode_u", "clamp-to-edge")
        filtering = kw.get("mag_filter", "linear") == "linear"
        return ("smp", mode, filtering)

    def create_bind_group(self, layout=None, entries=None):
        return CPUBindGroup(entries or [])

    def create_render_pipeline(self, layout=None, vertex=None, fragment=None,
                               primitive=None, depth_stencil=None):
        is_poly = bool(vertex.get("buffers"))
        blend = None
        targets = fragment.get("targets") or []
        if targets and isinstance(targets[0], dict):
            blend = targets[0].get("blend")
        return CPUPipeline(is_poly, blend, depth_stencil, fragment.get("entry_point"))

    def create_command_encoder(self):
        return CPUEncoder(self)

    # -- execution ------------------------------------------------------------
    @staticmethod
    def _sample(tex, u, v, sampler):
        """Bilinear / nearest sampling with clamp or repeat; u,v arrays in [0,1]."""
        mode = sampler[1] if isinstance(sampler, tuple) else "clamp-to-edge"
        bilinear = sampler[2] if isinstance(sampler, tuple) and len(sampler) > 2 else True
        h, w = tex.h, tex.w
        if w <= 0 or h <= 0:
            return np.zeros(u.shape + (4,), dtype=np.float32)
        if mode == "repeat":
            u = np.mod(u, 1.0)
            v = np.mod(v, 1.0)
        else:
            u = np.clip(u, 0.0, 1.0 - 1e-9)
            v = np.clip(v, 0.0, 1.0 - 1e-9)
        fx = u * w - 0.5
        fy = v * h - 0.5
        x0 = np.floor(fx).astype(np.int64)
        y0 = np.floor(fy).astype(np.int64)
        if mode == "repeat":
            x0 = np.mod(x0, w)
            y0 = np.mod(y0, h)
            x1 = np.mod(x0 + 1, w)
            y1 = np.mod(y0 + 1, h)
        else:
            x0 = np.clip(x0, 0, w - 1)
            y0 = np.clip(y0, 0, h - 1)
            x1 = np.clip(x0 + 1, 0, w - 1)
            y1 = np.clip(y0 + 1, 0, h - 1)
        if not bilinear:
            return tex.data[y0, x0]
        tx = (fx - x0)[..., None]
        ty = (fy - y0)[..., None]
        c00 = tex.data[y0, x0]
        c01 = tex.data[y0, x1]
        c10 = tex.data[y1, x0]
        c11 = tex.data[y1, x1]
        c0 = c00 * (1 - tx) + c01 * tx
        c1 = c10 * (1 - tx) + c11 * tx
        return c0 * (1 - ty) + c1 * ty

    def _fullscreen_ps_pass(self, rp):
        """Approximate ps_* fullscreen pass: out := blend(src0, dst) identity (tiled)."""
        tex = rp.tex
        if tex.fmt == "depth32float" or tex.data.ndim != 3:
            return
        h, w = tex.h, tex.w
        tex_views = [(b, r) for b, r in rp.bg.entries.items()
                     if isinstance(r, CPUView)]
        sampler = None
        for b, r in rp.bg.entries.items():
            if isinstance(r, tuple) and r and r[0] == "smp":
                sampler = r
        src = None
        if tex_views:
            tex_views.sort(key=lambda t: t[0])
            src = tex_views[0][1].tex
        smp = sampler or ("smp", "clamp-to-edge", True)
        blend = rp.pipe.blend
        tile = 256
        for y0 in range(0, h, tile):
            y1 = min(h, y0 + tile)
            th = y1 - y0
            xx = np.arange(w, dtype=np.float32)[None, :].repeat(th, 0)
            yy = np.arange(y0, y1, dtype=np.float32)[:, None].repeat(w, 1)
            u = (xx + 0.5) / np.float32(w)
            v = (yy + 0.5) / np.float32(h)
            if src is not None and src.data.ndim == 3:
                out = self._sample(src, u, v, smp)
                if out.shape[:2] != (th, w):
                    out = np.resize(out.astype(np.float32), (th, w, 4))
            else:
                out = np.zeros((th, w, 4), dtype=np.float32)
            self._blend_into(tex.data[y0:y1], out, blend, None, None)

    def _rasterize_polys(self, rp, n_verts):
        verts = rp.vbuf.data.astype(np.float64).reshape(-1, 10)
        n = min(len(verts), int(n_verts))
        tex = rp.tex
        if tex.data.ndim != 3:
            return
        h, w = tex.h, tex.w
        udata = None
        buf0 = rp.bg.entries.get(0)
        if isinstance(buf0, CPUBuffer):
            udata = buf0.data.astype(np.float32).ravel()
        src_view = rp.bg.entries.get(1)
        src = src_view.tex if isinstance(src_view, CPUView) else None
        sampler = rp.bg.entries.get(2)
        s118 = float(udata[0]) if udata is not None and udata.size > 0 else 0.0
        poly_alpha = float(udata[1]) if udata is not None and udata.size > 1 else 1.0
        gain = np.array([udata[2] if udata.size > 2 else 1.0,
                         udata[3] if udata.size > 3 else 1.0,
                         udata[4] if udata.size > 4 else 1.0], dtype=np.float32)
        untextured = float(udata[5]) if udata is not None and udata.size > 5 else 0.0

        blend = rp.pipe.blend
        depth = rp.ds_tex
        for t0 in range(0, n - 2, 3):
            v0, v1, v2 = verts[t0], verts[t0 + 1], verts[t0 + 2]
            cw = np.array([v0[3], v1[3], v2[3]])
            if np.any(np.abs(cw) < 1e-12):
                continue
            ndc = np.stack([v0[:4] / v0[3], v1[:4] / v1[3], v2[:4] / v2[3]])
            sx = (ndc[:, 0] * 0.5 + 0.5) * w
            sy = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * h
            z = ndc[:, 2]
            xs, ys = np.array([sx[0], sx[1], sx[2]]), np.array([sy[0], sy[1], sy[2]])
            minx = max(int(np.floor(xs.min())), 0)
            maxx = min(int(np.ceil(xs.max())), w - 1)
            miny = max(int(np.floor(ys.min())), 0)
            maxy = min(int(np.ceil(ys.max())), h - 1)
            if minx > maxx or miny > maxy:
                continue
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-12:
                continue
            pc = np.stack([np.arange(minx, maxx + 1)]).repeat(maxy - miny + 1, 0).ravel() + 0.5
            pr = np.tile(np.arange(miny, maxy + 1), maxx - minx + 1) + 0.5
            w0 = ((ys[1] - ys[2]) * (pc - xs[2]) + (xs[2] - xs[1]) * (pr - ys[2])) / d
            w1 = ((ys[2] - ys[0]) * (pc - xs[2]) + (xs[0] - xs[2]) * (pr - ys[2])) / d
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
            if not inside.any():
                continue
            # perspective-correct attribute interpolation
            iw = 1.0 / cw
            b0, b1, b2 = w0 * iw[0], w1 * iw[1], w2 * iw[2]
            bsum = b0 + b1 + b2
            b0n, b1n, b2n = b0 / bsum, b1 / bsum, b2 / bsum
            uv = (v0[4:6] * b0n[:, None] + v1[4:6] * b1n[:, None] + v2[4:6] * b2n[:, None])
            vc = (v0[6:10] * b0n[:, None] + v1[6:10] * b1n[:, None] + v2[6:10] * b2n[:, None])
            zz = z[0] * w0 + z[1] * w1 + z[2] * w2

            pix_y = pr[inside].astype(np.int64)
            pix_x = pc[inside].astype(np.int64)
            uvn = uv[inside]
            vcn = vc[inside]
            zn = zz[inside]
            if rp.pipe.use_depth and depth is not None:
                dpass = zn <= depth.data[pix_y, pix_x] + 1e-9
                if not dpass.any():
                    continue
                pix_y, pix_x, uvn, vcn, zn = pix_y[dpass], pix_x[dpass], uvn[dpass], vcn[dpass], zn[dpass]
                if rp.pipe.depth_write:
                    depth.data[pix_y, pix_x] = zn
            if untextured > 0.5:
                rgb = vcn[:, 0:3] * poly_alpha
                al = vcn[:, 3:4] * poly_alpha
                keep = (al[:, 0] > 1e-6) | (rgb.sum(axis=1) > 1e-6)
                frag = np.concatenate([rgb, al], axis=1)[keep]
                if frag.shape[0] == 0:
                    continue
                py, px = pix_y[keep], pix_x[keep]
                self._blend_into_rows(tex.data, py, px, frag, blend)
                continue
            if src is not None:
                tcol = self._sample(src, uvn[:, 0].astype(np.float32),
                                    uvn[:, 1].astype(np.float32),
                                    sampler or ("smp", "clamp-to-edge", True))
            else:
                tcol = np.ones((len(uvn), 4), dtype=np.float32)
            pa = poly_alpha * vcn[:, 3]
            if s118 > 0.5:
                a118 = tcol[:, 3] * pa
                keep = a118 > 0.001
                rgb = tcol[:, 0:3] * vcn[:, 0:3] * pa[:, None] * gain[None, :]
                frag = np.concatenate([rgb, a118[:, None]], axis=1)
            else:
                out_a = tcol[:, 3] * pa
                keep = (out_a > 1e-6) | (tcol[:, 0:3].sum(axis=1) > 1e-6)
                rgb = tcol[:, 0:3] * vcn[:, 0:3] * pa[:, None]
                frag = np.concatenate([rgb, out_a[:, None]], axis=1)
            if keep.shape[0] == 0 or not keep.any():
                continue
            frag = frag[keep]
            py, px = pix_y[keep], pix_x[keep]
            self._blend_into_rows(tex.data, py, px, frag, blend)

    @staticmethod
    def _blend_op(dst_rows, src, blend):
        if blend is None:
            return src
        cs = blend["color"]["src_factor"]
        cd = blend["color"]["dst_factor"]
        a_op = blend["alpha"]
        out = np.empty_like(src)
        if cs == "one" and cd == "zero":
            out = src
        elif cs == "one" and cd == "one":
            out = dst_rows + src
        elif cs == "one" and cd == "one-minus-src-alpha":
            out = src + dst_rows * (1.0 - src[:, 3:4])
        elif cs == "one" and cd == "one-minus-src":
            out = src + dst_rows * (1.0 - src)
        else:
            out = src + dst_rows * (1.0 - src[:, 3:4])
        # alpha channel per alpha blend state
        if a_op["dst_factor"] == "one":
            out[:, 3] = src[:, 3] + dst_rows[:, 3] if a_op["src_factor"] == "one" else dst_rows[:, 3]
        elif a_op["dst_factor"] == "zero":
            out[:, 3] = src[:, 3]
        elif a_op["dst_factor"] == "one-minus-src":
            out[:, 3] = src[:, 3] + dst_rows[:, 3] * (1.0 - src[:, 3])
        elif a_op["dst_factor"] == "one-minus-src-alpha":
            out[:, 3] = src[:, 3] + dst_rows[:, 3] * (1.0 - src[:, 3])
        else:
            out[:, 3] = np.minimum(1.0, src[:, 3] + dst_rows[:, 3])
        return out

    @classmethod
    def _blend_into_rows(cls, texdata, py, px, frag, blend):
        dst_rows = texdata[py, px]
        texdata[py, px] = cls._blend_op(dst_rows, frag.astype(np.float32), blend)

    @classmethod
    def _blend_into(cls, texdata, frag, blend, py, px):
        if py is None:
            texdata[...] = cls._blend_op(texdata, frag, blend)
        else:
            texdata[py, px] = cls._blend_op(texdata[py, px], frag, blend)


# ---------------------------------------------------------------------------
# Patch VulkanGpuBackend
# ---------------------------------------------------------------------------


def install_cpu_backend(aviutl2_sandbox_mod):
    """Patch aviutl2_sandbox.VulkanGpuBackend to run on the CPU device."""
    VGB = aviutl2_sandbox_mod.VulkanGpuBackend

    def cpu_init(self):
        self.device = CPUDevice()
        self.shader_cache = {}
        self.ps_pipeline_cache = {}
        self.fs_quad_vs = self.device.create_shader_module(code="cpu-fs-quad")
        self.poly_mod = self.device.create_shader_module(code="cpu-poly")
        self.poly_bgl = self.device.create_bind_group_layout(entries=[])
        self.poly_layout = self.device.create_pipeline_layout(bind_group_layouts=[self.poly_bgl])
        self.poly_pipes = {}
        self.samplers = {
            "clamp": ("smp", "clamp-to-edge", True),
            "dot": ("smp", "clamp-to-edge", False),
            "loop": ("smp", "repeat", True),
        }
        self.dummy_tex = self.device.create_texture(size=(1, 1, 1), format="rgba8unorm", usage=0)
        self.dummy_view = self.dummy_tex.create_view()

    VGB.__init__ = cpu_init
    VGB._instance = None

    def cpu_compile_hlsl(self, name, hlsl_code):
        import hashlib
        import pathlib
        import re
        h = hashlib.sha256((name + "::" + hlsl_code).encode("utf-8")).hexdigest()[:24]
        if h in self.shader_cache:
            return self.shader_cache[h]
        # keep the HLSL source cached for inspection, but skip glslangValidator
        try:
            cache_dir = aviutl2_sandbox_mod.SPV_CACHE_DIR
            hlsl_path = cache_dir / f"{name}_{h}.hlsl"
            if not hlsl_path.exists():
                hlsl_path.write_text(hlsl_code, encoding="utf-8")
        except Exception:
            pass
        mod = self.device.create_shader_module(code=name)
        clean = re.sub(r"//.*", "", hlsl_code)
        clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.S)
        has_cbuffer = bool(re.search(r"register\s*\(\s*b0\s*\)", clean))
        tex_slots = sorted({int(m.group(1)) for m in re.finditer(r"register\s*\(\s*t(\d+)\s*\)", clean)})
        has_sampler = bool(re.search(r"register\s*\(\s*s0\s*\)", clean))
        comp = aviutl2_sandbox_mod.CompiledHLSL(
            name=name, spv_bytes=b"", module=mod,
            has_cbuffer=has_cbuffer, tex_slots=tex_slots, has_sampler=has_sampler,
        )
        self.shader_cache[h] = comp
        return comp

    VGB.compile_hlsl = cpu_compile_hlsl
    return VGB
