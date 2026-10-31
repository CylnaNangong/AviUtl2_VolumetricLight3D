#pragma once
//----------------------------------------------------------------------------------
//  VL3DBridge v1.22 — Embedded Direct3D 11 Compute Shader (cs_5_0) Pipeline
//  3D Shadow Map & Camera Occlusion Depth Hardware Rasterizer
//  Zero local register arrays (100% FXC D3DCompile X3550-proof + Vulkan SPIR-V clean)
//----------------------------------------------------------------------------------

namespace vl3d {

static constexpr const char* kVl3dRastHlslCs = R"HLSL(
#ifdef VL3D_VULKAN_TEST
#define VL_LOOP
#else
#define VL_LOOP [loop]
#endif

cbuffer RastCB : register(b0)
{
    float4 c0;  // x=width, y=height, z=pass_mode (0=shadow, 1=camdepth), w=tri_count
    float4 c1;  // x=ox, y=oy, z=oz, w=is_vrm (0 or 1)
    float4 c2;  // x=r0x, y=r0y, z=r0z, w=use_plane2 (0 or 1)
    float4 c3;  // x=d0x, y=d0y, z=d0z, w=mode_kind
    float4 c4;  // x=n0x, y=n0y, z=n0z, w=L_tot_or_max_z
    float4 c5;  // plane1: xyz=normal, w=offset (dot(P, n) - off >= 0)
    float4 c6;  // plane2: xyz=normal, w=offset
    float4 c7;  // xyz=w_pos_or_cam, w=inv_L_or_invmz
    float4 c8;  // xyz=Wr_or_r, w=inv_2Hx_or_scalex
    float4 c9;  // xyz=Wd_or_d, w=inv_2Hy_or_scaley
    float4 c10; // xyz=Wn_or_f, w=u_wn
    float4 c11; // xyz=sp, w=dS_wn
    float4 c12; // x=splat_cap, y=large_bbox_thr, z=max_large_tris, w=total_pixels
    float4 c13;
    float4 c14;
    float4 c15;
};

StructuredBuffer<float4>   g_verts     : register(t0);
StructuredBuffer<uint4>    g_tris      : register(t1);

RWStructuredBuffer<uint>   g_prox_buf  : register(u0);
RWStructuredBuffer<uint>   g_rgba_buf  : register(u1);
RWStructuredBuffer<uint>   g_meta_buf  : register(u2); // [0]=large_tri_cnt, [1]=hit_pixel_cnt
RWStructuredBuffer<float4> g_large_buf : register(u3); // 4 float4s per queued large sub-triangle

uint pack_prox_val(float pr, uint is_vrm_u)
{
    float pc = saturate(pr);
    uint q = (uint)(pc * 16777214.0 + 0.5) + 1u;
    return (q << 1u) | (is_vrm_u & 1u);
}

// Returns float4(x_s, y_s, prox, z_front)
float4 project_world_pt(float3 P)
{
    if (c0.z < 0.5)
    {
        float3 w_pos = c7.xyz;
        float3 Wr    = c8.xyz;
        float3 Wd    = c9.xyz;
        float3 Wn    = c10.xyz;
        float  u_wn  = c10.w;
        float3 sp    = c11.xyz;
        float  dS_wn = c11.w;
        float  t_ray;
        float3 qw;
        if (c3.w < 0.5)
        {
            t_ray = dot(P - w_pos, Wn) / u_wn;
            qw = P - sp * t_ray;
        }
        else
        {
            float3 dv = P - sp;
            float denom = dot(dv, Wn);
            if (abs(denom) < 1.0e-4)
            {
                denom = (denom >= 0.0) ? 1.0e-4 : -1.0e-4;
            }
            float alpha = dS_wn / denom;
            t_ray = abs(1.0 - alpha) * length(dv);
            qw = sp + dv * alpha;
        }
        float3 dq = qw - w_pos;
        float u_w = 0.5 + dot(dq, Wr) * c8.w;
        float v_w = 0.5 + dot(dq, Wd) * c9.w;
        float inv_span = 1.0 / 1.08;
        float x_s = ((u_w + 0.04) * inv_span) * c0.x;
        float y_s = ((v_w + 0.04) * inv_span) * c0.y;
        float prox = saturate(1.0 - t_ray * c7.w);
        float z_front = abs(dot(P - w_pos, Wn));
        return float4(x_s, y_s, prox, z_front);
    }
    else
    {
        float3 dv = P - c7.xyz;
        float zc = max(4.0, dot(dv, c10.xyz));
        float inv_z = 1.0 / zc;
        float x_s = 0.5 * c0.x + dot(dv, c8.xyz) * c8.w * inv_z;
        float y_s = 0.5 * c0.y + dot(dv, c9.xyz) * c9.w * inv_z;
        float prox = saturate(1.0 - zc * c7.w);
        return float4(x_s, y_s, prox, zc);
    }
}

void push_v4(inout int cnt, inout float3 v0, inout float3 v1, inout float3 v2, inout float3 v3, float3 val)
{
    if (cnt == 0)      v0 = val;
    else if (cnt == 1) v1 = val;
    else if (cnt == 2) v2 = val;
    else if (cnt == 3) v3 = val;
    if (cnt < 4) cnt += 1;
}

float3 get_v4(int idx, float3 v0, float3 v1, float3 v2, float3 v3)
{
    if (idx <= 0) return v0;
    if (idx == 1) return v1;
    if (idx == 2) return v2;
    return v3;
}

void push_v5(inout int cnt, inout float3 v0, inout float3 v1, inout float3 v2, inout float3 v3, inout float3 v4, float3 val)
{
    if (cnt == 0)      v0 = val;
    else if (cnt == 1) v1 = val;
    else if (cnt == 2) v2 = val;
    else if (cnt == 3) v3 = val;
    else if (cnt == 4) v4 = val;
    if (cnt < 5) cnt += 1;
}

float3 get_v5(int idx, float3 v0, float3 v1, float3 v2, float3 v3, float3 v4)
{
    if (idx <= 0) return v0;
    if (idx == 1) return v1;
    if (idx == 2) return v2;
    if (idx == 3) return v3;
    return v4;
}

void clip_edge_to_v4(float3 prev, float prev_d, float3 cur, float cur_d,
                     inout int n1, inout float3 p0, inout float3 p1, inout float3 p2, inout float3 p3)
{
    if (cur_d >= 0.0)
    {
        if (prev_d < 0.0)
        {
            float t = prev_d / (prev_d - cur_d);
            push_v4(n1, p0, p1, p2, p3, prev + (cur - prev) * t);
        }
        push_v4(n1, p0, p1, p2, p3, cur);
    }
    else if (prev_d >= 0.0)
    {
        float t = prev_d / (prev_d - cur_d);
        push_v4(n1, p0, p1, p2, p3, prev + (cur - prev) * t);
    }
}

void clip_edge_to_v5(float3 prev, float prev_d, float3 cur, float cur_d,
                     inout int n2, inout float3 p0, inout float3 p1, inout float3 p2, inout float3 p3, inout float3 p4)
{
    if (cur_d >= 0.0)
    {
        if (prev_d < 0.0)
        {
            float t = prev_d / (prev_d - cur_d);
            push_v5(n2, p0, p1, p2, p3, p4, prev + (cur - prev) * t);
        }
        push_v5(n2, p0, p1, p2, p3, p4, cur);
    }
    else if (prev_d >= 0.0)
    {
        float t = prev_d / (prev_d - cur_d);
        push_v5(n2, p0, p1, p2, p3, p4, prev + (cur - prev) * t);
    }
}

void rasterize_subtri_inline(
    float x0, float y0, float p0,
    float x1, float y1, float p1,
    float x2, float y2, float p2,
    int w, int h, bool allow_wire_splat, uint is_vrm_u,
    bool allow_defer_large)
{
    float minx = min(x0, min(x1, x2));
    float maxx = max(x0, max(x1, x2));
    float miny = min(y0, min(y1, y2));
    float maxy = max(y0, max(y1, y2));
    if (maxx < 0.0 || minx >= (float)w || maxy < 0.0 || miny >= (float)h)
    {
        return;
    }

    int ix0 = max(0, (int)floor(minx));
    int ix1 = min(w - 1, (int)floor(maxx));
    int iy0 = max(0, (int)floor(miny));
    int iy1 = min(h - 1, (int)floor(maxy));
    if (ix0 > ix1 || iy0 > iy1)
    {
        return;
    }

    float area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0);
    float span_max = max(maxx - minx, maxy - miny);

