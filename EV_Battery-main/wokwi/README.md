# Wokwi battery-monitor simulation

This is a Wokwi-only simulation project. It does not change or replace the
hardware firmware in `../esp32_firmware.ino`.

Wokwi's supported-parts list does not include the INA229, so two potentiometers
stand in for its voltage and current measurements. The DS18B20, SSD1306 OLED,
and passive buzzer are simulated components. The simulator therefore exercises
the application logic and UI, not INA229 electrical behavior or battery physics.

## Run it

1. Open [Wokwi](https://wokwi.com/) and create an ESP32 Arduino project.
2. Copy `sketch.ino` into the project sketch.
3. Copy `diagram.json` and `libraries.txt` into the project files.
4. Start the simulation and use the potentiometer knobs and DS18B20 temperature
   popup to change the readings.
5. View values and `ALERT:` lines in the Serial Monitor; the OLED and buzzer
   update locally.

At startup, the Serial Monitor prints the number of DS18B20 devices found on
GPIO4. It should report `1`. If it reports `0`, verify the diagram wires:
DS18B20 `DQ` to ESP32 GPIO4, `VCC` to `3V3`, `GND` to `GND`, and a 4.7 kΩ
pull-up between `DQ` and `3V3`. The sensor popup should let you adjust its
temperature during the simulation.

The board preset is ESP32 DevKit v1. The pins are voltage knob GPIO34, current
knob GPIO35, DS18B20 GPIO4, buzzer GPIO27, and OLED I2C SDA/SCL GPIO21/GPIO22.

## Demonstrate alerts

- **Normal:** voltage knob around 3.7 V, current below 3 A, temperature below
  45 °C.
- **Possible internal short:** lower voltage knob to 3.0 V or less and raise
  current above 3 A; alternatively set temperature to at least 45 °C.
- **High current / voltage alerts:** set current to at least 3 A, voltage to
  4.1 V or higher, or voltage to 3.0 V or lower.
- **High-temperature emergency tone:** set DS18B20 to 45 °C or above.
- **Thermal-runaway indicator:** set temperature to 55 °C or above. To demo a
  rapid rise, change the DS18B20 temperature sharply while sampling.
- **Possible self-discharge:** set current near zero and keep it there for one
  real minute to establish a resting baseline, then lower the voltage knob by
  at least 0.05 V and wait for the next sample. For this simulation only,
  one real minute represents one simulated hour.

Low-tone alerts use 700 Hz; high-temperature and thermal-runaway alerts use
2400 Hz. Tone values and alert thresholds mirror the prototype's illustrative
logic and are not validated safety limits.

## Limitations

- Potentiometers approximate voltage/current values; they are not a simulated
  INA229 device.
- Only battery temperature is included. Ambient temperature is not simulated.
- The self-discharge timer is accelerated. Do not copy this acceleration into
  real hardware firmware.
- This simulation is for demonstration and software testing only, not battery
  safety testing or protection.
