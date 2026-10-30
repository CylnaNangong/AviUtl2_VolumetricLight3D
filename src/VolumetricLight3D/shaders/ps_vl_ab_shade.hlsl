    Texture2D g_src   : register(t0);
    Texture2D g_sil   : register(t1);
    Texture2D g_env   : register(t2);
    Texture2D g_front : register(t3);
    SamplerState g_smp : register(s0);

    cbuffer AbShadeCB : register(b0)
    {
        float4 c0;  // x: lw, y: lh, z: base_w, w: base_h
        float4 c1;  // x: pad_x, y: pad_y, z: style(0=back,1=light,2=rim), w: dual_enable(0/1)
        float4 c2;  // xyz: left_rgb, w: left_eff_str(0..200)
        float4 c3;  // xyz: right_rgb, w: right_eff_str(0..200)
        float4 c4;  // x: bright_pct(0..200), y: inside_t(0..1), z: env_opacity(0..1), w: has_front_2d(0/1)
        float4 c5;  // x: rim_outer_px, y: rim_inner_px, z: dir_x, w: dir_y
        float4 c6;  // xyz: plane_center_w, w: dir_weight(0..1)
        float4 c7;  // xyz: step_r_w, w: scr_w
        float4 c8;  // xyz: step_d_w, w: scr_h
        float4 c9;  // xyz: cam_pos, w: focal_d
        float4 c10; // xyz: cam_f, w: is_ortho
        float4 c11; // xyz: cam_r, w: camocc_active
        float4 c12; // xyz: cam_d, w: camocc_max_z
    };

    float sample_base_a(float2 bp, float2 bsz)
    {
        if (bp.x <= 0.0 || bp.x >= bsz.x || bp.y <= 0.0 || bp.y >= bsz.y) return 0.0;
        return g_src.SampleLevel(g_smp, bp / bsz, 0.0).a;
    }

    float4 ps_vl_ab_shade(float4 pos : SV_Position) : SV_Target
    {
        float2 lsz = max(c0.xy, float2(1.0, 1.0));
        float2 bsz = max(c0.zw, float2(1.0, 1.0));
        float2 bp  = pos.xy - c1.xy;
        float2 uv_l = pos.xy / lsz;

        // 3D world position & screen UV projection of this sprite texel
        float2 rel_c = pos.xy - 0.5 * lsz;
        float3 wp = c6.xyz + c7.xyz * rel_c.x + c8.xyz * rel_c.y;
        float3 v_cam = wp - c9.xyz;
        float zc = dot(v_cam, c10.xyz);
        if (c10.w < 0.5 && zc <= 2.0) return float4(0.0, 0.0, 0.0, 0.0);
        float2 scr_px = (c10.w > 0.5)
            ? float2(0.5 * c7.w + dot(v_cam, c11.xyz), 0.5 * c8.w + dot(v_cam, c12.xyz))
            : float2(0.5 * c7.w + dot(v_cam, c11.xyz) * (c9.w / max(zc, 1.0)),
                     0.5 * c8.w + dot(v_cam, c12.xyz) * (c9.w / max(zc, 1.0)));
        float2 uv_scr = clamp(scr_px / max(float2(c7.w, c8.w), float2(1.0, 1.0)), 0.001, 0.999);

        // Closer 2D foreground silhouette holdout (e.g. non-target text/overlay in front of this sprite)
        float front_vis = 1.0;
        if (c4.w > 0.5)
        {
            float fa = g_front.SampleLevel(g_smp, uv_scr, 0.0).a;
            front_vis *= saturate(1.0 - fa);
        }
        else if (c11.w > 0.5)
        {
            // 3D model camera-depth holdout when camocc is bound to t3
            float4 cm = g_front.SampleLevel(g_smp, uv_scr, 0.0);
            if (cm.a > 0.01)
            {
                float prox = (floor(cm.r * 255.0 + 0.5) * 65536.0
                            + floor(cm.g * 255.0 + 0.5) * 256.0
                            + floor(cm.b * 255.0 * 0.5) * 2.0) / 16777215.0;
                float z_surf = (1.0 - prox) * c12.w;
                if (zc > z_surf + 6.0) front_vis *= saturate(1.0 - cm.a);
            }
        }
        if (front_vis <= 0.002) return float4(0.0, 0.0, 0.0, 0.0);

        float4 base_p = float4(0.0, 0.0, 0.0, 0.0);
        if (bp.x > 0.0 && bp.x < bsz.x && bp.y > 0.0 && bp.y < bsz.y)
        {
            base_p = g_src.SampleLevel(g_smp, bp / bsz, 0.0);
        }
        float base_a = saturate(base_p.a);
        float3 base_rgb = (base_a > 1e-4) ? saturate(base_p.rgb / base_a) : float3(0.0, 0.0, 0.0);

        // 1) Base Brightness adjustment (AutoBlendLight: オブジェクト > 明るさ 0..200%)
        float br = max(c4.x, 0.0) * 0.01;
        if (br <= 1.0)
        {
            base_rgb *= br;
        }
        else
        {
            float lift = saturate(br - 1.0);
            base_rgb = lerp(base_rgb, 1.0 - (1.0 - base_rgb) * (1.0 - lift * 0.65), saturate(lift));
            base_rgb = saturate(base_rgb + lift * 0.22);
        }

        // 2) Dual-color / Single-color interpolation across sprite X
        float tx = (c1.w > 0.5) ? smoothstep(0.24, 0.76, clamp(bp.x / bsz.x, 0.0, 1.0)) : 0.0;
        float3 light_col = lerp(c2.xyz, c3.xyz, tx);
        float  eff_str   = lerp(c2.w, c3.w, tx);

        // 3) Optional 3D Volumetric Light directional rim weighting (mode 2)
        float2 sil = g_sil.SampleLevel(g_smp, uv_l, 0.0).rg;
        float dir_mod = 1.0;
        if (c6.w > 0.001)
        {
            float2 du = float2(2.0 / lsz.x, 0.0);
            float2 dv = float2(0.0, 2.0 / lsz.y);
            float gx = g_sil.SampleLevel(g_smp, clamp(uv_l + du, 0.001, 0.999), 0.0).r
                     - g_sil.SampleLevel(g_smp, clamp(uv_l - du, 0.001, 0.999), 0.0).r;
            float gy = g_sil.SampleLevel(g_smp, clamp(uv_l + dv, 0.001, 0.999), 0.0).r
                     - g_sil.SampleLevel(g_smp, clamp(uv_l - dv, 0.001, 0.999), 0.0).r;
            // Outward normal points from opaque interior (high sil.r) toward outside (-grad)
            float2 n_out = -float2(gx, gy);
            float nl = length(n_out);
            if (nl > 1e-3)
            {
                float align_l = dot(n_out / nl, c5.zw) * 0.5 + 0.5;
                dir_mod = lerp(1.0, 0.38 + 1.18 * align_l, saturate(c6.w));
            }
        }
        eff_str *= dir_mod;

        float str_n = saturate(eff_str * 0.01);
        float over_boost = saturate((eff_str - 100.0) * 0.01);
        float3 tint_col = lerp(saturate(light_col), float3(1.0, 1.0, 1.0), over_boost * 0.35);

        float inside_t = saturate(c4.y);
        int style_i = (int)(c1.z + 0.5);

        // 4) Environment Blend Multiply FIRST (settles base albedo into background before rim highlights shine on top!)
        float env_op = saturate(c4.z);
        if (env_op > 0.001 && base_a > 1e-4)
        {
            float4 env_s = g_env.SampleLevel(g_smp, uv_scr, 0.0);
            if (env_s.a > 1e-3)
            {
                base_rgb = lerp(base_rgb, base_rgb * saturate(env_s.rgb), env_op * saturate(env_s.a));
            }
        }

        float3 lit_rgb = base_rgb;
        float  outer_a = 0.0;

        if (eff_str > 0.05)
        {
            if (style_i == 2)
            {
                // Outline Rim (縁取り): 12-direction circular inner erosion + outer dilation
                float ro = max(c5.x, 0.0);
                float ri = max(c5.y, 0.0);
                float dil_a = base_a;
                float ero_a = base_a;
                [unroll]
                for (int ai = 0; ai < 12; ++ai)
                {
                    float ang = (float)ai * 0.5235987756; // 2*pi/12
                    float2 cs = float2(cos(ang), sin(ang));
                    if (ro > 0.05)
                    {
                        dil_a = max(dil_a, sample_base_a(bp + cs * ro, bsz));
                        dil_a = max(dil_a, sample_base_a(bp + cs * (ro * 0.55), bsz));
                    }
                    if (ri > 0.05 && base_a > 1e-4)
                    {
                        ero_a = min(ero_a, sample_base_a(bp + cs * ri, bsz));
                    }
                }
                float inner_rim = (ri > 0.05) ? saturate(base_a - ero_a) : 0.0;
                float outer_rim = (ro > 0.05) ? saturate(dil_a - base_a) : 0.0;
                float in_amt  = saturate(inner_rim * str_n * (1.0 + over_boost * 0.35));
                lit_rgb = lerp(lit_rgb, tint_col, in_amt);
                outer_a = saturate(outer_rim * str_n);
            }
            else if (style_i == 1)
            {
                // Gradient Normal Light (逆光 OFF): soft directional wrap + body fill
                float in_edge = saturate((1.0 - sil.r) * 1.75);
                float body_w = lerp(0.45, 1.0, in_edge) * str_n * (0.55 + 0.45 * inside_t);
                float3 scr_blend = 1.0 - (1.0 - lit_rgb) * (1.0 - tint_col);
                lit_rgb = lerp(lit_rgb, lerp(scr_blend, tint_col, 0.30), saturate(body_w));
                lit_rgb = saturate(lit_rgb + tint_col * (over_boost * 0.28 * body_w));
                float out_edge = saturate(sil.g * 1.85);
                outer_a = saturate(out_edge * (1.0 - base_a) * (1.0 - inside_t) * str_n * 0.65);
            }
            else
            {
                // Gradient Backlight (逆光 ON): sil.r == 0.5 at silhouette boundary -> edge_pen == 1.0!
                float edge_pen = saturate((1.0 - sil.r) * 2.0);
                float gamma_in = lerp(2.10, 0.60, inside_t);
                float wrap_in  = pow(max(edge_pen, 1e-4), gamma_in);
                float in_amt   = saturate(wrap_in * str_n * (0.85 + over_boost * 0.65));
                float3 scr_blend = 1.0 - (1.0 - lit_rgb) * (1.0 - tint_col);
                lit_rgb = lerp(lit_rgb, lerp(scr_blend, tint_col, 0.52), in_amt);
                float out_edge = saturate(sil.g * 1.85);
                outer_a = saturate(out_edge * (1.0 - base_a) * (1.0 - inside_t) * str_n * (0.65 + over_boost * 0.35));
            }
        }

        // Combine premultiplied character interior + premultiplied outer rim/halo
        float char_Modify = saturate(abs(br - 1.0) * 4.0 + env_op * 4.0 + str_n * 2.0);
        // Preserve crisp antialiased silhouette edge without double-alpha thickening when unmodified
        float eff_base_a = lerp(base_a * base_a, base_a, saturate(base_a * 1.35 - 0.35));
        eff_base_a = lerp(0.0, eff_base_a, char_Modify);
        float out_a = saturate(eff_base_a + outer_a * (1.0 - eff_base_a)) * front_vis;
        if (out_a <= 1e-4) return float4(0.0, 0.0, 0.0, 0.0);

        float3 out_premul = (lit_rgb * eff_base_a + tint_col * outer_a * (1.0 - eff_base_a)) * front_vis;
        return float4(saturate(out_premul), out_a);
    }
