#pragma once

constexpr int PIN_DS18B20 = 4;
constexpr int PIN_PH_ADC = 34;
// KHÔNG DÙNG: đã bỏ TDS (08/11/2026). Giữ lại theo quy ước "không xoá". GPIO35, GPIO25 bỏ trống.
constexpr int PIN_TDS_ADC = 35;
constexpr int PIN_TDS_POWER = 25;
constexpr int PIN_RELAY_AERATOR = 26;
constexpr int PIN_RELAY_PUMP = 27;
// Đo 09/11/2026 bằng test_relay: gửi LOW thì đèn relay sáng (đóng), HIGH thì tắt => kích mức thấp.
// Người dùng nói chung cho cả hai kênh; nếu một kênh khác thì sửa lại.
constexpr bool RELAY_ACTIVE_LOW = true;
