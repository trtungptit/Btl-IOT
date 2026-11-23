"""Tính hệ số hiệu chuẩn pH từ các điểm dung dịch chuẩn: pH = a * mV + b.

mV ở đây là cột "mV_bang_mach" của sketch firmware/tests/test_ph_mv (đã nhân hệ số cầu phân áp).

Cách dùng (từ thư mục gốc repo, .venv đã kích hoạt), mỗi điểm viết dạng pH:mV:
    python tools/calib_ph.py 4.01:3050 6.86:2520 9.18:2080
Kết quả: a, b (dán vào firmware/pond_node/calibration.h: PH_SLOPE_A, PH_OFFSET_B)
và sai số từng điểm. Cần ít nhất 2 điểm; 3 điểm cho biết đường có thẳng không.
(Số trong ví dụ chỉ để minh hoạ, KHÔNG phải số đo thật.)
"""

import sys

import numpy as np


def tinh_hieu_chuan(diem):
    """diem: list (ph, mv). Trả về (a, b, sai_so) với pH = a*mV + b, hồi quy tuyến tính bình phương tối thiểu."""
    if len(diem) < 2:
        raise ValueError("Cần ít nhất 2 điểm hiệu chuẩn")
    ph = np.array([p for p, _ in diem], dtype=float)
    mv = np.array([m for _, m in diem], dtype=float)
    if np.ptp(mv) == 0:
        raise ValueError("Các mV giống hệt nhau: kiểm tra lại số đo")
    a, b = np.polyfit(mv, ph, 1)            # bậc 1 = đường thẳng
    sai_so = ph - (a * mv + b)              # pH chuẩn trừ pH tính ra, từng điểm
    return float(a), float(b), sai_so.tolist()


def doc_diem(args):
    diem = []
    for s in args:
        try:
            p, m = s.split(":")
            diem.append((float(p), float(m)))
        except ValueError:
            raise SystemExit(f"Sai định dạng '{s}'. Viết dạng pH:mV, ví dụ 6.86:2520")
    return diem


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows mặc định cp1252, không in được tiếng Việt
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    diem = doc_diem(sys.argv[1:])
    try:
        a, b, sai_so = tinh_hieu_chuan(diem)
    except ValueError as e:
        raise SystemExit(str(e))

    print(f"pH = a * mV + b")
    print(f"a (PH_SLOPE_A)  = {a:.6f}")
    print(f"b (PH_OFFSET_B) = {b:.4f}")
    print("Sai số từng điểm (pH chuẩn - pH tính ra):")
    for (p, m), e in zip(diem, sai_so):
        print(f"  pH {p:5.2f}  mV {m:8.1f}  sai số {e:+.3f}")
    if a >= 0:
        print("Lưu ý: a dương là bất thường với đa số bảng mạch pH (pH tăng thì mV giảm). Kiểm tra lại số đo.")
    if max(abs(e) for e in sai_so) > 0.15:
        print("Cảnh báo: sai số một điểm > 0,15 pH. Rửa đầu dò bằng nước cất, chờ số ổn định rồi đo lại.")


if __name__ == "__main__":
    main()
