"""Bot Telegram: gửi cảnh báo theo SỰ KIỆN và trả lời lệnh /status, /aerator, /pump.

Luồng: tin MQTT -> Hub (api.py) -> TelegramBot.on_hub_message -> AlertManager quyết định
có cần nhắn không -> gửi tới mọi chat id được phép.

Hai phần:
  AlertManager : logic thuần (không mạng), test bằng giờ giả ở server/tests/test_telegram_bot.py
  TelegramBot  : nối với Telegram bằng python-telegram-bot, chạy chung vòng lặp với web

Token và chat id đọc từ .env (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS, cách nhau dấu phẩy).
Không có .env hoặc thiếu token thì server vẫn chạy, chỉ không có Telegram.
"""

import asyncio
import time
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
OFFLINE_CHECK_PERIOD_S = 5   # bao lâu kiểm tra "ESP32 im lặng" một lần (nhịp kiểm tra, không phải ngưỡng)

DEVICE_NAMES = {"aerator": "Máy sục khí", "pump": "Bơm làm mát"}


# =====================================================================
# 1. LOGIC CẢNH BÁO (thuần, không mạng)
# =====================================================================
class AlertManager:
    """Nhận tin sensors/twin/state, trả về danh sách câu cần nhắn (có thể rỗng).

    Quy tắc:
      - Chỉ nhắn khi có sự kiện: vượt ngưỡng, thiết bị đổi trạng thái, ESP32 im lặng, hồi phục.
      - Mỗi loại (key) cách nhau ít nhất telegram.cooldown_s giây.
      - Báo "đã trở lại bình thường" chỉ khi trước đó đã thật sự báo lỗi đó.
      - Lỗi còn kéo dài mà lần trước bị cooldown chặn thì hết cooldown sẽ báo bù.
    """

    def __init__(self, config, clock=time.time):
        self.clock = clock
        self.cooldown_s = config["telegram"]["cooldown_s"]
        self.offline_s = config["control"]["esp32_offline_failsafe_s"]
        self.do_on_below = config["do_model"]["aerator_on_below"]
        self.sensors = [s for s in config["sensors"] if s.get("enabled", True)]

        self.last_sent = {}      # key -> giờ gửi gần nhất
        self.active = {}         # key -> {"text": câu báo lỗi, "notified": đã gửi chưa}
        self.devices = {}        # trạng thái thiết bị gần nhất
        self.last_sensor_time = None  # giờ (theo clock) nhận số đo gần nhất

    # ---------- Vào ----------
    def on_sensors(self, data):
        now = self.clock()
        # Có số đo = ESP32 còn sống: nếu trước đó đã báo "im lặng" thì báo hồi phục
        out = self._condition("esp32_offline", False, "", "✅ ESP32 đã gửi số đo trở lại.")
        self.last_sensor_time = now

        for s in self.sensors:
            v = data.get(s["id"])
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue  # thiếu khoá hoặc sai kiểu: không kết luận gì
            low, high = s["warn_range"]
            unit = s.get("unit", "")
            ten = s.get("label", s["id"])
            bad = not (low <= v <= high)
            huong = "cao" if v > high else "thấp"
            out += self._condition(
                f"sensor:{s['id']}", bad,
                f"⚠️ {ten} {huong}: {v}{unit} (ngưỡng {low}–{high}{unit})",
                f"✅ {ten} đã về bình thường: {v}{unit}",
            )
        return out

    def on_twin(self, data):
        do = data.get("do")
        if not isinstance(do, (int, float)):
            return []
        return self._condition(
            "do_low", do < self.do_on_below,
            f"🚨 DO ước tính thấp: {do} mg/L (dưới {self.do_on_below}). Cần sục khí.",
            f"✅ DO ước tính đã lên lại: {do} mg/L",
        )

    def on_state(self, data):
        out = []
        for device, ten in DEVICE_NAMES.items():
            on = data.get(device)
            if not isinstance(on, bool):
                continue
            old = self.devices.get(device)
            self.devices[device] = on
            if old is None or old == on:
                continue  # lần đầu biết trạng thái hoặc không đổi: không nhắn
            # Mỗi chiều (bật/tắt) là một loại riêng, để bơm chạy theo đợt không nhắn liên tục
            key = f"device:{device}:{'on' if on else 'off'}"
            out += self._event(key, f"🔌 {ten} {'BẬT' if on else 'TẮT'}")
        return out

    def check_offline(self):
        """Gọi định kỳ. ESP32 im lặng quá esp32_offline_failsafe_s giây thì báo lỗi hệ thống."""
        if self.last_sensor_time is None:
            return []  # chưa nhận số đo lần nào (vừa bật server): chưa kết luận
        silent = self.clock() - self.last_sensor_time
        return self._condition(
            "esp32_offline", silent > self.offline_s,
            f"❌ Lỗi hệ thống: không nhận được số đo từ ESP32 hơn {self.offline_s} s. "
            "ESP32 sẽ tự vào chế độ an toàn (bơm tắt, sục khí bật).",
            "✅ ESP32 đã gửi số đo trở lại.",
        )

    # ---------- Bên trong ----------
    def _cooldown_ok(self, key, now):
        last = self.last_sent.get(key)
        return last is None or now - last >= self.cooldown_s

    def _event(self, key, text):
        """Sự kiện một lần (vd thiết bị đổi trạng thái): gửi nếu qua cooldown."""
        now = self.clock()
        if not self._cooldown_ok(key, now):
            return []
        self.last_sent[key] = now
        return [text]

    def _condition(self, key, bad, alert_text, recover_text):
        """Tình trạng kéo dài (vd nhiệt độ cao): báo khi bắt đầu, báo hồi phục khi hết."""
        now = self.clock()
        cur = self.active.get(key)
        if bad:
            if cur is None:
                cur = self.active[key] = {"notified": False}
            cur["text"] = alert_text  # luôn giữ số đo mới nhất cho lần báo bù
            if not cur["notified"] and self._cooldown_ok(key, now):
                cur["notified"] = True
                self.last_sent[key] = now
                return [alert_text]
            return []
        # Không còn lỗi
        if cur is not None:
            del self.active[key]
            if cur["notified"]:
                return [recover_text]  # chỉ báo hồi phục khi đã báo lỗi trước đó
        return []


