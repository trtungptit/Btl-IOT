# CLAUDE.md – Digital Twin ao nuôi cá rô phi (thùng xốp thu nhỏ)

Bài tập lớn môn IoT, nhóm 4 người (TV1–TV4), hiện một người (Tùng) làm cả bốn phần.
Người dùng là sinh viên mới học: giải thích ngắn, dễ hiểu, bằng tiếng Việt. Mỗi lệnh
đưa ra phải kèm giải thích lệnh đó làm gì. Làm từng bước nhỏ, mỗi bước có cách kiểm tra đạt.

## 1. Hệ thống làm gì

ESP32 đo nhiệt độ, pH, TDS trong thùng xốp, gửi qua MQTT lên laptop. Server Python
lưu dữ liệu, ước tính oxy hoà tan (DO) bằng mô hình, tự bật/tắt máy sục khí và bơm,
đẩy dữ liệu lên web (mô hình 3D + biểu đồ) và gửi cảnh báo Telegram.

Luồng: ESP32 → Mosquitto (laptop, cổng 1883) → server/ (FastAPI) → SQLite + WebSocket → web/
                                                       ↘ Telegram bot

## 2. Công nghệ đã chốt (không tự ý đổi)

- Firmware: Arduino IDE (KHÔNG dùng PlatformIO). Sketch ở `firmware/pond_node/pond_node.ino`
  (tên thư mục phải trùng tên file .ino). Chân khai báo trong `pins.h`.
- Server: Python 3.11+ trong `.venv`, paho-mqtt, FastAPI + WebSocket, uvicorn, SQLite,
  python-telegram-bot, python-dotenv, numpy, pytest. Thư viện ghi trong `server/requirements.txt`.
