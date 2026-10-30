local _edit_cam_probe = _VL_PERSIST.edit_cam_probe

local function try_read_live_edit_camera()
    if type(_G.VL3D_TEST_EDIT_CAM) == "table" then
        return _G.VL3D_TEST_EDIT_CAM
    end
    local br_c = vl3d_bridge()
    if br_c ~= nil and type(br_c.get_edit_camera) == "function" then
        local ok_c, okcam, x, y, z, tx, ty, tz, ux, uy, uz, rz, d = pcall(br_c.get_edit_camera)
        if ok_c and okcam == true then
            return { x = x, y = y, z = z, tx = tx, ty = ty, tz = tz, ux = ux, uy = uy, uz = uz, rz = rz, d = d }
        end
    end
    local ok_ffi, ffi = pcall(require, "ffi")
    if not (ok_ffi and ffi ~= nil and ffi.os == "Windows" and ffi.arch == "x64") then
        return nil
    end
    local cam_out = nil
    pcall(function()
        local wapi = get_w32_api()
        if wapi == nil or wapi.GetModuleHandleW == nil or wapi.VirtualQuery == nil then
            return
        end
        if _VL_PERSIST.edit_cam_mbi == nil then
            _VL_PERSIST.edit_cam_mbi = ffi.new("uint8_t[64]")
        end
        local mbi_ec = _VL_PERSIST.edit_cam_mbi
        local function is_readable_ptr(addr, min_bytes)
            if addr == 0 or addr < 0x10000 then return false end
            local q = wapi.VirtualQuery(ffi.cast("const void*", addr), mbi_ec, 48)
            if q < 48 then return false end
            local region_size = tonumber(ffi.cast("const uint64_t*", mbi_ec + 24)[0]) or 0
            local base_addr   = tonumber(ffi.cast("const uint64_t*", mbi_ec)[0]) or 0
            local state       = ffi.cast("const uint32_t*", mbi_ec + 32)[0]
            local protect     = ffi.cast("const uint32_t*", mbi_ec + 36)[0]
            if state ~= 0x1000 then return false end
            local pmask = protect % 256
            if not (pmask == 2 or pmask == 4 or pmask == 8 or pmask == 0x20 or pmask == 0x40) then
                return false
            end
            return (addr >= base_addr) and (addr + min_bytes <= base_addr + region_size)
        end

        if not _edit_cam_probe.init then
            _edit_cam_probe.init = true
            local base = tonumber(wapi.GetModuleHandleW(nil)) or 0
            if is_readable_ptr(base, 4096) then
                local b = ffi.cast("const uint8_t*", base)
                if b[0] == 0x4D and b[1] == 0x5A then
                    local pe_off = ffi.cast("const uint32_t*", b + 0x3C)[0]
                    if pe_off > 64 and pe_off < 2048 and b[pe_off] == 0x50 and b[pe_off + 1] == 0x45 then
                        local nsec   = ffi.cast("const uint16_t*", b + pe_off + 6)[0]
                        local opt_sz = tonumber(ffi.cast("const uint16_t*", b + pe_off + 20)[0]) or 0
                        local sec_ok = (opt_sz >= 0x60 and opt_sz <= 1024 and (pe_off + 24 + opt_sz + math.min(tonumber(nsec) or 0, 12) * 40) <= 4096)
                        local sec_p  = sec_ok and (b + pe_off + 24 + opt_sz) or b
                        for si = 0, (sec_ok and math.min(nsec, 12) or 0) - 1 do
                            local sh = sec_p + si * 40
                            if sh[0] == 0x2E and sh[1] == 0x74 and sh[2] == 0x65 and sh[3] == 0x78 and sh[4] == 0x74 then
                                local vsz = tonumber(ffi.cast("const uint32_t*", sh + 8)[0]) or 0
                                local rva = tonumber(ffi.cast("const uint32_t*", sh + 12)[0]) or 0
                                if vsz > 64 and vsz < 16777216 and is_readable_ptr(base + rva, vsz) then
                                    local code = b + rva
                                    for i = 0, vsz - 31 do
                                        if code[i] == 0x48 and code[i + 1] == 0x8B and code[i + 2] == 0x05
                                            and code[i + 7] == 0x4C and code[i + 8] == 0x8B and code[i + 9] == 0x80
                                            and code[i + 14] == 0x49 and code[i + 15] == 0x81 and code[i + 16] == 0xC0
                                            and code[i + 21] == 0xEB and code[i + 22] == 0x07
                                            and code[i + 23] == 0x4C and code[i + 24] == 0x8D and code[i + 25] == 0x87 then
                                            local disp_g = tonumber(ffi.cast("const int32_t*", code + i + 3)[0])
                                            local off_w  = tonumber(ffi.cast("const uint32_t*", code + i + 10)[0])
                                            local off_c  = tonumber(ffi.cast("const uint32_t*", code + i + 17)[0])
                                            _edit_cam_probe.g_addr = base + rva + i + 7 + disp_g
                                            _edit_cam_probe.off_w  = off_w
                                            _edit_cam_probe.off_c  = off_c
                                            _edit_cam_probe.ok     = true
                                            break
                                        end
                                    end
                                end
                                break
                            end
                        end
                    end
                end
            end
        end

        if _edit_cam_probe.ok and is_readable_ptr(_edit_cam_probe.g_addr, 8) then
            local app_ptr = tonumber(ffi.cast("const uint64_t*", _edit_cam_probe.g_addr)[0]) or 0
            if is_readable_ptr(app_ptr + _edit_cam_probe.off_w, 8) then
                local win_ptr = tonumber(ffi.cast("const uint64_t*", app_ptr + _edit_cam_probe.off_w)[0]) or 0
                local cam_ptr = win_ptr + _edit_cam_probe.off_c
                if is_readable_ptr(cam_ptr, 56) then
                    local fp = ffi.cast("const float*", cam_ptr)
                    local fd = fp[13]
                    if fd == fd and fd > 1.0 and fd < 1.0e7 then
                        cam_out = {
                            x = fp[0], y = fp[1], z = fp[2],
                            tx = fp[4], ty = fp[5], tz = fp[6],
                            ux = -fp[8], uy = -fp[9], uz = -fp[10],
                            rz = fp[12], d = fd,
                        }
                    end
                end
            end
        end
    end)
    return cam_out
