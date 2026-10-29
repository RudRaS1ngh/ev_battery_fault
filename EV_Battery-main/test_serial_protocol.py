import unittest

from serial_protocol import parse_serial_line


class SerialProtocolTests(unittest.TestCase):
    def test_parses_esp32_three_sensor_reading(self):
        self.assertEqual(
            parse_serial_line("3.72,1.25,32.0"),
            {
                "voltage": 3.72,
                "current": 1.25,
                "battery_temp": 32.0,
                "ambient_temp": None,
            },
        )

    def test_parses_four_sensor_reading_with_ambient_temperature(self):
        self.assertEqual(
            parse_serial_line("3.72,1.25,32.0,28.0"),
            {
                "voltage": 3.72,
                "current": 1.25,
                "battery_temp": 32.0,
                "ambient_temp": 28.0,
            },
        )

    def test_rejects_malformed_or_non_finite_data(self):
        self.assertIsNone(parse_serial_line("3.72,1.25"))
        self.assertIsNone(parse_serial_line("3.72,1.25,32.0,28.0,55.0"))
        self.assertIsNone(parse_serial_line("3.72,not-a-number,32.0"))
        self.assertIsNone(parse_serial_line("3.72,1.25,nan"))

    def test_parses_json_with_cell_voltage_vector(self):
        self.assertEqual(
            parse_serial_line(
                '{"voltage":3.7,"current":1.2,"battery_temp":30,'
                '"cell_voltages":[3.70,3.71,3.42]}'
            ),
            {
                "voltage": 3.7,
                "current": 1.2,
                "battery_temp": 30.0,
                "ambient_temp": None,
                "cell_voltages": [3.7, 3.71, 3.42],
            },
        )

    def test_rejects_invalid_json_cell_voltage_vector(self):
        self.assertIsNone(
            parse_serial_line(
                '{"voltage":3.7,"current":1.2,"battery_temp":30,'
                '"cell_voltages":[3.7, "nan"]}'
            )
        )


if __name__ == "__main__":
    unittest.main()
