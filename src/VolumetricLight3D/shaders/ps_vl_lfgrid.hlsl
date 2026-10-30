    // VL3D Light Field v2 — 遮蔽・色つき光源用の低解像度グリッド生成
    //   出力 (RGBA8, 既定 32x18):
    //     R : 2D 前景シルエット被覆率（occ_strength 適用済み）
    //     G : 3D オブジェクト（.obj/.vrm）被覆率
    //     B : 3D 表面のカメラ深度（0..1 正規化, 1.0 = 無し）
    //     A : 2D 遮蔽物平面までのカメラ深度（0..1 正規化, 1.0 = 無し）
    Texture2D g_occ    : register(t0);
    Texture2D g_camocc : register(t1);
    SamplerState g_smp : register(s0);

    cbuffer LFGridCB : register(b0)
    {
        float4 p0;   // x: grid_w, y: grid_h, z: scr_w, w: scr_h
        float4 p1;   // xyz: カメラ位置, w: 焦点距離
        float4 p2;   // xyz: カメラ前方, w: 2D 遮蔽物数
        float4 p3;   // xyz: カメラ右, w: UV スパン X（アトラス内の 1 遮蔽物の占有幅）
        float4 p4;   // xyz: カメラ下, w: UV スパン Y
        float4 p5;   // x: 遮蔽の強さ, y: camocc_w, z: camocc_h, w: 深度の正規化上限
        float4 p6;   // x: camocc 有効, y: 遮蔽の柔らかさ(0..1), z: 予備, w: 予備
        float4 occ[32];
    };

    // camocc の 1 テクセル: a<0.5 は空。r,g,b の平均が「光源平面からの近さ」を表す。
    void lf_camocc_one(int2 ip, int2 lim, float max_z,
        out float hit_w, out float z_val)
    {
        hit_w = 0.0;
        z_val = 1.0e9;
        if (ip.x < 0 || ip.y < 0 || ip.x >= lim.x || ip.y >= lim.y)
            return;
        float4 c = g_camocc.Load(int3(ip, 0));
        if (c.a < 0.5)
            return;
        float raw = (c.r + c.g + c.b) * (1.0 / 3.0);
        float prox = (raw > 0.50) ? saturate((raw - 0.51) * (1.0 / 0.48))
                                  : saturate((raw - 0.01) * (1.0 / 0.48));
        hit_w = 1.0;
        z_val = (1.0 - prox) * max_z;
    }

    float4 ps_vl_lfgrid(float4 pos : SV_Position) : SV_Target
    {
        float2 gdim = max(float2(p0.xy), float2(1.0, 1.0));
        float2 uv_s = (floor(pos.xy) + 0.5) / gdim;

        // ---- このセルを通る視線方向 ----
        float2 off = (uv_s - 0.5) * float2(p0.z, p0.w);
        float3 dir = p2.xyz * p1.w + p3.xyz * off.x + p4.xyz * off.y;
        dir = normalize(dir + float3(0.0, 0.0, 1.0e-6));

        // ---- 2D 前景マスク（beamatlas と同じ平面射影テスト） ----
        float mask = 0.0;
        float occ_d = 1.0e9;
        int n_occ = clamp((int)(p2.w + 0.5), 0, 8);
        [loop]
        for (int oi = 0; oi < 8; ++oi)
        {
            if (oi >= n_occ) break;
            int bi = oi * 4;
            float4 oc0 = occ[bi];
            float4 oc1 = occ[bi + 1];
            float4 oc2 = occ[bi + 2];
            float4 oc3 = occ[bi + 3];
            float den = dot(dir, oc3.xyz);
            if (abs(den) > 1.0e-4)
            {
                float t = dot(oc0.xyz - p1.xyz, oc3.xyz) / den;
                if (t > 0.001)
                {
                    float3 hp = p1.xyz + dir * t - oc0.xyz;
                    float2 uv_l = float2(0.5 + dot(hp, oc1.xyz) * oc0.w,
                                         0.5 + dot(hp, oc2.xyz) * oc1.w);
                    if (uv_l.x > 0.0 && uv_l.x < 1.0 && uv_l.y > 0.0 && uv_l.y < 1.0)
                    {
                        float2 uv_a = float2(oc2.w, oc3.w) + uv_l * float2(p3.w, p4.w);
                        float oa_fg = smoothstep(0.35, 0.85, g_occ.SampleLevel(g_smp, uv_a, 0.0).b);
                        if (oa_fg > mask) mask = oa_fg;
                        if (oa_fg > 0.01 && t < occ_d) occ_d = t;
                    }
                }
            }
        }
        mask = saturate(mask * p5.x);

        // ---- 3D オブジェクトのカメラ深度 ----
        float cov3 = 0.0;
        float z3 = 1.0e9;
        if (p6.x > 0.5)
        {
            int2 lim = max(int2(p5.y, p5.z), int2(1, 1));
            float2 fpos = uv_s * float2(lim) - 0.5;
            int2 i0 = int2(floor(fpos));
            float2 fr = fpos - float2(i0);
            float h00, z00, h10, z10, h01, z01, h11, z11;
            lf_camocc_one(i0,                lim, p5.w, h00, z00);
            lf_camocc_one(i0 + int2(1, 0),   lim, p5.w, h10, z10);
            lf_camocc_one(i0 + int2(0, 1),   lim, p5.w, h01, z01);
            lf_camocc_one(i0 + int2(1, 1),   lim, p5.w, h11, z11);
            cov3 = (1.0 - fr.x) * (1.0 - fr.y) * h00 + fr.x * (1.0 - fr.y) * h10
                 + (1.0 - fr.x) * fr.y * h01 + fr.x * fr.y * h11;
            if (h00 > 0.5) z3 = min(z3, z00);
            if (h10 > 0.5) z3 = min(z3, z10);
            if (h01 > 0.5) z3 = min(z3, z01);
            if (h11 > 0.5) z3 = min(z3, z11);
        }

        float inv_max = 1.0 / max(p5.w, 1.0);
        float zb = (z3 < 1.0e8) ? saturate(z3 * inv_max) : 1.0;
        float ab = (occ_d < 1.0e8) ? saturate(occ_d * inv_max) : 1.0;
        return float4(mask, saturate(cov3), zb, ab);
    }