    if (allow_wire_splat && abs(area) < 1.35 * max(span_max, 0.25))
    {
        float ax = x0, ay = y0, ap = p0;
        float bx = x1, by = y1, bp = p1;
        float d01 = abs(x1 - x0) + abs(y1 - y0);
        float d12 = abs(x2 - x1) + abs(y2 - y1);
        float d20 = abs(x0 - x2) + abs(y0 - y2);
        if (d12 >= d01 && d12 >= d20)
        {
            ax = x1; ay = y1; ap = p1;
            bx = x2; by = y2; bp = p2;
        }
        else if (d20 >= d01)
        {
            ax = x2; ay = y2; ap = p2;
            bx = x0; by = y0; bp = p0;
        }
        int cap_i = max(1, (int)c12.x);
        int req_i = max(1, (int)ceil(max(abs(bx - ax), abs(by - ay))));
        int n_step = min(cap_i, req_i);
        float inv_ns = 1.0 / (float)n_step;
        float stx = (bx - ax) * inv_ns;
        float sty = (by - ay) * inv_ns;
        float stp = (bp - ap) * inv_ns;
        float sx = ax, sy = ay, sp_v = ap;
        VL_LOOP
        for (int s = 0; s <= n_step; ++s)
        {
            int px_i = (int)floor(sx);
            int py_i = (int)floor(sy);
            if (px_i >= 0 && px_i < w && py_i >= 0 && py_i < h)
            {
                uint pv = pack_prox_val(sp_v, is_vrm_u);
                uint prev_u;
                InterlockedMax(g_prox_buf[py_i * w + px_i], pv, prev_u);
            }
            sx += stx;
            sy += sty;
            sp_v += stp;
        }
        if (abs(area) < 1.0e-5)
        {
            return;
        }
    }

