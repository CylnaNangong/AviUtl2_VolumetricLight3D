    Texture2D src : register(t0);
    cbuffer BlurCB : register(b0)
    {
        float4 p0; // x: w, y: h, z: dx, w: dy
        float4 p1; // x: sigma, y: radius
    };
    float4 ps_vl_occ_blur(float4 pos : SV_Position) : SV_Target
    {
        int2 ip = int2(pos.xy);
        int2 lim = int2((int)p0.x - 1, (int)p0.y - 1);
        int2 c_ip = clamp(ip, int2(0, 0), lim);
        float raw_b = src.Load(int3(c_ip, 0)).b;
        int2 step_d = int2((int)p0.z, (int)p0.w);
        int rad = clamp((int)p1.y, 0, 20);
        if (rad <= 0 || p1.x <= 0.15)
        {
            float raw_a = src.Load(int3(c_ip, 0)).a;
            return float4(0.0, 0.0, raw_b, raw_a);
        }
        float inv_2s2 = 0.5 / max(p1.x * p1.x, 1e-4);
        float sum_a = 0.0;
        float sum_w = 0.0;
        [loop]
        for (int k = -20; k <= 20; k++)
        {
            if (k < -rad || k > rad) continue;
            float wk = exp(-((float)(k * k)) * inv_2s2);
            int2 q = clamp(ip + step_d * k, int2(0, 0), lim);
            sum_a += src.Load(int3(q, 0)).a * wk;
            sum_w += wk;
        }
        return float4(0.0, 0.0, raw_b, saturate(sum_a / max(sum_w, 1e-6)));
    }
