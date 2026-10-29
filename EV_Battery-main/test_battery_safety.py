import unittest
from datetime import datetime, timedelta

from battery_safety import (
    calculate_cell_voltage_zscores,
    calculate_rest_voltage_slope,
    calculate_voltage_zscore,
    detect_persistent_cell_deviation,
    detect_safety_alerts,
)


class SafetyAlertTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 12, 0)

    def test_critical_temperature_alert(self):
        alerts = detect_safety_alerts(
            {
                "voltage": 3.7,
                "current": 1.0,
                "battery_temp": 55.0,
            },
            [],
            self.now,
        )
        self.assertIn(
            "Possible_Thermal_Runaway",
            {alert["type"] for alert in alerts},
        )

    def test_rapid_heating_alert(self):
        history = [
            {
                "timestamp": self.now - timedelta(seconds=60),
                "voltage": 3.7,
                "current": 1.0,
                "battery_temp": 30.0,
            }
        ]
        alerts = detect_safety_alerts(
            {
                "voltage": 3.7,
                "current": 1.0,
                "battery_temp": 31.2,
            },
            history,
            self.now,
        )
        self.assertIn(
            "Possible_Thermal_Runaway",
            {alert["type"] for alert in alerts},
        )

    def test_internal_short_circuit_indicator(self):
        history = [
            {
                "timestamp": self.now - timedelta(seconds=2),
                "cell_voltages": [3.70, 3.71, 3.40, 3.69],
            },
            {
                "timestamp": self.now - timedelta(seconds=1),
                "cell_voltages": [3.70, 3.71, 3.40, 3.69],
            },
        ]
        alerts = detect_safety_alerts(
            {
                "voltage": 3.7,
                "current": 0.2,
                "battery_temp": 30.0,
                "cell_voltages": [3.70, 3.71, 3.40, 3.69],
            },
            history,
            self.now,
        )
        self.assertIn(
            "Possible_Internal_Short_Circuit",
            {alert["type"] for alert in alerts},
        )

    def test_self_discharge_requires_resting_voltage_trend(self):
        history = [
            {
                "timestamp": self.now - timedelta(minutes=60),
                "voltage": 3.70,
                "current": 0.0,
                "battery_temp": 25.0,
            },
            {
                "timestamp": self.now - timedelta(minutes=30),
                "voltage": 3.67,
                "current": 0.02,
                "battery_temp": 25.0,
            },
        ]
        alerts = detect_safety_alerts(
            {
                "voltage": 3.64,
                "current": 0.0,
                "battery_temp": 25.0,
            },
            history,
            self.now,
        )
        self.assertIn(
            "Possible_Self_Discharge",
            {alert["type"] for alert in alerts},
        )

    def test_self_discharge_is_suppressed_by_current_load(self):
        history = [
            {
                "timestamp": self.now - timedelta(minutes=60),
                "voltage": 3.70,
                "current": 0.0,
                "battery_temp": 25.0,
            },
            {
                "timestamp": self.now - timedelta(minutes=30),
                "voltage": 3.67,
                "current": 0.5,
                "battery_temp": 25.0,
            },
        ]
        alerts = detect_safety_alerts(
            {
                "voltage": 3.64,
                "current": 0.0,
                "battery_temp": 25.0,
            },
            history,
            self.now,
        )
        self.assertNotIn(
            "Possible_Self_Discharge",
            {alert["type"] for alert in alerts},
        )

    def test_normal_reading_has_no_safety_alert(self):
        alerts = detect_safety_alerts(
            {
                "voltage": 3.7,
                "current": 0.5,
                "battery_temp": 25.0,
            },
            [],
            self.now,
        )
        self.assertEqual(alerts, [])

    def test_voltage_zscore_uses_prior_rolling_baseline(self):
        history = [
            {
                "timestamp": self.now - timedelta(seconds=10 * index),
                "voltage": 3.70 + (0.001 if index % 2 else -0.001),
            }
            for index in range(1, 11)
        ]
        zscore = calculate_voltage_zscore(3.80, history, self.now)
        self.assertIsNotNone(zscore)
        self.assertGreater(abs(zscore), 3.0)

    def test_voltage_anomaly_needs_corroboration_for_thermal_alert(self):
        history = [
            {
                "timestamp": self.now - timedelta(seconds=10 * index),
                "voltage": 3.70 + (0.001 if index % 2 else -0.001),
                "current": 0.5,
                "battery_temp": 30.0,
            }
            for index in range(1, 11)
        ]
        alerts = detect_safety_alerts(
            {
                "voltage": 3.80,
                "current": 0.5,
                "battery_temp": 30.0,
            },
            history,
            self.now,
        )
        self.assertEqual(
            {alert["type"] for alert in alerts},
            {"Voltage_Anomaly"},
        )

    def test_persistent_cell_outlier_uses_robust_peer_deviation(self):
        history = [
            {"cell_voltages": [3.70, 3.71, 3.40, 3.69]},
            {"cell_voltages": [3.70, 3.71, 3.40, 3.69]},
        ]
        result = detect_persistent_cell_deviation(
            [3.70, 3.71, 3.40, 3.69],
            history,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["cell_index"], 2)

    def test_cell_zscores_require_multiple_cells(self):
        self.assertIsNone(calculate_cell_voltage_zscores([3.7, 3.6]))

    def test_rest_slope_uses_linear_regression_and_stable_temperature(self):
        history = [
            {
                "timestamp": self.now - timedelta(minutes=60),
                "voltage": 3.70,
                "current": 0.0,
                "battery_temp": 25.0,
            },
            {
                "timestamp": self.now - timedelta(minutes=30),
                "voltage": 3.67,
                "current": 0.02,
                "battery_temp": 25.5,
            },
            {
                "timestamp": self.now,
                "voltage": 3.64,
                "current": 0.0,
                "battery_temp": 25.2,
            },
        ]
        slope = calculate_rest_voltage_slope(history, self.now)
        self.assertAlmostEqual(slope, -0.06, places=3)

        history[-1] = {**history[-1], "battery_temp": 30.0}
        self.assertIsNone(calculate_rest_voltage_slope(history, self.now))


if __name__ == "__main__":
    unittest.main()
