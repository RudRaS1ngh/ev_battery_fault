import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from wifi_collector import (
    append_reading,
    create_server,
    parse_reading_payload,
)


class WifiCollectorTests(unittest.TestCase):
    def test_parse_reading_payload_requires_finite_sensor_values(self):
        body = {
            "voltage_V": 3.7,
            "current_A": 0.5,
            "battery_temp_C": 25.0,
            "ambient_temp_C": 24.0,
            "humidity_percent": 45.0,
        }

        self.assertEqual(
            parse_reading_payload(json.dumps(body).encode("utf-8")),
            body,
        )

        body["current_A"] = float("inf")
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            parse_reading_payload(json.dumps(body).encode("utf-8"))

    def test_append_reading_creates_timestamped_csv_rows(self):
        reading = {
            "voltage_V": 3.7,
            "current_A": 0.5,
            "battery_temp_C": 25.0,
            "ambient_temp_C": 24.0,
            "humidity_percent": 45.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readings.csv"
            append_reading(path, reading)
            append_reading(path, reading)

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["timestamp"])
        self.assertEqual(float(rows[0]["voltage_V"]), 3.7)

    def test_append_upgrades_previous_reading_file_for_humidity(self):
        legacy_columns = [
            "timestamp",
            "voltage_V",
            "current_A",
            "battery_temp_C",
            "ambient_temp_C",
        ]
        reading = {
            "voltage_V": 3.7,
            "current_A": 0.5,
            "battery_temp_C": 25.0,
            "ambient_temp_C": 24.0,
            "humidity_percent": 45.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readings.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=legacy_columns)
                writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        **{
                            key: value
                            for key, value in reading.items()
                            if key != "humidity_percent"
                        },
                    }
                )

            append_reading(path, reading)
            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["humidity_percent"], "")
        self.assertEqual(float(rows[1]["humidity_percent"]), 45.0)

    def test_http_endpoint_saves_sensor_reading(self):
        body = {
            "voltage_V": 3.7,
            "current_A": 0.5,
            "battery_temp_C": 25.0,
            "ambient_temp_C": 24.0,
            "humidity_percent": 45.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readings.csv"
            server = create_server("127.0.0.1", 0, path)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/readings",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
