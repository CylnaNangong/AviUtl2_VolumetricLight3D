#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beam_brightness_twin.py — 体积光（3D）光束亮度「数字孪生」对比器 (CPU, 无需 GPU / AviUtl2)
=========================================================================================

目的
----
在完全相同的场景输入下，定量比较 ``Script/VolumetricLight3D.obj2`` 各历史版本
（v3.06 = README 演示动图源版本 … v3.13/HEAD）在**非聚光源路径**
（2D 图层光源 / 点光源 / 平行光，即 README 演示工程所用路径）下光束的最终亮度，
定位「最新版光束略微变暗」的引入点并验证修复方案。

复刻的代码（行号见各版本 .obj2）
--------------------------------
* Lua 增益链 (head L8957/L9031, v306 L8028/L8076):
    k_gain_base = (intensity/100)*0.155/L_char
    v3.06     : poly_alpha=0.27, min_opt_a=1/255, cam_comp=1
    v3.08+    : wboit_ka=0.24, poly_alpha=clamp(est*0.65,0.045,0.27),
                min_opt_a=ceil(0.00103*255/poly_alpha)/255
    k_gain_gpu = k_gain_base*(base_alpha/poly_alpha)*1.05*cam_comp
* beamatlas 输出级 v3.06 (L1486-1500):
    scaled=acc*(dz*k_gain_gpu)*fog*fg;  q=floor(sat(scaled)*255+d3)/255 (3 通道独立抖动)
    q_max<0.5/255 -> 0;  out_a = 1/255 (常量令牌)
* beamatlas 输出级 v3.08+ (head L2085-2120, 非聚光分支):
    单色 IG 抖动;  emit_s=max(scaled);  core_emit=min(1,w_core_acc*slab_gain)
    ka_eff=max(0.24, min_a*2.4/core_emit);  out_a=round(sat(emit_s*ka_eff)*255)/255
    **if (out_a < min_a-0.25/255) return 0**   ← 非聚光路径没有 v3.12 给聚光路径
    增加的「低亮度切片相干存活 + 色比 boost」分支 (head L2064-2075)
* 宿主 Shader #118 (aviutl2_sandbox.py L198-205 / v3.07 changelog):
    a118 = tex.a*poly_alpha <= 0.001 -> discard;  r4.rgb = tex.rgb*poly_alpha*H
    H = unlit_mul + amb + cl_rgb*Cone*Atten*N·L （宿主照明）
* 宿主合成 M1: 与仓库 GPU 沙箱 none_oit 管线一致的深度序 premultiplied over
* 宿主合成 M2: changelog 记载的 WBOIT 深度权重 w(z)=exp(-0.085z)（仅影响切片间
  权重，版本间比值与权重归一化无关）

用法
----
    python3 analysis/beam_brightness_twin.py
    python3 analysis/beam_brightness_twin.py --plot analysis/out/twin.png
