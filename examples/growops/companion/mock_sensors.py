"""Tiny mock sensor gateway for the GrowOps demo.

Stands in for the real ESP32 sensor fleet so the deployed agent has a live
endpoint to read from. Returns the same healthy telemetry for every bed, which
drives the nominal-cycle happy path (no actuation needed). Swap the readings to
exercise the attention/critical remediation path.

Run locally:  python mock_sensors.py    # serves on :8080
The Dockerfile next to this file builds the image deployed in M16.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

# Healthy readings — all within the synthesizer's nominal envelope.
TELEMETRY = {
    "soil_moisture": 55.0,
    "air_temp_c": 22.0,
    "humidity": 60.0,
    "co2_ppm": 800,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(TELEMETRY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # quiet
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
