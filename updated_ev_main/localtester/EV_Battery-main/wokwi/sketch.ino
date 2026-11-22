/*
  Wokwi-only simulation of the battery monitor.

  Two potentiometers stand in for the INA229:
  - Voltage knob: 2.50 V to 4.50 V
  - Current knob: 0.00 A to 6.00 A

  This sketch is separate from the real INA229 firmware. Its accelerated
  one-minute-to-one-hour self-discharge timer is only for simulation demos.
*/

#include <Arduino.h>
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <math.h>

constexpr uint8_t VOLTAGE_POT_PIN = 34;
constexpr uint8_t CURRENT_POT_PIN = 35;
constexpr uint8_t BATTERY_TEMP_PIN = 4;
constexpr uint8_t BUZZER_PIN = 27;
constexpr uint8_t OLED_SDA_PIN = 21;
constexpr uint8_t OLED_SCL_PIN = 22;

constexpr uint8_t OLED_ADDRESS = 0x3C;
constexpr int SCREEN_WIDTH = 128;
constexpr int SCREEN_HEIGHT = 64;

constexpr float THERMAL_RUNAWAY_TEMP_C = 55.0;
constexpr float THERMAL_RISE_RATE_C_PER_MIN = 1.0;
constexpr float HIGH_TEMPERATURE_ALERT_C = 45.0;
constexpr float SHORT_CIRCUIT_VOLTAGE_V = 3.0;
constexpr float SHORT_CIRCUIT_CURRENT_A = 3.0;
constexpr float SHORT_CIRCUIT_TEMP_C = 45.0;
constexpr float SHORT_CIRCUIT_VOLTAGE_DROP_V = 0.15;
constexpr float SHORT_CIRCUIT_TEMP_RISE_C = 2.0;
constexpr float SELF_DISCHARGE_CURRENT_A = 0.1;
constexpr float SELF_DISCHARGE_VOLTAGE_DROP_V = 0.05;

constexpr unsigned long SIM_TIME_MULTIPLIER = 60UL;
constexpr unsigned long SAMPLE_INTERVAL_MS = 1000UL;
constexpr unsigned long TREND_WINDOW_MS = 60000UL;
constexpr unsigned long SELF_DISCHARGE_WINDOW_MS = 3600000UL;
constexpr unsigned long BUZZER_PERIOD_MS = 1000UL;
constexpr unsigned long BUZZER_ON_MS = 180UL;
constexpr unsigned int BUZZER_LOW_FREQUENCY_HZ = 700;
constexpr unsigned int BUZZER_HIGH_FREQUENCY_HZ = 2400;

OneWire oneWire(BATTERY_TEMP_PIN);
DallasTemperature temperatureSensor(&oneWire);
Adafruit_SSD1306 display(
  SCREEN_WIDTH,
  SCREEN_HEIGHT,
  &Wire,
  -1
);

struct Reading {
  float voltage;
  float current;
  float batteryTemperature;
};

struct Alerts {
  bool thermalRunaway = false;
  bool highTemperature = false;
  bool internalShort = false;
  bool selfDischarge = false;
  bool overvoltage = false;
  bool undervoltage = false;
  bool highCurrent = false;
};

Reading latestReading;
Alerts currentAlerts;
bool displayAvailable = false;
bool buzzerOn = false;
unsigned int activeFrequencyHz = 0;
unsigned long lastSampleMs = 0;
unsigned long temperatureWindowStartMs = 0;
unsigned long restingStartMs = 0;
unsigned long restingSamples = 0;
unsigned long previousReadingMs = 0;
float temperatureWindowStartC = NAN;
float restingStartVoltage = NAN;
float previousVoltage = NAN;
float previousTemperature = NAN;

float readVoltage() {
  const int adc = analogRead(VOLTAGE_POT_PIN);
  return 2.50f + (static_cast<float>(adc) / 4095.0f) * 2.00f;
}

float readCurrent() {
  const int adc = analogRead(CURRENT_POT_PIN);
  return (static_cast<float>(adc) / 4095.0f) * 6.00f;
}