end

local function get_camera_basis()
    local cam_mode = 1
    pcall(function()
        local cm = obj.getoption("camera_mode")
        if type(cm) == "number" then cam_mode = cm end
    end)

    local cam = nil
    pcall(function() cam = obj.getoption("camera_param") end)

    local has_exact_cam = (cam_mode ~= 2)
    if cam_mode == 2 then
        local ecam = try_read_live_edit_camera()
        if ecam ~= nil then
            cam = ecam
            has_exact_cam = true
        end
    end

    local cam_x, cam_y, cam_z = 0.0, 0.0, -1024.0
    local focal_d = 1024.0
    local f_x, f_y, f_z = 0.0, 0.0, 1.0
    local r_x, r_y, r_z = 1.0, 0.0, 0.0
    local d_x, d_y, d_z = 0.0, 1.0, 0.0

    if cam ~= nil then
        cam_x = cam.x or 0.0
        cam_y = cam.y or 0.0
        cam_z = cam.z or -1024.0
        if type(cam.d) == "number" and cam.d > 1.0 then focal_d = cam.d end
        local vx = (cam.tx or 0.0) - cam_x
        local vy = (cam.ty or 0.0) - cam_y
        local vz = (cam.tz or 0.0) - cam_z
        local vl = math.sqrt(vx * vx + vy * vy + vz * vz)
        if vl > 1e-6 then f_x, f_y, f_z = vx / vl, vy / vl, vz / vl end

        local up_x, up_y, up_z = 0.0, -1.0, 0.0
        if type(cam.ux) == "number" then up_x, up_y, up_z = cam.ux, cam.uy, cam.uz end
        if up_x * up_x + up_y * up_y + up_z * up_z < 1e-9 then up_x, up_y, up_z = 0.0, -1.0, 0.0 end

        r_x = f_y * up_z - f_z * up_y
        r_y = f_z * up_x - f_x * up_z
        r_z = f_x * up_y - f_y * up_x
        local rl = math.sqrt(r_x * r_x + r_y * r_y + r_z * r_z)
        if rl > 1e-6 then r_x, r_y, r_z = r_x / rl, r_y / rl, r_z / rl else r_x, r_y, r_z = 1, 0, 0 end

        d_x = f_y * r_z - f_z * r_y
        d_y = f_z * r_x - f_x * r_z
        d_z = f_x * r_y - f_y * r_x
        local dl = math.sqrt(d_x * d_x + d_y * d_y + d_z * d_z)
        if dl > 1e-6 then d_x, d_y, d_z = d_x / dl, d_y / dl, d_z / dl end

        if type(cam.rz) == "number" and math.abs(cam.rz) > 1e-5 then
            local cr = math.cos(math.rad(cam.rz))
            local sr = math.sin(math.rad(cam.rz))
            local nr_x, nr_y, nr_z = r_x * cr - d_x * sr, r_y * cr - d_y * sr, r_z * cr - d_z * sr
            local nd_x, nd_y, nd_z = r_x * sr + d_x * cr, r_y * sr + d_y * cr, r_z * sr + d_z * cr
            r_x, r_y, r_z = nr_x, nr_y, nr_z
            d_x, d_y, d_z = nd_x, nd_y, nd_z
        end
    end
    return cam_mode, has_exact_cam, cam_x, cam_y, cam_z, focal_d, f_x, f_y, f_z, r_x, r_y, r_z, d_x, d_y, d_z
end
