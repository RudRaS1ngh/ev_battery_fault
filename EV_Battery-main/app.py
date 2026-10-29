
from __future__ import annotations

import html
import json
import time
from pathlib import Path

import joblib
import pandas as pd
import serial
import streamlit as st
from serial.tools import list_ports

from battery_safety import detect_safety_alerts
from serial_protocol import parse_serial_line
from train_model import train_and_save_models

ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "ev_battery_test_data (1).xlsx"
MODELS_DIR = ROOT / "models"
OUTPUTS_DIR = ROOT / "outputs"
METRICS_PATH = OUTPUTS_DIR / "model_metrics.json"

MODEL_FEATURES = {
    "model_a": ["voltage_V", "current_A", "battery_temp_C"],
    "model_b": [
        "voltage_V",
        "current_A",
        "battery_temp_C",
        "ambient_temp_C",
    ],
}

SCENARIO_VALUES = {
    "Normal": {
        "voltage_V": 3.75, "current_A": 0.74,
        "battery_temp_C": 28.9, "ambient_temp_C": 24.4,
    },
    "High_Battery_Temperature": {
        "voltage_V": 3.72, "current_A": 1.08,
        "battery_temp_C": 48.9, "ambient_temp_C": 31.9,
    },
    "Overvoltage": {
        "voltage_V": 4.28, "current_A": 0.80,
        "battery_temp_C": 30.2, "ambient_temp_C": 24.5,
    },
    "Undervoltage": {
        "voltage_V": 2.84, "current_A": 0.90,
        "battery_temp_C": 28.1, "ambient_temp_C": 24.0,
    },
    "High_Current": {
        "voltage_V": 3.54, "current_A": 4.90,
        "battery_temp_C": 34.7, "ambient_temp_C": 28.1,
    },
    "Hot_Environment": {
        "voltage_V": 3.69, "current_A": 1.03,
        "battery_temp_C": 38.5, "ambient_temp_C": 41.9,
    },
    "Possible_Thermal_Runaway": {
        "voltage_V": 3.65, "current_A": 1.10,
        "battery_temp_C": 56.0, "ambient_temp_C": 25.0,
    },
    "Possible_Internal_Short_Circuit": {
        "voltage_V": 2.85, "current_A": 4.20,
        "battery_temp_C": 46.0, "ambient_temp_C": 25.0,
    },
    "Possible_Self_Discharge": {
        "voltage_V": 3.60, "current_A": 0.0,
        "battery_temp_C": 29.0, "ambient_temp_C": 24.0,
    },
}


# ---------------------------------------------------------
# MODEL LOADING AND TRAINING
# ---------------------------------------------------------

def ensure_model_files():
    required_files = [
        MODELS_DIR / "model_a.pkl",
        MODELS_DIR / "model_b.pkl",
        METRICS_PATH,
    ]

    if not all(path.exists() for path in required_files):
        train_and_save_models()


@st.cache_resource
def load_model(model_name: str):
    ensure_model_files()
    path = MODELS_DIR / f"{model_name}.pkl"

    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")

    return joblib.load(path)


@st.cache_data
def load_metrics() -> dict:
    ensure_model_files()

    with METRICS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def predict_anomaly(model_name: str, reading: dict) -> int:
    """Return 0 for normal/inlier and 1 for anomaly/outlier."""
    model = load_model(model_name)
    features = MODEL_FEATURES[model_name]

    values = {
        "voltage_V": reading["voltage"],
        "current_A": reading["current"],
        "battery_temp_C": reading["battery_temp"],
        "ambient_temp_C": reading.get("ambient_temp"),
    }

    X = pd.DataFrame([
        {
            key: float(values[key])
            if values[key] is not None
            else float("nan")
            for key in features
        }
    ])

    # Isolation Forest: 1 = inlier; -1 = outlier.
    prediction = int(model.predict(X)[0])
    return 1 if prediction == -1 else 0


def summarize_condition(anomaly_value: int) -> str:
    return "Anomaly detected" if anomaly_value == 1 else "Normal"


# ---------------------------------------------------------
# RULE-BASED SAFETY INDICATORS
# These are illustrative project thresholds, not validated
# EV battery safety limits.
# ---------------------------------------------------------

