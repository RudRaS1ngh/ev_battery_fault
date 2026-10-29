
from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from export_esp32_model import export_model_header, verify_exported_predictor

ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "ev_battery_test_data (1).xlsx"
MODELS_DIR = ROOT / "models"
OUTPUTS_DIR = ROOT / "outputs"
METRICS_PATH = OUTPUTS_DIR / "model_metrics.json"

TARGET = "fault_label"

MODEL_FEATURES = {
    "model_a": [
        "voltage_V",
        "current_A",
        "battery_temp_C",
    ],
    "model_b": [
        "voltage_V",
        "current_A",
        "battery_temp_C",
        "ambient_temp_C",
    ],
}

# Expected outlier fraction within the normal training data.
# This is a tuning parameter, not a measured battery fault rate.
CONTAMINATION = 0.02


def load_dataset() -> pd.DataFrame:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATASET_PATH}\n"
            "Place ev_battery_test_data (1).xlsx beside this script."
        )

    df = pd.read_excel(DATASET_PATH, sheet_name="Data")

    required = set(
        [TARGET]
        + MODEL_FEATURES["model_a"]
        + MODEL_FEATURES["model_b"]
    )
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Dataset is missing required columns: {sorted(missing)}"
        )

    df = df.copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")

    for column in MODEL_FEATURES["model_b"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=[TARGET])
    df = df[df[TARGET].isin([0, 1])]
    df[TARGET] = df[TARGET].astype(int)

    df = df.dropna(subset=MODEL_FEATURES["model_b"], how="all")

    if df.empty:
        raise ValueError("The dataset has no usable sensor readings.")

    if not (df[TARGET] == 0).any():
        raise ValueError(
            "No normal rows found. Approach B requires rows with "
            "fault_label = 0 to select normal training examples."
        )

    return df


def make_model() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "isolation_forest",
                IsolationForest(
                    n_estimators=200,
                    contamination=CONTAMINATION,
                    random_state=42,
                ),
            ),
        ]
    )


def evaluate_model(
    model: Pipeline,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> dict:
    # Isolation Forest: 1 = inlier, -1 = anomaly.
    # Convert to project labels: 0 = normal, 1 = anomaly.
    raw_predictions = model.predict(X_test)
    predictions = (raw_predictions == -1).astype(int)

    return {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision": float(
            precision_score(y_test, predictions, zero_division=0)
        ),
        "recall": float(
            recall_score(y_test, predictions, zero_division=0)
        ),
        "f1_score": float(
            f1_score(y_test, predictions, zero_division=0)
        ),
        "confusion_matrix": confusion_matrix(
            y_test, predictions, labels=[0, 1]
        ).tolist(),
        "test_samples": int(len(y_test)),
        "normal_training_samples": None,
        "anomaly_predictions": int(predictions.sum()),
    }


def train_model_for_features(
    df: pd.DataFrame,
    model_name: str,
    features: list[str],
) -> dict:
    # Split the dataset first. Test labels are not used to fit the model.
    train_df, test_df = train_test_split(
        df,
        test_size=0.25,
        random_state=42,
        stratify=df[TARGET],
    )

    # Approach B: select normal rows ONLY from the training partition.
    # fault_label is used for selection, never as an input feature.
    normal_train = train_df[train_df[TARGET] == 0]
    X_train_normal = normal_train[features]

    if len(X_train_normal) < 10:
        raise ValueError(
            f"{model_name}: only {len(X_train_normal)} normal training "
            "rows remain. More normal training samples are recommended."
        )

    X_test = test_df[features]
    y_test = test_df[TARGET]

    model = make_model()

    # Unsupervised fit: only sensor features are supplied.
    model.fit(X_train_normal)

    # Evaluate only after fitting, using held-out labels.
    metrics = evaluate_model(model, X_test, y_test)
    metrics["model"] = model_name
    metrics["algorithm"] = "Isolation Forest (unsupervised)"
    metrics["features"] = features
    metrics["normal_training_samples"] = int(len(X_train_normal))
    metrics["total_training_partition_samples"] = int(len(train_df))
    metrics["contamination"] = CONTAMINATION

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODELS_DIR / f"{model_name}.pkl")

    print(f"\n{model_name} trained")
    print(f"Features: {features}")
    print(f"Normal training rows: {len(X_train_normal)}")
    print(f"Test rows: {len(X_test)}")
    print(f"Accuracy: {metrics['accuracy']:.4f}")
    print(f"Precision: {metrics['precision']:.4f}")
    print(f"Recall: {metrics['recall']:.4f}")
    print(f"F1 score: {metrics['f1_score']:.4f}")
    print(f"Confusion matrix: {metrics['confusion_matrix']}")

    return metrics


def train_and_save_models() -> dict:
    df = load_dataset()

    # Ensure both classes exist so the held-out evaluation is meaningful.
    if df[TARGET].nunique() < 2:
        raise ValueError(
            "Evaluation requires both normal (0) and fault (1) labels."
        )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    comparison = []

    for model_name, features in MODEL_FEATURES.items():
        metrics = train_model_for_features(
            df=df,
            model_name=model_name,
            features=features,
        )
        comparison.append(metrics)

    output = {
        "algorithm": "Isolation Forest",
        "training_approach": (
            "Approach B: train only on normal rows from the training split"
        ),
        "target_definition": {
            "0": "Normal",
            "1": "Fault/anomaly according to dataset label",
        },
        "contamination": CONTAMINATION,
        "comparison": comparison,
    }

    with METRICS_PATH.open("w", encoding="utf-8") as file:
        json.dump(output, file, indent=4)

    verify_exported_predictor(MODELS_DIR / "model_b.pkl")
    edge_model_path = export_model_header(
        MODELS_DIR / "model_b.pkl",
        ROOT / "esp32_model_data.h",
    )
    print(f"ESP32 model header saved to: {edge_model_path}")
    print(f"\nMetrics saved to: {METRICS_PATH}")
    print("Training completed.")

    return output


if __name__ == "__main__":
    train_and_save_models()
