"""Kiểm tra api.py bằng TestClient (không cần broker thật, không đụng data/pond.db).
Chạy từ thư mục gốc: python -m pytest server/tests"""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server import api, db
from server.mqtt_handler import MqttHandler, load_config


class FakeClient:
    """Thay cho paho client: ghi lại các tin 'đã publish'; rc != 0 giả vờ mất broker."""

    def __init__(self):
        self.published = []
        self.rc = 0

    def publish(self, topic, payload):
        if self.rc == 0:
            self.published.append((topic, json.loads(payload)))
        return SimpleNamespace(rc=self.rc)


def mqtt_tin(topic, data):
    """Một tin MQTT giả, giống thứ paho đưa vào _on_message."""
    return SimpleNamespace(topic=topic, payload=json.dumps(data).encode("utf-8"))


@pytest.fixture
def env(tmp_path):
    config = load_config()
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    client = FakeClient()
    hub = api.Hub()
    handler = MqttHandler(config, db_path=db_path, client=client, on_event=hub.publish_from_thread)
    app = api.create_app(handler, hub, web_dir=None, start_mqtt=False)
    with TestClient(app) as tc:  # vào "with" thì lifespan chạy và hub nhận vòng lặp asyncio
        yield SimpleNamespace(tc=tc, handler=handler, client=client, db_path=db_path, config=config)


def gui_tin(env, suffix, data):
    """Giả vờ ESP32 gửi một tin lên broker rồi handler nhận."""
    topic = f"{env.config['mqtt']['base_topic']}/{suffix}"
    env.handler._on_message(None, None, mqtt_tin(topic, data))


def cho_status(env, key):
    """Chờ tối đa ~1 giây cho tin đi từ luồng MQTT sang luồng web."""
    for _ in range(50):
        body = env.tc.get("/api/status").json()
        if body[key] is not None:
            return body
        time.sleep(0.02)
    return body


# ---------- GET /api/config ----------
def test_config_chi_tra_cac_muc_cho_web_va_chi_doc(env):
    r = env.tc.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"sensors", "do_model", "pump", "pond_geometry_m"}
    assert body["sensors"] == env.config["sensors"]
    assert body["do_model"]["aerator_on_below"] == env.config["do_model"]["aerator_on_below"]
    assert env.tc.post("/api/config", json={}).status_code == 405  # không cho ghi


# ---------- GET /api/status ----------
def test_status_luc_dau_toan_null(env):
    r = env.tc.get("/api/status")
    assert r.status_code == 200
    assert r.json() == {
        "sensors": None, "twin": None, "state": None,
        "auto_pause_s": {"aerator": 0, "pump": 0},  # chưa có lệnh tay: đang tự động
    }


def test_status_co_so_do_twin_va_state_that(env):
    sensors = {"v": 1, "ts": 1700000000, "temp": 28.4, "ph": 7.2, "tds": 310}
    state = {"v": 1, "aerator": True, "pump": False, "fw": "test"}
    gui_tin(env, "state", state)
    gui_tin(env, "sensors", sensors)

    body = cho_status(env, "twin")
    assert body["sensors"] == sensors
    assert body["state"] == state  # trạng thái thật từ pond1/state
    assert body["twin"]["v"] == 1 and "do" in body["twin"] and "level" in body["twin"]


# ---------- GET /api/history ----------
def test_history_chi_lay_trong_khoang_phut_va_cu_nhat_dung_dau(env):
    now = int(time.time())
    db.add_reading(now - 120 * 60, 27, 7, 300, 7.0, db_path=env.db_path)  # 2 giờ trước: ngoài khoảng
    db.add_reading(now - 30 * 60, 28, 7, 300, 6.9, db_path=env.db_path)
    db.add_reading(now - 10 * 60, 29, 7, 300, 6.8, {"water_level_cm": 22.5}, db_path=env.db_path)

    r = env.tc.get("/api/history", params={"minutes": 60})
    assert r.status_code == 200
    readings = r.json()["readings"]
    assert [x["temp"] for x in readings] == [28, 29]  # cũ -> mới
    assert readings[1]["extra"] == {"water_level_cm": 22.5}


def test_history_mac_dinh_60_phut_va_tu_choi_so_phut_sai(env):
    assert env.tc.get("/api/history").json()["minutes"] == 60
    assert env.tc.get("/api/history", params={"minutes": 0}).status_code == 422
    assert env.tc.get("/api/history", params={"minutes": "abc"}).status_code == 422


# ---------- POST /api/cmd/{device} ----------
def test_cmd_suc_khi_chi_tra_sent_va_publish_dung_chu_de(env):
    r = env.tc.post("/api/cmd/aerator", json={"on": True})
    assert r.status_code == 200
    assert r.json() == {"sent": True}  # KHÔNG có trạng thái mới
    assert env.client.published == [("pond1/cmd/aerator", {"on": True})]


