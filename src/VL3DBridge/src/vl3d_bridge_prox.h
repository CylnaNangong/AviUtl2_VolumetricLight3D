#pragma once
//----------------------------------------------------------------------------------
//  VL3DBridge — prox 编码（相机遮挡深度 → RGBA8 的私定协议）
//----------------------------------------------------------------------------------
//  camocc 纹理是 RGBA8，浮点深度必须先压成 [0,1] 的 code，再摊到三个通道：
//
//      code = depth * kStep + (kBaseMesh | kBaseChar)
//      r = sat(code * kChan - kOffR)   g = sat(code * kChan - kOffG)
//      b = sat(code * kChan - kOffB)
//
//  三通道互为「移位拷贝」：任一 code 恰好有一条不饱和，三者和恒等于 3*code，
//  所以解码侧 raw = (r + g + b) / 3 能还原（误差只剩 8bit 量化 ≤ 0.5/255）。
//  code 落在哪个区间由「是不是 VRM 角色」决定，kFlag 是区间标志位：
//  网格 [0.01, 0.49]、角色 [0.51, 0.99]，两侧各留 0.01 保护带。
//
//  同一协议在四处各有一份实现，必须完全一致：
//      1. Lua    : src/VolumetricLight3D/modules/00a_consts.lua  (K.PROX)
//      2. C++    : 本文件（vl3d_bridge_rast.cpp 使用）
//      3. HLSL   : src/VolumetricLight3D/shaders/beamatlas.hlsl
//                  src/VolumetricLight3D/shaders/ps_vl_lfpack.hlsl
//      4. HLSL   : 本目录 vl3d_bridge_shaders.h 内嵌的 CS 源码
//  sandbox/checks/checks_09_raster.py 的 contract 用例会把四份数值对齐，
//  任何一处漂移都会让「遮挡距离整体偏移」而画面不一定报错，所以必须拦住。
//
//  注意：这里的 0.48 与 Lua 侧 19_render.lua 的 cam_comp = 0.48 只是同值，
//  语义无关（一个是编码斜率，一个是相机补偿）。
//----------------------------------------------------------------------------------

namespace vl3d {
namespace prox {

constexpr float kStep     = 0.48f;  // 归一化深度 → code 的斜率（区间总宽 0.49 - 0.01）
constexpr float kBaseMesh = 0.01f;  // 网格区间下限
constexpr float kBaseChar = 0.51f;  // 角色区间下限（= kFlag + 0.01）
constexpr float kFlag     = 0.50f;  // 解码侧判据：raw > kFlag 即角色
constexpr float kChan     = 3.0f;   // 通道展开系数
constexpr float kOffR     = 2.0f;   // r = kChan * code - kOffR
constexpr float kOffG     = 1.0f;   // g = kChan * code - kOffG
constexpr float kOffB     = 0.0f;   // b = kChan * code - kOffB

// 编译期把协议的两条不变量钉住（改数值改错会直接编译失败）：
//   1. 区间不能溢出 [0,1]，否则 8bit 饱和会吃掉深度；
//   2. 标志位两侧的保护带必须宽于 8bit 量化误差（三通道均值最坏 1/510）。
static_assert(kBaseMesh + kStep <= kFlag, "prox: mesh range crosses the character flag");
static_assert(kBaseChar - kFlag > 1.0f / 510.0f, "prox: char guard band below quantization error");
static_assert(kFlag - (kBaseMesh + kStep) > 1.0f / 510.0f,
              "prox: mesh guard band below quantization error");
static_assert(kBaseChar + kStep <= 1.0f, "prox: char range overflows 0..1");

}  // namespace prox
}  // namespace vl3d
