"""Tests for trace schema and normalization helpers."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_blueprint.trace import (
    TRACE_SCHEMA_VERSION,
    TraceEvent,
    TraceManifest,
    TraceRunMetadata,
    diff_trace_manifests,
    stable_trace_hash,
    stable_trace_json,
    trace_replay_json,
)


class TestTraceSchema:
    def test_manifest_defaults_schema_version(self):
        manifest = TraceManifest(
            run=TraceRunMetadata(
                run_id="run-1",
                blueprint="customer-support",
                blueprint_version="1.2",
                mode="replay",
            )
        )
        assert manifest.schema_version == TRACE_SCHEMA_VERSION
        assert manifest.trace == []

    def test_event_requires_supported_event_type(self):
        with pytest.raises(ValidationError):
            TraceEvent(sequence=0, event="unknown-event")

    def test_event_accepts_hash_fields(self):
        event = TraceEvent(
            sequence=1,
            event="tool_called",
            node="billing",
            tool="lookup_invoice",
            input_state_hash="abc",
            output_state_hash="def",
            args_hash="ghi",
        )
        assert event.tool == "lookup_invoice"
        assert event.args_hash == "ghi"


class TestTraceNormalization:
    def test_state_hash_is_stable_across_dict_key_order(self):
        left = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
        right = {"nested": {"x": 1, "y": 2}, "a": 1, "b": 2}
        assert stable_trace_hash(left) == stable_trace_hash(right)

    def test_hash_normalizes_generated_ids_and_timestamps(self):
        left = {
            "request_id": "550e8400-e29b-41d4-a716-446655440000",
            "created_at": "2026-04-29T10:11:12Z",
            "started": datetime(2026, 4, 29, 10, 11, 12),
        }
        right = {
            "request_id": "123e4567-e89b-12d3-a456-426614174000",
            "created_at": "2027-05-30T01:02:03Z",
            "started": datetime(2027, 5, 30, 1, 2, 3),
        }
        assert stable_trace_hash(left) == stable_trace_hash(right)

    def test_json_normalizes_line_endings_and_trailing_whitespace(self):
        payload = {"text": "hello  \r\nworld\t \r\n"}
        assert stable_trace_json(payload) == '{"text":"hello\\nworld\\n"}'

    def test_hash_preserves_list_order(self):
        first = {"tools": ["lookup_invoice", "send_email"]}
        second = {"tools": ["send_email", "lookup_invoice"]}
        assert stable_trace_hash(first) != stable_trace_hash(second)

    def test_trace_replay_json_ignores_run_timestamps_and_modes(self):
        left = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run": {
                "blueprint": "agent",
                "blueprint_version": "1.0",
                "scenario_id": "case-1",
                "mode": "live",
                "started_at": "2026-04-29T10:11:12Z",
            },
            "trace": [],
            "replay": {},
        }
        right = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run": {
                "blueprint": "agent",
                "blueprint_version": "1.0",
                "scenario_id": "case-1",
                "mode": "replay",
                "started_at": "2027-05-30T01:02:03Z",
            },
            "trace": [],
            "replay": {},
        }
        assert trace_replay_json(left) == trace_replay_json(right)

    def test_diff_trace_manifests_reports_drift(self):
        left = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run": {"blueprint": "agent", "blueprint_version": "1.0", "scenario_id": "case-1"},
            "trace": [{"sequence": 0, "event": "tool_called", "tool": "lookup_invoice"}],
            "replay": {},
        }
        right = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run": {"blueprint": "agent", "blueprint_version": "1.0", "scenario_id": "case-1"},
            "trace": [{"sequence": 0, "event": "tool_called", "tool": "issue_refund"}],
            "replay": {},
        }
        diff = diff_trace_manifests(left, right)
        assert "--- golden" in diff
        assert "lookup_invoice" in diff
        assert "issue_refund" in diff
