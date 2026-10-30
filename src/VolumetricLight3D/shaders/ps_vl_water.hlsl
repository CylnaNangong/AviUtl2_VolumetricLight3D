    cbuffer WaterCB : register(b0)
    {
        float4 w0; // x: size, y: time, z: sp, w: freq
        float4 w1; // x: vx, y: vy, z: hw_rip, w: hh_rip
        float4 w2; // x: cx_rip, y: cy_rip, z: hb, w: rip_spd
        float4 w3; // x: col_r, y: col_g, z: col_b, w: alpha
    };

    float w_hash21(float2 p)
    {
        uint2 q = uint2(int2(floor(p)) + 65536) * uint2(1597334677u, 3812015801u);
        uint n = (q.x ^ q.y) * 1597334677u;
        return float(n) * (1.0 / 4294967296.0);
    }

    float w_vnoise(float2 p)
    {
        float2 ip = floor(p);
        float2 fp = p - ip;
        fp = fp * fp * (3.0 - 2.0 * fp);
        float a = w_hash21(ip);
        float b = w_hash21(ip + float2(1.0, 0.0));
        float c = w_hash21(ip + float2(0.0, 1.0));
        float d = w_hash21(ip + float2(1.0, 1.0));
        return lerp(lerp(a, b, fp.x), lerp(c, d, fp.x), fp.y);
    }

    float w_caustic(float2 uv_s, float t, float2 flow)
    {
        float2 p1 = uv_s + flow * t;
        float2 p2 = uv_s * 1.93 + float2(4.7, 2.1) - flow * (t * 0.72);
        float2 p3 = uv_s * 3.71 + float2(1.9, 8.4) + flow * (t * 1.28);
        float n1 = w_vnoise(p1);
        float n2 = w_vnoise(p2);
        float n3 = w_vnoise(p3);
        float f1 = pow(saturate(1.0 - abs(n1 * 2.0 - 1.0) * 1.35), 2.4);
        float f2 = pow(saturate(1.0 - abs(n2 * 2.0 - 1.0) * 1.35), 2.4);
        float f3 = pow(saturate(1.0 - abs(n3 * 2.0 - 1.0) * 1.45), 3.0);
        return saturate(f1 * 0.62 + f2 * 0.52 + f1 * f2 * 0.85 + f3 * 0.24);
    }

    float4 ps_vl_water(float4 pos : SV_Position) : SV_Target
    {
        float sz = max(w0.x, 16.0);
        float2 uv = pos.xy / sz;
        float t = w0.y;
        float freq = clamp(max(w0.w, 0.28) * 26.0, 7.5, 24.0);
        float2 flow = float2(w1.x, w1.y) * 0.55;

        float2 warp = float2(
            sin(uv.y * 28.2743 + t * 2.2) * 0.016,
            cos(uv.x * 21.9911 + t * 1.8) * 0.020
        );
        float2 p_base = (uv + warp) * freq;

        float2 center_uv = float2(0.5, 0.5) + float2(w2.x, w2.y) / sz;
        float dist_c = length(uv - center_uv) * sz;
        float rip_amp = saturate(w1.w * 0.030);
        float rip_w   = max(w1.z, 1.0) * 5.0;
        float rip_wave = sin(dist_c / rip_w - t * w2.w * 0.07) * rip_amp;

        float swell  = w_vnoise(p_base * 0.42 + flow * (t * 0.35));
        float caus   = w_caustic(p_base + rip_wave * 2.6, t, flow);
        // 波谷を 0 まで落とし、波間から差し込むサファイア〜アクアの鋭い集光光条を鮮明に分離
        float shaft  = smoothstep(0.44, 0.93, caus * (0.58 + 0.66 * swell) + rip_wave * 0.42);
        float crest  = pow(shaft, 1.85);

        float hb_px = max(w2.z, 0.0);
        float edge_fade = (hb_px > 0.5) ? saturate((sz - pos.y) / max(hb_px, 1.0)) : 1.0;

        float3 in_c = clamp(w3.xyz, 0.0, 1.0);
        // ユーザー指定色・光の色（黄色 #ffff00 や純白 #ffffff 等）を忠実に保持
        float3 water_mid  = in_c;
        float3 water_high = lerp(in_c, float3(1.00, 1.00, 1.00), 0.48);

        float3 bright_col = lerp(water_mid, water_high, crest);
        float out_a = saturate(w3.w * edge_fade * shaft * 0.96);
        return float4(bright_col * out_a, out_a);
    }
