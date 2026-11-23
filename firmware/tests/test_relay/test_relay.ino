// test_relay: bật tắt lần lượt từng kênh relay để xác định MỨC KÍCH (HIGH hay LOW làm relay đóng).
// Board: ESP32 Dev Module, baud 115200. Không cần thư viện ngoài.
//
// !!! CHỈ NỐI ĐÈN LED / TẢI THỬ, CHƯA NỐI MÁY SỦI, BƠM !!!
//
// Cách xem: nghe tiếng "tách" và nhìn đèn LED trên module relay.
// Mỗi bước giữ 2 giây, Serial in ra "GPIO26 = LOW" hoặc "GPIO26 = HIGH".
//   - Nếu relay tách + đèn sáng lúc in "= LOW"  -> relay kích mức THẤP: RELAY_ACTIVE_LOW = true
//   - Nếu relay tách + đèn sáng lúc in "= HIGH" -> relay kích mức CAO:  RELAY_ACTIVE_LOW = false
// Ghi kết quả lại rồi báo để cập nhật pins.h.

constexpr int PIN_RELAY_AERATOR = 26;   // kênh K1: máy sủi
constexpr int PIN_RELAY_PUMP    = 27;   // kênh K2: bơm

void datMuc(int pin, int level) {
  digitalWrite(pin, level);
  Serial.print("GPIO");
  Serial.print(pin);
  Serial.print(" = ");
  Serial.println(level == HIGH ? "HIGH" : "LOW");
}

void setup() {
  // Đặt mức TRƯỚC khi pinMode(OUTPUT) để chân không giật lúc khởi động.
  // Chưa biết mức tắt thật là gì nên tạm chọn HIGH (đa số module relay 2 kênh kích mức thấp).
  digitalWrite(PIN_RELAY_AERATOR, HIGH);
  digitalWrite(PIN_RELAY_PUMP, HIGH);
  pinMode(PIN_RELAY_AERATOR, OUTPUT);
  pinMode(PIN_RELAY_PUMP, OUTPUT);
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("Bắt đầu thử relay. Quan sát đèn LED và tiếng tách trên module.");
}

void loop() {
  datMuc(PIN_RELAY_AERATOR, LOW);   delay(2000);
  datMuc(PIN_RELAY_AERATOR, HIGH);  delay(2000);
  datMuc(PIN_RELAY_PUMP, LOW);      delay(2000);
  datMuc(PIN_RELAY_PUMP, HIGH);     delay(2000);
  Serial.println("--- hết một vòng ---");
}