"""
import argparse
import math

import numpy as np


# ---------------------------------------------------------------------------
# 1. Lua 侧常量（逐版本复刻）
# ---------------------------------------------------------------------------
def lua_constants(version, intensity=150.0, base_alpha=1.0, dz=16.4,
                  l_char=120.0, lum=1.0):
    """非聚光路径。返回 dict(poly_alpha, min_opt_a, cam_comp, wboit_ka, k_gain_gpu)。"""
    k_gain_base = (intensity / 100.0) * 0.155 / l_char
    poly_alpha, min_opt_a, cam_comp, wboit_ka = 0.27, 1.0 / 255.0, 1.0, 0.0
    if float(version) >= 3.08:
        wboit_ka = 0.24
        est = k_gain_base * base_alpha * 1.05 * dz * 1.6 * max(0.05, lum)
        if est < 0.030:
            poly_alpha = max(0.045, min(0.14, 0.14 - est * 3.1))
        else:
            poly_alpha = max(0.045, min(0.27, est * 0.65))
        min_opt_a = math.ceil(0.00103 * 255.0 / poly_alpha) / 255.0
    k_gain_gpu = k_gain_base * (base_alpha / poly_alpha) * 1.05 * cam_comp
    return dict(poly_alpha=poly_alpha, min_opt_a=min_opt_a, cam_comp=cam_comp,
                wboit_ka=wboit_ka, k_gain_gpu=k_gain_gpu, k_gain_base=k_gain_base)


# ---------------------------------------------------------------------------
# 2. beamatlas 输出级（输入: 单切片白光学厚度 W、源色最大通道 m）
#    返回 (量化色 q, alpha a)；q 已含宿主演示前的全部脚本侧数学
# ---------------------------------------------------------------------------
SRC_CH = np.array([1.0, 0.82, 0.64])                  # 演示源(彩绘玻璃暖色)通道比


def output_stage_v306(W, m, rng):
    scaled3 = np.clip(W * m * SRC_CH, 0, 1.0)
    if scaled3.max() <= 1e-6:
        return 0.0, 1.0 / 255.0
    d3 = rng.random(3)                                  # 三通道独立 U[0,1) 抖动
    q = float(np.floor(scaled3 * 255.0 + d3).max()) / 255.0
    if q < 0.5 / 255.0:
        return 0.0, 1.0 / 255.0
    return q, 1.0 / 255.0                               # 常量 alpha 令牌


def output_stage_new(W, m, min_a, rng, fix=False, si=0, gate=True):
    """v3.08+ 非聚光分支；fix=True 时复刻已修复(v3.14)的非聚光常量 alpha 令牌方案。"""
    scaled3 = np.clip(W * m * SRC_CH, 0, 1.0)
    scaled = float(scaled3.max())                       # = emit_s (h_norm=1)
    if scaled <= 1e-6:
        return 0.0, 0.0
    d_q = rng.random()                                  # 单色 IG 抖动
    q = float(np.floor(scaled3 * 255.0 + d_q).max()) / 255.0
    if q < 0.5 / 255.0:
        return 0.0, 0.0
    core_emit = min(1.0, W)                             # w_core_acc*slab_gain (白源)
    ka_eff = max(0.24, (min_a * 2.4) / max(core_emit, 1e-5))
    out_a = math.floor(min(1.0, scaled * ka_eff) * 255.0 + 0.5) / 255.0
    if fix:
        # 已随 v3.14 修复 shipped: 非聚光路径 wboit_ka=0 -> 常量 alpha 令牌 (out_a = p12.z)
        return q, min_a
    if out_a < min_a - 0.25 / 255.0:
        return 0.0, 0.0                                 # ← 整片硬件级 discard(现状)
    return q, out_a


# ---------------------------------------------------------------------------
# 3. 宿主合成（Shader #118 discard + premultiplied over / WBOIT 加权和）
# ---------------------------------------------------------------------------
def composite_m1(qs, als, pa, H, bg):
    add, trans = 0.0, 1.0
    for q, a in zip(qs, als):
        if q <= 0.0 or a * pa <= 0.001:                 # Shader #118 硬件 discard
            continue
        add += q * pa * H * trans
        trans *= (1.0 - a * pa)
    veil = 1.0 - trans
    return add, veil, min(1.0, max(0.0, bg * (1.0 - veil) + add))


def additive_wboit(qs, als, pa, H, dz):
    w = np.exp(-0.085 * np.arange(len(qs)) * dz)        # 按原始切片深度取权重
    tot = 0.0
    for i, (q, a) in enumerate(zip(qs, als)):
        if q > 0 and a * pa > 0.001:
            tot += w[i] * q * pa * H
    return float(tot)


# ---------------------------------------------------------------------------
# 4. 场景与版本跑批
# ---------------------------------------------------------------------------
def profile(n_sl=96, l_tot=1400.0, k_dec=1.0 / 700.0):
    z = np.linspace(3.0, l_tot, n_sl)
    dz = (l_tot - 3.0) / (n_sl - 1)
    te = np.clip((l_tot - z) / 120.0, 0, 1)
    p = np.exp(-z * k_dec) * (te * te * (3 - 2 * te)) * np.clip(z / 36.0, 0, 1)
    return p / p.max(), dz


def run(version, prof, dz, peak_c, m, H, rng, fix=False, n_mc=96, bg=0.0, gate=True):
    c = lua_constants(version, dz=dz)
    pa, min_a = c["poly_alpha"], c["min_opt_a"]
    # 设计不变量: 屏幕增量 c_i = pa*q_i (q_i = beamatlas 输出色, ∝ k_gain_gpu ∝ 1/pa)
    # => 每切片白光学厚度 W_i = c_i/(pa*m), 其中 c_i = prof_i*peak_c 为版本间相同的物理输入
    W = prof * peak_c / (pa * m)
    comp = 1.0 if version == "3.06" else min(1.0, 1.0 / H)   # v3.07 saturate(1/h_est)
    use_v306_stage = float(version) < 3.08
    adds, veils, pxs, wbo = [], [], [], []
    for _ in range(n_mc):
        qs, als = [], []
        for si, Wi in enumerate(W):
            if use_v306_stage:
                q, a = output_stage_v306(Wi * comp, m, rng)
            else:
                q, a = output_stage_new(Wi * comp, m, min_a, rng, fix=fix, si=si, gate=gate)
            qs.append(q)
            als.append(a)
        add, veil, px = composite_m1(qs, als, pa, H, bg)
        adds.append(add)
        veils.append(veil)
        pxs.append(px)
        wbo.append(additive_wboit(qs, als, pa, H, dz))
    return dict(add=float(np.mean(adds)), veil=float(np.mean(veils)),
                px=float(np.mean(pxs)), wbo=float(np.mean(wbo)),
                pa=pa, min_a=min_a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", default=None)
    args = ap.parse_args()
    rng = np.random.default_rng(7)
    prof, dz = profile()

    print("=" * 86)
    print("光束亮度数字孪生 | 非聚光源路径 | 96 切片, L_tot=1400, 指数衰减剖面, m=0.9")
    print("v3.06 = README 演示动图/对比图源版本；v3.13 = 当前 HEAD 导出")
    print("=" * 86)
    print(f"\n[A] 黑底光束叠加量 (H=1, M1 over 模型)   pa*k_gain*dz 为版本不变量")
    print(f"{'peak_c':>8} | {'v3.06':>9} | {'v3.07':>9} | {'v3.08':>9} | {'v3.13':>9} | "
          f"{'v3.13/v3.06':>11} | {'修复后':>9} | {'修复/v3.06':>10}")
    print("-" * 86)
    rows = []
    for peak_c in (0.004, 0.008, 0.012, 0.018, 0.026):
        r = {v: run(v, prof, dz, peak_c, 0.9, 1.0, rng) for v in ("3.06", "3.07", "3.08", "3.13")}
        rf = run("3.13", prof, dz, peak_c, 0.9, 1.0, rng, fix=True)
        rows.append((peak_c, r, rf))
        print(f"{peak_c:8.4f} | {r['3.06']['add']:9.5f} | {r['3.07']['add']:9.5f} | "
              f"{r['3.08']['add']:9.5f} | {r['3.13']['add']:9.5f} | "
              f"{r['3.13']['add']/r['3.06']['add']:11.3f} | {rf['add']:9.5f} | "
              f"{rf['add']/r['3.06']['add']:10.3f}")

    print(f"\n[A2] 消融 (peak_c=0.012): 分离 min_a 地板门 vs 其余改动")
    a6 = run("3.06", prof, dz, 0.012, 0.9, 1.0, rng)["add"]
    aG = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng, gate=True)["add"]
    aN = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng, gate=False)["add"]
    aF = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng, fix=True)["add"]
    print(f"    v3.06={a6:.5f} | v3.13(现状)={aG:.5f} ({aG/a6:.3f}) | "
          f"v3.13 去地板门={aN:.5f} ({aN/a6:.3f}) | v3.13+相干存活修复={aF:.5f} ({aF/a6:.3f})")

    print(f"\n[B] WBOIT w(z)=exp(-0.085z) 加权叠加量比值 (与权重归一化无关)")
    for peak_c, r, rf in rows:
        print(f"   peak_c={peak_c:.4f}: v3.13/v3.06={r['3.13']['wbo']/r['3.06']['wbo']:.3f}"
              f"   修复/v3.06={rf['wbo']/r['3.06']['wbo']:.3f}")

    print(f"\n[C] 背景耦合 (peak_c=0.012, M1): 最终像素 = bg*(1-veil)+add")
    r6 = run("3.06", prof, dz, 0.012, 0.9, 1.0, rng)
    rH = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng)
    rF = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng, fix=True)
    print(f"    v3.06: poly_alpha={r6['pa']:.4f} min_opt_a={r6['min_a']*255:.0f}/255  "
          f"v3.13: poly_alpha={rH['pa']:.4f} min_opt_a={rH['min_a']*255:.0f}/255")
    for bg in (0.0, 0.25, 0.5, 0.75, 1.0):
        p6 = run("3.06", prof, dz, 0.012, 0.9, 1.0, rng, bg=bg)["px"]
        pH = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng, bg=bg)["px"]
        pF = run("3.13", prof, dz, 0.012, 0.9, 1.0, rng, fix=True, bg=bg)["px"]
        print(f"    bg={bg:4.2f}: v3.06={p6:.4f}  v3.13={pH:.4f} ({pH-p6:+.4f})  修复={pF:.4f}")

    print(f"\n[D] 宿主照明 H 扫描 (v3.07 引入的 saturate(1/H) 逆补偿; peak_c=0.012)")
    for H in (0.6, 0.8, 1.0, 1.2, 1.5, 2.0):
        a6 = run("3.06", prof, dz, 0.012, 0.9, H, rng)["add"]
        aH = run("3.13", prof, dz, 0.012, 0.9, H, rng)["add"]
        print(f"    H={H:4.2f}: v3.06={a6:.5f}  v3.13={aH:.5f}  v3.13/v3.06={aH/a6:.3f}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        xs = [r[0] for r in rows]
        fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.3))
        ax[0].plot(xs, [r[1]["3.13"]["add"] / r[1]["3.06"]["add"] for r in rows],
                   "o-", label="v3.08..v3.13 / v3.06 (current)")
        ax[0].plot(xs, [r[1]["3.07"]["add"] / r[1]["3.06"]["add"] for r in rows],
                   "^:", label="v3.07 / v3.06")
        ax[0].plot(xs, [r[2]["add"] / r[1]["3.06"]["add"] for r in rows],
                   "s--", label="v3.13+fix / v3.06")
        ax[0].axhline(1.0, color="k", lw=0.7)
        ax[0].set_xlabel("peak per-slice screen delta (beam intensity)")
        ax[0].set_ylabel("beam additive ratio")
        ax[0].set_title("non-spot path: min_a floor discard loss")
        ax[0].legend(fontsize=8)
        ax[0].grid(alpha=0.3)
        hs = [0.6, 0.8, 1.0, 1.2, 1.5, 2.0]
        ax[1].plot(hs, hs, "o-", label="v3.06 net = H (no comp)")
        ax[1].plot(hs, [min(h, 1.0) for h in hs], "x-", label="v3.07+ net = min(H,1)")
        ax[1].set_xlabel("host illumination H")
        ax[1].set_ylabel("on-screen multiplicative scale")
        ax[1].set_title("host inverse-compensation saturation (v3.07+)")
        ax[1].legend(fontsize=8)
        ax[1].grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\n[plot] saved -> {args.plot}")


if __name__ == "__main__":
    main()
