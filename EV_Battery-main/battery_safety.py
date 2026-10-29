"""Configurable time-series indicators for prototype battery monitoring."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import median, pstdev
from typing import Mapping, Sequence


VOLTAGE_Z_THRESHOLD = 3.0
VOLTAGE_BASELINE_WINDOW = timedelta(minutes=5)
VOLTAGE_BASELINE_MIN_SAMPLES = 10
VOLTAGE_EPSILON = 1e-6

THERMAL_RUNAWAY_TEMP_C = 55.0
THERMAL_RISE_RATE_C_PER_MIN = 1.0
THERMAL_RATE_WINDOW = timedelta(seconds=60)
THERMAL_MIN_RATE_SPAN = timedelta(seconds=30)
THERMAL_CORROBORATION_TEMP_C = 45.0
THERMAL_CORROBORATION_CURRENT_A = 3.0

CELL_DEVIATION_Z_THRESHOLD = 3.5
CELL_DEVIATION_PERSISTENCE = 3
CELL_DEVIATION_EPSILON_V = 1e-6

SELF_DISCHARGE_CURRENT_A = 0.1
SELF_DISCHARGE_WINDOW = timedelta(hours=1)
SELF_DISCHARGE_MIN_SAMPLES = 3
SELF_DISCHARGE_MAX_TEMP_SPREAD_C = 2.0
EXPECTED_REST_VOLTAGE_SLOPE_V_PER_HOUR = 0.0
SELF_DISCHARGE_SLOPE_MARGIN_V_PER_HOUR = 0.05

Reading = Mapping[str, object]


def _timed_samples(
    history: Sequence[Reading],
    timestamp: datetime,
    window: timedelta,
) -> list[Reading]:
    return sorted(
        (
            sample
            for sample in history
            if isinstance(sample.get("timestamp"), datetime)
            and timedelta(0) <= timestamp - sample["timestamp"] <= window
        ),
        key=lambda sample: sample["timestamp"],
    )


def calculate_voltage_zscore(
    voltage: float,
    history: Sequence[Reading],
    timestamp: datetime,
    *,
    window: timedelta = VOLTAGE_BASELINE_WINDOW,
    min_samples: int = VOLTAGE_BASELINE_MIN_SAMPLES,
    epsilon: float = VOLTAGE_EPSILON,
) -> float | None:
    """Standardize voltage against earlier readings in the rolling window."""
    baseline = _timed_samples(history, timestamp, window)
    values = [
        float(sample["voltage"])
        for sample in baseline
        if sample.get("voltage") is not None
        and math.isfinite(float(sample["voltage"]))
    ]
    if len(values) < min_samples:
        return None

    standard_deviation = pstdev(values)
    return (float(voltage) - sum(values) / len(values)) / (
        standard_deviation + epsilon
    )


def calculate_cell_voltage_zscores(
    cell_voltages: Sequence[float],
    *,
    epsilon: float = CELL_DEVIATION_EPSILON_V,
) -> list[float] | None:
    """Return robust standardized deviations from same-pack cell peers."""
    if len(cell_voltages) < 3:
        return None

    voltages = [float(voltage) for voltage in cell_voltages]
    if not all(math.isfinite(voltage) for voltage in voltages):
        return None

    pack_median = median(voltages)
    deviations = [voltage - pack_median for voltage in voltages]
    deviation_median = median(deviations)
    mad = median(abs(value - deviation_median) for value in deviations)
    robust_scale = 1.4826 * mad

    return [
        (value - deviation_median) / (robust_scale + epsilon)
        for value in deviations
    ]


def detect_persistent_cell_deviation(
    cell_voltages: Sequence[float],
    history: Sequence[Reading],
    *,
    z_threshold: float = CELL_DEVIATION_Z_THRESHOLD,
    persistence: int = CELL_DEVIATION_PERSISTENCE,
) -> dict[str, float | int] | None:
    """Flag a cell that remains a robust outlier across consecutive samples."""
    current_zscores = calculate_cell_voltage_zscores(cell_voltages)
    if current_zscores is None or persistence < 1:
        return None

    prior_vectors = [
        sample.get("cell_voltages")
        for sample in history
        if isinstance(sample.get("cell_voltages"), (list, tuple))
    ]
    zscore_vectors = []
    for vector in prior_vectors[-(persistence - 1):] if persistence > 1 else []:
        zscores = calculate_cell_voltage_zscores(vector)
        if zscores is not None and len(zscores) == len(current_zscores):
            zscore_vectors.append(zscores)

    for cell_index, zscore in enumerate(current_zscores):
        if abs(zscore) <= z_threshold:
            continue
        consecutive_scores = [
            vector[cell_index] for vector in zscore_vectors
        ] + [zscore]
        if (
            len(consecutive_scores) >= persistence
            and all(abs(score) > z_threshold for score in consecutive_scores)
            and all(
                math.copysign(1, score) == math.copysign(1, zscore)
                for score in consecutive_scores
            )
        ):
            return {
                "cell_index": cell_index,
                "zscore": zscore,
            }
    return None


def calculate_rest_voltage_slope(
    history: Sequence[Reading],
    timestamp: datetime,
    *,
    window: timedelta = SELF_DISCHARGE_WINDOW,
    current_limit_a: float = SELF_DISCHARGE_CURRENT_A,
    max_temperature_spread_c: float = SELF_DISCHARGE_MAX_TEMP_SPREAD_C,
    min_samples: int = SELF_DISCHARGE_MIN_SAMPLES,
) -> float | None:
    """Estimate rested voltage slope in V/hour by ordinary least squares.

    Returns no estimate unless the window spans the full duration, all samples
    are at low current, and measured battery temperature remains comparable.
    """
    samples = _timed_samples(history, timestamp, window)
    if len(samples) < min_samples:
        return None
    if timestamp - samples[0]["timestamp"] < window:
        return None

    try:
        currents = [abs(float(sample["current"])) for sample in samples]
        voltages = [float(sample["voltage"]) for sample in samples]
        temperatures = [
            float(sample["battery_temp"])
            for sample in samples
            if sample.get("battery_temp") is not None
        ]
    except (KeyError, TypeError, ValueError):
        return None
    if not all(
        math.isfinite(value)
        for value in currents + voltages + temperatures
    ):
        return None
    if any(current > current_limit_a for current in currents):
        return None
    if len(temperatures) != len(samples):
        return None
    if max(temperatures) - min(temperatures) > max_temperature_spread_c:
        return None

    times_hours = [
        (sample["timestamp"] - samples[0]["timestamp"]).total_seconds()
        / 3600
        for sample in samples
    ]
    mean_time = sum(times_hours) / len(times_hours)
    mean_voltage = sum(voltages) / len(voltages)
    denominator = sum((time_value - mean_time) ** 2 for time_value in times_hours)
    if denominator == 0:
        return None
    return sum(
        (time_value - mean_time) * (voltage - mean_voltage)
        for time_value, voltage in zip(times_hours, voltages)
    ) / denominator


def detect_safety_alerts(
    reading: Mapping[str, object],
    history: Sequence[Reading],
    timestamp: datetime,
    *,
    voltage_z_threshold: float = VOLTAGE_Z_THRESHOLD,
    cell_deviation_z_threshold: float = CELL_DEVIATION_Z_THRESHOLD,
    cell_deviation_persistence: int = CELL_DEVIATION_PERSISTENCE,
    expected_rest_slope_v_per_hour: float = (
        EXPECTED_REST_VOLTAGE_SLOPE_V_PER_HOUR
    ),
    self_discharge_slope_margin_v_per_hour: float = (
        SELF_DISCHARGE_SLOPE_MARGIN_V_PER_HOUR
    ),
) -> list[dict[str, str]]:
    """Calculate configured voltage, thermal, cell-deviation and rest alerts.

    Thresholds must be calibrated for the cell chemistry, pack, and operating
    conditions. The supplied defaults are demonstration settings only.
    """
    samples = _timed_samples(
        history,
        timestamp,
        max(VOLTAGE_BASELINE_WINDOW, THERMAL_RATE_WINDOW),
    )
    current = {**reading, "timestamp": timestamp}
    alerts: list[dict[str, str]] = []

    voltage_zscore = calculate_voltage_zscore(
        float(reading["voltage"]),
        history,
        timestamp,
    )

    thermal_samples = [
        sample
        for sample in samples
        if sample.get("battery_temp") is not None
        and timestamp - sample["timestamp"] <= THERMAL_RATE_WINDOW
    ]
    battery_temperature = float(reading["battery_temp"])
    temperature_rise_rate: float | None = None
    if len(thermal_samples) >= 1:
        first = thermal_samples[0]
        elapsed = timestamp - first["timestamp"]
        if elapsed >= THERMAL_MIN_RATE_SPAN:
            temperature_rise_rate = (
                battery_temperature - float(first["battery_temp"])
            ) / (elapsed.total_seconds() / 60)

    rapid_temperature_rise = (
        temperature_rise_rate is not None
        and temperature_rise_rate >= THERMAL_RISE_RATE_C_PER_MIN
    )
    if battery_temperature >= THERMAL_RUNAWAY_TEMP_C or rapid_temperature_rise:
        alerts.append(
            {
                "type": "Possible_Thermal_Runaway",
                "severity": "Critical" if battery_temperature >= THERMAL_RUNAWAY_TEMP_C else "High",
                "message": (
                    "High battery temperature."
                    if battery_temperature >= THERMAL_RUNAWAY_TEMP_C
                    else (
                        "Battery temperature is rising rapidly "
                        f"({temperature_rise_rate:.2f} °C/min)."
                    ),
                ),
            }
        )
    elif (
        voltage_zscore is not None
        and abs(voltage_zscore) > voltage_z_threshold
        and (
            battery_temperature >= THERMAL_CORROBORATION_TEMP_C
            or rapid_temperature_rise
            or abs(float(reading["current"]))
            >= THERMAL_CORROBORATION_CURRENT_A
        )
    ):
        alerts.append(
            {
                "type": "Possible_Thermal_Runaway",
                "severity": "High",
                "message": (
                    "Rolling voltage anomaly is corroborated by elevated "
                    "temperature or current "
                    f"(voltage z-score {voltage_zscore:.2f})."
                ),
            }
        )
    elif (
        voltage_zscore is not None
        and abs(voltage_zscore) > voltage_z_threshold
    ):
        alerts.append(
            {
                "type": "Voltage_Anomaly",
                "severity": "Investigate",
                "message": (
                    "Voltage deviates from its rolling baseline "
                    f"(z-score {voltage_zscore:.2f})."
                ),
            }
        )

    cell_voltages = reading.get("cell_voltages")
    if isinstance(cell_voltages, (list, tuple)):
        cell_alert = detect_persistent_cell_deviation(
            cell_voltages,
            history,
            z_threshold=cell_deviation_z_threshold,
            persistence=cell_deviation_persistence,
        )
        if cell_alert is not None:
            alerts.append(
                {
                    "type": "Possible_Internal_Short_Circuit",
                    "severity": "High",
                    "message": (
                        f"Cell {cell_alert['cell_index'] + 1} has a persistent "
                        "robust voltage deviation from pack peers "
                        f"(z-score {cell_alert['zscore']:.2f}); investigate."
                    ),
                }
            )

    rest_samples = _timed_samples(history, timestamp, SELF_DISCHARGE_WINDOW)
    rest_samples.append(current)
    rest_slope = calculate_rest_voltage_slope(
        rest_samples,
        timestamp,
    )
    slope_limit = (
        expected_rest_slope_v_per_hour
        - self_discharge_slope_margin_v_per_hour
    )
    if rest_slope is not None and rest_slope < slope_limit:
        alerts.append(
            {
                "type": "Possible_Self_Discharge",
                "severity": "Medium",
                "message": (
                    "Resting voltage is decaying faster than the configured "
                    f"baseline ({rest_slope:.4f} V/hour; limit "
                    f"{slope_limit:.4f} V/hour)."
                ),
            }
        )

    return alerts