def classify_fault_type(reading: dict) -> tuple[str, str]:
    voltage = reading["voltage"]
    current = reading["current"]
    battery_temp = reading["battery_temp"]
    ambient_temp = reading.get("ambient_temp")

    if battery_temp >= 45.0:
        return "High_Battery_Temperature", "High"
    if voltage >= 4.1:
        return "Overvoltage", "High"
    if voltage <= 3.0:
        return "Undervoltage", "Medium"
    if current >= 3.0:
        return "High_Current", "High"
    if ambient_temp is not None and ambient_temp >= 35.0:
        return "Hot_Environment", "Medium"

    return "No threshold fault identified", "Low"


def get_recent_live_readings() -> list[dict]:
    readings = st.session_state.get("readings", pd.DataFrame())
    if readings.empty:
        return []

    live_readings = readings[readings["source"] != "dataset"]
    return [
        {
            "timestamp": row["timestamp"],
            "voltage": float(row["voltage_V"]),
            "current": float(row["current_A"]),
            "battery_temp": float(row["battery_temp_C"]),
            **(
                {"cell_voltages": row["cell_voltages"]}
                if isinstance(row.get("cell_voltages"), (list, tuple))
                else {}
            ),
        }
        for _, row in live_readings.iterrows()
    ]


def get_reading_status(
    reading: dict,
    model_name: str | None = None,
    history: list[dict] | None = None,
    timestamp: pd.Timestamp | None = None,
) -> dict:
    if model_name is None:
        model_name = (
            "model_b"
            if reading.get("ambient_temp") is not None
            else "model_a"
        )
    anomaly = predict_anomaly(model_name, reading)
    fault_type, severity = classify_fault_type(reading)
    observed_at = timestamp or pd.Timestamp.now()
    safety_alerts = detect_safety_alerts(
        reading,
        get_recent_live_readings() if history is None else history,
        observed_at,
    )

    # Do not hide a rule-based warning just because the ML model
    # considers the reading an inlier.
    threshold_fault = (
        fault_type != "No threshold fault identified" or bool(safety_alerts)
    )

    if safety_alerts:
        fault_type = safety_alerts[0]["type"]
        severity = safety_alerts[0]["severity"]
        display_status = fault_type.replace("_", " ").upper()
    elif threshold_fault:
        display_status = fault_type.replace("_", " ").upper()
    elif anomaly:
        display_status = "UNUSUAL PATTERN"
        fault_type = "Unclassified_Anomaly"
        severity = "Investigate"
    else:
        display_status = "NORMAL"

    return {
        "anomaly": anomaly,
        "condition": summarize_condition(anomaly),
        "fault_type": fault_type,
        "severity": severity,
        "threshold_fault": threshold_fault,
        "display_status": display_status,
        "alerts": safety_alerts,
        "model_name": model_name,
    }


# ---------------------------------------------------------
# DATA HANDLING
# ---------------------------------------------------------

def load_dataset_preview() -> pd.DataFrame:
    if not DATASET_PATH.exists():
        return pd.DataFrame()

    df = pd.read_excel(DATASET_PATH, sheet_name="Data")
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], errors="coerce"
    )

    columns = [
        "timestamp", "voltage_V", "current_A",
        "battery_temp_C", "ambient_temp_C",
    ]

    df = df[columns].copy()
    df["source"] = "dataset"
    df["condition"] = ""
    df["fault_type"] = ""
    df["severity"] = ""
    df["alerts"] = ""

    return df.head(20)


def add_reading(reading: dict, source: str):
    timestamp = pd.Timestamp.now()
    result = get_reading_status(
        reading,
        history=get_recent_live_readings(),
        timestamp=timestamp,
    )

    entry = {
        "timestamp": timestamp,
        "voltage_V": float(reading["voltage"]),
        "current_A": float(reading["current"]),
        "battery_temp_C": float(reading["battery_temp"]),
        "ambient_temp_C": (
            float(reading["ambient_temp"])
            if reading.get("ambient_temp") is not None
            else float("nan")
        ),
        "cell_voltages": reading.get("cell_voltages"),
        "source": source,
        "condition": result["condition"],
        "fault_type": result["fault_type"],
        "severity": result["severity"],
        "alerts": "; ".join(
            alert["message"] for alert in result["alerts"]
        ),
    }

    if "readings" not in st.session_state:
        st.session_state.readings = pd.DataFrame()

    st.session_state.readings = pd.concat(
        [
            st.session_state.readings,
            pd.DataFrame([entry]),
        ],
        ignore_index=True,
    )


