"""Export a trained Isolation Forest for ESP32 inference."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "model_b.pkl"
DEFAULT_OUTPUT = ROOT / "esp32_model_data.h"
MODEL_FEATURES = {
    "model_a": ["voltage_V", "current_A", "battery_temp_C"],
    "model_b": [
        "voltage_V",
        "current_A",
        "battery_temp_C",
        "ambient_temp_C",
    ],
}
EXPECTED_FEATURES = MODEL_FEATURES["model_b"]
EULER_GAMMA = 0.5772156649015329


def average_path_length(sample_count: int) -> float:
    if sample_count <= 1:
        return 0.0
    if sample_count == 2:
        return 1.0
    return (
        2.0 * (math.log(sample_count - 1) + EULER_GAMMA)
        - 2.0 * (sample_count - 1) / sample_count
    )


def _float_literal(value: float) -> str:
    return f"{float(value):.17g}"


def export_model_header(
    model_path: Path = DEFAULT_MODEL,
    output_path: Path = DEFAULT_OUTPUT,
) -> Path:
    pipeline = joblib.load(model_path)
    model_features = list(pipeline.feature_names_in_)
    if model_features not in MODEL_FEATURES.values():
        raise ValueError(
            "ESP32 export requires Model A or Model B feature order."
        )
    feature_count = len(model_features)

    forest = pipeline.named_steps["isolation_forest"]
    imputer = pipeline.named_steps["imputer"]
    if len(forest.estimators_) > 65535:
        raise ValueError("ESP32 export supports at most 65535 trees.")

    tree_offsets = [0]
    nodes: list[tuple[int, int, int, float, float]] = []
    for estimator, features in zip(
        forest.estimators_,
        forest.estimators_features_,
    ):
        tree = estimator.tree_
        if tree.node_count > 65534:
            raise ValueError("A tree is too large for the ESP32 node format.")
        if list(features) != list(range(feature_count)):
            raise ValueError(
                "ESP32 export expects every tree to use all model features."
            )

        for node_index in range(tree.node_count):
            left = int(tree.children_left[node_index])
            right = int(tree.children_right[node_index])
            if left == right:
                nodes.append(
                    (
                        65535,
                        65535,
                        255,
                        0.0,
                        average_path_length(
                            int(tree.n_node_samples[node_index])
                        ),
                    )
                )
            else:
                feature = int(tree.feature[node_index])
                if not 0 <= feature < feature_count:
                    raise ValueError(
                        f"Unexpected feature index {feature} in tree."
                    )
                nodes.append(
                    (
                        left,
                        right,
                        feature,
                        float(tree.threshold[node_index]),
                        0.0,
                    )
                )
        tree_offsets.append(len(nodes))

    denominator = average_path_length(int(forest.max_samples_))
    if denominator <= 0:
        raise ValueError("Model has an invalid max_samples value.")

    lines = [
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "namespace Esp32ModelData {",
        "struct Node {",
        "  uint16_t left;",
        "  uint16_t right;",
        "  uint8_t feature;",
        "  double threshold;",
        "  double leafCorrection;",
        "};",
        "",
        f"constexpr uint16_t TREE_COUNT = {len(forest.estimators_)};",
        f"constexpr uint16_t NODE_COUNT = {len(nodes)};",
        f"constexpr uint8_t FEATURE_COUNT = {feature_count};",
        f"constexpr double SCORE_DENOMINATOR = {_float_literal(denominator)};",
        f"constexpr double ANOMALY_SCORE_THRESHOLD = "
        f"{_float_literal(-float(forest.offset_))};",
        f"constexpr double IMPUTER_MEDIANS[{feature_count}] = {{"
        + ", ".join(_float_literal(v) for v in imputer.statistics_)
        + "};",
        "static const uint16_t TREE_OFFSETS[TREE_COUNT + 1] = {",
        "  " + ", ".join(str(value) for value in tree_offsets),
        "};",
        "static const Node NODES[NODE_COUNT] = {",
    ]

    for left, right, feature, threshold, correction in nodes:
        lines.append(
            "  {"
            f"{left}, {right}, {feature}, "
            f"{_float_literal(threshold)}, {_float_literal(correction)}"
            "},"
        )
    lines.extend(["};", "}  // namespace Esp32ModelData", ""])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def embedded_predict(pipeline, values: list[float]) -> int:
    """Evaluate the exported representation; return 1 for anomaly."""
    forest = pipeline.named_steps["isolation_forest"]
    imputer = pipeline.named_steps["imputer"]
    if len(values) != len(pipeline.feature_names_in_):
        raise ValueError(
            f"Model expects {len(pipeline.feature_names_in_)} values, "
            f"received {len(values)}."
        )
    features = np.asarray(values, dtype=float)
    features = np.where(np.isnan(features), imputer.statistics_, features)
    features = features.astype(np.float32)

    path_lengths = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        node_index = 0
        depth = 0
        while tree.children_left[node_index] != tree.children_right[node_index]:
            feature = int(tree.feature[node_index])
            node_index = int(
                tree.children_left[node_index]
                if features[feature] <= tree.threshold[node_index]
                else tree.children_right[node_index]
            )
            depth += 1
        path_length = depth + average_path_length(
            int(tree.n_node_samples[node_index])
        )
        path_lengths.append(path_length)

    mean_path_length = float(np.mean(path_lengths))
    score = 2.0 ** (
        -mean_path_length / average_path_length(forest.max_samples_)
    )
    return int(score > -float(forest.offset_))


def verify_exported_predictor(model_path: Path = DEFAULT_MODEL) -> None:
    pipeline = joblib.load(model_path)
    features = list(pipeline.feature_names_in_)
    test_vectors = [
        [3.7, 0.5, 25.0, 24.0],
        [4.3, 5.0, 60.0, 40.0],
        [2.8, 0.0, 20.0, 18.0],
        [float("nan"), 1.0, 30.0, 25.0],
    ]
    for values in test_vectors:
        values = values[:len(features)]
        frame = pd.DataFrame([values], columns=features)
        expected = int(pipeline.predict(frame)[0] == -1)
        actual = embedded_predict(pipeline, values)
        if actual != expected:
            raise AssertionError(
                f"Embedded model mismatch for {values}: "
                f"expected {expected}, got {actual}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    verify_exported_predictor(args.model)
    path = export_model_header(args.model, args.output)
    print(f"ESP32 model header generated: {path}")


if __name__ == "__main__":
    main()
