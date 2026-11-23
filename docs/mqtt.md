# Hợp đồng MQTT (cả nhóm phải đồng ý trước khi đổi)

Broker Mosquitto, cổng 1883, nội dung là JSON. Chữ đầu `pond1` lấy từ `mqtt.base_topic` trong config/config.json.

## 1. Các chủ đề hiện có (version 1)

| Chủ đề            | Ai gửi         | Nội dung                                                           |
| ----------------- | -------------- | ------------------------------------------------------------------ | ------- | -------- |
| pond1/sensors     | ESP32, mỗi 5 s | {"v":1, "ts":..., "temp":..., "ph":...}                            |
| pond1/cmd/aerator | server         | {"on": true}                                                       |
| pond1/cmd/pump    | server         | {"on": true, "seconds": 5}                                         |
| pond1/state       | ESP32          | {"v":1, "aerator": true, "pump": false, "fw": "0.1.0"}             |
| pond1/twin        | server         | {"v":1, "ts":..., "do":..., "do_sat":..., "level": "ok/warning/danger"} |

Lệnh bơm luôn kèm `seconds`. Giao diện chỉ đổi trạng thái thiết bị khi nhận `pond1/state`.

**Đã bỏ TDS (08/11/2026):** ESP32 không gửi khoá `tds` nữa; config để sensor `tds` `"enabled": false`
(không xoá). Bên nhận vốn chịu được khoá thiếu nên không tăng `v`. `sampling.tds_settle_s` giữ lại nhưng
không dùng. DO bão hoà tính cho nước ngọt (độ mặn = 0).

**Đã bỏ khoá số con cá trong `pond1/twin` (23/11/2026):** thùng không nuôi cá thật; mô hình DO dùng tốc độ
tiêu hao oxy giả định (`do_model.uptake_ref_mg_per_l_per_s`) thay cho số con cá. Đây là ngoại lệ của quy ước 1
(không xoá khoá), cả nhóm cần biết. Bên nhận vốn chịu được khoá thiếu nên không tăng `v`.

## 2. Quy ước mở rộng

1. Chỉ thêm, không đổi tên, không xoá. Thêm số đo mới = thêm khoá mới vào JSON. Khoá cũ giữ nguyên tên và đơn vị.
2. Bên nhận bỏ qua khoá lạ và chịu được khoá thiếu. Python dùng `data.get("temp")`, JavaScript kiểm tra `undefined`, ESP32 kiểm tra `containsKey`.
3. Trường `v` là số phiên bản định dạng, hiện là 1. Chỉ tăng khi buộc phải đổi kiểu dữ liệu khoá cũ, và đổi thì cả nhóm họp.
4. Danh sách cảm biến khai báo trong config.json, mục `sensors`. `id` trùng tên khoá trong JSON. Server đọc danh sách này để kiểm tra khoảng hợp lệ, so ngưỡng, vẽ biểu đồ, gửi cảnh báo; không viết cứng temp, ph, tds ở nhiều chỗ.
5. Đơn vị cố định theo `unit` trong config.
6. Tên khoá không được trùng. `level` trong pond1/twin là mức cảnh báo; mực nước sau này phải đặt tên `water_level_cm`.

## 3. Ví dụ thêm cảm biến sau này (chưa làm, chỉ minh hoạ)

Thêm cảm biến mực nước:

- ESP32 gửi thêm khoá: {"v":1, "ts":..., "temp":..., "ph":..., "water_level_cm": 22.5}
- config.json thêm một mục vào `sensors`: {"id":"water_level_cm","label":"Mực nước","unit":"cm","enabled":true,"valid_range":[0,40],"warn_range":[15,28]}
- pins.h thêm một dòng khai báo chân.
- Mô hình 3D có sẵn hàm `setWaterLevel(tỉ lệ)`: có số đo thì gọi, chưa có thì dùng `pond_geometry_m.water_level_fixed_m`.
- Bên nhận cũ vẫn chạy bình thường nhờ quy tắc 2.

## 4. Cơ sở dữ liệu

Bảng `readings` có các cột cố định `ts, temp, ph, tds, do_est` (cột `tds` giữ lại, nay luôn NULL) và một cột `extra` kiểu văn bản chứa JSON cho mọi số đo khác. Thêm cảm biến mới không phải sửa cấu trúc bảng.

## 5. Ghi chú các trường

- `ts`: giây Unix.
- `temp`: °C, đọc từ DS18B20.
- `ph`: đơn vị pH, đã hiệu chuẩn.
- `tds`: ppm, đã bù nhiệt. KHÔNG CÒN GỬI từ 08/11/2026 (giữ dòng này để đọc dữ liệu cũ).
- Bơm tự tắt sau `seconds` giây, kể cả khi mất kết nối.
