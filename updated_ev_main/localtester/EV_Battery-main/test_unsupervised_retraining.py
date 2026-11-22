import tempfile
import unittest
from pathlib import Path

import joblib
import pandas as pd

from train_model import (
    MODEL_FEATURES,
    load_dataset,
    make_model,
    retrain_from_collected_readings,
)


class UnsupervisedRetrainingTests(unittest.TestCase):
    def test_original_workbook_remains_trainable_without_humidity_column(self):
        readings = load_dataset()
        self.assertTrue(readings["humidity_percent"].isna().all())

        model = make_model().fit(readings[MODEL_FEATURES["model_b"]])
        self.assertEqual(
            model.named_steps["isolation_forest"].n_features_in_,
            len(MODEL_FEATURES["model_b"]),
        )

    def test_retraining_uses_unlabeled_readings_and_exports_model_b(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readings_path = root / "readings.csv"
            models_dir = root / "models"
            metrics_path = root / "metrics.json"
            header_path = root / "esp32_model_data.h"
            pd.DataFrame(
                {
                    "timestamp": [f"2026-01-01T00:00:{i:02d}Z" for i in range(20)],
                    "voltage_V": [3.6 + i * 0.01 for i in range(20)],
                    "current_A": [0.4 + i * 0.02 for i in range(20)],
                    "battery_temp_C": [24 + i * 0.1 for i in range(20)],
                    "ambient_temp_C": [22 + i * 0.1 for i in range(20)],
                    "humidity_percent": [40 + i * 0.5 for i in range(20)],
                }
            ).to_csv(readings_path, index=False)

            metrics = retrain_from_collected_readings(
                readings_path,
                models_dir,
                metrics_path,
                header_path,
            )

            model_b = joblib.load(models_dir / "model_b.pkl")
            self.assertEqual(
                list(model_b.feature_names_in_),
                [
                    "voltage_V",
                    "current_A",
                    "battery_temp_C",
                    "ambient_temp_C",
                    "humidity_percent",
                ],
            )
            self.assertTrue(header_path.exists())
            self.assertTrue(metrics_path.exists())
            self.assertIn("without labels", metrics["training_approach"])
            self.assertIsNone(metrics["comparison"][0]["accuracy"])

    def test_retraining_requires_minimum_sample_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readings_path = root / "readings.csv"
            pd.DataFrame(
                {
                    "voltage_V": [3.7] * 5,
                    "current_A": [0.5] * 5,
                    "battery_temp_C": [25.0] * 5,
                    "ambient_temp_C": [24.0] * 5,
                    "humidity_percent": [45.0] * 5,
                }
            ).to_csv(readings_path, index=False)

            with self.assertRaisesRegex(ValueError, "at least 10"):
                retrain_from_collected_readings(
                    readings_path,
                    root / "models",
                    root / "metrics.json",
                    root / "header.h",
                )


if __name__ == "__main__":
    unittest.main()
