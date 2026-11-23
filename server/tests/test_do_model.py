"""Kiểm tra mô hình DO. Chạy từ thư mục gốc: python -m pytest server/tests"""

import pytest

from server.do_model import do_sat, step, uptake_per_s

# Hệ số CHỈ để thử nghiệm (không phải giá trị chính thức trong config.json)
PARAMS = {
    "aerator_k_per_s": 0.01,
    "uptake_ref_mg_per_l_per_s": 0.006,
    "uptake_ref_temp_c": 25,
    "uptake_q10": 2.0,
}
# Giống PARAMS nhưng không có tiêu hao oxy, để thấy rõ riêng tác dụng của sục khí
NO_UPTAKE = {**PARAMS, "uptake_ref_mg_per_l_per_s": 0.0}


@pytest.mark.parametrize(
    "temp_c, expected",
    [(20, 9.09), (25, 8.26), (30, 7.56), (35, 6.95)],
)
def test_do_sat_khop_bang_tham_khao(temp_c, expected):
    assert do_sat(temp_c) == pytest.approx(expected, abs=0.05)


def test_do_sat_giam_khi_nuoc_nong_len():
    assert do_sat(20) > do_sat(30)


def test_tieu_hao_bang_r_ref_o_nhiet_do_tham_chieu():
    assert uptake_per_s(25, PARAMS) == pytest.approx(0.006)


def test_tieu_hao_nhan_q10_khi_tang_10_do():
    assert uptake_per_s(35, PARAMS) == pytest.approx(0.006 * 2.0)


def test_do_khong_am():
    do = 0.5
    for _ in range(1000):  # sục tắt, oxy tiêu hao liên tục
        do = step(do, 30, False, 5, PARAMS)
        assert do >= 0
    assert do == 0


def test_suc_khi_bat_do_tien_ve_bao_hoa():
    temp_c = 25
    do = 2.0
    for _ in range(500):
        do = step(do, temp_c, True, 5, NO_UPTAKE)
    assert do == pytest.approx(do_sat(temp_c), abs=0.05)


def test_suc_khi_tat_do_giam_do_tieu_hao():
    assert step(7.0, 25, False, 5, PARAMS) < 7.0


def test_nuoc_am_tieu_hao_nhieu_hon():
    lanh = step(7.0, 25, False, 5, PARAMS)
    nong = step(7.0, 35, False, 5, PARAMS)
    assert nong < lanh


def test_khong_tieu_hao_va_tat_suc_thi_do_dung_yen():
    assert step(6.0, 25, False, 5, NO_UPTAKE) == 6.0


def test_config_that_24_3_do_xuong_duoi_nguong_sau_khoang_235_s():
    """Với config.json thật: nước 24,3 °C, tắt sục, mỗi bước 5 s -> lần đầu DO < ngưỡng ở 235 s.
    (Bằng kết quả mô phỏng cũ: R_ref 0,024 = 80 × 0,0003 trước đây.)"""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "config" / "config.json"
    params = json.loads(path.read_text(encoding="utf-8"))["do_model"]
    do, t = do_sat(24.3), 0
    while do >= params["aerator_on_below"]:
        do = step(do, 24.3, False, 5, params)
        t += 5
    assert t == 235