    if (abs(area) < 1.0e-5)
    {
        return;
    }

    if (area < 0.0)
    {
        float tx = x1, ty = y1, tp = p1;
        x1 = x2; y1 = y2; p1 = p2;
        x2 = tx; y2 = ty; p2 = tp;
        area = -area;
    }

    int bbox_px = (ix1 - ix0 + 1) * (iy1 - iy0 + 1);
    int large_thr = (int)c12.y;
    uint max_large = (uint)c12.z;
    if (allow_defer_large && bbox_px > large_thr && max_large > 0u)
    {
        uint slot;
        InterlockedAdd(g_meta_buf[0], 1u, slot);
        if (slot < max_large)
        {
            uint base_i = slot * 4u;
            uint flags_u = (allow_wire_splat ? 2u : 0u) | (is_vrm_u & 1u);
            g_large_buf[base_i + 0u] = float4(x0, y0, p0, area);
            g_large_buf[base_i + 1u] = float4(x1, y1, p1, (float)flags_u);
            g_large_buf[base_i + 2u] = float4(x2, y2, p2, 0.0);
            g_large_buf[base_i + 3u] = float4((float)ix0, (float)ix1, (float)iy0, (float)iy1);
            return;
        }
    }

    float inv_a = 1.0 / area;
    float dx12 = x1 - x2, dy12 = y1 - y2;
    float dx20 = x2 - x0, dy20 = y2 - y0;
    float dx01 = x0 - x1, dy01 = y0 - y1;
    float eps = -1.0e-5;
    if (allow_wire_splat)
    {
        eps = -0.02 * max(max(abs(dx12) + abs(dy12), abs(dx20) + abs(dy20)), 1.0);
    }

