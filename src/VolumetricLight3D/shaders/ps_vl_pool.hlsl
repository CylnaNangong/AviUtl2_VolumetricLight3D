    Texture2D src : register(t0);
    cbuffer PoolCB : register(b0)
    {
        float4 p0; // x: img_w, y: img_h, z: factor, w: threshold
    };

    float4 ps_vl_pool(float4 pos : SV_Position) : SV_Target
    {
        int f = max((int)p0.z, 1);
        int stp = max((int)ceil(p0.z * 0.03125), 1);
        int2 base_p = int2(pos.xy) * f;
        int2 lim = int2(p0.xy);
        float m_thr  = 0.0;
        float m_base = 0.0;
        float cnt_base = 0.0;
        float tot = 0.0;
        [loop]
        for (int j = 0; j < 32; j++)
        {
            int oy = j * stp;
            if (oy >= f) break;
            [loop]
            for (int i = 0; i < 32; i++)
            {
                int ox = i * stp;
                if (ox >= f) break;
                int2 q = base_p + int2(ox, oy);
                if (q.x < lim.x && q.y < lim.y)
                {
                    tot += 1.0;
                    float4 s = src.Load(int3(q, 0));
                    if (s.a > 0.02)
                    {
                        float3 st = saturate(s.rgb / s.a);
                        float v = max(st.r, max(st.g, st.b));
                        if (v >= 0.05)
                        {
                            m_base = max(m_base, s.a);
                            cnt_base += 1.0;
                        }
                        if (v >= p0.w)
                        {
                            m_thr = max(m_thr, s.a);
                        }
                    }
                }
            }
        }
        float frac_base = (tot > 0.0) ? (cnt_base / tot) : 0.0;
        return float4(frac_base, frac_base, (m_base > 0.02) ? 1.0 : 0.0, m_thr);
    }
