--[[pixelshader@ps_snow_shape_tint:
Texture2D<float4> Src : register(t0);
cbuffer SnowTintCB : register(b0) { float4 tint; float4 pad; };
// ぼかし済みの図形テクスチャ（乗算済みα・白）へ受光色を乗算する。
// rgb のみ色付けし α はそのまま残すので、加算合成しても
// 「光の色 × 図形のα」という物理的に正しい加算項になる。
float4 ps_snow_shape_tint(float4 pos : SV_Position) : SV_Target {
    float4 s = Src.Load(int3(int2(pos.xy), 0));
    return float4(s.rgb * tint.rgb, s.a);
}
]]