    VL_LOOP
    for (int py = iy0; py <= iy1; ++py)
    {
        float cy = (float)py + 0.5;
        int px0 = ix0;
        int px1 = ix1;

        float rhs0 = eps + (cy - y1) * dx12 - (0.5 - x1) * dy12;
        if (dy12 > 1.0e-9)       px0 = max(px0, (int)ceil(rhs0 / dy12 - 1.0e-6));
        else if (dy12 < -1.0e-9) px1 = min(px1, (int)floor(rhs0 / dy12 + 1.0e-6));
        else if (rhs0 > 0.0)     px0 = px1 + 1;

        if (px0 <= px1)
        {
            float rhs1 = eps + (cy - y2) * dx20 - (0.5 - x2) * dy20;
            if (dy20 > 1.0e-9)       px0 = max(px0, (int)ceil(rhs1 / dy20 - 1.0e-6));
            else if (dy20 < -1.0e-9) px1 = min(px1, (int)floor(rhs1 / dy20 + 1.0e-6));
            else if (rhs1 > 0.0)     px0 = px1 + 1;
        }
        if (px0 <= px1)
        {
            float rhs2 = eps + (cy - y0) * dx01 - (0.5 - x0) * dy01;
            if (dy01 > 1.0e-9)       px0 = max(px0, (int)ceil(rhs2 / dy01 - 1.0e-6));
            else if (dy01 < -1.0e-9) px1 = min(px1, (int)floor(rhs2 / dy01 + 1.0e-6));
            else if (rhs2 > 0.0)     px0 = px1 + 1;
        }

        if (px0 <= px1)
        {
            float cx = (float)px0 + 0.5;
            float w0 = (cx - x1) * dy12 - (cy - y1) * dx12;
            float w1 = (cx - x2) * dy20 - (cy - y2) * dx20;
            float w2 = (cx - x0) * dy01 - (cy - y0) * dx01;
            int row_off = py * w;
            VL_LOOP
            for (int px = px0; px <= px1; ++px)
            {
                if (w0 >= eps && w1 >= eps && w2 >= eps)
                {
                    float b0 = w0 * inv_a;
                    float b1 = w1 * inv_a;
                    float pr = b0 * p0 + b1 * p1 + (1.0 - b0 - b1) * p2;
                    uint pv = pack_prox_val(pr, is_vrm_u);
                    uint prev_u;
                    InterlockedMax(g_prox_buf[row_off + px], pv, prev_u);
                }
                w0 += dy12;
                w1 += dy20;
                w2 += dy01;
            }
        }
    }
}

[numthreads(256, 1, 1)]
void cs_clear(uint3 dtid : SV_DispatchThreadID)
{
    uint idx = dtid.x;
    uint total_px = (uint)c12.w;
    if (idx == 0u)
    {
        g_meta_buf[0] = 0u;
        g_meta_buf[1] = 0u;
    }
    if (idx < total_px)
    {
        g_prox_buf[idx] = 0u;
    }
}

