"""Nhận tin MQTT từ ESP32 (hoặc simulator), tính DO, ghi SQLite, gửi pond1/twin.

Chạy thử riêng (từ thư mục gốc repo, .venv đã kích hoạt):
    python -m server.mqtt_handler
Chạy cùng web: python -m server.main

Hợp đồng chủ đề và JSON nằm ở docs/mqtt.md.
"""

import json
import math
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from server import control, db, do_model

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"

# Đuôi chủ đề, ghép sau mqtt.base_topic trong config (vd "pond1" + "/sensors")
SUFFIX_SENSORS = "sensors"
SUFFIX_STATE = "state"
SUFFIX_TWIN = "twin"
SUFFIX_CMD = "cmd"  # lệnh gửi tới pond1/cmd/aerator và pond1/cmd/pump

DEVICES = ("aerator", "pump")      # thiết bị trong pond1/state
TEMP_KEY = "temp"                  # mô hình DO cần đúng số đo nhiệt độ
META_KEYS = ("v", "ts")            # khoá không phải số đo, không lưu vào extra


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def la_so(x):
    """True nếu x là số thật (không phải chữ, True/False, NaN, vô cực)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class MqttHandler:
    def __init__(self, config, db_path=db.DEFAULT_DB_PATH, client=None, on_event=None):
        self.config = config
        self.db_path = db_path
        self.client = client
        # on_event(topic, data): nơi đẩy tin sensors/twin/state ra ngoài (vd cho WebSocket).
        # Được gọi từ luồng MQTT, nên bên nhận phải tự chuyển sang luồng của mình.
        self.on_event = on_event

        base = config["mqtt"]["base_topic"]
        self.topic_sensors = f"{base}/{SUFFIX_SENSORS}"
        self.topic_state = f"{base}/{SUFFIX_STATE}"
        self.topic_twin = f"{base}/{SUFFIX_TWIN}"
        self.topic_cmd = f"{base}/{SUFFIX_CMD}"

        # Bộ điều khiển tự động; api.py sau này gọi self.controller.manual_command(...)
        self.controller = control.Controller(config, self._send_command)

        # Chỉ xét cảm biến đang bật trong config
        self.sensors = {s["id"]: s for s in config["sensors"] if s.get("enabled", True)}
        self.all_sensor_ids = {s["id"] for s in config["sensors"]}

        self.devices = {}      # trạng thái thiết bị gần nhất từ pond1/state
        self.do = None         # DO ước tính hiện tại (mg/L)
        self.last_time = None  # lúc nhận số đo trước (để tính dt)

    # ---------- Kết nối broker ----------
    def _tao_client(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pond1-server")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)  # mất broker thì tự nối lại
        mqtt_cfg = self.config["mqtt"]
        print(f"Đang nối broker {mqtt_cfg['host']}:{mqtt_cfg['port']} ...")
        return mqtt_cfg

    def start(self):
        """Nối broker rồi chạy mãi trong luồng hiện tại (Ctrl+C để dừng)."""
        mqtt_cfg = self._tao_client()
        self.client.connect(mqtt_cfg["host"], mqtt_cfg["port"], keepalive=30)
        self.client.loop_forever()

    def start_background(self):
        """Nối broker và chạy trong một luồng riêng của paho (không chặn chương trình).

        connect_async: broker chưa bật thì server vẫn chạy, paho tự thử nối lại.
        """
        mqtt_cfg = self._tao_client()
        self.client.connect_async(mqtt_cfg["host"], mqtt_cfg["port"], keepalive=30)
        self.client.loop_start()

    def stop(self):
        """Dừng luồng nền (dùng với start_background)."""
        if self.client is not None:
            self.client.disconnect()
            self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            print(f"[lỗi  ] Không kết nối được broker, mã lỗi {reason_code}")
            return
        print("[mqtt ] Đã nối broker")
        # Đăng ký ở đây để nối lại xong vẫn nghe tiếp
        client.subscribe([(self.topic_sensors, 0), (self.topic_state, 0)])

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON phải là một đối tượng {...}")
        except (ValueError, UnicodeDecodeError) as e:
            print(f"[bỏ qua] {msg.topic}: JSON không hợp lệ ({e})")
            return
        try:
            if msg.topic == self.topic_sensors:
                self._phat(msg.topic, data)  # cho web thấy cả tin thô
                self.handle_sensors(data)
            elif msg.topic == self.topic_state:
                self._phat(msg.topic, data)
                self.handle_state(data)
        except Exception as e:  # một tin lỗi không được làm sập server
            print(f"[lỗi  ] Xử lý {msg.topic} thất bại: {e!r}")

    # ---------- Nhận số đo ----------
    def handle_sensors(self, data):
        # ts = giờ SERVER lúc nhận tin. Không tin đồng hồ ESP32 (có thể sai hoặc chưa chỉnh giờ),
        # nên lịch sử và /api/history luôn dùng giờ này.
        ts = int(time.time())

        values, extra = self._lay_so_do(data)
        if not values and not extra:
            print("[bỏ qua] Không có số đo hợp lệ nào")
            return

        # ts do ESP32 gửi (nếu có) vẫn giữ lại, trong cột extra
        device_ts = data.get("ts")
        if la_so(device_ts):
            extra["device_ts"] = device_ts

        # Ước tính DO (cần nhiệt độ hợp lệ)
        temp = values.get(TEMP_KEY)
        do_est = None
        if temp is None:
            print("[cảnh báo] Thiếu nhiệt độ hợp lệ, không tính DO lần này")
        else:
            do_est = self._tinh_do(temp)

        db.add_reading(
            ts, values.get("temp"), values.get("ph"), values.get("tds"),
            do_est, extra, self.db_path,
        )

        if do_est is not None:
            twin = {
                "v": 1,
                "ts": ts,
                "do": round(do_est, 2),
                "do_sat": round(do_model.do_sat(temp), 2),
                "level": self._tinh_level({**values, **extra}, do_est),
            }
            self.client.publish(self.topic_twin, json.dumps(twin))
            print(f"[twin ] {twin}")
            self._phat(self.topic_twin, twin)

            # Mỗi tin twin mới: để bộ điều khiển xét có cần gửi lệnh không
            self.controller.on_twin(temp, do_est)

    def _lay_so_do(self, data):
        """Tách số đo hợp lệ trong tin nhắn.

        Trả về (values, extra):
          values = số đo có cột riêng (temp, ph, tds)
          extra  = số đo khác và khoá lạ (lưu JSON vào cột extra)
        Sensor có "enabled": false trong config (vd tds đã bỏ) thì bỏ qua hẳn:
        không lưu, không so ngưỡng; cột riêng của nó trong SQLite để NULL.
        """
        values, extra = {}, {}
        for sid, cfg in self.sensors.items():
            v = data.get(sid)  # thiếu khoá thì None, không báo lỗi
            if v is None:
                continue
            low, high = cfg["valid_range"]
            if not la_so(v) or not (low <= v <= high):
                print(f"[cảnh báo] {sid}={v!r} ngoài khoảng hợp lệ [{low}, {high}], bỏ qua")
                continue
            (values if sid in db.READING_COLUMNS else extra)[sid] = v

        # Khoá lạ (không có trong config): giữ lại trong extra, không kiểm tra
        for key, v in data.items():
            if key not in META_KEYS and key not in self.all_sensor_ids:
                extra[key] = v
        return values, extra

    def _tinh_do(self, temp):
        """Cập nhật và trả về DO ước tính. Sục khí lấy từ pond1/state gần nhất."""
        params = self.config["do_model"]
        now = time.monotonic()
        if self.last_time is None:
            dt = self.config["sampling"]["period_s"]
            self.do = do_model.do_sat(temp)  # lúc khởi động coi như nước bão hoà
        else:
            # Mất tin lâu thì không tính dồn quá ngưỡng ESP32 coi là offline
            dt = min(now - self.last_time, self.config["control"]["esp32_offline_failsafe_s"])
        self.last_time = now

        aerator_on = bool(self.devices.get("aerator", False))
        self.do = do_model.step(self.do, temp, aerator_on, dt, params)
        return self.do

    def _tinh_level(self, values, do_est):
        """Mức cảnh báo: "ok" / "warning" / "danger".

        - danger : DO ước tính thấp hơn ngưỡng bật sục khí (do_model.aerator_on_below)
        - warning: có số đo ngoài warn_range trong config
        - ok     : còn lại
        (Cách chia mức này là giả định, cần nhóm đồng ý.)
        """
        if do_est < self.config["do_model"]["aerator_on_below"]:
            return "danger"
        for sid, cfg in self.sensors.items():
            v = values.get(sid)  # khoá lạ hoặc số đo bị bỏ qua thì không xét
            if v is None:
                continue
            low, high = cfg["warn_range"]
            if not (low <= v <= high):
                return "warning"
        return "ok"

    # ---------- Nhận trạng thái thiết bị ----------
    def handle_state(self, data):
        ts = int(time.time())
        for device in DEVICES:
            on = data.get(device)
            if not isinstance(on, bool):
                continue
            if self.devices.get(device) != on:  # chỉ ghi khi có thay đổi
                # source = ai ra lệnh gần nhất cho thiết bị này ("auto"/"manual")
                source = self.controller.source_for(device)
                db.add_device_event(ts, device, on, source, self.db_path)
                print(f"[state] {device} -> {'BẬT' if on else 'TẮT'} ({source})")
            self.devices[device] = on
        self.controller.update_state(data)

    def _phat(self, topic, data):
        """Đẩy một tin ra ngoài qua on_event (nếu có). Lỗi ở bên nhận không làm sập server."""
        if self.on_event is None:
            return
        try:
            self.on_event(topic, data)
        except Exception as e:
            print(f"[lỗi  ] on_event thất bại: {e!r}")

    # ---------- Gửi lệnh cho ESP32 ----------
    def _send_command(self, device, payload):
        """Controller gọi hàm này để publish lệnh lên pond1/cmd/<device>."""
        topic = f"{self.topic_cmd}/{device}"
        info = self.client.publish(topic, json.dumps(payload))
        if info.rc != mqtt.MQTT_ERR_SUCCESS:  # vd chưa nối được broker
            raise ConnectionError(f"Không gửi được lệnh tới broker (mã {info.rc})")
        print(f"[lệnh ] {topic} {payload}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # in được tiếng Việt trên Windows
    config = load_config()
    db.init_db()
    try:
        MqttHandler(config).start()
    except KeyboardInterrupt:
        print("Dừng.")
