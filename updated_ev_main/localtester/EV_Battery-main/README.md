# Edge-Based Electric Vehicle Battery Monitoring and Anomaly Detection

This project is a beginner-friendly prototype for a battery monitoring dashboard that can run in two modes:

- Simulation mode: works without hardware and demonstrates normal and faulty conditions.
- ESP32 Serial mode: reads live values from a microcontroller over USB serial.

The project uses:

- Python
- Streamlit for the dashboard
- Pandas and NumPy for data handling
- Scikit-learn Isolation Forest for PC-side anomaly detection and ESP32 Model B
- PySerial for USB serial communication
- Arduino C++ prototype firmware for ESP32

## Project files

- `app.py` — Streamlit dashboard
- `train_model.py` — trains the PC models and exports Model B for ESP32
- `wifi_collector.py` — receives live ESP32 readings over local Wi-Fi
- `export_esp32_model.py` — converts the trained model into a C++ header
- `esp32_model_data.h` — generated model parameters included by the firmware
- `esp32_firmware.ino` — standalone ESP32 inference, rules, and alert sketch
- `hardware_wiring.svg` — ESP32 wiring for INA219, DS18B20, DHT22, ILI9341 TFT,
  and buzzer
- `wokwi/` — separate Wokwi simulation with potentiometer-based voltage and
  current inputs, simulated DS18B20, OLED, and buzzer
- `requirements.txt` — Python dependencies
- `ev_battery_test_data (1).xlsx` — original Excel dataset in the project root
- `models/` — saved trained models
- `outputs/` — saved evaluation metrics and exported csv files
- `outputs/esp32_readings.csv` — timestamped, unlabeled Wi-Fi readings

## 1) Install dependencies on Windows PowerShell

```powershell
cd C:\EV_Vehicle
python -m pip install -r requirements.txt
```

If needed, install directly:

```powershell
cd C:\EV_Vehicle
python -m pip install pandas numpy scikit-learn streamlit pyserial openpyxl joblib
```

## 2) Train the models

```powershell
cd C:\EV_Vehicle
python train_model.py
```

This script reads `ev_battery_test_data (1).xlsx`, trains Model A and Model B,
saves the PC models, verifies the embedded Model B predictor, and generates
`esp32_model_data.h`. Upload the firmware after training so the header matches
the current model.

## 3) Start the dashboard

```powershell
cd C:\EV_Vehicle
python -m streamlit run app.py
```

Then open the local URL shown in the terminal, usually:

- http://localhost:8501

## 4) Dashboard usage

### Simulation mode

1. Choose `Simulation mode` in the sidebar.
2. Pick a scenario such as `Normal`, `Overvoltage`, `High_Current`, etc.
3. Adjust the slider values or use the scenario defaults.
4. Click `Generate simulated reading`.
5. The dashboard will display:
   - voltage
   - current
   - battery temperature
   - ambient temperature
   - predicted condition
   - fault type and severity
   - recent table and graphs

This mode works without any ESP32 or sensors.

### Wokwi simulation

The separate [`wokwi/`](./wokwi/) folder contains a Wokwi-ready sketch,
`diagram.json`, and library list. It simulates voltage/current with knobs
because the INA229 is not included as a virtual device; the hardware firmware
remains in `esp32_firmware.ino`. See [`wokwi/README.md`](./wokwi/README.md) for
setup steps and how to demonstrate each alert. Its accelerated
self-discharge timer is only for the Wokwi demo.

### Standalone ESP32 edge mode

The firmware runs without a PC after upload. It uses:

- The generated Model B Isolation Forest for binary anomaly detection.
- On-device rules for thermal warning, over/undervoltage, high current,
  voltage-baseline anomaly, short-circuit investigation heuristics, and
  long-rest voltage-decay investigation.
- The ILI9341 TFT and buzzer for local status and alerts.

Training is still performed offline on the PC. The ESP32 does not continuously
learn or save new training examples. The Wi-Fi collection flow below can
collect readings and retrain the model on the PC. Upload the firmware again
after retraining so the generated header is refreshed.

For Arduino IDE, place `esp32_firmware.ino` and the generated
`esp32_model_data.h` together in a sketch folder named `esp32_firmware`, then
open and upload that sketch.

The firmware uses an INA219 on I²C (GPIO 21/22), one DS18B20 battery
temperature sensor on GPIO 4, a DHT22 ambient temperature/humidity sensor on
GPIO 13, and an SPI ILI9341 TFT (SCK 18, MOSI 23, CS 5, DC 16, RST 17).
Connect the DS18B20 with a 4.7 kΩ pull-up from DQ to 3.3 V. A bare DHT22 also
needs a data-line pull-up; many DHT22 breakout boards already include one. The
INA219 measures the 2S pack voltage and total load current; the model receives
average cell voltage (pack voltage divided by two). This is not per-cell
monitoring and cannot detect an imbalanced individual cell. The DHT22 provides
both ambient temperature and relative humidity.

