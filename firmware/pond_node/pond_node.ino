// pond_node: firmware chính của ESP32 trong thùng cá.
// Việc làm: đọc nhiệt độ + pH mỗi 5 s -> gửi pond1/sensors; nhận lệnh sục khí/bơm qua MQTT;
// báo trạng thái thật qua pond1/state; mất MQTT quá 60 s thì vào chế độ an toàn.
//
// Thư viện cần cài (Library Manager): OneWire, DallasTemperature, PubSubClient (Nick O'Leary),
// ArduinoJson (Benoit Blanchon, bản 7.x). Board: ESP32 Dev Module.
// Trước khi nạp: sao chép secrets.h.example thành secrets.h rồi điền WiFi và IP laptop.

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>

#include "pins.h"
#include "calibration.h"
#include "secrets.h"

#define FW_VERSION "0.1.0"
#define MQTT_BASE_TOPIC "pond1"   // phải khớp mqtt.base_topic trong config/config.json (ESP32 không đọc config)

// ---- Chủ đề MQTT (theo docs/mqtt.md) ----
const char* TOPIC_SENSORS     = MQTT_BASE_TOPIC "/sensors";
const char* TOPIC_STATE       = MQTT_BASE_TOPIC "/state";
const char* TOPIC_CMD_AERATOR = MQTT_BASE_TOPIC "/cmd/aerator";
const char* TOPIC_CMD_PUMP    = MQTT_BASE_TOPIC "/cmd/pump";

// ---- Thời gian (ms) ----
const unsigned long SENSOR_PERIOD_MS   = 5000;    // gửi số đo mỗi 5 s
const unsigned long TEMP_CONVERT_MS    = 800;     // DS18B20 cần ~750 ms để đo xong
const unsigned long STATE_PERIOD_MS    = 30000;   // gửi lại trạng thái định kỳ
const unsigned long FAILSAFE_MS        = 60000;   // mất MQTT quá 60 s thì vào chế độ an toàn
const unsigned long WIFI_RETRY_MS      = 10000;
const unsigned long MQTT_RETRY_MS      = 5000;

// Giới hạn cứng số giây một lần bật bơm (lớp bảo vệ cuối, chặn lệnh quá dài). Cần nhóm đối chiếu.
const int PUMP_MAX_SECONDS = 30;

const int PH_SAMPLES = 32;                         // số mẫu pH lấy trung bình

OneWire oneWire(PIN_DS18B20);
DallasTemperature tempSensor(&oneWire);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

bool aeratorOn = false;
bool pumpOn = false;
unsigned long pumpOffAt = 0;       // millis() lúc bơm phải tắt

bool inFailsafe = false;
unsigned long lastMqttOkMs = 0;    // lần cuối còn nối MQTT (hoặc lúc khởi động)
unsigned long lastWifiTry = 0;
unsigned long lastMqttTry = 0;
unsigned long lastStateMs = 0;
unsigned long lastSensorMs = 0;
unsigned long tempRequestMs = 0;
bool tempRequested = false;
bool wifiWasUp = false;

// ---------- Relay ----------
// RELAY_ACTIVE_LOW = true: LOW làm relay đóng (thiết bị chạy), HIGH làm relay mở (tắt).
int relayLevel(bool on) {
  return (on == RELAY_ACTIVE_LOW) ? LOW : HIGH;
}

void setAerator(bool on) {
  aeratorOn = on;
  digitalWrite(PIN_RELAY_AERATOR, relayLevel(on));
}

void setPump(bool on) {
  pumpOn = on;
  digitalWrite(PIN_RELAY_PUMP, relayLevel(on));
}

// ---------- Gửi trạng thái ----------
void publishState() {
  if (!mqtt.connected()) return;
  JsonDocument doc;
  doc["v"] = 1;
  doc["aerator"] = aeratorOn;
  doc["pump"] = pumpOn;
  doc["fw"] = FW_VERSION;
  char buf[128];
  serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(TOPIC_STATE, buf);
  lastStateMs = millis();
}

// ---------- Nhận lệnh ----------
void onMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) {
    Serial.println("Lệnh không phải JSON hợp lệ, bỏ qua");
    return;
  }
  if (!doc["on"].is<bool>()) {
    Serial.println("Lệnh thiếu khoá 'on', bỏ qua");
    return;
  }
  bool on = doc["on"];

  if (strcmp(topic, TOPIC_CMD_AERATOR) == 0) {
    setAerator(on);
    Serial.printf("Lệnh sục khí: %s\n", on ? "BẬT" : "TẮT");
  } else if (strcmp(topic, TOPIC_CMD_PUMP) == 0) {
    if (!on) {
      setPump(false);
      Serial.println("Lệnh bơm: TẮT");
    } else {
      int seconds = doc["seconds"] | 0;
      if (seconds <= 0) {                       // luật: bơm luôn kèm số giây
        Serial.println("Lệnh bật bơm thiếu 'seconds', TỪ CHỐI");
        return;
      }
      if (seconds > PUMP_MAX_SECONDS) seconds = PUMP_MAX_SECONDS;
      pumpOffAt = millis() + (unsigned long)seconds * 1000UL;
      setPump(true);
      Serial.printf("Lệnh bơm: BẬT %d s\n", seconds);
    }
  } else {
    return;
  }
  publishState();   // báo ngay trạng thái thật để giao diện cập nhật
}

