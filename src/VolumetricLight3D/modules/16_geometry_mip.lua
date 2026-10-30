local function vl_bf_geometry()
    -- 2) 光線ジオメトリ＆ワールド固定スライス格子計算（"object" が光源を持ったまま先に確定！）
    ------------------------------------------------------------
    VL_BF.f_dec= math.max(0.0, math.min(1.0, P.decay / 100.0))
    VL_BF.L_tot= math.max(1.0, P.length * (1.0 + 0.35 * VL_BF.f_dec))
    VL_BF.k_dec= (1.65 * VL_BF.f_dec * VL_BF.f_dec + 0.45 * VL_BF.f_dec) / math.max(P.length, 1.0)
    VL_BF.inv_end= 1.0 / math.max(VL_BF.L_tot * 0.22, 1.0)

    VL_BF.mode_kind= 0.0
    VL_BF.sx_p, VL_BF.sy_p, VL_BF.sz_p= 0.0, 0.0, 0.0
    VL_BF.u_wn, VL_BF.dS_wn= -1.0, 1.0

    VL_BF.mid_x, VL_BF.mid_y, VL_BF.mid_z= 0.0, 0.0, 0.0
    VL_BF.s_min, VL_BF.s_max, VL_BF.hw, VL_BF.hh, VL_BF.dz= 0.0, 1.0, 8.0, 8.0, 1.0
    VL_BF.total_sl= 64
    VL_BF.pts= {}
    VL_BF.pi= 0
    VL_BF.is_edit= (not VL_BF.has_exact_cam)
    VL_BF.sf_x, VL_BF.sf_y, VL_BF.sf_z= 0.0, 0.0, 1.0
    VL_BF.sr_x, VL_BF.sr_y, VL_BF.sr_z= 1.0, 0.0, 0.0
    VL_BF.sd_x, VL_BF.sd_y, VL_BF.sd_z= 0.0, 1.0, 0.0

    if VL_BF.use_cam_virtual_src and VL_BF.cam_light_info.primary ~= nil then
        local cl = VL_BF.cam_light_info.primary
        local lt_g = tonumber(cl.ltype) or 0
        local is_par_g = (lt_g == 1 or lt_g == 2)
        local is_spot_g = (lt_g == 4 or lt_g == 5)
        if is_par_g then
            local dl = math.max(1e-5, math.sqrt(cl.dx * cl.dx + cl.dy * cl.dy + cl.dz * cl.dz))
            local ux_c, uy_c, uz_c = -cl.dx / dl, -cl.dy / dl, -cl.dz / dl
            VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz = -ux_c, -uy_c, -uz_c
            VL_BF.wx = VL_BF.cam_x + VL_BF.f_x * 1200.0 - ux_c * (VL_BF.L_tot * 0.45)
            VL_BF.wy = VL_BF.cam_y + VL_BF.f_y * 1200.0 - uy_c * (VL_BF.L_tot * 0.45)
            VL_BF.wz = VL_BF.cam_z + VL_BF.f_z * 1200.0 - uz_c * (VL_BF.L_tot * 0.45)
            VL_BF.Hx, VL_BF.Hy = math.max(VL_BF.scr_w, VL_BF.scr_h) * 1.25, math.max(VL_BF.scr_w, VL_BF.scr_h) * 1.25
            VL_BF.L_char = 195.0
        else
            local dir_x, dir_y, dir_z
            if is_spot_g then
                local dl = math.max(1e-5, math.sqrt(cl.dx * cl.dx + cl.dy * cl.dy + cl.dz * cl.dz))
                dir_x, dir_y, dir_z = -cl.dx / dl, -cl.dy / dl, -cl.dz / dl
            else
                local tx_c = VL_BF.obj_x - cl.px
                local ty_c = VL_BF.obj_y - cl.py
                local tz_c = VL_BF.obj_z - cl.pz
                local tl = math.sqrt(tx_c * tx_c + ty_c * ty_c + tz_c * tz_c)
                if tl < 1.0 then tx_c, ty_c, tz_c, tl = 0.0, 0.0, -1.0, 1.0 end
                dir_x, dir_y, dir_z = tx_c / tl, ty_c / tl, tz_c / tl
            end
            VL_BF.Wnx, VL_BF.Wny, VL_BF.Wnz = -dir_x, -dir_y, -dir_z
            local ap_dist = 3.0
            VL_BF.cam_ap_dist = ap_dist
            local half_ang = is_spot_g
                and (VL_BF.cam_spot_eff_rad or 0.0)
                or math.rad(math.max(8.0, math.min(78.0, math.abs(P.spread) > 1.0 and math.abs(P.spread) or 48.0)))
            local clamped_ang = math.max(1e-4, math.min(math.rad(86.5), half_ang))
            local ap_half = math.max(0.02, ap_dist * math.tan(clamped_ang))
            local ap_pad = ap_half
            if is_spot_g then
                local pad_ang = math.min(math.rad(87.5), clamped_ang + math.rad(1.5))
                ap_pad = math.max(ap_half * 1.12, ap_dist * math.tan(pad_ang))
            end
            VL_BF.wx = cl.px + dir_x * ap_dist
            VL_BF.wy = cl.py + dir_y * ap_dist
            VL_BF.wz = cl.pz + dir_z * ap_dist
            VL_BF.Hx, VL_BF.Hy = ap_pad, ap_pad
            local ref_r = math.min(520.0, 600.0 * math.tan(math.min(1.15, clamped_ang)))
            VL_BF.L_char = math.min(200.0, math.max(22.0, ref_r * 0.32))
        end
        local upx, upy, upz = 0.0, 1.0, 0.0
        if math.abs(VL_BF.Wny) > 0.92 then upx, upy, upz = 1.0, 0.0, 0.0 end
        VL_BF.Wrx = upy * VL_BF.Wnz - upz * VL_BF.Wny
        VL_BF.Wry = upz * VL_BF.Wnx - upx * VL_BF.Wnz
        VL_BF.Wrz = upx * VL_BF.Wny - upy * VL_BF.Wnx
        local rl_w = math.max(1e-6, math.sqrt(VL_BF.Wrx * VL_BF.Wrx + VL_BF.Wry * VL_BF.Wry + VL_BF.Wrz * VL_BF.Wrz))
        VL_BF.Wrx, VL_BF.Wry, VL_BF.Wrz = VL_BF.Wrx / rl_w, VL_BF.Wry / rl_w, VL_BF.Wrz / rl_w
        VL_BF.Wdx = VL_BF.Wny * VL_BF.Wrz - VL_BF.Wnz * VL_BF.Wry
        VL_BF.Wdy = VL_BF.Wnz * VL_BF.Wrx - VL_BF.Wnx * VL_BF.Wrz
        VL_BF.Wdz = VL_BF.Wnx * VL_BF.Wry - VL_BF.Wny * VL_BF.Wrx
    end

    do
        if P.cam_light_mode == 1 and VL_BF.cam_light_info.primary ~= nil then
            local cl = VL_BF.cam_light_info.primary
            local lt_m = tonumber(cl.ltype) or 0
            if lt_m == 1 or lt_m == 2 then
                local dl = math.max(1e-5, math.sqrt(cl.dx * cl.dx + cl.dy * cl.dy + cl.dz * cl.dz))
                local ux_c, uy_c, uz_c = -cl.dx / dl, -cl.dy / dl, -cl.dz / dl
                if (ux_c * (-VL_BF.Wnx) + uy_c * (-VL_BF.Wny) + uz_c * (-VL_BF.Wnz)) < 0.0 then
                    ux_c, uy_c, uz_c = -ux_c, -uy_c, -uz_c
                end
                VL_BF.mode_kind = 0.0
                VL_BF.sx_p, VL_BF.sy_p, VL_BF.sz_p = ux_c, uy_c, uz_c
                VL_BF.u_wn = ux_c * VL_BF.Wnx + uy_c * VL_BF.Wny + uz_c * VL_BF.Wnz
                if math.abs(VL_BF.u_wn) < 1e-3 then VL_BF.u_wn = (VL_BF.u_wn >= 0) and 1e-3 or -1e-3 end
            else
                VL_BF.sx_p, VL_BF.sy_p, VL_BF.sz_p = cl.px, cl.py, cl.pz
                VL_BF.dS_wn = (VL_BF.wx - VL_BF.sx_p) * VL_BF.Wnx + (VL_BF.wy - VL_BF.sy_p) * VL_BF.Wny + (VL_BF.wz - VL_BF.sz_p) * VL_BF.Wnz
                if math.abs(VL_BF.dS_wn) < 1.0 then VL_BF.dS_wn = (VL_BF.dS_wn >= 0.0) and 1.0 or -1.0 end
                VL_BF.mode_kind = 1.0
            end
        elseif P.ray_mode == 0 then
            local dir_deg = P.direction
            if P.screen_dir == true or P.screen_dir == 1 then
                dir_deg = dir_deg - VL_BF.wrz
            end
            local th = math.rad(dir_deg)
            local ph_deg = math.max(-89.5, math.min(89.5, P.pitch))
            if math.abs(ph_deg) < 2.0 then
                ph_deg = (ph_deg >= 0) and 2.0 or -2.0
            end
            local ph = math.rad(ph_deg)
            local lx_d = math.cos(ph) * math.cos(th)
            local ly_d = math.cos(ph) * math.sin(th)
            local lz_d = -math.sin(ph)
            local ux = VL_BF.Wrx * lx_d + VL_BF.Wdx * ly_d + VL_BF.Wnx * lz_d
            local uy = VL_BF.Wry * lx_d + VL_BF.Wdy * ly_d + VL_BF.Wny * lz_d
            local uz = VL_BF.Wrz * lx_d + VL_BF.Wdz * ly_d + VL_BF.Wnz * lz_d
            local ul = math.sqrt(ux * ux + uy * uy + uz * uz)
            if ul > 1e-6 then ux, uy, uz = ux / ul, uy / ul, uz / ul end

            local sp_deg = math.max(-165.0, math.min(165.0, P.spread))
            if math.abs(sp_deg) < 0.25 then
                VL_BF.mode_kind = 0.0
                VL_BF.sx_p, VL_BF.sy_p, VL_BF.sz_p = ux, uy, uz
                VL_BF.u_wn = ux * VL_BF.Wnx + uy * VL_BF.Wny + uz * VL_BF.Wnz
                if math.abs(VL_BF.u_wn) < 1e-3 then VL_BF.u_wn = (VL_BF.u_wn >= 0) and 1e-3 or -1e-3 end
            else
                local ka = math.tan(math.rad(sp_deg) * 0.5) / VL_BF.R_fixed
                local r0 = 1.0 / ka
                local cwx = VL_BF.wx + VL_BF.Wrx * VL_BF.cx_sh + VL_BF.Wdx * VL_BF.cy_sh
                local cwy = VL_BF.wy + VL_BF.Wry * VL_BF.cx_sh + VL_BF.Wdy * VL_BF.cy_sh
                local cwz = VL_BF.wz + VL_BF.Wrz * VL_BF.cx_sh + VL_BF.Wdz * VL_BF.cy_sh
                VL_BF.sx_p = cwx - ux * r0
                VL_BF.sy_p = cwy - uy * r0
                VL_BF.sz_p = cwz - uz * r0
                VL_BF.dS_wn = (VL_BF.wx - VL_BF.sx_p) * VL_BF.Wnx + (VL_BF.wy - VL_BF.sy_p) * VL_BF.Wny + (VL_BF.wz - VL_BF.sz_p) * VL_BF.Wnz
                VL_BF.mode_kind = (sp_deg > 0) and 1.0 or 2.0
            end
        else
            local lz_src = math.max(80.0, P.light_z)
            if P.ray_mode == 2 then lz_src = -lz_src end
            VL_BF.sx_p = VL_BF.wx + VL_BF.Wrx * P.light_x + VL_BF.Wdx * P.light_y + VL_BF.Wnx * lz_src
            VL_BF.sy_p = VL_BF.wy + VL_BF.Wry * P.light_x + VL_BF.Wdy * P.light_y + VL_BF.Wny * lz_src
            VL_BF.sz_p = VL_BF.wz + VL_BF.Wrz * P.light_x + VL_BF.Wdz * P.light_y + VL_BF.Wnz * lz_src
            VL_BF.dS_wn = (VL_BF.wx - VL_BF.sx_p) * VL_BF.Wnx + (VL_BF.wy - VL_BF.sy_p) * VL_BF.Wny + (VL_BF.wz - VL_BF.sz_p) * VL_BF.Wnz
            VL_BF.mode_kind = (P.ray_mode == 1) and 1.0 or 2.0
        end

        local GRID_N = 6
        for iu = 0, GRID_N do
            local uu = VL_BF.u0_s + (VL_BF.u1_s - VL_BF.u0_s) * (iu / GRID_N)
            local ox_l = (uu - 0.5) * 2.0 * VL_BF.Hx
            for iv = 0, GRID_N do
                local vv = VL_BF.v0_s + (VL_BF.v1_s - VL_BF.v0_s) * (iv / GRID_N)
                local oy_l = (vv - 0.5) * 2.0 * VL_BF.Hy
                local qx = VL_BF.wx + VL_BF.Wrx * ox_l + VL_BF.Wdx * oy_l
                local qy = VL_BF.wy + VL_BF.Wry * ox_l + VL_BF.Wdy * oy_l
                local qz = VL_BF.wz + VL_BF.Wrz * ox_l + VL_BF.Wdz * oy_l
                VL_BF.pi = VL_BF.pi + 1; VL_BF.pts[VL_BF.pi] = { qx, qy, qz }

                local ex, ey, ez
                if VL_BF.mode_kind < 0.5 then
                    ex, ey, ez = qx + VL_BF.sx_p * VL_BF.L_tot, qy + VL_BF.sy_p * VL_BF.L_tot, qz + VL_BF.sz_p * VL_BF.L_tot
                else
                    local dvx, dvy, dvz = qx - VL_BF.sx_p, qy - VL_BF.sy_p, qz - VL_BF.sz_p
                    local dvl = math.max(1.0, math.sqrt(dvx * dvx + dvy * dvz * 0.0 + dvy * dvy + dvz * dvz))
                    dvx, dvy, dvz = dvx / dvl, dvy / dvl, dvz / dvl
                    if VL_BF.mode_kind < 1.5 then
                        ex, ey, ez = qx + dvx * VL_BF.L_tot, qy + dvy * VL_BF.L_tot, qz + dvz * VL_BF.L_tot
                    else
                        ex, ey, ez = qx - dvx * VL_BF.L_tot, qy - dvy * VL_BF.L_tot, qz - dvz * VL_BF.L_tot
                    end
                end
                VL_BF.pi = VL_BF.pi + 1; VL_BF.pts[VL_BF.pi] = { ex, ey, ez }
            end
        end

        if not VL_BF.is_edit then
            -- カメラ視点・動画出力時：視線直交スライス基底（360°軸切替ポップ完全ゼロ＋近平面・視錐台クランプで解像度約 2 倍！）
            VL_BF.sf_x, VL_BF.sf_y, VL_BF.sf_z = VL_BF.f_x, VL_BF.f_y, VL_BF.f_z
            VL_BF.sr_x, VL_BF.sr_y, VL_BF.sr_z = VL_BF.r_x, VL_BF.r_y, VL_BF.r_z
            VL_BF.sd_x, VL_BF.sd_y, VL_BF.sd_z = VL_BF.d_x, VL_BF.d_y, VL_BF.d_z

            local zc_min, zc_max = 1e9, -1e9
            for i = 1, VL_BF.pi do
                local dxp = VL_BF.pts[i][1] - VL_BF.cam_x
                local dyp = VL_BF.pts[i][2] - VL_BF.cam_y
                local dzp = VL_BF.pts[i][3] - VL_BF.cam_z
                local zcv = dxp * VL_BF.f_x + dyp * VL_BF.f_y + dzp * VL_BF.f_z
                local rvv = math.abs(dxp * VL_BF.r_x + dyp * VL_BF.r_y + dzp * VL_BF.r_z)
                local dvv = math.abs(dxp * VL_BF.d_x + dyp * VL_BF.d_y + dzp * VL_BF.d_z)
                if zcv < zc_min then zc_min = zcv end
                if zcv > zc_max then zc_max = zcv end
                if rvv > VL_BF.hw then VL_BF.hw = rvv end
                if dvv > VL_BF.hh then VL_BF.hh = dvv end
            end

            -- カメラ背面の無駄なスライスをカットし、全スライスをカメラ前方の可視体積に集中配置
            local near_z = 16.0
            if zc_max < near_z + 8.0 then zc_max = near_z + 8.0 end
            if zc_min < near_z then zc_min = near_z end

            local zc_mid = 0.5 * (zc_min + zc_max)
            VL_BF.mid_x = VL_BF.cam_x + VL_BF.f_x * zc_mid
            VL_BF.mid_y = VL_BF.cam_y + VL_BF.f_y * zc_mid
            VL_BF.mid_z = VL_BF.cam_z + VL_BF.f_z * zc_mid

            local span0 = math.max(1.0, zc_max - zc_min)
            local est_sl = (P.auto_slice == true or P.auto_slice == 1)
                and math.min(192, math.max(64, math.ceil(span0 / 16.4)))
                or math.min(64, math.max(16, math.floor(P.slices + 0.5)))
            local dz0 = math.max(1.0, span0 / est_sl)
            local pad_s = 2.6 * dz0 + 24.0
            local zc0 = math.max(near_z * 0.5, zc_min - pad_s)
            local zc1 = zc_max + pad_s
            VL_BF.s_min = zc0 - zc_mid
            VL_BF.s_max = zc1 - zc_mid

            if P.auto_slice == true or P.auto_slice == 1 then
                VL_BF.total_sl = math.min(192, math.max(64, math.ceil(math.max(1.0, VL_BF.s_max - VL_BF.s_min) / 16.4)))
            else
                VL_BF.total_sl = math.min(64, math.max(16, math.floor(P.slices + 0.5)))
            end
            VL_BF.dz = math.max(1.0, (VL_BF.s_max - VL_BF.s_min) / VL_BF.total_sl)

            -- 透視スラブシフト余裕を加算した上で、最遠スラブでのカメラ視錐台幅にタイトクランプ
            local lat_margin = 40.0 + 0.01 * VL_BF.L_tot
            VL_BF.hw = VL_BF.hw + lat_margin
            VL_BF.hh = VL_BF.hh + lat_margin
            local tan_x = (0.5 * VL_BF.scr_w) / math.max(VL_BF.focal_d, 32.0)
            local tan_y = (0.5 * VL_BF.scr_h) / math.max(VL_BF.focal_d, 32.0)
            local max_frust_w = zc1 * tan_x + 64.0
            local max_frust_h = zc1 * tan_y + 64.0
            if VL_BF.hw > max_frust_w then VL_BF.hw = math.max(16.0, max_frust_w) end
            if VL_BF.hh > max_frust_h then VL_BF.hh = math.max(16.0, max_frust_h) end
        else
            -- 編集用視点モード（cam_mode == 2）：ワールド／光源 3 軸基底を維持
            local d_fn = VL_BF.f_x * (-VL_BF.Wnx) + VL_BF.f_y * (-VL_BF.Wny) + VL_BF.f_z * (-VL_BF.Wnz)
            local d_fr = VL_BF.f_x * VL_BF.Wrx + VL_BF.f_y * VL_BF.Wry + VL_BF.f_z * VL_BF.Wrz
            if P.view_mode == 1 then
                local sgn = ((VL_BF.cam_x - VL_BF.wx) * VL_BF.Wrx + (VL_BF.cam_y - VL_BF.wy) * VL_BF.Wry + (VL_BF.cam_z - VL_BF.wz) * VL_BF.Wrz >= 0.0) and -1.0 or 1.0
                VL_BF.sf_x, VL_BF.sf_y, VL_BF.sf_z = VL_BF.Wrx * sgn, VL_BF.Wry * sgn, VL_BF.Wrz * sgn
                VL_BF.sr_x, VL_BF.sr_y, VL_BF.sr_z = VL_BF.Wnx * sgn, VL_BF.Wny * sgn, VL_BF.Wnz * sgn
                VL_BF.sd_x, VL_BF.sd_y, VL_BF.sd_z = VL_BF.Wdx, VL_BF.Wdy, VL_BF.Wdz
            else
                VL_BF.sf_x, VL_BF.sf_y, VL_BF.sf_z = -VL_BF.Wnx, -VL_BF.Wny, -VL_BF.Wnz
                VL_BF.sr_x, VL_BF.sr_y, VL_BF.sr_z = VL_BF.Wrx, VL_BF.Wry, VL_BF.Wrz
                VL_BF.sd_x, VL_BF.sd_y, VL_BF.sd_z = VL_BF.Wdx, VL_BF.Wdy, VL_BF.Wdz
            end

            local bmin_x, bmax_x = 1e9, -1e9
            local bmin_y, bmax_y = 1e9, -1e9
            local bmin_z, bmax_z = 1e9, -1e9
            for i = 1, VL_BF.pi do
                local p = VL_BF.pts[i]
                if p[1] < bmin_x then bmin_x = p[1] end; if p[1] > bmax_x then bmax_x = p[1] end
                if p[2] < bmin_y then bmin_y = p[2] end; if p[2] > bmax_y then bmax_y = p[2] end
                if p[3] < bmin_z then bmin_z = p[3] end; if p[3] > bmax_z then bmax_z = p[3] end
            end
            VL_BF.mid_x = 0.5 * (bmin_x + bmax_x)
            VL_BF.mid_y = 0.5 * (bmin_y + bmax_y)
            VL_BF.mid_z = 0.5 * (bmin_z + bmax_z)

            local s_min0, s_max0 = 1e9, -1e9
            for i = 1, VL_BF.pi do
                local rxp, ryp, rzp = VL_BF.pts[i][1] - VL_BF.mid_x, VL_BF.pts[i][2] - VL_BF.mid_y, VL_BF.pts[i][3] - VL_BF.mid_z
                local sv = rxp * VL_BF.sf_x + ryp * VL_BF.sf_y + rzp * VL_BF.sf_z
                local rv = math.abs(rxp * VL_BF.sr_x + ryp * VL_BF.sr_y + rzp * VL_BF.sr_z)
                local dv = math.abs(rxp * VL_BF.sd_x + ryp * VL_BF.sd_y + rzp * VL_BF.sd_z)
                if sv < s_min0 then s_min0 = sv end
                if sv > s_max0 then s_max0 = sv end
                if rv > VL_BF.hw then VL_BF.hw = rv end
                if dv > VL_BF.hh then VL_BF.hh = dv end
            end

            local span0 = math.max(1.0, s_max0 - s_min0)
            local est_sl = (P.auto_slice == true or P.auto_slice == 1)
                and math.min(192, math.max(64, math.ceil(span0 / 16.4)))
                or math.min(64, math.max(16, math.floor(P.slices + 0.5)))
            local dz0 = math.max(1.0, span0 / est_sl)
            local pad_s = 2.6 * dz0 + 0.012 * VL_BF.L_tot + 36.0
            VL_BF.s_min = s_min0 - pad_s
            VL_BF.s_max = s_max0 + pad_s

            if P.auto_slice == true or P.auto_slice == 1 then
                VL_BF.total_sl = math.min(192, math.max(64, math.ceil(math.max(1.0, VL_BF.s_max - VL_BF.s_min) / 16.4)))
            else
                VL_BF.total_sl = math.min(64, math.max(16, math.floor(P.slices + 0.5)))
            end
            VL_BF.dz = math.max(1.0, (VL_BF.s_max - VL_BF.s_min) / VL_BF.total_sl)
            if (VL_BF.s_max - s_max0) < 2.5 * VL_BF.dz then
                local extra_s = 2.5 * VL_BF.dz - (VL_BF.s_max - s_max0) + 8.0
                VL_BF.s_min = VL_BF.s_min - extra_s
                VL_BF.s_max = VL_BF.s_max + extra_s
                VL_BF.dz = math.max(1.0, (VL_BF.s_max - VL_BF.s_min) / VL_BF.total_sl)
            end
            local lat_margin = 40.0 + 0.01 * VL_BF.L_tot
            VL_BF.hw = math.min(VL_BF.hw + lat_margin, 4200.0)
            VL_BF.hh = math.min(VL_BF.hh + lat_margin, 4200.0)
        end
    end

    VL_BF.use_source= 1.0
    VL_BF.lc_r, VL_BF.lc_g, VL_BF.lc_b= 1.0, 1.0, 1.0
    if type(P.light_color) == "number" then
        VL_BF.use_source = 0.0
        VL_BF.lc_r = math.floor(P.light_color / 0x10000) % 0x100 / 255.0
        VL_BF.lc_g = math.floor(P.light_color / 0x100) % 0x100 / 255.0
        VL_BF.lc_b = (P.light_color % 0x100) / 255.0
    elseif P.cam_light_mode == 1 and VL_BF.cam_light_info.primary ~= nil and VL_BF.cam_light_info.primary.lum > 1e-4 then
        local cl_t = VL_BF.cam_light_info.primary
        VL_BF.lc_r = math.min(1.0, cl_t.r / cl_t.lum)
        VL_BF.lc_g = math.min(1.0, cl_t.g / cl_t.lum)
        VL_BF.lc_b = math.min(1.0, cl_t.b / cl_t.lum)
    end

    ------------------------------------------------------------
