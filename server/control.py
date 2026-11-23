"""Tự động điều khiển máy sục khí và bơm làm mát (bước 1.4).

Luồng: mỗi khi có tin pond1/twin mới -> on_twin(temp, do) quyết định có gửi lệnh không.
File này KHÔNG biết MQTT: lệnh được gửi qua hàm `send(device, payload)` do bên ngoài
đưa vào (mqtt_handler publish lên pond1/cmd/<device>). Giờ lấy qua `clock`, nhờ vậy
test được bằng giờ giả, không phải chờ thật.

Mọi ngưỡng đọc từ config/config.json, không viết cứng số ở đây.
"""

import threading
import time

DEVICES = ("aerator", "pump")


class Controller:
    def __init__(self, config, send, clock=time.time):
        self.aerator_cfg = config["do_model"]
        self.pump_cfg = config["pump"]
        self.manual_pause_s = config["control"]["manual_pause_s"]
        self.send = send
        self.clock = clock
        # API (luồng web) và MQTT (luồng paho) cùng gọi vào đây nên cần khoá
        self._lock = threading.RLock()

        self.state = {}        # trạng thái thật từ pond1/state: {"aerator": True, ...}
        self.pause_until = {}  # thiết bị -> giờ hết tạm dừng tự động (sau lệnh tay)
        self.last_source = {}  # thiết bị -> "auto"/"manual": ai ra lệnh gần nhất

        self.manual_want = {}  # thiết bị -> trạng thái người dùng muốn khi bấm tay (bơm cần để gia hạn)

        self.cooling = False   # đang trong chế độ làm mát (có trễ giữa bật và dừng)

    # ---------- Thông tin từ ngoài vào ----------
    def update_state(self, data):
        """Nhớ trạng thái thật của thiết bị (từ pond1/state)."""
        with self._lock:
            for device in DEVICES:
                on = data.get(device)
                if isinstance(on, bool):
                    self.state[device] = on

    def source_for(self, device):
        """Nguồn của lần đổi trạng thái: "auto"/"manual", chưa có lệnh nào thì "esp32"."""
        return self.last_source.get(device, "esp32")

    def on_twin(self, temp, do):
        """Gọi mỗi khi có số đo mới (cùng lúc với tin pond1/twin)."""
        with self._lock:
            now = self.clock()
            self._auto_aerator(do, now)
            self._auto_pump(temp, now)

    # ---------- Điều khiển tay (api.py, Telegram gọi) ----------
    def manual_command(self, device, on, seconds=None):
        """Gửi lệnh tay rồi tạm dừng tự động thiết bị đó manual_pause_s giây.

        Bơm bật tay chạy LIÊN TỤC tới khi người dùng tắt (hoặc hết tạm dừng thì tự động quyết định),
        nhờ server gia hạn mỗi lần có số đo. `seconds` giữ lại cho tương thích, không còn dùng.
        Trả về nội dung lệnh đã gửi.
        """
        if device not in DEVICES:
            raise ValueError(f"Thiết bị lạ: {device!r}")

        with self._lock:
            now = self.clock()
            if device == "pump":
                payload = self._pump_payload(bool(on))
            else:
                payload = {"on": bool(on)}

            self._send(device, payload, "manual")  # gửi lỗi thì dừng ở đây, chưa ghi gì
            self.manual_want[device] = bool(on)
            self.pause_until[device] = now + self.manual_pause_s
            return payload

    # ---------- Máy sục khí: hysteresis ----------
    def _auto_aerator(self, do, now):
        danger = do < self.aerator_cfg["aerator_on_below"]
        if self._is_paused("aerator", now):
            if not danger:
                return
            # Ưu tiên an toàn (chốt 09/11/2026): DO dưới ngưỡng bật thì bật sục khí NGAY,
            # kể cả đang tạm dừng sau lệnh tay. Chỉ chiều BẬT; chiều tắt vẫn tôn trọng lệnh tay.
            if self.state.get("aerator") is not True:
                del self.pause_until["aerator"]  # bỏ tạm dừng, tự động chạy lại bình thường
                print(f"[an toàn] DO {do:.2f} dưới ngưỡng: bỏ tạm dừng tay, bật sục khí")
        if danger:
            want = True
        elif do > self.aerator_cfg["aerator_off_above"]:
            want = False
        else:
            return  # nằm giữa hai ngưỡng: giữ nguyên

        # Chỉ gửi khi cần đổi so với trạng thái thật gần nhất
        if self.state.get("aerator") != want:
            self._send("aerator", {"on": want}, "auto")

    # ---------- Bơm làm mát: liên tục, có gia hạn ----------
    def _auto_pump(self, temp, now):
        """Bơm LIÊN TỤC khi nóng, tới khi nhiệt độ về mức an toàn (chốt 09/11/2026).

        "Liên tục" làm bằng cách gia hạn: mỗi lần có số đo (khoảng 5 s) gửi lại
        {"on": true, "seconds": renew_s}. ESP32 nhận lệnh mới thì đặt lại hẹn giờ.
        Server, mạng hoặc cảm biến chết -> không còn gia hạn -> ESP32 tự tắt bơm sau renew_s giây.
        """
        cfg = self.pump_cfg
        # Có trễ: nóng quá mới bắt đầu, mát hẳn mới dừng, ở giữa thì giữ chế độ cũ
        if temp > cfg["cool_on_above_c"]:
            self.cooling = True
        elif temp < cfg["cool_off_below_c"]:
            self.cooling = False

        if self._is_paused("pump", now):
            want = self.manual_want.get("pump", False)  # đang điều khiển tay: theo ý người dùng
        else:
            want = self.cooling

        if want:
            self._send("pump", self._pump_payload(True), self._pump_source(now))  # bật / gia hạn
        elif self.state.get("pump"):
            self._send("pump", self._pump_payload(False), self._pump_source(now))

    def _pump_payload(self, on):
        """Lệnh bơm luôn kèm seconds: bật = renew_s giây (sẽ được gia hạn), tắt = 0."""
        if on:
            return {"on": True, "seconds": int(self.pump_cfg["renew_s"])}
        return {"on": False, "seconds": 0}

    def _pump_source(self, now):
        return "manual" if self._is_paused("pump", now) else "auto"

    def pause_remaining(self):
        """Mỗi thiết bị còn tạm dừng tự động bao nhiêu giây (0 = đang tự động). Cho web và /status."""
        with self._lock:
            now = self.clock()
            return {d: max(0, round(self.pause_until.get(d, 0) - now)) for d in DEVICES}

    # ---------- Dùng chung ----------
    def _is_paused(self, device, now):
        return now < self.pause_until.get(device, 0)

    def _send(self, device, payload, source):
        """Chốt chặn cuối: lệnh bơm luôn phải có seconds."""
        if device == "pump" and "seconds" not in payload:
            raise ValueError("Lệnh bơm thiếu seconds")
        self.send(device, payload)  # gửi lỗi thì ném lỗi, chưa ghi nhận nguồn lệnh
        self.last_source[device] = source
