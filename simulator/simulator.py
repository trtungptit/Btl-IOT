"""
simulator.py - Giả lập ESP32 cho ao cá (bước 1.1)

Làm 3 việc giống hệt ESP32 thật sẽ làm:
  1. Mỗi 5 giây gửi số đo giả lên  pond1/sensors
  2. Nghe lệnh ở                    pond1/cmd/aerator, pond1/cmd/pump
  3. Báo trạng thái thật lên         pond1/state  (mỗi khi thiết bị đổi trạng thái)

Phím điều khiển (gõ chữ rồi bấm Enter):
  n  = đổ nước ấm (nhiệt độ tăng ~4 °C)
  m  = pha muối   (TDS tăng ~300 ppm)
  s  = in trạng thái hiện tại
  q  = thoát

Chạy từ thư mục gốc repo, .venv đã kích hoạt:
  python simulator/simulator.py
"""

import json
import random
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

# ---------- Đọc cấu hình chung (không viết cứng số trong code) ----------
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"
with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

HOST = CONFIG["mqtt"]["host"]
PORT = CONFIG["mqtt"]["port"]
BASE = CONFIG["mqtt"]["base_topic"]          # "pond1"
PERIOD_S = CONFIG["sampling"]["period_s"]    # 5 giây

TOPIC_SENSORS = f"{BASE}/sensors"
TOPIC_STATE = f"{BASE}/state"
TOPIC_CMD = f"{BASE}/cmd/#"                  # dấu # = nghe mọi chủ đề con của cmd/

FW_VERSION = "sim-0.1.0"                     # để biết dữ liệu đến từ simulator

# ---------- "Ao ảo": các con số mà simulator tự thay đổi ----------
# Các hằng số dưới đây chỉ để mô phỏng cho dễ nhìn, KHÔNG phải số đo thật.
ROOM_TEMP_C = 27.0       # nhiệt độ phòng, nước tự nguội dần về mức này
pond = {
    "temp": 27.0,        # °C
    "ph": 7.2,
    "tds": 250.0,        # ppm
}
devices = {
    "aerator": False,    # máy sục khí
    "pump": False,       # bơm
}
pump_off_at = 0.0        # thời điểm (giây) bơm phải tự tắt
lock = threading.Lock()  # khoá để 2 luồng không sửa biến cùng lúc


# ---------- Gửi trạng thái thiết bị ----------
def publish_state(client):
    """Gửi pond1/state. Giao diện chỉ tin vào tin nhắn này."""
    with lock:
        msg = {"v": 1, "aerator": devices["aerator"], "pump": devices["pump"], "fw": FW_VERSION}
    client.publish(TOPIC_STATE, json.dumps(msg))
    print(f"[state ] {msg}")


# ---------- Nhận lệnh ----------
def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        print(f"[lỗi  ] Không kết nối được broker, mã lỗi {reason_code}")
        return
    print(f"[mqtt  ] Đã nối broker {HOST}:{PORT}")
    client.subscribe(TOPIC_CMD)   # đăng ký lại mỗi lần (kể cả khi nối lại)
    publish_state(client)         # khởi động xong thì báo trạng thái ngay


def on_message(client, userdata, msg):
    global pump_off_at
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        print(f"[bỏ qua] {msg.topic}: không phải JSON hợp lệ -> {msg.payload!r}")
        return

    on = data.get("on")
    if not isinstance(on, bool):
        print(f"[bỏ qua] {msg.topic}: thiếu khoá 'on' true/false -> {data}")
        return

    changed = False
    with lock:
        if msg.topic == f"{BASE}/cmd/aerator":
            changed = devices["aerator"] != on
            devices["aerator"] = on

        elif msg.topic == f"{BASE}/cmd/pump":
            if on:
                seconds = data.get("seconds")
                # Quy tắc an toàn: lệnh bật bơm bắt buộc có số giây
                if not isinstance(seconds, (int, float)) or seconds <= 0:
                    print(f"[từ chối] Lệnh bật bơm thiếu 'seconds' -> {data}")
                    return
                pump_off_at = time.time() + seconds
                print(f"[bơm  ] Bật {seconds} s")
            changed = devices["pump"] != on
            devices["pump"] = on
        else:
            print(f"[bỏ qua] Chủ đề lạ {msg.topic}")
            return

    print(f"[lệnh  ] {msg.topic} {data}")
    if changed:
        publish_state(client)


# ---------- Mô phỏng ao thay đổi theo thời gian ----------
def update_pond(dt):
    """Cập nhật số đo giả sau dt giây."""
    with lock:
        # Nước tự nguội/ấm dần về nhiệt độ phòng (chậm)
        pond["temp"] += (ROOM_TEMP_C - pond["temp"]) * 0.002 * dt
        # Bơm chạy: bơm nước mát từ xô vào, nhiệt độ và TDS giảm nhẹ
        if devices["pump"]:
            pond["temp"] -= 0.15 * dt
            pond["tds"] -= 3.0 * dt
        # pH dao động nhỏ quanh 7.2
        pond["ph"] += (7.2 - pond["ph"]) * 0.05 * dt
        pond["tds"] = max(0.0, pond["tds"])


def read_sensors():
    """Giống ESP32 đọc cảm biến: giá trị thật + một chút nhiễu."""
    with lock:
        return {
            "v": 1,
            "ts": int(time.time()),   # giây kể từ 1/1/1970 (Unix time)
            "temp": round(pond["temp"] + random.uniform(-0.05, 0.05), 2),
            "ph": round(pond["ph"] + random.uniform(-0.02, 0.02), 2),
            "tds": round(pond["tds"] + random.uniform(-2, 2), 1),
        }


# ---------- Luồng đọc bàn phím ----------
def keyboard_loop(client, stop_event):
    print("Phím: n = nước ấm, m = pha muối, s = trạng thái, q = thoát (gõ rồi Enter)")
    while not stop_event.is_set():
        try:
            key = input().strip().lower()
        except EOFError:
            break
        with lock:
            if key == "n":
                pond["temp"] += 4.0
                print(f"[phím  ] Đổ nước ấm -> temp ~ {pond['temp']:.1f} °C")
            elif key == "m":
                pond["tds"] += 300.0
                print(f"[phím  ] Pha muối -> tds ~ {pond['tds']:.0f} ppm")
            elif key == "s":
                print(f"[ao    ] {pond}  thiết bị: {devices}")
            elif key == "q":
                stop_event.set()


# ---------- Chương trình chính ----------
def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pond1-simulator")
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)  # mất broker thì tự nối lại

    print(f"Đang nối broker {HOST}:{PORT} ...")
    client.connect(HOST, PORT, keepalive=30)
    client.loop_start()   # paho tự chạy một luồng riêng để nhận tin

    stop_event = threading.Event()
    threading.Thread(target=keyboard_loop, args=(client, stop_event), daemon=True).start()

    last = time.time()
    next_send = last
    try:
        while not stop_event.is_set():
            now = time.time()
            update_pond(now - last)
            last = now

            # Bơm tự tắt khi hết số giây trong lệnh (giống ESP32 thật)
            pump_expired = False
            with lock:
                if devices["pump"] and now >= pump_off_at:
                    devices["pump"] = False
                    pump_expired = True
            if pump_expired:
                print("[bơm  ] Hết giờ, tự tắt")
                publish_state(client)

            # Mỗi PERIOD_S giây gửi số đo một lần
            if now >= next_send:
                data = read_sensors()
                client.publish(TOPIC_SENSORS, json.dumps(data))
                print(f"[sensor] {data}")
                next_send = now + PERIOD_S

            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print("Dừng simulator.")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