bool readSensors(Reading &reading) {
  reading.voltage = readVoltage();
  reading.current = readCurrent();
  temperatureSensor.requestTemperatures();
  reading.batteryTemperature = temperatureSensor.getTempCByIndex(0);

  const bool validTemperature =
    isfinite(reading.batteryTemperature)
    && reading.batteryTemperature != DEVICE_DISCONNECTED_C;
  if (!validTemperature) {
    Serial.printf(
      "DS18B20 read failed: devices=%u, temperature=%.2f C\n",
      temperatureSensor.getDeviceCount(),
      reading.batteryTemperature
    );
  }
  return validTemperature;
}

Alerts evaluateAlerts(const Reading &reading, unsigned long now) {
  Alerts alerts;
  alerts.highTemperature =
    reading.batteryTemperature >= HIGH_TEMPERATURE_ALERT_C;
  alerts.thermalRunaway =
    reading.batteryTemperature >= THERMAL_RUNAWAY_TEMP_C;

  if (isnan(temperatureWindowStartC)) {
    temperatureWindowStartC = reading.batteryTemperature;
    temperatureWindowStartMs = now;
  } else if (now - temperatureWindowStartMs >= 1000UL) {
    const float simulatedMinutes =
      static_cast<float>(now - temperatureWindowStartMs)
      * SIM_TIME_MULTIPLIER / 60000.0f;
    const float riseRate =
      (reading.batteryTemperature - temperatureWindowStartC)
      / simulatedMinutes;
    alerts.thermalRunaway =
      alerts.thermalRunaway
      || riseRate >= THERMAL_RISE_RATE_C_PER_MIN;
    temperatureWindowStartC = reading.batteryTemperature;
    temperatureWindowStartMs = now;
  }

  alerts.internalShort =
    reading.voltage <= SHORT_CIRCUIT_VOLTAGE_V
    && (
      reading.current >= SHORT_CIRCUIT_CURRENT_A
      || reading.batteryTemperature >= SHORT_CIRCUIT_TEMP_C
    );

  const unsigned long elapsedMs = now - previousReadingMs;
  if (previousReadingMs != 0 && elapsedMs <= 1000UL) {
    const float voltageDrop = previousVoltage - reading.voltage;
    const float temperatureRise =
      reading.batteryTemperature - previousTemperature;
    alerts.internalShort =
      alerts.internalShort
      || (
        voltageDrop >= SHORT_CIRCUIT_VOLTAGE_DROP_V
        && (
          reading.current >= SHORT_CIRCUIT_CURRENT_A
          || temperatureRise >= SHORT_CIRCUIT_TEMP_RISE_C
        )
      );
  }

  if (fabs(reading.current) <= SELF_DISCHARGE_CURRENT_A) {
    if (restingStartMs == 0) {
      restingStartMs = now;
      restingStartVoltage = reading.voltage;
      restingSamples = 1;
    } else {
      restingSamples++;
      alerts.selfDischarge =
        (now - restingStartMs) * SIM_TIME_MULTIPLIER
          >= SELF_DISCHARGE_WINDOW_MS
        && restingSamples >= 3
        && restingStartVoltage - reading.voltage
          >= SELF_DISCHARGE_VOLTAGE_DROP_V;
    }
  } else {
    restingStartMs = 0;
    restingStartVoltage = NAN;
    restingSamples = 0;
  }

  alerts.overvoltage = reading.voltage >= 4.1f;
  alerts.undervoltage = reading.voltage <= 3.0f;
  alerts.highCurrent = reading.current >= 3.0f;

  previousVoltage = reading.voltage;
  previousTemperature = reading.batteryTemperature;
  previousReadingMs = now;
  return alerts;
}

bool hasAnyAlert(const Alerts &alerts) {
  return alerts.thermalRunaway
    || alerts.highTemperature
    || alerts.internalShort
    || alerts.selfDischarge
    || alerts.overvoltage
    || alerts.undervoltage
    || alerts.highCurrent;
}