The Adafruit INA219 firmware calibration is configured for its common 32 V/2 A
setting. Verify the exact breakout shunt and current limits against the combined
load, including motor starting/stall current. Do not use an underrated INA219
board in the motor current path.

The rolling voltage baseline and thermal-rate calculations use RAM history.
Self-discharge evaluation needs about one hour of continuous, low-current
readings with stable temperature; a power loss or restart clears that history.
The internal-short indication is a pack-level heuristic, not a confirmed
cell-level fault diagnosis.

### Optional ESP32 Serial mode

1. Connect the ESP32 to the PC over USB.
2. In the sidebar, choose `ESP32 Serial mode`.
3. Select the COM port and baud rate.
4. Install Adafruit INA219, OneWire, DallasTemperature, Adafruit DHT sensor
   library, Adafruit GFX, and Adafruit ILI9341. Set the Wi-Fi and collector
   configuration, then upload
   `esp32_firmware.ino` to the ESP32.
5. To also display the ESP32 readings in the PC dashboard, click `Connect`.
6. Click `Read one serial message` or enable auto-refresh. This PC display is
   optional; the ESP32's local detection continues without it.

7. With the INA219, DS18B20, and DHT22 connected, the firmware sends average
   cell voltage, current, battery temperature, ambient temperature, and
   humidity:

```text
voltage,current,battery_temperature,ambient_temperature,humidity_percent
```

Example for a single-cell measurement:

```text
3.72,1.25,32.0,24.5,48.5
```

The serial parser accepts the five-value line and still supports older
three/four-value messages. Model B includes humidity when a humidity-aware
model has been trained; until then the compatibility model ignores humidity.

For per-cell monitoring, the serial reader also accepts one JSON object per
line, for example:

```json
{"voltage":14.8,"current":1.25,"battery_temp":32.0,"cell_voltages":[3.70,3.71,3.69,3.70]}
```

`cell_voltages` must contain at least three simultaneous cell measurements
from the same pack. The current ESP32 sketch does not measure or send individual
cell voltages; this JSON form is for a BMS or future multi-cell measurement
source.

The displayed and logged voltage is pack voltage divided by the fixed
`SERIES_CELL_COUNT = 2`; it is average cell voltage, not an individual cell
measurement. Verify the example pin assignments against the exact ESP32 and
TFT module before wiring. The firmware does not control the L293D or motors.
A passive buzzer provides a pulsed 700 Hz tone for non-emergency warnings and
a pulsed 2400 Hz tone for battery temperature at or above 45 °C or a
thermal-runaway indicator. Use a suitable transistor/driver if the buzzer
current exceeds the ESP32 GPIO rating. An active buzzer generally cannot
reproduce selectable tones. The embedded model and thresholds are
demonstrations, not validated battery protection or a certified classifier.

Use two matched 18650 cells in a proper 2S holder with suitable 2S protection,
balancing/charging, and a fuse. Do not charge bare cells through this monitor.
The motor driver and motors can draw high transient/stall current; use a power
path and INA219/shunt rated for that current, and keep motor power off ESP32
logic rails. This prototype is not a certified battery protection system.

### Wi-Fi collection and fully unsupervised retraining

The ESP32 can POST sensor readings to a collector running on the same trusted
local network. The collector writes timestamped CSV rows; the dashboard can
download those rows as an Excel workbook and retrain without using labels.

1. On the PC, find its local network IPv4 address (for example with
   `ipconfig`). Allow inbound TCP port `8765` on the PC firewall for the
   trusted private network only.
2. Start the receiver in one PowerShell window:

   ```powershell
   python wifi_collector.py
   ```

   It listens on port `8765` and appends to
   `outputs/esp32_readings.csv`. Keep this process running while collecting.
   The receiver is intended for a trusted LAN; do not expose or port-forward
   its port to the internet.
3. Open `esp32_firmware.ino` and set `WIFI_SSID`, `WIFI_PASSWORD`, and
   `COLLECTOR_URL` to your Wi-Fi credentials and the PC's local address, for
   example `http://192.168.1.25:8765/readings`. Keep the credentials private.
   The ESP32 and PC must be on the same LAN; guest Wi-Fi or client isolation
   may block communication.
4. Upload the sketch. Every valid sensor sample is sent to the collector.
   Local inference, ILI9341 TFT display, buzzer, and USB serial output continue
   to operate. A Wi-Fi or collector outage only skips upload; the firmware
   retries the connection periodically.
5. In the Streamlit dashboard, refresh the page to view the collected rows or
   download them as `esp32_readings.xlsx`. Stop the collector with Ctrl+C
   before retraining so the CSV is not being appended while it is read.
6. Review the data and confirm the dashboard checkbox before clicking
   `Retrain models from Wi-Fi readings`. This fits Model A and Model B using
   sensor columns only, saves updated PC models, verifies Model B, and
   regenerates `esp32_model_data.h`. Classification metrics are not available
   for this unlabeled training run.
