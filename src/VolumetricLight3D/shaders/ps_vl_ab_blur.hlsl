    Texture2D g_in : register(t0);
    SamplerState g_smp : register(s0);
    cbuffer AbBlurCB : register(b0)
    {
        float4 b0_c; // x: step_u, y: step_v, z: sigma, w: do_normalize
        float4 b1_c; // x: w, y: h, z: 0, w: 0
        float4 b2_c; // xyz: fallback_rgb, w: 0
    };
    float4 ps_vl_ab_blur(float4 pos : SV_Position) : SV_Target
    {
        float2 uv = pos.xy / max(b1_c.xy, float2(1.0, 1.0));
        float2 step_uv = b0_c.xy;
        float inv_2s2 = 0.5 / max(b0_c.z * b0_c.z, 0.25);
        float4 acc = float4(0.0, 0.0, 0.0, 0.0);
        float wsum = 0.0;
        [unroll]
        for (int k = -6; k <= 6; ++k)
        {
            float fk = (float)k;
            float wk = exp(-fk * fk * inv_2s2);
            acc += g_in.SampleLevel(g_smp, clamp(uv + step_uv * fk, 0.001, 0.999), 0.0) * wk;
            wsum += wk;
        }
        float4 res = acc / max(wsum, 1e-5);
        if (b0_c.w > 0.5)
        {
            if (res.a <= 1e-4) return float4(b2_c.xyz, 0.0);
            float conf = saturate(res.a / 0.03);
            float3 nc = saturate(res.rgb / res.a);
            return float4(lerp(b2_c.xyz, nc, conf), conf);
        }
        return res;
    }
