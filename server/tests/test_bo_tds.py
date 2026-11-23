"""Kiểm tra server chạy đúng khi đã BỎ cảm biến TDS (08/11/2026).

ESP32 giờ chỉ gửi temp và ph. Server phải:
  - vẫn lưu số đo, cột tds để NULL
  - vẫn tính DO ước tính và gửi pond1/twin
  - không so ngưỡng TDS (sensor tds "enabled": false thì không sinh cảnh báo)

Chạy từ thư mục gốc: python -m pytest server/tests
"""

import copy
import json
from types import SimpleNamespace

from server import db
from server.mqtt_handler import MqttHandler, load_config


class FakeClient:
    """Thay cho paho client: chỉ ghi lại các tin 'đã publish', không cần broker thật."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))
        return SimpleNamespace(rc=0)


def tao_handler(tmp_path, tds_enabled):
    """Tạo handler với config thật, chỉ đổi cờ enabled của tds (không sửa file config.json)."""
    config = copy.deepcopy(load_config())
    for s in config["sensors"]:
        if s["id"] == "tds":
            s["enabled"] = tds_enabled
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    client = FakeClient()
    handler = MqttHandler(config, db_path=db_path, client=client)
    return handler, client, db_path


def tin_twin(client):
    """Các tin pond1/twin mà handler đã gửi."""
    return [data for topic, data in client.published if topic.endswith("/twin")]


def test_tin_chi_co_temp_ph_van_luu_va_tinh_do(tmp_path):
    handler, client, db_path = tao_handler(tmp_path, tds_enabled=False)

    handler.handle_sensors({"v": 1, "ts": 1700000000, "temp": 28.4, "ph": 7.2})

    row = db.get_latest_readings(1, db_path)[0]
    assert row["temp"] == 28.4
    assert row["ph"] == 7.2
    assert row["tds"] is None          # cột tds giữ nguyên trong bảng nhưng để trống
    assert row["do_est"] is not None   # DO ước tính vẫn được tính

    twins = tin_twin(client)
    assert len(twins) == 1
    assert twins[0]["level"] == "ok"   # nhiệt độ, pH trong ngưỡng, DO lúc đầu = bão hoà


def test_tds_da_tat_thi_khong_canh_bao_du_esp32_cu_van_gui(tmp_path):
    # ESP32 bản cũ lỡ gửi tds rất cao: server phải bỏ qua, không cảnh báo, không lưu
    handler, client, db_path = tao_handler(tmp_path, tds_enabled=False)

    handler.handle_sensors({"v": 1, "temp": 28.4, "ph": 7.2, "tds": 5000})

    row = db.get_latest_readings(1, db_path)[0]
    assert row["tds"] is None
    assert "tds" not in row["extra"]   # cũng không lọt vào cột extra
    assert tin_twin(client)[0]["level"] == "ok"


def test_doi_chung_tds_bat_thi_van_canh_bao(tmp_path):
    # Đối chứng: cùng tin đó nhưng tds đang bật -> phải là "warning".
    # Chứng tỏ test trên "ok" là nhờ enabled=false, không phải do may.
    handler, client, _ = tao_handler(tmp_path, tds_enabled=True)

    handler.handle_sensors({"v": 1, "temp": 28.4, "ph": 7.2, "tds": 5000})

    assert tin_twin(client)[0]["level"] == "warning"


def test_config_that_da_tat_tds_va_van_giu_khoa():
    # Đọc đúng file config/config.json: tds phải tắt nhưng KHÔNG bị xoá (quy ước "chỉ thêm, không xoá")
    config = load_config()
    sensors = {s["id"]: s for s in config["sensors"]}
    assert "tds" in sensors
    assert sensors["tds"]["enabled"] is False
    assert sensors["temp"]["enabled"] is True and sensors["ph"]["enabled"] is True
    assert "tds_settle_s" in config["sampling"]