def get_active_reading_for_display(mode: str, simulated: dict | None = None):
    if mode == "Simulation mode" and simulated is not None:
        return simulated

    if "latest_serial_reading" in st.session_state:
        return st.session_state.latest_serial_reading

    if "readings" in st.session_state and not st.session_state.readings.empty:
        latest = st.session_state.readings.iloc[-1]

        ambient_temp = latest["ambient_temp_C"]
        return {
            "voltage": float(latest["voltage_V"]),
            "current": float(latest["current_A"]),
            "battery_temp": float(latest["battery_temp_C"]),
            "ambient_temp": (
                float(ambient_temp) if pd.notna(ambient_temp) else None
            ),
        }

    return {
        "voltage": 0.0,
        "current": 0.0,
        "battery_temp": 0.0,
        "ambient_temp": 0.0,
    }


# ---------------------------------------------------------
# VIRTUAL OLED DISPLAY ON LAPTOP
# ---------------------------------------------------------

def render_virtual_oled(reading: dict, status: dict):
    color = "#ff6262" if (
        status["anomaly"] or status["threshold_fault"]
    ) else "#8cff9a"

    status_text = html.escape(status["display_status"])
    ambient_temp = reading.get("ambient_temp")
    ambient_text = (
        f"{ambient_temp:.1f} C" if ambient_temp is not None else "N/A"
    )

    oled_html = f"""
    <div style="
        max-width: 440px;
        margin: 12px auto;
        padding: 18px;
        background: #061008;
        color: #aaffb4;
        border: 5px solid #344438;
        border-radius: 16px;
        font-family: monospace;
        box-shadow: 0 0 18px #00ff8820;
    ">
      <div style="
          text-align:center;
          font-weight:bold;
          padding-bottom:10px;
          border-bottom:1px solid #426747;
      ">EV BATTERY MONITOR</div>
      <div style="line-height:2; padding-top:8px;">
        VOLTAGE&nbsp;&nbsp; {reading['voltage']:.2f} V<br>
        CURRENT&nbsp;&nbsp; {reading['current']:.2f} A<br>
        BAT TEMP&nbsp; {reading['battery_temp']:.1f} C<br>
        AMB TEMP&nbsp; {ambient_text}
      </div>
      <div style="
          margin-top:10px;
          padding:8px;
          text-align:center;
          border:1px solid {color};
          color:{color};
          font-weight:bold;
      ">STATUS: {status_text}</div>
      <div style="font-size:11px;text-align:center;margin-top:8px;">
        VIRTUAL OLED SIMULATION
      </div>
    </div>
    """

    st.markdown(oled_html, unsafe_allow_html=True)


# ---------------------------------------------------------
# STREAMLIT PAGE
# ---------------------------------------------------------

st.set_page_config(
    page_title="EV Battery Monitor",
    page_icon="🔋",
    layout="wide",
)

st.title("EV Battery Monitoring and Anomaly Detection")
st.caption(
    "Unsupervised Isolation Forest trained on normal sensor readings."
)

try:
    ensure_model_files()
except Exception as exc:
    st.error(f"Model setup failed: {exc}")
    st.stop()