- Broker: Mosquitto 2.x, cấu hình trong `mosquitto/`, chạy từ thư mục gốc repo.
- Web: Three.js (mô hình .glb làm bằng Blender) + Chart.js.
- Máy chính: Windows, terminal Git Bash trong VS Code (dùng `/` trong đường dẫn, không dùng `\`).

## 3. Hợp đồng chung (đổi phải báo cả nhóm)

- `docs/mqtt.md`: chủ đề và định dạng JSON. Đọc file này trước khi viết code MQTT.
  - `pond1/sensors` (ESP32 gửi mỗi 5 s), `pond1/cmd/aerator`, `pond1/cmd/pump`,
    `pond1/state` (ESP32 báo trạng thái thật), `pond1/twin` (server gửi DO ước tính, mức cảnh báo).
- `config/config.json`: mọi ngưỡng và tham số. KHÔNG viết cứng số ngưỡng trong code,
  luôn đọc từ config. Danh sách cảm biến lấy từ mục `sensors`, không viết cứng temp/ph/tds rải rác.
- Nếu thay đổi cần sửa một trong hai file trên: dừng lại, nói rõ là "cần nhóm đồng ý", đừng tự sửa.

Quy ước mở rộng: chỉ thêm khoá mới, không đổi tên/xoá khoá cũ; bên nhận bỏ qua khoá lạ và chịu
được khoá thiếu (`data.get(...)`); trường `v` là phiên bản định dạng; `level` đã dùng cho mức
cảnh báo, số đo mực nước phải tên `water_level_cm`; bảng SQLite có cột `extra` (JSON) cho số đo mới.
ESP32 không đọc config.json, tiền tố topic trong firmware là một `#define` riêng.

## 4. Quy tắc nghiệp vụ bắt buộc

- Lệnh bơm luôn kèm số giây: `{"on": true, "seconds": 5}`. Không bao giờ gửi lệnh bơm vô thời hạn.
- Giao diện chỉ đổi trạng thái thiết bị khi nhận `pond1/state` từ ESP32, không đổi ngay khi bấm nút.
- Sục khí có trễ (hysteresis): bật khi DO < `aerator_on_below`, tắt khi DO > `aerator_off_above`.
- Bơm làm mát: bật theo đợt `burst_s`, chờ `wait_s`, tối đa `max_s_per_hour` giây mỗi giờ.
- Người dùng điều khiển tay thì tạm dừng tự động `manual_pause_s` giây.
- ESP32 mất kết nối quá `esp32_offline_failsafe_s` thì tự tắt thiết bị (an toàn).
- Telegram: chỉ gửi khi có sự kiện (vượt ngưỡng, thiết bị đổi trạng thái, lỗi hệ thống,
  hồi phục), mỗi loại cảnh báo cách nhau ít nhất `telegram.cooldown_s` (600 s).
  Lệnh `/status` không bị cooldown và chỉ trả lời chat id được phép.

## 5. Mô hình DO (oxy hoà tan, là giá trị ƯỚC TÍNH, không có cảm biến DO)

    DO(t+1) = DO(t) + k_sục × (DO_bão_hoà(T) − DO(t)) − R_cá_ảo(T)

- Cá ảo (`do_model.fish_count`) là nguồn tiêu thụ oxy. Không có tiêu thụ thì DO không bao giờ
  xuống dưới ngưỡng bật sục khí.
- DO_bão_hoà theo nhiệt độ (mg/L): 20°C 9.09, 25°C 8.26, 30°C 7.56, 35°C 6.95 (nội suy).
- k_sục và R chỉnh ở bước thử nghiệm để demo thấy rõ. Báo cáo phải ghi DO là ước tính.
- Demo: đổ nước ấm vào thùng → nhiệt độ tăng → DO bão hoà giảm, cá tiêu thụ nhiều hơn → DO giảm
  → bật sục khí; nhiệt độ vượt ngưỡng → bơm làm mát.
- Code nằm ở `server/do_model.py`, có test ở `server/tests/test_do_model.py`.

## 6. Ngưỡng (số trong config là ví dụ của nhóm)

Nhiệt độ cảnh báo 26–32°C, pH 6.5–8.5, DO bật sục < 4, tắt > 5.5 mg/L.
TDS 800 ppm là ngưỡng vận hành nhóm tự đặt sau khi đo nước nền, không phải chuẩn sinh học.
Không tự bịa thông số cảm biến hay ngưỡng mới; nếu cần thì ghi "cần đối chiếu nhãn/tài liệu".

## 7. Bí mật và Git (rất quan trọng)

- Token Telegram và chat id nằm trong `.env` (server) và `firmware/pond_node/secrets.h` (WiFi, ESP32).
  KHÔNG đọc, in, sao chép hay commit hai file này. Chỉ sửa `.env.example` (giá trị giả).
- `.gitignore` phải luôn có: `.env`, `firmware/pond_node/secrets.h`, `.venv/`, `__pycache__/`,
  `data/*`, `!data/.gitkeep`.
- Trước khi commit, kiểm tra: `git ls-files | grep -E "(^|/)\.env$|secrets\.h$|\.venv"` phải trống.
- KHÔNG dùng `git push --force`, `git reset --hard` hay xoá lịch sử. Không commit/push khi chưa
  được người dùng đồng ý.

## 8. Cấu trúc thư mục

    config/config.json        tham số dùng chung
    docs/mqtt.md              hợp đồng MQTT
    firmware/pond_node/       sketch ESP32 (pond_node.ino, pins.h, secrets.h)
    mosquitto/                cấu hình broker
    server/                   main.py, mqtt_handler.py, db.py, do_model.py, control.py,
                              api.py, telegram_bot.py, telegram_test.py, tests/
    simulator/                giả lập ESP32 để test khi chưa có phần cứng
    web/                      giao diện 3D + biểu đồ
    data/                     file SQLite (không commit)

## 9. Cách làm việc

- Giữ đúng phạm vi bài: không thêm tính năng ngoài kế hoạch (camera, cloud, đăng nhập, app di động...)
  trừ khi người dùng yêu cầu.
- Khi gỡ lỗi, kiểm tra từ dễ đến khó: nguồn điện/dây nối → cấu hình (cổng, IP, topic, đường dẫn)
  → code.
- An toàn điện: bơm/sục khí dùng nguồn riêng qua relay hoặc MOSFET, không cấp từ chân ESP32;
  tách phần điện khỏi nước.
- Chạy server từ thư mục gốc repo với `.venv` đã kích hoạt. Chạy test: `python -m pytest server/tests`.
- Ưu tiên code đơn giản, dễ đọc cho người mới; giải thích ngắn sau mỗi thay đổi.
