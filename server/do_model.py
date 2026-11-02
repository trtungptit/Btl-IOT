"""Mô hình ước tính oxy hoà tan (DO) trong thùng nuôi cá.

DO ở đây là giá trị ƯỚC TÍNH (không có cảm biến DO).

    DO(t+1) = DO(t) + k_sục × (DO_bão_hoà(T) − DO(t)) − R_cá_ảo(T)

Các hệ số (k_sục, tiêu thụ của cá) không viết cứng ở đây: truyền vào qua
`params`, lấy từ mục "do_model" trong config/config.json.
"""

import math


def do_sat(temp_c):
    """DO bão hoà của nước ngọt (mg/L) ở nhiệt độ temp_c (°C), áp suất 1 atm.

    Công thức Benson-Krause (1984). Nhiệt độ phải đổi sang Kelvin.
    """
    t = temp_c + 273.15
    ln_do = (
        -139.34411
        + 1.575701e5 / t
        - 6.642308e7 / t**2
        + 1.243800e10 / t**3
        - 8.621949e11 / t**4
    )
    return math.exp(ln_do)


def fish_uptake_per_s(temp_c, fish_count, params):
    """Cá ảo tiêu thụ bao nhiêu oxy mỗi giây (mg/L/s).

    Nước càng ấm cá càng tiêu thụ nhiều: cứ tăng 10 °C thì nhân với q10.
    """
    he_so_nhiet = params["fish_uptake_q10"] ** (
        (temp_c - params["fish_uptake_ref_temp_c"]) / 10
    )
    return fish_count * params["fish_uptake_mg_per_l_per_s"] * he_so_nhiet


def step(do, temp_c, aerator_on, fish_count, dt_s, params):
    """Tính DO mới (mg/L) sau dt_s giây.

    - do: DO hiện tại (mg/L)
    - temp_c: nhiệt độ nước (°C)
    - aerator_on: True nếu máy sục khí đang chạy
    - fish_count: số cá ảo
    - dt_s: khoảng thời gian của một bước (giây)
    - params: dict lấy từ config["do_model"]
    """
    bao_hoa = do_sat(temp_c)

    # Sục khí đẩy DO về phía mức bão hoà. Tắt máy thì không có phần này.
    tang = 0.0
    if aerator_on:
        # min(..., 1) để dt_s lớn cũng không vọt quá mức bão hoà
        he_so = min(params["aerator_k_per_s"] * dt_s, 1.0)
        tang = he_so * (bao_hoa - do)

    giam = fish_uptake_per_s(temp_c, fish_count, params) * dt_s

    # DO không bao giờ âm
    return max(do + tang - giam, 0.0)
