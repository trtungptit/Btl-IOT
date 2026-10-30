# Hợp đồng MQTT

Cả firmware, server và web đều theo file này. Muốn đổi phải báo cả nhóm.

Broker: Mosquitto, cổng 1883, chạy trên laptop. Mọi bản tin là JSON UTF-8.

| Chủ đề | Hướng | Nội dung |
|---|---|---|
| `pond1/sensors` | ESP32 → server | Số đo mỗi 5 giây |
| `pond1/cmd/aerator` | server → ESP32 | Lệnh bật tắt máy sủi |
| `pond1/cmd/pump` | server → ESP32 | Lệnh bơm nước ngọt |
| `pond1/state` | ESP32 → server | Trạng thái thật của relay |
| `pond1/twin` | server → web | DO ước lượng và trạng thái tổng hợp |

## `pond1/sensors`

```json
{ "ts": 1761800000, "temp": 28.4, "ph": 7.2, "tds": 310 }
```

- `ts`: giây Unix
- `temp`: °C, từ DS18B20
- `ph`: đơn vị pH, đã hiệu chuẩn
- `tds`: ppm, đã bù nhiệt

## `pond1/cmd/aerator`

```json
{ "on": true }
```

## `pond1/cmd/pump`

```json
{ "on": true, "seconds": 5 }
```

Bơm tự tắt sau `seconds` giây, kể cả khi mất kết nối.

## `pond1/state`

```json
{ "aerator": true, "pump": false, "fw": "0.1.0" }
```

## `pond1/twin`

```json
{ "ts": 1761800000, "do": 4.8, "do_sat": 7.9, "fish": 20, "level": "ok" }
```

`level` nhận một trong ba giá trị: `ok`, `warning`, `danger`.
