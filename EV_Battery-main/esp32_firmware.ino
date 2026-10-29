/*
  Standalone ESP32 battery monitor with embedded Model B inference.

  Install these Arduino libraries:
  - INA229 by Rob Tillaart
  - OneWire
  - DallasTemperature
  - Adafruit GFX Library
  - Adafruit SSD1306

  Generate/update esp32_model_data.h by running train_model.py after the
  training dataset or model configuration changes.

  Safety: prototype indicators only, not a certified BMS or protection
  system. Check the INA229 breakout, shunt, fuse, wiring, isolation, and
  battery voltage/current ratings before connecting a real battery.
*/

#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "INA229.h"
#include "esp32_model_data.h"
#include <math.h>

static_assert(
  Esp32ModelData::FEATURE_COUNT == 4,
  "Firmware requires the four-feature Model B header."
);

// ---- ESP32 DevKit example pins; change to match your board ------------------
constexpr uint8_t INA229_CS_PIN = 5;
constexpr uint8_t SPI_SCK_PIN = 18;
constexpr uint8_t SPI_MISO_PIN = 19;
constexpr uint8_t SPI_MOSI_PIN = 23;
constexpr uint8_t BATTERY_TEMP_ONEWIRE_PIN = 4;
constexpr uint8_t BUZZER_PIN = 27;
constexpr uint8_t OLED_SDA_PIN = 21;
constexpr uint8_t OLED_SCL_PIN = 22;
constexpr uint8_t BATTERY_SENSOR_INDEX = 0;
constexpr uint8_t AMBIENT_SENSOR_INDEX = 1;
static_assert(
  BATTERY_SENSOR_INDEX != AMBIENT_SENSOR_INDEX,
  "Battery and ambient sensors must use different indices."
);

// Match these to the installed INA229 breakout and battery configuration.
constexpr float MAX_EXPECTED_CURRENT_A = 10.0;
constexpr float SHUNT_RESISTANCE_OHMS = 0.001;
constexpr uint8_t SERIES_CELL_COUNT = 1;
static_assert(SERIES_CELL_COUNT > 0, "SERIES_CELL_COUNT must be positive.");

constexpr uint8_t OLED_ADDRESS = 0x3C;
constexpr int SCREEN_WIDTH = 128;
constexpr int SCREEN_HEIGHT = 64;
constexpr int OLED_RESET = -1;

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

INA229 ina(INA229_CS_PIN, &SPI);
OneWire oneWire(BATTERY_TEMP_ONEWIRE_PIN);
DallasTemperature batteryTemperatureSensor(&oneWire);
DeviceAddress batterySensorAddress;
DeviceAddress ambientSensorAddress;
Adafruit_SSD1306 display(
  SCREEN_WIDTH,
  SCREEN_HEIGHT,
  &Wire,
  OLED_RESET
);

struct BatteryReading {
  float voltage;
  float current;
  float batteryTemp;
  float ambientTemp;
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
  reading.voltage = ina.getBusVoltage();
  reading.current = ina.getCurrent();
  batteryTemperatureSensor.requestTemperatures();
  reading.batteryTemp =
    batteryTemperatureSensor.getTempC(batterySensorAddress);
  reading.ambientTemp =
    batteryTemperatureSensor.getTempC(ambientSensorAddress);

  return isfinite(reading.voltage)
    && isfinite(reading.current)
    && isfinite(reading.batteryTemp)
    && isfinite(reading.ambientTemp)
    && reading.voltage >= 0.0
    && reading.voltage <= 85.0
    && reading.batteryTemp != DEVICE_DISCONNECTED_C
    && reading.batteryTemp >= -55.0
    && reading.batteryTemp <= 125.0
    && reading.ambientTemp != DEVICE_DISCONNECTED_C
    && reading.ambientTemp >= -55.0
    && reading.ambientTemp <= 125.0;
}