[numthreads(64, 1, 1)]
void cs_project_and_rast_small(uint3 dtid : SV_DispatchThreadID)
{
    uint ti = dtid.x;
    uint tri_cnt = (uint)c0.w;
    if (ti >= tri_cnt)
    {
        return;
    }

    uint4 tri = g_tris[ti];
    float3 la = g_verts[tri.x].xyz;
    float3 lb = g_verts[tri.y].xyz;
    float3 lc = g_verts[tri.z].xyz;

    float3 wa = c1.xyz + c2.xyz * la.x + c3.xyz * la.y + c4.xyz * la.z;
    float3 wb = c1.xyz + c2.xyz * lb.x + c3.xyz * lb.y + c4.xyz * lb.z;
    float3 wc = c1.xyz + c2.xyz * lc.x + c3.xyz * lc.y + c4.xyz * lc.z;

    float d1a = dot(wa, c5.xyz) - c5.w;
    float d1b = dot(wb, c5.xyz) - c5.w;
    float d1c = dot(wc, c5.xyz) - c5.w;
    if (d1a < 0.0 && d1b < 0.0 && d1c < 0.0)
    {
        return;
    }

    bool use_p2 = (c2.w > 0.5);
    float d2a = 1.0, d2b = 1.0, d2c = 1.0;
    if (use_p2)
    {
        d2a = dot(wa, c6.xyz) - c6.w;
        d2b = dot(wb, c6.xyz) - c6.w;
        d2c = dot(wc, c6.xyz) - c6.w;
    }
    if (d2a < 0.0 && d2b < 0.0 && d2c < 0.0)
    {
        return;
    }

    int w = (int)c0.x;
    int h = (int)c0.y;
    bool allow_wire = (c0.z < 0.5);
    uint is_vrm_u = (c1.w > 0.5) ? 1u : 0u;

    if (d1a >= 0.0 && d1b >= 0.0 && d1c >= 0.0 && d2a >= 0.0 && d2b >= 0.0 && d2c >= 0.0)
    {
        float4 pa = project_world_pt(wa);
        float4 pb = project_world_pt(wb);
        float4 pc = project_world_pt(wc);
        if (allow_wire && pa.w > c4.w && pb.w > c4.w && pc.w > c4.w)
        {
            return;
        }
        rasterize_subtri_inline(
            pa.x, pa.y, pa.z,
            pb.x, pb.y, pb.z,
            pc.x, pc.y, pc.z,
            w, h, allow_wire, is_vrm_u, true);
        return;
    }

    // Register-only Sutherland-Hodgman clip against plane 1 (max 4 verts)
    float3 p1_0 = float3(0.0, 0.0, 0.0);
    float3 p1_1 = float3(0.0, 0.0, 0.0);
    float3 p1_2 = float3(0.0, 0.0, 0.0);
    float3 p1_3 = float3(0.0, 0.0, 0.0);
    int n1 = 0;
    clip_edge_to_v4(wc, d1c, wa, d1a, n1, p1_0, p1_1, p1_2, p1_3);
    clip_edge_to_v4(wa, d1a, wb, d1b, n1, p1_0, p1_1, p1_2, p1_3);
    clip_edge_to_v4(wb, d1b, wc, d1c, n1, p1_0, p1_1, p1_2, p1_3);
    if (n1 < 3)
    {
        return;
    }

    // Optional plane 2 clip (max 5 verts)
    float3 p2_0 = p1_0, p2_1 = p1_1, p2_2 = p1_2, p2_3 = p1_3, p2_4 = float3(0.0, 0.0, 0.0);
    int n2 = n1;
    if (use_p2)
    {
        p2_0 = float3(0.0, 0.0, 0.0);
        p2_1 = float3(0.0, 0.0, 0.0);
        p2_2 = float3(0.0, 0.0, 0.0);
        p2_3 = float3(0.0, 0.0, 0.0);
        n2 = 0;
        float3 prev = get_v4(n1 - 1, p1_0, p1_1, p1_2, p1_3);
        float prev_d = dot(prev, c6.xyz) - c6.w;
        for (int i = 0; i < 4; ++i)
        {
            if (i < n1)
            {
                float3 cur = get_v4(i, p1_0, p1_1, p1_2, p1_3);
                float cur_d = dot(cur, c6.xyz) - c6.w;
                clip_edge_to_v5(prev, prev_d, cur, cur_d, n2, p2_0, p2_1, p2_2, p2_3, p2_4);
                prev = cur;
                prev_d = cur_d;
            }
        }
    }
    if (n2 < 3)
    {
        return;
    }

    float4 q0 = project_world_pt(p2_0);
    float4 q1 = project_world_pt(p2_1);
    for (int k = 1; k < 4; ++k)
    {
        if (k < n2 - 1)
        {
            float3 vk = get_v5(k + 1, p2_0, p2_1, p2_2, p2_3, p2_4);
            float4 q2 = project_world_pt(vk);
            rasterize_subtri_inline(
                q0.x, q0.y, q0.z,
                q1.x, q1.y, q1.z,
                q2.x, q2.y, q2.z,
                w, h, allow_wire, is_vrm_u, true);
            q1 = q2;
        }
    }
}

