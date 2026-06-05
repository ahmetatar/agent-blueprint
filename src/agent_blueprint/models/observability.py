"""Observability configuration models — declarative telemetry export (`observability:`)."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class TraceExporter(str, Enum):
    otlp = "otlp"
    console = "console"  # stdout spans, for local debugging


class OtlpProtocol(str, Enum):
    http_protobuf = "http/protobuf"
    grpc = "grpc"


class TracingConfig(BaseModel):
    """OpenTelemetry trace export for generated agents.

    Standard OTEL_* environment variables always win over blueprint values
    at runtime; `ABP_OTEL=off` disables export entirely.
    """

    enabled: bool = False
    exporter: TraceExporter = TraceExporter.otlp
    endpoint: str | None = None          # e.g. http://localhost:4318; OTEL_EXPORTER_OTLP_ENDPOINT wins
    protocol: OtlpProtocol = OtlpProtocol.http_protobuf
    service_name: str | None = None      # defaults to the blueprint name
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("endpoint", "service_name")
    @classmethod
    def validate_non_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("must be a non-empty string when provided")
        return v


class ObservabilityConfig(BaseModel):
    """Top-level `observability:` blueprint section."""

    tracing: TracingConfig | None = None
