// test_ph_mv: đọc điện áp chân pH (GPIO34) và in mV, để hiệu chuẩn pH.
// Board: ESP32 Dev Module, baud 115200. Không cần thư viện ngoài.
//
// !!! AN TOÀN TRƯỚC KHI CẮM VÀO GPIO34 !!!
//   Đo bằng đồng hồ điểm giữa cầu phân áp (giữa điện trở 20k và 10k) khi bảng mạch pH đã có 5V:
//   phải DƯỚI 3,3 V (theo sơ đồ: dưới 1,75 V). Trên 3,3 V thì KHÔNG cắm vào GPIO34 (có thể hỏng ESP32).
//
// Sơ đồ: Po --20k--+--10k-- GND, điểm giữa nối GPIO34  => mV chân = mV bảng mạch x 10/(20+10)
//        => mV bảng mạch = mV chân x 3.
// PH_DIVIDER_FACTOR = 3.0 theo sơ đồ nối. Cần đối chiếu điện trở thật bằng đồng hồ;
// nếu lệch một chút thì bước hiệu chuẩn 3 điểm (tools/calib_ph.py) sẽ tự bù.

constexpr int   PIN_PH_ADC         = 34;    // ADC1, KHÔNG dùng ADC2 (xung đột WiFi)
constexpr float PH_DIVIDER_FACTOR  = 3.0;
constexpr int   SAMPLES            = 64;    // trung bình nhiều mẫu cho đỡ nhiễu

// Hệ số hiệu chuẩn: pH = A * mV_bang_mach + B (giống firmware/pond_node/calibration.h, đổi thì sửa cả hai)
// Đo 09/11/2026 bằng bột đệm 4.01 (3510 mV) và 9.18 (2875 mV).
constexpr float PH_SLOPE_A  = -0.008142f;
constexpr float PH_OFFSET_B = 32.5875f;

void setup() {
  Serial.begin(115200);
  delay(500);
  analogSetPinAttenuation(PIN_PH_ADC, ADC_11db);  // đo được tới ~3,1 V
  Serial.println();
  Serial.println("Đọc pH (mV). Đứng im, chờ số ổn định rồi ghi lại cột 'mV_bang_mach'.");
}

void loop() {
  uint32_t sum = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogReadMilliVolts(PIN_PH_ADC);      // ESP32 đã bù sai số ADC bằng dữ liệu hiệu chuẩn chip
    delay(2);
  }
  float mvPin   = sum / (float)SAMPLES;
  float mvBoard = mvPin * PH_DIVIDER_FACTOR;

  Serial.print("mV_chan_GPIO34=");
  Serial.print(mvPin, 1);
  Serial.print("   mV_bang_mach=");
  Serial.print(mvBoard, 1);
  Serial.print("   pH=");
  Serial.print(PH_SLOPE_A * mvBoard + PH_OFFSET_B, 2);   // ESP32 tự đổi mV sang pH
  if (mvPin > 3100) Serial.print("   <-- CAO QUÁ, ADC bão hoà, kiểm tra cầu phân áp!");
  if (mvPin < 5)    Serial.print("   <-- gần 0: chưa cắm đầu dò / mất 5V / dây đứt");
  Serial.println();
  delay(1000);
}
