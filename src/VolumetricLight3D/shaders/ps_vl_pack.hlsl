    Texture2D prev_atl : register(t0);
    Texture2D cur_occ  : register(t1);
    SamplerState smp   : register(s0);
    cbuffer PackCB : register(b0)
    {
        float4 p0; // x: tile_x0, y: tile_y0, z: tile_w, w: tile_h
        float4 p1; // x: pad_x,   y: pad_y,   z: alpha,  w: 0
        float4 p2; // x: inv_src_w, y: inv_src_h, z: taps_x, w: taps_y
    };

    float4 ps_vl_pack(float4 pos : SV_Position) : SV_Target
    {
        float2 px = pos.xy;
        if (px.x < p0.x || px.x >= p0.x + p0.z || px.y < p0.y || px.y >= p0.y + p0.w)
        {
            return prev_atl.Load(int3(int2(px), 0));
        }
        float2 inner = max(float2(p0.z, p0.w) - 2.0 * p1.xy, float2(1.0, 1.0));
        float2 uv = (px - p0.xy - p1.xy) / inner;
        if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)
        {
            return float4(0.0, 0.0, 0.0, 0.0);
        }
        // 高解像度素材（6K 等）や繊細なバラ窓切り抜きも潰さない 2D テントフィルタ縮小
        int nx = clamp((int)(p2.z + 0.5), 1, 8);
        int ny = clamp((int)(p2.w + 0.5), 1, 8);
        float2 half_span = 0.5 / inner;
        float acc_a = 0.0;
        float wsum  = 0.0;
        [loop]
        for (int j = 0; j < 8; ++j)
        {
            if (j >= ny) break;
            float ty = (ny > 1) ? (2.0 * ((float)j + 0.5) / (float)ny - 1.0) : 0.0;
            float wy = 1.0 - abs(ty);
            [loop]
            for (int i = 0; i < 8; ++i)
            {
                if (i >= nx) break;
                float tx = (nx > 1) ? (2.0 * ((float)i + 0.5) / (float)nx - 1.0) : 0.0;
                float w = (1.0 - abs(tx)) * wy;
                float2 suv = clamp(uv + float2(tx, ty) * half_span, 0.0, 1.0);
                acc_a += w * cur_occ.SampleLevel(smp, suv, 0.0).a;
                wsum  += w;
            }
        }
        float a = saturate((acc_a / max(wsum, 1e-5)) * p1.z);
        // .b: 非ブラー生シルエット（2D 前景マスク黒フチ防止用） / .a: ソフト半影用
        return float4(0.0, 0.0, a, a);
    }