end

local function vl_bf_mip()
    -- 3) "object" から直接 cache:vl3d/mip を生成し、中間バッファを即座に 1x1 解放！
    --    （この時点で生存する cache は <= 640x640 = 40.9 万ドットのみ！）
    ------------------------------------------------------------
    VL_BF.mip_ready= false
    VL_BF.mip_dots= 0
    VL_BF.peak_cache_dots= 0
    pcall(function()
        local kf_p = math.max(1, math.ceil(math.max(VL_BF.lw, VL_BF.lh) / 640.0))
        local pw = math.max(2, math.ceil(VL_BF.lw / kf_p))
        local ph = math.max(2, math.ceil(VL_BF.lh / kf_p))
        local prep_r, prep_g, prep_b = VL_BF.lc_r, VL_BF.lc_g, VL_BF.lc_b
        local prep_use_src = VL_BF.use_source
        local prep_thr = VL_BF.thr_norm
        if VL_BF.use_cam_virtual_src then
            prep_r, prep_g, prep_b = 1.0, 1.0, 1.0
            prep_use_src = 0.0
            prep_thr = 0.0
        end
        obj.clearbuffer("cache:vl3d/prep", pw, ph)
        obj.pixelshader("ps_vl_prep", "cache:vl3d/prep", { "object" },
            { VL_BF.lw, VL_BF.lh, kf_p, prep_thr, prep_r, prep_g, prep_b, prep_use_src }, "copy")

        local world_sigma = math.max(2.0, 0.56 * VL_BF.dz)
        local span_2x = 2.0 * VL_BF.Hx
        local span_2y = 2.0 * VL_BF.Hy
        if VL_BF.use_cam_virtual_src and VL_BF.cam_ap_dist ~= nil and VL_BF.cam_ap_dist > 0.0 then
            local ref_mul = 600.0 / VL_BF.cam_ap_dist
            span_2x = math.max(32.0, span_2x * ref_mul)
            span_2y = math.max(32.0, span_2y * ref_mul)
        end
        local sig_x = world_sigma * (pw / math.max(span_2x, 1.0))
        local sig_y = world_sigma * (ph / math.max(span_2y, 1.0))
        local rad_x = math.min(20, math.ceil(sig_x * 2.5))
        local rad_y = math.min(20, math.ceil(sig_y * 2.5))

        obj.clearbuffer("cache:vl3d/tmp", pw, ph)
        obj.pixelshader("ps_vl_blur", "cache:vl3d/tmp", { "cache:vl3d/prep" },
            { pw, ph, 1.0, 0.0, sig_x, rad_x }, "copy")
        obj.clearbuffer("cache:vl3d/prep", 1, 1)

        obj.clearbuffer("cache:vl3d/mip", pw, ph)
        obj.pixelshader("ps_vl_blur", "cache:vl3d/mip", { "cache:vl3d/tmp" },
            { pw, ph, 0.0, 1.0, sig_y, rad_y }, "copy")
        obj.clearbuffer("cache:vl3d/tmp", 1, 1)

        VL_BF.mip_dots = pw * ph
        VL_BF.peak_cache_dots = VL_BF.mip_dots * 2
        VL_BF.mip_ready = true
        pcall(function() obj.clearbuffer("object", 4, 4) end)
    end)

    ------------------------------------------------------------
end

