# Kịch bản thử hệ thống

Mỗi dòng: làm gì → mong đợi gì → ghi kết quả thật (Đạt / Không đạt + ghi chú, ngày).
Chạy hệ thống theo mục "Chạy toàn hệ thống" trong README trước.

## A. Thử bằng simulator (không cần phần cứng)

| # | Làm gì | Mong đợi | Kết quả |
|---|---|---|---|
| A1 | `python -m pytest server/tests` | Tất cả passed (23/11/2026: 70 passed) | |
| A2 | Bật broker, server, simulator; mở web | Thẻ nhiệt độ, pH, DO cập nhật mỗi 5 s; không có thẻ TDS | |
| A3 | Bấm "Bật sục khí" trên web | Nút hiện "Đang chờ ESP32...", rồi "Đang BẬT (điều khiển tay, tự động tạm dừng còn ~300 s)"; 3D có bong bóng | |
| A4 | Telegram gõ `/status` | Bot trả số đo, DO, trạng thái thiết bị | |
| A5 | Chat lạ (không có trong `.env`) gõ `/status` | Bot không trả lời | |
| A6 | Simulator gõ `n` vài lần cho nhiệt độ > 31 °C | Telegram "Bơm làm mát BẬT" (chỉ báo "Nhiệt độ cao" khi > 36 °C); bơm chạy liên tục tới khi < 30,5 °C rồi "TẮT"; bấm "Tắt bơm" giữa chừng thì tắt được | |
| A7 | Để nguyên ~4 phút ở 25 °C (xem mục C) | DO tụt dưới 3 → Telegram "DO ước tính thấp" → sục khí tự bật; DO lên trên 5,5 → tự tắt | |
| A8 | Tắt simulator (Ctrl+C), chờ > 60 s | Telegram "Lỗi hệ thống: không nhận được số đo từ ESP32" | |
| A9 | Bật lại simulator | Telegram "ESP32 đã gửi số đo trở lại" | |
| A10 | Tắt server khi web đang mở | Web báo "Mất kết nối, đang thử nối lại..."; bật lại thì tự nối | |

## B. Thử với ESP32 thật

| # | Làm gì | Mong đợi | Kết quả |
|---|---|---|---|
| B1 | Nạp `pond_node.ino`, mở Serial 115200 | In IP ESP32, "MQTT đã nối", mỗi 5 s in nhiệt độ + pH | |
| B2 | Web bấm "Bật sục khí" (relay mới nối đèn/tải thử) | Relay K1 tách, đèn sáng; web đổi "Đang BẬT" chỉ sau khi ESP32 báo state | |
| B3 | Web bấm "Bật bơm", sau đó "Tắt bơm" | Relay K2 đóng liên tục tới khi bấm tắt | |
| B3b | Đang bật bơm, tắt server (Ctrl+C) | Khoảng 15 s sau ESP32 tự tắt bơm (hết gia hạn) | |
| B4 | Rút dây mạng/tắt broker > 60 s | Serial "MẤT MQTT quá 60 s: chế độ an toàn (bơm TẮT, sủi BẬT)"; K1 đóng, K2 nhả | |
| B5 | Bật lại broker | ESP32 nối lại, gửi state ngay; server điều khiển tiếp | |
| B6 | Cắm ESP32 lúc relay đang nối | Relay không giật lúc khởi động | |
| B7 | Nối máy sủi, bơm thật (nguồn riêng 5V B qua relay) | Như B2, B3 nhưng thiết bị chạy thật | |
| B8 | Demo: đổ nước ấm | Nhiệt độ tăng, DO bão hoà giảm, DO giảm nhanh hơn → sục khí bật; > 31 °C → bơm làm mát | |

## C. Thời gian demo với tham số hiện tại (config.json)

DO là giá trị **ước tính** bằng mô hình, không có cảm biến DO.

09/11/2026, nhóm đồng ý tăng tiêu hao oxy 0,006 → 0,024 mg/L/s và `aerator_k_per_s` 0,01 → 0,04 để demo nhanh
(tiêu hao gấp 4, sục khí mạnh gấp 4 → mức DO cân bằng khi sục giữ nguyên, chỉ nhanh hơn).
23/11/2026: ngưỡng bật sục đổi 4,0 → 3,0 mg/L (QCVN 02-26:2017/BNNPTNT), tắt vẫn > 5,5.
Kết quả mô phỏng bằng `do_model.step`, bước 5 s:

| Nhiệt độ | Lần đầu cần sục sau | Sục bật | Sục nghỉ | DO cân bằng khi sục (> 5,5 mới tự tắt) |
|---|---|---|---|---|
| 24,3 °C | 235 s ≈ 4 phút | 20 s | 125 s | 7,80 |
| 27 °C | 185 s | 20 s | 105 s | 7,28 |
| 30 °C | 135 s | 30 s | 80 s | 6,71 |
| 32 °C | 115 s | 35 s | 70 s | 6,33 |
| 35 °C | 85 s | 55 s | 55 s | 5,75 |

- Tiêu hao oxy giả định: `uptake_ref_mg_per_l_per_s` = 0,024 mg/L/s ở 25 °C; cứ tăng 10 °C thì nhân 2 (`uptake_q10`).
- DO cân bằng khi sục = bão hoà − tiêu thụ / k. Nếu số này < 5,5 thì máy sục không bao giờ tự tắt;
  với k = 0,04 vẫn > 5,5 đến 35 °C. Đổi lại phải tính lại bảng này.

Lưu ý: vừa bật server thì DO coi như bão hoà, nên khoảng 1,5–4 phút đầu máy sục **chưa tự bật** là đúng.
Bơm chỉ tự chạy khi nhiệt độ > 31 °C. Muốn thử relay ngay thì dùng nút trên web hoặc `/aerator on`, `/pump on`.
