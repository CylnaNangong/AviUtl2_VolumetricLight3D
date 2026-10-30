            -- 体積光（3D）連動: この粒子が光束の内側なら受光色と受光量を得る
            --   座標は「オブジェクトのローカル → ワールド」へ戻してから評価
            --   （soft には粒子半径を渡し、光束の縁を粒子サイズでぼかす）
            local lf_r, lf_g, lf_b, lf_lit = 0, 0, 0, 0
            if SNOW_LF.on and SNOW_LF.n > 0 then
                lf_r, lf_g, lf_b, lf_lit = lf_sample(
                    p_x * ozm + ox0, p_y * ozm + oy0, p_z * ozm + oz0, sz * ozm * 0.5)
            end
