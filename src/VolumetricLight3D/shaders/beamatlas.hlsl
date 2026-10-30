    Texture2D g_src    : register(t0);
    Texture2D g_occ    : register(t1);
    Texture2D g_shmap  : register(t2);
    Texture2D g_camocc : register(t3);
    SamplerState g_smp : register(s0);

    cbuffer BeamCB : register(b0)
    {
        float4 p0;   // x: tile_size, y: tiles_x, z: n_slices, w: pad_px
        float4 p1;   // xyz: mid_c,   w: start_s
        float4 p2;   // xyz: slice_f, w: dz
        float4 p3;   // xyz: slice_r, w: hw
        float4 p4;   // xyz: slice_d, w: hh
        float4 p5;   // xyz: W0,      w: inv_2Hx
        float4 p6;   // xyz: Wr,      w: inv_2Hy
        float4 p7;   // xyz: Wd,      w: dS_wn
        float4 p8;   // xyz: Wn,      w: u_wn
        float4 p9;   // xyz: S_or_U,  w: mode_kind
        float4 p10;  // xyz: cam_pos, w: edit_mode
        float4 p11;  // x: k_dec, y: L_tot, z: inv_end, w: k_gain_base
        float4 p12;  // x: ch_offset, y: fill_mode, z: min_opt_a, w: has_tex
        float4 p13;  // xyz: light_rgb, w: fog_amt
        float4 p14;  // xyz: wind_ofs,  w: inv_fog_scale
        float4 p15;  // x: n_occ, y: occ_mode, z: occ_strength, w: sh3d_active
        float4 p16;  // x: tile_uv_span_x, y: tile_uv_span_y, z: sh_res, w: sh_soft_px
        float4 p17;  // xyz: cam_f,   w: camocc_active
        float4 p18;  // xyz: cam_r,   w: focal_over_scrw
        float4 p19;  // xyz: cam_d,   w: focal_over_scrh
        float4 p20;  // x: camocc_w, y: camocc_h, z: camocc_max_z, w: occ_3d_mode
        float4 p21;  // x: cam_comp_mode, y: unlit_mul, z: cl_ltype, w: atten_k
        float4 p22;  // xyz: amb_rgb,    w: spot_in_rad
        float4 p23;  // xyz: cl_rgb,     w: spot_out_rad
        float4 p24;  // xyz: cl_pos,     w: other_lum
        float4 p25;  // xyz: cl_ray_dir, w: spot_ray_env_mode
        float4 p26;  // xyz: target_rgb, w: wboit_ka
        float4 occ[32]; // 最大 8 個の 2D 前景遮蔽オブジェクト
    };

    float sample_shmap_one(int2 ip, int2 sh_lim, float t_ray, float L_tot)
    {
        if (ip.x < 0 || ip.y < 0 || ip.x >= sh_lim.x || ip.y >= sh_lim.y)
            return 1.0;
        float4 c = g_shmap.Load(int3(ip, 0));
        if (c.a < 0.5)
            return 1.0;
        float prox = (c.r + c.g + c.b) * (1.0 / 3.0);
        float t_occ = (1.0 - prox) * L_tot;
        return 1.0 - smoothstep(t_occ - 2.0, t_occ + 6.0, t_ray);
    }

    float eval_shmap_bilinear(float2 fpos, int2 sh_lim, float t_ray, float L_tot)
    {
        int2 i0 = int2(floor(fpos));
        float2 f = fpos - float2(i0);
        float v00 = sample_shmap_one(i0,              sh_lim, t_ray, L_tot);
        float v10 = sample_shmap_one(i0 + int2(1, 0), sh_lim, t_ray, L_tot);
        float v01 = sample_shmap_one(i0 + int2(0, 1), sh_lim, t_ray, L_tot);
        float v11 = sample_shmap_one(i0 + int2(1, 1), sh_lim, t_ray, L_tot);
        return lerp(lerp(v00, v10, f.x), lerp(v01, v11, f.x), f.y);
    }

    void decode_camocc_one(int2 ip, int2 cm_lim, float max_z,
        out float hit_w, out float z_val, out float char_w)
    {
        hit_w  = 0.0;
        z_val  = 0.0;
        char_w = 0.0;
        if (ip.x < 0 || ip.y < 0 || ip.x >= cm_lim.x || ip.y >= cm_lim.y)
            return;
        float4 c = g_camocc.Load(int3(ip, 0));
        if (c.a < 0.5)
            return;
        hit_w = 1.0;
        float raw = (c.r + c.g + c.b) * (1.0 / 3.0);
        float prox;
        if (raw > 0.50)
        {
            char_w = 1.0;
            prox = saturate((raw - 0.51) * (1.0 / 0.48));
        }
        else
        {
            prox = saturate((raw - 0.01) * (1.0 / 0.48));
        }
        z_val = (1.0 - prox) * max_z;
    }

    void eval_camocc_point(float2 fpos, int2 cm_lim, float max_z,
        out float cov_out, out float z_surf_out, out float char_cov_out)
    {
        int2 i0 = int2(floor(fpos));
        float2 f = fpos - float2(i0);
        float w00 = (1.0 - f.x) * (1.0 - f.y);
        float w10 = f.x * (1.0 - f.y);
        float w01 = (1.0 - f.x) * f.y;
        float w11 = f.x * f.y;

        float h00, z00, c00; decode_camocc_one(i0,              cm_lim, max_z, h00, z00, c00);
        float h10, z10, c10; decode_camocc_one(i0 + int2(1, 0), cm_lim, max_z, h10, z10, c10);
        float h01, z01, c01; decode_camocc_one(i0 + int2(0, 1), cm_lim, max_z, h01, z01, c01);
        float h11, z11, c11; decode_camocc_one(i0 + int2(1, 1), cm_lim, max_z, h11, z11, c11);

        cov_out      = w00 * h00 + w10 * h10 + w01 * h01 + w11 * h11;
        char_cov_out = w00 * c00 + w10 * c10 + w01 * c01 + w11 * c11;
        float zmin = 1.0e9;
        if (h00 > 0.5) zmin = min(zmin, z00);
        if (h10 > 0.5) zmin = min(zmin, z10);
        if (h01 > 0.5) zmin = min(zmin, z01);
        if (h11 > 0.5) zmin = min(zmin, z11);
        z_surf_out = zmin;
    }

    // beamatlas タイル拡大時のハードウェア線形補間が 3D モデル外周の空気へ黒縁を滲ませないよう、
    // スラブ 1 テクセルの画面ヤコビアン (±0.75 texel) 内包フットプリントで厳密に内側判定！
    void eval_camocc_surface(float2 fpos, float2 du_cm, float2 dv_cm, int2 cm_lim, float max_z,
        out float cov_out, out float z_surf_out, out float char_cov_out)
    {
        float c0, z0, ch0;
        eval_camocc_point(fpos, cm_lim, max_z, c0, z0, ch0);
        if (c0 <= 0.02)
        {
            cov_out      = 0.0;
            z_surf_out   = 1.0e9;
            char_cov_out = 0.0;
            return;
        }
        float c1, z1, ch1; eval_camocc_point(fpos + du_cm + dv_cm, cm_lim, max_z, c1, z1, ch1);
        float c2, z2, ch2; eval_camocc_point(fpos - du_cm + dv_cm, cm_lim, max_z, c2, z2, ch2);
        float c3, z3, ch3; eval_camocc_point(fpos + du_cm - dv_cm, cm_lim, max_z, c3, z3, ch3);
        float c4, z4, ch4; eval_camocc_point(fpos - du_cm - dv_cm, cm_lim, max_z, c4, z4, ch4);

        float c_min  = min(c0, min(min(c1, c2), min(c3, c4)));
        float ch_min = min(ch0, min(min(ch1, ch2), min(ch3, ch4)));
        cov_out      = saturate((c_min  - 0.45) * (1.0 / 0.55));
        char_cov_out = saturate((ch_min - 0.45) * (1.0 / 0.55));
        z_surf_out   = z0;
    }

    float2 pcg3d_rand2(uint3 v)
    {
        v = v * 1664525u + 1013904223u;
        v.x += v.y * v.z;
        v.y += v.z * v.x;
        v.z += v.x * v.y;
        v ^= (v >> 16u);
        v.x += v.y * v.z;
        v.y += v.z * v.x;
        v.z += v.x * v.y;
        return float2(v.xy) * (1.0 / 4294967296.0);
    }

    float hash31(float3 ip)
    {
        float3 q = ip - floor(ip / 289.0) * 289.0;
        float d = dot(q, float3(127.1, 311.7, 74.7));
        return frac(sin(d) * 43758.5453);
    }

    float vnoise3(float3 x)
    {
        float3 i = floor(x);
        float3 f = x - i;
        float3 u = f * f * (3.0 - 2.0 * f);

        float c000 = hash31(i + float3(0.0, 0.0, 0.0));
        float c100 = hash31(i + float3(1.0, 0.0, 0.0));
        float c010 = hash31(i + float3(0.0, 1.0, 0.0));
        float c110 = hash31(i + float3(1.0, 1.0, 0.0));
        float c001 = hash31(i + float3(0.0, 0.0, 1.0));
        float c101 = hash31(i + float3(1.0, 0.0, 1.0));
        float c011 = hash31(i + float3(0.0, 1.0, 1.0));
        float c111 = hash31(i + float3(1.0, 1.0, 1.0));

        float x00 = lerp(c000, c100, u.x);
        float x10 = lerp(c010, c110, u.x);
        float x01 = lerp(c001, c101, u.x);
        float x11 = lerp(c011, c111, u.x);
        return lerp(lerp(x00, x10, u.y), lerp(x01, x11, u.y), u.z);
    }

    float fog_fbm3(float3 q)
    {
        float a = vnoise3(q);
        float b = vnoise3(q * 2.03 + float3(11.3, 7.1, 5.7));
        float c = vnoise3(q * 4.07 + float3(3.7, 17.9, 13.1));
        return a * 0.57 + b * 0.29 + c * 0.14;
    }

    float4 proc_rose(float2 uv)
    {
        float2 d = (uv - 0.5) * 2.0;
        float r = length(d);
        if (r > 0.96) return float4(0.0, 0.0, 0.0, 0.0);
        float ang = atan2(d.y, d.x);
        float pet12 = abs(sin(ang * 6.0));
        float pet24 = abs(sin(ang * 12.0 + 0.2618));
        float ring = smoothstep(0.05, 0.13, abs(r - 0.52)) * smoothstep(0.04, 0.10, abs(r - 0.88));
        float pane = saturate((smoothstep(0.15, 0.45, pet12) * 0.6 + smoothstep(0.18, 0.52, pet24) * 0.55) * ring);
        float3 c1 = float3(0.58, 0.42, 0.96);
        float3 c2 = float3(0.28, 0.62, 1.00);
        float3 col = lerp(c1, c2, saturate(pet12 * 0.7 + r * 0.3));
        return float4(col * pane, pane);
    }

    float4 beamatlas(float4 pos : SV_Position) : SV_Target
    {
        float tile_sz = p0.x;
        float tiles_x = p0.y;
        float n_sl    = p0.z;
        float pad     = p0.w;

        float2 pix = pos.xy;
        float tx = floor(pix.x / tile_sz);
        float ty = floor(pix.y / tile_sz);
        float si = ty * tiles_x + tx;

        if (si >= n_sl || tx >= tiles_x)
        {
            return float4(0.0, 0.0, 0.0, 0.0);
        }

        float inner = max(tile_sz - 2.0 * pad, 1.0);
        float lu = saturate((pix.x - tx * tile_sz - pad) / inner) * 2.0 - 1.0;
        float lv = saturate((pix.y - ty * tile_sz - pad) / inner) * 2.0 - 1.0;

        float dz = p2.w;
        float s_center = p1.w + (si + 0.5) * dz;
        float3 wp0 = p1.xyz + p2.xyz * s_center + p3.xyz * (lu * p3.w) + p4.xyz * (lv * p4.w);

        if (dot(wp0 - p5.xyz, p8.xyz) >= -2.0)
        {
            return float4(0.0, 0.0, 0.0, 0.0);
        }

        int n_occ      = clamp((int)(p15.x + 0.5), 0, 8);
        float occ_mode = p15.y;
        float occ_str  = p15.z;
        float2 uv_span = p16.xy;
        float fg_vis   = 1.0;

        // occ_mode: 0=なし, 1=3D影のみ(物理遮光), 2=前景マスクのみ, 3=3D影＋前景マスク(推奨)
        // p20.w (occ_3d_mode): 0=自動(VRM輪郭保護＋OBJ前後), 1=物理前後(両方), 2=輪郭保護(両方), 3=3D除外
        bool do_shadow  = (occ_mode > 0.5 && occ_mode < 1.5) || (occ_mode > 2.5);
        bool do_fg_mask = (occ_mode > 1.5);
        float occ3d_m   = p20.w;
        bool use_2d_occ = (occ_mode > 0.5);
        bool use_3d_occ = (occ_mode > 0.5) && (occ3d_m < 2.5);

        float3 st_vec  = p2.xyz * dz;
        float step_len = dz;
        if (p10.w < 0.5)
        {
            float3 v_dir = normalize(wp0 - p10.xyz + float3(0.0, 0.0, 1e-4));
            float dt_slab = dz / max(abs(dot(v_dir, p2.xyz)), 0.14);
            st_vec   = v_dir * dt_slab;
            step_len = dt_slab;
        }

        float cam_cov_s  = 0.0;
        float cam_z_surf = 1.0e9;
        float zc_base    = dot(wp0 - p10.xyz, p17.xyz);
        float dzc_step   = dot(st_vec, p17.xyz);

        if (p17.w > 0.5 && use_3d_occ && occ_str > 0.001)
        {
            float3 dpc = wp0 - p10.xyz;
            if (zc_base > 1.0)
            {
                float inv_zc = 1.0 / zc_base;
                float2 uv_c = float2(0.5 + dot(dpc, p18.xyz) * p18.w * inv_zc,
                                     0.5 + dot(dpc, p19.xyz) * p19.w * inv_zc);
                if (uv_c.x > 0.0 && uv_c.x < 1.0 && uv_c.y > 0.0 && uv_c.y < 1.0)
                {
                    int2 cm_lim = max(int2(p20.xy + 0.5), int2(1, 1));
                    float2 cm_f = float2(cm_lim);
                    float inv_teff = 2.0 / max(tile_sz - 2.0 * p0.w, 1.0);
                    float3 dwp_u = p3.xyz * (p3.w * inv_teff);
                    float3 dwp_v = p4.xyz * (p4.w * inv_teff);
                    float2 du_cm = float2(dot(dwp_u, p18.xyz) * p18.w, dot(dwp_u, p19.xyz) * p19.w) * (inv_zc * 0.75) * cm_f;
                    float2 dv_cm = float2(dot(dwp_v, p18.xyz) * p18.w, dot(dwp_v, p19.xyz) * p19.w) * (inv_zc * 0.75) * cm_f;
                    float char_cov = 0.0;
                    eval_camocc_surface(uv_c * cm_f - 0.5, du_cm, dv_cm, cm_lim, p20.z,
                        cam_cov_s, cam_z_surf, char_cov);

                    // 3D 表面座標 P_surf を厳密復元し、光源平面より前方 (dot(P_surf - W0, Wn) < -2.0) に
                    // 存在するときのみ輪郭保護を適用（光源後方に置いた VRM が手前の光束を貫通遮蔽するのを完全防止！）
                    float3 p_surf = p10.xyz + dpc * (cam_z_surf * inv_zc);
                    bool surf_in_front = (dot(p_surf - p5.xyz, p8.xyz) < -2.0);

                    if (do_fg_mask && surf_in_front)
                    {
                        float hold_cov = 0.0;
                        float min_veil = 0.05;
                        if (occ_mode > 1.5 && occ_mode < 2.5)
                        {
                            // 「前景マスクのみ」モード：光源前方の 3D オブジェクト輪郭を保護
                            hold_cov = cam_cov_s;
                            min_veil = 0.0;
                        }
                        else if (occ3d_m < 0.5)
                        {
                            // 自動：VRM は手前ヘイズから輪郭保護し、OBJ（背景・小道具メッシュ等）は純 3D 前後深度比較へ委ねる
                            hold_cov = char_cov;
                            min_veil = 0.05;
                        }
                        else if (occ3d_m > 1.5)
                        {
                            // 輪郭くっきり保護：VRM・OBJ ともに手前の光束をカット
                            hold_cov = cam_cov_s;
                            min_veil = 0.0;
                        }
                        if (hold_cov > 0.001)
                        {
                            fg_vis *= lerp(1.0, min_veil, saturate(hold_cov * occ_str));
                        }
                    }

                    if (fg_vis <= 0.002)
                    {
                        return float4(0.0, 0.0, 0.0, 0.0);
                    }

                    float zc_near = zc_base - 2.0 * abs(dzc_step);
                    if (cam_cov_s > 0.999 && occ_str > 0.999 && zc_near > cam_z_surf + 6.0)
                    {
                        return float4(0.0, 0.0, 0.0, 0.0);
                    }
                }
            }
        }

        // 2D 前景マスク（v2.20 準拠：do_fg_mask 有効時かつ遮蔽レイヤーより奥のスライスで非ブラー .b のみを参照 → 黒縁 0.0%！）
        if (do_fg_mask && n_occ > 0 && use_2d_occ && occ_str > 0.001)
        {
            float3 org_c = (p10.w > 0.5) ? (wp0 - p2.xyz * 8192.0) : p10.xyz;
            float3 ray_c = wp0 - org_c;
            [loop]
            for (int oi = 0; oi < 8; ++oi)
            {
                if (oi >= n_occ) break;
                int bi = oi * 4;
                float4 oc0 = occ[bi];
                float4 oc1 = occ[bi + 1];
                float4 oc2 = occ[bi + 2];
                float4 oc3 = occ[bi + 3];
                float den_c = dot(ray_c, oc3.xyz);
                if (abs(den_c) > 1e-4)
                {
                    float lam_c = dot(oc0.xyz - org_c, oc3.xyz) / den_c;
                    // m3 (do_shadow == true): カメラ org_c (0) とボクセル wp0 (1) の間にある遮蔽物のみ視線カットし、
                    // 窓枠・サブシーン隙間を抜けて手前空間 (lam_c > 1) へ伸びる 3D 光条を 100% 保持！
                    // m2 (do_shadow == false): 2D 前景輪郭ホールドアウト専用モードとして視線全深度をマスク。
                    bool occ_hit = do_shadow ? ((lam_c > 0.001) && (lam_c <= 1.005)) : (lam_c > 0.001);
                    if (occ_hit)
                    {
                        float3 hp_c = org_c + ray_c * lam_c - oc0.xyz;
                        float2 uv_l = float2(0.5 + dot(hp_c, oc1.xyz) * oc0.w, 0.5 + dot(hp_c, oc2.xyz) * oc1.w);
                        if (uv_l.x > 0.0 && uv_l.x < 1.0 && uv_l.y > 0.0 && uv_l.y < 1.0)
                        {
                            float2 uv_a = float2(oc2.w, oc3.w) + uv_l * uv_span;
                            float oa_fg = smoothstep(0.35, 0.85, g_occ.SampleLevel(g_smp, uv_a, 0.0).b);
                            fg_vis *= (1.0 - saturate(oa_fg * occ_str));
                            if (fg_vis <= 0.002)
                            {
                                return float4(0.0, 0.0, 0.0, 0.0);
                            }
                        }
                    }
                }
            }
        }

        uint g_si_u   = (uint)(p12.x + si + 0.5);
        float g_si_f  = (float)g_si_u;
        uint2 tile_px = uint2(max(pix.xy - float2(tx, ty) * tile_sz, 0.0));
        float2 rnd_qmc = pcg3d_rand2(uint3(tile_px.x, tile_px.y, 17u));
        float2 rnd_grp = pcg3d_rand2(uint3(tile_px.x, tile_px.y, 131u + (g_si_u >> 3u)));
        float ign  = frac(g_si_f * 0.61803399 + (rnd_qmc.x - 0.5) * 0.12);
        float ign2 = frac(rnd_grp.x + (float)(g_si_u & 7u) * 0.125);

        float kind = p9.w;
        bool cast_shadow    = do_shadow && (n_occ > 0) && use_2d_occ && (occ_str > 0.001);
        bool cast_shadow_3d = do_shadow && (p15.w > 0.5) && use_3d_occ && (occ_str > 0.001);
        int2 sh_lim = max(int2(p16.z + 0.5, p16.z + 0.5), int2(1, 1));

        // 広角コーン等で 1 beamatlas テクセルが g_shmap 上で複数テクセルへ拡大投影される際の
        // ナイキスト折り返し（砂状スペックル）を根絶する解析的シャドウマップ・ヤコビアン (du_sh, dv_sh)
        float2 du_sh = float2(0.0, 0.0);
        float2 dv_sh = float2(0.0, 0.0);
        float len_u_sh = 0.0;
        float len_v_sh = 0.0;
        if (cast_shadow_3d)
        {
            float inv_teff_sh = 2.0 / max(tile_sz - 2.0 * pad, 1.0);
            float3 dwp_tx = p3.xyz * (p3.w * inv_teff_sh);
            float3 dwp_ty = p4.xyz * (p4.w * inv_teff_sh);
            float3 dq_tx = float3(0.0, 0.0, 0.0);
            float3 dq_ty = float3(0.0, 0.0, 0.0);
            bool jac_ok = false;
            if (kind < 0.5)
            {
                float inv_uwn = 1.0 / p8.w;
                dq_tx = dwp_tx - p9.xyz * (dot(dwp_tx, p8.xyz) * inv_uwn);
                dq_ty = dwp_ty - p9.xyz * (dot(dwp_ty, p8.xyz) * inv_uwn);
                jac_ok = true;
            }
            else
            {
                float3 dv0_sh = wp0 - p9.xyz;
                float den0_sh = dot(dv0_sh, p8.xyz);
                if (abs(den0_sh) > 1.0e-4)
                {
                    float inv_den0 = 1.0 / den0_sh;
                    float a0_sh = p7.w * inv_den0;
                    dq_tx = (dwp_tx - dv0_sh * (dot(dwp_tx, p8.xyz) * inv_den0)) * a0_sh;
                    dq_ty = (dwp_ty - dv0_sh * (dot(dwp_ty, p8.xyz) * inv_den0)) * a0_sh;
                    jac_ok = true;
                }
            }
            if (jac_ok)
            {
                float2 sh_sc = float2(p5.w, p6.w) * (float2(sh_lim) * (1.0 / 1.08));
                du_sh = float2(dot(dq_tx, p6.xyz), dot(dq_tx, p7.xyz)) * sh_sc;
                dv_sh = float2(dot(dq_ty, p6.xyz), dot(dq_ty, p7.xyz)) * sh_sc;
                len_u_sh = length(du_sh);
                len_v_sh = length(dv_sh);
                if (len_u_sh > 12.0) { du_sh *= (12.0 / len_u_sh); len_u_sh = 12.0; }
                if (len_v_sh > 12.0) { dv_sh *= (12.0 / len_v_sh); len_v_sh = 12.0; }
            }
        }

        float fog_mod = 1.0;
        if (p13.w > 0.001)
        {
            float3 u_dir = (kind < 0.5)
                ? p9.xyz
                : normalize(wp0 - p9.xyz + float3(0.0, 0.0, 1e-4));
            if (kind > 1.5) u_dir = -u_dir;
            float3 qw = wp0 + p14.xyz;
            float3 q_aniso = (qw - u_dir * (dot(qw, u_dir) * 0.875)) * (p14.w * 1.45);
            float fn = fog_fbm3(q_aniso);
            fog_mod = saturate(1.0 - p13.w * 0.55 * (1.0 - fn));
        }

        float k_dec   = p11.x;
        float L_tot   = p11.y;
        float inv_end = p11.z;

        bool spot_env     = (p25.w > 0.5);
        float spot_in_rh  = p22.w;
        float spot_out_rh = max(p23.w, spot_in_rh + 1.0e-4);
        float spot_sil_rh = max(spot_out_rh, spot_in_rh + 0.0043633);
        float cos_out_sp  = cos(spot_out_rh);
        float cos_in_sp   = max(cos(spot_in_rh), cos_out_sp + 1.0e-5);
        float cos_sil_sp  = cos(spot_sil_rh);
        float cos_in_sil  = max(cos(spot_in_rh), cos_sil_sp + 1.0e-5);
        float dist_sp0    = 0.0;
        float cos_sp0     = 1.0;
        float ang_sp0     = 0.0;
        float ang_pad_sp  = 0.0;
        float cull_rh_sp  = spot_out_rh;
        float phi_ray_sp  = 1.0;
        float ray_axial_sp = 0.0;
        bool  ray_has_chord = false;
        float tex_step_sp = 0.0;
        float pad_k_sp    = 0.85;
        float w_u_sp      = 1.0;

        if (spot_env)
        {
            float3 dv_sp0 = wp0 - p24.xyz;
            dist_sp0 = sqrt(max(dot(dv_sp0, dv_sp0), 1.0e-4));
            float axial_sp0 = dot(dv_sp0, p25.xyz);
            if (axial_sp0 <= 3.0 || dist_sp0 >= L_tot)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            float inv_inner_sp = 2.0 / max(tile_sz - 2.0 * pad, 1.0);
            tex_step_sp = max(p3.w, p4.w) * inv_inner_sp;
            float ang_step_sp = tex_step_sp / max(dist_sp0, 24.0);
            float base_u_pad = p21.y + p24.w + max(p22.x, max(p22.y, p22.z));
            w_u_sp     = smoothstep(0.0, 0.25, base_u_pad);
            pad_k_sp   = lerp(1.30, 0.85, w_u_sp);
            ang_pad_sp = min(pad_k_sp * ang_step_sp, spot_out_rh * 0.45);
            cos_sp0    = clamp(axial_sp0 / dist_sp0, -1.0, 1.0);
            ang_sp0    = acos(cos_sp0);
            if (ang_sp0 + ang_pad_sp >= spot_out_rh)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            float3 org_sp   = (p10.w > 0.5) ? (wp0 - p2.xyz * 8192.0) : p10.xyz;
            float3 v_ray_sp = normalize(wp0 - org_sp + float3(0.0, 0.0, 1.0e-4));
            float3 o_sp     = org_sp - p24.xyz;
            float od_sp     = dot(o_sp, p25.xyz);
            float ov_sp     = dot(o_sp, v_ray_sp);
            float vd_sp     = dot(v_ray_sp, p25.xyz);
            float oo_sp     = dot(o_sp, o_sp);
            float s_lo_sp   = 0.0;
            float s_hi_sp   = 65536.0;
            if (abs(vd_sp) > 1.0e-5)
            {
                float sa_sp = (3.0 - od_sp) / vd_sp;
                float sb_sp = (L_tot - od_sp) / vd_sp;
                s_lo_sp = max(s_lo_sp, min(sa_sp, sb_sp));
                s_hi_sp = min(s_hi_sp, max(sa_sp, sb_sp));
            }
            else if (od_sp < 3.0 || od_sp > L_tot)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            if (p10.w <= 0.5 && cam_cov_s > 0.85 && occ_str > 0.85 && zc_base > 1.0)
            {
                float len_dpc_sp = length(wp0 - p10.xyz);
                s_hi_sp = min(s_hi_sp, cam_z_surf * (len_dpc_sp / zc_base) + step_len);
            }
            if (do_fg_mask && do_shadow && n_occ > 0 && use_2d_occ && occ_str > 0.85)
            {
                [loop]
                for (int ois = 0; ois < 8; ++ois)
                {
                    if (ois >= n_occ) break;
                    int bis = ois * 4;
                    float4 ocs0 = occ[bis];
                    float4 ocs1 = occ[bis + 1];
                    float4 ocs2 = occ[bis + 2];
                    float4 ocs3 = occ[bis + 3];
                    float den_os = dot(v_ray_sp, ocs3.xyz);
                    if (abs(den_os) > 1.0e-4)
                    {
                        float s_os = dot(ocs0.xyz - org_sp, ocs3.xyz) / den_os;
                        if (s_os > 1.0 && s_os < s_hi_sp)
                        {
                            float3 hp_os = org_sp + v_ray_sp * s_os - ocs0.xyz;
                            float2 uv_los = float2(0.5 + dot(hp_os, ocs1.xyz) * ocs0.w, 0.5 + dot(hp_os, ocs2.xyz) * ocs1.w);
                            if (uv_los.x > 0.0 && uv_los.x < 1.0 && uv_los.y > 0.0 && uv_los.y < 1.0)
                            {
                                float2 uv_aos = float2(ocs2.w, ocs3.w) + uv_los * uv_span;
                                if (g_occ.SampleLevel(g_smp, uv_aos, 0.0).b * occ_str > 0.85)
                                {
                                    s_hi_sp = s_os;
                                }
                            }
                        }
                    }
                }
            }
            if (s_hi_sp < s_lo_sp)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            float den_sp   = od_sp - vd_sp * ov_sp;
            float s_mid_sp = (abs(den_sp) > 1.0e-6)
                ? clamp((vd_sp * oo_sp - od_sp * ov_sp) / den_sp, s_lo_sp, s_hi_sp)
                : s_lo_sp;
            float3 p_lo_sp  = o_sp + v_ray_sp * s_lo_sp;
            float3 p_hi_sp  = o_sp + v_ray_sp * s_hi_sp;
            float3 p_mid_sp = o_sp + v_ray_sp * s_mid_sp;
            float c_lo_sp   = dot(p_lo_sp,  p25.xyz) * rsqrt(max(dot(p_lo_sp,  p_lo_sp),  1.0e-4));
            float c_hi_sp   = dot(p_hi_sp,  p25.xyz) * rsqrt(max(dot(p_hi_sp,  p_hi_sp),  1.0e-4));
            float c_mid_sp  = dot(p_mid_sp, p25.xyz) * rsqrt(max(dot(p_mid_sp, p_mid_sp), 1.0e-4));
            float c_max_sp  = clamp(max(c_mid_sp, max(c_lo_sp, c_hi_sp)), -1.0, 1.0);
            phi_ray_sp = smoothstep(cos_sil_sp, cos_in_sil, c_max_sp);
            if (phi_ray_sp <= 1.0e-4)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            cull_rh_sp = spot_out_rh;

            // v3.12: 解析的「視線 ∩ 光円錐 ∩ 終端球 L_tot」弦区間 [s_c0, s_c1] 上の 6 点中点積分で軸減衰・距離減衰・角度包絡を評価し、
            // 同一カメラ視線上の全スライスで Straight 色比 r4.rgb / r4.w を完全一致させて WBOIT 手前壁バイアスを根絶！
            float s_c0 = s_lo_sp;
            float s_c1 = s_hi_sp;
            float disc_sph = ov_sp * ov_sp - (oo_sp - L_tot * L_tot);
            bool chord_ok  = (disc_sph > 0.0);
            if (chord_ok)
            {
                float sq_sph = sqrt(disc_sph);
                s_c0 = max(s_c0, -ov_sp - sq_sph);
                s_c1 = min(s_c1, -ov_sp + sq_sph);
                chord_ok = (s_c1 > s_c0 + 1.0e-3);
            }
            if (chord_ok)
            {
                float c2_sil = cos_sil_sp * cos_sil_sp;
                float qa_sp  = vd_sp * vd_sp - c2_sil;
                float qb_sp  = od_sp * vd_sp - c2_sil * ov_sp;
                float qc_sp  = od_sp * od_sp - c2_sil * oo_sp;
                if (abs(qa_sp) < 1.0e-7)
                {
                    if (abs(qb_sp) < 1.0e-7)
                    {
                        chord_ok = (qc_sp >= 0.0);
                    }
                    else
                    {
                        float r_lin = -qc_sp / (2.0 * qb_sp);
                        if (qb_sp > 0.0) s_c0 = max(s_c0, r_lin);
                        else             s_c1 = min(s_c1, r_lin);
                    }
                }
                else
                {
                    float disc_c = qb_sp * qb_sp - qa_sp * qc_sp;
                    if (disc_c < 0.0)
                    {
                        chord_ok = (qa_sp > 0.0);
                    }
                    else
                    {
                        float sq_c = sqrt(max(disc_c, 0.0));
                        float rc0  = (-qb_sp - sq_c) / qa_sp;
                        float rc1  = (-qb_sp + sq_c) / qa_sp;
                        float r_lo = min(rc0, rc1);
                        float r_hi = max(rc0, rc1);
                        if (qa_sp < 0.0)
                        {
                            s_c0 = max(s_c0, r_lo);
                            s_c1 = min(s_c1, r_hi);
                        }
                        else
                        {
                            float m_lo  = 0.5 * (s_c0 + min(s_c1, r_lo));
                            float m_hi  = 0.5 * (max(s_c0, r_hi) + s_c1);
                            bool ok_lo  = (s_c0 < r_lo) && (od_sp + m_lo * vd_sp >= 0.0);
                            bool ok_hi  = (r_hi < s_c1) && (od_sp + m_hi * vd_sp >= 0.0);
                            if (ok_lo && !ok_hi)       s_c1 = min(s_c1, r_lo);
                            else if (ok_hi && !ok_lo)  s_c0 = max(s_c0, r_hi);
                            else if (!ok_lo && !ok_hi) chord_ok = false;
                        }
                    }
                }
                chord_ok = chord_ok && (s_c1 > s_c0 + 1.0e-3);
            }
            if (chord_ok)
            {
                float sum_w_sp     = 0.0;
                float sum_phi_w_sp = 0.0;
                [unroll]
                for (int kt = 0; kt < 6; ++kt)
                {
                    float s_kt  = lerp(s_c0, s_c1, ((float)kt + 0.5) * (1.0 / 6.0));
                    float3 p_kt = o_sp + v_ray_sp * s_kt;
                    float tr_kt = sqrt(max(dot(p_kt, p_kt), 1.0e-4));
                    if (tr_kt < L_tot)
                    {
                        float c_kt   = clamp(dot(p_kt, p25.xyz) / tr_kt, -1.0, 1.0);
                        float phi_kt = smoothstep(cos_sil_sp, cos_in_sil, c_kt);
                        float att_kt = (p21.w > 1.0e-6)
                            ? (1.0 / (1.0 + p21.w * (tr_kt * (1.0 / 800.0)) * (tr_kt * (1.0 / 800.0))))
                            : 1.0;
                        float te_kt  = saturate((L_tot - tr_kt) * inv_end);
                        float w_kt   = saturate(tr_kt / 36.0) * exp(-tr_kt * k_dec)
                                     * (te_kt * te_kt * (3.0 - 2.0 * te_kt)) * att_kt;
                        sum_w_sp     += w_kt;
                        sum_phi_w_sp += w_kt * phi_kt;
                    }
                }
                if (sum_w_sp > 1.0e-6)
                {
                    ray_axial_sp  = sum_w_sp * (1.0 / 6.0);
                    ray_has_chord = true;
                    float mean_phi_sp = sum_phi_w_sp / sum_w_sp;
                    phi_ray_sp = sqrt(max(mean_phi_sp * phi_ray_sp, 0.0));
                }
            }
        }

        float3 acc_rgb      = float3(0.0, 0.0, 0.0);
        float max_cov       = 0.0;
        float3 best_c       = float3(0.0, 0.0, 0.0);
        float w_core_acc    = 0.0;
        float unsh_sp_acc   = 0.0;
        float cov_sh_sp_acc = 0.0;
        float phi_h_acc     = 0.0;
        float phi_w_sum     = 0.0;

        [unroll]
        for (int sub_k = 0; sub_k < 12; ++sub_k)
        {
            float u_hat = -2.0 + ((float)sub_k + ign) * (4.0 / 12.0);
            float w_hat = (1.0 / 12.0) * (1.0 + cos(1.57079633 * u_hat));
            float3 wp = wp0 + st_vec * u_hat;

            if (!spot_env && p21.x > 0.5 && p21.z > 2.5)
            {
                float3 dv_lh = wp - p24.xyz;
                float dist_lh = sqrt(max(dot(dv_lh, dv_lh), 1.0e-4));
                float phi_sub = 1.0;
                if (p21.w > 1.0e-6)
                {
                    float dn_h = dist_lh * (1.0 / 800.0);
                    phi_sub *= 1.0 / (1.0 + p21.w * dn_h * dn_h);
                }
                if (p21.z > 3.5)
                {
                    float cos_th = clamp(dot(dv_lh * (1.0 / dist_lh), p25.xyz), -1.0, 1.0);
                    phi_sub *= smoothstep(cos_out_sp, cos_in_sp, cos_th);
                }
                phi_h_acc += phi_sub * w_hat;
                phi_w_sum += w_hat;
            }

            float cam_vis_sub = 1.0;
            if (cam_cov_s > 0.001)
            {
                float zc_sub = zc_base + dzc_step * u_hat;
                float behind = smoothstep(cam_z_surf - 3.0, cam_z_surf + 5.0, zc_sub);
                cam_vis_sub = 1.0 - behind * cam_cov_s * occ_str;
            }

            float t_ray = -1.0;
            float3 q_win = float3(0.0, 0.0, 0.0);
            float g_div = 1.0;
            bool valid_geo = false;

            if (spot_env || cam_vis_sub > 0.002)
            {
                if (kind < 0.5)
                {
                    t_ray = dot(wp - p5.xyz, p8.xyz) / p8.w;
                    q_win = wp - p9.xyz * t_ray;
                    valid_geo = (t_ray > 0.0) && (t_ray < L_tot);
                }
                else
                {
                    float3 dv = wp - p9.xyz;
                    float denom = dot(dv, p8.xyz);
                    if (abs(denom) > 1e-4)
                    {
                        float alpha = p7.w / denom;
                        float vlen = length(dv);
                        t_ray = abs(1.0 - alpha) * vlen;
                        q_win = p9.xyz + dv * alpha;
                        if (kind < 1.5)
                        {
                            valid_geo = (alpha > 0.0) && (alpha < 1.0) && (t_ray < L_tot);
                            g_div = 1.0;
                        }
                        else
                        {
                            valid_geo = ((alpha > 1.0) || (alpha < 0.0)) && (t_ray < L_tot);
                            float conc = clamp(abs(alpha), 1.0, 9.0);
                            g_div = min(pow(conc, 1.15), 6.5);
                        }
                    }
                }
            }

            if (spot_env && valid_geo)
            {
                float fin_sp = saturate(t_ray / 36.0);
                float w_dec_sp = exp(-t_ray * k_dec);
                float te_sp = saturate((L_tot - t_ray) * inv_end);
                float w_end_sp = te_sp * te_sp * (3.0 - 2.0 * te_sp);
                unsh_sp_acc += fin_sp * w_dec_sp * w_end_sp * g_div * w_hat;
            }

            if (valid_geo && cam_vis_sub > 0.002)
            {
                float fin = saturate(t_ray / 36.0);
                float w_dec = exp(-t_ray * k_dec);
                w_core_acc += fin * w_dec * g_div * w_hat;

                float3 dq = q_win - p5.xyz;
                float2 uv = float2(0.5 + dot(dq, p6.xyz) * p5.w, 0.5 + dot(dq, p7.xyz) * p6.w);
                if (spot_env)
                {
                    uv = clamp(uv, 0.001, 0.999);
                }

                if (uv.x > 0.0 && uv.x < 1.0 && uv.y > 0.0 && uv.y < 1.0)
                {
                    float4 s = spot_env
                        ? float4(1.0, 1.0, 1.0, 1.0)
                        : ((p12.w > 0.5) ? g_src.SampleLevel(g_smp, uv, 0.0) : proc_rose(uv));
                    if (s.a > 0.0005)
                    {
                        float sh_vis = cam_vis_sub;
                        if (cast_shadow_3d)
                        {
                            float2 uv_sh = (uv + 0.04) * (1.0 / 1.08);
                            float2 fpos_sh = uv_sh * float2(sh_lim) - 0.5;
                            float vis3d = eval_shmap_bilinear(fpos_sh, sh_lim, t_ray, L_tot);
                            float rad_px = 0.0;
                            if (p16.w > 0.15)
                            {
                                int2 ic = clamp(int2(fpos_sh + 0.5), int2(0, 0), sh_lim - int2(1, 1));
                                float4 cm = g_shmap.Load(int3(ic, 0));
                                float dist_after = (cm.a > 0.5)
                                    ? max(0.0, t_ray - (1.0 - (cm.r + cm.g + cm.b) * (1.0 / 3.0)) * L_tot)
                                    : (L_tot * 0.35);
                                rad_px = p16.w * (0.22 + 0.78 * saturate(dist_after / 520.0));
                            }
                            if (max(max(len_u_sh, len_v_sh) * 0.55, rad_px) > 0.35)
                            {
                                float2 bu = (len_u_sh > 1.0e-4) ? (du_sh * (1.0 / len_u_sh)) : float2(1.0, 0.0);
                                float2 bv = (len_v_sh > 1.0e-4) ? (dv_sh * (1.0 / len_v_sh)) : float2(0.0, 1.0);
                                float ru = min(14.0, sqrt(0.30 * len_u_sh * len_u_sh + rad_px * rad_px));
                                float rv = min(14.0, sqrt(0.30 * len_v_sh * len_v_sh + rad_px * rad_px));
                                float2 ax_u = bu * ru;
                                float2 ax_v = bv * rv;
                                float sgn_p = ((sub_k & 1) != 0) ? -1.0 : 1.0;
                                float2 o1 = ax_u * 0.8944 + ax_v * (0.4472 * sgn_p);
                                float2 o2 = ax_u * (0.4472 * sgn_p) - ax_v * 0.8944;
                                float v1 = eval_shmap_bilinear(fpos_sh + o1, sh_lim, t_ray, L_tot);
                                float v2 = eval_shmap_bilinear(fpos_sh - o1, sh_lim, t_ray, L_tot);
                                float v3 = eval_shmap_bilinear(fpos_sh + o2, sh_lim, t_ray, L_tot);
                                float v4 = eval_shmap_bilinear(fpos_sh - o2, sh_lim, t_ray, L_tot);
                                vis3d = (vis3d * 2.0 + v1 + v2 + v3 + v4) * (1.0 / 6.0);
                            }
                            sh_vis *= lerp(1.0, vis3d, occ_str);
                        }

                        if (cast_shadow && sh_vis > 0.001)
                        {
                            float3 seg = wp - q_win;
                            [loop]
                            for (int oi = 0; oi < 8; ++oi)
                            {
                                if (oi >= n_occ) break;
                                int bi = oi * 4;
                                float4 oc0 = occ[bi];
                                float4 oc1 = occ[bi + 1];
                                float4 oc2 = occ[bi + 2];
                                float4 oc3 = occ[bi + 3];
                                float den_s = dot(seg, oc3.xyz);
                                if (abs(den_s) > 1e-4)
                                {
                                    float lam_s = dot(oc0.xyz - q_win, oc3.xyz) / den_s;
                                    if (lam_s > 0.002 && lam_s < 0.999)
                                    {
                                        float3 hp_s = q_win + seg * lam_s - oc0.xyz;
                                        float2 uv_l = float2(0.5 + dot(hp_s, oc1.xyz) * oc0.w, 0.5 + dot(hp_s, oc2.xyz) * oc1.w);
                                        if (uv_l.x > 0.0 && uv_l.x < 1.0 && uv_l.y > 0.0 && uv_l.y < 1.0)
                                        {
                                            float2 uv_a = float2(oc2.w, oc3.w) + uv_l * uv_span;
                                            float oa = g_occ.SampleLevel(g_smp, uv_a, 0.0).a;
                                            sh_vis *= (1.0 - saturate(oa * occ_str));
                                            if (sh_vis <= 0.001) break;
                                        }
                                    }
                                }
                            }
                        }

                        if (sh_vis > 0.001)
                        {
                            float te = saturate((L_tot - t_ray) * inv_end);
                            float w_end = te * te * (3.0 - 2.0 * te);
                            float w = fin * w_dec * w_end * sh_vis;

                            acc_rgb += s.rgb * (w * g_div * w_hat);
                            if (spot_env)
                            {
                                float cone_cov = 1.0;
                                if (w_u_sp < 0.999)
                                {
                                    float3 dv_sp = wp - p24.xyz;
                                    float dist_sp = sqrt(max(dot(dv_sp, dv_sp), 1.0e-4));
                                    float axial_sp = dot(dv_sp, p25.xyz);
                                    cone_cov = 0.0;
                                    if (axial_sp > 3.0 && dist_sp < L_tot)
                                    {
                                        float pad_sub = min(pad_k_sp * tex_step_sp / max(dist_sp, 24.0), spot_out_rh * 0.45);
                                        float ang_sp  = acos(clamp(axial_sp / dist_sp, -1.0, 1.0));
                                        float cov_hi  = max(0.0, cull_rh_sp - pad_sub);
                                        float cov_lo  = max(0.0, cov_hi - max(pad_sub, 0.012));
                                        cone_cov = 1.0 - smoothstep(cov_lo, max(cov_hi, cov_lo + 1.0e-4), ang_sp);
                                    }
                                    cone_cov = lerp(cone_cov, 1.0, w_u_sp);
                                }
                                cov_sh_sp_acc += w * g_div * w_hat * cone_cov;
                            }
                            float cw = s.a * w * w_hat;
                            if (cw > max_cov)
                            {
                                max_cov = cw;
                                best_c = saturate(s.rgb / max(s.a, 1e-4));
                            }
                        }
                    }
                }
            }
        }

        float dither = (ign2 - 0.5) * (1.0 / 255.0);

        float3 host_comp_rgb = float3(1.0, 1.0, 1.0);
        float3 h_est      = float3(1.0, 1.0, 1.0);
        float3 h_core     = float3(1.0, 1.0, 1.0);
        float3 h_worst_sp = float3(1.0, 1.0, 1.0);
        float att_d_sp    = 1.0;
        float phi_worst_sp = 1.0;

        if (spot_env)
        {
            if (p21.w > 1.0e-6)
            {
                float dn_sp = dist_sp0 * (1.0 / 800.0);
                att_d_sp = 1.0 / (1.0 + p21.w * dn_sp * dn_sp);
            }
            float cos_worst_sp = cos(min(1.57079633, ang_sp0 + ang_pad_sp));
            float phi_wp0_sp   = smoothstep(cos_out_sp, cos_in_sp, cos_sp0) * att_d_sp;
            phi_worst_sp       = smoothstep(cos_out_sp, cos_in_sp, cos_worst_sp) * att_d_sp;
            float base_u_sp    = p21.y + p24.w;
            h_est      = float3(base_u_sp, base_u_sp, base_u_sp) + p22.xyz + p23.xyz * phi_wp0_sp;
            h_worst_sp = float3(base_u_sp, base_u_sp, base_u_sp) + p22.xyz + p23.xyz * phi_worst_sp;
            h_core     = float3(base_u_sp, base_u_sp, base_u_sp) + p22.xyz + p23.xyz;
            host_comp_rgb = saturate((p26.xyz * (phi_ray_sp * att_d_sp)) / max(h_est, float3(1.0e-4, 1.0e-4, 1.0e-4)));
        }
        else if (p21.x > 0.5)
        {
            float phi_h = 1.0;
            if (p21.z > 2.5)
            {
                phi_h = (phi_w_sum > 1.0e-5) ? (phi_h_acc / phi_w_sum) : 1.0;
            }
            float base_u = p21.y + p24.w;
            h_est  = float3(base_u, base_u, base_u) + p22.xyz + p23.xyz * phi_h;
            h_core = float3(base_u, base_u, base_u) + p22.xyz + p23.xyz;
            if (p21.x < 1.5)
            {
                host_comp_rgb = saturate((p26.xyz * phi_h) / max(h_est, float3(1.0e-4, 1.0e-4, 1.0e-4)));
            }
            else
            {
                host_comp_rgb = saturate(float3(1.0, 1.0, 1.0) / max(h_est, float3(0.08, 0.08, 0.08)));
            }
        }

        if (p12.y > 0.5)
        {
            float eff_cov = spot_env ? (max_cov * phi_ray_sp) : max_cov;
            if (eff_cov <= 0.001) return float4(0.0, 0.0, 0.0, 0.0);
            float a_solid = saturate(1.0 - exp(-eff_cov * fg_vis * fog_mod * dz * 0.015));
            float out_as = saturate(a_solid + dither);
            if (out_as < (0.5 / 255.0)) return float4(0.0, 0.0, 0.0, 0.0);
            float3 c_solid = saturate(best_c * min(p11.w * 360.0, 1.15) * host_comp_rgb);
            return float4(c_solid * out_as, out_as);
        }

        float slab_gain = step_len * p11.w * fog_mod * fg_vis;
        if (spot_env)
        {
            if (unsh_sp_acc <= 1.0e-6 || cov_sh_sp_acc <= 1.0e-6)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            float vis_cov_sp    = saturate(cov_sh_sp_acc / unsh_sp_acc);
            float eff_axial_sp  = ray_has_chord ? ray_axial_sp : (unsh_sp_acc * att_d_sp);
            float3 unsh_post_sp = (eff_axial_sp * slab_gain) * p26.xyz * (phi_ray_sp * vis_cov_sp);
            float unsh_post_m   = max(unsh_post_sp.r, max(unsh_post_sp.g, unsh_post_sp.b));
            if (unsh_post_m <= 1.0e-6)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            float3 raw_div_sp = float3(
                (h_core.r > 1.0e-4) ? (unsh_post_sp.r / max(h_est.r, 1.0e-5)) : 0.0,
                (h_core.g > 1.0e-4) ? (unsh_post_sp.g / max(h_est.g, 1.0e-5)) : 0.0,
                (h_core.b > 1.0e-4) ? (unsh_post_sp.b / max(h_est.b, 1.0e-5)) : 0.0
            );
            float worst_r_sp = (h_core.r > 1.0e-4 && p26.r > 1.0e-4) ? (unsh_post_sp.r / max(h_worst_sp.r, 1.0e-5)) : 0.0;
            float worst_g_sp = (h_core.g > 1.0e-4 && p26.g > 1.0e-4) ? (unsh_post_sp.g / max(h_worst_sp.g, 1.0e-5)) : 0.0;
            float worst_b_sp = (h_core.b > 1.0e-4 && p26.b > 1.0e-4) ? (unsh_post_sp.b / max(h_worst_sp.b, 1.0e-5)) : 0.0;
            float worst_max_sp = max(worst_r_sp, max(worst_g_sp, worst_b_sp));
            float w_hr_sp    = (worst_max_sp > 0.96) ? clamp(0.96 / worst_max_sp, 0.0, 1.0) : 1.0;

            float3 scaled_sp = raw_div_sp * w_hr_sp;
            float s_max_sp   = max(scaled_sp.r, max(scaled_sp.g, scaled_sp.b));
            if (s_max_sp <= 1.0e-6)
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            if (s_max_sp > 1.0)
            {
                scaled_sp *= (1.0 / s_max_sp);
            }
            float3 s255_sp = saturate(scaled_sp) * 255.0;
            float s255_max = max(s255_sp.r, max(s255_sp.g, s255_sp.b));
            float out_a_sp = p12.z;
            float k_min_sp = max(1.0, floor(out_a_sp * 255.0 + 0.5));
            float d_sl_sp  = frac(g_si_f * 0.61803399 + 0.5 * frac(g_si_f * 0.75487767));
            if (s255_max < k_min_sp)
            {
                if (s255_max < k_min_sp * d_sl_sp)
                {
                    return float4(0.0, 0.0, 0.0, 0.0);
                }
                float3 s_boost = min(float3(255.0, 255.0, 255.0), s255_sp * (k_min_sp / max(s255_max, 1.0e-5)));
                float3 q_min_v = floor(s_boost + 0.5) * (1.0 / 255.0);
                return float4(q_min_v, out_a_sp);
            }
            float d_ig_sp = frac(52.9829189 * frac(dot(float2(tile_px), float2(0.06711056, 0.00583715))) + g_si_f * 0.61803399);
            float3 q_sp   = floor(s255_sp + float3(d_ig_sp, d_ig_sp, d_ig_sp)) * (1.0 / 255.0);
            if (max(q_sp.r, max(q_sp.g, q_sp.b)) < (0.5 / 255.0))
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
            return float4(q_sp, out_a_sp);
        }
        float3 scaled_rgb = acc_rgb * slab_gain * host_comp_rgb;
        float s_max = max(scaled_rgb.r, max(scaled_rgb.g, scaled_rgb.b));
        if (s_max <= 1e-6)
        {
            return float4(0.0, 0.0, 0.0, 0.0);
        }
        if (s_max > 1.0)
        {
            scaled_rgb *= (1.0 / s_max);
        }
        float2 rnd_c2 = pcg3d_rand2(uint3(tile_px.x ^ 0x5bd1u, tile_px.y ^ 0x1b87u, 277u + g_si_u));
        float3 d3 = frac(float3(ign2, rnd_c2.x, rnd_c2.y) + float3(0.0, 0.38196601, 0.72360680));
        float3 q_rgb = floor(saturate(scaled_rgb) * 255.0 + d3) * (1.0 / 255.0);
        float q_max = max(q_rgb.r, max(q_rgb.g, q_rgb.b));
        if (q_max < (0.5 / 255.0))
        {
            return float4(0.0, 0.0, 0.0, 0.0);
        }
        float out_a = p12.z;
        if (p26.w > 0.01)
        {
            float min_a = p12.z;
            float h_core_m = max(h_core.r, max(h_core.g, max(h_core.b, 1.0e-4)));
            float3 h_norm = (p21.x > 0.5 && p21.x < 1.5) ? (h_est * (1.0 / h_core_m)) : float3(1.0, 1.0, 1.0);
            float3 core_c = (p21.x > 0.5 && p21.x < 1.5)
                ? (p26.xyz * (1.0 / h_core_m))
                : (p13.xyz * host_comp_rgb);
            float core_emit = min(1.0, w_core_acc * slab_gain) * max(core_c.r, max(core_c.g, max(core_c.b, 1.0e-4)));
            float emit_s = max(scaled_rgb.r * h_norm.r, max(scaled_rgb.g * h_norm.g, scaled_rgb.b * h_norm.b));
            float ka_eff = max(p26.w, (min_a * 2.4) / max(core_emit, 1.0e-5));
            out_a = floor(saturate(emit_s * ka_eff) * 255.0 + 0.5) * (1.0 / 255.0);
            if (out_a < min_a - (0.25 / 255.0))
            {
                return float4(0.0, 0.0, 0.0, 0.0);
            }
        }
        return float4(q_rgb, out_a);
    }
