import tempfile
import unittest
from pathlib import Path

import joblib
import pandas as pd

from export_esp32_model import (
    DEFAULT_MODEL,
    EXPECTED_FEATURES,
    MODEL_FEATURES,
    embedded_predict,
    export_model_header,
    verify_exported_predictor,
)


class Esp32ModelExportTests(unittest.TestCase):
    def test_exported_predictor_matches_trained_model(self):
        pipeline = joblib.load(DEFAULT_MODEL)
        dataset_path = DEFAULT_MODEL.parents[1] / "ev_battery_test_data (1).xlsx"
        data = pd.read_excel(dataset_path, sheet_name="Data")
        vectors = data[EXPECTED_FEATURES].head(30).values.tolist()
        vectors.extend(
            [
                [3.7, 0.5, 25.0, 24.0],
                [4.3, 5.0, 60.0, 40.0],
                [2.8, 0.0, 20.0, 18.0],
                [float("nan"), 1.0, 30.0, 25.0],
            ]
        )

        for values in vectors:
            frame = pd.DataFrame([values], columns=EXPECTED_FEATURES)
            expected = int(pipeline.predict(frame)[0] == -1)
            self.assertEqual(embedded_predict(pipeline, values), expected)

    def test_model_a_export_remains_supported(self):
        model_a_path = DEFAULT_MODEL.with_name("model_a.pkl")
        model_a = joblib.load(model_a_path)
        row = [3.7, 0.5, 25.0]
        frame = pd.DataFrame([row], columns=MODEL_FEATURES["model_a"])
        self.assertEqual(
            embedded_predict(model_a, row),
            int(model_a.predict(frame)[0] == -1),
        )

    def test_export_generates_model_header(self):
        verify_exported_predictor(DEFAULT_MODEL)
        with tempfile.TemporaryDirectory() as directory:
            output = export_model_header(
                DEFAULT_MODEL,
                Path(directory) / "esp32_model_data.h",
            )
            header = output.read_text(encoding="utf-8")

        self.assertIn("TREE_COUNT = 200", header)
        self.assertIn("FEATURE_COUNT = 4", header)
        self.assertIn("ANOMALY_SCORE_THRESHOLD", header)
        self.assertIn("static const Node NODES", header)


if __name__ == "__main__":
    unittest.main()
