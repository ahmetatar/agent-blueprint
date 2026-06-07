"""Background editor actions — test / run / gate / generate / doctor (phase E3a).

The editor reuses the same logic modules as the CLI (`harness_runner`,
`eval_runner`, `gating`, `doctoring`, generators, `runners.local`) but shapes
results as JSON-friendly dicts instead of Rich output. One task runs at a
time per server; starting a second one raises `TaskBusyError` (HTTP 409).

Cancellation: the worker loop checks a cancel flag between scenarios/suites,
and the currently running generated-project subprocess (exposed through
`LocalRunner`'s ``process_hook``) is terminated so a live-LLM run cannot pin
the editor.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_blueprint.cli.generate import TargetFramework
from agent_blueprint.doctoring import DoctorSeverity, doctor_blueprint
from agent_blueprint.eval_runner import EvalRunResult, EvalSuiteResult, run_eval_suite
from agent_blueprint.exceptions import (
    BlueprintCompilationError,
    BlueprintError,
    BlueprintValidationError,
    GeneratorError,
)
from agent_blueprint.gating import (
    GATE_SCHEMA_VERSION,
    build_gate_snapshot,
    compare_gate_snapshots,
    current_run_all_green,
)
from agent_blueprint.harness_runner import ScenarioResult, run_harness_scenario
from agent_blueprint.ir.compiler import AgentGraph, compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.run import SandboxConfig
from agent_blueprint.runners.local import LocalRunner
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

ACTIONS = ("test", "run", "gate", "generate", "doctor")


class TaskBusyError(Exception):
    """A task is already running — one at a time per editor session."""


class ActionError(Exception):
    """The action could not execute (as opposed to executing and failing)."""


@dataclass
class TaskRecord:
    id: str
    action: str
    params: dict[str, Any]
    status: str = "running"  # running | passed | failed | error | cancelled
    progress: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "params": self.params,
            "status": self.status,
            "progress": list(self.progress),
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    """Single-slot background runner; pushes progress over the WS broadcaster."""

    def __init__(
        self,
        blueprint_path: Path,
        publish: Callable[[dict[str, Any]], None],
    ) -> None:
        self._blueprint_path = blueprint_path
        self._publish = publish  # must be thread-safe (worker threads call it)
        self._lock = threading.Lock()
        self._current: TaskRecord | None = None
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._process: subprocess.Popen[str] | None = None

    @property
    def current(self) -> TaskRecord | None:
        return self._current

    def start(self, action: str, params: dict[str, Any]) -> TaskRecord:
        if action not in ACTIONS:
            raise ActionError(f"unknown action '{action}' (expected one of: {', '.join(ACTIONS)})")
        with self._lock:
            if self._current is not None and self._current.status == "running":
                raise TaskBusyError(
                    f"task {self._current.id} ({self._current.action}) is still running"
                )
            record = TaskRecord(id=uuid.uuid4().hex[:12], action=action, params=params)
            self._current = record
            self._cancel.clear()
            self._process = None
            self._thread = threading.Thread(
                target=self._run, args=(record,), name=f"abp-editor-{action}", daemon=True
            )
        # Publish before the thread starts so a fast task cannot emit its
        # task_done ahead of task_started.
        self._publish({"type": "task_started", "task": record.to_dict()})
        self._thread.start()
        return record

    def cancel(self) -> bool:
        """Request cancellation of the running task; True if one was running."""
        with self._lock:
            record = self._current
            if record is None or record.status != "running":
                return False
            self._cancel.set()
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        return True

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker thread (test helper)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- worker side ---------------------------------------------------

    def _run(self, record: TaskRecord) -> None:
        try:
            status, result = _HANDLERS[record.action](_TaskContext(self, record))
            record.result = result
            record.status = "cancelled" if self._cancel.is_set() else status
        except ActionError as e:
            record.status = "cancelled" if self._cancel.is_set() else "error"
            record.error = str(e)
        except Exception as e:  # noqa: BLE001 — surface, don't kill the server
            record.status = "cancelled" if self._cancel.is_set() else "error"
            record.error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self._process = None
            self._publish({"type": "task_done", "task": record.to_dict()})

    def _register_process(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._process = process
        # Cancel may have raced the spawn — don't leave an orphan running.
        if self._cancel.is_set() and process.poll() is None:
            process.terminate()

    def _emit_progress(self, record: TaskRecord, event: dict[str, Any]) -> None:
        record.progress.append(event)
        self._publish({"type": "task_progress", "task_id": record.id, "event": event})


class _TraceStreamTailer:
    """Tails an `ABP_TRACE_STREAM_FILE` and forwards each JSON-line event.

    The generated project's stream observer appends one JSON object per line
    (flushed per event), so polling the file and draining complete lines is
    enough — no inotify required, and it works for sandboxed runs too (the
    stream file lives on the host).
    """

    def __init__(
        self,
        path: Path,
        emit: Callable[[dict[str, Any]], None],
        poll_interval: float = 0.05,
    ) -> None:
        self._path = path
        self._emit = emit
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._offset = 0
        self._thread = threading.Thread(
            target=self._run, name="abp-editor-trace-tail", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        """Stop tailing; drains once more first (callers close after the
        subprocess has exited, so everything is already on disk)."""
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            final = self._stop.is_set()
            self._drain()
            if final:
                break
            self._stop.wait(self._poll_interval)

    def _drain(self) -> None:
        try:
            with open(self._path, "rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except OSError:
            return  # not created yet (subprocess still booting)
        if not chunk:
            return
        lines = chunk.split(b"\n")
        complete, remainder = lines[:-1], lines[-1]
        self._offset += len(chunk) - len(remainder)
        for line in complete:
            if not line.strip():
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # torn or garbled line — never break the task
            if isinstance(event, dict):
                self._emit(event)


@dataclass
class _TaskContext:
    manager: TaskManager
    record: TaskRecord

    @property
    def blueprint_path(self) -> Path:
        return self.manager._blueprint_path

    @property
    def params(self) -> dict[str, Any]:
        return self.record.params

    @property
    def cancelled(self) -> bool:
        return self.manager._cancel.is_set()

    def progress(self, kind: str, **data: Any) -> None:
        self.manager._emit_progress(self.record, {"kind": kind, **data})

    def register_process(self, process: subprocess.Popen[str]) -> None:
        self.manager._register_process(process)

    @contextlib.contextmanager
    def trace_stream(self, scope: str | None = None) -> Iterator[dict[str, str]]:
        """Env for a generated-project run whose trace events should reach
        the UI live: yields `{ABP_TRACE_STREAM_FILE: ...}` and tails that
        file for the duration, publishing each event as `task_trace`.

        Trace events are deliberately NOT accumulated on the task record —
        they are ephemeral UI state (node highlights), and a long run could
        produce thousands of them.
        """
        with tempfile.TemporaryDirectory(prefix="abp-editor-stream-") as tmp:
            path = Path(tmp) / "trace-stream.jsonl"
            tailer = _TraceStreamTailer(
                path,
                lambda event: self.manager._publish(
                    {
                        "type": "task_trace",
                        "task_id": self.record.id,
                        "scope": scope,
                        "event": event,
                    }
                ),
            )
            tailer.start()
            try:
                yield {"ABP_TRACE_STREAM_FILE": str(path)}
            finally:
                tailer.close()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_compiled(path: Path) -> tuple[BlueprintSpec, AgentGraph]:
    try:
        spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
    except (BlueprintValidationError, ValidationError) as e:
        raise ActionError(f"blueprint does not validate: {e}") from e
    try:
        return spec, compile_blueprint(spec)
    except BlueprintCompilationError as e:
        raise ActionError(f"blueprint does not compile: {e}") from e


def _parse_target(params: dict[str, Any]) -> TargetFramework:
    raw = params.get("target", TargetFramework.langgraph.value)
    try:
        return TargetFramework(raw)
    except ValueError as e:
        raise ActionError(f"unknown target '{raw}'") from e


def _trace_store(path: Path) -> Path:
    return path.parent / ".abp" / "traces"


def _scenario_summary(result: ScenarioResult) -> dict[str, Any]:
    return {
        "scenario": result.scenario_id,
        "passed": result.passed,
        "checks": list(result.checks),
        "failures": list(result.failures),
        "warnings": list(result.warnings),
    }


def action_surface(spec: BlueprintSpec, blueprint_path: Path) -> dict[str, Any]:
    """What the Actions pane can offer for this blueprint (scenario ids etc.)."""
    surface: dict[str, Any] = {
        "scenarios": [],
        "eval_suites": [],
        "has_gate_baseline": (
            blueprint_path.parent / ".abp" / "gate-baseline.json"
        ).exists(),
        "sandbox": bool(spec.run and spec.run.sandbox and spec.run.sandbox.enabled),
    }
    try:
        ir = compile_blueprint(spec)
    except BlueprintError:
        return surface  # compile errors already surface as a lint finding
    if ir.harness:
        surface["scenarios"] = [scenario.id for scenario in ir.harness.scenarios]
    if ir.evals:
        surface["eval_suites"] = [suite.id for suite in ir.evals.suites]
    return surface


# ---------------------------------------------------------------------------
# Action handlers — return (status, result)
# ---------------------------------------------------------------------------


def _action_doctor(ctx: _TaskContext) -> tuple[str, dict[str, Any]]:
    spec, ir = _load_compiled(ctx.blueprint_path)
    target = _parse_target(ctx.params)
    findings = doctor_blueprint(spec, ir, target=target)
    errors = sum(1 for finding in findings if finding.severity == DoctorSeverity.error)
    return (
        "failed" if errors else "passed",
        {
            "target": target.value,
            "errors": errors,
            "warnings": len(findings) - errors,
            "findings": [
                {
                    "severity": finding.severity.value,
                    "code": finding.code,
                    "location": finding.location,
                    "message": finding.message,
                }
                for finding in findings
            ],
        },
    )


def _action_generate(ctx: _TaskContext) -> tuple[str, dict[str, Any]]:
    spec, ir = _load_compiled(ctx.blueprint_path)
    target = _parse_target(ctx.params)
    from agent_blueprint.generators.base import BaseGenerator

    generator: BaseGenerator
    if target == TargetFramework.langgraph:
        from agent_blueprint.generators.langgraph import LangGraphGenerator

        generator = LangGraphGenerator()
    elif target == TargetFramework.plain:
        from agent_blueprint.generators.plain import PlainPythonGenerator

        generator = PlainPythonGenerator()
    else:
        raise ActionError("the crewai target is not implemented")
    try:
        files = generator.generate(ir)
    except GeneratorError as e:
        raise ActionError(str(e)) from e

    raw_out = ctx.params.get("output_dir")
    safe_name = spec.blueprint.name.replace(" ", "-").lower()
    output_dir = Path(raw_out) if raw_out else Path(f"{safe_name}-{target.value}")
    if not output_dir.is_absolute():
        # CLI resolves against CWD; the editor anchors to the blueprint.
        output_dir = ctx.blueprint_path.parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        file_path = output_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    return (
        "passed",
        {
            "target": target.value,
            "output_dir": str(output_dir),
            "files": sorted(files),
            "warnings": list(ir.warnings),
        },
    )


def _action_test(ctx: _TaskContext) -> tuple[str, dict[str, Any]]:
    _, ir = _load_compiled(ctx.blueprint_path)
    if ir.harness is None or not ir.harness.scenarios:
        raise ActionError("no harness scenarios are defined for this blueprint")
    scenarios = list(ir.harness.scenarios)
    selected = ctx.params.get("scenarios")
    if selected:
        wanted = set(selected)
        unknown = wanted - {scenario.id for scenario in scenarios}
        if unknown:
            raise ActionError(f"unknown scenario id(s): {', '.join(sorted(unknown))}")
        scenarios = [scenario for scenario in scenarios if scenario.id in wanted]
    install = bool(ctx.params.get("install", False))

    summaries: list[dict[str, Any]] = []
    for scenario in scenarios:
        if ctx.cancelled:
            break
        ctx.progress("scenario_started", scenario=scenario.id)
        with ctx.trace_stream(scope=scenario.id) as stream_env:
            result = run_harness_scenario(
                ir,
                scenario,
                install=install,
                trace_store=_trace_store(ctx.blueprint_path),
                save_traces="failed",
                process_hook=ctx.register_process,
                extra_env=stream_env,
            )
        summary = _scenario_summary(result)
        summaries.append(summary)
        ctx.progress("scenario_finished", **summary)
    failed = sum(1 for summary in summaries if not summary["passed"])
    return (
        "failed" if failed else "passed",
        {
            "scenarios": summaries,
            "total": len(summaries),
            "passed_count": len(summaries) - failed,
            "failed_count": failed,
        },
    )


def _action_run(ctx: _TaskContext) -> tuple[str, dict[str, Any]]:
    spec, ir = _load_compiled(ctx.blueprint_path)
    user_input = ctx.params.get("input")
    if not isinstance(user_input, str) or not user_input.strip():
        raise ActionError(
            "run requires a non-empty 'input' — the editor runs one-shot only "
            "(use `abp run` for the interactive REPL)"
        )
    sandbox_cfg = (spec.run.sandbox if spec.run else None) or SandboxConfig()
    if sandbox_cfg.enabled:
        raise ActionError(
            "this blueprint requests a sandboxed run (run.sandbox.enabled); "
            "sandboxed runs are not supported in the editor yet — use `abp run`"
        )
    install = bool(ctx.params.get("install", True))
    env_file = ctx.blueprint_path.parent / ".env"
    runner = LocalRunner(
        ir,
        thread_id=str(ctx.params.get("thread_id", "editor")),
        process_hook=ctx.register_process,
    )
    with ctx.trace_stream() as stream_env:
        captured = runner.run_capture(
            user_input=user_input,
            install=install,
            env_file=env_file if env_file.exists() else None,
            extra_env=stream_env,
        )
    events = (captured.trace_manifest or {}).get("trace", [])
    return (
        "passed" if captured.returncode == 0 else "failed",
        {
            "returncode": captured.returncode,
            "stdout": captured.stdout,
            "stderr": captured.stderr,
            "trace_events": len(events) if isinstance(events, list) else 0,
        },
    )


def _action_gate(ctx: _TaskContext) -> tuple[str, dict[str, Any] | None]:
    spec, ir = _load_compiled(ctx.blueprint_path)
    scenarios = list(ir.harness.scenarios) if ir.harness else []
    suites = list(ir.evals.suites) if ir.evals else []
    if not scenarios and not suites:
        raise ActionError(
            "blueprint defines no harness scenarios and no eval suites; nothing to gate"
        )
    install = bool(ctx.params.get("install", False))
    tolerance = float(ctx.params.get("tolerance", 0.0))
    update_baseline = bool(ctx.params.get("update_baseline", False))
    trace_store = _trace_store(ctx.blueprint_path)

    harness_results: list[ScenarioResult] = []
    for scenario in scenarios:
        if ctx.cancelled:
            return ("cancelled", None)
        ctx.progress("scenario_started", scenario=scenario.id)
        with ctx.trace_stream(scope=scenario.id) as stream_env:
            result = run_harness_scenario(
                ir,
                scenario,
                install=install,
                trace_store=trace_store,
                save_traces="failed",
                process_hook=ctx.register_process,
                extra_env=stream_env,
            )
        harness_results.append(result)
        ctx.progress("scenario_finished", **_scenario_summary(result))

    eval_result: EvalRunResult | None = None
    if suites:
        suite_results: list[EvalSuiteResult] = []
        for suite in suites:
            if ctx.cancelled:
                return ("cancelled", None)
            ctx.progress("suite_started", suite=suite.id)
            try:
                with ctx.trace_stream(scope=suite.id) as stream_env:
                    suite_result = run_eval_suite(
                        ir,
                        suite,
                        blueprint_dir=ctx.blueprint_path.parent,
                        install=install,
                        trace_store=trace_store,
                        save_traces="failed",
                        process_hook=ctx.register_process,
                        extra_env=stream_env,
                    )
            except BlueprintValidationError as e:
                raise ActionError(str(e)) from e
            suite_results.append(suite_result)
            ctx.progress(
                "suite_finished",
                suite=suite_result.suite_id,
                passed=suite_result.passed,
                score=suite_result.score,
                failures=list(suite_result.failures),
            )
        eval_result = EvalRunResult(
            blueprint=ir.name,
            blueprint_version=ir.version,
            passed=all(result.passed for result in suite_results),
            suites=suite_results,
        )

    if ctx.cancelled:
        return ("cancelled", None)
    current = build_gate_snapshot(
        blueprint=spec.blueprint.name,
        blueprint_version=ir.version,
        harness_results=harness_results,
        eval_result=eval_result,
    )
    all_green = current_run_all_green(current)
    baseline_path = ctx.blueprint_path.parent / ".abp" / "gate-baseline.json"

    if update_baseline:
        if not all_green:
            return (
                "failed",
                {
                    "all_green": False,
                    "message": "refusing to write a red baseline — "
                    "fix the failing scenarios/suites first",
                    "current": current,
                },
            )
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return (
            "passed",
            {"all_green": True, "baseline_written": str(baseline_path), "current": current},
        )

    if not baseline_path.exists():
        raise ActionError(
            f"no gate baseline found at {baseline_path}; "
            "run gate with update_baseline to create one"
        )
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline_data.get("schema_version") != GATE_SCHEMA_VERSION:
        raise ActionError(
            f"unsupported baseline schema_version {baseline_data.get('schema_version')!r}; "
            "regenerate with update_baseline"
        )
    if baseline_data.get("blueprint") != current["blueprint"]:
        raise ActionError(
            f"baseline belongs to blueprint '{baseline_data.get('blueprint')}', "
            f"current run is '{current['blueprint']}'"
        )
    comparison = compare_gate_snapshots(baseline_data, current, tolerance=tolerance)
    gate_passed = all_green and comparison.passed
    return (
        "passed" if gate_passed else "failed",
        {
            "blueprint": current["blueprint"],
            "passed": gate_passed,
            "all_green": all_green,
            "regressions": comparison.regressions,
            "improvements": comparison.improvements,
            "new_entries": comparison.new_entries,
            "current": current,
        },
    )


_HANDLERS: dict[str, Callable[[_TaskContext], tuple[str, dict[str, Any] | None]]] = {
    "doctor": _action_doctor,
    "generate": _action_generate,
    "test": _action_test,
    "run": _action_run,
    "gate": _action_gate,
}