String statusText(const Alerts &alerts) {
  if (alerts.thermalRunaway) return "THERMAL RUNAWAY";
  if (alerts.highTemperature) return "HIGH TEMPERATURE";
  if (alerts.internalShort) return "POSSIBLE SHORT";
  if (alerts.selfDischarge) return "SELF-DISCHARGE";
  if (alerts.overvoltage) return "OVERVOLTAGE";
  if (alerts.undervoltage) return "UNDERVOLTAGE";
  if (alerts.highCurrent) return "HIGH CURRENT";
  return "NORMAL";
}

void updateBuzzer(const Alerts &alerts, unsigned long now) {
  const bool emergency =
    alerts.highTemperature || alerts.thermalRunaway;
  if (!hasAnyAlert(alerts)) {
    noTone(BUZZER_PIN);
    buzzerOn = false;
    activeFrequencyHz = 0;
    return;
  }

  const unsigned int desiredFrequency =
    emergency
      ? BUZZER_HIGH_FREQUENCY_HZ
      : BUZZER_LOW_FREQUENCY_HZ;
  if (now % BUZZER_PERIOD_MS < BUZZER_ON_MS) {
    if (!buzzerOn || activeFrequencyHz != desiredFrequency) {
      tone(BUZZER_PIN, desiredFrequency);
      buzzerOn = true;
      activeFrequencyHz = desiredFrequency;
    }
  } else if (buzzerOn) {
    noTone(BUZZER_PIN);
    buzzerOn = false;
    activeFrequencyHz = 0;
  }
}

void updateDisplay(const Reading &reading, const Alerts &alerts) {
  if (!displayAvailable) return;

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("WOKWI BATTERY MONITOR");
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);
  display.setCursor(0, 14);
  display.printf("Voltage: %.2f V", reading.voltage);
  display.setCursor(0, 25);
  display.printf("Current: %.2f A", reading.current);
  display.setCursor(0, 36);
  display.printf("Battery: %.1f C", reading.batteryTemperature);
  display.setCursor(0, 49);
  display.print("STATUS: ");
  display.println(statusText(alerts));
  display.display();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  pinMode(VOLTAGE_POT_PIN, INPUT);
  pinMode(CURRENT_POT_PIN, INPUT);

  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  displayAvailable = display.begin(
    SSD1306_SWITCHCAPVCC,
    OLED_ADDRESS
  );
  if (displayAvailable) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.display();
  }

  temperatureSensor.begin();
  temperatureSensor.setWaitForConversion(true);
  temperatureSensor.setResolution(12);
  const uint8_t temperatureDeviceCount =
    temperatureSensor.getDeviceCount();
  Serial.printf(
    "DS18B20 devices found on GPIO%u: %u\n",
    BATTERY_TEMP_PIN,
    temperatureDeviceCount
  );
  if (temperatureDeviceCount == 0) {
    Serial.println(
      "Check diagram.json: DS18B20 DQ -> GPIO4, VCC -> 3V3, GND -> GND."
    );
  }
  Serial.println("WOKWI SIMULATOR READY");
  Serial.println("voltage,current,battery_temp");
  Serial.println(
    "Self-discharge demo time is accelerated: 1 simulated hour = 1 real minute."
  );
}

void loop() {
  const unsigned long now = millis();
  if (now - lastSampleMs < SAMPLE_INTERVAL_MS) return;
  lastSampleMs = now;

  if (!readSensors(latestReading)) {
    Serial.println("ERROR: DS18B20 not detected");
    noTone(BUZZER_PIN);
    buzzerOn = false;
    activeFrequencyHz = 0;
    return;
  }

  currentAlerts = evaluateAlerts(latestReading, now);
  updateBuzzer(currentAlerts, now);
  updateDisplay(latestReading, currentAlerts);

  // Keep the first three CSV fields compatible with the PC dashboard parser.
  Serial.print(latestReading.voltage, 3);
  Serial.print(",");
  Serial.print(latestReading.current, 3);
  Serial.print(",");
  Serial.println(latestReading.batteryTemperature, 2);

  if (hasAnyAlert(currentAlerts)) {
    Serial.print("ALERT: ");
    Serial.println(statusText(currentAlerts));
  }
}