// ---------- Kết nối ----------
void keepWifi() {
  bool up = (WiFi.status() == WL_CONNECTED);
  if (up && !wifiWasUp) {
    Serial.print("WiFi đã nối, IP ESP32: ");
    Serial.println(WiFi.localIP());
  }
  if (!up && wifiWasUp) Serial.println("WiFi bị mất");
  wifiWasUp = up;

  if (!up && millis() - lastWifiTry >= WIFI_RETRY_MS) {
    lastWifiTry = millis();
    Serial.println("Đang nối WiFi...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

void keepMqtt() {
  if (mqtt.connected()) {
    lastMqttOkMs = millis();
    return;
  }
  if (WiFi.status() != WL_CONNECTED) return;
  if (millis() - lastMqttTry < MQTT_RETRY_MS) return;
  lastMqttTry = millis();

  String clientId = "pond-esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  Serial.printf("Đang nối MQTT %s:%d ...\n", MQTT_HOST, MQTT_PORT);
  if (mqtt.connect(clientId.c_str())) {
    Serial.println("MQTT đã nối");
    mqtt.subscribe(TOPIC_CMD_AERATOR);
    mqtt.subscribe(TOPIC_CMD_PUMP);
    lastMqttOkMs = millis();
    if (inFailsafe) {
      inFailsafe = false;
      Serial.println("Thoát chế độ an toàn, server điều khiển tiếp");
    }
    publishState();   // có kết nối lại thì gửi state ngay
  } else {
    Serial.printf("MQTT lỗi, mã %d\n", mqtt.state());
  }
}

// ---------- An toàn ----------
void checkPumpTimer() {
  if (pumpOn && (long)(millis() - pumpOffAt) >= 0) {   // bơm tự tắt sau số giây đã nhận, kể cả khi mất mạng
    setPump(false);
    Serial.println("Bơm hết giờ, tự tắt");
    publishState();
  }
}

void checkFailsafe() {
  if (inFailsafe) return;
  if (millis() - lastMqttOkMs > FAILSAFE_MS) {
    inFailsafe = true;
    setPump(false);       // bơm tắt để không tràn thùng
    setAerator(true);     // máy sủi bật để giữ oxy
    Serial.println("MẤT MQTT quá 60 s: chế độ an toàn (bơm TẮT, sủi BẬT)");
  }
}

// ---------- Cảm biến ----------
void runSensors() {
  unsigned long now = millis();
  if (!tempRequested && now - lastSensorMs >= SENSOR_PERIOD_MS) {
    tempSensor.requestTemperatures();     // ra lệnh đo, không chờ
    tempRequestMs = now;
    tempRequested = true;
  } else if (tempRequested && now - tempRequestMs >= TEMP_CONVERT_MS) {
    tempRequested = false;
    lastSensorMs = now;

    float t = tempSensor.getTempCByIndex(0);
    bool tempOk = (t != DEVICE_DISCONNECTED_C && t != 85.0f);

    uint32_t sum = 0;
    for (int i = 0; i < PH_SAMPLES; i++) {
      sum += analogReadMilliVolts(PIN_PH_ADC);
      delay(2);
    }
    float mvPin = sum / (float)PH_SAMPLES;
    bool phOk = (mvPin >= 5.0f);          // gần 0 là chưa cắm đầu dò hoặc đứt dây
    float ph = PH_SLOPE_A * (mvPin * PH_DIVIDER_FACTOR) + PH_OFFSET_B;

    Serial.printf("Nhiệt độ: %s%.2f C   pH: %s%.2f   (mV chân %.0f)\n",
                  tempOk ? "" : "(LỖI) ", t, phOk ? "" : "(LỖI) ", ph, mvPin);

    if (!mqtt.connected()) return;
    JsonDocument doc;
    doc["v"] = 1;                         // không gửi 'ts': server dùng giờ của chính nó
    if (tempOk) doc["temp"] = roundf(t * 100.0f) / 100.0f;
    if (phOk)   doc["ph"]   = roundf(ph * 100.0f) / 100.0f;
    char buf[160];
    serializeJson(doc, buf, sizeof(buf));
    mqtt.publish(TOPIC_SENSORS, buf);
  }
}

// ---------- Khởi động ----------
void setup() {
  // Đặt mức TẮT trước khi pinMode(OUTPUT) để relay không giật lúc cấp điện.
  digitalWrite(PIN_RELAY_AERATOR, relayLevel(false));
  digitalWrite(PIN_RELAY_PUMP, relayLevel(false));
  pinMode(PIN_RELAY_AERATOR, OUTPUT);
  pinMode(PIN_RELAY_PUMP, OUTPUT);

  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("pond_node " FW_VERSION " khởi động");

  analogSetPinAttenuation(PIN_PH_ADC, ADC_11db);
  tempSensor.begin();
  tempSensor.setWaitForConversion(false);   // không chặn vòng loop khi đo nhiệt độ

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setKeepAlive(15);        // phát hiện mất kết nối trong ~15 s
  mqtt.setSocketTimeout(3);     // không treo lâu khi server không trả lời

  lastMqttOkMs = millis();      // tính giờ failsafe từ lúc khởi động
  lastWifiTry = millis() - WIFI_RETRY_MS;   // thử nối WiFi ngay
}

void loop() {
  keepWifi();
  keepMqtt();
  mqtt.loop();
  checkPumpTimer();
  checkFailsafe();
  runSensors();

  if (mqtt.connected() && millis() - lastStateMs >= STATE_PERIOD_MS) publishState();
}