void printSensorAddress(const char *label, const DeviceAddress address) {
  Serial.printf("%s ROM: ", label);
  for (uint8_t i = 0; i < 8; i++) {
    Serial.printf("%02X", address[i]);
  }
  Serial.println();
}

bool predictEmbeddedAnomaly(
  double voltage,
  double current,
  double batteryTemp,
  double ambientTemp
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
    reading.ambientTemp
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

void showOLED(const BatteryReading &reading, const String &condition) {
  if (!displayAvailable) {
    return;
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("EV BATTERY EDGE MONITOR");
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);
  display.setCursor(0, 14);
  display.printf("Cell V : %.2f V", getCellVoltage(reading));
  display.setCursor(0, 24);
  display.printf("Current: %.2f A", reading.current);
  display.setCursor(0, 34);
  display.printf("Battery: %.1f C", reading.batteryTemp);
  display.setCursor(0, 44);
  display.printf("Ambient: %.1f C", reading.ambientTemp);
  display.setCursor(0, 54);
  display.print("STATUS: ");
  display.println(condition);
  display.display();
}

void showSensorError() {
  if (!displayAvailable) {
    return;
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("EV BATTERY EDGE MONITOR");
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);
  display.setCursor(0, 25);
  display.println("SENSOR READ ERROR");
  display.setCursor(0, 40);
  display.println("Check INA229 / DS18B20");
  display.display();
}

void setup() {
  Serial.begin(115200);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  displayAvailable = display.begin(
    SSD1306_SWITCHCAPVCC,
    OLED_ADDRESS
  );
  if (displayAvailable) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.display();
  } else {
    Serial.println("WARNING: OLED not detected; continuing without display.");
  }

  SPI.begin(SPI_SCK_PIN, SPI_MISO_PIN, SPI_MOSI_PIN, INA229_CS_PIN);
  if (!ina.begin()) {
    Serial.println("ERROR: INA229 not detected; check SPI wiring.");
    showSensorError();
    return;
  }

  const int calibrationResult =
    ina.setMaxCurrentShunt(
      MAX_EXPECTED_CURRENT_A,
      SHUNT_RESISTANCE_OHMS
    );
  if (calibrationResult != 0) {
    Serial.println("ERROR: INA229 shunt calibration failed.");
    showSensorError();
    return;
  }

  batteryTemperatureSensor.begin();
  if (batteryTemperatureSensor.getDeviceCount() < 2) {
    Serial.println("ERROR: two DS18B20 sensors are required.");
    showSensorError();
    return;
  }
  if (
    !batteryTemperatureSensor.getAddress(
      batterySensorAddress,
      BATTERY_SENSOR_INDEX
    )
    || !batteryTemperatureSensor.getAddress(
      ambientSensorAddress,
      AMBIENT_SENSOR_INDEX
    )
  ) {
    Serial.println("ERROR: unable to identify both DS18B20 sensors.");
    showSensorError();
    return;
  }
  Serial.printf(
    "DS18B20 battery sensor index: %u\n",
    BATTERY_SENSOR_INDEX
  );
  Serial.printf(
    "DS18B20 ambient sensor index: %u\n",
    AMBIENT_SENSOR_INDEX
  );
  printSensorAddress("Battery DS18B20", batterySensorAddress);
  printSensorAddress("Ambient DS18B20", ambientSensorAddress);

  sensorSetupSucceeded = true;
  Serial.println("READY: embedded Model B, rules, and time-series alerts");
  Serial.println("Voltage thresholds and model expect average cell voltage.");
}

void loop() {
  if (!sensorSetupSucceeded) {
    delay(1000);
    return;
  }

  const uint32_t now = millis();
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

  // Match the dashboard's four-feature Model B serial format.
  Serial.print(getCellVoltage(latestReading), 3);
  Serial.print(",");
  Serial.print(latestReading.current, 3);
  Serial.print(",");
  Serial.print(latestReading.batteryTemp, 2);
  Serial.print(",");
  Serial.println(latestReading.ambientTemp, 2);

  showOLED(latestReading, condition);
}
