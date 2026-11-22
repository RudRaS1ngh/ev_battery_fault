"""Receive ESP32 readings over local Wi-Fi and append them to a CSV dataset."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "outputs" / "esp32_readings.csv"
FEATURES = [
    "voltage_V",
    "current_A",
    "battery_temp_C",
    "ambient_temp_C",
    "humidity_percent",
]
CSV_COLUMNS = ["timestamp", *FEATURES]
LEGACY_COLUMNS = CSV_COLUMNS[:-1]
MAX_REQUEST_BYTES = 2048
_write_lock = threading.Lock()


def upgrade_legacy_csv(output_path: Path) -> None:
    temporary_path = None
    try:
        with output_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames == CSV_COLUMNS:
                return
            if reader.fieldnames != LEGACY_COLUMNS:
                raise ValueError(
                    "Unsupported collected CSV columns: "
                    f"{reader.fieldnames}"
                )
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f"{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as target:
                temporary_path = Path(target.name)
                writer = csv.DictWriter(target, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                for row in reader:
                    writer.writerow(
                        {
                            **row,
                            "humidity_percent": "",
                        }
                    )
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_reading_payload(payload: bytes) -> dict[str, float]:
    try:
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON.") from exc

    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")

    reading = {}
    for feature in FEATURES:
        if feature not in body:
            raise ValueError(f"Missing required sensor field: {feature}")
        try:
            value = float(body[feature])
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value for {feature}.") from exc
        if not math.isfinite(value):
            raise ValueError(f"Non-finite sensor value for {feature}.")
        reading[feature] = value
    return reading


def append_reading(
    output_path: Path,
    reading: dict[str, float],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        needs_header = (
            not output_path.exists() or output_path.stat().st_size == 0
        )
        if not needs_header:
            upgrade_legacy_csv(output_path)
        with output_path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
            if needs_header:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **reading,
                }
            )


def make_handler(output_path: Path):
    class ReadingHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict[str, object]) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path != "/health":
                self._send_json(404, {"error": "Not found."})
                return
            self._send_json(200, {"status": "ready"})

        def do_POST(self) -> None:
            if self.path != "/readings":
                self._send_json(404, {"error": "Not found."})
                return
            if not self.headers.get("Content-Type", "").startswith(
                "application/json"
            ):
                self._send_json(
                    415, {"error": "Content-Type must be application/json."}
                )
                return

            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send_json(400, {"error": "Invalid Content-Length."})
                return
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._send_json(413, {"error": "Invalid request size."})
                return

            try:
                reading = parse_reading_payload(self.rfile.read(length))
                append_reading(output_path, reading)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except OSError as exc:
                self._send_json(500, {"error": f"Unable to save reading: {exc}"})
                return

            self._send_json(201, {"status": "saved"})

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

    return ReadingHandler


def create_server(
    host: str = "0.0.0.0",
    port: int = 8765,
    output_path: Path = DEFAULT_OUTPUT,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(output_path),
    )
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    server = create_server(args.host, args.port, args.output)
    print(f"ESP32 collector listening on {args.host}:{args.port}/readings")
    print(f"Appending readings to {args.output.resolve()}")
    print("Keep this service on a trusted local network; do not port-forward it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ESP32 collector.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
