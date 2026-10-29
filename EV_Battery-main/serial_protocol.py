"""Parser for ESP32 battery-monitor serial readings."""

from __future__ import annotations

import json
import math


def parse_serial_line(raw_line: str) -> dict[str, object] | None:
    """Parse three/four CSV values or JSON with optional per-cell voltages."""
    line = str(raw_line).strip()
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        required = ("voltage", "current", "battery_temp")
        if any(key not in data for key in required):
            return None
        try:
            reading: dict[str, object] = {
                key: float(data[key]) for key in required
            }
            if "ambient_temp" in data and data["ambient_temp"] is not None:
                reading["ambient_temp"] = float(data["ambient_temp"])
            else:
                reading["ambient_temp"] = None
            if "cell_voltages" in data:
                cell_voltages = data["cell_voltages"]
                if not isinstance(cell_voltages, list) or len(cell_voltages) < 3:
                    return None
                reading["cell_voltages"] = [
                    float(value) for value in cell_voltages
                ]
        except (TypeError, ValueError):
            return None
        numeric_values = [
            reading[key]
            for key in ("voltage", "current", "battery_temp", "ambient_temp")
            if reading.get(key) is not None
        ]
        if "cell_voltages" in reading:
            numeric_values.extend(reading["cell_voltages"])
        if not all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in numeric_values
        ):
            return None
        return reading

    parts = line.split(",")
    if len(parts) not in {3, 4}:
        return None

    try:
        values = [float(part.strip()) for part in parts]
    except ValueError:
        return None

    if not all(math.isfinite(value) for value in values):
        return None

    reading: dict[str, object] = {
        "voltage": values[0],
        "current": values[1],
        "battery_temp": values[2],
        "ambient_temp": None,
    }
    if len(values) == 4:
        reading["ambient_temp"] = values[3]
    return reading
