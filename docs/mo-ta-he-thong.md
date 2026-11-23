# Mô tả hệ thống (dùng cho báo cáo)

## 1. Luồng dữ liệu

```
DS18B20 (GPIO4), pH (GPIO34) → ESP32 ──MQTT──► Mosquitto (laptop :1883) ──► server Python
                                  ▲                                          │  ├─ SQLite (data/pond.db)
                    relay K1/K2 ◄─┘ pond1/cmd/aerator, pond1/cmd/pump ◄──────┤  ├─ WebSocket → web 3D + biểu đồ
                   (máy sủi, bơm)                                            │  └─ Telegram bot
```

Chủ đề và JSON: xem `docs/mqtt.md`. Mọi ngưỡng: `config/config.json`.

## 2. Các khối phần mềm

| Khối | File | Việc chính |
|---|---|---|
| Firmware | `firmware/pond_node/pond_node.ino` | Đo mỗi 5 s, nhận lệnh, báo `pond1/state`, bơm tự tắt theo số giây, failsafe |
| Nhận tin | `server/mqtt_handler.py` | Kiểm tra số đo theo `valid_range`, tính DO, tính mức cảnh báo, gửi `pond1/twin` |
| Mô hình DO | `server/do_model.py` | DO bão hoà (Benson-Krause, nước ngọt) + sục khí − tiêu hao oxy giả định R(T) |
| Điều khiển | `server/control.py` | Sục khí có trễ (3 / 5,5 mg/L), bơm làm mát liên tục có gia hạn 15 s, tạm dừng tự động sau lệnh tay |
| Lưu trữ | `server/db.py` | Bảng `readings` (có cột `extra` JSON) và `device_events` |
| Web API | `server/api.py` | `/ws`, `/api/status`, `/api/history`, `/api/config`, `/api/cmd/{device}` |
| Telegram | `server/telegram_bot.py` | Cảnh báo theo sự kiện + cooldown 600 s, báo hồi phục, báo ESP32 im lặng; `/status`, `/aerator`, `/pump` |
| Giao diện | `web/app.js` | Thùng 3D (màu nước theo mức, bong bóng khi sục, dòng nước khi bơm), biểu đồ có đường ngưỡng |
| Giả lập | `simulator/simulator.py` | Thay ESP32 khi chưa có phần cứng |

## 3. Quy tắc an toàn

- Lệnh bơm luôn kèm số giây; ESP32 tự tắt bơm khi hết giờ kể cả mất mạng, và chặn tối đa 30 s/lần.
- Giao diện và Telegram chỉ coi thiết bị đã đổi khi ESP32 báo `pond1/state`.
- ESP32 mất MQTT quá 60 s: bơm tắt (không tràn thùng), máy sủi bật (giữ oxy).
- Relay đặt mức TẮT trước khi bật chân OUTPUT để không giật lúc khởi động.
- Máy sủi, bơm dùng nguồn 5V riêng qua tiếp điểm relay, không lấy từ chân ESP32.

## 4. Giới hạn (ghi trong báo cáo)

- DO là giá trị **ước tính** bằng mô hình, không có cảm biến DO. Hệ số `aerator_k_per_s` và
  tốc độ tiêu hao oxy giả định (thùng không nuôi cá) được chỉnh để demo thấy rõ, không phải số đo thật.
- DO bão hoà tính cho nước ngọt (độ mặn = 0), không bù độ mặn. Đã bỏ cảm biến TDS (08/11/2026).
- pH hiệu chuẩn 2 điểm (4,01 và 9,18), chưa kiểm tra độ thẳng bằng điểm thứ ba.
- Mô hình không có phần oxy khuếch tán tự nhiên qua mặt nước khi tắt sục khí.
