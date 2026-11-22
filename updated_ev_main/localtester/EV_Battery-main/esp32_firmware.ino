/*
  ESP32 battery monitor with INA219, DS18B20, DHT22, and ILI9341 TFT.

  Install these Arduino libraries:
  - Adafruit INA219
  - OneWire
  - DallasTemperature
  - DHT sensor library by Adafruit
  - Adafruit GFX Library
  - Adafruit ILI9341

  Generate/update esp32_model_data.h by retraining after collecting readings.

  Safety: prototype indicators only, not a certified BMS or protection
  system. Use a protected 2S battery pack, suitable fuse, and an INA219 board
  rated for the total motor/load current. Never power motors through the ESP32.
*/

#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <DHT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <Adafruit_INA219.h>
#include "esp32_model_data.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <math.h>
#include <string.h>

static_assert(
  Esp32ModelData::FEATURE_COUNT == 5,
  "Firmware requires the five-feature Model B header."
);

// ---- ESP32 DevKit example pins; change to match your board ------------------
constexpr uint8_t I2C_SDA_PIN = 21;
constexpr uint8_t I2C_SCL_PIN = 22;
constexpr uint8_t DS18B20_ONEWIRE_PIN = 4;
constexpr uint8_t DHT22_DATA_PIN = 13;
constexpr uint8_t TFT_CS_PIN = 5;
constexpr uint8_t TFT_DC_PIN = 16;
constexpr uint8_t TFT_RST_PIN = 17;
constexpr uint8_t TFT_SCK_PIN = 18;
constexpr uint8_t TFT_MISO_PIN = 19;
constexpr uint8_t TFT_MOSI_PIN = 23;
constexpr uint8_t BUZZER_PIN = 27;

// Model voltage is average cell voltage; the INA219 measures the 2S pack.
constexpr uint8_t SERIES_CELL_COUNT = 2;
static_assert(SERIES_CELL_COUNT > 0, "SERIES_CELL_COUNT must be positive.");

// Demonstration thresholds; validate for the actual chemistry and pack.
constexpr float THERMAL_RUNAWAY_TEMP_C = 55.0;
constexpr float THERMAL_RISE_RATE_C_PER_MIN = 1.0;
constexpr float THERMAL_CORROBORATION_TEMP_C = 45.0;
constexpr float THERMAL_CORROBORATION_CURRENT_A = 3.0;
constexpr float HIGH_TEMPERATURE_ALERT_C = 45.0;
constexpr float VOLTAGE_Z_THRESHOLD = 3.0;
constexpr float VOLTAGE_EPSILON = 1e-6;
constexpr float SHORT_CIRCUIT_VOLTAGE_V = 3.0;
constexpr float SHORT_CIRCUIT_CURRENT_A = 3.0;
constexpr float SHORT_CIRCUIT_TEMP_C = 45.0;
constexpr float SHORT_CIRCUIT_VOLTAGE_DROP_V = 0.15;
constexpr float SHORT_CIRCUIT_TEMP_RISE_C = 2.0;
constexpr float SELF_DISCHARGE_CURRENT_A = 0.1;
constexpr float SELF_DISCHARGE_SLOPE_LIMIT_V_PER_HOUR = -0.05;
constexpr float SELF_DISCHARGE_MAX_TEMP_SPREAD_C = 2.0;

constexpr uint32_t SAMPLE_INTERVAL_MS = 2000UL;
constexpr uint32_t VOLTAGE_BASELINE_WINDOW_MS = 300000UL;
constexpr uint32_t THERMAL_RATE_WINDOW_MS = 60000UL;
constexpr uint32_t THERMAL_MIN_RATE_SPAN_MS = 30000UL;
constexpr uint32_t SELF_DISCHARGE_WINDOW_MS = 3600000UL;
constexpr uint32_t HISTORY_CAPACITY = 1810;
constexpr uint32_t BUZZER_PERIOD_MS = 1000UL;
constexpr uint32_t BUZZER_ON_MS = 180UL;
constexpr unsigned int BUZZER_LOW_FREQUENCY_HZ = 700;
constexpr unsigned int BUZZER_HIGH_FREQUENCY_HZ = 2400;
######################################################################################set
constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char COLLECTOR_URL[] =
  "http://192.168.1.10:8765/readings";
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 15000UL;
constexpr uint16_t HTTP_TIMEOUT_MS = 1000;