# =====================================================================
# 2. TRẢ LỜI /status (thuần, dễ test)
# =====================================================================
def format_status(latest, topics, pause_remaining, now=None):
    """Tạo câu trả lời /status từ tin mới nhất (hub.latest).

    topics: {"sensors": ..., "twin": ..., "state": ...} tên chủ đề đầy đủ
    pause_remaining: {"aerator": giây còn tạm dừng tự động, ...}
    """
    sensors = latest.get(topics["sensors"]) or {}
    twin = latest.get(topics["twin"]) or {}
    state = latest.get(topics["state"])

    def so(v, unit=""):
        return "chưa có" if v is None else f"{v}{unit}"

    def bat_tat(v):
        return "chưa rõ" if v is None else ("BẬT" if v else "TẮT")

    lines = ["📊 Trạng thái ao"]
    lines.append(f"Nhiệt độ: {so(sensors.get('temp'), ' °C')}")
    lines.append(f"pH: {so(sensors.get('ph'))}")
    lines.append(f"DO ước tính: {so(twin.get('do'), ' mg/L')} (bão hoà {so(twin.get('do_sat'), ' mg/L')})")
    lines.append(f"Mức: {twin.get('level', 'chưa có')}")
    if state is None:
        lines.append("Thiết bị: chưa nhận pond1/state từ ESP32")
    else:
        for device, ten in DEVICE_NAMES.items():
            text = f"{ten}: {bat_tat(state.get(device))}"
            left = pause_remaining.get(device, 0)
            if left > 0:
                text += f" (tay, tự động tạm dừng còn {int(left)} s)"
            lines.append(text)
    ts = twin.get("ts")
    if ts:
        now = time.time() if now is None else now
        lines.append(f"Cập nhật cách đây {int(now - ts)} s")
    return "\n".join(lines)


def parse_device_command(device, args):
    """Đọc tham số lệnh /aerator on|off và /pump on|off.

    Trả về (on, seconds); seconds luôn None (bơm bật tay chạy liên tục, server tự gia hạn).
    Sai cú pháp thì ValueError kèm hướng dẫn.
    """
    word = args[0].lower() if args else ""
    if word in ("on", "bat"):
        return True, None
    if word in ("off", "tat"):
        return False, None
    raise ValueError(f"Cách dùng: /{device} on  hoặc  /{device} off")


