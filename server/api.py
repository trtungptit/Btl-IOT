"""Web API cho trình duyệt: WebSocket thời gian thực + vài địa chỉ HTTP + phục vụ thư mục web/.

Luồng: tin MQTT (luồng paho) -> Hub.publish_from_thread -> luồng asyncio của web -> WebSocket /ws

  WS   /ws                    đẩy mọi tin sensors, twin, state: {"topic": ..., "data": ...}
  GET  /api/config            ngưỡng/nhãn cho giao diện (chỉ đọc, chỉ vài mục an toàn)
  GET  /api/status            số đo, twin, state mới nhất
  GET  /api/history?minutes=  số đo trong N phút gần nhất (cho biểu đồ)
  POST /api/cmd/{device}      điều khiển tay: {"on": true, "seconds": 5}
  GET  /                      các file trong thư mục web/

Giao diện chỉ đổi trạng thái thiết bị khi nhận pond1/state, vì vậy POST /api/cmd
chỉ trả {"sent": true} chứ không trả trạng thái mới.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import control, db

DEFAULT_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
QUEUE_SIZE = 100  # mỗi trình duyệt có hàng đợi riêng; đầy (trình duyệt quá chậm) thì bỏ tin mới

# Chỉ các mục này của config.json được đưa ra trình duyệt (danh sách cho phép).
# Mục nào cần thêm cho giao diện thì ghi vào đây; token/bí mật không nằm trong config nhưng
# vẫn giữ nguyên tắc "chỉ đưa ra thứ cần dùng".
WEB_CONFIG_KEYS = ("sensors", "do_model", "pump", "pond_geometry_m")


class Hub:
    """Cầu nối từ luồng MQTT sang các WebSocket đang mở."""

    def __init__(self):
        self.loop = None     # vòng lặp asyncio của web (gắn lúc server khởi động)
        self.clients = set() # mỗi WebSocket đang mở có một asyncio.Queue
        self.latest = {}     # topic -> data của tin mới nhất
        self.listeners = []  # hàm f(topic, data) chạy trong vòng lặp web (vd bot Telegram)

    def publish_from_thread(self, topic, data):
        """Gọi từ luồng MQTT. Không đụng WebSocket trực tiếp mà nhờ vòng lặp web làm."""
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._dispatch, topic, data)

    def _dispatch(self, topic, data):
        """Chạy trong vòng lặp web: nhớ tin mới nhất và chia cho mọi WebSocket."""
        self.latest[topic] = data
        message = {"topic": topic, "data": data}
        for queue in list(self.clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass
        for listener in list(self.listeners):
            try:
                listener(topic, data)
            except Exception as e:  # lỗi ở bot không được làm hỏng WebSocket
                print(f"[lỗi  ] listener thất bại: {e!r}")


class CmdBody(BaseModel):
    on: bool
    seconds: int | None = None  # bơm bật thì bắt buộc


def create_app(handler, hub, web_dir=DEFAULT_WEB_DIR, start_mqtt=False, bot=None):
    """Tạo ứng dụng FastAPI.

    handler: MqttHandler (cần .controller, .db_path và tên chủ đề)
    hub: Hub (đã được gắn vào handler.on_event)
    web_dir: thư mục giao diện; None thì không phục vụ file tĩnh (dùng khi test)
    start_mqtt: True thì khởi động MQTT khi server lên, dừng khi server tắt
    bot: TelegramBot hoặc None. Bot lỗi lúc bật (sai token, mất mạng) thì server vẫn chạy.
    """

    @asynccontextmanager
    async def lifespan(app):
        hub.loop = asyncio.get_running_loop()
        bot_ok = False
        if bot is not None:
            try:
                await bot.start()
                bot_ok = True
            except Exception as e:
                print(f"[tele ] Không bật được bot Telegram ({type(e).__name__}), chạy tiếp không Telegram")
        if start_mqtt:
            handler.start_background()
        yield
        if start_mqtt:
            handler.stop()
        if bot_ok:
            await bot.stop()

    app = FastAPI(title="Digital Twin ao ca", lifespan=lifespan)

    @app.middleware("http")
    async def no_cache_web(request, call_next):
        """Bắt trình duyệt hỏi lại server mỗi lần tải app.js/index.html (không dùng bản cũ đã lưu).

        Lý do (10/11/2026): sửa web xong, trình duyệt vẫn chạy app.js cũ -> nút "Bơm 5 giây" cũ
        bật bơm mà không có nút tắt. "no-cache" vẫn cho lưu, nhưng phải hỏi server file còn mới không.
        """
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # ---------- WebSocket ----------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        hub.clients.add(queue)
        try:
            # Tin mới nhất gửi ngay để trang vừa mở không phải chờ 5 giây
            for topic, data in list(hub.latest.items()):
                await ws.send_json({"topic": topic, "data": data})
            while True:
                await ws.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass  # trình duyệt đã đóng
        finally:
            hub.clients.discard(queue)

    # ---------- HTTP ----------
    @app.get("/api/config")
    async def get_config():
        """Ngưỡng và nhãn từ config.json để giao diện không phải viết cứng số. Chỉ đọc."""
        return {key: handler.config[key] for key in WEB_CONFIG_KEYS if key in handler.config}

    @app.get("/api/status")
    async def status():
        """Số đo, twin, trạng thái thiết bị THẬT (từ pond1/state) mới nhất. Chưa có thì null."""
        return {
            "sensors": hub.latest.get(handler.topic_sensors),
            "twin": hub.latest.get(handler.topic_twin),
            "state": hub.latest.get(handler.topic_state),
            # Giây còn tạm dừng tự động sau lệnh tay (0 = đang tự động)
            "auto_pause_s": handler.controller.pause_remaining(),
        }

    @app.get("/api/history")
    def history(minutes: int = Query(60, ge=1, le=10080)):
        """Số đo trong `minutes` phút gần nhất (tính theo giờ server lúc nhận tin), cũ nhất đứng đầu."""
        since = int(time.time()) - minutes * 60
        return {"minutes": minutes, "readings": db.get_readings_since(since, handler.db_path)}

    @app.post("/api/cmd/{device}")
    def command(device: str, body: CmdBody):
        """Điều khiển tay. Chỉ báo đã gửi; trạng thái mới đến từ pond1/state."""
        if device not in control.DEVICES:
            raise HTTPException(status_code=404, detail=f"Không có thiết bị {device!r}")
        try:
            handler.controller.manual_command(device, body.on, body.seconds)
        except ValueError as e:  # vd bật bơm mà thiếu seconds
            raise HTTPException(status_code=422, detail=str(e))
        except ConnectionError as e:  # chưa nối được broker
            raise HTTPException(status_code=503, detail=str(e))
        return {"sent": True}

    # Phải đặt CUỐI CÙNG: "/" khớp mọi đường dẫn còn lại nên đặt trước sẽ che mất /api và /ws
    if web_dir is not None:
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