with st.sidebar:
    st.header("Controls")
    mode = st.radio(
        "Operating mode",
        ["Simulation mode", "ESP32 Serial mode"],
    )

    simulated_reading = None

    if mode == "Simulation mode":
        scenario = st.selectbox(
            "Simulation scenario",
            list(SCENARIO_VALUES.keys()),
        )

        defaults = SCENARIO_VALUES[scenario]

        voltage = st.slider(
            "Voltage (V)", 2.5, 5.0,
            float(defaults["voltage_V"]), step=0.01,
        )
        current = st.slider(
            "Current (A)", 0.0, 6.0,
            float(defaults["current_A"]), step=0.01,
        )
        battery_temp = st.slider(
            "Battery temperature (°C)", 20.0, 60.0,
            float(defaults["battery_temp_C"]), step=0.1,
        )
        ambient_temp = st.slider(
            "Ambient temperature (°C)", 15.0, 50.0,
            float(defaults["ambient_temp_C"]), step=0.1,
        )
        simulated_reading = {
            "voltage": voltage,
            "current": current,
            "battery_temp": battery_temp,
            "ambient_temp": ambient_temp,
        }

        if st.button("Add preview to readings"):
            add_reading(simulated_reading, "simulated")
            st.success("Simulation reading added.")

    else:
        ports = [port.device for port in list_ports.comports()]
        if not ports:
            ports = ["No COM ports detected"]

        selected_port = st.selectbox("COM port", ports)
        baud_rate = st.selectbox("Baud rate", [115200, 9600, 57600])

        if st.button("Connect"):
            if selected_port == "No COM ports detected":
                st.error("Connect the ESP32 and select its COM port.")
            else:
                try:
                    old_handle = st.session_state.get("serial_handle")
                    if old_handle is not None:
                        old_handle.close()

                    handle = serial.Serial(
                        selected_port, baud_rate, timeout=1
                    )
                    time.sleep(0.5)
                    st.session_state.serial_handle = handle
                    st.success(f"Connected to {selected_port}.")
                except Exception as exc:
                    st.error(f"Serial connection failed: {exc}")

        if st.button("Disconnect"):
            handle = st.session_state.get("serial_handle")
            if handle is not None:
                try:
                    handle.close()
                finally:
                    st.session_state.serial_handle = None
                st.info("Serial connection closed.")

        if st.button("Read one serial message"):
            handle = st.session_state.get("serial_handle")

            if handle is None:
                st.warning("Connect to the ESP32 first.")
            else:
                try:
                    raw = handle.readline()

                    if not raw:
                        st.warning("No serial data received.")
                    else:
                        parsed = parse_serial_line(
                            raw.decode("utf-8", errors="replace")
                        )

                        if parsed is None:
                            st.warning(
                                "Invalid reading. Expected CSV "
                                "voltage,current,battery_temp[,ambient_temp] "
                                "or JSON with voltage, current, "
                                "battery_temp and optional cell_voltages."
                            )
                        else:
                            st.session_state.latest_serial_reading = parsed
                            add_reading(parsed, "serial")
                            st.success("Reading received.")
                except Exception as exc:
                    st.error(f"Serial read failed: {exc}")

        auto_refresh = st.checkbox(
            "Auto-read serial data every 2 seconds"
        )

        if auto_refresh:
            handle = st.session_state.get("serial_handle")

            if handle is None:
                st.warning("Connect to the ESP32 first.")
            else:
                try:
                    raw = handle.readline()
                    if raw:
                        parsed = parse_serial_line(
                            raw.decode("utf-8", errors="replace")
                        )
                        if parsed is not None:
                            st.session_state.latest_serial_reading = parsed
                            add_reading(parsed, "serial")
                        else:
                            st.warning(
                                "ESP32 sent a non-reading line. Waiting for "
                                "a three/four-value CSV line or valid JSON."
                            )
                except Exception as exc:
                    st.error(f"Serial read failed: {exc}")

                time.sleep(2)
                st.rerun()

    st.markdown("---")
    st.caption(
        "Illustrative thresholds only. This prototype is not a "
        "validated battery safety system."
    )


# ---------------------------------------------------------
# READINGS TABLE STATE
# ---------------------------------------------------------

if "readings" not in st.session_state:
    st.session_state.readings = load_dataset_preview()

if st.session_state.readings.empty:
    st.info("No readings yet. Select a simulation scenario or connect ESP32.")

if mode == "Simulation mode":
    active_reading = get_active_reading_for_display(
        mode, simulated_reading
    )
else:
    active_reading = get_active_reading_for_display(mode)

status_history = get_recent_live_readings()
if mode != "Simulation mode" and status_history:
    status_history = status_history[:-1]
status = get_reading_status(active_reading, history=status_history)

# Sensor cards
st.subheader("Current sensor readings")

cols = st.columns(4)
sensor_metrics = [
    ("Voltage", active_reading["voltage"], "V", 2),
    ("Current", active_reading["current"], "A", 2),
    ("Battery temperature", active_reading["battery_temp"], "°C", 1),
    ("Ambient temperature", active_reading["ambient_temp"], "°C", 1),
]

