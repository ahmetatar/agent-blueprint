"""Tests for the `observability:` blueprint section."""

import pytest
from pydantic import ValidationError

from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.observability import (
    ObservabilityConfig,
    OtlpProtocol,
    TraceExporter,
    TracingConfig,
)

_MINIMAL = {
    "blueprint": {"name": "demo"},
    "graph": {
        "entry_point": "n",
        "nodes": {"n": {"type": "function"}},
        "edges": [],
    },
}


class TestTracingConfigDefaults:
    def test_defaults(self):
        cfg = TracingConfig()
        assert cfg.enabled is False
        assert cfg.exporter == TraceExporter.otlp
        assert cfg.endpoint is None
        assert cfg.protocol == OtlpProtocol.http_protobuf
        assert cfg.service_name is None
        assert cfg.sample_ratio == 1.0

    def test_observability_tracing_optional(self):
        assert ObservabilityConfig().tracing is None


class TestTracingConfigValidation:
    def test_full_config(self):
        cfg = TracingConfig(
            enabled=True,
            exporter="console",
            endpoint="http://collector:4318",
            protocol="grpc",
            service_name="my-agent",
            sample_ratio=0.25,
        )
        assert cfg.exporter == TraceExporter.console
        assert cfg.protocol == OtlpProtocol.grpc
        assert cfg.sample_ratio == 0.25

    def test_invalid_exporter_rejected(self):
        with pytest.raises(ValidationError):
            TracingConfig(exporter="jaeger")

    def test_invalid_protocol_rejected(self):
        with pytest.raises(ValidationError):
            TracingConfig(protocol="http/json")

    @pytest.mark.parametrize("ratio", [-0.1, 1.1])
    def test_sample_ratio_bounds(self, ratio):
        with pytest.raises(ValidationError):
            TracingConfig(sample_ratio=ratio)

    def test_empty_endpoint_rejected(self):
        with pytest.raises(ValidationError):
            TracingConfig(endpoint="  ")

    def test_empty_service_name_rejected(self):
        with pytest.raises(ValidationError):
            TracingConfig(service_name="")


class TestBlueprintObservabilitySection:
    def test_spec_without_observability(self):
        spec = BlueprintSpec.model_validate(_MINIMAL)
        assert spec.observability is None

    def test_spec_with_tracing(self):
        raw = dict(_MINIMAL)
        raw["observability"] = {
            "tracing": {
                "enabled": True,
                "endpoint": "http://localhost:4318",
                "sample_ratio": 0.5,
            }
        }
        spec = BlueprintSpec.model_validate(raw)
        assert spec.observability is not None
        assert spec.observability.tracing is not None
        assert spec.observability.tracing.enabled is True
        assert spec.observability.tracing.sample_ratio == 0.5
