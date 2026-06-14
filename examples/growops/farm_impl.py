"""Function-tool + retriever implementations for the GrowOps demo."""
from __future__ import annotations

import json
import math


def parse_telemetry(payload: str) -> dict:
    data = json.loads(payload) if isinstance(payload, str) else dict(payload)
    return {
        "soil_moisture": float(data.get("soil_moisture", 0.0)),
        "air_temp_c": float(data.get("air_temp_c", 0.0)),
        "humidity": float(data.get("humidity", 0.0)),
        "co2_ppm": float(data.get("co2_ppm", 0.0)),
    }


def compute_vpd(air_temp_c: float, humidity: float) -> float:
    sat = 0.6108 * math.exp((17.27 * air_temp_c) / (air_temp_c + 237.3))
    return round(sat * (1.0 - humidity / 100.0), 3)


def agronomy_retriever(query: str, top_k: int = 5, config: dict | None = None) -> list[dict]:
    return [{"text": f"(agronomy note relevant to: {query})", "source": "corpus/stub.md"}]