def test_cmd_bat_bom_gui_kem_renew_s(env):
    """Bật bơm tay không cần seconds: server tự gửi kèm renew_s (rồi gia hạn mỗi số đo)."""
    renew = env.config["pump"]["renew_s"]
    for body in ({"on": True}, {"on": True, "seconds": 5}):  # seconds cũ: bỏ qua, không lỗi
        env.client.published.clear()
        r = env.tc.post("/api/cmd/pump", json=body)
        assert r.json() == {"sent": True}
        assert env.client.published == [("pond1/cmd/pump", {"on": True, "seconds": renew})]


def test_cmd_tat_bom_van_co_seconds(env):
    env.tc.post("/api/cmd/pump", json={"on": False})
    assert env.client.published == [("pond1/cmd/pump", {"on": False, "seconds": 0})]


def test_cmd_thiet_bi_la_tra_404_va_body_sai_tra_422(env):
    assert env.tc.post("/api/cmd/heater", json={"on": True}).status_code == 404
    assert env.tc.post("/api/cmd/aerator", json={}).status_code == 422
    assert env.tc.post("/api/cmd/aerator", json={"on": "khong"}).status_code == 422


def test_cmd_mat_broker_tra_503_khong_noi_da_gui(env):
    env.client.rc = 4  # giả vờ chưa nối được broker
    r = env.tc.post("/api/cmd/aerator", json={"on": True})
    assert r.status_code == 503
    assert "sent" not in r.json()


def test_cmd_tay_tam_dung_tu_dong(env):
    env.tc.post("/api/cmd/aerator", json={"on": False})
    assert env.handler.controller.pause_until["aerator"] > time.time()


# ---------- WebSocket /ws ----------
def test_ws_day_sensors_roi_twin_dung_dinh_dang(env):
    with env.tc.websocket_connect("/ws") as ws:
        gui_tin(env, "sensors", {"v": 1, "ts": 1700000000, "temp": 28.4, "ph": 7.2, "tds": 310})
        msg1 = ws.receive_json()
        msg2 = ws.receive_json()
    assert msg1["topic"] == "pond1/sensors" and msg1["data"]["temp"] == 28.4
    assert msg2["topic"] == "pond1/twin" and "do" in msg2["data"]


def test_ws_moi_mo_nhan_ngay_tin_moi_nhat(env):
    gui_tin(env, "state", {"v": 1, "aerator": False, "pump": False, "fw": "t"})
    cho_status(env, "state")  # chờ tin tới hub
    with env.tc.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
    assert msg["topic"] == "pond1/state" and msg["data"]["aerator"] is False


# ---------- ts của readings là giờ server ----------
def test_readings_luu_gio_server_va_giu_ts_esp32_trong_extra(env):
    truoc = int(time.time())
    gui_tin(env, "sensors", {"v": 1, "ts": 1700000000, "temp": 28.4, "ph": 7.2, "tds": 310})
    sau = int(time.time())

    row = db.get_latest_readings(1, env.db_path)[0]
    assert truoc <= row["ts"] <= sau  # giờ server, không phải 1700000000
    assert row["extra"] == {"device_ts": 1700000000}


def test_readings_khong_co_ts_esp32_thi_khong_co_device_ts(env):
    gui_tin(env, "sensors", {"v": 1, "temp": 28.4, "ph": 7.2, "tds": 310})
    assert db.get_latest_readings(1, env.db_path)[0]["extra"] == {}


def test_history_van_thay_so_do_du_esp32_gui_ts_sai(env):
    # ESP32 chưa chỉnh giờ: gửi ts = 0. Trước đây biểu đồ sẽ rỗng.
    gui_tin(env, "sensors", {"v": 1, "ts": 0, "temp": 28.4, "ph": 7.2, "tds": 310})
    readings = env.tc.get("/api/history", params={"minutes": 5}).json()["readings"]
    assert len(readings) == 1
    assert readings[0]["extra"] == {"device_ts": 0}


def test_twin_dung_chung_gio_server_voi_readings(env):
    gui_tin(env, "sensors", {"v": 1, "ts": 1700000000, "temp": 28.4, "ph": 7.2, "tds": 310})
    twin = cho_status(env, "twin")["twin"]
    assert twin["ts"] == db.get_latest_readings(1, env.db_path)[0]["ts"]


def test_file_web_khong_cho_dung_ban_cu(tmp_path):
    """File giao diện có Cache-Control: no-cache để trình duyệt luôn lấy app.js mới."""
    web = tmp_path / "web"
    web.mkdir()
    (web / "app.js").write_text("// thu", encoding="utf-8")
    config = load_config()
    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    hub = api.Hub()
    handler = MqttHandler(config, db_path=db_path, client=FakeClient(), on_event=hub.publish_from_thread)
    with TestClient(api.create_app(handler, hub, web_dir=web)) as tc:
        r = tc.get("/app.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
