"""Tests for OTel export — conditional generation and the generated bridge."""

import ast
import importlib
import sys
from pathlib import Path

import pytest

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_ir(observability: dict | None = None, name: str = "basic_chatbot.yml"):
    raw = load_blueprint_yaml(FIXTURES / name)
    if observability is not None:
        raw["observability"] = observability
    spec = BlueprintSpec.model_validate(raw)
    return compile_blueprint(spec)


def generate(observability: dict | None = None) -> dict[str, str]:
    return LangGraphGenerator().generate(make_ir(observability))


_ENABLED = {"tracing": {"enabled": True}}


class TestCompilerPassthrough:
    def test_observability_reaches_ir(self):
        ir = make_ir(_ENABLED)
        assert ir.observability is not None
        assert ir.observability.tracing is not None
        assert ir.observability.tracing.enabled is True

    def test_absent_by_default(self):
        assert make_ir().observability is None


class TestConditionalGeneration:
    def test_otel_module_generated_when_enabled(self):
        files = generate(_ENABLED)
        assert "_abp_otel.py" in files
        ast.parse(files["_abp_otel.py"])

    def test_otel_module_absent_by_default(self):
        files = generate()
        assert "_abp_otel.py" not in files
        assert "opentelemetry" not in files["requirements.txt"]
        assert "_abp_init_tracing" not in files["main.py"]

    def test_main_initializes_tracing(self):
        files = generate(_ENABLED)
        assert "_abp_init_tracing()" in files["main.py"]
        ast.parse(files["main.py"])

    def test_requirements_http_exporter_default(self):
        reqs = generate(_ENABLED)["requirements.txt"]
        assert "opentelemetry-sdk" in reqs
        assert "opentelemetry-exporter-otlp-proto-http" in reqs
        assert "opentelemetry-exporter-otlp-proto-grpc" not in reqs

    def test_requirements_grpc_exporter(self):
        reqs = generate(
            {"tracing": {"enabled": True, "protocol": "grpc"}}
        )["requirements.txt"]
        assert "opentelemetry-exporter-otlp-proto-grpc" in reqs
        assert "opentelemetry-exporter-otlp-proto-http" not in reqs

    def test_requirements_console_exporter_needs_no_otlp(self):
        reqs = generate(
            {"tracing": {"enabled": True, "exporter": "console"}}
        )["requirements.txt"]
        assert "opentelemetry-sdk" in reqs
        assert "opentelemetry-exporter-otlp" not in reqs

    def test_service_name_defaults_to_blueprint(self):
        otel = generate(_ENABLED)["_abp_otel.py"]
        assert "'basic-chatbot'" in otel

    def test_service_name_override(self):
        otel = generate(
            {"tracing": {"enabled": True, "service_name": "my-svc"}}
        )["_abp_otel.py"]
        assert "'my-svc'" in otel