Adafruit_INA219 ina219;
OneWire oneWire(DS18B20_ONEWIRE_PIN);
DallasTemperature batteryTemperatureSensor(&oneWire);
DHT humidityTemperatureSensor(DHT22_DATA_PIN, DHT22);
Adafruit_ILI9341 display(TFT_CS_PIN, TFT_DC_PIN, TFT_RST_PIN);

struct BatteryReading {
  float voltage;
  float current;
  float batteryTemp;
  float ambientTemp;
  float humidity;
};

struct HistorySample {
  uint32_t millisAtSample;
  float voltage;
  float current;
  float batteryTemp;
};

struct SafetyState {
  bool thermalRunaway = false;
  bool internalShort = false;
  bool selfDischarge = false;
  bool voltageAnomaly = false;
  bool modelAnomaly = false;
  bool highTemperature = false;
  bool hotAmbient = false;
  bool overvoltage = false;
  bool undervoltage = false;
  bool highCurrent = false;
  double voltageZScore = NAN;
};

HistorySample history[HISTORY_CAPACITY];
uint32_t historyCount = 0;
uint32_t historyNext = 0;
bool displayAvailable = false;
bool sensorSetupSucceeded = false;
bool buzzerToneOn = false;
unsigned int activeBuzzerFrequencyHz = 0;
uint32_t lastSampleMillis = 0;
uint32_t lastWiFiAttemptMillis = 0;
SafetyState safetyState;
BatteryReading latestReading;

float getCellVoltage(const BatteryReading &reading) {
  return reading.voltage / SERIES_CELL_COUNT;
}

const HistorySample &historyAt(uint32_t chronologicalIndex) {
  const uint32_t oldest =
    (historyNext + HISTORY_CAPACITY - historyCount) % HISTORY_CAPACITY;
  return history[(oldest + chronologicalIndex) % HISTORY_CAPACITY];
}

void appendHistory(const BatteryReading &reading, uint32_t now) {
  history[historyNext] = {
    now,
    getCellVoltage(reading),
    reading.current,
    reading.batteryTemp
  };
  historyNext = (historyNext + 1) % HISTORY_CAPACITY;
  if (historyCount < HISTORY_CAPACITY) {
    historyCount++;
  }
}

bool readBatteryReading(BatteryReading &reading) {
  reading.voltage =
    ina219.getBusVoltage_V() + ina219.getShuntVoltage_mV() / 1000.0;
  reading.current = ina219.getCurrent_mA() / 1000.0;
  batteryTemperatureSensor.requestTemperatures();
  reading.batteryTemp = batteryTemperatureSensor.getTempCByIndex(0);
  reading.ambientTemp = humidityTemperatureSensor.readTemperature();
  reading.humidity = humidityTemperatureSensor.readHumidity();

  return isfinite(reading.voltage)
    && isfinite(reading.current)
    && isfinite(reading.batteryTemp)
    && isfinite(reading.ambientTemp)
    && isfinite(reading.humidity)
    && reading.voltage >= 0.0
    && reading.voltage <= 12.0
    && reading.batteryTemp != DEVICE_DISCONNECTED_C
    && reading.batteryTemp >= -55.0
    && reading.batteryTemp <= 125.0
    && reading.ambientTemp >= -55.0
    && reading.ambientTemp <= 125.0
    && reading.humidity >= 0.0
    && reading.humidity <= 100.0;
}

void attemptWiFiConnection() {
  if (
    strlen(WIFI_SSID) == 0
    || strcmp(WIFI_SSID, "YOUR_WIFI_SSID") == 0
  ) {
    return;
  }
  lastWiFiAttemptMillis = millis();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("Connecting to Wi-Fi for reading collection...");
}

