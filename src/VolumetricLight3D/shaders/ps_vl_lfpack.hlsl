    // VolumetricLight3D v3.16 — Shared 3D Light Field Packer (VL3DLF v3)
    // Packs up to 3 bands into a single 48 x H_pack RGBA8 target in one GPU pass:
    //   Band 0 (rows 0..47)             : 48x48 thresholded source radiance RGB + coverage A
    //   Band 1 (rows row_sh..row_sh+47) : 48x48 unified 3D shmap + 2D occ cast-shadow depth (RG16 + B=cov)
    //   Band 2 (rows row_cm..row_cm+17) : 48x18 unified 3D camocc + 2D occ foreground depth (RG16 + B=cov)

    Texture2D g_mip    : register(t0);
    Texture2D g_occ    : register(t1);
    Texture2D g_shmap  : register(t2);
    Texture2D g_camocc : register(t3);
    SamplerState g_smp : register(s0);

    cbuffer LfPackCB : register(b0)
    {
        float4 q0;   // x: out_w(48), y: rad_h(48/0), z: sh_h(48/0), w: cam_h(18/0)
        float4 q1;   // x: row_sh, y: row_cam, z: mode_kind, w: spot_env
        float4 q2;   // xyz: W0, w: Hx
        float4 q3;   // xyz: Wr, w: Hy
        float4 q4;   // xyz: Wd, w: L_tot
        float4 q5;   // xyz: Wn, w: has_tex
        float4 q6;   // xyz: S_or_U, w: sh3d_on
        float4 q7;   // x: u0_b, y: u1_b, z: v0_b, w: v1_b
        float4 q8;   // x: su0,  y: su1,  z: sv0,  w: sv1
        float4 q9;   // xyz: light_rgb, w: n_occ
        float4 q10;  // x: occ_str, y: cam_zmax, z: sh_res, w: cam3d_on
        float4 q11;  // x: uv_span_x, y: uv_span_y, z: camocc_w, w: camocc_h
        float4 q12;  // xyz: cam_pos, w: is_edit
        float4 q13;  // xyz: cam_f,   w: do_fg_2d
        float4 q14;  // xyz: cam_r,   w: inv_fsw  (scr_w / focal_d)
        float4 q15;  // xyz: cam_d,   w: inv_fsh  (scr_h / focal_d)
        float4 occ[32];
    };

    float4 lf_proc_rose(float2 uv)
    {
        float2 d = uv * 2.0 - 1.0;
        float r = length(d);
        if (r > 0.98) return float4(0.0, 0.0, 0.0, 0.0);
        float a = atan2(d.y, d.x);
        float spoke = abs(sin(a * 6.0));
        float ring1 = abs(r - 0.52);
        float ring2 = abs(r - 0.92);
        float lead = (spoke < 0.075 || ring1 < 0.032 || ring2 < 0.045) ? 0.08 : 1.0;
        float sector = floor((a + 3.14159265) / (6.28318530 / 12.0));
        float3 pal0 = float3(1.00, 0.34, 0.22);
        float3 pal1 = float3(0.24, 0.68, 1.00);
        float3 pal2 = float3(1.00, 0.80, 0.24);
        float3 pal3 = float3(0.45, 0.95, 0.62);
        int idx = ((int)sector) & 3;
        float3 col = (idx == 0) ? pal0 : ((idx == 1) ? pal1 : ((idx == 2) ? pal2 : pal3));
        if (r < 0.20) col = float3(1.0, 0.95, 0.80);
        float vig = saturate(1.12 - r * r * 0.55);
        return float4(col * lead * vig, lead);
    }

    float4 pack_depth16(float tn, float cov)
    {
        float tc = saturate(tn);
        float scaled = floor(tc * 65535.0 + 0.5);
        float hi = floor(scaled * (1.0 / 256.0));
        float lo = scaled - hi * 256.0;
        return float4(hi * (1.0 / 255.0), lo * (1.0 / 255.0), saturate(cov), 1.0);
    }

    float4 ps_vl_lfpack(float4 pos : SV_Position) : SV_Target
    {
        int ix = (int)pos.x;
        int iy = (int)pos.y;
        int out_w = (int)(q0.x + 0.5);
        int rad_h = (int)(q0.y + 0.5);
        int sh_h  = (int)(q0.z + 0.5);
        int cam_h = (int)(q0.w + 0.5);
        int row_sh  = (int)(q1.x + 0.5);
        int row_cam = (int)(q1.y + 0.5);

        if (ix < 0 || ix >= out_w)
            return float4(0.0, 0.0, 0.0, 0.0);

        // -----------------------------------------------------------------
        // Band 0: 48x48 Source Radiance (pre-thresholded in g_mip, tinted)
        // -----------------------------------------------------------------
        if (rad_h > 0 && iy >= 0 && iy < rad_h)
        {
            float fx = ((float)ix + 0.5) / max(q0.x, 1.0);
            float fy = ((float)iy + 0.5) / max(q0.y, 1.0);
            float u = lerp(q7.x, q7.y, fx);
            float v = lerp(q7.z, q7.w, fy);

            if (q1.w > 0.5)
            {
                return float4(saturate(q9.xyz), 1.0);
            }
            if (u <= 0.0 || u >= 1.0 || v <= 0.0 || v >= 1.0)
                return float4(0.0, 0.0, 0.0, 0.0);

            float4 s;
            if (q5.w > 0.5)
            {
                s = g_mip.SampleLevel(g_smp, float2(u, v), 0.0);
            }
            else
            {
                s = lf_proc_rose(float2(u, v));
            }
            float cov = saturate(s.a);
            float3 rgb = saturate(s.rgb * q9.xyz);
            return float4(rgb, cov);
        }

        // -----------------------------------------------------------------
        // Band 1: 48x48 Cast-Shadow Depth along Light Rays (3D shmap + 2D occ)
        // -----------------------------------------------------------------
        if (sh_h > 0 && iy >= row_sh && iy < row_sh + sh_h)
        {
            int ry = iy - row_sh;
            float fx = ((float)ix + 0.5) / max(q0.x, 1.0);
            float fy = ((float)ry + 0.5) / max(q0.z, 1.0);
            float u = lerp(q8.x, q8.y, fx);
            float v = lerp(q8.z, q8.w, fy);

            float L_tot   = max(q4.w, 1.0);
            float occ_str = saturate(q10.x);
            float t_min   = L_tot * 2.0;
            float best_cov = 0.0;

            // 1) 3D mesh shadow map (g_shmap: prox = (r+g+b)/3, t_occ = (1 - prox) * L_tot)
            if (q6.w > 0.5)
            {
                int sh_n = max((int)(q10.z + 0.5), 1);
                int2 sh_lim = int2(sh_n, sh_n);
                float2 fpos = float2(fx, fy) * (float)sh_n - 0.5;
                int2 ic = int2(floor(fpos + 0.5));
                for (int dy = -1; dy <= 1; ++dy)
                {
                    for (int dx = -1; dx <= 1; ++dx)
                    {
                        int2 ip = clamp(ic + int2(dx, dy), int2(0, 0), sh_lim - int2(1, 1));
                        float4 sm = g_shmap.Load(int3(ip, 0));
                        if (sm.a >= 0.5)
                        {
                            float prox = (sm.r + sm.g + sm.b) * (1.0 / 3.0);
                            float t_3d = (1.0 - prox) * L_tot;
                            if (t_3d < t_min) t_min = t_3d;
                            best_cov = max(best_cov, occ_str);
                        }
                    }
                }
            }

            // 2) 2D occluder ray-plane intersections along the exact light ray from (u, v)
            int n_occ = (int)(q9.w + 0.5);
            if (n_occ > 0)
            {
                float3 Pw = q2.xyz
                          + q3.xyz * ((u - 0.5) * 2.0 * q2.w)
                          + q4.xyz * ((v - 0.5) * 2.0 * q3.w);
                float kind = q1.z;
                float3 Rd;
                if (kind < 0.5)
                {
                    Rd = normalize(q6.xyz);
                }
                else if (kind < 1.5)
                {
                    Rd = normalize(Pw - q6.xyz);
                }
                else
                {
                    Rd = normalize(q6.xyz - Pw);
                }

                float2 uv_span = q11.xy;
                for (int oi = 0; oi < 8; ++oi)
                {
                    if (oi >= n_occ) break;
                    int bi = oi * 4;
                    float4 oc0 = occ[bi];
                    float4 oc1 = occ[bi + 1];
                    float4 oc2 = occ[bi + 2];
                    float4 oc3 = occ[bi + 3];
                    float den = dot(Rd, oc3.xyz);
                    if (abs(den) > 1.0e-5)
                    {
                        float t_hit = dot(oc0.xyz - Pw, oc3.xyz) / den;
                        if (t_hit > 0.5 && t_hit < L_tot)
                        {
                            float3 hp = Pw + Rd * t_hit - oc0.xyz;
                            float2 uv_l = float2(
                                0.5 + dot(hp, oc1.xyz) * oc0.w,
                                0.5 + dot(hp, oc2.xyz) * oc1.w
                            );
                            if (uv_l.x > 0.0 && uv_l.x < 1.0 && uv_l.y > 0.0 && uv_l.y < 1.0)
                            {
                                float2 uv_a = float2(oc2.w, oc3.w) + uv_l * uv_span;
                                float oa = g_occ.SampleLevel(g_smp, uv_a, 0.0).a;
                                float c_i = saturate(oa * occ_str);
                                if (c_i > 0.15)
                                {
                                    if (t_hit < t_min) t_min = t_hit;
                                    best_cov = max(best_cov, c_i);
                                }
                            }
                        }
                    }
                }
            }

            if (best_cov <= 0.001 || t_min > L_tot * 1.5)
            {
                return float4(1.0, 1.0, 0.0, 0.0);
            }
            return pack_depth16(t_min / L_tot, best_cov);
        }

        // -----------------------------------------------------------------
        // Band 2: 48x18 Unified Camera-Space Foreground Depth (3D camocc + 2D occ)
        // -----------------------------------------------------------------
        if (cam_h > 0 && iy >= row_cam && iy < row_cam + cam_h)
        {
            int ry = iy - row_cam;
            float su = ((float)ix + 0.5) / max(q0.x, 1.0);
            float sv = ((float)ry + 0.5) / max(q0.w, 1.0);
            float max_z   = max(q10.y, 1.0);
            float occ_str = saturate(q10.x);
            float z_min   = max_z * 2.0;
            float fg_cov  = 0.0;

            // 1) 3D camera depth map (g_camocc)
            if (q10.w > 0.5)
            {
                int2 cm_lim = max(int2(q11.zw + 0.5), int2(1, 1));
                int2 ip = clamp(int2(su * (float)cm_lim.x, sv * (float)cm_lim.y), int2(0, 0), cm_lim - int2(1, 1));
                float4 cm = g_camocc.Load(int3(ip, 0));
                if (cm.a >= 0.5)
                {
                    float raw = (cm.r + cm.g + cm.b) * (1.0 / 3.0);
                    float prox = (raw > 0.50)
                        ? saturate((raw - 0.51) * (1.0 / 0.48))
                        : saturate((raw - 0.01) * (1.0 / 0.48));
                    float z_3d = (1.0 - prox) * max_z;
                    if (z_3d < z_min) z_min = z_3d;
                    fg_cov = max(fg_cov, occ_str);
                }
            }

            // 2) 2D foreground silhouette planes along camera ray through (su, sv)
            int n_occ = (int)(q9.w + 0.5);
            if (q13.w > 0.5 && n_occ > 0 && q12.w < 0.5)
            {
                float3 org_c = q12.xyz;
                float3 ray_c = q13.xyz
                             + q14.xyz * ((su - 0.5) * q14.w)
                             + q15.xyz * ((sv - 0.5) * q15.w);
                float2 uv_span = q11.xy;
                for (int oi = 0; oi < 8; ++oi)
                {
                    if (oi >= n_occ) break;
                    int bi = oi * 4;
                    float4 oc0 = occ[bi];
                    float4 oc1 = occ[bi + 1];
                    float4 oc2 = occ[bi + 2];
                    float4 oc3 = occ[bi + 3];
                    float den_c = dot(ray_c, oc3.xyz);
                    if (abs(den_c) > 1.0e-5)
                    {
                        // Since dot(ray_c, cam_f) == 1.0, lam_c equals exact camera depth zc!
                        float lam_c = dot(oc0.xyz - org_c, oc3.xyz) / den_c;
                        if (lam_c > 1.0 && lam_c < max_z)
                        {
                            float3 hp_c = org_c + ray_c * lam_c - oc0.xyz;
                            float2 uv_l = float2(
                                0.5 + dot(hp_c, oc1.xyz) * oc0.w,
                                0.5 + dot(hp_c, oc2.xyz) * oc1.w
                            );
                            if (uv_l.x > 0.0 && uv_l.x < 1.0 && uv_l.y > 0.0 && uv_l.y < 1.0)
                            {
                                float2 uv_a = float2(oc2.w, oc3.w) + uv_l * uv_span;
                                float oa_fg = smoothstep(0.35, 0.85, g_occ.SampleLevel(g_smp, uv_a, 0.0).b);
                                float c_fg = saturate(oa_fg * occ_str);
                                if (c_fg > 0.15)
                                {
                                    if (lam_c < z_min) z_min = lam_c;
                                    fg_cov = max(fg_cov, c_fg);
                                }
                            }
                        }
                    }
                }
            }

            if (fg_cov <= 0.001 || z_min > max_z * 1.5)
            {
                return float4(1.0, 1.0, 0.0, 0.0);
            }
            return pack_depth16(z_min / max_z, fg_cov);
        }

        return float4(0.0, 0.0, 0.0, 0.0);
    }
