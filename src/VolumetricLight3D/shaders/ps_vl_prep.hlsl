    Texture2D src : register(t0);
    cbuffer PrepCB : register(b0)
    {
        float4 p0; // x: img_w, y: img_h, z: kf, w: threshold
        float4 p1; // xyz: light_rgb, w: use_source
    };

    float4 ps_vl_prep(float4 pos : SV_Position) : SV_Target
    {
        int f = max((int)p0.z, 1);
        int stp = max((int)ceil(p0.z * 0.0625), 1);
        int2 base_p = int2(pos.xy) * f;
        int2 lim = int2(p0.xy);
        float thr  = p0.w;
        float knee = max(0.08, (1.0 - thr) * 0.18);

        float3 acc_c = float3(0.0, 0.0, 0.0);
        float  acc_a = 0.0;
        float  cnt   = 0.0;

        [loop]
        for (int j = 0; j < 16; j++)
        {
            int oy = j * stp;
            if (oy >= f) break;
            [loop]
            for (int i = 0; i < 16; i++)
            {
                int ox = i * stp;
                if (ox >= f) break;
                int2 q = base_p + int2(ox, oy);
                if (q.x < lim.x && q.y < lim.y)
                {
                    cnt += 1.0;
                    float4 s = src.Load(int3(q, 0));
                    if (s.a > 0.01)
                    {
                        float3 straight = saturate(s.rgb / max(s.a, 1e-4));
                        float vmax = max(straight.r, max(straight.g, straight.b));
                        float gate = (thr <= 0.001) ? 1.0 : smoothstep(thr, min(thr + knee, 1.0), vmax);
                        float cov = s.a * gate;
                        if (cov > 0.001)
                        {
                            float vmin = min(straight.r, min(straight.g, straight.b));
                            float3 pure_c = straight - vmin * 0.30 * (1.0 - vmin);
                            float lum_lift = min(pow(max(vmax, 1e-4), -0.20), 1.40);
                            float3 base_c = (p1.w > 0.5)
                                ? saturate(pure_c * lum_lift)
                                : (p1.xyz * pow(max(vmax, 1e-4), 0.72));
                            acc_c += base_c * cov;
                            acc_a += cov;
                        }
                    }
                }
            }
        }
        float inv_n = (cnt > 0.0) ? (1.0 / cnt) : 0.0;
        return float4(saturate(acc_c * inv_n), saturate(acc_a * inv_n));
    }
