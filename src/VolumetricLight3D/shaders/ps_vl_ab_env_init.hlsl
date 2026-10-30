    Texture2D g_fb   : register(t0);
    Texture2D g_mask : register(t1);
    SamplerState g_smp : register(s0);
    cbuffer AbEnvInitCB : register(b0)
    {
        float4 e0; // x: ew, y: eh, z: mask_gain, w: 0
    };
    float4 ps_vl_ab_env_init(float4 pos : SV_Position) : SV_Target
    {
        float2 uv = pos.xy / max(e0.xy, float2(1.0, 1.0));
        float4 fb = g_fb.SampleLevel(g_smp, uv, 0.0);
        float ma  = g_mask.SampleLevel(g_smp, uv, 0.0).a;
        float keep = max(saturate(1.0 - ma * e0.z), 0.02);
        return float4(saturate(fb.rgb) * keep, saturate(fb.a) * keep);
    }