def read_env(path=ENV_PATH):
    """Đọc token và danh sách chat id từ .env. Thiếu thì trả (None, [])."""
    if not Path(path).exists():
        return None, []
    from dotenv import dotenv_values
    env = dotenv_values(path)
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    ids = []
    for part in (env.get("TELEGRAM_ALLOWED_CHAT_IDS") or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():  # chat id nhóm có dấu trừ
            ids.append(int(part))
    if not token or token.startswith("dan_") or not ids:  # còn giá trị giả của .env.example
        return None, []
    return token, ids


# =====================================================================
# 3. NỐI VỚI TELEGRAM
# =====================================================================
class TelegramBot:
    def __init__(self, token, chat_ids, handler, hub, alerts):
        self.token = token
        self.chat_ids = chat_ids
        self.handler = handler
        self.hub = hub
        self.alerts = alerts
        self.app = None
        self._offline_task = None
        self.topics = {
            "sensors": handler.topic_sensors,
            "twin": handler.topic_twin,
            "state": handler.topic_state,
        }

    # ---------- Bật / tắt (api.py gọi trong lifespan) ----------
    async def start(self):
        from telegram.ext import Application, CommandHandler, filters

        self.app = Application.builder().token(self.token).build()
        allowed = filters.Chat(chat_id=self.chat_ids)  # chat lạ: bot im lặng, không trả lời
        self.app.add_handler(CommandHandler("status", self._cmd_status, filters=allowed))
        self.app.add_handler(CommandHandler("aerator", self._cmd_aerator, filters=allowed))
        self.app.add_handler(CommandHandler("pump", self._cmd_pump, filters=allowed))
        self.app.add_handler(CommandHandler("start", self._cmd_help, filters=allowed))
        self.app.add_handler(CommandHandler("help", self._cmd_help, filters=allowed))

        await self.app.initialize()   # hỏi Telegram token có đúng không (cần Internet)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)  # bỏ lệnh cũ gửi lúc server tắt
        self.hub.listeners.append(self.on_hub_message)
        self._offline_task = asyncio.create_task(self._offline_loop())
        print(f"[tele ] Bot Telegram đã chạy, gửi tới {len(self.chat_ids)} chat")
        await self.send_all("🟢 Server ao cá đã khởi động.")

    async def stop(self):
        if self.on_hub_message in self.hub.listeners:
            self.hub.listeners.remove(self.on_hub_message)
        if self._offline_task:
            self._offline_task.cancel()
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    # ---------- Gửi ----------
    async def send_all(self, text):
        for chat_id in self.chat_ids:
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:  # mất mạng, chat id sai...: không làm sập server
                print(f"[tele ] Gửi Telegram lỗi: {type(e).__name__}")

    def _send_texts(self, texts):
        for text in texts:
            print(f"[tele ] {text}")
            asyncio.create_task(self.send_all(text))

    def on_hub_message(self, topic, data):
        """Hub gọi hàm này (trong vòng lặp web) cho mỗi tin mới."""
        if topic == self.topics["sensors"]:
            self._send_texts(self.alerts.on_sensors(data))
        elif topic == self.topics["twin"]:
            self._send_texts(self.alerts.on_twin(data))
        elif topic == self.topics["state"]:
            self._send_texts(self.alerts.on_state(data))

    async def _offline_loop(self):
        while True:
            await asyncio.sleep(OFFLINE_CHECK_PERIOD_S)
            self._send_texts(self.alerts.check_offline())

    # ---------- Lệnh ----------
    async def _cmd_help(self, update, context):
        await update.message.reply_text(
            "Lệnh:\n/status  xem số đo và thiết bị\n"
            "/aerator on|off  bật/tắt sục khí\n/pump on|off  bật/tắt bơm (chạy liên tục tới khi tắt)\n"
            "Điều khiển tay sẽ tạm dừng tự động một lúc."
        )

    async def _cmd_status(self, update, context):
        # Không có cooldown cho /status
        pause = self.handler.controller.pause_remaining()
        await update.message.reply_text(format_status(self.hub.latest, self.topics, pause))

    async def _cmd_aerator(self, update, context):
        await self._device_cmd(update, "aerator", context.args)

    async def _cmd_pump(self, update, context):
        await self._device_cmd(update, "pump", context.args)

    async def _device_cmd(self, update, device, args):
        try:
            on, seconds = parse_device_command(device, args)
            payload = self.handler.controller.manual_command(device, on, seconds)
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
        except ConnectionError:
            await update.message.reply_text("Server chưa nối được broker MQTT, lệnh chưa gửi.")
            return
        await update.message.reply_text(
            f"Đã gửi lệnh {payload}. Chờ ESP32 xác nhận (sẽ có tin khi thiết bị đổi trạng thái)."
        )


def create_bot(handler, hub, config, env_path=ENV_PATH):
    """Tạo bot nếu .env có token thật, không thì trả None (server chạy không Telegram)."""
    token, chat_ids = read_env(env_path)
    if token is None:
        print("[tele ] Không có token/chat id trong .env: chạy không Telegram")
        return None
    return TelegramBot(token, chat_ids, handler, hub, AlertManager(config))
