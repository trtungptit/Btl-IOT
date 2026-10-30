# btl IOT: Digital Twin cho ao nuôi cá rô phi (thùng xốp thu nhỏ)

Bài tập lớn môn IoT. Hệ thống đo nhiệt độ, pH, TDS của một thùng xốp chứa nước, ước lượng DO (oxy hoà tan), tự bật máy sủi oxy và bơm nước ngọt khi vượt ngưỡng, hiển thị mô hình 3D và gửi cảnh báo qua Telegram.

## Cách hệ thống hoạt động

Cảm biến → ESP32 → MQTT (Mosquitto) → dịch vụ Python trên laptop (tính DO, quyết định, lưu SQLite) → lệnh quay ngược về ESP32 → relay → máy sủi và bơm. Mô hình 3D và Telegram nhận kết quả từ dịch vụ Python.

## Cấu trúc thư mục

| Thư mục | Nội dung | Phụ trách |
|---|---|---|
| `config/` | Ngưỡng cảnh báo, tham số mô hình DO | Cả nhóm |
| `firmware/` | Code ESP32 (PlatformIO) | TV1 |
| `server/` | Dịch vụ Python: MQTT, SQLite, mô hình DO, điều khiển, Telegram, API | TV2, TV4 |
| `simulator/` | Giả lập ESP32 khi chưa có phần cứng | TV2 |
| `web/` | Giao diện: mô hình 3D, biểu đồ | TV3 |
| `mosquitto/` | Cấu hình broker MQTT | TV2 |
| `docs/` | Tài liệu, sơ đồ, nhật ký thử nghiệm | TV4 |
| `data/` | Chứa `pond.db` khi chạy (không đưa lên Git) | |

## Cài đặt

1. Cài Mosquitto và chạy với `mosquitto/mosquitto.conf`.
2. Server: `cd server`, tạo môi trường ảo, `pip install -r requirements.txt`.
3. Sao chép `.env.example` thành `.env` và điền token Telegram, chat ID.
4. Firmware: sao chép `firmware/include/secrets.h.example` thành `secrets.h`, điền WiFi và địa chỉ IP laptop, rồi nạp bằng PlatformIO.

## Quy ước làm việc nhóm

- Không đưa `.env`, `secrets.h`, `data/*.db` lên Git.
- Mỗi người làm trên một nhánh riêng, gộp vào `main` bằng pull request.
- `docs/mqtt.md` và `config/config.json` là hợp đồng chung: muốn sửa phải báo cả nhóm.
- File Blender gốc (`.blend`) để trên Drive, chỉ đưa file `.glb` đã xuất vào `web/models/`.