[numthreads(8, 8, 1)]
void cs_rast_large_tiles(uint3 dtid : SV_DispatchThreadID)
{
    uint max_large = (uint)c12.z;
    uint n_large = min(g_meta_buf[0], max_large);
    int w = (int)c0.x;
    int h = (int)c0.y;
    int px = (int)dtid.x;
    int py = (int)dtid.y;
    if (n_large > 0u && px < w && py < h)
    {
        float cx = (float)px + 0.5;
        float cy = (float)py + 0.5;
        int pix_idx = py * w + px;

        VL_LOOP
        for (uint li = 0u; li < n_large; ++li)
        {
            uint base_i = li * 4u;
            float4 f3 = g_large_buf[base_i + 3u];
            int ix0 = (int)f3.x;
            int ix1 = (int)f3.y;
            int iy0 = (int)f3.z;
            int iy1 = (int)f3.w;
            if (px >= ix0 && px <= ix1 && py >= iy0 && py <= iy1)
            {
                float4 f0 = g_large_buf[base_i + 0u];
                float4 f1 = g_large_buf[base_i + 1u];
                float4 f2 = g_large_buf[base_i + 2u];
                uint flags_u = (uint)f1.w;
                uint is_vrm_u = flags_u & 1u;
                float wire_k = (float)((flags_u >> 1u) & 1u);

                float x0 = f0.x, y0 = f0.y, p0 = f0.z, area = f0.w;
                float x1 = f1.x, y1 = f1.y, p1 = f1.z;
                float x2 = f2.x, y2 = f2.y, p2 = f2.z;

                float dx12 = x1 - x2, dy12 = y1 - y2;
                float dx20 = x2 - x0, dy20 = y2 - y0;
                float dx01 = x0 - x1, dy01 = y0 - y1;
                float wire_eps = -0.02 * max(max(abs(dx12) + abs(dy12), abs(dx20) + abs(dy20)), 1.0);
                float eps = lerp(-1.0e-5, wire_eps, wire_k);

                float w0 = (cx - x1) * dy12 - (cy - y1) * dx12;
                float w1 = (cx - x2) * dy20 - (cy - y2) * dx20;
                float w2 = (cx - x0) * dy01 - (cy - y0) * dx01;
                if (w0 >= eps && w1 >= eps && w2 >= eps)
                {
                    float inv_a = 1.0 / area;
                    float b0 = w0 * inv_a;
                    float b1 = w1 * inv_a;
                    float pr = b0 * p0 + b1 * p1 + (1.0 - b0 - b1) * p2;
                    uint pv = pack_prox_val(pr, is_vrm_u);
                    uint prev_u;
                    InterlockedMax(g_prox_buf[pix_idx], pv, prev_u);
                }
            }
        }
    }
}

[numthreads(256, 1, 1)]
void cs_encode_rgba(uint3 dtid : SV_DispatchThreadID)
{
    uint idx = dtid.x;
    uint total_px = (uint)c12.w;
    if (idx >= total_px)
    {
        return;
    }

    uint val = g_prox_buf[idx];
    if (val == 0u)
    {
        g_rgba_buf[idx] = 0u;
        return;
    }

    uint prev_hit;
    InterlockedAdd(g_meta_buf[1], 1u, prev_hit);

    float pr = saturate((float)((val >> 1u) - 1u) * (1.0 / 16777214.0));
    float code = pr;
    if (c0.z >= 0.5)
    {
        code = pr * 0.48 + 0.01;
        if ((val & 1u) != 0u)
        {
            code = pr * 0.48 + 0.51;
        }
    }

    float r_f = saturate(code * 3.0 - 2.0);
    float g_f = saturate(code * 3.0 - 1.0);
    float b_f = saturate(code * 3.0);

    uint r_u = (uint)(r_f * 255.0 + 0.5);
    uint g_u = (uint)(g_f * 255.0 + 0.5);
    uint b_u = (uint)(b_f * 255.0 + 0.5);
    g_rgba_buf[idx] = (r_u & 0xFFu) | ((g_u & 0xFFu) << 8u) | ((b_u & 0xFFu) << 16u) | 0xFF000000u;
}
)HLSL";

}  // namespace vl3d