@pytest.fixture
def generated_modules(tmp_path, monkeypatch):
    """Write the rendered _abp_trace/_abp_otel to disk and import them."""
    files = generate({"tracing": {"enabled": True, "exporter": "console"}})
    for name in ("_abp_trace.py", "_abp_otel.py"):
        (tmp_path / name).write_text(files[name], encoding="utf-8")
    monkeypatch.delenv("ABP_TRACE_FILE", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in ("_abp_trace", "_abp_otel"):
        sys.modules.pop(mod, None)
    trace_mod = importlib.import_module("_abp_trace")
    otel_mod = importlib.import_module("_abp_otel")
    yield trace_mod, otel_mod
    for mod in ("_abp_trace", "_abp_otel"):
        sys.modules.pop(mod, None)


def _drive_run(trace_mod, *, status: str = "success") -> None:
    """Simulate a minimal agent run through the trace API."""
    trace_mod.start_trace(
        run_id="r1", blueprint="demo", blueprint_version="1.0", mode="live"
    )
    trace_mod.emit_trace_event("node_started", node="assistant", input_state={"x": 1})
    trace_mod.emit_trace_event("tool_called", tool="search", args={"q": "hi"})

    class _Response:
        usage_metadata = {"input_tokens": 10, "output_tokens": 5}

    trace_mod.register_llm_budget_usage("assistant", _Response())
    trace_mod.emit_trace_event(
        "node_finished",
        node="assistant",
        output_state={"x": 2},
        metadata={"escalated": False},
    )
    trace_mod.finalize_trace(status=status, output_state={"x": 2})


class TestObserverRegistry:
    def test_observer_receives_lifecycle_and_events(self, generated_modules):
        trace_mod, _ = generated_modules
        seen: dict = {"events": []}

        class Observer:
            def on_run_started(self, run_info):
                seen["run"] = run_info

            def on_event(self, payload):
                seen["events"].append(payload["event"])

            def on_llm_usage(self, node_id, usage, cost_usd):
                seen["usage"] = (node_id, usage)

            def on_run_finished(self, status, error):
                seen["finished"] = (status, error)

        observer = Observer()
        trace_mod.register_trace_observer(observer)
        try:
            _drive_run(trace_mod)
        finally:
            trace_mod.unregister_trace_observer(observer)

        assert seen["run"]["run_id"] == "r1"
        assert "node_started" in seen["events"]
        assert "tool_called" in seen["events"]
        assert seen["usage"][0] == "assistant"
        assert seen["usage"][1]["total_tokens"] == 15
        assert seen["finished"] == ("success", None)

    def test_failing_observer_does_not_break_run(self, generated_modules):
        trace_mod, _ = generated_modules

        class Broken:
            def on_event(self, payload):
                raise RuntimeError("observer bug")

        broken = Broken()
        trace_mod.register_trace_observer(broken)
        try:
            _drive_run(trace_mod)  # must not raise
        finally:
            trace_mod.unregister_trace_observer(broken)
        manifest = trace_mod.get_last_trace_manifest()
        events = [e["event"] for e in manifest["trace"]]
        assert "node_started" in events
        assert events[-1] == "run_finished"

    def test_manifest_unchanged_by_observer(self, generated_modules):
        trace_mod, _ = generated_modules

        _drive_run(trace_mod)
        baseline = trace_mod.get_last_trace_manifest()

        class Observer:
            def on_event(self, payload):
                pass

        observer = Observer()
        trace_mod.register_trace_observer(observer)
        try:
            _drive_run(trace_mod)
        finally:
            trace_mod.unregister_trace_observer(observer)
        observed = trace_mod.get_last_trace_manifest()

        strip = trace_mod.normalize_for_trace  # normalizes timestamps
        assert strip(baseline["trace"]) == strip(observed["trace"])


class TestOTelBridge:
    def _make_observer(self, otel_mod):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")
        return otel_mod.OTelTraceObserver(tracer, provider), exporter

    def test_span_tree_and_attributes(self, generated_modules):
        from opentelemetry.trace import StatusCode

        trace_mod, otel_mod = generated_modules
        observer, exporter = self._make_observer(otel_mod)
        trace_mod.register_trace_observer(observer)
        try:
            _drive_run(trace_mod)
        finally:
            trace_mod.unregister_trace_observer(observer)

        spans = {s.name: s for s in exporter.get_finished_spans()}
        assert set(spans) == {"abp.run", "abp.node assistant"}

        root = spans["abp.run"]
        assert root.attributes["abp.run_id"] == "r1"
        assert root.attributes["abp.blueprint"] == "demo"
        assert root.status.status_code == StatusCode.OK

        node = spans["abp.node assistant"]
        assert node.parent.span_id == root.context.span_id
        assert node.attributes["abp.node.id"] == "assistant"
        assert node.attributes["gen_ai.system"] == "openai"
        assert node.attributes["gen_ai.request.model"] == "gpt-4o"
        assert node.attributes["gen_ai.usage.input_tokens"] == 10
        assert node.attributes["gen_ai.usage.output_tokens"] == 5
        assert node.attributes["abp.escalated"] is False
        assert "tool_called" in [e.name for e in node.events]

    def test_failed_run_marks_root_error(self, generated_modules):
        from opentelemetry.trace import StatusCode

        trace_mod, otel_mod = generated_modules
        observer, exporter = self._make_observer(otel_mod)
        trace_mod.register_trace_observer(observer)
        try:
            _drive_run(trace_mod, status="failed")
        finally:
            trace_mod.unregister_trace_observer(observer)

        root = {s.name: s for s in exporter.get_finished_spans()}["abp.run"]
        assert root.status.status_code == StatusCode.ERROR

    def test_error_event_marks_node_span(self, generated_modules):
        from opentelemetry.trace import StatusCode

        trace_mod, otel_mod = generated_modules
        observer, exporter = self._make_observer(otel_mod)
        trace_mod.register_trace_observer(observer)
        try:
            trace_mod.start_trace(
                run_id="r2", blueprint="demo", blueprint_version="1.0", mode="live"
            )
            trace_mod.emit_trace_event("node_started", node="assistant")
            trace_mod.emit_trace_event(
                "tool_failed", tool="search", error="boom"
            )
            trace_mod.emit_trace_event("node_finished", node="assistant")
            trace_mod.finalize_trace(status="failed", error="boom")
        finally:
            trace_mod.unregister_trace_observer(observer)

        node = {s.name: s for s in exporter.get_finished_spans()}[
            "abp.node assistant"
        ]
        assert node.status.status_code == StatusCode.ERROR
        failed = [e for e in node.events if e.name == "tool_failed"]
        assert failed and failed[0].attributes["abp.error"] == "boom"


class TestInitTracing:
    def test_kill_switch(self, generated_modules, monkeypatch):
        _, otel_mod = generated_modules
        monkeypatch.setenv("ABP_OTEL", "off")
        assert otel_mod.init_tracing() is False

    def test_missing_packages_degrade_gracefully(
        self, generated_modules, monkeypatch, capsys
    ):
        _, otel_mod = generated_modules
        monkeypatch.delenv("ABP_OTEL", raising=False)
        # Simulate opentelemetry not being installed
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        assert otel_mod.init_tracing() is False
        assert "OpenTelemetry" in capsys.readouterr().err

    def test_console_exporter_initializes(self, generated_modules, monkeypatch):
        _, otel_mod = generated_modules
        monkeypatch.delenv("ABP_OTEL", raising=False)
        assert otel_mod.init_tracing() is True
        # idempotent
        assert otel_mod.init_tracing() is True
