"""Versioned trace schema and normalization helpers for ABP runs."""

from __future__ import annotations

import hashlib
import json
import re
from difflib import unified_diff
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

TRACE_SCHEMA_VERSION = "1.0"

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}\b"
)
_ISO_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)


class TraceRunMode(str, Enum):
    mock = "mock"
    stubbed = "stubbed"
    live_tools = "live-tools"
    live = "live"
    replay = "replay"


class TraceEventType(str, Enum):
    node_started = "node_started"
    node_finished = "node_finished"
    tool_called = "tool_called"
    tool_failed = "tool_failed"
    approval_requested = "approval_requested"
    approval_granted = "approval_granted"
    contract_failed = "contract_failed"
    artifact_written = "artifact_written"
    run_finished = "run_finished"


class TraceRunMetadata(BaseModel):
    run_id: str
    blueprint: str
    blueprint_version: str
    scenario_id: str | None = None
    mode: TraceRunMode
    seed: int | None = None
    started_at: datetime | None = None


class TraceEvent(BaseModel):
    sequence: int = Field(ge=0)
    event: TraceEventType
    node: str | None = None
    tool: str | None = None
    route: str | None = None
    input_state_hash: str | None = None
    output_state_hash: str | None = None
    args_hash: str | None = None
    error: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceManifest(BaseModel):
    schema_version: str = TRACE_SCHEMA_VERSION
    run: TraceRunMetadata
    trace: list[TraceEvent] = Field(default_factory=list)


def normalize_for_trace(value: Any) -> Any:
    """Normalize unstable values into a deterministic trace-safe form."""
    if isinstance(value, dict):
        return {
            str(key): normalize_for_trace(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [normalize_for_trace(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_for_trace(item) for item in value]
    if isinstance(value, datetime):
        return "<timestamp>"
    if isinstance(value, str):
        text = value.replace("\r\n", "\n")
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        text = _UUID_RE.sub("<generated-id>", text)
        text = _ISO_TS_RE.sub("<timestamp>", text)
        return text
    if hasattr(value, "__dict__"):
        public = {
            key: val
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
        if public:
            return {
                "__class__": value.__class__.__name__,
                "fields": normalize_for_trace(public),
            }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)


def stable_trace_json(value: Any) -> str:
    """Render normalized trace data as stable JSON."""
    normalized = normalize_for_trace(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_trace_hash(value: Any) -> str:
    """Hash normalized trace data using SHA-256."""
    payload = stable_trace_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def trace_replay_view(manifest: dict[str, Any]) -> dict[str, Any]:
    """Project a manifest into a normalized, diff-friendly replay view."""
    run = manifest.get("run", {}) if isinstance(manifest, dict) else {}
    replay = manifest.get("replay", {}) if isinstance(manifest, dict) else {}
    return normalize_for_trace({
        "schema_version": manifest.get("schema_version") if isinstance(manifest, dict) else None,
        "run": {
            "blueprint": run.get("blueprint"),
            "blueprint_version": run.get("blueprint_version"),
            "scenario_id": run.get("scenario_id"),
        },
        "trace": manifest.get("trace", []) if isinstance(manifest, dict) else [],
        "replay": replay,
    })


def trace_replay_json(manifest: dict[str, Any]) -> str:
    """Render a replay view as stable, pretty JSON."""
    return json.dumps(trace_replay_view(manifest), indent=2, sort_keys=True, ensure_ascii=True)


def diff_trace_manifests(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    """Return a unified diff between two normalized replay views."""
    expected_lines = trace_replay_json(expected).splitlines()
    actual_lines = trace_replay_json(actual).splitlines()
    return "\n".join(unified_diff(expected_lines, actual_lines, fromfile="golden", tofile="actual", lineterm=""))