7. Upload the firmware again to deploy the regenerated model to the ESP32.
   The ESP32 does not retrain itself.

The fully unsupervised fit uses all collected samples and assumes anomalies are
a small minority (`contamination=0.02`). It does not know which readings are
healthy. If faulty readings dominate or are included as common patterns, they
can be learned as normal. Collect representative, mostly healthy readings and
review them before retraining. The receiver timestamps each sample when it
arrives at the PC; the ESP32 does not need a real-time clock.

### Battery safety alerts

The dashboard uses time-series indicators:

- Rolling voltage z-score against the previous five minutes (at least ten
  samples, threshold 3.0). A voltage-only deviation is labeled for
  investigation; a thermal-runaway warning from this signal also requires
  elevated temperature or current corroboration. Absolute temperature and
  rapid-temperature-rise indicators remain separate checks.
- Internal-short investigation requires a cell to be a robust peer outlier
  (MAD-based z-score above 3.5) for three consecutive samples. It is not
  calculated when the input contains only one pack/cell voltage.
- Self-discharge uses linear-regression voltage slope across at least one hour
  of near-zero-current samples, and suppresses the result if battery
  temperature changes by more than 2 °C during that interval.

These are configurable prototype defaults, not battery-calibrated safety
thresholds. Baseline voltage, peer-cell comparison, current/rest conditions,
temperature limits, and expected rest-voltage slope require validation using
healthy and fault data for the actual chemistry, pack, SOC, and load. Voltage
relaxation after charging and SOC are not measured by the current interface,
so alerts must be treated as investigation indicators, not diagnoses or
protection controls.

## 5) Serial message format

The dashboard accepts three, four, or five comma-separated values:

1. voltage (V)
2. current (A)
3. battery temperature (°C)
4. optional ambient temperature (°C)
5. optional relative humidity (%), following ambient temperature

The current firmware sends all five values per line. The voltage is the
two-cell pack voltage divided by two. The JSON form is required to provide
per-cell voltage arrays for pack-relative outlier detection.

## 6) ESP32 firmware notes

The Arduino sketch is in `esp32_firmware.ino`. Install Adafruit INA219, OneWire,
DallasTemperature, Adafruit DHT sensor library, Adafruit GFX, and Adafruit
ILI9341 through the Arduino IDE Library Manager. The default example pin mapping
is documented in `hardware_wiring.svg` and the firmware constants.

Important safety note:

- Do not connect an EV battery directly to an ESP32 GPIO pin.
- The INA219 must be wired in series with the monitored pack/load according to
  its breakout documentation and rated for actual motor current.
- Change the example GPIO and display wiring to match the exact modules.
- The DS18B20 measures battery temperature; the DHT22 measures ambient
  temperature and humidity.
- The DHT22 is sampled with the other sensors every two seconds; invalid or
  disconnected sensor readings are skipped rather than uploaded.
- Use a protected 2S battery pack and fuse. The firmware does not provide
  battery cutoff, balancing, or motor control.

This firmware is a prototype because the actual hardware is not available in
this workspace.

## 7) Dataset information

The project uses the real Excel workbook already present in the root folder:

- `ev_battery_test_data (1).xlsx`

The actual dataset columns are:

- `sample_id`
- `timestamp`
- `voltage_V`
- `current_A`
- `battery_temp_C`
- `ambient_temp_C`
- `humidity_percent` (available in newly collected Wi-Fi data; absent in the
  original workbook and imputed until a humidity dataset is collected)
- `scenario`
- `fault_label`
- `fault_type`

The target label is `fault_label` and the values are:

- `0` = Normal
- `1` = Fault present

The data is synthetic illustrative data for software demonstration. It should not be treated as battery safety validation data.

## 8) Model comparison

Two models are trained on the same split:

- Model A: battery voltage, current, battery temperature
- Model B: voltage, current, battery temperature, ambient temperature, and
  relative humidity

The dashboard shows accuracy, precision, recall, F1-score, and confusion
matrix information for both models. Model B, which includes ambient
temperature, is exported for the ESP32. Isolation Forest
identifies an unusual reading but does not identify a physical cause; the
firmware's fault-type names come from separate prototype rules.

## 9) CSV export

The dashboard includes a CSV export button for the recent readings.

## 10) Demo procedure

1. Install requirements.
2. Run `python train_model.py`.
3. Run `python -m streamlit run app.py`.
4. Open the page in the browser.
5. Use `Simulation mode` to test the dashboard.
6. Switch to `ESP32 Serial mode` when hardware is available.
7. Connect to the ESP32 COM port and start reading serial data.

## 11) Important note

This is a prototype for demonstration and learning. It is not a validated battery safety system and should not be used for real safety-critical diagnosis without proper engineering validation and hardware testing.
