// test_ds18b20: đọc nhiệt độ DS18B20 mỗi giây và in ra Serial Monitor (115200 baud).
// Thư viện cần cài (Library Manager): "OneWire" (Paul Stoffregen), "DallasTemperature" (Miles Burton).
// Board: ESP32 Dev Module.
//
// Cách đọc kết quả:
//   - Số hợp lý (vd 25-30 °C)  -> đạt.
//   - -127.00 (DEVICE_DISCONNECTED_C) -> không thấy cảm biến: kiểm tra dây DATA/VCC/GND, trở kéo 4.7k.
//   - 85.00 -> cảm biến vừa bật, đọc quá sớm / nguồn yếu (số mặc định lúc khởi động).

#include <OneWire.h>
#include <DallasTemperature.h>

// Giống firmware/pond_node/pins.h (sketch thử để riêng cho gọn, nếu đổi chân nhớ sửa cả hai chỗ)
constexpr int PIN_DS18B20 = 4;

OneWire oneWire(PIN_DS18B20);
DallasTemperature sensors(&oneWire);

void setup() {
  Serial.begin(115200);
  delay(500);
  sensors.begin();
  Serial.println();
  Serial.print("Số cảm biến DS18B20 tìm thấy: ");
  Serial.println(sensors.getDeviceCount());  // phải là 1
}

void loop() {
  sensors.requestTemperatures();             // ra lệnh đo (chờ ~750 ms cho độ phân giải 12 bit)
  float t = sensors.getTempCByIndex(0);
  Serial.print("Nhiệt độ: ");
  Serial.print(t, 2);
  Serial.print(" C");
  if (t == DEVICE_DISCONNECTED_C) Serial.print("  <-- LỖI -127: không thấy cảm biến (dây/trở kéo)");
  else if (t == 85.0)            Serial.print("  <-- 85: đọc quá sớm hoặc nguồn yếu");
  Serial.println();
  delay(1000);
}
