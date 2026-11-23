"""Kiểm tra control.py bằng giờ giả (không chờ thật).
Chạy từ thư mục gốc: python -m pytest server/tests"""

import pytest

from server.control import Controller

# Ngưỡng thử nghiệm, cùng giá trị với config.json (QCVN 02-26:2017, SRAC 283)
CONFIG = {
    "do_model": {"aerator_on_below": 3.0, "aerator_off_above": 5.5},
    "pump": {
        "cool_on_above_c": 31,
        "cool_off_below_c": 30.5,
        "burst_s": 5,
        "wait_s": 30,
        "max_s_per_hour": 60,
        "renew_s": 15,
    },
    "control": {"manual_pause_s": 300},
}
HOT, COOL, MID = 33, 29, 30.8  # nhiệt độ: trên ngưỡng bật / dưới ngưỡng tắt / ở giữa
LOW_DO, HIGH_DO = 2.5, 6.0
OK_DO = 4.5  # nằm giữa hai ngưỡng sục khí


class FakeClock:
    """Đồng hồ giả: tự tăng giờ bằng tay."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def ctl():
    """Trả về (bộ điều khiển, đồng hồ giả, danh sách lệnh đã gửi)."""
    sent = []
    clock = FakeClock()
    c = Controller(CONFIG, lambda device, payload: sent.append((clock.t, device, payload)), clock)
    return c, clock, sent


def cmds(sent, device):
    return [(t, p) for t, d, p in sent if d == device]


# ---------- Máy sục khí ----------
def test_suc_khi_bat_khi_do_thap_tat_khi_do_cao(ctl):
    c, clock, sent = ctl
    c.update_state({"aerator": False})
    c.on_twin(COOL, LOW_DO)
    assert cmds(sent, "aerator") == [(clock.t, {"on": True})]

    c.update_state({"aerator": True})
    c.on_twin(COOL, HIGH_DO)
    assert cmds(sent, "aerator")[-1][1] == {"on": False}


def test_suc_khi_khong_gui_lap_khi_da_dung_trang_thai(ctl):
    c, clock, sent = ctl
    c.update_state({"aerator": True})
    for _ in range(10):  # DO thấp liên tục nhưng máy đã bật rồi
        c.on_twin(COOL, LOW_DO)
        clock.t += 5
    assert cmds(sent, "aerator") == []


def test_suc_khi_hysteresis_khong_bat_tat_lien_tuc(ctl):
    c, clock, sent = ctl
    c.update_state({"aerator": False})
    # DO dao động trong vùng giữa hai ngưỡng (4.2 <-> 5.3): không lệnh nào
    for do in [4.2, 5.3, 4.2, 5.3, 4.5, 5.0] * 5:
        c.on_twin(COOL, do)
        clock.t += 5
    assert cmds(sent, "aerator") == []


def test_suc_khi_vung_giua_giu_nguyen_trang_thai(ctl):
    c, clock, sent = ctl
    c.update_state({"aerator": True})
    c.on_twin(COOL, OK_DO)  # đang bật, DO ở giữa -> không tắt
    assert cmds(sent, "aerator") == []


# ---------- Bơm làm mát: liên tục, có gia hạn ----------
RENEW = {"on": True, "seconds": CONFIG["pump"]["renew_s"]}
OFF = {"on": False, "seconds": 0}


def test_bom_nong_thi_bat_va_gia_han_moi_lan_co_so_do(ctl):
    c, clock, sent = ctl
    c.on_twin(HOT, OK_DO)
    assert cmds(sent, "pump") == [(clock.t, RENEW)]
    c.update_state({"pump": True})
    for _ in range(3):  # còn nóng: mỗi số đo gia hạn một lần
        clock.t += 5
        c.on_twin(HOT, OK_DO)
    assert [p for _, p in cmds(sent, "pump")] == [RENEW] * 4


def test_bom_chay_lien_tuc_khong_gioi_han_gio(ctl):
    c, clock, sent = ctl
    for _ in range(2 * 3600 // 5):  # nóng liên tục 2 giờ
        c.on_twin(HOT, OK_DO)
        c.update_state({"pump": True})
        clock.t += 5
    assert all(p == RENEW for _, p in cmds(sent, "pump"))  # lệnh nào cũng kèm seconds
    assert len(cmds(sent, "pump")) == 2 * 3600 // 5         # không bị cắt theo hạn mức giờ


def test_bom_khong_chay_khi_nhiet_do_chua_vuot_nguong(ctl):
    c, clock, sent = ctl
    c.on_twin(MID, OK_DO)
    c.on_twin(CONFIG["pump"]["cool_on_above_c"], OK_DO)  # bằng ngưỡng: chưa phải "lớn hơn"
    assert cmds(sent, "pump") == []


def test_bom_tiep_tuc_o_vung_giua_va_tat_khi_du_mat(ctl):
    c, clock, sent = ctl
    c.on_twin(HOT, OK_DO)  # bắt đầu làm mát
    c.update_state({"pump": True})
    clock.t += 5
    c.on_twin(MID, OK_DO)  # giữa hai ngưỡng: vẫn bơm
    assert cmds(sent, "pump")[-1][1] == RENEW

    clock.t += 5
    c.on_twin(COOL, OK_DO)  # dưới ngưỡng tắt: tắt
    assert cmds(sent, "pump")[-1][1] == OFF
    c.update_state({"pump": False})
    n = len(cmds(sent, "pump"))
    clock.t += 5
    c.on_twin(MID, OK_DO)  # ở giữa nhưng đã dừng: không tự bơm lại
    assert len(cmds(sent, "pump")) == n


def test_bom_da_tat_thi_khong_gui_lenh_tat_lap(ctl):
    c, clock, sent = ctl
    c.update_state({"pump": False})
    c.on_twin(COOL, OK_DO)
    assert cmds(sent, "pump") == []


# ---------- Điều khiển tay ----------
def test_lenh_tay_tam_dung_tu_dong_roi_het_han_thi_tu_dong_lai(ctl):
    c, clock, sent = ctl
    c.update_state({"aerator": False})
    c.manual_command("aerator", True)  # người dùng bật tay
    c.update_state({"aerator": True})
    pause = CONFIG["control"]["manual_pause_s"]

    clock.t += pause - 1
    c.on_twin(COOL, HIGH_DO)  # DO cao (bình thường sẽ tắt) nhưng đang tạm dừng tự động
    assert cmds(sent, "aerator") == [(1000.0, {"on": True})]

    clock.t += 2  # hết tạm dừng
    c.on_twin(COOL, HIGH_DO)
    assert cmds(sent, "aerator")[-1][1] == {"on": False}


def test_do_nguy_hiem_thi_bat_suc_du_dang_tam_dung_tay(ctl):
    """Ưu tiên an toàn: tắt tay rồi DO tụt dưới ngưỡng -> bật ngay, không chờ hết 300 s."""
    c, clock, sent = ctl
    c.update_state({"aerator": True})
    c.manual_command("aerator", False)  # người dùng tắt tay
    c.update_state({"aerator": False})

    clock.t += 10
    c.on_twin(COOL, OK_DO)  # DO còn ổn: vẫn tôn trọng lệnh tay, không gửi gì
    assert cmds(sent, "aerator") == [(1000.0, {"on": False})]

    clock.t += 10
    c.on_twin(COOL, LOW_DO)  # DO nguy hiểm: bật ngay
    assert cmds(sent, "aerator")[-1] == (clock.t, {"on": True})
    assert c.source_for("aerator") == "auto"
    assert c.pause_remaining()["aerator"] == 0  # tạm dừng đã bỏ


def test_do_nguy_hiem_ma_suc_dang_bat_thi_giu_tam_dung(ctl):
    """Đang bật tay rồi thì DO thấp không phải ghi đè gì."""
    c, clock, sent = ctl
    c.update_state({"aerator": False})
    c.manual_command("aerator", True)
    c.update_state({"aerator": True})
    clock.t += 10
    c.on_twin(COOL, LOW_DO)
    assert cmds(sent, "aerator") == [(1000.0, {"on": True})]
    assert c.pause_remaining()["aerator"] > 0


def test_bom_tay_bat_chay_lien_tuc_toi_khi_tat(ctl):
    c, clock, sent = ctl
    assert c.manual_command("pump", True) == RENEW
    c.update_state({"pump": True})
    clock.t += 5
    c.on_twin(COOL, OK_DO)  # nước mát (tự động sẽ tắt) nhưng người dùng đang bật tay: gia hạn
    assert cmds(sent, "pump")[-1][1] == RENEW
    assert c.source_for("pump") == "manual"

    c.manual_command("pump", False)
    c.update_state({"pump": False})
    clock.t += 5
    c.on_twin(COOL, OK_DO)
    assert cmds(sent, "pump")[-1][1] == OFF  # lệnh cuối là lệnh tắt tay, không gia hạn nữa


def test_bom_tay_tat_khi_dang_nong_thi_tam_dung_tu_dong(ctl):
    c, clock, sent = ctl
    c.manual_command("pump", False)
    clock.t += 100
    c.on_twin(HOT, OK_DO)  # đang nóng nhưng người dùng vừa tắt tay
    assert cmds(sent, "pump") == [(1000.0, OFF)]
    clock.t += CONFIG["control"]["manual_pause_s"]  # hết tạm dừng: tự động bơm lại
    c.on_twin(HOT, OK_DO)
    assert cmds(sent, "pump")[-1][1] == RENEW


def test_bom_tay_het_tam_dung_thi_tu_dong_quyet_dinh(ctl):
    c, clock, sent = ctl
    c.manual_command("pump", True)
    c.update_state({"pump": True})
    clock.t += CONFIG["control"]["manual_pause_s"] + 1
    c.on_twin(COOL, OK_DO)  # hết tạm dừng, nước mát: tự động tắt
    assert cmds(sent, "pump")[-1][1] == OFF


def test_nguon_lenh_auto_manual_esp32(ctl):
    c, clock, sent = ctl
    assert c.source_for("aerator") == "esp32"  # chưa có lệnh nào
    c.update_state({"aerator": False})
    c.on_twin(COOL, LOW_DO)
    assert c.source_for("aerator") == "auto"
    c.manual_command("aerator", False)
    assert c.source_for("aerator") == "manual"


def test_gui_loi_thi_khong_ghi_nhan_dot_bom_hay_tam_dung():
    def send_loi(device, payload):
        raise ConnectionError("mất broker")

    clock = FakeClock()
    c = Controller(CONFIG, send_loi, clock)
    with pytest.raises(ConnectionError):
        c.manual_command("pump", True)
    assert c.manual_want == {}  # không ghi nhận ý muốn
    assert c.pause_until == {}  # không tạm dừng tự động
    assert c.source_for("pump") == "esp32"
