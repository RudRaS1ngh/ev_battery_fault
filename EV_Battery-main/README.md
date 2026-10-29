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
- `export_esp32_model.py` — converts the trained model into a C++ header
- `esp32_model_data.h` — generated model parameters included by the firmware
- `esp32_firmware.ino` — standalone ESP32 inference, rules, and alert sketch
- `hardware_wiring.svg` — current ESP32 wiring diagram for INA229, OLED,
  buzzer driver, and both DS18B20 sensors
- `wokwi/` — separate Wokwi simulation with potentiometer-based voltage and
  current inputs, simulated DS18B20, OLED, and buzzer
- `requirements.txt` — Python dependencies
- `ev_battery_test_data (1).xlsx` — original Excel dataset in the project root
- `models/` — saved trained models
- `outputs/` — saved evaluation metrics and exported csv files

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
- The OLED and buzzer for local status and alerts.

Training is still performed offline on the PC. The ESP32 does not continuously
learn or save new training examples. After changing the dataset or training
configuration, run `python train_model.py` and upload the firmware again so
the generated header is refreshed.

For Arduino IDE, place `esp32_firmware.ino` and the generated
`esp32_model_data.h` together in a sketch folder named `esp32_firmware`, then
open and upload that sketch.

The firmware uses one INA229 voltage/current measurement and two DS18B20
sensors: one attached to the battery and one measuring surrounding air. Connect
both DS18B20 devices in parallel to GPIO 4, with both data pins on the same
OneWire line, shared 3.3 V/GND, and a single 4.7 kOhm pull-up from data to
3.3 V. Set `BATTERY_SENSOR_INDEX` and `AMBIENT_SENSOR_INDEX` to match their
enumerated order. At startup, the ESP32 prints each index and ROM address;
identify which physical sensor is which (for example, briefly warm one sensor)
and change the indices if needed. Set `SERIES_CELL_COUNT` to the number of
series cells if the INA229 measures the complete series pack. The model and voltage rules use the
resulting average cell voltage. This average cannot replace individual cell
measurements: cell-to-cell internal-short detection still requires simultaneous
per-cell voltage data. EIS-based detection requires separate EIS hardware.

The rolling voltage baseline and thermal-rate calculations use RAM history.
Self-discharge evaluation needs about one hour of continuous, low-current
readings with stable temperature; a power loss or restart clears that history.
The internal-short indication is a pack-level heuristic, not a confirmed
cell-level fault diagnosis.

### Optional ESP32 Serial mode

1. Connect the ESP32 to the PC over USB.
2. In the sidebar, choose `ESP32 Serial mode`.
3. Select the COM port and baud rate.
4. Install the Arduino libraries listed in the firmware comments, set the
   GPIO pins and INA229 shunt calibration to match your hardware, then upload
   `esp32_firmware.ino` to the ESP32.
5. To also display the ESP32 readings in the PC dashboard, click `Connect`.
6. Click `Read one serial message` or enable auto-refresh. This PC display is
   optional; the ESP32's local detection continues without it.
7. With the INA229 and both DS18B20 sensors connected, the firmware sends
   average cell voltage, current, battery temperature, and ambient temperature:

```text
voltage,current,battery_temperature,ambient_temperature
```

Example for a single-cell measurement:

```text
3.72,1.25,32.0,24.5
```

The app's existing serial parser accepts this four-value line and selects
Model B when ambient temperature is present. It still accepts three-value
lines from older firmware and falls back to Model A for those inputs.

For per-cell monitoring, the serial reader also accepts one JSON object per
line, for example:

```json
{"voltage":14.8,"current":1.25,"battery_temp":32.0,"cell_voltages":[3.70,3.71,3.69,3.70]}
```

`cell_voltages` must contain at least three simultaneous cell measurements
from the same pack. The current ESP32 sketch does not measure or send individual
cell voltages; this JSON form is for a BMS or future multi-cell measurement
source.

For real readings, calibrate the INA229 shunt resistance and expected maximum
current in the sketch to the module’s fitted shunt. The serial voltage is pack
voltage divided by `SERIES_CELL_COUNT` for Model B compatibility; it is an
average cell voltage, not an individual cell measurement. The default pin
assignments are examples for a common ESP32 DevKit and must be checked against
your board.
A passive buzzer provides a pulsed 700 Hz tone for non-emergency warnings and
a pulsed 2400 Hz tone for battery temperature at or above 45 °C or a
thermal-runaway indicator. Use a suitable transistor/driver if the buzzer
current exceeds the ESP32 GPIO rating. An active buzzer generally cannot
reproduce selectable tones. The embedded model and thresholds are
demonstrations, not validated battery protection or a certified classifier.

The INA229 bus input is rated up to 85 V. Do not connect a higher-voltage EV
battery pack to this setup; use a properly rated, isolated measurement system
designed for the pack voltage instead.

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

The dashboard expects this exact order:

1. voltage (V)
2. current (A)
3. battery temperature (°C)
4. optional ambient temperature (°C)

CSV readings must be a single comma-separated line. The JSON form is required
to provide per-cell voltage arrays for pack-relative outlier detection.

## 6) ESP32 firmware notes

The Arduino sketch is in `esp32_firmware.ino`. Install INA229 by Rob Tillaart,
OneWire, DallasTemperature, Adafruit GFX, and Adafruit SSD1306 through the
Arduino IDE Library Manager.

Important safety note:

- Do not connect an EV battery directly to an ESP32 GPIO pin.
- Use a proper safe voltage divider or analog front end for voltage measurement.
- Use a suitable INA229 breakout with a shunt rated for the battery/current.
- Change the example GPIO and shunt configuration in the sketch to match the
  actual board and module.
- Two DS18B20 sensors are required by the updated firmware: one for battery
  temperature and one for ambient temperature.

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
- Model B: voltage, current, battery temperature, and ambient temperature

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
