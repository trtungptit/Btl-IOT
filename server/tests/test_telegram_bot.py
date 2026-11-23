"""Test logic bot Telegram (không cần mạng, không đọc .env thật).
Chạy từ thư mục gốc: python -m pytest server/tests"""

import pytest

from server import control
from server.mqtt_handler import load_config
from server.telegram_bot import AlertManager, format_status, parse_device_command, read_env


class Clock:
    """Đồng hồ giả: tự chỉnh giờ, không phải chờ thật."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def am(cfg):
    clock = Clock()
    a = AlertManager(cfg, clock=clock)
    a.clock_obj = clock
    return a


def temp_cao(cfg):
    return next(s for s in cfg["sensors"] if s["id"] == "temp")["warn_range"][1] + 1


def temp_tot(cfg):
    low, high = next(s for s in cfg["sensors"] if s["id"] == "temp")["warn_range"]
    return (low + high) / 2


def test_binh_thuong_khong_nhan_gi(am, cfg):
    assert am.on_sensors({"temp": temp_tot(cfg), "ph": 7.2}) == []


def test_vuot_nguong_bao_mot_lan_roi_hoi_phuc(am, cfg):
    msgs = am.on_sensors({"temp": temp_cao(cfg)})
    assert len(msgs) == 1 and "Nhiệt độ cao" in msgs[0]
    # Vẫn nóng: không nhắn lại
    assert am.on_sensors({"temp": temp_cao(cfg)}) == []
    # Về bình thường: báo hồi phục
    msgs = am.on_sensors({"temp": temp_tot(cfg)})
    assert len(msgs) == 1 and "bình thường" in msgs[0]


def test_cooldown_chan_bao_lai_som(am, cfg):
    am.on_sensors({"temp": temp_cao(cfg)})
    am.on_sensors({"temp": temp_tot(cfg)})          # hồi phục
    am.clock_obj.t += 10
    assert am.on_sensors({"temp": temp_cao(cfg)}) == []  # nóng lại ngay: bị cooldown chặn
    # Hết cooldown mà vẫn nóng: báo bù
    am.clock_obj.t += cfg["telegram"]["cooldown_s"]
    msgs = am.on_sensors({"temp": temp_cao(cfg)})
    assert len(msgs) == 1 and "cao" in msgs[0]


def test_khong_bao_hoi_phuc_neu_chua_bao_loi(am, cfg):
    am.on_sensors({"temp": temp_cao(cfg)})
    am.on_sensors({"temp": temp_tot(cfg)})
    am.clock_obj.t += 10
    am.on_sensors({"temp": temp_cao(cfg)})  # bị chặn, chưa báo
    assert am.on_sensors({"temp": temp_tot(cfg)}) == []  # nên cũng không báo hồi phục


def test_moi_loai_canh_bao_rieng(am, cfg):
    am.on_sensors({"temp": temp_cao(cfg)})
    msgs = am.on_sensors({"temp": temp_cao(cfg), "ph": 3.0})  # pH là loại khác, không bị chặn
    assert len(msgs) == 1 and "pH" in msgs[0]


def test_bo_qua_sensor_tat_va_khoa_thieu(am):
    assert am.on_sensors({"tds": 99999}) == []   # tds enabled false
    assert am.on_sensors({}) == []


def test_do_thap(am, cfg):
    nguong = cfg["do_model"]["aerator_on_below"]
    assert len(am.on_twin({"do": nguong - 1})) == 1
    assert am.on_twin({"do": nguong - 1}) == []
    assert len(am.on_twin({"do": nguong + 2})) == 1  # hồi phục


def test_thiet_bi_doi_trang_thai(am):
    assert am.on_state({"aerator": False, "pump": False}) == []  # lần đầu: chỉ ghi nhớ
    msgs = am.on_state({"aerator": True, "pump": False})
    assert msgs == ["🔌 Máy sục khí BẬT"]
    assert am.on_state({"aerator": True, "pump": False}) == []   # không đổi
    assert am.on_state({"aerator": False}) == ["🔌 Máy sục khí TẮT"]
    # Bật lại ngay: loại "bật" còn cooldown
    assert am.on_state({"aerator": True}) == []


def test_esp32_im_lang(am, cfg):
    assert am.check_offline() == []       # chưa nhận số đo nào: chưa kết luận
    am.on_sensors({"temp": temp_tot(cfg)})
    am.clock_obj.t += cfg["control"]["esp32_offline_failsafe_s"] - 1
    assert am.check_offline() == []
    am.clock_obj.t += 2
    msgs = am.check_offline()
    assert len(msgs) == 1 and "ESP32" in msgs[0]
    assert am.check_offline() == []       # không nhắn lặp
    msgs = am.on_sensors({"temp": temp_tot(cfg)})
    assert msgs == ["✅ ESP32 đã gửi số đo trở lại."]


def test_parse_lenh():
    assert parse_device_command("aerator", ["on"]) == (True, None)
    assert parse_device_command("aerator", ["OFF"]) == (False, None)
    assert parse_device_command("pump", ["on"]) == (True, None)
    assert parse_device_command("pump", ["off"]) == (False, None)
    with pytest.raises(ValueError):
        parse_device_command("pump", [])      # thiếu on/off: từ chối
    with pytest.raises(ValueError):
        parse_device_command("pump", ["5"])   # kiểu cũ /pump 5: không còn dùng
    with pytest.raises(ValueError):
        parse_device_command("aerator", ["abc"])


def test_format_status():
    topics = {"sensors": "p/sensors", "twin": "p/twin", "state": "p/state"}
    latest = {
        "p/sensors": {"temp": 27.5, "ph": 7.1},
        "p/twin": {"do": 6.2, "do_sat": 7.9, "level": "ok", "ts": 100},
        "p/state": {"aerator": True, "pump": False},
    }
    text = format_status(latest, topics, {"aerator": 120, "pump": 0}, now=105)
    assert "27.5" in text and "6.2" in text and "Máy sục khí: BẬT" in text
    assert "còn 120 s" in text and "cách đây 5 s" in text
    # Chưa có gì vẫn không lỗi
    assert "chưa" in format_status({}, topics, {})


def test_read_env(tmp_path):
    p = tmp_path / ".env"
    p.write_text("TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_ALLOWED_CHAT_IDS=111, -222\n", encoding="utf-8")
    assert read_env(p) == ("123:abc", [111, -222])
    p.write_text("TELEGRAM_BOT_TOKEN=dan_token_vao_day\nTELEGRAM_ALLOWED_CHAT_IDS=dan_chat_id_vao_day\n")
    assert read_env(p) == (None, [])     # giá trị giả của .env.example
    assert read_env(tmp_path / "khong_co") == (None, [])


def test_pause_remaining(cfg):
    clock = Clock()
    c = control.Controller(cfg, lambda d, p: None, clock=clock)
    assert c.pause_remaining() == {"aerator": 0, "pump": 0}
    c.manual_command("aerator", True)
    clock.t += 100
    assert c.pause_remaining()["aerator"] == cfg["control"]["manual_pause_s"] - 100
