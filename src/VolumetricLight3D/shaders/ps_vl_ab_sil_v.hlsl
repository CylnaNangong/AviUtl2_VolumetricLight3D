    Texture2D g_h : register(t0);
    SamplerState g_smp : register(s0);
    cbuffer AbSilVCB : register(b0)
    {
        float4 s0; // x: lw, y: lh, z: rad_in, w: rad_out
    };
    float4 ps_vl_ab_sil_v(float4 pos : SV_Position) : SV_Target
    {
        float2 lsz = max(s0.xy, float2(1.0, 1.0));
        float step_in  = max(s0.z, 0.5) / 8.0;
        float step_out = max(s0.w, 0.5) / 8.0;
        float acc_in = 0.0, win_sum = 0.0;
        float acc_out = 0.0, wout_sum = 0.0;
        [unroll]
        for (int k = -8; k <= 8; ++k)
        {
            float fk = (float)k;
            float wg = exp(-0.5 * (fk * fk) / 14.0);
            float2 uv_in  = clamp((pos.xy + float2(0.0, fk * step_in)) / lsz, 0.001, 0.999);
            float2 uv_out = clamp((pos.xy + float2(0.0, fk * step_out)) / lsz, 0.001, 0.999);
            acc_in  += g_h.SampleLevel(g_smp, uv_in, 0.0).r * wg;
            win_sum += wg;
            acc_out += g_h.SampleLevel(g_smp, uv_out, 0.0).g * wg;
            wout_sum += wg;
        }
        return float4(acc_in / win_sum, acc_out / wout_sum, 0.0, 1.0);
    }
