#pragma once
// Hệ số hiệu chuẩn pH: pH = PH_SLOPE_A * mV_bang_mach + PH_OFFSET_B
// Đo ngày 09/11/2026, hiệu chuẩn 2 điểm bằng bột đệm pH 4.01 và 9.18
// (mV_bang_mach = mV chân GPIO34 x PH_DIVIDER_FACTOR, đo bằng firmware/tests/test_ph_mv).
//   pH 4.01 -> 3510 mV,  pH 9.18 -> 2875 mV
// Tính bằng: python tools/calib_ph.py 4.01:3510 9.18:2875
// Chỉ 2 điểm nên chưa tự kiểm tra được độ thẳng; cần đo thêm nước máy để đối chiếu.

constexpr float PH_DIVIDER_FACTOR = 3.0f;       // cầu phân áp 20k + 10k (cần đối chiếu điện trở thật)
constexpr float PH_SLOPE_A        = -0.008142f;
constexpr float PH_OFFSET_B       = 32.5875f;
