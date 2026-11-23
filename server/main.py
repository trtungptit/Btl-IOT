"""Chạy toàn bộ server trong MỘT tiến trình: MQTT handler + web (FastAPI/uvicorn).

Chạy từ thư mục gốc repo, .venv đã kích hoạt:
    python -m server.main
Rồi mở http://localhost:8000
"""

import sys

import uvicorn

from server import api, db

# Windows mặc định in bằng bảng mã cp1252 (không có chữ tiếng Việt) -> print("Đang nối...") làm sập server.
# Ép in bằng UTF-8; ký tự nào terminal không hiện được thì thay bằng "?" chứ không báo lỗi.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from server.mqtt_handler import MqttHandler, load_config
from server.telegram_bot import create_bot

# Địa chỉ web. 127.0.0.1 = chỉ máy này truy cập được.
# Muốn điện thoại cùng WiFi xem được thì đổi thành "0.0.0.0".
WEB_HOST = "127.0.0.1"
WEB_PORT = 8000


def main():
    config = load_config()
    db.init_db()

    hub = api.Hub()
    # Mỗi tin MQTT đi qua on_event -> hub, hub chuyển sang luồng web một cách an toàn
    handler = MqttHandler(config, on_event=hub.publish_from_thread)
    bot = create_bot(handler, hub, config)  # None nếu .env chưa có token thật
    app = api.create_app(handler, hub, start_mqtt=True, bot=bot)  # MQTT chạy luồng riêng (loop_start)

    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    main()