for col, (label, value, unit, precision) in zip(cols, sensor_metrics):
    with col:
        display_value = (
            "N/A"
            if value is None or pd.isna(value)
            else f"{value:.{precision}f} {unit}"
        )
        st.metric(label, display_value)


# Virtual OLED and anomaly result
left, right = st.columns([1, 1])

with left:
    st.subheader("Virtual OLED")
    render_virtual_oled(active_reading, status)

with right:
    st.subheader("Anomaly detection result")
    st.write(
        f"Model: Isolation Forest (unsupervised), "
        f"{status['model_name'].replace('_', ' ').title()}"
    )

    if status["anomaly"] == 1:
        st.warning("The model flagged this reading as unusual.")
    else:
        st.success("The model considers this reading an inlier.")

    if status["threshold_fault"]:
        st.error(
            f"Threshold warning: {status['fault_type']} "
            f"(illustrative severity: {status['severity']})"
        )
    elif status["anomaly"]:
        st.warning(
            "An unusual pattern was detected, but the rule-based "
            "checks did not identify a specific fault type."
        )
    else:
        st.info("No configured threshold warning was triggered.")

st.subheader("Battery safety alerts")
if status["alerts"]:
    for alert in status["alerts"]:
        alert_text = f"{alert['type'].replace('_', ' ')}: {alert['message']}"
        if alert["severity"] in {"Critical", "High"}:
            st.error(f"{alert['severity']} — {alert_text}")
        else:
            st.warning(f"{alert['severity']} — {alert_text}")
else:
    st.success("No thermal-runaway, internal-short, or self-discharge indicator.")

st.caption(
    "Isolation Forest detects unusual patterns; it does not identify "
    "the exact physical cause. Threshold rules are separate from ML."
)


# ---------------------------------------------------------
# RECENT READINGS, CSV, CHARTS AND METRICS
# ---------------------------------------------------------

st.subheader("Recent sensor readings")

if not st.session_state.readings.empty:
    recent_df = st.session_state.readings.tail(20).copy()

    display_columns = [
        "timestamp", "voltage_V", "current_A",
        "battery_temp_C", "ambient_temp_C",
        "source", "condition", "fault_type", "severity", "alerts",
    ]
    recent_df_display = recent_df[
        [col for col in display_columns if col in recent_df.columns]
    ].reset_index(drop=True)

    st.dataframe(recent_df_display, width="stretch")

    st.download_button(
        "Export recent readings as CSV",
        data=recent_df_display.to_csv(index=False),
        file_name="battery_readings.csv",
        mime="text/csv",
    )

    st.subheader("Voltage and temperature trend")

    chart_df = recent_df[
        ["timestamp", "voltage_V", "battery_temp_C"]
    ].copy()

    chart_df["timestamp"] = pd.to_datetime(
        chart_df["timestamp"], errors="coerce"
    )
    chart_df = chart_df.dropna(subset=["timestamp"])
    chart_df = chart_df.set_index("timestamp")

    if not chart_df.empty:
        st.line_chart(chart_df)

st.subheader("Model comparison and evaluation")

metrics = load_metrics()
comparison = pd.DataFrame(metrics["comparison"])

if not comparison.empty:
    columns_to_show = [
        "model", "algorithm", "normal_training_samples",
        "test_samples", "accuracy", "precision", "recall", "f1_score",
    ]
    columns_to_show = [
        column for column in columns_to_show
        if column in comparison.columns
    ]
    st.dataframe(comparison[columns_to_show], width="stretch")

    for item in metrics["comparison"]:
        with st.expander(
            f"{item['model']} — confusion matrix and details"
        ):
            st.write("Features:", item["features"])
            st.write(
                "Normal training samples:",
                item["normal_training_samples"],
            )
            st.write("Contamination:", item["contamination"])
            st.write(
                "Confusion matrix (rows = actual, columns = predicted; "
                "order: normal, anomaly):"
            )
            st.write(item["confusion_matrix"])

st.markdown("---")
st.caption(
    "Demonstration prototype using a dataset and optional ESP32 Serial "
    "readings. Do not use its predictions or example thresholds as the "
    "sole basis for EV battery safety decisions."
)
