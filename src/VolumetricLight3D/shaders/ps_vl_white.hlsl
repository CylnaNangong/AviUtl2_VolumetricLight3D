    cbuffer WhiteCB : register(b0)
    {
        float4 p0; // x: inv_half_w, y: inv_half_h, z: shape_mode, w: r_lim_or_aa
    };

    float4 ps_vl_white(float4 pos : SV_Position) : SV_Target
    {
        if (p0.z < 0.5)
        {
            return float4(1.0, 1.0, 1.0, 1.0);
        }
        float2 d = float2(pos.x * p0.x - 1.0, pos.y * p0.y - 1.0);
        float r = length(d);
        float cov = 0.0;
        if (p0.z < 1.5)
        {
            float r_lim = (p0.w > 0.1) ? p0.w : 1.0;
            float e0 = max(0.0, r_lim - (2.0 / 128.0));
            cov = 1.0 - smoothstep(e0, r_lim, r);
        }
        else
        {
            float c = cos(saturate(r) * 1.57079633);
            cov = c * sqrt(max(c, 0.0));
        }
        return float4(cov, cov, cov, cov);
    }
