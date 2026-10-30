    Texture2D g_src : register(t0);
    SamplerState g_smp : register(s0);
    cbuffer AbSilHCB : register(b0)
    {
        float4 s0; // x: lw, y: lh, z: base_w, w: base_h
        float4 s1; // x: pad_x, y: pad_y, z: rad_in, w: rad_out
    };
    float sample_a_h(float2 bp, float2 bsz)
    {
        if (bp.x <= 0.0 || bp.x >= bsz.x || bp.y <= 0.0 || bp.y >= bsz.y) return 0.0;
        return g_src.SampleLevel(g_smp, bp / bsz, 0.0).a;
    }
    float4 ps_vl_ab_sil_h(float4 pos : SV_Position) : SV_Target
    {
        float2 bp0 = pos.xy - s1.xy;
        float2 bsz = max(s0.zw, float2(1.0, 1.0));
        float step_in  = max(s1.z, 0.5) / 8.0;
        float step_out = max(s1.w, 0.5) / 8.0;
        float acc_in = 0.0, win_sum = 0.0;
        float acc_out = 0.0, wout_sum = 0.0;
        [unroll]
        for (int k = -8; k <= 8; ++k)
        {
            float fk = (float)k;
            float wg = exp(-0.5 * (fk * fk) / 14.0);
            acc_in  += sample_a_h(bp0 + float2(fk * step_in, 0.0), bsz) * wg;
            win_sum += wg;
            acc_out += sample_a_h(bp0 + float2(fk * step_out, 0.0), bsz) * wg;
            wout_sum += wg;
        }
        return float4(acc_in / win_sum, acc_out / wout_sum, 0.0, 1.0);
    }