void maintainWiFiConnection(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  if (now - lastWiFiAttemptMillis >= WIFI_RETRY_INTERVAL_MS) {
    attemptWiFiConnection();
  }
}

void uploadReading(const BatteryReading &reading) {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  char payload[256];
  const int payloadLength = snprintf(
    payload,
    sizeof(payload),
    "{\"voltage_V\":%.6f,\"current_A\":%.6f,"
    "\"battery_temp_C\":%.6f,\"ambient_temp_C\":%.6f,"
    "\"humidity_percent\":%.6f}",
    getCellVoltage(reading),
    reading.current,
    reading.batteryTemp,
    reading.ambientTemp,
    reading.humidity
  );
  if (payloadLength <= 0 || payloadLength >= sizeof(payload)) {
    Serial.println("ERROR: Wi-Fi reading payload could not be formatted.");
    return;
  }

  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  if (!http.begin(client, COLLECTOR_URL)) {
    Serial.println("ERROR: unable to initialize reading upload.");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  const int status = http.POST(
    reinterpret_cast<uint8_t *>(payload),
    static_cast<size_t>(payloadLength)
  );
  if (status != HTTP_CODE_CREATED) {
    Serial.printf("WARNING: reading upload returned HTTP %d.\n", status);
  }
  http.end();
}

bool predictEmbeddedAnomaly(
  double voltage,
  double current,
  double batteryTemp,
  double ambientTemp,
  double humidity
) {
  const float features[Esp32ModelData::FEATURE_COUNT] = {
    static_cast<float>(
      isfinite(voltage) ? voltage : Esp32ModelData::IMPUTER_MEDIANS[0]
    ),
    static_cast<float>(
      isfinite(current) ? current : Esp32ModelData::IMPUTER_MEDIANS[1]
    ),
    static_cast<float>(
      isfinite(batteryTemp)
        ? batteryTemp
        : Esp32ModelData::IMPUTER_MEDIANS[2]
    ),
    static_cast<float>(
      isfinite(ambientTemp)
        ? ambientTemp
        : Esp32ModelData::IMPUTER_MEDIANS[3]
    ),
    static_cast<float>(
      isfinite(humidity)
        ? humidity
        : Esp32ModelData::IMPUTER_MEDIANS[4]
    )
  };
  double totalPathLength = 0.0;

  for (uint16_t treeIndex = 0;
       treeIndex < Esp32ModelData::TREE_COUNT;
       treeIndex++) {
    const uint16_t treeStart =
      Esp32ModelData::TREE_OFFSETS[treeIndex];
    uint16_t nodeIndex = 0;
    uint16_t depth = 0;

    while (true) {
      const Esp32ModelData::Node &node =
        Esp32ModelData::NODES[treeStart + nodeIndex];
      if (node.feature == 255) {
        totalPathLength += depth + node.leafCorrection;
        break;
      }

      nodeIndex = static_cast<double>(features[node.feature]) <= node.threshold
        ? node.left
        : node.right;
      depth++;
    }
  }

  const double meanPathLength = totalPathLength / Esp32ModelData::TREE_COUNT;
  const double meanAnomalyScore = exp2(
    -meanPathLength / Esp32ModelData::SCORE_DENOMINATOR
  );
  return meanAnomalyScore > Esp32ModelData::ANOMALY_SCORE_THRESHOLD;
}

double calculateVoltageZScore(float voltage, uint32_t now) {
  double sum = 0.0;
  double sumSquares = 0.0;
  uint32_t samples = 0;

  for (uint32_t i = 0; i < historyCount; i++) {
    const HistorySample &sample = historyAt(i);
    if (now - sample.millisAtSample > VOLTAGE_BASELINE_WINDOW_MS) {
      continue;
    }
    sum += sample.voltage;
    sumSquares += static_cast<double>(sample.voltage) * sample.voltage;
    samples++;
  }

  if (samples < 10) {
    return NAN;
  }

  const double mean = sum / samples;
  const double variance = fmax(0.0, sumSquares / samples - mean * mean);
  const double standardDeviation = sqrt(variance);
  return (voltage - mean) / (standardDeviation + VOLTAGE_EPSILON);
}

double calculateTemperatureRiseRate(
  float currentTemperature,
  uint32_t now
) {
  for (uint32_t i = 0; i < historyCount; i++) {
    const HistorySample &sample = historyAt(i);
    const uint32_t elapsed = now - sample.millisAtSample;
    if (
      elapsed <= THERMAL_RATE_WINDOW_MS
      && elapsed >= THERMAL_MIN_RATE_SPAN_MS
    ) {
      return (currentTemperature - sample.batteryTemp)
        / (elapsed / 60000.0);
    }
  }
  return NAN;
}

bool detectRestSelfDischarge(
  const BatteryReading &currentReading,
  uint32_t now
) {
  uint32_t sampleCount = 1;
  uint32_t oldestAge = 0;
  double sumTimeHours = 0.0;
  double sumVoltage = getCellVoltage(currentReading);
  double sumTimeSquared = 0.0;
  double sumTimeVoltage = 0.0;
  float minTemperature = currentReading.batteryTemp;
  float maxTemperature = currentReading.batteryTemp;

  if (
    fabs(currentReading.current) > SELF_DISCHARGE_CURRENT_A
  ) {
    return false;
  }

  for (uint32_t i = 0; i < historyCount; i++) {
    const HistorySample &sample = historyAt(i);
    const uint32_t age = now - sample.millisAtSample;
    if (age > SELF_DISCHARGE_WINDOW_MS) {
      continue;
    }
    if (fabs(sample.current) > SELF_DISCHARGE_CURRENT_A) {
      return false;
    }

    const double timeHours = -age / 3600000.0;
    sumTimeHours += timeHours;
    sumVoltage += sample.voltage;
    sumTimeSquared += timeHours * timeHours;
    sumTimeVoltage += timeHours * sample.voltage;
    sampleCount++;
    oldestAge = max(oldestAge, age);
    minTemperature = min(minTemperature, sample.batteryTemp);
    maxTemperature = max(maxTemperature, sample.batteryTemp);
  }

  if (
    sampleCount < 3
    || oldestAge < SELF_DISCHARGE_WINDOW_MS
    || maxTemperature - minTemperature > SELF_DISCHARGE_MAX_TEMP_SPREAD_C
  ) {
    return false;
  }

  const double denominator =
    sampleCount * sumTimeSquared - sumTimeHours * sumTimeHours;
  if (denominator <= 0.0) {
    return false;
  }

  const double slope =
    (sampleCount * sumTimeVoltage - sumTimeHours * sumVoltage)
    / denominator;
  return slope < SELF_DISCHARGE_SLOPE_LIMIT_V_PER_HOUR;
}

SafetyState evaluateSafetyIndicators(
  const BatteryReading &reading,
  uint32_t now
) {
  SafetyState result;
  const float cellVoltage = getCellVoltage(reading);
  const double temperatureRiseRate =
    calculateTemperatureRiseRate(reading.batteryTemp, now);
  const bool rapidTemperatureRise =
    isfinite(temperatureRiseRate)
    && temperatureRiseRate >= THERMAL_RISE_RATE_C_PER_MIN;

  result.voltageZScore = calculateVoltageZScore(cellVoltage, now);
  result.voltageAnomaly =
    isfinite(result.voltageZScore)
    && fabs(result.voltageZScore) > VOLTAGE_Z_THRESHOLD;
  result.modelAnomaly = predictEmbeddedAnomaly(
    cellVoltage,
    reading.current,
    reading.batteryTemp,
    reading.ambientTemp,
    reading.humidity
  );

  result.thermalRunaway =
    reading.batteryTemp >= THERMAL_RUNAWAY_TEMP_C
    || rapidTemperatureRise
    || (
      result.voltageAnomaly
      && (
        reading.batteryTemp >= THERMAL_CORROBORATION_TEMP_C
        || rapidTemperatureRise
        || fabs(reading.current) >= THERMAL_CORROBORATION_CURRENT_A
      )
    );
  result.internalShort =
    (
      cellVoltage <= SHORT_CIRCUIT_VOLTAGE_V
      && (
        reading.current >= SHORT_CIRCUIT_CURRENT_A
        || reading.batteryTemp >= SHORT_CIRCUIT_TEMP_C
      )
    );

  if (historyCount > 0) {
    const HistorySample &previous = historyAt(historyCount - 1);
    if (now - previous.millisAtSample <= THERMAL_RATE_WINDOW_MS) {
      const float voltageDrop = previous.voltage - cellVoltage;
      const float temperatureRise =
        reading.batteryTemp - previous.batteryTemp;
      result.internalShort =
        result.internalShort
        || (
          voltageDrop >= SHORT_CIRCUIT_VOLTAGE_DROP_V
          && (
            reading.current >= SHORT_CIRCUIT_CURRENT_A
            || temperatureRise >= SHORT_CIRCUIT_TEMP_RISE_C
          )
        );
    }
  }

  result.selfDischarge = detectRestSelfDischarge(reading, now);
  result.highTemperature =
    reading.batteryTemp >= HIGH_TEMPERATURE_ALERT_C;
  result.hotAmbient = reading.ambientTemp >= 35.0;
  result.overvoltage = cellVoltage >= 4.1;
  result.undervoltage = cellVoltage <= 3.0;
  result.highCurrent = reading.current >= 3.0;
  appendHistory(reading, now);
  return result;
}

String getBatteryCondition(const SafetyState &state) {
  if (state.thermalRunaway) {
    return "THERMAL RUNAWAY?";
  }
  if (state.internalShort) {
    return "SHORT-CIRCUIT?";
  }
  if (state.selfDischarge) {
    return "SELF-DISCHARGE?";
  }
  if (state.highTemperature) {
    return "HIGH TEMP";
  }
  if (state.overvoltage) {
    return "OVERVOLTAGE";
  }
  if (state.undervoltage) {
    return "UNDERVOLTAGE";
  }
  if (state.highCurrent) {
    return "HIGH CURRENT";
  }
  if (state.hotAmbient) {
    return "HOT AMBIENT";
  }
  if (state.voltageAnomaly) {
    return "VOLTAGE ANOMALY";
  }
  if (state.modelAnomaly) {
    return "UNCLASSIFIED ANOMALY";
  }
  return "NORMAL";
}

void updateBuzzer(const SafetyState &state, uint32_t now) {
  const bool emergency =
    state.thermalRunaway || state.highTemperature;
  const bool alert =
    emergency
    || state.internalShort
    || state.selfDischarge
    || state.overvoltage
    || state.undervoltage
    || state.highCurrent
    || state.hotAmbient
    || state.voltageAnomaly
    || state.modelAnomaly;

  if (!alert) {
    noTone(BUZZER_PIN);
    buzzerToneOn = false;
    activeBuzzerFrequencyHz = 0;
    return;
  }

  if (now % BUZZER_PERIOD_MS < BUZZER_ON_MS) {
    const unsigned int requestedFrequency = emergency
      ? BUZZER_HIGH_FREQUENCY_HZ
      : BUZZER_LOW_FREQUENCY_HZ;
    if (!buzzerToneOn || activeBuzzerFrequencyHz != requestedFrequency) {
      tone(BUZZER_PIN, requestedFrequency);
      buzzerToneOn = true;
      activeBuzzerFrequencyHz = requestedFrequency;
    }
  } else if (buzzerToneOn) {
    noTone(BUZZER_PIN);
    buzzerToneOn = false;
    activeBuzzerFrequencyHz = 0;
  }
}

void showTFT(const BatteryReading &reading, const String &condition) {
  if (!displayAvailable) {
    return;
  }

  display.fillScreen(ILI9341_BLACK);
  display.setTextSize(2);
  display.setTextColor(ILI9341_CYAN);
  display.setCursor(12, 12);
  display.println("2S Battery Monitor");
  display.drawFastHLine(12, 40, 296, ILI9341_DARKGREY);

  display.setTextColor(ILI9341_WHITE);
  display.setCursor(12, 48);
  display.print("Pack:     ");
  display.print(reading.voltage, 2);
  display.println(" V");
  display.setCursor(12, 72);
  display.print("Cell avg: ");
  display.print(getCellVoltage(reading), 2);
  display.println(" V");
  display.setCursor(12, 96);
  display.print("Current:  ");
  display.print(reading.current, 2);
  display.println(" A");
  display.setCursor(12, 120);
  display.print("Battery:  ");
  display.print(reading.batteryTemp, 1);
  display.println(" C");
  display.setCursor(12, 144);
  display.print("Ambient:  ");
  display.print(reading.ambientTemp, 1);
  display.println(" C");
  display.setCursor(12, 168);
  display.print("Humidity: ");
  display.print(reading.humidity, 1);
  display.println(" %");
  display.setCursor(12, 204);
  display.setTextColor(
    safetyState.thermalRunaway || safetyState.highTemperature
      ? ILI9341_RED
      : ILI9341_YELLOW
  );
  display.print("Status: ");
  display.print(condition);
}

void showSensorError() {
  if (!displayAvailable) {
    return;
  }

  display.fillScreen(ILI9341_BLACK);
  display.setTextSize(2);
  display.setTextColor(ILI9341_RED);
  display.setCursor(12, 20);
  display.println("SENSOR READ ERROR");
  display.setTextColor(ILI9341_WHITE);
  display.setCursor(12, 60);
  display.println("Check INA219, DS18B20,");
  display.println("and DHT22 wiring.");
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  attemptWiFiConnection();
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  SPI.begin(TFT_SCK_PIN, TFT_MISO_PIN, TFT_MOSI_PIN, TFT_CS_PIN);
  display.begin();
  display.setRotation(1);
  displayAvailable = true;
  display.fillScreen(ILI9341_BLACK);

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  if (!ina219.begin()) {
    Serial.println("ERROR: INA219 not detected on I2C.");
    showSensorError();
    return;
  }
  ina219.setCalibration_32V_2A();
  Serial.println(
    "INA219 uses Adafruit 32V/2A calibration; verify board shunt/current "
    "rating for the motor load."
  );

  batteryTemperatureSensor.begin();
  if (batteryTemperatureSensor.getDeviceCount() < 1) {
    Serial.println("ERROR: DS18B20 battery sensor not detected.");
    showSensorError();
    return;
  }
  humidityTemperatureSensor.begin();

  sensorSetupSucceeded = true;
  Serial.println(
    "READY: INA219, DS18B20, DHT22 humidity, ILI9341, and Model B"
  );
  Serial.println(
    "Model voltage is average cell voltage from the 2S pack measurement."
  );
}

void loop() {
  if (!sensorSetupSucceeded) {
    delay(1000);
    return;
  }

  const uint32_t now = millis();
  maintainWiFiConnection(now);
  updateBuzzer(safetyState, now);
  if (now - lastSampleMillis < SAMPLE_INTERVAL_MS) {
    return;
  }
  lastSampleMillis = now;

  if (!readBatteryReading(latestReading)) {
    Serial.println("ERROR: invalid sensor reading; sample skipped.");
    showSensorError();
    noTone(BUZZER_PIN);
    buzzerToneOn = false;
    activeBuzzerFrequencyHz = 0;
    return;
  }

  safetyState = evaluateSafetyIndicators(latestReading, now);
  const String condition = getBatteryCondition(safetyState);
  updateBuzzer(safetyState, now);

  // Model B serial format: voltage,current,battery temp,ambient temp,humidity.
  Serial.print(getCellVoltage(latestReading), 3);
  Serial.print(",");
  Serial.print(latestReading.current, 3);
  Serial.print(",");
  Serial.print(latestReading.batteryTemp, 2);
  Serial.print(",");
  Serial.print(latestReading.ambientTemp, 2);
  Serial.print(",");
  Serial.println(latestReading.humidity, 2);
  uploadReading(latestReading);

  showTFT(latestReading, condition);
}
