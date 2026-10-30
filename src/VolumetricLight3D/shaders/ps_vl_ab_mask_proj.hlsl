    Texture2D g_src  : register(t0);
    Texture2D g_prev : register(t1);
    SamplerState g_smp : register(s0);
    cbuffer AbMaskCB : register(b0)
    {
        float4 m0; // x: ew, y: eh, z: focal_d, w: alpha_mul
        float4 m1; // xyz: cam_pos, w: scr_w
        float4 m2; // xyz: cam_f, w: scr_h
        float4 m3; // xyz: cam_r, w: is_ortho
        float4 m4; // xyz: cam_d, w: color_comp_mode(0=mask_only, 1=srcover_prev, 2=srcover_first)
        float4 m5; // xyz: plane_c, w: inv_2Hx
        float4 m6; // xyz: Or, w: inv_2Hy
        float4 m7; // xyz: Od, w: 0
        float4 m8; // xyz: On, w: 0
    };
    float4 ps_vl_ab_mask_proj(float4 pos : SV_Position) : SV_Target
    {
        float2 uv_scr = pos.xy / max(m0.xy, float2(1.0, 1.0));
        float4 prev = (m4.w > 0.5 && m4.w < 1.5) ? g_prev.SampleLevel(g_smp, uv_scr, 0.0) : float4(0.0, 0.0, 0.0, 0.0);
        float2 sp = uv_scr * float2(m1.w, m2.w) - 0.5 * float2(m1.w, m2.w);
        float3 org = (m3.w > 0.5) ? (m1.xyz + m3.xyz * sp.x + m4.xyz * sp.y) : m1.xyz;
        float3 ray = (m3.w > 0.5) ? m2.xyz : (m2.xyz + m3.xyz * (sp.x / max(m0.z, 1.0)) + m4.xyz * (sp.y / max(m0.z, 1.0)));
        float den = dot(ray, m8.xyz);
        if (abs(den) < 1e-5) return prev;
        float lam = dot(m5.xyz - org, m8.xyz) / den;
        if (lam <= 1e-3) return prev;
        float3 hp = org + ray * lam - m5.xyz;
        float2 uv = float2(0.5 + dot(hp, m6.xyz) * m5.w, 0.5 + dot(hp, m7.xyz) * m6.w);
        if (uv.x <= 0.0 || uv.x >= 1.0 || uv.y <= 0.0 || uv.y >= 1.0) return prev;
        float4 s = g_src.SampleLevel(g_smp, uv, 0.0);
        if (m4.w > 0.5)
        {
            float4 col = saturate(s) * saturate(m0.w);
            return col + prev * saturate(1.0 - col.a);
        }
        float a = saturate(s.a * m0.w);
        return float4(a, a, a, a);
    }
