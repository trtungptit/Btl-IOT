# btl IOT: Digital Twin cho ao nuôi cá rô phi (thùng xốp thu nhỏ)

Bài tập lớn môn IoT. Hệ thống đo nhiệt độ, pH của một thùng xốp chứa nước, ước lượng DO (oxy hoà tan, giá trị ước tính bằng mô hình, không có cảm biến DO), tự bật máy sủi oxy khi DO thấp và bơm nước mát khi nước quá nóng, hiển thị mô hình 3D và gửi cảnh báo qua Telegram.

## Cách hệ thống hoạt động

Cảm biến → ESP32 → MQTT (Mosquitto) → dịch vụ Python trên laptop (tính DO, quyết định, lưu SQLite) → lệnh quay ngược về ESP32 → relay → máy sủi và bơm. Mô hình 3D và Telegram nhận kết quả từ dịch vụ Python.

## Cấu trúc thư mục

| Thư mục | Nội dung | Phụ trách |
|---|---|---|
| `config/` | Ngưỡng cảnh báo, tham số mô hình DO | Cả nhóm |
| `firmware/` | Code ESP32 (Arduino IDE) | TV1 |
| `server/` | Dịch vụ Python: MQTT, SQLite, mô hình DO, điều khiển, Telegram, API | TV2, TV4 |
| `simulator/` | Giả lập ESP32 khi chưa có phần cứng | TV2 |
| `web/` | Giao diện: mô hình 3D, biểu đồ | TV3 |
| `mosquitto/` | Cấu hình broker MQTT | TV2 |
| `docs/` | Tài liệu, sơ đồ, nhật ký thử nghiệm | TV4 |
| `data/` | Chứa `pond.db` khi chạy (không đưa lên Git) | |

## Cài đặt (làm một lần)

1. Cài Mosquitto 2.x. Mở Windows Firewall cổng 1883 (để ESP32 qua WiFi nối được).
2. Ở thư mục gốc repo (Git Bash):
   ```bash
   python -m venv .venv                        # tạo môi trường ảo
   source .venv/Scripts/activate               # bật môi trường ảo
   pip install -r server/requirements.txt      # cài thư viện server
   ```
3. Sao chép `.env.example` thành `.env` và điền token Telegram, chat ID (nhiều chat cách nhau dấu phẩy).
   Không có `.env` thì server vẫn chạy, chỉ không có Telegram.
4. Firmware: sao chép `firmware/pond_node/secrets.h.example` thành `secrets.h`, điền WiFi và địa chỉ IP laptop,
   cài thư viện OneWire, DallasTemperature, PubSubClient, ArduinoJson 7.x rồi nạp `pond_node.ino` bằng Arduino IDE.

## Chạy toàn hệ thống (mỗi lần demo, 3 cửa sổ terminal, đều ở thư mục gốc repo)

| Cửa sổ | Lệnh | Kết quả mong đợi |
|---|---|---|
| 1. Broker | `"/c/Program Files/mosquitto/mosquitto" -c mosquitto/mosquitto.conf -v` | `Opening ipv4 listen socket on port 1883` |
| 2. Server | `source .venv/Scripts/activate && python -m server.main` | `[mqtt ] Đã nối broker`, có `[tele ] Bot Telegram đã chạy` nếu có `.env` |
| 3a. Có phần cứng | Cắm ESP32, mở Serial Monitor 115200 | `MQTT đã nối`, mỗi 5 s in nhiệt độ, pH |
| 3b. Chưa có phần cứng | `source .venv/Scripts/activate && python simulator/simulator.py` | mỗi 5 s gửi số đo giả |

Mở web: http://localhost:8000. Telegram: gõ `/status`, `/aerator on`, `/aerator off`, `/pump on`, `/pump off`.
Chạy test: `python -m pytest server/tests`. Kịch bản thử từng bước: `docs/test-results/kich-ban-thu.md`.

Lưu ý: không chạy hai server cùng lúc (cùng tên client MQTT `pond1-server`, broker sẽ đá nhau).

## Quy ước làm việc nhóm

- Không đưa `.env`, `secrets.h`, `data/*.db` lên Git.
- Mỗi người làm trên một nhánh riêng, gộp vào `main` bằng pull request.
- `docs/mqtt.md` và `config/config.json` là hợp đồng chung: muốn sửa phải báo cả nhóm.
- File Blender gốc (`.blend`) để trên Drive, chỉ đưa file `.glb` đã xuất vào `web/models/`.
